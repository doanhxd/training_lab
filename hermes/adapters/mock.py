from __future__ import annotations

from trading_lab.hermes.memory import HermesMemoryStore, strategy_fingerprint
from trading_lab.hermes.skills import HermesSkillProfile, choose_skill_from_memory, get_skill_profile
from trading_lab.models import StrategySpec


def _strategy_payload_for(skill: HermesSkillProfile, suffix: int) -> dict:
    if skill.name == "trend_pullback":
        rsi_long = 48 + (suffix % 5)
        rsi_short = 52 - (suffix % 5)
        tp = 1.5 + (suffix % 4) * 0.2
        return {
            "market": "XAUUSD",
            "timeframe": "1h",
            "strategy_name": f"trend_pullback_ema_rsi_v{suffix}",
            "thesis": "Trade XAUUSD pullbacks in the direction of a short-term EMA/SMA trend.",
            "entry": {
                "long": f"close > ema_20 and rsi_14 > {rsi_long}",
                "short": f"close < ema_20 and rsi_14 < {rsi_short}",
            },
            "exit": {"stop_loss": "1.0 * atr_14", "take_profit": f"{tp:.1f} * atr_14", "time_exit": f"{8 + suffix % 5} bars"},
            "filters": ["spread <= max_spread", "rolling_volatility_20 > 0"],
            "risk": {"position_size": "0.5% equity risk; target reward:risk >= 1:1.5"},
            "assumptions": ["trend features use only closed candles", "avoid entries around high-impact news in paper/live execution"],
        }
    if skill.name == "volatility_reversion":
        long_threshold = 38 + (suffix % 5)
        short_threshold = 62 - (suffix % 5)
        stop = 1.0 + (suffix % 3) * 0.1
        return {
            "market": "XAUUSD",
            "timeframe": "1h",
            "strategy_name": f"volatility_reversion_rsi_v{suffix}",
            "thesis": "Fade stretched XAUUSD candles when RSI and candle range imply short-term exhaustion.",
            "entry": {
                "long": f"rsi_14 < {long_threshold} and range > atr_14",
                "short": f"rsi_14 > {short_threshold} and range > atr_14",
            },
            "exit": {"stop_loss": f"{stop:.1f} * atr_14", "take_profit": "1.8 * atr_14", "time_exit": f"{5 + suffix % 4} bars"},
            "filters": ["spread <= max_spread", "rolling_volatility_20 > 0"],
            "risk": {"position_size": "0.5% equity risk; target reward:risk >= 1:1.5"},
            "assumptions": ["mean reversion is evaluated without same-bar entry fills", "avoid entries around high-impact news in paper/live execution"],
        }
    if skill.name == "intraday_momentum":
        atr_multiplier = 0.7 + (suffix % 4) * 0.15
        stop = 0.8 + (suffix % 3) * 0.1
        take_profit = max(1.5 * stop, 1.2 + (suffix % 4) * 0.2)
        session = "london" if suffix % 2 else "new_york"
        return {
            "market": "XAUUSD",
            "timeframe": "15m",
            "strategy_name": f"intraday_momentum_ema_atr_v{suffix}",
            "thesis": "Trade 15m gold momentum only during liquid sessions when candle range expands with EMA direction.",
            "entry": {
                "long": f"close > ema_20 and range > {atr_multiplier:.2f} * atr_14 and rsi_14 > 52",
                "short": f"close < ema_20 and range > {atr_multiplier:.2f} * atr_14 and rsi_14 < 48",
            },
            "exit": {"stop_loss": f"{stop:.1f} * atr_14", "take_profit": f"{take_profit:.1f} * atr_14", "time_exit": f"{6 + suffix % 5} bars"},
            "filters": [f"session == {session}", "spread <= max_spread", "rolling_volatility_20 > 0"],
            "risk": {"position_size": "0.3% equity risk; target reward:risk >= 1:1.5"},
            "assumptions": ["intraday strategy uses synthetic spread unless broker spread is supplied", "avoid entries around FOMC/NFP/CPI/PCE windows in paper/live execution"],
        }
    breakout_multiplier = 0.8 + (suffix % 4) * 0.2
    take_profit = 1.8 + (suffix % 4) * 0.2
    return {
        "market": "XAUUSD",
        "timeframe": "1h",
        "strategy_name": f"london_range_breakout_v{suffix}",
        "thesis": "Breakout after Asian session compression during London open.",
        "entry": {
            "long": f"close > session_high_asia and range > {breakout_multiplier:.1f} * atr_14",
            "short": f"close < session_low_asia and range > {breakout_multiplier:.1f} * atr_14",
        },
        "exit": {
            "stop_loss": "1.2 * atr_14",
            "take_profit": f"{take_profit:.1f} * atr_14",
            "time_exit": f"{6 + suffix % 5} bars",
        },
        "filters": [
            "session == london",
            "spread <= max_spread",
        ],
        "risk": {"position_size": "0.5% equity risk; target reward:risk >= 1:1.5"},
        "assumptions": ["synthetic spread model if raw spread unavailable", "avoid entries around high-impact news in paper/live execution"],
    }


def generate_mock_strategy(skill_name: str | None = None, memory: HermesMemoryStore | None = None) -> StrategySpec:
    memory = memory or HermesMemoryStore()
    skill = get_skill_profile(skill_name) if skill_name else choose_skill_from_memory(memory.recent_rejection_reasons())
    seen_names = memory.seen_strategy_names()
    seen_fingerprints = memory.seen_strategy_fingerprints()
    suffix = 1
    while True:
        payload = _strategy_payload_for(skill, suffix)
        if payload["strategy_name"] not in seen_names and strategy_fingerprint(payload) not in seen_fingerprints:
            return StrategySpec.model_validate(payload)
        suffix += 1


def generate_and_remember_strategy(skill_name: str | None = None, memory: HermesMemoryStore | None = None) -> StrategySpec:
    memory = memory or HermesMemoryStore()
    spec = generate_mock_strategy(skill_name=skill_name, memory=memory)
    skill = get_skill_profile(skill_name) if skill_name else choose_skill_from_memory(memory.recent_rejection_reasons())
    memory.append_strategy(spec, skill_name=skill.name, status="generated")
    return spec


def generate_legacy_mock_strategy() -> StrategySpec:
    return StrategySpec.model_validate(
        {
            "market": "XAUUSD",
            "timeframe": "1h",
            "strategy_name": "london_range_breakout_v1",
            "thesis": "Breakout after Asian session compression during London open.",
            "entry": {
                "long": "close > session_high_asia and volume > sma_20",
                "short": "close < session_low_asia and volume > sma_20",
            },
            "exit": {
                "stop_loss": "1.2 * atr_14",
                "take_profit": "2.0 * atr_14",
                "time_exit": "8 bars",
            },
            "filters": [
                "session == london",
                "spread <= max_spread",
            ],
            "risk": {"position_size": "0.5% equity risk"},
            "assumptions": ["synthetic spread model if raw spread unavailable"],
        }
    )
