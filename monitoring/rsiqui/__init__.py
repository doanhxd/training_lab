from __future__ import annotations

from typing import TYPE_CHECKING

from training_lab.monitoring.rsiqui.position_monitor import MonitorSnapshot, PositionView, RsiquiV3PositionMonitor

if TYPE_CHECKING:
    from training_lab.monitoring.rsiqui.monitor_gui import (
        APP_TITLE,
        REFRESH_MILLISECONDS,
        RsiquiV3MonitorApp,
        UiPalette,
        load_read_only_profile,
    )

__all__ = [
    "APP_TITLE", "REFRESH_MILLISECONDS", "UiPalette", "PositionView", "MonitorSnapshot",
    "RsiquiV3PositionMonitor", "RsiquiV3MonitorApp", "load_read_only_profile",
]


def __getattr__(name: str):
    if name in {"APP_TITLE", "REFRESH_MILLISECONDS", "RsiquiV3MonitorApp", "UiPalette", "load_read_only_profile"}:
        from training_lab.monitoring.rsiqui import monitor_gui

        return getattr(monitor_gui, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
