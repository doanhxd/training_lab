from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class E0V1EConfig:
    initial_equity: float = 10_000.0
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 20.0
    synthetic_spread: float = 0.5
    slippage_per_side: float = 0.05
    max_spread: float = 0.6
    session_filter: str = "all"
    trade_side: str = "long"
    allowed_entry_hours: tuple[int, ...] = ()
    buy_rsi_fast_32: int = 46
    buy_rsi_32: int = 19
    buy_sma15_32: float = 0.942
    buy_cti_32: float = -0.86

    @property
    def quantity(self) -> float:
        return self.volume_lots * self.price_value_per_lot

    @property
    def stop_distance(self) -> float:
        return self.risk_usd / max(self.quantity, 1e-12)

    @property
    def take_profit_distance(self) -> float:
        return self.reward_usd / max(self.quantity, 1e-12)


@dataclass(frozen=True)
class E0V1EResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def e0v1e_config_for_preset(preset: str, **overrides) -> E0V1EConfig:
    if preset == "original":
        return E0V1EConfig(**overrides)
    if preset == "gold-balanced":
        params = {
            "buy_rsi_fast_32": 48,
            "buy_rsi_32": 24,
            "buy_sma15_32": 0.996,
            "buy_cti_32": -0.74,
        }
        params.update(overrides)
        return E0V1EConfig(**params)
    if preset == "gold-loose":
        params = {
            "buy_rsi_fast_32": 55,
            "buy_rsi_32": 22,
            "buy_sma15_32": 0.999,
            "buy_cti_32": -0.62,
        }
        params.update(overrides)
        return E0V1EConfig(**params)
    raise ValueError(f"unknown E0V1E preset: {preset}")


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def _cti(series: pd.Series, length: int = 20) -> pd.Series:
    trend = pd.Series(np.arange(length, dtype=float), index=range(length))

    def corr(values: np.ndarray) -> float:
        if np.isnan(values).any() or np.std(values) == 0:
            return np.nan
        return float(np.corrcoef(values, trend.values)[0, 1])

    return series.rolling(length, min_periods=length).apply(corr, raw=True)


def prepare_e0v1e_frame(features: pd.DataFrame) -> pd.DataFrame:
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").reset_index(drop=True)
    data["sma_15"] = data["close"].rolling(15, min_periods=15).mean()
    data["cti"] = _cti(data["close"], 20)
    data["rsi"] = _rsi(data["close"], 14)
    data["rsi_fast"] = _rsi(data["close"], 4)
    data["rsi_slow"] = _rsi(data["close"], 20)
    data["rsi_slow_prev"] = data["rsi_slow"].shift(1)
    lowest_low = data["low"].rolling(5, min_periods=5).min()
    highest_high = data["high"].rolling(5, min_periods=5).max()
    data["fastk"] = 100 * (data["close"] - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    data["fastk"] = data["fastk"].rolling(3, min_periods=3).mean()
    return data


def evaluate_e0v1e_signal(row: pd.Series, config: E0V1EConfig) -> bool:
    required = [row.get("rsi_slow"), row.get("rsi_slow_prev"), row.get("rsi_fast"), row.get("rsi"), row.get("sma_15"), row.get("cti")]
    if not all(pd.notna(value) and np.isfinite(float(value)) for value in required):
        return False
    return bool(
        row["rsi_slow"] < row["rsi_slow_prev"]
        and row["rsi_fast"] < config.buy_rsi_fast_32
        and row["rsi"] > config.buy_rsi_32
        and row["close"] < row["sma_15"] * config.buy_sma15_32
        and row["cti"] < config.buy_cti_32
    )


def run_e0v1e_backtest(features: pd.DataFrame, config: E0V1EConfig) -> E0V1EResult:
    data = prepare_e0v1e_frame(features)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"buy_1": 0, "blocked_spread": 0, "entered": 0, "long": 0, "short": 0}
    active: dict | None = None
    for index in range(120, len(data) - 1):
        row = data.iloc[index]
        next_row = data.iloc[index + 1]
        spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
        if active is not None:
            active["bars_held"] += 1
            exit_price = None
            if active["side"] == "long":
                if row["low"] <= active["stop_price"]:
                    exit_price = active["stop_price"]
                elif row["high"] >= active["take_profit_price"]:
                    exit_price = active["take_profit_price"]
            else:
                if row["high"] >= active["stop_price"]:
                    exit_price = active["stop_price"]
                elif row["low"] <= active["take_profit_price"]:
                    exit_price = active["take_profit_price"]
            if exit_price is not None:
                direction = 1 if active["side"] == "long" else -1
                pnl = (float(exit_price) - active["entry_price"]) * active["quantity"] * direction
                pnl -= (spread + config.slippage_per_side) * active["quantity"]
                equity += pnl
                trades.append(
                    TradeRecord(
                        side=active["side"],
                        entry_time=active["entry_time"],
                        exit_time=row["timestamp"],
                        entry_price=active["entry_price"],
                        exit_price=float(exit_price),
                        quantity=active["quantity"],
                        pnl=pnl,
                        bars_held=active["bars_held"],
                    )
                )
                active = None
        if active is None:
            if config.session_filter != "all" and row.get("session") != config.session_filter:
                equity_curve.append(equity)
                continue
            if config.allowed_entry_hours and int(row["timestamp"].hour) not in config.allowed_entry_hours:
                equity_curve.append(equity)
                continue
            if evaluate_e0v1e_signal(row, config):
                signal_counts["buy_1"] += 1
                if spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    entry = float(next_row["open"])
                    side = config.trade_side
                    stop = entry - config.stop_distance if side == "long" else entry + config.stop_distance
                    target = entry + config.take_profit_distance if side == "long" else entry - config.take_profit_distance
                    active = {"side": side, "entry_time": next_row["timestamp"], "entry_price": entry, "quantity": config.quantity, "stop_price": stop, "take_profit_price": target, "bars_held": 0}
                    signal_counts["entered"] += 1
                    signal_counts[side] += 1
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return E0V1EResult(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
