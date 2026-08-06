from __future__ import annotations

"""RSIQUI V3 FINAL variant.

Entry/signal logic is intentionally re-exported from the operator-supplied
root implementation so FINAL cannot drift from `rsiqui_v3_root.py`. Runner and
JSON config files may still define symbol/risk/spread execution guards.
"""

from training_lab.strategies.builtins.rsiqui_v3_root import (  # noqa: F401
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
