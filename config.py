from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA_RAW_DIR = ROOT / "data" / "raw"
DATA_PROCESSED_DIR = ROOT / "data" / "processed"
OUTPUT_SPECS_DIR = ROOT / "outputs" / "specs"
OUTPUT_BACKTESTS_DIR = ROOT / "outputs" / "backtests"
OUTPUT_REPORTS_DIR = ROOT / "outputs" / "reports"
OUTPUT_HERMES_DIR = ROOT / "outputs" / "hermes"
