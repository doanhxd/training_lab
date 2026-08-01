from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

import numpy as np
import pandas as pd

from trading_lab.backtest.metrics.performance import compute_metrics
from trading_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class BollingerMacdV2Config:
    """Frozen execution contract for the Bollinger/MACD V2 M5 candidate."""

    initial_equity: float = 10_000.0
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 5.0
    reward_usd: float = 5.0
    fixed_spread: float = 0.2
    slippage_per_side: float = 0.5
    trade_side: Literal["both", "long", "short"] = "both"

    @property
    def quantity(self) -> float:
        return self.volume_lots * self.price_value_per_lot

    @property
    def stop_distance(self) -> float:
        return self.risk_usd / self.quantity

    @property
    def take_profit_distance(self) -> float:
        return self.reward_usd / self.quantity

    @property
    def round_turn_execution_cost(self) -> float:
        """Dollar cost per 1-lot-equivalent trade: 2 slips + one bid/ask spread."""
        return (2 * self.slippage_per_side + self.fixed_spread) * self.quantity

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload.update(
            quantity=self.quantity,
            stop_distance=self.stop_distance,
            take_profit_distance=self.take_profit_distance,
            round_turn_execution_cost=self.round_turn_execution_cost,
        )
        return payload


@dataclass(frozen=True)
class BollingerMacdV2Result:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]
    open_trade: dict | None


def prepare_bollinger_macd_v2_frame(features: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required.difference(features.columns)
    if missing:
        raise ValueError(f"missing required candle columns: {sorted(missing)}")
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    data["volume"] = pd.to_numeric(data.get("volume", 1.0), errors="coerce").fillna(0.0)
    for column in ("open", "high", "low", "close"):
        data[column] = pd.to_numeric(data[column], errors="coerce")

    typical = (data["high"] + data["low"] + data["close"]) / 3.0
    middle = typical.rolling(20, min_periods=20).mean()
    deviation = typical.rolling(20, min_periods=20).std(ddof=0)
    data["bb_middleband"] = middle
    data["bb_upperband"] = middle + 2.0 * deviation
    data["bb_lowerband"] = middle - 2.0 * deviation

    ema_fast = data["close"].ewm(span=12, adjust=False, min_periods=26).mean()
    ema_slow = data["close"].ewm(span=26, adjust=False, min_periods=26).mean()
    data["macd"] = ema_fast - ema_slow
    data["macdsignal"] = data["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    data["macdhist"] = data["macd"] - data["macdsignal"]
    return data


def _crossed_above(current: float, prior: float, benchmark: float, prior_benchmark: float) -> bool:
    return current > benchmark and prior <= prior_benchmark


def _crossed_below(current: float, prior: float, benchmark: float, prior_benchmark: float) -> bool:
    return current < benchmark and prior >= prior_benchmark


def _finite(*values: float) -> bool:
    return all(pd.notna(value) and np.isfinite(float(value)) for value in values)


def evaluate_bollinger_macd_v2_entry(row: pd.Series, previous: pd.Series) -> tuple[str | None, str | None]:
    values = [
        row["close"], previous["close"], row["bb_middleband"], previous["bb_middleband"],
        row["macd"], row["macdsignal"], row["macdhist"], previous["macdhist"], row["volume"],
    ]
    if not _finite(*values) or row["volume"] <= 0:
        return None, None
    if (
        _crossed_above(row["close"], previous["close"], row["bb_middleband"], previous["bb_middleband"])
        and row["macd"] > row["macdsignal"]
        and row["macdhist"] > 0
        and row["macdhist"] > previous["macdhist"]
    ):
        return "long", "bb_mid_reclaim"
    if (
        _crossed_below(row["close"], previous["close"], row["bb_middleband"], previous["bb_middleband"])
        and row["macd"] < row["macdsignal"]
        and row["macdhist"] < 0
        and row["macdhist"] < previous["macdhist"]
    ):
        return "short", "bb_mid_break"
    return None, None


def _exit_signal(row: pd.Series, previous: pd.Series, side: str) -> bool:
    values = [row["close"], previous["close"], row["bb_middleband"], previous["bb_middleband"], row["macd"], previous["macd"], row["macdsignal"], previous["macdsignal"]]
    if not _finite(*values):
        return False
    if side == "long":
        return _crossed_below(row["close"], previous["close"], row["bb_middleband"], previous["bb_middleband"]) or _crossed_below(row["macd"], previous["macd"], row["macdsignal"], previous["macdsignal"])
    return _crossed_above(row["close"], previous["close"], row["bb_middleband"], previous["bb_middleband"]) or _crossed_above(row["macd"], previous["macd"], row["macdsignal"], previous["macdsignal"])


def _adverse_fill(raw_price: float, side: str, is_entry: bool, slippage: float) -> float:
    direction = 1.0 if side == "long" else -1.0
    return raw_price + direction * slippage if is_entry else raw_price - direction * slippage


def run_bollinger_macd_v2_backtest(features: pd.DataFrame, config: BollingerMacdV2Config) -> BollingerMacdV2Result:
    data = prepare_bollinger_macd_v2_frame(features)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    active: dict | None = None
    counts = {"long_signals": 0, "short_signals": 0, "entered": 0, "stop_loss": 0, "take_profit": 0, "signal_exit": 0}

    for index in range(35, len(data)):
        row = data.iloc[index]
        previous = data.iloc[index - 1]
        next_row = data.iloc[index + 1] if index + 1 < len(data) else None

        if active is not None:
            active["bars_held"] += 1
            raw_exit: float | None = None
            reason: str | None = None
            # For a bar touching both thresholds, apply stop first (adverse collision rule).
            if active["side"] == "long":
                if row["low"] <= active["stop_price"]:
                    raw_exit, reason = active["stop_price"], "stop_loss"
                elif row["high"] >= active["take_profit_price"]:
                    raw_exit, reason = active["take_profit_price"], "take_profit"
            else:
                if row["high"] >= active["stop_price"]:
                    raw_exit, reason = active["stop_price"], "stop_loss"
                elif row["low"] <= active["take_profit_price"]:
                    raw_exit, reason = active["take_profit_price"], "take_profit"
            if raw_exit is None and _exit_signal(row, previous, active["side"]):
                raw_exit, reason = float(row["close"]), "signal_exit"

            if raw_exit is not None:
                exit_price = _adverse_fill(raw_exit, active["side"], False, config.slippage_per_side)
                direction = 1.0 if active["side"] == "long" else -1.0
                pnl = (exit_price - active["entry_price"]) * config.quantity * direction
                pnl -= config.fixed_spread * config.quantity
                equity += pnl
                trades.append(TradeRecord(
                    side=active["side"], entry_time=active["entry_time"], exit_time=row["timestamp"],
                    entry_price=active["entry_price"], exit_price=exit_price, quantity=config.quantity,
                    pnl=pnl, bars_held=active["bars_held"],
                ))
                counts[reason] += 1
                active = None

        if active is None and next_row is not None:
            side, _tag = evaluate_bollinger_macd_v2_entry(row, previous)
            if side is not None:
                counts[f"{side}_signals"] += 1
            if side is not None and (config.trade_side == "both" or config.trade_side == side):
                entry_price = _adverse_fill(float(next_row["open"]), side, True, config.slippage_per_side)
                direction = 1.0 if side == "long" else -1.0
                active = {
                    "side": side,
                    "entry_time": next_row["timestamp"],
                    "entry_price": entry_price,
                    "stop_price": entry_price - direction * config.stop_distance,
                    "take_profit_price": entry_price + direction * config.take_profit_distance,
                    "bars_held": 0,
                }
                counts["entered"] += 1
        equity_curve.append(equity)

    open_trade = None
    if active is not None:
        open_trade = {**active, "unrealized_at_last_close": float(data.iloc[-1]["close"])}
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return BollingerMacdV2Result(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=counts, open_trade=open_trade)
