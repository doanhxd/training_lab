from __future__ import annotations

from dataclasses import replace
import json
from typing import Any

import pandas as pd

from training_lab.backtest.execution.engine import FixedLotConfig, run_fixed_lot_backtest
from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.hermes.xauusd_knowledge import assess_strategy_against_policy
from training_lab.models import StrategySpec, ValidationConfig, ValidationSummary
from training_lab.validation.benchmarks.baselines import benchmark_summary
from training_lab.validation.walk_forward.windows import make_walk_forward_windows


def _metrics_for_segment(features: pd.DataFrame, spec: StrategySpec, config: FixedLotConfig):
    trades, equity_curve = run_fixed_lot_backtest(features.reset_index(drop=True), spec, config)
    return compute_metrics(trades, equity_curve, config.initial_equity)


def validate_fixed_lot_strategy(
    features: pd.DataFrame,
    spec: StrategySpec,
    config: FixedLotConfig,
    validation_config: ValidationConfig | None = None,
    metadata: dict[str, Any] | None = None,
) -> ValidationSummary:
    validation_config = validation_config or ValidationConfig()
    reasons: list[str] = []
    base_metrics = _metrics_for_segment(features, spec, config)
    split = int(len(features) * (1 - validation_config.oos_fraction))
    in_sample = features.iloc[:split]
    out_sample = features.iloc[split:]
    in_metrics = _metrics_for_segment(in_sample, spec, config) if len(in_sample) > 30 else None
    out_metrics = _metrics_for_segment(out_sample, spec, config) if len(out_sample) > 30 else None

    stressed_config = replace(
        config,
        synthetic_spread=config.synthetic_spread * validation_config.spread_stress_multiplier,
        max_spread=max(config.max_spread, config.synthetic_spread * validation_config.spread_stress_multiplier),
    )
    stressed_metrics = _metrics_for_segment(features, spec, stressed_config)
    perturbed_config = replace(config, reward_usd=config.reward_usd * validation_config.perturbation_take_profit_multiplier)
    perturbed_metrics = _metrics_for_segment(features, spec, perturbed_config)

    windows = make_walk_forward_windows(
        features,
        validation_config.walk_forward_train_fraction,
        validation_config.walk_forward_test_fraction,
    )
    window_results = []
    for window in windows:
        test_frame = window.test(features)
        metrics = _metrics_for_segment(test_frame, spec, config) if len(test_frame) > 30 else None
        window_results.append({"window": window.index, "test_return": metrics.net_return if metrics else 0.0, "trades": metrics.total_trades if metrics else 0})
    profitable_windows = sum(1 for item in window_results if item["test_return"] > 0)
    profitable_fraction = profitable_windows / len(window_results) if window_results else 0.0
    benchmarks = benchmark_summary(features)
    policy = assess_strategy_against_policy(spec, metadata)

    if base_metrics.total_trades < validation_config.min_trades:
        reasons.append("too few trades")
    if base_metrics.net_return <= 0:
        reasons.append("net return is not positive")
    if base_metrics.max_drawdown > validation_config.max_drawdown:
        reasons.append("excessive drawdown")
    if (in_metrics and in_metrics.net_return <= 0) or (out_metrics and out_metrics.net_return <= 0):
        reasons.append("positive result not sustained across in-sample and out-of-sample")
    if len(windows) < validation_config.min_walk_forward_windows:
        reasons.append("not enough walk-forward windows")
    elif profitable_fraction < validation_config.min_profitable_windows_fraction:
        reasons.append("not robust across walk-forward windows")
    if stressed_metrics.net_return <= 0 or stressed_metrics.net_return < base_metrics.net_return * 0.5:
        reasons.append("strategy collapses under spread stress")
    if perturbed_metrics.net_return <= 0 or perturbed_metrics.net_return < base_metrics.net_return * 0.5:
        reasons.append("strategy is too sensitive to reward perturbation")
    if base_metrics.net_return <= max(benchmarks["no_trade_return"], benchmarks["sma_trend_return"]):
        reasons.append("strategy does not beat required benchmarks")
    if not policy["passed_core_policy"]:
        reasons.append("strategy violates XAUUSD knowledge policy")
    if metadata and (metadata.get("provider") == "yahoo" or metadata.get("ticker") == "GC=F"):
        reasons.append("research-only proxy data; broker XAUUSD validation required before live")
    if metadata and float(metadata.get("missing_spread_fraction") or 0.0) >= 1.0:
        reasons.append("research-only synthetic spread; real broker spread required before live")

    checks = {
        "in_sample_return": in_metrics.net_return if in_metrics else 0.0,
        "out_of_sample_return": out_metrics.net_return if out_metrics else 0.0,
        "walk_forward_windows": len(windows),
        "walk_forward_profitable_fraction": profitable_fraction,
        "walk_forward_results_json": json.dumps(window_results, sort_keys=True),
        "stressed_return": stressed_metrics.net_return,
        "stressed_profit_factor": stressed_metrics.profit_factor,
        "perturbed_return": perturbed_metrics.net_return,
        "perturbed_profit_factor": perturbed_metrics.profit_factor,
        "buy_hold_benchmark_return": benchmarks["buy_hold_return"],
        **benchmarks,
        "knowledge_policy_warnings": "; ".join(policy["warnings"]),
        **base_metrics.model_dump(),
    }
    return ValidationSummary(passed=not reasons, reasons=reasons, checks=checks)
