from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from training_lab.config import DATA_PROCESSED_DIR
from training_lab.io import write_json
from training_lab.models import DatasetMetadata


CANONICAL_COLUMNS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "spread",
    "symbol",
    "timezone",
]


def normalize_candles(frame: pd.DataFrame, symbol: str, timeframe: str) -> pd.DataFrame:
    dataset = frame.copy()
    dataset["timestamp"] = pd.to_datetime(dataset["timestamp"], utc=True)
    dataset = dataset.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    dataset["symbol"] = symbol
    dataset["timezone"] = "UTC"
    if "volume" not in dataset:
        dataset["volume"] = pd.NA
    if "spread" not in dataset:
        dataset["spread"] = pd.NA
    numeric_cols = ["open", "high", "low", "close", "volume", "spread"]
    for column in numeric_cols:
        dataset[column] = pd.to_numeric(dataset[column], errors="coerce")
    dataset = dataset.dropna(subset=["timestamp", "open", "high", "low", "close"]) 
    invalid = (dataset["low"] > dataset["high"]) | (dataset["high"] < dataset[["open", "close"]].max(axis=1)) | (dataset["low"] > dataset[["open", "close"]].min(axis=1))
    dataset = dataset.loc[~invalid, CANONICAL_COLUMNS].reset_index(drop=True)
    dataset.attrs["timeframe"] = timeframe
    return dataset


def dataset_id_for(source_path: Path, symbol: str, timeframe: str) -> str:
    digest = hashlib.sha256(f"{source_path.resolve()}::{symbol}::{timeframe}".encode("utf-8")).hexdigest()
    return digest[:16]


def write_processed_dataset(dataset: pd.DataFrame, source_path: Path, symbol: str, timeframe: str) -> DatasetMetadata:
    dataset_id = dataset_id_for(source_path, symbol, timeframe)
    out_dir = DATA_PROCESSED_DIR / dataset_id
    out_dir.mkdir(parents=True, exist_ok=True)
    dataset_path = out_dir / "candles.csv"
    metadata_path = out_dir / "metadata.json"
    dataset.to_csv(dataset_path, index=False)
    metadata = DatasetMetadata(
        dataset_id=dataset_id,
        symbol=symbol,
        timeframe=timeframe,
        row_count=len(dataset),
        source_path=str(source_path),
        processed_path=str(dataset_path),
        created_at=datetime.now(UTC),
    )
    write_json(metadata_path, metadata.model_dump())
    return metadata
