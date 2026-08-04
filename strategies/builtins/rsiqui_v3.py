"""Compatibility export for the active RSIQUI V3 FINAL contract."""

from trading_lab.strategies.builtins.rsiqui.final import (
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
