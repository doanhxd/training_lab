from __future__ import annotations

"""RSIQUI V3 ORI variant.

ORI preserves the operator-supplied root RSIQUI V3 entry/signal logic. The
extra `blocked_entry_hours_gmt7` field is an execution/backtest gate only; it
must not alter root signal preparation or entry conditions.
"""

from dataclasses import asdict, dataclass
from typing import Any

from trading_lab.strategies.builtins.rsiqui_v3_root import (
    RsiquiV3Config as _RootRsiquiV3Config,
    RsiquiV3Result,
    evaluate_rsiqui_v3_signal,
    prepare_rsiqui_v3_frame,
    rsiqui_v3_config_for_preset as _root_config_for_preset,
    run_rsiqui_v3_backtest,
)


@dataclass(frozen=True)
class RsiquiV3Config(_RootRsiquiV3Config):
    blocked_entry_hours_gmt7: tuple[int, ...] = ()


def rsiqui_v3_config_for_preset(preset: str, **overrides: Any) -> RsiquiV3Config:
    blocked = tuple(int(hour) for hour in overrides.pop("blocked_entry_hours_gmt7", ()))
    base = _root_config_for_preset(preset, **overrides)
    return RsiquiV3Config(**asdict(base), blocked_entry_hours_gmt7=blocked)


__all__ = [
    "RsiquiV3Config",
    "RsiquiV3Result",
    "evaluate_rsiqui_v3_signal",
    "prepare_rsiqui_v3_frame",
    "rsiqui_v3_config_for_preset",
    "run_rsiqui_v3_backtest",
]
