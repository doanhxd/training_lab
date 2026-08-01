from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from trading_lab.models import BacktestConfig, StrategySpec, TradeRecord
from trading_lab.strategies.compiler.expression import CompiledExpression, compile_expression


@dataclass
class CompiledStrategy:
    long_entry: CompiledExpression | None
    short_entry: CompiledExpression | None
    filters: list[CompiledExpression]
    stop_loss: CompiledExpression
    take_profit: CompiledExpression | None
    time_exit_bars: int | None


@dataclass(frozen=True)
class FixedLotConfig:
    initial_equity: float = 10_000.0
    volume_lots: float = 0.01
    price_value_per_lot: float = 100.0
    risk_usd: float = 10.0
    reward_usd: float = 10.0
    synthetic_spread: float = 0.5
    slippage_per_side: float = 0.05
    max_spread: float = 0.6
    time_exit_bars: int | None = None

    @property
    def quantity(self) -> float:
        return self.volume_lots * self.price_value_per_lot

    @property
    def stop_distance(self) -> float:
        return self.risk_usd / max(self.quantity, 1e-12)

    @property
    def take_profit_distance(self) -> float:
        return self.reward_usd / max(self.quantity, 1e-12)


def _prepare_expression(expression: str) -> str:
    return expression.replace("session == london", 'session == "london"').replace(
        "session == asia", 'session == "asia"'
    ).replace("session == new_york", 'session == "new_york"')


def compile_strategy(spec: StrategySpec, supported_names: set[str]) -> CompiledStrategy:
    return CompiledStrategy(
        long_entry=compile_expression(_prepare_expression(spec.entry.long), supported_names) if spec.entry.long else None,
        short_entry=compile_expression(_prepare_expression(spec.entry.short), supported_names) if spec.entry.short else None,
        filters=[compile_expression(_prepare_expression(item), supported_names | {"max_spread", "high_impact_news"}) for item in spec.filters],
        stop_loss=compile_expression(_prepare_expression(spec.exit.stop_loss), supported_names),
        take_profit=compile_expression(_prepare_expression(spec.exit.take_profit), supported_names) if spec.exit.take_profit else None,
        time_exit_bars=int(spec.exit.time_exit.split()[0]) if spec.exit.time_exit else None,
    )


def run_backtest(features: pd.DataFrame, spec: StrategySpec, config: BacktestConfig) -> tuple[list[TradeRecord], list[float]]:
    compiled = compile_strategy(spec, set(features.columns))
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    active_trade: dict | None = None
    for index in range(len(features) - 1):
        row = features.iloc[index]
        next_row = features.iloc[index + 1]
        spread = row["spread"] if pd.notna(row["spread"]) else config.synthetic_spread
        context = row.copy()
        context["spread"] = spread
        context["max_spread"] = config.synthetic_spread
        context["high_impact_news"] = False
        if active_trade is not None:
            active_trade["bars_held"] += 1
            stop_distance = compiled.stop_loss.evaluate(context)
            take_profit_distance = compiled.take_profit.evaluate(context) if compiled.take_profit else None
            exit_price = None
            if active_trade["side"] == "long":
                stop_price = active_trade["entry_price"] - stop_distance
                tp_price = active_trade["entry_price"] + take_profit_distance if take_profit_distance is not None else None
                if row["low"] <= stop_price:
                    exit_price = stop_price
                elif tp_price is not None and row["high"] >= tp_price:
                    exit_price = tp_price
            else:
                stop_price = active_trade["entry_price"] + stop_distance
                tp_price = active_trade["entry_price"] - take_profit_distance if take_profit_distance is not None else None
                if row["high"] >= stop_price:
                    exit_price = stop_price
                elif tp_price is not None and row["low"] <= tp_price:
                    exit_price = tp_price
            if exit_price is None and compiled.time_exit_bars is not None and active_trade["bars_held"] >= compiled.time_exit_bars:
                exit_price = next_row["open"]
            if exit_price is not None:
                direction = 1 if active_trade["side"] == "long" else -1
                pnl = (exit_price - active_trade["entry_price"]) * active_trade["quantity"] * direction
                pnl -= (spread + config.slippage_per_side) * active_trade["quantity"]
                equity += pnl
                trades.append(
                    TradeRecord(
                        side=active_trade["side"],
                        entry_time=active_trade["entry_time"],
                        exit_time=next_row["timestamp"],
                        entry_price=active_trade["entry_price"],
                        exit_price=exit_price,
                        quantity=active_trade["quantity"],
                        pnl=pnl,
                        bars_held=active_trade["bars_held"],
                    )
                )
                active_trade = None
        if active_trade is None:
            filters_pass = all(bool(item.evaluate(context)) for item in compiled.filters)
            if filters_pass:
                if compiled.long_entry and bool(compiled.long_entry.evaluate(context)):
                    stop_distance = max(float(compiled.stop_loss.evaluate(context)), 1e-9)
                    quantity = (equity * config.risk_fraction) / stop_distance
                    active_trade = {
                        "side": "long",
                        "entry_time": next_row["timestamp"],
                        "entry_price": next_row["open"],
                        "quantity": quantity,
                        "bars_held": 0,
                    }
                elif compiled.short_entry and bool(compiled.short_entry.evaluate(context)):
                    stop_distance = max(float(compiled.stop_loss.evaluate(context)), 1e-9)
                    quantity = (equity * config.risk_fraction) / stop_distance
                    active_trade = {
                        "side": "short",
                        "entry_time": next_row["timestamp"],
                        "entry_price": next_row["open"],
                        "quantity": quantity,
                        "bars_held": 0,
                    }
        equity_curve.append(equity)
    return trades, equity_curve


def run_fixed_lot_backtest(features: pd.DataFrame, spec: StrategySpec, config: FixedLotConfig) -> tuple[list[TradeRecord], list[float]]:
    compiled = compile_strategy(spec, set(features.columns))
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    active_trade: dict | None = None
    for index in range(len(features) - 1):
        row = features.iloc[index]
        next_row = features.iloc[index + 1]
        spread = row["spread"] if pd.notna(row["spread"]) else config.synthetic_spread
        context = row.copy()
        context["spread"] = spread
        context["max_spread"] = config.max_spread
        context["high_impact_news"] = False
        if active_trade is not None:
            active_trade["bars_held"] += 1
            exit_price = None
            if active_trade["side"] == "long":
                stop_price = active_trade["entry_price"] - config.stop_distance
                tp_price = active_trade["entry_price"] + config.take_profit_distance
                if row["low"] <= stop_price:
                    exit_price = stop_price
                elif row["high"] >= tp_price:
                    exit_price = tp_price
            else:
                stop_price = active_trade["entry_price"] + config.stop_distance
                tp_price = active_trade["entry_price"] - config.take_profit_distance
                if row["high"] >= stop_price:
                    exit_price = stop_price
                elif row["low"] <= tp_price:
                    exit_price = tp_price
            if exit_price is None and config.time_exit_bars is not None and active_trade["bars_held"] >= config.time_exit_bars:
                exit_price = next_row["open"]
            if exit_price is not None:
                direction = 1 if active_trade["side"] == "long" else -1
                pnl = (exit_price - active_trade["entry_price"]) * active_trade["quantity"] * direction
                pnl -= (spread + config.slippage_per_side) * active_trade["quantity"]
                equity += pnl
                trades.append(
                    TradeRecord(
                        side=active_trade["side"],
                        entry_time=active_trade["entry_time"],
                        exit_time=next_row["timestamp"],
                        entry_price=active_trade["entry_price"],
                        exit_price=exit_price,
                        quantity=active_trade["quantity"],
                        pnl=pnl,
                        bars_held=active_trade["bars_held"],
                    )
                )
                active_trade = None
        if active_trade is None and spread <= config.max_spread:
            filters_pass = all(bool(item.evaluate(context)) for item in compiled.filters)
            if filters_pass:
                if compiled.long_entry and bool(compiled.long_entry.evaluate(context)):
                    active_trade = {
                        "side": "long",
                        "entry_time": next_row["timestamp"],
                        "entry_price": next_row["open"],
                        "quantity": config.quantity,
                        "bars_held": 0,
                    }
                elif compiled.short_entry and bool(compiled.short_entry.evaluate(context)):
                    active_trade = {
                        "side": "short",
                        "entry_time": next_row["timestamp"],
                        "entry_price": next_row["open"],
                        "quantity": config.quantity,
                        "bars_held": 0,
                    }
        equity_curve.append(equity)
    return trades, equity_curve
