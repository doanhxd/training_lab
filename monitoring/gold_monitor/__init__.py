"""Standalone read-only GOLD Monitor package."""

from training_lab.monitoring.gold_monitor.adapter import AccountSnapshot, GoldPositionMonitor, HistoryDealView, HistoryStats, PositionView

__all__ = [
    "AccountSnapshot", "GoldMonitorApp", "GoldPositionMonitor", "HistoryDealView", "HistoryStats", "PositionView",
]


def __getattr__(name: str):
    if name == "GoldMonitorApp":
        from training_lab.monitoring.gold_monitor.app import GoldMonitorApp
        return GoldMonitorApp
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
