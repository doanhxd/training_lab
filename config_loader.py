from __future__ import annotations

import json
from pathlib import Path

from training_lab.models import RunConfig


def load_run_config(path: Path | None) -> RunConfig:
    if path is None:
        return RunConfig()
    payload = json.loads(path.read_text(encoding="utf-8"))
    return RunConfig.model_validate(payload)


def run_config_hash(config: RunConfig) -> str:
    import hashlib

    payload = config.model_dump_json(exclude_none=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
