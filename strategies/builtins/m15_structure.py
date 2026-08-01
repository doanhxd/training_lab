from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading_lab.models import BacktestMetrics, TradeRecord
from trading_lab.backtest.metrics.performance import compute_metrics


@dataclass(frozen=True)
class FixedRiskConfig:
    initial_equity: float = 10_000.0
    risk_usd: float = 10.0
    reward_risk: float = 1.0
    synthetic_spread: float = 0.5
    slippage_per_side: float = 0.05
    max_spread: float = 0.6
    max_positions: int = 1
    min_sl_distance: float = 2.0
    max_sl_distance: float = 8.0
    atr_stop_multiplier: float = 1.2
    side_filter: str = "both"
    session_filter: str = "all"


@dataclass(frozen=True)
class BuiltinBacktestResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def add_live_bot_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data["ema_20_live"] = data["close"].ewm(span=20, adjust=False).mean()
    data["ema_50_live"] = data["close"].ewm(span=50, adjust=False).mean()
    data["ema_200_live"] = data["close"].ewm(span=200, adjust=False).mean()
    delta = data["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=13, adjust=False).mean()
    avg_loss = loss.ewm(com=13, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    data["rsi_live"] = (100 - (100 / (1 + rs))).fillna(50)
    high_low = data["high"] - data["low"]
    high_close = (data["high"] - data["close"].shift()).abs()
    low_close = (data["low"] - data["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    data["atr_live"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    return data


def add_h1_trend(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    h1 = data.set_index("timestamp").resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    h1["ema_50_h1"] = h1["close"].ewm(span=50, adjust=False).mean()
    h1["ema_200_h1"] = h1["close"].ewm(span=200, adjust=False).mean()
    h1["h1_trend"] = np.select([h1["ema_50_h1"] > h1["ema_200_h1"], h1["ema_50_h1"] < h1["ema_200_h1"]], [1, -1], default=0)
    trend = h1["h1_trend"].shift(1).rename("h1_trend")
    data = data.sort_values("timestamp")
    data = pd.merge_asof(data, trend.reset_index().sort_values("timestamp"), on="timestamp", direction="backward")
    data["h1_trend"] = data["h1_trend"].fillna(0).astype(int)
    return data


def find_swing_points(frame: pd.DataFrame, end_index: int, k: int = 3) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    highs = frame["high"].values
    lows = frame["low"].values
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    for index in range(k, max(k, end_index - k + 1)):
        if all(highs[index] > highs[index - j] and highs[index] > highs[index + j] for j in range(1, k + 1)):
            swing_highs.append((index, float(highs[index])))
        if all(lows[index] < lows[index - j] and lows[index] < lows[index + j] for j in range(1, k + 1)):
            swing_lows.append((index, float(lows[index])))
    return swing_highs, swing_lows


def detect_hhll_signal(frame: pd.DataFrame, index: int, swing_highs: list[tuple[int, float]], swing_lows: list[tuple[int, float]], htf_trend: int) -> tuple[str | None, str | None]:
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None, None
    old_high = swing_highs[-2][1]
    new_high = swing_highs[-1][1]
    old_low = swing_lows[-2][1]
    new_low = swing_lows[-1][1]
    high_index = swing_highs[-1][0]
    low_index = swing_lows[-1][0]
    close = frame.iloc[index]["close"]
    if high_index > swing_highs[-2][0] and low_index > high_index and new_low < old_low and htf_trend <= 0 and close < old_low:
        return "short", f"HHLL SELL HH={new_high:.1f} break L={old_low:.1f} LL={new_low:.1f}"
    if low_index > swing_lows[-2][0] and high_index > low_index and new_high > old_high and htf_trend >= 0 and close > old_high:
        return "long", f"HHLL BUY LL={new_low:.1f} break H={old_high:.1f} HH={new_high:.1f}"
    return None, None


def detect_bos_ob_signal(frame: pd.DataFrame, index: int, swing_highs: list[tuple[int, float]], swing_lows: list[tuple[int, float]], htf_trend: int, atr: float) -> tuple[str | None, str | None]:
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return None, None
    current = frame.iloc[index]
    closes = frame["close"].values
    opens = frame["open"].values
    if htf_trend >= 0:
        bos_level = swing_highs[-2][1]
        bos_index = swing_highs[-2][0]
        confirmed = next((i for i in range(bos_index + 1, index + 1) if closes[i] > bos_level), None)
        if confirmed:
            for ob_index in range(confirmed - 1, bos_index, -1):
                if closes[ob_index] < opens[ob_index]:
                    ob_low = frame.iloc[ob_index]["low"]
                    ob_high = frame.iloc[ob_index]["high"]
                    if ob_low - 0.5 * atr <= current["close"] <= ob_high + 0.5 * atr and current["close"] > current["open"]:
                        return "long", f"BOS BUY break {bos_level:.1f} retest OB [{ob_low:.1f}-{ob_high:.1f}]"
                    break
    if htf_trend <= 0:
        bos_level = swing_lows[-2][1]
        bos_index = swing_lows[-2][0]
        confirmed = next((i for i in range(bos_index + 1, index + 1) if closes[i] < bos_level), None)
        if confirmed:
            for ob_index in range(confirmed - 1, bos_index, -1):
                if closes[ob_index] > opens[ob_index]:
                    ob_low = frame.iloc[ob_index]["low"]
                    ob_high = frame.iloc[ob_index]["high"]
                    if ob_low - 0.5 * atr <= current["close"] <= ob_high + 0.5 * atr and current["close"] < current["open"]:
                        return "short", f"BOS SELL break {bos_level:.1f} retest OB [{ob_low:.1f}-{ob_high:.1f}]"
                    break
    return None, None


def evaluate_m15_structure_signal(frame: pd.DataFrame, index: int) -> tuple[str | None, str | None]:
    if index < 220:
        return None, None
    current = frame.iloc[index]
    previous = frame.iloc[index - 1]
    htf_trend = int(current["h1_trend"])
    close = current["close"]
    opening = current["open"]
    ema20 = current["ema_20_live"]
    ema50 = current["ema_50_live"]
    rsi = current["rsi_live"]
    atr = float(current["atr_live"])
    if not np.isfinite(atr) or atr <= 0:
        return None, None
    bull_candle = close > opening and (close - opening) > 0.15 * atr
    bear_candle = close < opening and (opening - close) > 0.15 * atr
    bull_engulf = close > opening and previous["close"] < previous["open"] and close >= previous["open"] and opening <= previous["close"]
    bear_engulf = close < opening and previous["close"] > previous["open"] and close <= previous["open"] and opening >= previous["close"]
    near_ema = min(abs(close - ema20), abs(close - ema50)) <= 0.6 * atr
    ema_buy = htf_trend == 1 and close > ema50 and near_ema and 35 <= rsi <= 65 and (bull_candle or bull_engulf)
    ema_sell = htf_trend == -1 and close < ema50 and near_ema and 35 <= rsi <= 65 and (bear_candle or bear_engulf)
    swing_highs, swing_lows = find_swing_points(frame, end_index=index, k=3)
    hhll_side, hhll_reason = detect_hhll_signal(frame, index, swing_highs, swing_lows, htf_trend)
    bos_side, bos_reason = detect_bos_ob_signal(frame, index, swing_highs, swing_lows, htf_trend, atr)
    buy_score = sum([ema_buy, hhll_side == "long", bos_side == "long"])
    sell_score = sum([ema_sell, hhll_side == "short", bos_side == "short"])
    if buy_score >= 2:
        return "long", "CONFLUENCE BUY"
    if sell_score >= 2:
        return "short", "CONFLUENCE SELL"
    if hhll_side:
        return hhll_side, hhll_reason
    if bos_side:
        return bos_side, bos_reason
    return None, None


def run_m15_structure_backtest(features: pd.DataFrame, config: FixedRiskConfig) -> BuiltinBacktestResult:
    data = add_h1_trend(add_live_bot_indicators(features)).reset_index(drop=True)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"long": 0, "short": 0, "blocked_spread": 0}
    active: dict | None = None
    for index in range(220, len(data) - 1):
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
                pnl = (exit_price - active["entry_price"]) * active["quantity"] * direction
                pnl -= (spread + config.slippage_per_side) * active["quantity"]
                equity += pnl
                trades.append(
                    TradeRecord(
                        side=active["side"],
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
        if active is None:
            side, _reason = evaluate_m15_structure_signal(data, index)
            if side and config.side_filter != "both" and side != config.side_filter:
                side = None
            if side and config.session_filter != "all" and row.get("session") != config.session_filter:
                side = None
            if side:
                signal_counts[side] += 1
                if spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    atr = float(row["atr_live"])
                    stop_distance = float(np.clip(config.atr_stop_multiplier * atr, config.min_sl_distance, config.max_sl_distance))
                    quantity = config.risk_usd / stop_distance
                    entry = float(next_row["open"])
                    if side == "long":
                        stop_price = entry - stop_distance
                        take_profit_price = entry + stop_distance * config.reward_risk
                    else:
                        stop_price = entry + stop_distance
                        take_profit_price = entry - stop_distance * config.reward_risk
                    active = {
                        "side": side,
                        "entry_time": next_row["timestamp"],
                        "entry_price": entry,
                        "stop_price": stop_price,
                        "take_profit_price": take_profit_price,
                        "quantity": quantity,
                        "bars_held": 0,
                    }
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return BuiltinBacktestResult(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
