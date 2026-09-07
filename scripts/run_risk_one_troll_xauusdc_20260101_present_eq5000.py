from __future__ import annotations

from pathlib import Path

from training_lab.scripts import run_risk_one_troll_xauusdc_20260101_present as base

base.CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "strategies" / "risk_one_troll_xauusdc_20260101_present_eq5000_vol005.json"

if __name__ == "__main__":
    base.main()
