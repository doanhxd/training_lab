from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class Best5mConfig:
    initial_equity: float = 10_000.0
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 20.0
    synthetic_spread: float = 0.5
    slippage_per_side: float = 0.05
    max_spread: float = 0.6
    session_filter: str = "all"
    trade_side: str = "both"
    allowed_entry_hours: tuple[int, ...] = ()
    fast_ma_length: int = 10
    slow_ma_length: int = 30
    rsi_length: int = 14
    rsi_buy_threshold: int = 30
    rsi_sell_threshold: int = 70

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
class Best5mResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def best5m_config_for_preset(preset: str, **overrides) -> Best5mConfig:
    if preset == "original":
        return Best5mConfig(**overrides)
    if preset == "gold-balanced":
        params = {
            "fast_ma_length": 12,
            "slow_ma_length": 48,
            "rsi_length": 14,
            "rsi_buy_threshold": 42,
            "rsi_sell_threshold": 58,
        }
        params.update(overrides)
        return Best5mConfig(**params)
    if preset == "gold-loose":
        params = {
            "fast_ma_length": 8,
            "slow_ma_length": 34,
            "rsi_length": 14,
            "rsi_buy_threshold": 48,
            "rsi_sell_threshold": 52,
        }
        params.update(overrides)
        return Best5mConfig(**params)
    raise ValueError(f"unknown Best5m preset: {preset}")


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def prepare_best5m_frame(features: pd.DataFrame, config: Best5mConfig) -> pd.DataFrame:
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").reset_index(drop=True)
    data["fast_sma"] = data["close"].rolling(config.fast_ma_length, min_periods=config.fast_ma_length).mean()
    data["slow_sma"] = data["close"].rolling(config.slow_ma_length, min_periods=config.slow_ma_length).mean()
    data["rsi"] = _rsi(data["close"], config.rsi_length)
    return data


def evaluate_best5m_signal(row: pd.Series, config: Best5mConfig) -> str | None:
    required = [row.get("fast_sma"), row.get("slow_sma"), row.get("rsi")]
    if not all(pd.notna(value) and np.isfinite(float(value)) for value in required):
        return None
    long_signal = row["fast_sma"] > row["slow_sma"] and row["rsi"] < config.rsi_buy_threshold
    short_signal = row["fast_sma"] < row["slow_sma"] and row["rsi"] > config.rsi_sell_threshold
    if long_signal and config.trade_side in {"both", "long"}:
        return "long"
    if short_signal and config.trade_side in {"both", "short"}:
        return "short"
    return None


def run_best5m_backtest(features: pd.DataFrame, config: Best5mConfig) -> Best5mResult:
    data = prepare_best5m_frame(features, config)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"long_signal": 0, "short_signal": 0, "blocked_spread": 0, "entered_long": 0, "entered_short": 0}
    active: dict | None = None
    warmup = max(config.fast_ma_length, config.slow_ma_length, config.rsi_length, 120)
    for index in range(warmup, len(data) - 1):
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
            side = evaluate_best5m_signal(row, config)
            if side is not None:
                signal_counts[f"{side}_signal"] += 1
                if spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    entry = float(next_row["open"])
                    stop = entry - config.stop_distance if side == "long" else entry + config.stop_distance
                    target = entry + config.take_profit_distance if side == "long" else entry - config.take_profit_distance
                    active = {"side": side, "entry_time": next_row["timestamp"], "entry_price": entry, "quantity": config.quantity, "stop_price": stop, "take_profit_price": target, "bars_held": 0}
                    signal_counts["entered_long" if side == "long" else "entered_short"] += 1
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return Best5mResult(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
