from __future__ import annotations

"""Risk One Troll: fixed-price 1:1 session reversal/continuation strategy.

This module is intentionally standalone. It does not import RSIQUI or any
RSIQUI root. Signals use only completed candles and deterministic OHLC replay.

Default GMT+7 session contract:
- Session opens at 08:00 (Asia open proxy).
- New entries stop at 20:00, one hour before the configured 21:00 US-open proxy.
- Any active position is force-closed at the session cutoff; no overnight hold.
- Initial direction comes from the previous completed H1 candle: green=long,
  red=short, doji=no trade.
- TP continues in the same direction; SL reverses direction.
"""

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Literal

import pandas as pd

Side = Literal["long", "short"]
ExitReason = Literal["sl", "tp", "session_cutoff"]


@dataclass(frozen=True)
class RiskOneTrollConfig:
    timezone_name: str = "Asia/Ho_Chi_Minh"
    session_open_gmt7: time = time(8, 0)
    stop_new_entries_gmt7: time = time(20, 0)
    us_open_proxy_gmt7: time = time(21, 0)
    force_close_at_cutoff: bool = True
    sl_price_distance: float = 10.0
    tp_price_distance: float = 10.0
    volume_lots: float = 0.10
    price_value_per_lot: float = 100.0
    initial_equity: float = 5_000.0
    commission_per_trade_usd: float = 0.60
    slippage_per_side_price: float = 0.0
    same_bar_collision: Literal["sl_first", "tp_first"] = "sl_first"

    def __post_init__(self) -> None:
        if self.sl_price_distance <= 0 or self.tp_price_distance <= 0:
            raise ValueError("SL and TP price distances must be positive")
        if self.volume_lots <= 0 or self.price_value_per_lot <= 0:
            raise ValueError("volume and price value must be positive")
        if self.stop_new_entries_gmt7 <= self.session_open_gmt7:
            raise ValueError("session cutoff must be after session open")
        if self.same_bar_collision not in {"sl_first", "tp_first"}:
            raise ValueError("unsupported same-bar collision policy")

    @property
    def sl_usd(self) -> float:
        return self.sl_price_distance * self.volume_lots * self.price_value_per_lot

    @property
    def tp_usd(self) -> float:
        return self.tp_price_distance * self.volume_lots * self.price_value_per_lot


@dataclass(frozen=True)
class TrollTrade:
    side: Side
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    pnl: float
    exit_reason: ExitReason
    reversal_index: int


def _side_from_h1_color(open_price: float, close_price: float) -> Side | None:
    if close_price > open_price:
        return "long"
    if close_price < open_price:
        return "short"
    return None


def _opposite(side: Side) -> Side:
    return "short" if side == "long" else "long"


def _session_cutoff(day: pd.Timestamp, config: RiskOneTrollConfig) -> pd.Timestamp:
    return day.normalize() + pd.Timedelta(
        hours=config.stop_new_entries_gmt7.hour,
        minutes=config.stop_new_entries_gmt7.minute,
    )


def _session_open(day: pd.Timestamp, config: RiskOneTrollConfig) -> pd.Timestamp:
    return day.normalize() + pd.Timedelta(
        hours=config.session_open_gmt7.hour,
        minutes=config.session_open_gmt7.minute,
    )


def prepare_m5_with_previous_h1_direction(
    candles_m5: pd.DataFrame,
    candles_h1: pd.DataFrame,
    config: RiskOneTrollConfig = RiskOneTrollConfig(),
) -> pd.DataFrame:
    """Attach the previous completed H1 color to M5 rows causally.

    Both frames must have UTC timestamps. The H1 row is shifted one complete
    candle before merge, so the M5 row cannot consume the currently forming H1.
    """
    required_m5 = {"timestamp", "open", "high", "low", "close"}
    required_h1 = required_m5
    if required_m5 - set(candles_m5.columns) or required_h1 - set(candles_h1.columns):
        raise ValueError("M5 and H1 frames require timestamp/open/high/low/close")
    m5 = candles_m5.copy()
    h1 = candles_h1.copy()
    m5["timestamp"] = pd.to_datetime(m5["timestamp"], utc=True)
    h1["timestamp"] = pd.to_datetime(h1["timestamp"], utc=True)
    m5 = m5.sort_values("timestamp").reset_index(drop=True)
    h1 = h1.sort_values("timestamp").reset_index(drop=True)
    h1["h1_direction"] = [
        _side_from_h1_color(float(o), float(c)) for o, c in zip(h1["open"], h1["close"])
    ]
    # Direction becomes available only after the H1 candle closes.
    h1["available_at"] = h1["timestamp"] + pd.Timedelta(hours=1)
    return pd.merge_asof(
        m5,
        h1[["available_at", "h1_direction"]].sort_values("available_at"),
        left_on="timestamp",
        right_on="available_at",
        direction="backward",
    ).drop(columns=["available_at"])


def backtest_risk_one_troll(
    candles_m5: pd.DataFrame,
    candles_h1: pd.DataFrame,
    config: RiskOneTrollConfig = RiskOneTrollConfig(),
) -> list[TrollTrade]:
    """Replay one-position session trading with deterministic next-bar entries."""
    data = prepare_m5_with_previous_h1_direction(candles_m5, candles_h1, config)
    trades: list[TrollTrade] = []
    active: dict | None = None
    session_day: pd.Timestamp | None = None
    next_side: Side | None = None
    reversal_index = 0

    for index, row in data.iterrows():
        ts = pd.Timestamp(row["timestamp"])
        local_ts = ts.tz_convert(config.timezone_name)
        day = local_ts.normalize()
        session_open = _session_open(day, config)
        cutoff = _session_cutoff(day, config)
        if session_day != day:
            session_day = day
            next_side = row.get("h1_direction")

        if ts < session_open:
            continue

        if active is not None:
            reason: ExitReason | None = None
            exit_price: float | None = None
            if ts >= cutoff and config.force_close_at_cutoff:
                reason, exit_price = "session_cutoff", float(row["close"])
            elif active["side"] == "long":
                sl_hit = float(row["low"]) <= active["sl"]
                tp_hit = float(row["high"]) >= active["tp"]
                if sl_hit and tp_hit:
                    reason = "sl" if config.same_bar_collision == "sl_first" else "tp"
                elif sl_hit:
                    reason = "sl"
                elif tp_hit:
                    reason = "tp"
                if reason == "sl":
                    exit_price = active["sl"]
                elif reason == "tp":
                    exit_price = active["tp"]
            else:
                sl_hit = float(row["high"]) >= active["sl"]
                tp_hit = float(row["low"]) <= active["tp"]
                if sl_hit and tp_hit:
                    reason = "sl" if config.same_bar_collision == "sl_first" else "tp"
                elif sl_hit:
                    reason = "sl"
                elif tp_hit:
                    reason = "tp"
                if reason == "sl":
                    exit_price = active["sl"]
                elif reason == "tp":
                    exit_price = active["tp"]
            if reason is not None and exit_price is not None:
                direction = 1 if active["side"] == "long" else -1
                pnl = (exit_price - active["entry_price"]) * config.volume_lots * config.price_value_per_lot * direction
                pnl -= config.commission_per_trade_usd
                trades.append(TrollTrade(active["side"], active["entry_time"], ts, active["entry_price"], exit_price, pnl, reason, reversal_index))
                if reason == "sl":
                    next_side = _opposite(active["side"])
                    reversal_index += 1
                elif reason == "tp":
                    next_side = active["side"]
                active = None
                if reason == "session_cutoff":
                    next_side = None
                    continue
                # Consume the exit bar. Any continuation/reversal starts next bar.
                continue

        if active is None and ts < cutoff and next_side in {"long", "short"}:
            # First entry uses the first M5 bar at/after session open; later
            # entries use the next available bar after the prior exit.
            entry = float(row["open"])
            if next_side == "long":
                sl, tp = entry - config.sl_price_distance, entry + config.tp_price_distance
            else:
                sl, tp = entry + config.sl_price_distance, entry - config.tp_price_distance
            active = {"side": next_side, "entry_time": ts, "entry_price": entry, "sl": sl, "tp": tp}

    return trades


__all__ = [
    "RiskOneTrollConfig",
    "TrollTrade",
    "backtest_risk_one_troll",
    "prepare_m5_with_previous_h1_direction",
]
