from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class WickFillConfig:
    initial_equity: float = 10_000.0
    risk_usd: float = 15.0
    reward_risk: float = 1.0
    synthetic_spread: float = 0.5
    slippage_per_side: float = 0.05
    max_spread: float = 0.6
    min_body_atr: float = 0.35
    min_upper_wick_body_ratio: float = 1.0
    min_upper_wick_atr: float = 0.35
    confirmation_delay_bars: int = 2
    max_entry_distance_atr: float = 1.25
    min_target_distance: float = 1.0
    max_target_distance: float = 12.0
    session_filter: str = "all"


@dataclass(frozen=True)
class WickFillResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def _session_label(hour: int) -> str:
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 16:
        return "london"
    return "new_york"


def prepare_wick_fill_frame(features: pd.DataFrame) -> pd.DataFrame:
    frame = features.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    h1 = frame.set_index("timestamp").resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    prev_close = h1["close"].shift(1)
    tr = pd.concat([(h1["high"] - h1["low"]), (h1["high"] - prev_close).abs(), (h1["low"] - prev_close).abs()], axis=1).max(axis=1)
    h1["h1_atr_14"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    h1["h1_body"] = (h1["close"] - h1["open"]).abs()
    h1["h1_upper_wick"] = h1["high"] - h1[["open", "close"]].max(axis=1)
    h1["h1_lower_wick"] = h1[["open", "close"]].min(axis=1) - h1["low"]
    h1["h1_closed_at"] = h1.index + pd.Timedelta(hours=1)
    h1_context = h1.shift(1).add_prefix("prev_")
    merged = pd.merge_asof(
        frame,
        h1_context.reset_index().rename(columns={"timestamp": "h1_open_time"}).sort_values("h1_open_time"),
        left_on="timestamp",
        right_on="h1_open_time",
        direction="backward",
    )
    merged["session"] = merged["timestamp"].dt.hour.map(_session_label)
    return merged


def _is_valid_h1_wick_setup(row: pd.Series, config: WickFillConfig) -> bool:
    body = float(row.get("prev_h1_body", np.nan))
    upper = float(row.get("prev_h1_upper_wick", np.nan))
    atr = float(row.get("prev_h1_atr_14", np.nan))
    if not all(np.isfinite(value) and value > 0 for value in [body, upper, atr]):
        return False
    return body >= config.min_body_atr * atr and upper >= config.min_upper_wick_body_ratio * body and upper >= config.min_upper_wick_atr * atr


def _is_confirmation_bar(frame: pd.DataFrame, index: int, config: WickFillConfig) -> bool:
    if index < 2:
        return False
    row = frame.iloc[index]
    prev = frame.iloc[index - 1]
    h1_closed_at = row.get("prev_h1_closed_at")
    if pd.isna(h1_closed_at):
        return False
    expected_time = pd.Timestamp(h1_closed_at) + pd.Timedelta(minutes=15 * config.confirmation_delay_bars)
    if row["timestamp"] != expected_time:
        return False
    body_top = max(float(row["prev_open"]), float(row["prev_close"]))
    target = float(row["prev_high"])
    target_distance = target - float(row["close"])
    atr = float(row["prev_h1_atr_14"])
    from_below = float(prev["close"]) < body_top and float(row["close"]) > float(prev["close"])
    bullish_m15 = float(row["close"]) > float(row["open"])
    distance_ok = config.min_target_distance <= target_distance <= config.max_target_distance and target_distance <= config.max_entry_distance_atr * atr
    return from_below and bullish_m15 and distance_ok


def run_h1_upper_wick_fill_backtest(features: pd.DataFrame, config: WickFillConfig) -> WickFillResult:
    frame = prepare_wick_fill_frame(features)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"setup": 0, "confirmed_long": 0, "blocked_spread": 0, "entered": 0}
    active: dict | None = None
    for index in range(2, len(frame) - 1):
        row = frame.iloc[index]
        spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
        if active is not None:
            active["bars_held"] += 1
            exit_price = None
            if row["low"] <= active["stop_price"]:
                exit_price = active["stop_price"]
            elif row["high"] >= active["take_profit_price"]:
                exit_price = active["take_profit_price"]
            if exit_price is not None:
                pnl = (exit_price - active["entry_price"]) * active["quantity"]
                pnl -= (spread + config.slippage_per_side) * active["quantity"]
                equity += pnl
                trades.append(
                    TradeRecord(
                        side="long",
                        entry_time=active["entry_time"],
                        exit_time=row["timestamp"],
                        entry_price=active["entry_price"],
                        exit_price=exit_price,
                        quantity=active["quantity"],
                        pnl=pnl,
                        bars_held=active["bars_held"],
                    )
                )
                active = None
        if active is None and _is_valid_h1_wick_setup(row, config):
            signal_counts["setup"] += 1
            if config.session_filter != "all" and row["session"] != config.session_filter:
                equity_curve.append(equity)
                continue
            if _is_confirmation_bar(frame, index, config):
                signal_counts["confirmed_long"] += 1
                if spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    next_row = frame.iloc[index + 1]
                    entry = float(next_row["open"])
                    target = float(row["prev_high"])
                    distance = target - entry
                    if config.min_target_distance <= distance <= config.max_target_distance:
                        quantity = config.risk_usd / distance
                        active = {
                            "entry_time": next_row["timestamp"],
                            "entry_price": entry,
                            "stop_price": entry - distance,
                            "take_profit_price": entry + distance * config.reward_risk,
                            "quantity": quantity,
                            "bars_held": 0,
                        }
                        signal_counts["entered"] += 1
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return WickFillResult(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
