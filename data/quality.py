from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class DataQualityReport:
    row_count: int
    start: str | None
    end: str | None
    duplicate_timestamps: int
    missing_ohlc_rows: int
    invalid_ohlc_rows: int
    missing_spread_fraction: float
    median_interval_minutes: float | None
    largest_gap_minutes: float | None
    readiness: str
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "row_count": self.row_count,
            "start": self.start,
            "end": self.end,
            "duplicate_timestamps": self.duplicate_timestamps,
            "missing_ohlc_rows": self.missing_ohlc_rows,
            "invalid_ohlc_rows": self.invalid_ohlc_rows,
            "missing_spread_fraction": self.missing_spread_fraction,
            "median_interval_minutes": self.median_interval_minutes,
            "largest_gap_minutes": self.largest_gap_minutes,
            "readiness": self.readiness,
            "reasons": list(self.reasons),
        }


def inspect_candles(frame: pd.DataFrame, min_rows: int = 2_000) -> DataQualityReport:
    data = frame.copy()
    data["timestamp"] = pd.to_datetime(data["timestamp"], utc=True, errors="coerce")
    data = data.sort_values("timestamp")
    required = ["open", "high", "low", "close"]
    missing_ohlc = int(data[required].isna().any(axis=1).sum()) if set(required).issubset(data.columns) else len(data)
    invalid_ohlc = int(
        (
            (data["low"] > data["high"])
            | (data["high"] < data[["open", "close"]].max(axis=1))
            | (data["low"] > data[["open", "close"]].min(axis=1))
        ).sum()
    ) if set(required).issubset(data.columns) else len(data)
    diffs = data["timestamp"].diff().dropna().dt.total_seconds() / 60
    duplicate_timestamps = int(data["timestamp"].duplicated().sum())
    missing_spread_fraction = float(data["spread"].isna().mean()) if "spread" in data else 1.0
    blocking_reasons: list[str] = []
    if len(data) < min_rows:
        blocking_reasons.append(f"row count below recommended minimum {min_rows}")
    if missing_ohlc:
        blocking_reasons.append("missing OHLC rows")
    if invalid_ohlc:
        blocking_reasons.append("invalid OHLC rows")
    if duplicate_timestamps:
        blocking_reasons.append("duplicate timestamps")
    if diffs.empty:
        blocking_reasons.append("not enough timestamps to infer interval")
    reasons = list(blocking_reasons)
    if not diffs.empty and diffs.max() > diffs.median() * 5:
        reasons.append("large timestamp gaps detected")
    if missing_spread_fraction == 1.0:
        reasons.append("spread unavailable; synthetic spread will be required")
    readiness = "ready" if not blocking_reasons else "not_ready"
    return DataQualityReport(
        row_count=len(data),
        start=data["timestamp"].iloc[0].isoformat() if len(data) else None,
        end=data["timestamp"].iloc[-1].isoformat() if len(data) else None,
        duplicate_timestamps=duplicate_timestamps,
        missing_ohlc_rows=missing_ohlc,
        invalid_ohlc_rows=invalid_ohlc,
        missing_spread_fraction=missing_spread_fraction,
        median_interval_minutes=float(diffs.median()) if not diffs.empty else None,
        largest_gap_minutes=float(diffs.max()) if not diffs.empty else None,
        readiness=readiness,
        reasons=tuple(reasons),
    )
