from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HermesSkillProfile:
    name: str
    description: str
    preferred_features: tuple[str, ...]
    avoid_if_recent_rejections_contain: tuple[str, ...] = ()


SKILL_PROFILES: dict[str, HermesSkillProfile] = {
    "london_breakout": HermesSkillProfile(
        name="london_breakout",
        description="Asian range compression followed by London session breakout, with spread and fakeout/retest awareness.",
        preferred_features=("session_high_asia", "session_low_asia", "atr_14", "session", "spread"),
        avoid_if_recent_rejections_contain=("spread", "too few trades"),
    ),
    "trend_pullback": HermesSkillProfile(
        name="trend_pullback",
        description="Top-down trend continuation using EMA/SMA alignment, shallow pullbacks, ATR stops, and R:R >= 1:1.5.",
        preferred_features=("ema_20", "sma_20", "atr_14", "rsi_14", "rolling_volatility_20"),
        avoid_if_recent_rejections_contain=("parameter perturbation",),
    ),
    "volatility_reversion": HermesSkillProfile(
        name="volatility_reversion",
        description="Mean reversion after stretched candle range or RSI extremes, only when session and spread conditions are safe.",
        preferred_features=("rsi_14", "range", "body", "atr_14", "rolling_volatility_20"),
        avoid_if_recent_rejections_contain=("drawdown",),
    ),
    "intraday_momentum": HermesSkillProfile(
        name="intraday_momentum",
        description="Session-filtered XAUUSD momentum with ATR expansion, EMA direction, spread guard, and fixed-risk research preference.",
        preferred_features=("ema_20", "atr_14", "range", "session", "rolling_volatility_20", "spread"),
        avoid_if_recent_rejections_contain=("overtrade", "excessive drawdown"),
    ),
}


def list_skill_profiles() -> list[HermesSkillProfile]:
    return list(SKILL_PROFILES.values())


def get_skill_profile(name: str | None) -> HermesSkillProfile:
    if name is None:
        return SKILL_PROFILES["london_breakout"]
    try:
        return SKILL_PROFILES[name]
    except KeyError as exc:
        raise ValueError(f"unknown Hermes skill: {name}") from exc


def choose_skill_from_memory(recent_rejection_reasons: list[str]) -> HermesSkillProfile:
    reasons = " ".join(recent_rejection_reasons).lower()
    for profile in SKILL_PROFILES.values():
        if not any(fragment in reasons for fragment in profile.avoid_if_recent_rejections_contain):
            return profile
    return SKILL_PROFILES["trend_pullback"]
