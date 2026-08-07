from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.models import BacktestMetrics, TradeRecord


@dataclass(frozen=True)
class FreqtradeSupportConfig:
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
    allowed_signal_tags: tuple[str, ...] = ()
    allowed_entry_hours: tuple[int, ...] = ()
    buy_rmi: int = 49
    buy_cci: int = -116
    buy_srsi_fk: int = 32
    buy_cci_length: int = 25
    buy_rmi_length: int = 17
    buy_bb_width: float = 0.095
    buy_bb_delta: float = 0.025
    buy_roc_1h: int = 10
    buy_bb_width_1h: float = 1.074
    buy_bb_factor: float = 0.995
    buy_closedelta: float = 15.0
    buy_clucha_rocr_1h: float = 0.526
    buy_clucha_bbdelta_close: float = 0.049
    buy_clucha_closedelta_close: float = 0.017
    buy_clucha_bbdelta_tail: float = 1.146
    buy_ema_diff: float = 0.025
    buy_44_ma_offset: float = 0.982
    buy_44_ewo: float = -18.143
    buy_44_cti: float = -0.8
    buy_44_r_1h: float = -75.0
    nfix29_ewo: float = -10.0
    nfix29_cti: float = -0.9
    vwap_tpct_change: float = 0.053
    vwap_cti: float = -0.8
    vwap_rsi: float = 35.0
    wvap_tpct_change_1: float = 0.04
    nfinext32_sma_offset: float = 0.942
    nfinext32_cti: float = -0.86
    sma3_bb_delta_mult: float = 0.059
    sma3_ha_closedelta_mult: float = 0.023
    sma3_tail_mult: float = 0.24
    buy_37_ma_offset: float = 0.98
    buy_37_ewo: float = 9.8
    buy_37_rsi: float = 56.0
    buy_37_cti: float = -0.7
    buy_ema_open_mult_7: float = 0.03
    buy_cti_7: float = -0.89

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
class FreqtradeSupportResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: BacktestMetrics
    signal_counts: dict[str, int]


def freqtrade_support_config_for_preset(preset: str, **overrides) -> FreqtradeSupportConfig:
    if preset == "original":
        return FreqtradeSupportConfig(**overrides)
    if preset == "gold-balanced":
        params = {
            "buy_bb_width": 0.008,
            "buy_bb_delta": 0.0025,
            "buy_closedelta": 1.2,
            "buy_44_ma_offset": 0.998,
            "buy_44_ewo": -0.9,
            "buy_44_cti": -0.78,
            "buy_44_r_1h": -80.0,
            "nfix29_ewo": -0.85,
            "nfix29_cti": -0.82,
            "vwap_tpct_change": 0.0012,
            "vwap_cti": -0.78,
            "vwap_rsi": 38.0,
            "buy_37_ma_offset": 0.995,
            "buy_37_ewo": 0.6,
            "buy_37_cti": -0.72,
            "buy_ema_open_mult_7": 0.0009,
            "buy_cti_7": -0.80,
        }
        params.update(overrides)
        return FreqtradeSupportConfig(**params)
    if preset == "gold-loose":
        params = {
            "buy_bb_width": 0.004,
            "buy_bb_delta": 0.0012,
            "buy_closedelta": 0.6,
            "buy_44_ma_offset": 1.0,
            "buy_44_ewo": -0.45,
            "buy_44_cti": -0.65,
            "buy_44_r_1h": -70.0,
            "nfix29_ewo": -0.45,
            "nfix29_cti": -0.70,
            "vwap_tpct_change": 0.0008,
            "vwap_cti": -0.65,
            "vwap_rsi": 42.0,
            "buy_37_ma_offset": 1.0,
            "buy_37_ewo": 0.25,
            "buy_37_rsi": 62.0,
            "buy_37_cti": -0.60,
            "buy_ema_open_mult_7": 0.0005,
            "buy_cti_7": -0.65,
        }
        params.update(overrides)
        return FreqtradeSupportConfig(**params)
    if preset == "gene-gold":
        params = {
            "buy_rmi": 39,
            "buy_cci": -100,
            "buy_srsi_fk": 50,
            "buy_cci_length": 41,
            "buy_rmi_length": 15,
            "buy_bb_width": 0.006,
            "buy_bb_delta": 0.0018,
            "buy_roc_1h": 132,
            "buy_bb_width_1h": 0.04,
            "buy_clucha_bbdelta_close": 0.001,
            "buy_clucha_bbdelta_tail": 0.925,
            "buy_clucha_closedelta_close": 0.0015,
            "buy_clucha_rocr_1h": 1.0,
            "buy_ema_diff": 0.0008,
            "buy_bb_factor": 0.997,
            "buy_closedelta": 0.9,
            "buy_44_ma_offset": 0.998,
            "buy_44_ewo": -0.7,
            "buy_44_cti": -0.75,
            "buy_44_r_1h": -75.0,
            "buy_37_ma_offset": 0.997,
            "buy_37_ewo": 0.45,
            "buy_37_rsi": 58.0,
            "buy_37_cti": -0.66,
            "buy_ema_open_mult_7": 0.0007,
            "buy_cti_7": -0.72,
            "nfix29_ewo": -0.7,
            "nfix29_cti": -0.78,
            "vwap_tpct_change": 0.001,
            "vwap_cti": -0.72,
            "vwap_rsi": 40.0,
            "wvap_tpct_change_1": 0.0015,
            "nfinext32_sma_offset": 0.996,
            "nfinext32_cti": -0.74,
            "sma3_bb_delta_mult": 0.0025,
            "sma3_ha_closedelta_mult": 0.0018,
            "sma3_tail_mult": 0.35,
        }
        params.update(overrides)
        return FreqtradeSupportConfig(**params)
    raise ValueError(f"unknown freqtrade support preset: {preset}")


def _rsi(series: pd.Series, length: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=length - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=length - 1, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return (100 - (100 / (1 + rs))).fillna(50)


def _cti(series: pd.Series, length: int) -> pd.Series:
    trend = pd.Series(np.arange(length, dtype=float), index=range(length))

    def corr(values: np.ndarray) -> float:
        if np.isnan(values).any() or np.std(values) == 0:
            return np.nan
        return float(np.corrcoef(values, trend.values)[0, 1])

    return series.rolling(length, min_periods=length).apply(corr, raw=True)


def _williams_r(data: pd.DataFrame, period: int) -> pd.Series:
    highest = data["high"].rolling(period, min_periods=period).max()
    lowest = data["low"].rolling(period, min_periods=period).min()
    return ((highest - data["close"]) / (highest - lowest).replace(0, np.nan)) * -100


def _cci(data: pd.DataFrame, length: int) -> pd.Series:
    typical = (data["high"] + data["low"] + data["close"]) / 3
    mean = typical.rolling(length, min_periods=length).mean()
    mad = (typical - mean).abs().rolling(length, min_periods=length).mean()
    return (typical - mean) / (0.015 * mad.replace(0, np.nan))


def _cmf(data: pd.DataFrame, length: int) -> pd.Series:
    denom = (data["high"] - data["low"]).replace(0, np.nan)
    mfv = (((data["close"] - data["low"]) - (data["high"] - data["close"])) / denom).fillna(0) * data["volume"]
    return mfv.rolling(length, min_periods=1).sum() / data["volume"].rolling(length, min_periods=1).sum().replace(0, np.nan)


def _rmi(data: pd.DataFrame, length: int, mom: int = 4) -> pd.Series:
    momentum = data["close"] - data["close"].shift(mom)
    up = momentum.clip(lower=0)
    down = -momentum.clip(upper=0)
    ema_up = up.ewm(com=length - 1, adjust=False).mean()
    ema_down = down.ewm(com=length - 1, adjust=False).mean()
    return 100 - (100 / (1 + ema_up / ema_down.replace(0, np.nan)))


def _bollinger(series: pd.Series, length: int, stds: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    mid = series.rolling(length, min_periods=length).mean()
    std = series.rolling(length, min_periods=length).std(ddof=0)
    return mid - stds * std, mid, mid + stds * std


def _heikin_ashi(data: pd.DataFrame) -> pd.DataFrame:
    ha_close = (data["open"] + data["high"] + data["low"] + data["close"]) / 4
    ha_open = ha_close.copy()
    if len(data):
        ha_open.iloc[0] = (data["open"].iloc[0] + data["close"].iloc[0]) / 2
        for i in range(1, len(data)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2
    return pd.DataFrame(
        {
            "ha_open": ha_open,
            "ha_close": ha_close,
            "ha_high": pd.concat([data["high"], ha_open, ha_close], axis=1).max(axis=1),
            "ha_low": pd.concat([data["low"], ha_open, ha_close], axis=1).min(axis=1),
        }
    )


def _stoch_rsi(series: pd.Series, length: int = 15) -> pd.Series:
    rsi = _rsi(series, length)
    lowest = rsi.rolling(length, min_periods=length).min()
    highest = rsi.rolling(length, min_periods=length).max()
    fastk = 100 * (rsi - lowest) / (highest - lowest).replace(0, np.nan)
    return fastk.rolling(2, min_periods=2).mean()


def _pmax_state(data: pd.DataFrame) -> pd.Series:
    source = (data["high"] + data["low"] + data["open"] + data["close"]) / 4
    ma = source.ewm(span=9, adjust=False, min_periods=9).mean()
    prev_close = data["close"].shift(1)
    tr = pd.concat([(data["high"] - data["low"]), (data["high"] - prev_close).abs(), (data["low"] - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(10, min_periods=10).mean()
    return ma - (2.7 * atr)


def _top_percent_change(data: pd.DataFrame, length: int) -> pd.Series:
    if length == 0:
        return (data["open"] - data["close"]) / data["close"]
    return (data["open"].rolling(length, min_periods=length).max() - data["close"]) / data["close"]


def _add_1h_informative(data: pd.DataFrame) -> pd.DataFrame:
    hourly = data.set_index("timestamp").resample("1h", label="right", closed="right").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna().reset_index()
    ha = _heikin_ashi(hourly)
    hourly = pd.concat([hourly, ha], axis=1)
    hourly["rocr_1h"] = hourly["ha_close"] / hourly["ha_close"].shift(168)
    hourly["roc_1h"] = hourly["close"].pct_change(9) * 100
    hourly["r_480_1h"] = _williams_r(hourly, 480)
    hourly["r_84_1h"] = _williams_r(hourly, 84)
    hourly["cti_40_1h"] = _cti(hourly["close"], 40)
    typical = (hourly["high"] + hourly["low"] + hourly["close"]) / 3
    low, mid, high = _bollinger(typical, 20, 2)
    hourly["bb_width_1h"] = (high - low) / mid.replace(0, np.nan)
    support = hourly["low"].rolling(5, min_periods=5).apply(lambda row: float(all(row[i] > row[i + 1] for i in range(2)) and all(row[i] < row[i + 1] for i in range(2, 4))), raw=True)
    hourly["sup_level_1h"] = np.where(support > 0, np.where(hourly["close"] < hourly["open"], hourly["close"], hourly["open"]), np.nan)
    hourly["sup_level_1h"] = pd.Series(hourly["sup_level_1h"]).ffill()
    cols = ["timestamp", "rocr_1h", "roc_1h", "r_480_1h", "r_84_1h", "cti_40_1h", "bb_width_1h", "sup_level_1h"]
    return pd.merge_asof(data.sort_values("timestamp"), hourly[cols].sort_values("timestamp"), on="timestamp", direction="backward")


def prepare_freqtrade_support_frame(features: pd.DataFrame) -> pd.DataFrame:
    data = features.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True)
    data = data.sort_values("timestamp").reset_index(drop=True)
    ha = _heikin_ashi(data)
    data = pd.concat([data, ha], axis=1)
    typical = (data["high"] + data["low"] + data["close"]) / 3
    low2, mid2, high2 = _bollinger(typical, 20, 2)
    low3, _, _ = _bollinger(typical, 20, 3)
    low40, mid40, _ = _bollinger(typical, 40, 2)
    ha_typical = (data["ha_high"] + data["ha_low"] + data["ha_close"]) / 3
    lower_ha, mid_ha, _ = _bollinger(ha_typical, 40, 2)
    data["bb_lowerband2"] = low2
    data["bb_middleband2"] = mid2
    data["bb_width"] = (high2 - low2) / mid2.replace(0, np.nan)
    data["bb_lowerband3"] = low3
    data["bb_delta"] = (low2 - low3) / low2.replace(0, np.nan)
    data["bb_lowerband2_40"] = low40
    data["bb_middleband2_40"] = mid40
    data["bb_delta_cluc"] = (mid40 - low40).abs()
    data["lower"] = lower_ha
    data["mid"] = mid_ha
    data["bbdelta"] = (mid_ha - lower_ha).abs()
    data["closedelta"] = (data["ha_close"] - data["ha_close"].shift()).abs()
    data["ha_closedelta"] = data["closedelta"]
    data["tail"] = (data["ha_close"] - data["ha_low"]).abs()
    data["ema_200"] = data["close"].ewm(span=200, adjust=False, min_periods=200).mean()
    data["ema_50"] = data["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    data["ema_26"] = data["close"].ewm(span=26, adjust=False, min_periods=26).mean()
    data["ema_16"] = data["close"].ewm(span=16, adjust=False, min_periods=16).mean()
    data["ema_12"] = data["close"].ewm(span=12, adjust=False, min_periods=12).mean()
    data["ema_5"] = data["close"].ewm(span=5, adjust=False, min_periods=5).mean()
    data["ema_10"] = data["close"].ewm(span=10, adjust=False, min_periods=10).mean()
    data["sma_15"] = data["close"].rolling(15, min_periods=15).mean()
    data["ema_fast"] = data["ha_close"].ewm(span=3, adjust=False, min_periods=3).mean()
    data["ema_slow"] = data["ha_close"].ewm(span=50, adjust=False, min_periods=50).mean()
    data["sma_75"] = data["close"].rolling(75, min_periods=75).mean()
    data["rsi"] = _rsi(data["close"], 14)
    data["rsi_fast"] = _rsi(data["close"], 4)
    data["rsi_slow"] = _rsi(data["close"], 20)
    data["rsi_84"] = _rsi(data["close"], 84)
    data["rsi_112"] = _rsi(data["close"], 112)
    data["srsi_fk"] = _stoch_rsi(data["close"], 15)
    data["rmi"] = _rmi(data, 17)
    data["cci"] = _cci(data, 25)
    data["cti"] = _cti(data["close"], 20)
    data["r_14"] = _williams_r(data, 14)
    data["cmf"] = _cmf(data, 20)
    data["ewo"] = (data["close"].ewm(span=50, adjust=False, min_periods=50).mean() - data["close"].ewm(span=200, adjust=False, min_periods=200).mean()) / data["close"].replace(0, np.nan) * 100
    data["EWO"] = (data["close"].ewm(span=50, adjust=False, min_periods=50).mean() - data["close"].ewm(span=200, adjust=False, min_periods=200).mean()) / data["close"].replace(0, np.nan) * 100
    data["vwap"] = (typical * data["volume"]).rolling(20, min_periods=20).sum() / data["volume"].rolling(20, min_periods=20).sum().replace(0, np.nan)
    vwap_std = data["vwap"].rolling(20, min_periods=20).std(ddof=0)
    data["vwap_low"] = data["vwap"] - vwap_std
    data["tpct_change_0"] = _top_percent_change(data, 0)
    data["tpct_change_1"] = _top_percent_change(data, 1)
    data["tcp_percent_4"] = _top_percent_change(data, 4)
    data["pm"] = _pmax_state(data)
    data["source"] = (data["high"] + data["low"] + data["open"] + data["close"]) / 4
    data["pmax_thresh"] = data["source"].ewm(span=9, adjust=False, min_periods=9).mean()
    return _add_1h_informative(data)


def evaluate_freqtrade_support_signal(row: pd.Series, config: FreqtradeSupportConfig) -> tuple[bool, str | None]:
    tags: list[str] = []
    if (
        row["rmi"] < config.buy_rmi
        and row["cci"] <= config.buy_cci
        and row["srsi_fk"] < config.buy_srsi_fk
        and row["bb_delta"] > config.buy_bb_delta
        and row["bb_width"] > config.buy_bb_width
        and row["closedelta"] > row["close"] * config.buy_closedelta / 1000
        and row["close"] < row["bb_lowerband3"] * config.buy_bb_factor
        and row["roc_1h"] < config.buy_roc_1h
        and row["bb_width_1h"] < config.buy_bb_width_1h
    ):
        tags.append("DIP signal")
    if (
        row["bb_delta"] > config.buy_bb_delta
        and row["bb_width"] > config.buy_bb_width
        and row["closedelta"] > row["close"] * config.buy_closedelta / 1000
        and row["close"] < row["bb_lowerband3"] * config.buy_bb_factor
        and row["roc_1h"] < config.buy_roc_1h
        and row["bb_width_1h"] < config.buy_bb_width_1h
    ):
        tags.append("Break signal")
    if (
        row["rocr_1h"] > config.buy_clucha_rocr_1h
        and row["bb_lowerband2_40"] > 0
        and row["bb_delta_cluc"] > row["ha_close"] * config.buy_clucha_bbdelta_close
        and row["ha_closedelta"] > row["ha_close"] * config.buy_clucha_closedelta_close
        and row["tail"] < row["bb_delta_cluc"] * config.buy_clucha_bbdelta_tail
        and row["ha_close"] < row["bb_lowerband2_40"]
        and row["close"] > row["sup_level_1h"] * 0.88
    ):
        tags.append("cluc_HA")
    if (
        row["ema_200"] > row["ema_200_shift12"] * 1.01
        and row["ema_200"] > row["ema_200_shift48"] * 1.07
        and row["bb_delta_cluc"] > row["close"] * 0.056
        and row["closedelta"] > row["close"] * 0.01
        and row["tail"] < row["bb_delta_cluc"] * 0.5
        and row["close"] < row["bb_lowerband2_40"]
        and row["close"] <= row["close_shift1"]
        and row["close"] > row["ema_50"] * 0.912
    ):
        tags.append("NFIX39")
    if row["close"] > row["sup_level_1h"] * 0.72 and row["close"] < row["ema_16"] * 0.982 and row["EWO"] < config.nfix29_ewo and row["cti"] < config.nfix29_cti:
        tags.append("NFIX29")
    if (
        row["ema_26"] > row["ema_12"]
        and row["ema_26"] - row["ema_12"] > row["open"] * config.buy_ema_diff
        and row["ema_26_shift1"] - row["ema_12_shift1"] > row["open"] / 100
        and row["close"] < row["bb_lowerband2"] * config.buy_bb_factor
        and row["closedelta"] > row["close"] * config.buy_closedelta / 1000
    ):
        tags.append("local_uptrend")
    if row["close"] < row["vwap_low"] and row["tpct_change_0"] > config.vwap_tpct_change and row["cti"] < config.vwap_cti and row["rsi"] < config.vwap_rsi and row["rsi_84"] < 60 and row["rsi_112"] < 60:
        tags.append("vwap")
    if row["close"] < row["ema_16"] * config.buy_44_ma_offset and row["ewo"] < config.buy_44_ewo and row["cti"] < config.buy_44_cti and row["r_480_1h"] < config.buy_44_r_1h:
        tags.append("NFINext44")
    if row["pm"] > row["pmax_thresh"] and row["close"] < row["sma_75"] * config.buy_37_ma_offset and row["ewo"] > config.buy_37_ewo and row["rsi"] < config.buy_37_rsi and row["cti"] < config.buy_37_cti:
        tags.append("NFINext37")
    if row["ema_26"] > row["ema_12"] and row["ema_26"] - row["ema_12"] > row["open"] * config.buy_ema_open_mult_7 and row["ema_26_shift1"] - row["ema_12_shift1"] > row["open"] / 100 and row["cti"] < config.buy_cti_7:
        tags.append("NFINext7")
    if row["rsi_slow"] < row["rsi_slow_shift1"] and row["rsi_fast"] < 46 and row["rsi"] > 19 and row["close"] < row["sma_15"] * config.nfinext32_sma_offset and row["cti"] < config.nfinext32_cti:
        tags.append("NFINext32")
    if (
        row["bb_lowerband2_40"] > 0
        and row["bb_delta_cluc"] > row["close"] * config.sma3_bb_delta_mult
        and row["ha_closedelta"] > row["close"] * config.sma3_ha_closedelta_mult
        and row["tail"] < row["bb_delta_cluc"] * config.sma3_tail_mult
        and row["close"] < row["bb_lowerband2_40"]
        and row["close"] < row["close_shift1"]
    ):
        tags.append("sma_3")
    if row["close"] < row["vwap_low"] and row["tpct_change_1"] > config.wvap_tpct_change_1 and row["cti"] < config.vwap_cti and row["rsi"] < config.vwap_rsi and row["rsi_84"] < 60 and row["rsi_112"] < 60:
        tags.append("WVAP")
    for tag in tags:
        if not config.allowed_signal_tags or tag in config.allowed_signal_tags:
            return True, tag
    return False, None


def run_freqtrade_support_backtest(features: pd.DataFrame, config: FreqtradeSupportConfig) -> FreqtradeSupportResult:
    data = prepare_freqtrade_support_frame(features)
    for col, periods in [("ema_200", 12), ("ema_200", 48), ("close", 1), ("ema_26", 1), ("ema_12", 1), ("rsi_slow", 1)]:
        data[f"{col}_shift{periods}"] = data[col].shift(periods)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {tag: 0 for tag in ["DIP signal", "Break signal", "cluc_HA", "NFIX39", "NFIX29", "local_uptrend", "vwap", "NFINext44", "NFINext37", "NFINext7", "NFINext32", "sma_3", "WVAP"]}
    signal_counts |= {"blocked_spread": 0, "entered": 0, "long": 0, "short": 0}
    active: dict | None = None
    for index in range(500, len(data) - 1):
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
                trades.append(TradeRecord(side=active["side"], entry_time=active["entry_time"], exit_time=row["timestamp"], entry_price=active["entry_price"], exit_price=float(exit_price), quantity=active["quantity"], pnl=pnl, bars_held=active["bars_held"]))
                active = None
        if active is None:
            if config.session_filter != "all" and row.get("session") != config.session_filter:
                equity_curve.append(equity)
                continue
            if config.allowed_entry_hours and int(row["timestamp"].hour) not in config.allowed_entry_hours:
                equity_curve.append(equity)
                continue
            signal, tag = evaluate_freqtrade_support_signal(row, config)
            if signal and tag:
                signal_counts[tag] += 1
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
    return FreqtradeSupportResult(trades=trades, equity_curve=equity_curve, metrics=metrics, signal_counts=signal_counts)
