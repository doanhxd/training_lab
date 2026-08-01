from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trading_lab.models import StrategySpec


@dataclass(frozen=True)
class XauusdKnowledgePolicy:
    name: str
    version: str
    minimum_reward_risk: float
    preferred_sessions: tuple[str, ...]
    required_safety_filters: tuple[str, ...]
    preferred_confluence_features: tuple[str, ...]
    risk_rules: tuple[str, ...]
    research_caveats: tuple[str, ...]


XAUUSD_KNOWLEDGE_POLICY = XauusdKnowledgePolicy(
    name="xauusd-trading-knowledge",
    version="1.0.0",
    minimum_reward_risk=1.5,
    preferred_sessions=("london", "new_york"),
    required_safety_filters=("spread <= max_spread",),
    preferred_confluence_features=("session", "spread", "ema_20", "atr_14", "rsi_14", "rolling_volatility_20"),
    risk_rules=(
        "Use fixed maximum risk per trade; never widen stop-loss after entry.",
        "Prefer reward:risk >= 1:1.5 for XAUUSD research candidates.",
        "Avoid new entries around high-impact news unless a news calendar filter is available.",
        "Do not promote GC=F proxy results to live XAUUSD without broker data and real spread validation.",
        "Stop research candidates that fail cost stress, walk-forward, or side/regime robustness.",
    ),
    research_caveats=(
        "Yahoo GC=F is a gold futures proxy, not broker spot XAUUSD.",
        "Missing spread data requires synthetic cost assumptions.",
        "Short lookback intraday results can be regime-fit and require out-of-sample confirmation.",
    ),
)


def describe_policy() -> dict[str, Any]:
    return {
        "name": XAUUSD_KNOWLEDGE_POLICY.name,
        "version": XAUUSD_KNOWLEDGE_POLICY.version,
        "minimum_reward_risk": XAUUSD_KNOWLEDGE_POLICY.minimum_reward_risk,
        "preferred_sessions": list(XAUUSD_KNOWLEDGE_POLICY.preferred_sessions),
        "required_safety_filters": list(XAUUSD_KNOWLEDGE_POLICY.required_safety_filters),
        "preferred_confluence_features": list(XAUUSD_KNOWLEDGE_POLICY.preferred_confluence_features),
        "risk_rules": list(XAUUSD_KNOWLEDGE_POLICY.risk_rules),
        "research_caveats": list(XAUUSD_KNOWLEDGE_POLICY.research_caveats),
    }


def assess_strategy_against_policy(spec: StrategySpec, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    metadata = metadata or {}
    filters = " | ".join(spec.filters).lower()
    expressions = " | ".join(
        item
        for item in [spec.entry.long, spec.entry.short, spec.exit.stop_loss, spec.exit.take_profit, *spec.filters]
        if item
    ).lower()
    warnings: list[str] = []
    passed = True

    if not any(required.lower() in filters for required in XAUUSD_KNOWLEDGE_POLICY.required_safety_filters):
        passed = False
        warnings.append("missing spread safety filter")
    if "session == london" not in filters and "session == new_york" not in filters:
        warnings.append("no preferred London/New York session filter")
    if "atr_14" not in expressions:
        warnings.append("no ATR volatility reference for XAUUSD stop/target sizing")
    if "ema_" not in expressions and "sma_" not in expressions:
        warnings.append("no moving-average trend context")
    if "rsi_14" not in expressions:
        warnings.append("no momentum/exhaustion confirmation")
    if metadata.get("provider") == "yahoo" or metadata.get("ticker") == "GC=F":
        warnings.append("proxy data only; validate on broker XAUUSD before paper/live")
    if metadata.get("missing_spread_fraction", 0.0) == 1.0:
        warnings.append("spread unavailable; synthetic spread must be stress-tested")

    return {
        "passed_core_policy": passed,
        "warnings": warnings,
        "policy": describe_policy(),
    }


def research_recommendation_guard(summary: dict[str, Any]) -> str | None:
    best = summary.get("best_run") or {}
    profit_factor = float(best.get("profit_factor") or 0.0)
    passed_runs = int(summary.get("passed_runs") or 0)
    if passed_runs == 0:
        return "Knowledge policy: no live/paper promotion; continue offline research and improve robustness."
    if profit_factor < 1.2:
        return "Knowledge policy: paper sandbox only at most; edge is too thin for live XAUUSD."
    return "Knowledge policy: candidate still requires broker XAUUSD data, real spread, and news/session risk locks before any paper sandbox."
