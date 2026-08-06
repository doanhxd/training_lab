from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class FreqtradeEwoConfig:
    initial_equity: float = 10_000.0
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 15.0
    synthetic_spread: float = 0.5
    slippage_per_side: float = 0.05
    max_spread: float = 0.6
    session_filter: str = "all"
    trade_side: str = "long"
    allowed_signal_tags: tuple[str, ...] = ("ewo", "buy_1", "ewo buy_1")
    allowed_entry_hours: tuple[int, ...] = ()
    use_fastk_breakeven_exit: bool = False
    sell_fastx: int = 75
    buy_rsi_fast: int = 50
    buy_rsi: int = 30
    buy_ewo: float = -1.238
    buy_ema_low: float = 0.956
    buy_ema_high: float = 0.986
    buy_rsi_fast_32: int = 63
    buy_rsi_32: int = 16
    buy_sma15_32: float = 0.932
    buy_cti_32: float = -0.8

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
class FreqtradeEwoResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def freqtrade_ewo_config_for_preset(preset: str, **overrides) -> FreqtradeEwoConfig:
    if preset == "original":
        return FreqtradeEwoConfig(**overrides)
    if preset == "gold-balanced":
        params = {
            "buy_rsi_fast": 35,
            "buy_rsi": 40,
            "buy_ewo": -0.4,
            "buy_ema_low": 0.999,
            "buy_ema_high": 0.9995,
            "buy_rsi_fast_32": 55,
            "buy_rsi_32": 25,
            "buy_sma15_32": 0.999,
            "buy_cti_32": -0.75,
        }
        params.update(overrides)
        return FreqtradeEwoConfig(**params)
    if preset == "gold-loose":
        params = {
            "buy_rsi_fast": 45,
            "buy_rsi": 45,
            "buy_ewo": -0.6,
            "buy_ema_low": 0.9995,
            "buy_ema_high": 1.0,
            "buy_rsi_fast_32": 63,
            "buy_rsi_32": 25,
            "buy_sma15_32": 0.9995,
            "buy_cti_32": -0.7,
        }
        params.update(overrides)
        return FreqtradeEwoConfig(**params)
    raise ValueError(f"unknown freqtrade EWO preset: {preset}")


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


def prepare_freqtrade_ewo_frame(features: pd.DataFrame) -> pd.DataFrame:
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").reset_index(drop=True)
    data["sma_15"] = data["close"].rolling(15, min_periods=15).mean()
    data["cti"] = _cti(data["close"], 20)
    data["rsi"] = _rsi(data["close"], 14)
    data["rsi_fast"] = _rsi(data["close"], 4)
    data["rsi_slow"] = _rsi(data["close"], 20)
    data["ema_8"] = data["close"].ewm(span=8, adjust=False, min_periods=8).mean()
    data["ema_16"] = data["close"].ewm(span=16, adjust=False, min_periods=16).mean()
    ema_50 = data["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    ema_200 = data["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    data["ewo"] = (ema_50 - ema_200) / data["low"].replace(0, np.nan) * 100
    lowest_low = data["low"].rolling(5, min_periods=5).min()
    highest_high = data["high"].rolling(5, min_periods=5).max()
    data["fastk"] = 100 * (data["close"] - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    data["fastd"] = data["fastk"].rolling(3, min_periods=3).mean()
    typical = (data["high"] + data["low"] + data["close"]) / 3
    bb_middle = typical.rolling(20, min_periods=20).mean()
    bb_std = typical.rolling(20, min_periods=20).std(ddof=0)
    data["bb_lowerband2"] = bb_middle - 2 * bb_std
    data["bb_middleband2"] = bb_middle
    data["bb_upperband2"] = bb_middle + 2 * bb_std
    data["bb_width"] = (data["bb_upperband2"] - data["bb_lowerband2"]) / data["bb_middleband2"].replace(0, np.nan)
    data["volume_mean_12"] = data["volume"].rolling(12, min_periods=12).mean().shift(1)
    data["volume_mean_24"] = data["volume"].rolling(24, min_periods=24).mean().shift(1)
    return data


def evaluate_freqtrade_ewo_signal(row: pd.Series, previous: pd.Series, config: FreqtradeEwoConfig) -> tuple[bool, str | None]:
    values = [
        row.get("rsi_fast"),
        row.get("ema_8"),
        row.get("ewo"),
        row.get("ema_16"),
        row.get("rsi"),
        row.get("rsi_slow"),
        previous.get("rsi_slow"),
        row.get("sma_15"),
        row.get("cti"),
    ]
    if not all(pd.notna(value) and np.isfinite(float(value)) for value in values):
        return False, None
    is_ewo = (
        row["rsi_fast"] < config.buy_rsi_fast
        and row["close"] < row["ema_8"] * config.buy_ema_low
        and row["ewo"] > config.buy_ewo
        and row["close"] < row["ema_16"] * config.buy_ema_high
        and row["rsi"] < config.buy_rsi
    )
    buy_1 = (
        row["rsi_slow"] < previous["rsi_slow"]
        and row["rsi_fast"] < config.buy_rsi_fast_32
        and row["rsi"] > config.buy_rsi_32
        and row["close"] < row["sma_15"] * config.buy_sma15_32
        and row["cti"] < config.buy_cti_32
    )
    if is_ewo and buy_1:
        return True, "ewo buy_1"
    if is_ewo:
        return True, "ewo"
    if buy_1:
        return True, "buy_1"
    return False, None


def run_freqtrade_ewo_backtest(features: pd.DataFrame, config: FreqtradeEwoConfig) -> FreqtradeEwoResult:
    return run_freqtrade_ewo_prepared_backtest(prepare_freqtrade_ewo_frame(features), config)


def run_freqtrade_ewo_prepared_backtest(data: pd.DataFrame, config: FreqtradeEwoConfig) -> FreqtradeEwoResult:
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"ewo": 0, "buy_1": 0, "both": 0, "blocked_spread": 0, "entered": 0, "fastk_exit": 0, "long": 0, "short": 0}
    active: dict | None = None
    for index in range(200, len(data) - 1):
        row = data.iloc[index]
        previous = data.iloc[index - 1]
        next_row = data.iloc[index + 1]
        spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
        if active is not None:
            active["bars_held"] += 1
            exit_price = None
            exit_time = row["timestamp"]
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
            fastk_exit = config.use_fastk_breakeven_exit and row.get("fastk", 0) > config.sell_fastx and (
                (active["side"] == "long" and row["close"] > active["entry_price"]) or (active["side"] == "short" and row["close"] < active["entry_price"])
            )
            if exit_price is None and fastk_exit:
                exit_price = row["close"]
                signal_counts["fastk_exit"] += 1
            if exit_price is not None:
                direction = 1 if active["side"] == "long" else -1
                pnl = (float(exit_price) - active["entry_price"]) * active["quantity"] * direction
                pnl -= (spread + config.slippage_per_side) * active["quantity"]
                equity += pnl
                trades.append(
                    TradeRecord(
                        side=active["side"],
                        entry_time=active["entry_time"],
                        exit_time=exit_time,
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
            signal, tag = evaluate_freqtrade_ewo_signal(row, previous, config)
            if signal and tag and tag in config.allowed_signal_tags:
                if tag == "ewo buy_1":
                    signal_counts["both"] += 1
                elif tag == "ewo":
                    signal_counts["ewo"] += 1
                else:
                    signal_counts["buy_1"] += 1
                if spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    entry = float(next_row["open"])
                    side = config.trade_side
                    if side == "long":
                        stop_price = entry - config.stop_distance
                        take_profit_price = entry + config.take_profit_distance
                    elif side == "short":
                        stop_price = entry + config.stop_distance
                        take_profit_price = entry - config.take_profit_distance
                    else:
                        raise ValueError(f"unsupported trade side: {side}")
                    active = {
                        "side": side,
                        "entry_time": next_row["timestamp"],
                        "entry_price": entry,
                        "stop_price": stop_price,
                        "take_profit_price": take_profit_price,
                        "quantity": config.quantity,
                        "bars_held": 0,
                    }
                    signal_counts["entered"] += 1
                    signal_counts[side] += 1
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return FreqtradeEwoResult(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
