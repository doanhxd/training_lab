from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from training_lab.config import OUTPUT_HERMES_DIR
from training_lab.io import ensure_parent
from training_lab.models import BacktestMetrics, StrategySpec, ValidationSummary


class StrategyMemoryRecord(BaseModel):
    memory_id: str
    strategy_fingerprint: str = ""
    created_at: datetime
    strategy_name: str
    skill_name: str
    thesis: str
    spec: dict[str, Any]
    dataset_id: str | None = None
    feature_set_id: str | None = None
    run_id: str | None = None
    metrics: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    status: str = "generated"
    rejection_reasons: list[str] = Field(default_factory=list)


class ResearchNote(BaseModel):
    note_id: str
    created_at: datetime
    topic: str
    content: str
    tags: list[str] = Field(default_factory=list)


class HermesMemoryStore:
    def __init__(self, base_dir: Path = OUTPUT_HERMES_DIR) -> None:
        self.base_dir = base_dir
        self.strategy_path = base_dir / "strategy_memory.jsonl"
        self.notes_path = base_dir / "research_notes.jsonl"

    def append_strategy(
        self,
        spec: StrategySpec,
        skill_name: str,
        status: str = "generated",
        dataset_id: str | None = None,
        feature_set_id: str | None = None,
        run_id: str | None = None,
        metrics: BacktestMetrics | None = None,
        validation: ValidationSummary | None = None,
    ) -> StrategyMemoryRecord:
        payload = spec.model_dump()
        fingerprint = strategy_fingerprint(payload)
        memory_id = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        record = StrategyMemoryRecord(
            memory_id=memory_id,
            strategy_fingerprint=fingerprint,
            created_at=datetime.now(UTC),
            strategy_name=spec.strategy_name,
            skill_name=skill_name,
            thesis=spec.thesis,
            spec=payload,
            dataset_id=dataset_id,
            feature_set_id=feature_set_id,
            run_id=run_id,
            metrics=metrics.model_dump() if metrics else None,
            validation=validation.model_dump() if validation else None,
            status=status,
            rejection_reasons=validation.reasons if validation else [],
        )
        self._append_jsonl(self.strategy_path, record.model_dump())
        return record

    def append_note(self, topic: str, content: str, tags: list[str] | None = None) -> ResearchNote:
        note_id = hashlib.sha256(f"{topic}::{content}".encode("utf-8")).hexdigest()[:16]
        note = ResearchNote(
            note_id=note_id,
            created_at=datetime.now(UTC),
            topic=topic,
            content=content,
            tags=tags or [],
        )
        self._append_jsonl(self.notes_path, note.model_dump())
        return note

    def list_strategies(self, limit: int = 20) -> list[StrategyMemoryRecord]:
        records = [StrategyMemoryRecord.model_validate(item) for item in self._read_jsonl(self.strategy_path)]
        return records[-limit:]

    def list_notes(self, limit: int = 20) -> list[ResearchNote]:
        notes = [ResearchNote.model_validate(item) for item in self._read_jsonl(self.notes_path)]
        return notes[-limit:]

    def seen_strategy_names(self) -> set[str]:
        return {record.strategy_name for record in self.list_strategies(limit=10_000)}

    def seen_strategy_fingerprints(self) -> set[str]:
        return {record.strategy_fingerprint for record in self.list_strategies(limit=10_000) if record.strategy_fingerprint}

    def recent_rejection_reasons(self, limit: int = 20) -> list[str]:
        reasons: list[str] = []
        for record in self.list_strategies(limit=limit):
            reasons.extend(record.rejection_reasons)
        return reasons

    def best_strategies(self, limit: int = 5) -> list[StrategyMemoryRecord]:
        records = [record for record in self.list_strategies(limit=10_000) if record.metrics]
        records.sort(key=lambda item: float(item.metrics.get("net_return", 0.0)), reverse=True)
        return records[:limit]

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        ensure_parent(path)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str, sort_keys=True) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def strategy_fingerprint(spec_payload: dict[str, Any]) -> str:
    comparable = dict(spec_payload)
    comparable.pop("strategy_name", None)
    comparable.pop("thesis", None)
    return hashlib.sha256(json.dumps(comparable, sort_keys=True).encode("utf-8")).hexdigest()[:16]
