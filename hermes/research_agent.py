from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from training_lab.hermes.memory import HermesMemoryStore, ResearchNote
from training_lab.hermes.xauusd_knowledge import research_recommendation_guard


@dataclass(frozen=True)
class ResearchAgentSummary:
    dataset_id: str
    feature_set_id: str
    total_runs: int
    passed_runs: int
    best_run: dict[str, Any] | None
    top_rejection_reasons: list[tuple[str, int]]
    recommendation: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "feature_set_id": self.feature_set_id,
            "total_runs": self.total_runs,
            "passed_runs": self.passed_runs,
            "best_run": self.best_run,
            "top_rejection_reasons": self.top_rejection_reasons,
            "recommendation": self.recommendation,
        }


def summarize_research_cycle(dataset_id: str, feature_set_id: str, leaderboard: list[dict[str, Any]]) -> ResearchAgentSummary:
    passed = [row for row in leaderboard if row.get("passed")]
    best_run = leaderboard[0] if leaderboard else None
    reasons = Counter(reason for row in leaderboard for reason in row.get("reasons", []))
    recommendation = _recommendation(best_run, passed)
    return ResearchAgentSummary(
        dataset_id=dataset_id,
        feature_set_id=feature_set_id,
        total_runs=len(leaderboard),
        passed_runs=len(passed),
        best_run=best_run,
        top_rejection_reasons=reasons.most_common(5),
        recommendation=recommendation,
    )


def record_research_cycle(memory: HermesMemoryStore, summary: ResearchAgentSummary) -> ResearchNote:
    best = summary.best_run or {}
    content = (
        f"Auto-research cycle for dataset={summary.dataset_id}, feature_set={summary.feature_set_id}. "
        f"Runs={summary.total_runs}, passed={summary.passed_runs}. "
        f"Best={best.get('strategy_name')} run={best.get('run_id')} net_return={best.get('net_return')} "
        f"max_drawdown={best.get('max_drawdown')} profit_factor={best.get('profit_factor')} trades={best.get('total_trades')}. "
        f"Top rejection reasons={summary.top_rejection_reasons}. Recommendation={summary.recommendation}"
    )
    return memory.append_note(
        topic="auto_research_cycle",
        content=content,
        tags=["hermes", "auto-research", "xauusd", "15m", summary.dataset_id, summary.feature_set_id],
    )


def _recommendation(best_run: dict[str, Any] | None, passed: list[dict[str, Any]]) -> str:
    policy_guard = research_recommendation_guard({"best_run": best_run, "passed_runs": len(passed)})
    if not best_run:
        return "No candidate. Need more experiments or data."
    if passed:
        best_passed = passed[0]
        if float(best_passed.get("profit_factor") or 0.0) >= 1.1 and float(best_passed.get("max_drawdown") or 1.0) <= 0.15:
            return f"Paper-trade candidate only. Keep evaluator/risk fixed and monitor with broker spread. {policy_guard}"
        return f"Passed validation but not strong enough for live; paper/sandbox only. {policy_guard}"
    if float(best_run.get("net_return") or 0.0) > 0:
        return f"Positive but failed validation; research filters/robustness before paper trading. {policy_guard}"
    return f"No trade candidate. Continue offline research; do not paper/live trade this set. {policy_guard}"
