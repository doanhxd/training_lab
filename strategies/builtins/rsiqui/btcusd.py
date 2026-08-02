from __future__ import annotations

"""RSIQUI V3 BTCUSD variant.

BTCUSD uses the operator-supplied root RSIQUI V3 entry/signal logic exactly.
Only the runner/config money contract and symbol metadata are BTC-specific.
"""

from trading_lab.strategies.builtins.rsiqui_v3_root import (  # noqa: F401
    RsiquiV3Config,
    RsiquiV3Result,
    evaluate_rsiqui_v3_signal,
    prepare_rsiqui_v3_frame,
    rsiqui_v3_config_for_preset,
    run_rsiqui_v3_backtest,
)

__all__ = [
    "RsiquiV3Config",
    "RsiquiV3Result",
    "evaluate_rsiqui_v3_signal",
    "prepare_rsiqui_v3_frame",
    "rsiqui_v3_config_for_preset",
    "run_rsiqui_v3_backtest",
]
