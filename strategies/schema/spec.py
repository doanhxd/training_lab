from __future__ import annotations

import json
from pathlib import Path

from trading_lab.models import StrategySpec


def load_strategy_spec(path: Path) -> StrategySpec:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if "normalized" in payload:
        payload = payload["normalized"]
    return StrategySpec.model_validate(payload)
