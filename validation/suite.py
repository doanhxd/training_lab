from __future__ import annotations

import pandas as pd

from trading_lab.backtest.execution.engine import run_backtest
from trading_lab.backtest.metrics.performance import compute_metrics
from trading_lab.models import BacktestConfig, StrategySpec, ValidationConfig, ValidationSummary
from trading_lab.validation.benchmarks.baselines import benchmark_summary
from trading_lab.validation.walk_forward.windows import make_walk_forward_windows


def _segment_metrics(features: pd.DataFrame, spec: StrategySpec, config: BacktestConfig) -> float:
    trades, equity_curve = run_backtest(features, spec, config)
    return compute_metrics(trades, equity_curve, config.initial_equity).net_return


def validate_strategy(
    features: pd.DataFrame,
    spec: StrategySpec,
    config: BacktestConfig,
    validation_config: ValidationConfig | None = None,
) -> ValidationSummary:
    validation_config = validation_config or ValidationConfig()
    reasons: list[str] = []
    split = int(len(features) * (1 - validation_config.oos_fraction))
    in_sample = features.iloc[:split].reset_index(drop=True)
    out_sample = features.iloc[split:].reset_index(drop=True)
    in_return = _segment_metrics(in_sample, spec, config) if len(in_sample) > 30 else 0.0
    out_return = _segment_metrics(out_sample, spec, config) if len(out_sample) > 30 else 0.0
    base_trades, base_equity = run_backtest(features, spec, config)
    base_metrics = compute_metrics(base_trades, base_equity, config.initial_equity)
    stressed = BacktestConfig(
        **(config.model_dump() | {"synthetic_spread": config.synthetic_spread * validation_config.spread_stress_multiplier})
    )
    stressed_return = _segment_metrics(features, spec, stressed)
    perturbed = BacktestConfig(**config.model_dump())
    perturbed_spec = spec.model_copy(deep=True)
    if perturbed_spec.exit.take_profit and "* atr_14" in perturbed_spec.exit.take_profit:
        multiplier = float(perturbed_spec.exit.take_profit.split("*")[0].strip())
        perturbed_spec.exit.take_profit = f"{multiplier * validation_config.perturbation_take_profit_multiplier:.2f} * atr_14"
    perturbed_return = _segment_metrics(features, perturbed_spec, perturbed)
    benchmarks = benchmark_summary(features)
    windows = make_walk_forward_windows(
        features,
        validation_config.walk_forward_train_fraction,
        validation_config.walk_forward_test_fraction,
    )
    window_results = [
        {
            "window": window.index,
            "test_return": _segment_metrics(window.test(features), spec, config) if len(window.test(features)) > 30 else 0.0,
        }
        for window in windows
    ]
    profitable_windows = sum(1 for item in window_results if item["test_return"] > 0)
    profitable_fraction = profitable_windows / len(window_results) if window_results else 0.0
    if base_metrics.total_trades < validation_config.min_trades:
        reasons.append("too few trades")
    if base_metrics.max_drawdown > validation_config.max_drawdown:
        reasons.append("excessive drawdown")
    if in_return <= 0 or out_return <= 0:
        reasons.append("positive result not sustained across in-sample and out-of-sample")
    if len(windows) < validation_config.min_walk_forward_windows:
        reasons.append("not enough walk-forward windows")
    elif profitable_fraction < validation_config.min_profitable_windows_fraction:
        reasons.append("not robust across walk-forward windows")
    if stressed_return < base_metrics.net_return * 0.5:
        reasons.append("strategy collapses under spread stress")
    if perturbed_return < base_metrics.net_return * 0.5:
        reasons.append("strategy is too sensitive to parameter perturbation")
    if base_metrics.net_return <= max(benchmarks["no_trade_return"], benchmarks["sma_trend_return"]):
        reasons.append("strategy does not beat required benchmarks")
    checks = {
        "in_sample_return": in_return,
        "out_of_sample_return": out_return,
        "walk_forward_windows": len(windows),
        "walk_forward_profitable_fraction": profitable_fraction,
        "stressed_return": stressed_return,
        "perturbed_return": perturbed_return,
        "buy_hold_benchmark_return": benchmarks["buy_hold_return"],
        **benchmarks,
        **base_metrics.model_dump(),
    }
    return ValidationSummary(passed=not reasons, reasons=reasons, checks=checks)
