from __future__ import annotations

import numpy as np

from trading_lab.models import BacktestMetrics, TradeRecord


def compute_metrics(trades: list[TradeRecord], equity_curve: list[float], initial_equity: float) -> BacktestMetrics:
    returns = np.diff(equity_curve) / np.array(equity_curve[:-1] or [initial_equity])
    downside = returns[returns < 0]
    gross_profit = sum(max(trade.pnl, 0.0) for trade in trades)
    gross_loss = abs(sum(min(trade.pnl, 0.0) for trade in trades))
    if not trades or gross_loss == 0:
        profit_factor = None
    else:
        profit_factor = gross_profit / gross_loss
    wins = [trade for trade in trades if trade.pnl > 0]
    drawdowns = []
    peak = initial_equity
    for equity in equity_curve or [initial_equity]:
        peak = max(peak, equity)
        drawdowns.append((peak - equity) / peak if peak else 0.0)
    net_return = ((equity_curve[-1] if equity_curve else initial_equity) - initial_equity) / initial_equity
    return BacktestMetrics(
        net_return=net_return,
        max_drawdown=max(drawdowns, default=0.0),
        sharpe_ratio=float(np.sqrt(252) * returns.mean() / returns.std(ddof=0)) if len(returns) and returns.std(ddof=0) else 0.0,
        sortino_ratio=float(np.sqrt(252) * returns.mean() / downside.std(ddof=0)) if len(downside) and downside.std(ddof=0) else 0.0,
        profit_factor=profit_factor,
        win_rate=(len(wins) / len(trades)) if trades else 0.0,
        expectancy=(sum(trade.pnl for trade in trades) / len(trades)) if trades else 0.0,
        total_trades=len(trades),
        average_holding_period=(sum(trade.bars_held for trade in trades) / len(trades)) if trades else 0.0,
        exposure_time=(sum(trade.bars_held for trade in trades) / max(len(equity_curve), 1)) if trades else 0.0,
    )
