from __future__ import annotations

from pathlib import Path

import pandas as pd


def _normalize_column_name(column: str) -> str:
    return column.lower().strip().strip("<>").replace(" ", "_")


def _infer_price_point(series: pd.Series) -> float:
    decimals = 0
    for value in series.dropna().astype(str).head(100):
        if "." in value:
            decimals = max(decimals, len(value.split(".", 1)[1].strip()))
    return 10 ** (-decimals) if decimals else 1.0


def load_raw_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path, sep=None, engine="python")
    normalized = {_normalize_column_name(column): column for column in frame.columns}
    mt5_spread_points = "spread" in normalized and str(normalized["spread"]).strip().startswith("<")
    rename_map = {}
    aliases = {
        "timestamp": ["timestamp", "time", "datetime", "date"],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c"],
        "volume": ["volume", "tick_volume", "tickvol", "vol"],
        "spread": ["spread"],
    }
    if "date" in normalized and "time" in normalized:
        frame["timestamp"] = frame[normalized["date"]].astype(str).str.strip() + " " + frame[normalized["time"]].astype(str).str.strip()
        normalized["timestamp"] = "timestamp"
    for target, names in aliases.items():
        for name in names:
            if name in normalized:
                rename_map[normalized[name]] = target
                break
    frame = frame.rename(columns=rename_map)
    required = {"timestamp", "open", "high", "low", "close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    if mt5_spread_points and "spread" in frame:
        point = _infer_price_point(frame["open"])
        frame["spread"] = pd.to_numeric(frame["spread"], errors="coerce") * point
    return frame
