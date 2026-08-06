from __future__ import annotations

from training_lab.backtest.execution.engine import _prepare_expression
from training_lab.features.engine import SUPPORTED_FEATURES
from training_lab.models import StrategySpec
from training_lab.strategies.compiler.expression import compile_expression


def validate_strategy_spec(spec: StrategySpec) -> None:
    supported = set(SUPPORTED_FEATURES) | {"max_spread", "high_impact_news"}
    expressions = [
        spec.entry.long,
        spec.entry.short,
        spec.exit.stop_loss,
        spec.exit.take_profit,
        *spec.filters,
    ]
    if spec.exit.time_exit:
        if not spec.exit.time_exit.endswith(" bars"):
            raise ValueError("time_exit must use '<n> bars' format")
        int(spec.exit.time_exit.split()[0])
    for expression in expressions:
        if not expression:
            continue
        compile_expression(_prepare_expression(expression), supported)
