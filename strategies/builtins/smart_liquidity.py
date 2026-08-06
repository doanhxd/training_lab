from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class SmartLiquidityConfig:
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
    order_block_range: int = 2
    ob_volume_threshold: float = 0.8
    swing_high_lookback: int = 10
    swing_low_lookback: int = 10
    sweep_reversal_threshold: float = 0.002
    volume_spike_multiplier: float = 2.0
    rsi_buy_threshold: int = 30
    rsi_sell_threshold: int = 70
    williams_r_threshold: int = 80
    laguerre_rsi_threshold: int = 30
    vidya_length: int = 9
    vfi_length: int = 130
    vpci_length: int = 20
    cmf_length: int = 21
    bos_confirmation_bars: int = 1
    volume_confirmation_bars: int = 2
    volume_trend_period: int = 5
    volume_momentum_period: int = 5
    entry_delay_bars: int = 2
    min_primary_conditions: int = 1
    min_secondary_conditions: int = 2
    trade_bullish_regime: bool = True
    trade_sideways_regime: bool = True
    trade_bearish_regime: bool = False
    trade_choppy_regime: bool = False

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
class SmartLiquidityResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def smart_liquidity_config_for_preset(preset: str, **overrides) -> SmartLiquidityConfig:
    if preset == "original":
        return SmartLiquidityConfig(**overrides)
    if preset == "gold-balanced":
        params = {
            "ob_volume_threshold": 0.9,
            "sweep_reversal_threshold": 0.0008,
            "volume_spike_multiplier": 1.6,
            "rsi_buy_threshold": 38,
            "rsi_sell_threshold": 62,
            "williams_r_threshold": 78,
            "laguerre_rsi_threshold": 38,
            "min_primary_conditions": 1,
            "min_secondary_conditions": 2,
            "trade_bearish_regime": True,
        }
        params.update(overrides)
        return SmartLiquidityConfig(**params)
    if preset == "gold-loose":
        params = {
            "ob_volume_threshold": 0.7,
            "sweep_reversal_threshold": 0.0004,
            "volume_spike_multiplier": 1.35,
            "rsi_buy_threshold": 45,
            "rsi_sell_threshold": 55,
            "williams_r_threshold": 72,
            "laguerre_rsi_threshold": 45,
            "entry_delay_bars": 1,
            "min_primary_conditions": 1,
            "min_secondary_conditions": 1,
            "trade_bearish_regime": True,
            "trade_choppy_regime": False,
        }
        params.update(overrides)
        return SmartLiquidityConfig(**params)
    raise ValueError(f"unknown SmartLiquidity preset: {preset}")


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def _atr(data: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = data["close"].shift(1)
    true_range = pd.concat(
        [data["high"] - data["low"], (data["high"] - prev_close).abs(), (data["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(length, min_periods=length).mean()


def _williams_r(data: pd.DataFrame, period: int = 14) -> pd.Series:
    highest = data["high"].rolling(period, min_periods=period).max()
    lowest = data["low"].rolling(period, min_periods=period).min()
    return ((highest - data["close"]) / (highest - lowest).replace(0, np.nan)) * -100


def _laguerre_rsi(close: pd.Series, gamma: float = 0.75) -> pd.Series:
    values = close.astype(float).to_numpy()
    l0 = np.zeros(len(values))
    l1 = np.zeros(len(values))
    l2 = np.zeros(len(values))
    l3 = np.zeros(len(values))
    output = np.full(len(values), 50.0)
    if len(values) == 0:
        return pd.Series(output, index=close.index)
    l0[0] = l1[0] = l2[0] = l3[0] = values[0]
    for index in range(1, len(values)):
        l0[index] = (1 - gamma) * values[index] + gamma * l0[index - 1]
        l1[index] = -gamma * l0[index] + l0[index - 1] + gamma * l1[index - 1]
        l2[index] = -gamma * l1[index] + l1[index - 1] + gamma * l2[index - 1]
        l3[index] = -gamma * l2[index] + l2[index - 1] + gamma * l3[index - 1]
        cu = 0.0
        cd = 0.0
        for upper, lower in ((l0[index], l1[index]), (l1[index], l2[index]), (l2[index], l3[index])):
            if upper >= lower:
                cu += upper - lower
            else:
                cd += lower - upper
        output[index] = 100 * cu / (cu + cd) if cu + cd > 0 else output[index - 1]
    return pd.Series(output, index=close.index)


def _cmf(data: pd.DataFrame, length: int) -> pd.Series:
    denom = (data["high"] - data["low"]).replace(0, np.nan)
    mfv = (((data["close"] - data["low"]) - (data["high"] - data["close"])) / denom).fillna(0) * data["volume"]
    return mfv.rolling(length, min_periods=1).sum() / data["volume"].rolling(length, min_periods=1).sum().replace(0, np.nan)


def _prepare_smart_liquidity_frame(features: pd.DataFrame, config: SmartLiquidityConfig) -> pd.DataFrame:
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").reset_index(drop=True)
    data["rsi"] = _rsi(data["close"], 14)
    data["laguerre_rsi"] = _laguerre_rsi(data["close"])
    data["williams_r"] = _williams_r(data, 14)
    data["atr"] = data["atr_14"] if "atr_14" in data else _atr(data, 14)
    data["ema_20"] = data["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    data["ema_50"] = data["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    data["ema_200"] = data["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    data["vidya"] = data["close"].ewm(span=config.vidya_length, adjust=False, min_periods=config.vidya_length).mean()
    data["mmar"] = np.select(
        [
            (data["ema_20"] > data["ema_50"]) & (data["ema_50"] > data["ema_200"]),
            (data["ema_20"] < data["ema_50"]) & (data["ema_50"] < data["ema_200"]),
            ((data["ema_20"] - data["ema_50"]).abs() / data["ema_50"]) < 0.01,
        ],
        [1, 2, 3],
        default=4,
    )
    volume_mean = data["volume"].rolling(20, min_periods=20).mean()
    volume_std = data["volume"].rolling(20, min_periods=20).std()
    data["volume_mean"] = volume_mean
    data["volume_z_score"] = np.where(volume_std > 0, (data["volume"] - volume_mean) / volume_std, 0)
    data["volume_ratio"] = data["volume"] / volume_mean.replace(0, np.nan)
    data["volume_spike"] = data["volume_ratio"] > config.volume_spike_multiplier
    data["volume_trend"] = data["volume"].rolling(config.volume_trend_period, min_periods=config.volume_trend_period).mean() > volume_mean
    data["volume_momentum"] = data["volume"].pct_change(config.volume_momentum_period)
    data["volume_confirmed"] = (data["volume_ratio"] > 1.2) | (data["volume_spike"].rolling(config.volume_confirmation_bars, min_periods=1).sum() >= 1)
    data["trend_strength"] = (data["ema_20"] - data["ema_50"]).abs() / data["atr"].replace(0, np.nan)
    data["trend_bias"] = data["ema_20"] > data["ema_50"]
    data["vidya_trend"] = data["close"] > data["vidya"]
    data["price_change"] = data["close"].diff()
    bullish_ob = (data["price_change"] > 0) & (data["volume_ratio"] > config.ob_volume_threshold)
    bearish_ob = (data["price_change"] < 0) & (data["volume_ratio"] > config.ob_volume_threshold)
    data["bullish_ob_high"] = np.where(bullish_ob, data["high"].rolling(config.order_block_range, min_periods=config.order_block_range).max(), 0)
    data["bullish_ob_low"] = np.where(bullish_ob, data["low"].rolling(config.order_block_range, min_periods=config.order_block_range).min(), 0)
    data["bearish_ob_high"] = np.where(bearish_ob, data["high"].rolling(config.order_block_range, min_periods=config.order_block_range).max(), 0)
    data["bearish_ob_low"] = np.where(bearish_ob, data["low"].rolling(config.order_block_range, min_periods=config.order_block_range).min(), 0)
    data["ob_strength"] = np.where(bullish_ob | bearish_ob, data["volume_ratio"], 0)
    data["fvg_bullish"] = np.where(data["low"] > data["high"].shift(1), data["low"] - data["high"].shift(1), 0)
    data["fvg_bearish"] = np.where(data["high"] < data["low"].shift(1), data["low"].shift(1) - data["high"], 0)
    data["recent_high"] = data["high"].rolling(config.swing_high_lookback, min_periods=config.swing_high_lookback).max()
    data["recent_low"] = data["low"].rolling(config.swing_low_lookback, min_periods=config.swing_low_lookback).min()
    data["bos_bullish"] = data["close"] > data["recent_high"].shift(1)
    data["bos_bearish"] = data["close"] < data["recent_low"].shift(1)
    data["bos_confirmed"] = data["bos_bullish"].rolling(config.bos_confirmation_bars, min_periods=1).sum() >= 1
    recent_high_3 = data["high"].rolling(3, min_periods=3).max()
    recent_low_3 = data["low"].rolling(3, min_periods=3).min()
    data["liquidity_sweep_high"] = (data["high"] > recent_high_3.shift(1)) & (data["close"] < recent_high_3.shift(1) * (1 - config.sweep_reversal_threshold))
    data["liquidity_sweep_low"] = (data["low"] < recent_low_3.shift(1)) & (data["close"] > recent_low_3.shift(1) * (1 + config.sweep_reversal_threshold))
    signed_volume = np.sign(data["close"].diff()).fillna(0) * data["volume"]
    data["vfi"] = signed_volume.rolling(config.vfi_length, min_periods=20).sum() / data["volume"].rolling(config.vfi_length, min_periods=20).sum().replace(0, np.nan)
    vwma_num = (data["close"] * data["volume"]).rolling(config.vpci_length, min_periods=config.vpci_length).sum()
    vwma_den = data["volume"].rolling(config.vpci_length, min_periods=config.vpci_length).sum().replace(0, np.nan)
    vwma = vwma_num / vwma_den
    sma = data["close"].rolling(config.vpci_length, min_periods=config.vpci_length).mean()
    data["vpci"] = (vwma / sma.replace(0, np.nan)) - 1
    data["cmf"] = _cmf(data, config.cmf_length)
    data["htf_bullish_bias"] = data["ema_20"].rolling(3, min_periods=1).mean() > data["ema_50"].rolling(3, min_periods=1).mean()
    data["htf_bearish_bias"] = data["ema_20"].rolling(3, min_periods=1).mean() < data["ema_50"].rolling(3, min_periods=1).mean()
    data["market_regime_allowed"] = (
        ((data["mmar"] == 1) & config.trade_bullish_regime)
        | ((data["mmar"] == 2) & config.trade_bearish_regime)
        | ((data["mmar"] == 3) & config.trade_sideways_regime)
        | ((data["mmar"] == 4) & config.trade_choppy_regime)
    )
    data["entry_delay"] = True
    if config.entry_delay_bars > 1:
        data["entry_delay"] = data["volume_spike"].rolling(config.entry_delay_bars, min_periods=1).sum() >= 1
    return data


def evaluate_smart_liquidity_signal(row: pd.Series, config: SmartLiquidityConfig) -> str | None:
    required = [row.get("rsi"), row.get("williams_r"), row.get("laguerre_rsi"), row.get("trend_strength")]
    if not all(pd.notna(value) and np.isfinite(float(value)) for value in required):
        return None
    if not bool(row.get("market_regime_allowed", True)) or not bool(row.get("volume_confirmed", False)) or not bool(row.get("entry_delay", True)):
        return None
    long_primary = sum(
        [
            row["rsi"] < config.rsi_buy_threshold,
            row["williams_r"] > -config.williams_r_threshold,
            row["laguerre_rsi"] < config.laguerre_rsi_threshold,
            bool(row.get("liquidity_sweep_low", False)),
            row.get("bullish_ob_high", 0) > 0,
            row.get("fvg_bullish", 0) > 0,
        ]
    )
    long_secondary = sum(
        [
            bool(row.get("volume_spike", False)),
            row["trend_strength"] > 0.3,
            bool(row.get("htf_bullish_bias", True)),
            bool(row.get("bos_confirmed", False)),
            row.get("vfi", 0) > 0,
            row.get("vpci", 0) > 0,
            row.get("cmf", 0) > 0,
            bool(row.get("vidya_trend", False)),
        ]
    )
    short_primary = sum(
        [
            row["rsi"] > config.rsi_sell_threshold,
            row["williams_r"] < -100 + config.williams_r_threshold,
            row["laguerre_rsi"] > 100 - config.laguerre_rsi_threshold,
            bool(row.get("liquidity_sweep_high", False)),
            row.get("bearish_ob_low", 0) > 0,
            row.get("fvg_bearish", 0) > 0,
        ]
    )
    short_secondary = sum(
        [
            bool(row.get("volume_spike", False)),
            row["trend_strength"] > 0.3,
            bool(row.get("htf_bearish_bias", True)),
            bool(row.get("bos_bearish", False)),
            row.get("vfi", 0) < 0,
            row.get("vpci", 0) < 0,
            row.get("cmf", 0) < 0,
            not bool(row.get("vidya_trend", True)),
        ]
    )
    long_signal = long_primary >= config.min_primary_conditions and long_secondary >= config.min_secondary_conditions
    short_signal = short_primary >= config.min_primary_conditions and short_secondary >= config.min_secondary_conditions
    if long_signal and config.trade_side in {"both", "long"}:
        return "long"
    if short_signal and config.trade_side in {"both", "short"}:
        return "short"
    return None


def run_smart_liquidity_backtest(features: pd.DataFrame, config: SmartLiquidityConfig) -> SmartLiquidityResult:
    data = _prepare_smart_liquidity_frame(features, config)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {"long_signal": 0, "short_signal": 0, "blocked_spread": 0, "entered_long": 0, "entered_short": 0}
    active: dict | None = None
    warmup = max(200, config.vfi_length, config.swing_high_lookback, config.swing_low_lookback)
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
            side = evaluate_smart_liquidity_signal(row, config)
            if side is not None:
                signal_counts[f"{side}_signal"] += 1
                if spread > config.max_spread:
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
    return SmartLiquidityResult(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)


def prepare_smart_liquidity_frame(features: pd.DataFrame, config: SmartLiquidityConfig) -> pd.DataFrame:
    return _prepare_smart_liquidity_frame(features, config)
