from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading_lab.backtest.metrics.performance import compute_metrics
from trading_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class RsiquiV3Config:
    initial_equity: float = 10_000.0
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 20.0
    synthetic_spread: float = 0.5
    spread_multiplier: float = 1.0
    slippage_per_side: float = 0.05
    commission_per_trade_usd: float = 0.0
    max_spread: float = 0.6
    session_filter: str = "all"
    trade_side: str = "both"
    allowed_entry_hours: tuple[int, ...] = ()
    rsi_length: int = 14
    gradient_periods: int = 60
    blocked_entry_hours_gmt7: tuple[int, ...] = ()
    rsi_entry_long: int = 50
    rsi_entry_short: int = 50

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
class RsiquiV3Result:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def rsiqui_v3_config_for_preset(preset: str, **overrides) -> RsiquiV3Config:
    if preset == "original":
        return RsiquiV3Config(**overrides)
    if preset == "gold-balanced":
        params = {
            "rsi_entry_long": 48,
            "rsi_entry_short": 52,
            "gradient_periods": 45,
        }
        params.update(overrides)
        return RsiquiV3Config(**params)
    if preset == "gold-loose":
        params = {
            "rsi_entry_long": 55,
            "rsi_entry_short": 45,
            "gradient_periods": 30,
        }
        params.update(overrides)
        return RsiquiV3Config(**params)
    raise ValueError(f"unknown RsiquiV3 preset: {preset}")


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def _crossed_above(series: pd.Series, threshold: float) -> pd.Series:
    return (series > threshold) & (series.shift(1) <= threshold)


def _crossed_below(series: pd.Series, threshold: float) -> pd.Series:
    return (series < threshold) & (series.shift(1) >= threshold)


def prepare_rsiqui_v3_frame(features: pd.DataFrame, config: RsiquiV3Config) -> pd.DataFrame:
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").reset_index(drop=True)
    data["rsi"] = _rsi(data["close"], config.rsi_length)
    data["rsi_gra"] = np.gradient(data["rsi"].to_numpy(dtype=float), config.gradient_periods)
    data["rsi_gra_cross_above_0"] = _crossed_above(data["rsi_gra"], 0)
    data["rsi_gra_cross_below_0"] = _crossed_below(data["rsi_gra"], 0)
    return data


def evaluate_rsiqui_v3_signal(row: pd.Series, config: RsiquiV3Config) -> str | None:
    required = [row.get("rsi"), row.get("rsi_gra")]
    if not all(pd.notna(value) and np.isfinite(float(value)) for value in required):
        return None
    long_signal = row["rsi"] < config.rsi_entry_long and bool(row.get("rsi_gra_cross_above_0", False))
    short_signal = row["rsi"] > config.rsi_entry_short and bool(row.get("rsi_gra_cross_below_0", False))
    if long_signal and config.trade_side in {"both", "long"}:
        return "long"
    if short_signal and config.trade_side in {"both", "short"}:
        return "short"
    return None


def run_rsiqui_v3_backtest(features: pd.DataFrame, config: RsiquiV3Config) -> RsiquiV3Result:
    data = prepare_rsiqui_v3_frame(features, config)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"long_signal": 0, "short_signal": 0, "blocked_spread": 0, "entered_long": 0, "entered_short": 0}
    active: dict | None = None
    warmup = max(120, config.rsi_length + config.gradient_periods)
    for index in range(warmup, len(data) - 1):
        row = data.iloc[index]
        next_row = data.iloc[index + 1]
        spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
        stressed_spread = spread * config.spread_multiplier
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
                pnl -= (stressed_spread + config.slippage_per_side) * active["quantity"]
                pnl -= config.commission_per_trade_usd
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
            side = evaluate_rsiqui_v3_signal(row, config)
            if side is not None:
                signal_counts[f"{side}_signal"] += 1
                if stressed_spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    entry = float(next_row["open"])
                    stop = entry - config.stop_distance if side == "long" else entry + config.stop_distance
                    target = entry + config.take_profit_distance if side == "long" else entry - config.take_profit_distance
                    active = {
                        "side": side,
                        "entry_time": next_row["timestamp"],
                        "entry_price": entry,
                        "quantity": config.quantity,
                        "stop_price": stop,
                        "take_profit_price": target,
                        "bars_held": 0,
                    }
                    signal_counts["entered_long" if side == "long" else "entered_short"] += 1
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return RsiquiV3Result(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
