from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from trading_lab.config import DATA_PROCESSED_DIR
from trading_lab.io import write_json
from trading_lab.models import FeatureSetMetadata


FEATURE_VERSION = "v1"
SUPPORTED_FEATURES = {
    "open",
    "high",
    "low",
    "close",
    "volume",
    "spread",
    "atr_14",
    "ema_20",
    "sma_20",
    "rolling_high_20",
    "rolling_low_20",
    "rolling_volatility_20",
    "session_high_asia",
    "session_low_asia",
    "session",
    "range",
    "body",
    "rsi_14",
}


def _session_label(hour: int) -> str:
    if 0 <= hour < 8:
        return "asia"
    if 8 <= hour < 16:
        return "london"
    return "new_york"


def build_features(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame["session"] = frame["timestamp"].dt.hour.map(_session_label)
    frame["range"] = frame["high"] - frame["low"]
    frame["body"] = (frame["close"] - frame["open"]).abs()
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_14"] = true_range.rolling(14, min_periods=14).mean()
    frame["sma_20"] = frame["close"].rolling(20, min_periods=20).mean()
    frame["ema_20"] = frame["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    frame["rolling_high_20"] = frame["high"].rolling(20, min_periods=20).max()
    frame["rolling_low_20"] = frame["low"].rolling(20, min_periods=20).min()
    frame["rolling_volatility_20"] = frame["close"].pct_change().rolling(20, min_periods=20).std(ddof=0)
    delta = frame["close"].diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.rolling(14, min_periods=14).mean()
    avg_loss = losses.rolling(14, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    frame["rsi_14"] = 100 - (100 / (1 + rs))
    day_key = frame["timestamp"].dt.floor("D")
    asia_mask = frame["session"] == "asia"
    asia_high = frame[asia_mask].groupby(day_key[asia_mask])["high"].cummax()
    asia_low = frame[asia_mask].groupby(day_key[asia_mask])["low"].cummin()
    frame["session_high_asia"] = asia_high.reindex(frame.index)
    frame["session_low_asia"] = asia_low.reindex(frame.index)
    frame["session_high_asia"] = frame.groupby(day_key)["session_high_asia"].ffill()
    frame["session_low_asia"] = frame.groupby(day_key)["session_low_asia"].ffill()
    return frame


def write_feature_set(dataset_id: str, timeframe: str, features: pd.DataFrame) -> FeatureSetMetadata:
    feature_set_id = hashlib.sha256(f"{dataset_id}::{FEATURE_VERSION}".encode("utf-8")).hexdigest()[:16]
    out_dir = DATA_PROCESSED_DIR / dataset_id / "features"
    out_dir.mkdir(parents=True, exist_ok=True)
    feature_path = out_dir / f"{feature_set_id}.csv"
    metadata_path = out_dir / f"{feature_set_id}.json"
    features.to_csv(feature_path, index=False)
    metadata = FeatureSetMetadata(
        dataset_id=dataset_id,
        feature_set_id=feature_set_id,
        timeframe=timeframe,
        feature_version=FEATURE_VERSION,
        row_count=len(features),
        created_at=datetime.now(UTC),
        feature_path=str(feature_path),
    )
    write_json(metadata_path, metadata.model_dump())
    return metadata
