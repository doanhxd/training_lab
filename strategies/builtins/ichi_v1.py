from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from trading_lab.backtest.metrics.performance import compute_metrics
from trading_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class IchiV1Config:
    initial_equity: float = 10_000.0
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 20.0
    synthetic_spread: float = 0.5
    slippage_per_side: float = 0.05
    max_spread: float = 0.6
    session_filter: str = "all"
    allowed_entry_hours: tuple[int, ...] = ()
    buy_trend_above_senkou_level: int = 1
    buy_trend_bullish_level: int = 6
    buy_fan_magnitude_shift_value: int = 3
    buy_min_fan_magnitude_gain: float = 1.002

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
class IchiV1Result:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def ichi_v1_config_for_preset(preset: str, **overrides) -> IchiV1Config:
    if preset == "original":
        return IchiV1Config(**overrides)
    if preset == "gold-balanced":
        params = {
            "buy_trend_above_senkou_level": 1,
            "buy_trend_bullish_level": 4,
            "buy_fan_magnitude_shift_value": 2,
            "buy_min_fan_magnitude_gain": 1.001,
        }
        params.update(overrides)
        return IchiV1Config(**params)
    if preset == "gold-loose":
        params = {
            "buy_trend_above_senkou_level": 1,
            "buy_trend_bullish_level": 2,
            "buy_fan_magnitude_shift_value": 1,
            "buy_min_fan_magnitude_gain": 1.0005,
        }
        params.update(overrides)
        return IchiV1Config(**params)
    raise ValueError(f"unknown IchiV1 preset: {preset}")


def _heikin_ashi(data: pd.DataFrame) -> pd.DataFrame:
    ha = pd.DataFrame(index=data.index)
    ha["close"] = (data["open"] + data["high"] + data["low"] + data["close"]) / 4
    ha_open = np.zeros(len(data), dtype=float)
    if len(data):
        ha_open[0] = (float(data["open"].iloc[0]) + float(data["close"].iloc[0])) / 2
        for index in range(1, len(data)):
            ha_open[index] = (ha_open[index - 1] + float(ha["close"].iloc[index - 1])) / 2
    ha["open"] = ha_open
    ha["high"] = pd.concat([data["high"], ha["open"], ha["close"]], axis=1).max(axis=1)
    ha["low"] = pd.concat([data["low"], ha["open"], ha["close"]], axis=1).min(axis=1)
    return ha


def _atr(data: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = data["close"].shift(1)
    true_range = pd.concat(
        [data["high"] - data["low"], (data["high"] - prev_close).abs(), (data["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(length, min_periods=length).mean()


def _ichimoku(data: pd.DataFrame, conversion_line_period: int = 20, base_line_period: int = 60, displacement: int = 30) -> pd.DataFrame:
    conv_high = data["high"].rolling(conversion_line_period, min_periods=conversion_line_period).max()
    conv_low = data["low"].rolling(conversion_line_period, min_periods=conversion_line_period).min()
    base_high = data["high"].rolling(base_line_period, min_periods=base_line_period).max()
    base_low = data["low"].rolling(base_line_period, min_periods=base_line_period).min()
    tenkan = (conv_high + conv_low) / 2
    kijun = (base_high + base_low) / 2
    leading_a = ((tenkan + kijun) / 2).shift(displacement)
    leading_b = ((data["high"].rolling(120, min_periods=120).max() + data["low"].rolling(120, min_periods=120).min()) / 2).shift(displacement)
    return pd.DataFrame(
        {
            "tenkan_sen": tenkan,
            "kijun_sen": kijun,
            "senkou_a": leading_a,
            "senkou_b": leading_b,
            "cloud_green": leading_a > leading_b,
            "cloud_red": leading_a < leading_b,
        }
    )


def prepare_ichi_v1_frame(features: pd.DataFrame, config: IchiV1Config) -> pd.DataFrame:
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").reset_index(drop=True)
    ha = _heikin_ashi(data)
    data["ha_open"] = ha["open"]
    data["ha_high"] = ha["high"]
    data["ha_low"] = ha["low"]
    data["trend_close_5m"] = data["close"]
    data["trend_open_5m"] = data["ha_open"]
    for label, period in (("15m", 3), ("30m", 6), ("1h", 12), ("2h", 24), ("4h", 48), ("6h", 72), ("8h", 96)):
        data[f"trend_close_{label}"] = data["close"].ewm(span=period, adjust=False, min_periods=period).mean()
        data[f"trend_open_{label}"] = data["ha_open"].ewm(span=period, adjust=False, min_periods=period).mean()
    data["fan_magnitude"] = data["trend_close_1h"] / data["trend_close_8h"]
    data["fan_magnitude_gain"] = data["fan_magnitude"] / data["fan_magnitude"].shift(1)
    ichi = _ichimoku(data.assign(open=data["ha_open"], high=data["ha_high"], low=data["ha_low"]))
    for column in ichi.columns:
        data[column] = ichi[column]
    data["atr"] = data["atr_14"] if "atr_14" in data else _atr(data)
    return data


def evaluate_ichi_v1_signal(row: pd.Series, history: pd.DataFrame, index: int, config: IchiV1Config) -> bool:
    required = [row.get("senkou_a"), row.get("senkou_b"), row.get("fan_magnitude"), row.get("fan_magnitude_gain")]
    if not all(pd.notna(value) and np.isfinite(float(value)) for value in required):
        return False
    trend_labels = ["5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h"]
    for level, label in enumerate(trend_labels, start=1):
        if config.buy_trend_above_senkou_level >= level:
            if not (row[f"trend_close_{label}"] > row["senkou_a"] and row[f"trend_close_{label}"] > row["senkou_b"]):
                return False
    for level, label in enumerate(trend_labels, start=1):
        if config.buy_trend_bullish_level >= level:
            if not row[f"trend_close_{label}"] > row[f"trend_open_{label}"]:
                return False
    if not (row["fan_magnitude_gain"] >= config.buy_min_fan_magnitude_gain and row["fan_magnitude"] > 1):
        return False
    for shift in range(1, config.buy_fan_magnitude_shift_value + 1):
        if index - shift < 0 or not history["fan_magnitude"].iloc[index - shift] < row["fan_magnitude"]:
            return False
    return True


def run_ichi_v1_backtest(features: pd.DataFrame, config: IchiV1Config) -> IchiV1Result:
    data = prepare_ichi_v1_frame(features, config)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"long_signal": 0, "blocked_spread": 0, "entered_long": 0}
    active: dict | None = None
    warmup = 180
    for index in range(warmup, len(data) - 1):
        row = data.iloc[index]
        next_row = data.iloc[index + 1]
        spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
        if active is not None:
            active["bars_held"] += 1
            exit_price = None
            if row["low"] <= active["stop_price"]:
                exit_price = active["stop_price"]
            elif row["high"] >= active["take_profit_price"]:
                exit_price = active["take_profit_price"]
            if exit_price is not None:
                pnl = (float(exit_price) - active["entry_price"]) * active["quantity"]
                pnl -= (spread + config.slippage_per_side) * active["quantity"]
                equity += pnl
                trades.append(
                    TradeRecord(
                        side="long",
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
            if evaluate_ichi_v1_signal(row, data, index, config):
                signal_counts["long_signal"] += 1
                if spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    entry = float(next_row["open"])
                    active = {
                        "entry_time": next_row["timestamp"],
                        "entry_price": entry,
                        "quantity": config.quantity,
                        "stop_price": entry - config.stop_distance,
                        "take_profit_price": entry + config.take_profit_distance,
                        "bars_held": 0,
                    }
                    signal_counts["entered_long"] += 1
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return IchiV1Result(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
