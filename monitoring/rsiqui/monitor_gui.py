from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
import json
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

import pandas as pd

from training_lab.monitoring.rsiqui.position_monitor import MonitorSnapshot, RsiquiV3PositionMonitor, RunnerView
from training_lab.monitoring.rsiqui.mt5_account_scanner import Mt5TerminalCandidate, load_cached_candidates, open_remote_desktop, scan_mt5_terminals
from training_lab.telegram_notifier import TelegramNotifier, TelegramSettings, format_signal_message



APP_TITLE = "RSIQUI V3 • GOLD Trader"
# Display-only account identity for the GUI card. The live MT5 snapshot is not
# used for these two labels until the hard-coded display is intentionally removed.
DISPLAY_ACCOUNT_LOGIN = "#198384858"
DISPLAY_ACCOUNT_SERVER = "HFMarketsGlobal-Live16"
REFRESH_MILLISECONDS = 2_000
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "configs" / "strategies" / "rsiqui"
GMT_PLUS_7 = timezone(timedelta(hours=7))



class UiPalette:
    """Semantic palettes for the GOLD TRADER dashboard."""

    TELEGRAM_BLUE = "#2AABEE"

    DARK = {
        "mode": "dark", "app": "#0F1722", "sidebar": "#111C2C", "card": "#172334", "card_alt": "#1C2B3F",
        "border": "#2B3B50", "text": "#F7FAFC", "muted": "#9AAAC0", "nav_text": "#F7FAFC", "nav_muted": "#B7C5D8", "accent": "#D9AA54", "close": "#B4235A",
        "accent_dark": "#B9842B", "success": "#35C78A", "danger": "#F0727F", "warning": "#E3A64D", "info": "#69A8FF",
        "table": "#142132", "table_alt": "#192A3F", "badge_fg": "#F8FBFF",
    }
    LIGHT = {
        "mode": "light", "app": "#F5F7FB", "sidebar": "#111C2C", "card": "#FFFFFF", "card_alt": "#EEF2F6",
        "border": "#DCE3EB", "text": "#142235", "muted": "#687B91", "nav_text": "#F3F7FC", "nav_muted": "#B7C5D8", "accent": "#B7791F", "close": "#B4235A",
        "accent_dark": "#926017", "success": "#118A61", "danger": "#D04454", "warning": "#B7791F", "info": "#2563EB",
        "table": "#FFFFFF", "table_alt": "#F5F7FA", "badge_fg": "#FFFFFF",
    }

    @classmethod
    def for_mode(cls, mode: str) -> dict[str, str]:
        return dict(cls.LIGHT if mode == "light" else cls.DARK)

    @classmethod
    def apply_mode(cls, mode: str) -> None:
        colors = cls.for_mode(mode)
        for key, value in colors.items():
            setattr(cls, key.upper(), value)


UiPalette.apply_mode("light")


@dataclass(frozen=True)
class StrategySelection:
    key: str
    label: str
    config_path: Path


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    badge: str
    message: str
    badge_color: str
    telegram_message: str | None = None


def format_telegram_signal_message(
    *,
    symbol: str,
    timeframe: str,
    eta: str,
    side: str,
    request: dict,
    blocked: bool = False,
) -> str:
    """Format the operator-approved Telegram alert from one immutable signal preview."""
    message = format_signal_message(
        symbol=f"XAUUSD",
        side=side,
        request=request,
        timeframe=f"Next M5 ({eta})",
    )
    return message


STRATEGY_SELECTIONS = {
    "rsiqui_v3_final_x": StrategySelection("rsiqui_v3_final_x", "F X $5K", CONFIG_ROOT / "final_x_m5.json"),
    "rsiqui_v3_final": StrategySelection("rsiqui_v3_final", "F Root", CONFIG_ROOT / "final_m5.json"),
    "rsiqui_v3_final_trailing": StrategySelection("rsiqui_v3_final_trailing", "F TRL", CONFIG_ROOT / "final_trailing_m5.json"),
    "rsiqui_v3_btcusd": StrategySelection("rsiqui_v3_btcusd", "BTC", CONFIG_ROOT / "btcusd_m5.json"),
}

RUNNER_SCRIPT_BY_STRATEGY = {
    "rsiqui_v3_final": PROJECT_ROOT / "runners" / "mt5" / "rsiqui_final.py",
    "rsiqui_v3_final_trailing": PROJECT_ROOT / "runners" / "mt5" / "rsiqui_final_trailing.py",
    "rsiqui_v3_final_x": PROJECT_ROOT / "runners" / "mt5" / "rsiqui_final_trailing.py",
    "rsiqui_v3_btcusd": PROJECT_ROOT / "runners" / "mt5" / "rsiqui_btcusd.py",
}

RUNNER_MODULE_BY_STRATEGY = {
    "rsiqui_v3_final": "training_lab.runners.mt5.rsiqui_final",
    "rsiqui_v3_final_trailing": "training_lab.runners.mt5.rsiqui_final_trailing",
    "rsiqui_v3_final_x": "training_lab.runners.mt5.rsiqui_final_trailing",
    "rsiqui_v3_btcusd": "training_lab.runners.mt5.rsiqui_btcusd",
}


def _load_json(path: str | Path) -> dict:
    config_path = Path(path)
    return json.loads(config_path.read_text(encoding="utf-8"))


def _strategy_loader_for_payload(payload: dict):
    strategy = str(payload.get("strategy", "")).strip().lower()
    if strategy == "rsiqui-v3-final":
        from training_lab.runners.mt5.rsiqui_final import load_config

        return "rsiqui_v3_final", load_config
    if strategy in {"rsiqui-v3-final-trailing", "rsiqui-v3-final-x"}:
        from training_lab.runners.mt5.rsiqui_final_trailing import load_config

        return ("rsiqui_v3_final_x" if strategy.endswith("-x") else "rsiqui_v3_final_trailing"), load_config
    if strategy == "rsiqui-v3-btcusd":
        from training_lab.runners.mt5.rsiqui_btcusd import load_config

        return "rsiqui_v3_btcusd", load_config
    raise ValueError(f"Unsupported RSIQUI strategy payload: {strategy or '<missing>'}")


def _strategy_runtime(strategy_key: str):
    if strategy_key in {"rsiqui_v3_final", "rsiqui_v3_final_trailing", "rsiqui_v3_final_x"}:
        from training_lab.strategies.builtins.rsiqui.final import evaluate_rsiqui_v3_signal, prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset

        return prepare_rsiqui_v3_frame, evaluate_rsiqui_v3_signal, rsiqui_v3_config_for_preset
    if strategy_key == "rsiqui_v3_btcusd":
        from training_lab.strategies.builtins.rsiqui.btcusd import evaluate_rsiqui_v3_signal, prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset

        return prepare_rsiqui_v3_frame, evaluate_rsiqui_v3_signal, rsiqui_v3_config_for_preset
    raise ValueError(f"Unsupported strategy runtime: {strategy_key}")


def load_read_only_profile(config_path: str | Path) -> dict[str, str | float]:
    """Read the RSIQUI contract for display and signal preview only."""
    payload = _load_json(config_path)
    strategy_key, load_config = _strategy_loader_for_payload(payload)
    config = load_config(config_path)
    return {
        "strategy_key": strategy_key,
        "strategy_name": payload["strategy"],
        "timeframe": config.timeframe,
        "entry_mode": str(getattr(config, "entry_mode", "close_confirm")),
        "preset": config.preset,
        "side": config.trade_side,
        "symbol": str(payload.get("symbol", getattr(config, "symbol", "XAUUSD"))),
        "volume": float(payload["volume"]),
        "price_value_per_lot": float(payload.get("price_value_per_lot", 100.0)),
        "risk_usd": float(payload["risk_usd"]),
        "reward_usd": float(payload["reward_usd"]),
        "max_spread": float(payload.get("max_spread", 0.0)),
    }


class RsiquiV3MonitorApp(tk.Tk):
    """Modern GOLD TRADER observer shell with signal preview and runner status."""

    def __init__(
        self,
        monitor: RsiquiV3PositionMonitor,
        profile: dict[str, str | float],
        *,
        refresh_ms: int = REFRESH_MILLISECONDS,
        clock: Callable[[], datetime] = lambda: datetime.now(tz=GMT_PLUS_7),
    ) -> None:
        super().__init__()
        self.monitor = monitor
        self.refresh_ms = refresh_ms
        self.clock = clock
        self._closed = False
        self._refresh_after_id: str | None = None
        self._refresh_in_progress = False
        self._theme_mode = "light"
        self._latest_snapshot: MonitorSnapshot | None = None
        self._log_history: list[LogEntry] = []
        self._last_signal_bar_by_strategy: dict[str, int] = {}
        self._telegram_notifier = TelegramNotifier(TelegramSettings.from_environment(enabled=True))
        self.shell: tk.Frame | None = None
        self._strategy_profiles = {key: load_read_only_profile(selection.config_path) for key, selection in STRATEGY_SELECTIONS.items()}
        current_strategy_key = str(profile.get("strategy_key", "rsiqui_v3_final_x"))
        if current_strategy_key not in self._strategy_profiles:
            current_strategy_key = "rsiqui_v3_final_x"
        self._selected_strategy_keys: list[str] = [current_strategy_key]
        self._profile_menu_vars: dict[str, tk.BooleanVar] = {}
        self._selected_local_accounts: list[Mt5TerminalCandidate] = []
        self._selected_account_snapshots: list[tuple[Mt5TerminalCandidate, MonitorSnapshot]] = []
        self._selected_accounts_host: tk.Frame | None = None
        self._selected_account_card_keys: tuple[str, ...] = ()
        self._selected_account_card_values: list[tuple[tk.Label, tk.Label, tk.Label, tk.Label, tk.Label, tk.Label]] = []

        self._account_value = tk.StringVar(value="—")
        self._equity_value = tk.StringVar(value="—")
        self._position_count_value = tk.StringVar(value="0")
        self._mt5_status_value = tk.StringVar(value="Đang kết nối MT5 hiện tại…")
        self._bot_status_value = tk.StringVar(value="UNKNOWN")
        self._bot_status_detail_value = tk.StringVar(value="Đang dò runner chiến lược…")
        self._last_signal_check_value = tk.StringVar(value="—")
        self._last_signal_value = tk.StringVar(value="NONE")
        self._last_signal_detail_value = tk.StringVar(value="Chưa thấy signal mới.")
        self._updated_value = tk.StringVar(value="CHƯA CẬP NHẬT")
        self._updated_button_value = tk.StringVar(value="CẬP NHẬT\nCHƯA CẬP NHẬT")
        self._currency_alias_enabled = False
        self._raw_currency = "USC"
        self._profile_line_value = tk.StringVar(value="")
        self._strategy_choice = tk.StringVar(value=current_strategy_key)
        self._profile_choice = tk.StringVar(value=STRATEGY_SELECTIONS[current_strategy_key].label)
        self._timeframe_value = tk.StringVar(value="M5")
        self._preset_value = tk.StringVar(value="gold-loose")
        self._side_value = tk.StringVar(value="both")
        self._volume_value = tk.StringVar(value="0.01")
        self._risk_value = tk.StringVar(value="5.00")
        self._reward_value = tk.StringVar(value="5.00")
        self._run_button_label = tk.StringVar(value="RUN")
        self._history_filter_value = tk.StringVar(value="Hôm nay")
        self._history_symbol_value = tk.StringVar(value="Tất cả")
        gmt7_today = self._clock_gmt7().date()
        self._history_start_date_value = tk.StringVar(value=(gmt7_today - timedelta(days=30)).isoformat())
        self._history_end_date_value = tk.StringVar(value=gmt7_today.isoformat())
        self._history_start_time_value = tk.StringVar(value="08:00")
        self._history_end_time_value = tk.StringVar(value="23:59")
        self._history_range_value = tk.StringVar(value="—")
        self._history_deals_value = tk.StringVar(value="0")
        self._history_winrate_value = tk.StringVar(value="0.0%")
        self._history_daily_dd_value = tk.StringVar(value="0.00 USC")
        self._history_net_value = tk.StringVar(value="0.00 USC")
        self._history_volume_value = tk.StringVar(value="0 RATE")
        self._position_day_value = tk.StringVar(value=gmt7_today.strftime("%d/%m"))
        self._history_window: tk.Toplevel | None = None
        self._history_table: ttk.Treeview | None = None
        self._history_status_value = tk.StringVar(value="Sẵn sàng quét lịch sử read-only.")
        self._history_loading = False
        self._history_refresh_pending = False
        self._history_request_id = 0
        self._history_results: queue.Queue = queue.Queue()
        self._history_custom_start_wrap: tk.Frame | None = None
        self._history_custom_end_wrap: tk.Frame | None = None
        self._history_flr_enabled = False
        self._history_flr_button: tk.Button | None = None
        self._history_flr_previous_state: tuple[str, str, str] | None = None
        self._bot_status_badge_color = UiPalette.WARNING
        self._last_signal_badge_color = UiPalette.INFO
        self._bot_status_badge_widget: tk.Canvas | None = None
        self._last_signal_badge_widget: tk.Canvas | None = None
        self._run_button: tk.Button | None = None

        self.title(APP_TITLE)
        self.geometry("1380x860")
        self.minsize(1180, 720)
        self.configure(background=UiPalette.APP)
        self._apply_strategy_profile(current_strategy_key)
        self._configure_style()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._append_log("Observer mode active. No trade orders are sent from this app.", badge="INFO")
        self._schedule_refresh(0)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Monitor.Treeview",
            background=UiPalette.TABLE,
            fieldbackground=UiPalette.TABLE,
            foreground=UiPalette.TEXT,
            borderwidth=0,
            rowheight=32,
            font=("Segoe UI", 9),
        )
        style.map(
            "Monitor.Treeview",
            background=[("selected", UiPalette.ACCENT_DARK)],
            foreground=[("selected", UiPalette.TEXT)],
        )
        style.configure(
            "Monitor.Treeview.Heading",
            background=UiPalette.SIDEBAR,
            foreground=UiPalette.NAV_TEXT,
            borderwidth=0,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padding=(10, 8),
        )
        style.map("Monitor.Treeview.Heading", background=[("active", UiPalette.ACCENT_DARK)], foreground=[("active", UiPalette.NAV_TEXT)])
        style.configure("LocalAccount.Treeview", rowheight=38, font=("Segoe UI Symbol", 11))
        style.configure("LocalAccount.Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=(12, 9))
        style.configure(
            "Monitor.TCombobox",
            fieldbackground=UiPalette.TABLE,
            background=UiPalette.CARD_ALT,
            foreground=UiPalette.TEXT,
            arrowcolor=UiPalette.ACCENT,
            bordercolor=UiPalette.BORDER,
            lightcolor=UiPalette.BORDER,
            darkcolor=UiPalette.BORDER,
            padding=(10, 6),
            font=("Segoe UI", 10),
        )
        style.map(
            "Monitor.TCombobox",
            fieldbackground=[("readonly", UiPalette.TABLE), ("focus", UiPalette.TABLE)],
            selectbackground=[("readonly", UiPalette.ACCENT_DARK)],
            selectforeground=[("readonly", UiPalette.TEXT)],
            bordercolor=[("focus", UiPalette.ACCENT), ("active", UiPalette.ACCENT)],
        )

    def _label(self, parent: tk.Misc, text: str = "", *, font: tuple = ("Segoe UI", 10), fg: str | None = None, bg: str | None = None, **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, font=font, fg=fg or UiPalette.TEXT, bg=bg or UiPalette.CARD, bd=0, highlightthickness=0, **kwargs)

    def _card(self, parent: tk.Misc, *, padding: int = 18, bg: str | None = None) -> tk.Frame:
        card = tk.Frame(parent, bg=bg or UiPalette.CARD, highlightbackground=UiPalette.BORDER, highlightthickness=1, bd=0)
        card.configure(padx=padding, pady=padding)
        return card

    @staticmethod
    def _center_window(window: tk.Toplevel) -> None:
        """Place a fully laid-out dialog at the center of the current screen."""
        window.update_idletasks()
        width = window.winfo_width()
        height = window.winfo_height()
        x = max(0, (window.winfo_screenwidth() - width) // 2)
        y = max(0, (window.winfo_screenheight() - height) // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def _current_profile(self) -> dict[str, str | float]:
        return self._strategy_profiles[self._strategy_choice.get()]

    def _apply_strategy_profile(self, strategy_key: str) -> None:
        profile = self._strategy_profiles[strategy_key]
        self._refresh_profile_selector_label()
        self._timeframe_value.set(str(profile["timeframe"]))
        self._preset_value.set(str(profile["preset"]))
        self._side_value.set(str(profile["side"]))
        self._volume_value.set(f"{float(profile['volume']):.2f}")
        self._risk_value.set(f"{float(profile['risk_usd']):.2f}")
        self._reward_value.set(f"{float(profile['reward_usd']):.2f}")
        self._profile_line_value.set(
            f"{self._selected_profile_labels().upper()}  /  PRIMARY {STRATEGY_SELECTIONS[strategy_key].label.upper()}  /  {self._display_symbol(str(profile['symbol']))} {profile['timeframe']}  /  PRESET {str(profile['preset']).upper()}  /  {str(profile['side']).upper()}"
        )

    def _selected_profile_labels(self) -> str:
        keys = self._selected_strategy_keys or [self._strategy_choice.get()]
        return " + ".join(STRATEGY_SELECTIONS[key].label for key in keys)

    def _refresh_profile_selector_label(self) -> None:
        self._profile_choice.set(self._selected_profile_labels())

    def _set_profile_menu_state(self, strategy_key: str, selected: bool) -> None:
        if selected:
            if strategy_key not in self._selected_strategy_keys:
                self._selected_strategy_keys.append(strategy_key)
        else:
            if len(self._selected_strategy_keys) <= 1:
                self._profile_menu_vars[strategy_key].set(True)
                return
            self._selected_strategy_keys = [key for key in self._selected_strategy_keys if key != strategy_key]
        if self._strategy_choice.get() not in self._selected_strategy_keys:
            self._strategy_choice.set(self._selected_strategy_keys[0])
        self._refresh_profile_selector_label()
        self._apply_strategy_profile(self._strategy_choice.get())
        self._set_last_signal_state("NONE", f"Đang theo dõi: {self._selected_profile_labels()}.", UiPalette.INFO)
        if self._latest_snapshot is not None:
            self._apply_bot_status(self._latest_snapshot)
        else:
            self._update_run_button(None)
        self._append_log(f"Đã cập nhật profile theo dõi: {self._selected_profile_labels()}.", badge="INFO")

    def _build_ui(self) -> None:
        shell = tk.Frame(self, bg=UiPalette.APP)
        shell.pack(fill="both", expand=True)
        self.shell = shell

        sidebar = tk.Frame(shell, bg=UiPalette.SIDEBAR, width=230, padx=20, pady=24)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._label(sidebar, text="GOLD", font=("Segoe UI", 20, "bold"), fg=UiPalette.ACCENT, bg=UiPalette.SIDEBAR).pack(anchor="w")
        self._label(sidebar, text="VIEWER", font=("Segoe UI", 20, "bold"), fg=UiPalette.NAV_TEXT, bg=UiPalette.SIDEBAR).pack(anchor="w", pady=(0, 6))
        self._label(sidebar, text="DOANHHD", font=("Segoe UI", 9, "bold"), fg=UiPalette.NAV_MUTED, bg=UiPalette.SIDEBAR).pack(anchor="w")

        nav_line = tk.Frame(sidebar, bg=UiPalette.ACCENT, height=2)
        nav_line.pack(fill="x", pady=(26, 18))
        monitor_button = tk.Button(
            sidebar,
            text="◉  THEO DÕI LỆNH",
            command=self._show_monitor_page,
            font=("Segoe UI", 10, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.CARD_ALT,
            activeforeground=UiPalette.TEXT,
            activebackground=UiPalette.BORDER,
            relief="flat",
            bd=0,
            padx=12,
            pady=11,
            anchor="w",
            cursor="hand2",
        )
        monitor_button.pack(fill="x")
        tk.Button(
            sidebar, text="▣  MT5 LOCAL", command=self._open_local_accounts_window,
            font=("Segoe UI", 10, "bold"), fg=UiPalette.NAV_TEXT, bg=UiPalette.SIDEBAR,
            activeforeground=UiPalette.NAV_TEXT, activebackground=UiPalette.CARD_ALT,
            relief="flat", bd=0, padx=12, pady=11, anchor="w", cursor="hand2",
        ).pack(fill="x", pady=(8, 0))
        tk.Button(
            sidebar, text="▤  VPS / RDP", command=self._open_vps_window,
            font=("Segoe UI", 10, "bold"), fg=UiPalette.NAV_TEXT, bg=UiPalette.SIDEBAR,
            activeforeground=UiPalette.NAV_TEXT, activebackground=UiPalette.CARD_ALT,
            relief="flat", bd=0, padx=12, pady=11, anchor="w", cursor="hand2",
        ).pack(fill="x", pady=(8, 0))
        history_button = tk.Button(
            sidebar,
            text="◷  LỊCH SỬ LỆNH",
            command=self._open_history_window,
            font=("Segoe UI", 10, "bold"),
            fg=UiPalette.NAV_TEXT,
            bg=UiPalette.SIDEBAR,
            activeforeground=UiPalette.NAV_TEXT,
            activebackground=UiPalette.CARD_ALT,
            relief="flat",
            bd=0,
            padx=12,
            pady=11,
            anchor="w",
            cursor="hand2",
        )
        history_button.pack(fill="x", pady=(8, 0))

        sidebar_bottom = tk.Frame(sidebar, bg=UiPalette.SIDEBAR)
        sidebar_bottom.pack(side="bottom", fill="x")
        theme_button = tk.Button(
            sidebar_bottom,
            text="☀  LIGHT MODE" if self._theme_mode == "dark" else "◐  DARK MODE",
            command=self._toggle_theme,
            font=("Segoe UI", 9, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.CARD_ALT,
            activeforeground=UiPalette.TEXT,
            activebackground=UiPalette.BORDER,
            relief="flat",
            bd=0,
            padx=12,
            pady=10,
            cursor="hand2",
        )
        theme_button.pack(fill="x", pady=(10, 0))

        content = tk.Frame(shell, bg=UiPalette.APP, padx=24, pady=22)
        content.pack(side="left", fill="both", expand=True)

        header = tk.Frame(content, bg=UiPalette.APP)
        header.pack(fill="x", pady=(0, 18))
        title_group = tk.Frame(header, bg=UiPalette.APP)
        title_group.pack(side="left")
        self._label(title_group, text="TRẠM QUAN SÁT DOANHHD", font=("Segoe UI", 19, "bold"), bg=UiPalette.APP).pack(anchor="w")
        self._label(title_group, text="GOLD VIEWER • Theo dõi lệnh, preset và signal nội bộ", font=("Segoe UI", 10), fg=UiPalette.MUTED, bg=UiPalette.APP).pack(anchor="w", pady=(4, 0))
        updated = tk.Button(
            header,
            text="CẬP NHẬT\nCHƯA CẬP NHẬT",
            textvariable=self._updated_button_value,
            command=self._toggle_currency_alias,
            font=("Consolas", 9, "bold"),
            fg=UiPalette.ACCENT,
            bg=UiPalette.CARD_ALT,
            activeforeground=UiPalette.ACCENT,
            activebackground=UiPalette.BORDER,
            relief="flat",
            bd=0,
            padx=12,
            pady=9,
            cursor="hand2",
            justify="right",
        )
        updated.pack(side="right", anchor="s")

        self._selected_accounts_host = tk.Frame(content, bg=UiPalette.APP)
        self._selected_accounts_host.pack(fill="x", pady=(0, 14))

        body = tk.Frame(content, bg=UiPalette.APP)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=5, uniform="main")
        body.grid_columnconfigure(1, weight=6, uniform="main")
        body.grid_rowconfigure(0, weight=1)

        positions_card = self._card(body, padding=0)
        positions_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        section = tk.Frame(positions_card, bg=UiPalette.CARD, padx=18, pady=15)
        section.pack(fill="x")
        self._label(section, text="LỆNH ĐANG MỞ", font=("Segoe UI", 11, "bold")).pack(side="left")
        self._label(section, textvariable=self._position_day_value, font=("Segoe UI", 9, "bold"), fg=UiPalette.MUTED).pack(side="left", padx=(10, 0), pady=(1, 0))
        self._label(section, text="Tất cả vị thế XAU / BTC • read-only", font=("Segoe UI", 9), fg=UiPalette.MUTED).pack(side="right")
        columns = ("time", "account", "symbol", "side", "volume", "entry", "sl", "tp", "profit")
        self.positions = ttk.Treeview(positions_card, columns=columns, show="headings", style="Monitor.Treeview", height=9)
        specs = (("time", 62, "TIME"), ("account", 94, "ACCOUNT"), ("symbol", 56, "SYMBOL"), ("side", 50, "TYPE"), ("volume", 42, "LOT"), ("entry", 72, "ENTRY"), ("sl", 66, "SL"), ("tp", 66, "TP"), ("profit", 70, "PnL"))
        for key, width, title in specs:
            self.positions.heading(key, text=title)
            self.positions.column(key, width=width, anchor="center", stretch=True)
        self.positions.tag_configure("profit", foreground=UiPalette.SUCCESS)
        self.positions.tag_configure("loss", foreground=UiPalette.DANGER)
        self.positions.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        log_card = self._card(body, padding=0)
        log_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        log_header = tk.Frame(log_card, bg=UiPalette.CARD, padx=18, pady=11)
        log_header.pack(fill="x")
        self._label(log_header, text="BẢNG LOG TÍN HIỆU", font=("Segoe UI", 11, "bold")).pack(side="left")
        clear_button = tk.Button(log_header, text="XÓA", command=self._clear_logs, font=("Segoe UI", 8, "bold"), fg=UiPalette.TEXT, bg=UiPalette.CARD_ALT, activeforeground=UiPalette.TEXT, activebackground=UiPalette.BORDER, relief="flat", bd=0, padx=10, pady=4, cursor="hand2")
        clear_button.pack(side="right")

        log_table_header = tk.Frame(log_card, bg=UiPalette.CARD_ALT, padx=14, pady=8)
        log_table_header.pack(fill="x")
        self._label(log_table_header, text="TIME", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT, width=9, anchor="w").pack(side="left")
        self._label(log_table_header, text="TAG", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT, width=9, anchor="w").pack(side="left", padx=(8, 4))
        self._label(log_table_header, text="CONTENT", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT, anchor="w").pack(side="left", fill="x", expand=True)

        log_holder = tk.Frame(log_card, bg=UiPalette.TABLE)
        log_holder.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self._log_canvas = tk.Canvas(log_holder, bg=UiPalette.TABLE, highlightthickness=0, bd=0)
        self._log_scrollbar = ttk.Scrollbar(log_holder, orient="vertical", command=self._log_canvas.yview)
        self._log_canvas.configure(yscrollcommand=self._log_scrollbar.set)
        self._log_scrollbar.pack(side="right", fill="y")
        self._log_canvas.pack(side="left", fill="both", expand=True)
        self._log_rows = tk.Frame(self._log_canvas, bg=UiPalette.TABLE)
        self._log_canvas_window = self._log_canvas.create_window((0, 0), window=self._log_rows, anchor="nw")
        self._log_rows.bind("<Configure>", self._on_log_frame_configure)
        self._log_canvas.bind("<Configure>", self._on_log_canvas_configure)

        footer = tk.Frame(content, bg=UiPalette.APP)
        footer.pack(fill="x", pady=(14, 0))
        refresh = tk.Button(footer, text="↻  LÀM MỚI NGAY", command=lambda: self._schedule_refresh(0), font=("Segoe UI", 10, "bold"), fg="#101722", bg=UiPalette.ACCENT, activeforeground="#101722", activebackground="#E8C270", relief="flat", bd=0, padx=16, pady=9, cursor="hand2")
        refresh.pack(side="left")
        self._label(footer, text="Không gửi lệnh MT5 • chỉ giám sát và hiển thị signal nội bộ", font=("Segoe UI", 9), fg=UiPalette.MUTED, bg=UiPalette.APP).pack(side="right")

    def _render_selected_account_cards(self) -> None:
        """Render every observed MT5 account using one fixed read-only card layout."""
        host = self._selected_accounts_host
        if host is None:
            return
        rows: list[tuple[Mt5TerminalCandidate | None, MonitorSnapshot]] = list(self._selected_account_snapshots)
        if not rows and self._latest_snapshot is not None:
            rows = [(None, self._latest_snapshot)]
        if not rows:
            for child in host.winfo_children():
                child.destroy()
            self._selected_account_card_keys = ()
            self._selected_account_card_values = []
            return
        keys = tuple(str(candidate.path).casefold() if candidate is not None else f"current:{snapshot.login}" for candidate, snapshot in rows)
        if keys != self._selected_account_card_keys:
            for child in host.winfo_children():
                child.destroy()
            self._selected_account_card_keys = keys
            self._selected_account_card_values = []
            for _candidate, _snapshot in rows:
                row = tk.Frame(host, bg=UiPalette.APP)
                row.pack(fill="x", pady=(0, 8))
                for column in range(3):
                    row.grid_columnconfigure(column, weight=1, uniform="selected_account")
                fields: list[tk.Label] = []
                for column, caption in enumerate(("TÀI KHOẢN MT5", "EQUITY", "LỆNH XAU/BTC")):
                    card = self._card(row, padding=10)
                    card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 5))
                    self._label(card, text=caption, font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
                    if column == 0:
                        account_line = tk.Frame(card, bg=UiPalette.CARD)
                        account_line.pack(anchor="w", pady=(5, 0))
                        value = self._label(account_line, font=("Segoe UI", 14, "bold"), fg=UiPalette.TEXT)
                        value.pack(side="left")
                        detail = self._label(account_line, text="", font=("Segoe UI", 8), fg=UiPalette.MUTED)
                        detail.pack(side="left", padx=(9, 0), pady=(3, 0))
                    else:
                        value = self._label(card, font=("Segoe UI", 12 if column == 2 else 14, "bold"), fg=(UiPalette.SUCCESS, UiPalette.ACCENT)[column - 1])
                        value.pack(anchor="w", pady=(5, 0))
                        detail = self._label(card, text=" ", font=("Segoe UI", 8), fg=UiPalette.MUTED)
                    fields.extend((value, detail))
                self._selected_account_card_values.append(tuple(fields))
        for index, (candidate, snapshot) in enumerate(rows):
            account_value, account_detail, equity_value, equity_detail, positions_value, positions_detail = self._selected_account_card_values[index]
            account_value.configure(text=f"#{snapshot.login}")
            account_detail.configure(text=f"ONLINE • {snapshot.server}")
            equity_value.configure(text=f"{snapshot.equity:,.2f} {self._display_currency(snapshot.currency)}")
            equity_detail.configure(text=" ")
            account_name = (candidate.name if candidate is not None else "Read-only") or "Read-only"
            positions_value.configure(text=f"{len(snapshot.positions)} ({account_name})")
            positions_detail.configure(text=" ")

    def _labeled_entry(self, parent: tk.Misc, row: int, column: int, caption: str, variable: tk.StringVar, *, state: str = "normal") -> None:
        wrap = tk.Frame(parent, bg=UiPalette.CARD)
        wrap.grid(row=row, column=column, sticky="ew", padx=6, pady=6)
        self._label(wrap, text=caption, font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        entry = tk.Entry(
            wrap,
            textvariable=variable,
            state=state,
            font=("Segoe UI", 10, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.TABLE,
            disabledforeground=UiPalette.TEXT,
            relief="flat",
            bd=0,
            insertbackground=UiPalette.TEXT,
        )
        entry.pack(fill="x", pady=(6, 0), ipady=8)

    def _labeled_combobox(
        self,
        parent: tk.Misc,
        row: int,
        column: int,
        caption: str,
        variable: tk.StringVar,
        values: list[str],
        handler: Callable[[object], None],
        *,
        allow_blank: bool = False,
    ) -> ttk.Combobox:
        wrap = tk.Frame(parent, bg=UiPalette.CARD)
        wrap.grid(row=row, column=column, sticky="ew", padx=6, pady=6)
        self._label(wrap, text=caption, font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        combo_values = [""] + values if allow_blank else values
        combo = ttk.Combobox(wrap, textvariable=variable, values=combo_values, state="readonly", style="Monitor.TCombobox", font=("Segoe UI", 10))
        combo.pack(fill="x", pady=(6, 0), ipady=5)
        combo.bind("<<ComboboxSelected>>", handler)
        return combo

    def _labeled_multi_profile_selector(
        self,
        parent: tk.Misc,
        row: int,
        column: int,
        caption: str,
        variable: tk.StringVar,
    ) -> tk.Menubutton:
        wrap = tk.Frame(parent, bg=UiPalette.CARD)
        wrap.grid(row=row, column=column, sticky="ew", padx=6, pady=6)
        self._label(wrap, text=caption, font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        button = tk.Menubutton(
            wrap,
            textvariable=variable,
            indicatoron=True,
            anchor="w",
            font=("Segoe UI", 10),
            fg=UiPalette.TEXT,
            bg=UiPalette.TABLE,
            activeforeground=UiPalette.TEXT,
            activebackground=UiPalette.TABLE_ALT,
            relief="flat",
            bd=0,
            padx=8,
            pady=6,
        )
        menu = tk.Menu(button, tearoff=False, bg=UiPalette.TABLE, fg=UiPalette.TEXT, activebackground=UiPalette.ACCENT, activeforeground="#101722")
        self._profile_menu_vars.clear()
        for strategy_key, selection in STRATEGY_SELECTIONS.items():
            variable_for_key = tk.BooleanVar(value=strategy_key in self._selected_strategy_keys)
            self._profile_menu_vars[strategy_key] = variable_for_key
            menu.add_checkbutton(
                label=selection.label,
                variable=variable_for_key,
                command=lambda key=strategy_key, var=variable_for_key: self._set_profile_menu_state(key, var.get()),
            )
        button.configure(menu=menu)
        button.pack(fill="x", pady=(6, 0), ipady=3)
        self._refresh_profile_selector_label()
        return button

    def _show_monitor_page(self) -> None:
        if self._history_window is not None and self._history_window.winfo_exists():
            self._close_history_window()
        self.lift()
        self.focus_force()

    def _history_date_range(self) -> tuple[datetime, datetime]:
        today = self._clock_gmt7().date()
        mode = self._history_filter_value.get().strip().lower()
        if mode == "hôm nay":
            start_date, end_date = today, today
        elif mode == "7 ngày qua":
            start_date, end_date = today - timedelta(days=6), today
        elif mode == "90 ngày qua":
            start_date, end_date = today - timedelta(days=89), today
        elif mode == "1 năm qua":
            start_date, end_date = today - timedelta(days=364), today
        elif mode == "tùy chỉnh":
            try:
                start_date = date.fromisoformat(self._history_start_date_value.get().strip())
                end_date = date.fromisoformat(self._history_end_date_value.get().strip())
            except ValueError:
                end_date = today
                start_date = today - timedelta(days=30)
                self._history_start_date_value.set(start_date.isoformat())
                self._history_end_date_value.set(end_date.isoformat())
            if end_date < start_date:
                start_date, end_date = end_date, start_date
                self._history_start_date_value.set(start_date.isoformat())
                self._history_end_date_value.set(end_date.isoformat())
        else:
            start_date, end_date = today - timedelta(days=29), today
        return datetime.combine(start_date, time.min), datetime.combine(end_date + timedelta(days=1), time.min)

    def _open_history_window(self) -> None:
        if self._history_window is not None and self._history_window.winfo_exists():
            self._history_window.lift()
            self._history_window.focus_force()
            return
        window = tk.Toplevel(self)
        self._history_window = window
        window.title("Lịch sử lệnh")
        window.geometry("1180x720")
        window.minsize(980, 620)
        window.configure(background=UiPalette.APP)
        window.protocol("WM_DELETE_WINDOW", self._close_history_window)

        shell = tk.Frame(window, bg=UiPalette.APP, padx=22, pady=18)
        shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=UiPalette.APP)
        header.pack(fill="x", pady=(0, 14))
        self._label(header, text="LỊCH SỬ LỆNH", font=("Segoe UI", 18, "bold"), bg=UiPalette.APP).pack(side="left")
        self._label(header, textvariable=self._history_status_value, font=("Segoe UI", 9), fg=UiPalette.MUTED, bg=UiPalette.APP).pack(side="left", padx=(14, 0), pady=(6, 0))
        self._history_flr_button = tk.Button(
            header,
            text="RESET LỌC",
            command=self._toggle_flr_filter,
            font=("Segoe UI", 9, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.CARD_ALT,
            activeforeground="#101722",
            activebackground=UiPalette.ACCENT,
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
        )
        self._history_flr_button.pack(side="right")

        controls = self._card(shell, padding=14)
        controls.pack(fill="x", pady=(0, 12))
        for column in range(6):
            controls.grid_columnconfigure(column, weight=1)
        controls.grid_columnconfigure(6, weight=0)
        self._labeled_combobox(
            controls,
            0,
            0,
            "KHOẢNG THỜI GIAN",
            self._history_filter_value,
            ["Hôm nay", "7 ngày qua", "30 ngày qua", "90 ngày qua", "1 năm qua", "Tùy chỉnh"],
            self._on_history_filter_change,
        )
        self._labeled_combobox(
            controls,
            0,
            1,
            "MÃ GIAO DỊCH",
            self._history_symbol_value,
            ["Tất cả", "XAUUSD", "BTCUSD"],
            self._on_history_symbol_change,
        )
        self._history_custom_start_wrap = tk.Frame(controls, bg=UiPalette.CARD)
        self._history_custom_start_wrap.grid(row=0, column=2, sticky="ew", padx=6, pady=6)
        self._label(self._history_custom_start_wrap, text="START DATE", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        tk.Entry(
            self._history_custom_start_wrap,
            textvariable=self._history_start_date_value,
            font=("Segoe UI", 10, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.TABLE,
            relief="flat",
            bd=0,
            insertbackground=UiPalette.TEXT,
        ).pack(fill="x", pady=(6, 0), ipady=8)
        self._history_custom_end_wrap = tk.Frame(controls, bg=UiPalette.CARD)
        self._history_custom_end_wrap.grid(row=0, column=3, sticky="ew", padx=6, pady=6)
        self._label(self._history_custom_end_wrap, text="END DATE", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        tk.Entry(
            self._history_custom_end_wrap,
            textvariable=self._history_end_date_value,
            font=("Segoe UI", 10, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.TABLE,
            relief="flat",
            bd=0,
            insertbackground=UiPalette.TEXT,
        ).pack(fill="x", pady=(6, 0), ipady=8)
        self._label(controls, text="TỪ GIỜ", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).grid(row=0, column=4, sticky="w", padx=6, pady=(6, 0))
        tk.Entry(
            controls,
            textvariable=self._history_start_time_value,
            font=("Segoe UI", 10, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.TABLE,
            relief="flat",
            bd=0,
            insertbackground=UiPalette.TEXT,
        ).grid(row=0, column=4, sticky="ew", padx=6, pady=(29, 6), ipady=8)
        self._label(controls, text="ĐẾN GIỜ", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).grid(row=0, column=5, sticky="w", padx=6, pady=(6, 0))
        tk.Entry(
            controls,
            textvariable=self._history_end_time_value,
            font=("Segoe UI", 10, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.TABLE,
            relief="flat",
            bd=0,
            insertbackground=UiPalette.TEXT,
        ).grid(row=0, column=5, sticky="ew", padx=6, pady=(29, 6), ipady=8)
        action_wrap = tk.Frame(controls, bg=UiPalette.CARD)
        action_wrap.grid(row=0, column=6, sticky="sew", padx=6, pady=6)
        refresh = tk.Button(action_wrap, text="LỌC", command=self._refresh_history, font=("Segoe UI", 9, "bold"), fg="#101722", bg=UiPalette.ACCENT, activeforeground="#101722", activebackground="#E8C270", relief="flat", bd=0, padx=18, pady=9, cursor="hand2")
        refresh.pack(fill="x", pady=(22, 0))
        self._set_custom_history_controls_visible()

        stats = tk.Frame(shell, bg=UiPalette.APP)
        stats.pack(fill="x", pady=(0, 12))
        for column in range(5):
            stats.grid_columnconfigure(column, weight=1, uniform="history_metric")
        self._metric(stats, 0, "DEALS", self._history_deals_value, UiPalette.ACCENT)
        self._metric(stats, 1, "WINRATE", self._history_winrate_value, UiPalette.SUCCESS)
        self._metric(stats, 2, "MAX DD NGÀY", self._history_daily_dd_value, UiPalette.DANGER)
        self._metric(stats, 3, "NET P/L", self._history_net_value, UiPalette.SUCCESS)
        self._metric(stats, 4, "FOLLOW TREND RATE", self._history_volume_value, UiPalette.ACCENT)

        history_card = self._card(shell, padding=0)
        history_card.pack(fill="both", expand=True)
        history_header = tk.Frame(history_card, bg=UiPalette.CARD, padx=18, pady=12)
        history_header.pack(fill="x")
        self._label(history_header, text="DEAL HISTORY", font=("Segoe UI", 11, "bold")).pack(side="left")
        self._label(history_header, textvariable=self._history_range_value, font=("Segoe UI", 9), fg=UiPalette.MUTED).pack(side="right")

        columns = ("time", "account", "symbol", "side", "volume", "price", "net")
        self._history_table = ttk.Treeview(history_card, columns=columns, show="headings", style="Monitor.Treeview", height=12)
        specs = (("time", 154, "TIME"), ("account", 102, "ACCOUNT"), ("symbol", 108, "SYMBOL"), ("side", 64, "SIDE"), ("volume", 72, "LOT"), ("price", 92, "PRICE"), ("net", 92, "NET $"))
        for key, width, title in specs:
            self._history_table.heading(key, text=title)
            self._history_table.column(key, width=width, anchor="center", stretch=True)
        self._history_table.tag_configure("profit", foreground=UiPalette.SUCCESS)
        self._history_table.tag_configure("loss", foreground=UiPalette.DANGER)
        self._history_table.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        self._refresh_history()
        self._center_window(window)

    def _on_history_filter_change(self, _event=None) -> None:
        if self._history_flr_enabled:
            self._history_flr_enabled = False
            self._history_flr_previous_state = None
            self._update_flr_button()
        self._set_custom_history_controls_visible()
        if self._history_filter_value.get().strip().lower() != "tùy chỉnh":
            self._refresh_history()

    def _on_history_symbol_change(self, _event=None) -> None:
        self._refresh_history()

    def _update_flr_button(self) -> None:
        if self._history_flr_button is None:
            return
        self._history_flr_button.configure(
            bg=UiPalette.ACCENT if self._history_flr_enabled else UiPalette.CARD_ALT,
            fg="#101722" if self._history_flr_enabled else UiPalette.TEXT,
            activebackground=UiPalette.ACCENT_DARK if self._history_flr_enabled else UiPalette.ACCENT,
        )

    def _toggle_flr_filter(self) -> None:
        """Toggle today's GMT+7 profitable-only history filter."""
        if not self._history_flr_enabled:
            self._history_flr_previous_state = (
                self._history_filter_value.get(),
                self._history_start_time_value.get(),
                self._history_end_time_value.get(),
            )
            self._history_flr_enabled = True
            self._history_filter_value.set("Hôm nay")
            self._history_start_time_value.set("08:00")
            self._history_end_time_value.set("23:59")
        else:
            previous = self._history_flr_previous_state
            self._history_flr_enabled = False
            self._history_flr_previous_state = None
            if previous is not None:
                self._history_filter_value.set(previous[0])
                self._history_start_time_value.set(previous[1])
                self._history_end_time_value.set(previous[2])
        self._update_flr_button()
        self._set_custom_history_controls_visible()
        self._refresh_history()

    def _filter_history_deals_by_symbol(self, deals: tuple, symbol_filter: str | None = None) -> tuple:
        selected_symbol = (symbol_filter if symbol_filter is not None else self._history_symbol_value.get()).strip()
        if not selected_symbol or selected_symbol.lower() == "tất cả":
            return tuple(deals)
        selected_base = selected_symbol.upper()
        return tuple(
            deal for deal in deals
            if str(getattr(deal, "symbol", "")).upper().startswith(selected_base)
        )

    def _history_time_range(self) -> tuple[time, time]:
        defaults = (time(8, 0), time(23, 59))
        try:
            start = time.fromisoformat(self._history_start_time_value.get().strip())
            end = time.fromisoformat(self._history_end_time_value.get().strip())
        except ValueError:
            start, end = defaults
            self._history_start_time_value.set("08:00")
            self._history_end_time_value.set("23:59")
        return start.replace(second=0, microsecond=0), end.replace(second=59, microsecond=999999)

    def _filter_history_deals_by_time_gmt7(self, deals: tuple) -> tuple:
        start, end = self._history_time_range()
        return tuple(
            deal for deal in deals
            if start <= deal.time.replace(tzinfo=None).time() <= end
        )

    def _set_custom_history_controls_visible(self) -> None:
        show_custom = self._history_filter_value.get().strip().lower() == "tùy chỉnh"
        for wrap in (self._history_custom_start_wrap, self._history_custom_end_wrap):
            if wrap is None:
                continue
            if show_custom:
                wrap.grid()
            else:
                wrap.grid_remove()

    def _close_history_window(self) -> None:
        self._history_request_id += 1
        self._history_loading = False
        self._history_refresh_pending = False
        if self._history_window is not None:
            self._history_window.destroy()
        self._history_window = None
        self._history_table = None
        self._history_custom_start_wrap = None
        self._history_custom_end_wrap = None

    def _refresh_history(self) -> None:
        if self._history_table is None:
            return
        if self._history_loading:
            self._history_refresh_pending = True
            self._history_status_value.set("Đang quét lịch sử… thay đổi lọc sẽ áp dụng sau lượt hiện tại.")
            return
        start, end = self._history_date_range()
        start_time, end_time = self._history_time_range()
        self._history_range_value.set(f"{start:%Y-%m-%d} → {(end - timedelta(seconds=1)):%Y-%m-%d} • {start_time:%H:%M}–{end_time:%H:%M}")
        self._history_table.delete(*self._history_table.get_children())
        self._history_loading = True
        self._history_request_id += 1
        request_id = self._history_request_id
        symbol = self._selected_symbol()
        candidates = tuple(self._selected_local_accounts)
        current_account = str(self._latest_snapshot.login) if self._latest_snapshot is not None else "CURRENT"
        self._history_status_value.set(f"Đang quét lịch sử read-only • {len(candidates) or 1} MT5 account…")

        def worker() -> None:
            try:
                records, raw_deals = self._history_records(start, end, symbol=symbol, candidates=candidates, current_account=current_account)
                self._history_results.put((request_id, records, raw_deals, None))
            except Exception as exc:
                self._history_results.put((request_id, (), 0, exc))

        threading.Thread(target=worker, name="mt5-history-read", daemon=True).start()
        self.after(40, self._poll_history_result)

    def _poll_history_result(self) -> None:
        if not self._history_loading:
            return
        try:
            request_id, records, raw_deals, error = self._history_results.get_nowait()
        except queue.Empty:
            self.after(40, self._poll_history_result)
            return
        if request_id != self._history_request_id:
            self.after(0, self._poll_history_result)
            return
        self._history_loading = False
        if self._history_table is None or self._history_window is None or not self._history_window.winfo_exists():
            return
        if error is not None:
            self._history_deals_value.set("0")
            self._history_winrate_value.set("0.0%")
            self._history_daily_dd_value.set(f"0.00 {self._display_currency(self._raw_currency)}")
            self._history_net_value.set(f"0.00 {self._display_currency(self._raw_currency)}")
            self._history_volume_value.set("0 LOT")
            self._history_table.insert("", "end", values=("LỖI", "", "", "", "", "", str(error)), tags=("loss",))
            self._history_status_value.set("Không quét được lịch sử MT5.")
        else:
            self._render_history_records(records, raw_deals)
            self._history_status_value.set(f"Đã quét {len(records)} deal đóng • read-only history_deals_get.")
        if self._history_refresh_pending:
            self._history_refresh_pending = False
            self._refresh_history()

    def _render_history_records(self, records: list[tuple[str, object]], raw_deals: int) -> None:
        """Apply UI filters and render a completed background history scan."""
        if self._history_table is None:
            return
        records = [(account, deal) for account, deal in records if deal in self._filter_history_deals_by_symbol(tuple(deal for _, deal in records))]
        records = [(account, deal) for account, deal in records if deal in self._filter_history_deals_by_time_gmt7(tuple(deal for _, deal in records))]
        if self._history_flr_enabled:
            records = [(account, deal) for account, deal in records if float(getattr(deal, "net_profit", 0.0) or 0.0) > 0.0]
        deals = tuple(deal for _, deal in records)
        stats = RsiquiV3PositionMonitor.history_stats(deals, raw_deals=raw_deals)
        self._history_deals_value.set(str(stats.deals))
        self._history_winrate_value.set(f"{stats.winrate:.1f}%")
        currency = self._display_currency(self._raw_currency)
        self._history_daily_dd_value.set(f"{stats.max_daily_drawdown:,.2f} {currency}")
        self._history_net_value.set(f"{stats.net_profit:+,.2f} {currency}")
        self._history_volume_value.set(f"{sum(float(deal.volume) for deal in deals):g}")
        if not deals:
            selected_symbol = self._history_symbol_value.get().strip()
            if stats.raw_deals:
                symbol_suffix = "" if selected_symbol.lower() == "tất cả" else f" cho {selected_symbol}"
                empty_message = f"MT5 có {stats.raw_deals} bản ghi history nhưng không có deal BUY/SELL đóng lệnh{symbol_suffix} trong khoảng này."
            else:
                empty_message = "MT5 không trả về deal nào trong khoảng đã chọn."
            self._history_table.insert("", "end", values=("KHÔNG CÓ DỮ LIỆU", "", "", "", "", "", empty_message))
            return
        for account, deal in records:
            tag = "profit" if deal.net_profit >= 0 else "loss"
            self._history_table.insert(
                "",
                "end",
                tags=(tag,),
                values=(self._format_gmt7_datetime(deal.time), account, self._history_display_symbol(deal.symbol), deal.side, f"{deal.volume:.2f}", f"{deal.price:.2f}", f"{deal.net_profit:+.2f}"),
            )

    def _read_local_account(
        self,
        candidate: Mt5TerminalCandidate,
        *,
        history_range: tuple[datetime, datetime] | None = None,
        symbol: str | None = None,
        include_snapshot: bool = True,
    ) -> tuple[MonitorSnapshot | None, tuple, int]:
        """Read one local terminal sequentially; never logs in, sends, or modifies orders."""
        import MetaTrader5 as mt5

        mt5.shutdown()
        if not mt5.initialize(path=str(candidate.path)):
            raise RuntimeError(f"{candidate.login or candidate.path.name}: {mt5.last_error()}")
        probe = RsiquiV3PositionMonitor(symbol=symbol or self._selected_symbol(), mt5=mt5)
        probe._connected = True
        try:
            if history_range is None:
                snapshot = probe.refresh()
                return snapshot, (), 0
            deals, stats = probe.history(*history_range)
            snapshot = probe.refresh() if include_snapshot else None
            return snapshot, deals, stats.raw_deals
        finally:
            probe.stop()

    def _history_records(
        self,
        start: datetime,
        end: datetime,
        *,
        symbol: str | None = None,
        candidates: tuple[Mt5TerminalCandidate, ...] | None = None,
        current_account: str | None = None,
    ) -> tuple[list[tuple[str, object]], int]:
        selected_accounts = tuple(self._selected_local_accounts) if candidates is None else candidates
        if not selected_accounts:
            deals, stats = self.monitor.history(start, end)
            account = current_account or (str(self._latest_snapshot.login) if self._latest_snapshot is not None else "CURRENT")
            return [(account, deal) for deal in deals], stats.raw_deals
        records: list[tuple[str, object]] = []
        raw_deals = 0
        for candidate in selected_accounts:
            snapshot, deals, raw_count = self._read_local_account(candidate, history_range=(start, end), symbol=symbol, include_snapshot=False)
            account = candidate.login or (str(snapshot.login) if snapshot is not None else "CURRENT")
            records.extend((account, deal) for deal in deals)
            raw_deals += raw_count
        records.sort(key=lambda item: item[1].time, reverse=True)
        return records, raw_deals

    @staticmethod
    def _format_gmt7_timestamp(value: datetime | None) -> str:
        if value is None:
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(GMT_PLUS_7).strftime("%H:%M:%S")

    @staticmethod
    def _format_gmt7_datetime(value: datetime | None) -> str:
        if value is None:
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(GMT_PLUS_7).strftime("%Y-%m-%d %H:%M:%S")

    def _clock_gmt7(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            return value.replace(tzinfo=GMT_PLUS_7)
        return value.astimezone(GMT_PLUS_7)

    @staticmethod
    def _display_symbol(symbol: str) -> str:
        upper = str(symbol or "").upper()
        if upper.startswith("BTC"):
            return "BTC"
        if upper.startswith("XAU"):
            return "XAUUSD"
        if upper.endswith("USDT"):
            return upper[:-4]
        if upper.endswith("USD"):
            return upper[:-3]
        return upper

    @staticmethod
    def _display_currency(currency: str) -> str:
        upper = str(currency or "").upper()
        return "USD" if upper == "USC" else upper

    @staticmethod
    def _display_terminal_path(path: str | Path) -> str:
        """Keep the Local picker readable by omitting the common Program Files root."""
        value = str(path or "").replace("/", "\\")
        for prefix in ("C:\\Program Files\\", "C:\\Program Files (x86)\\"):
            if value.casefold().startswith(prefix.casefold()):
                return value[len(prefix):]
        return value

    def _toggle_currency_alias(self) -> None:
        """Toggle the USC -> USD display alias for the whole GUI."""
        self._currency_alias_enabled = not self._currency_alias_enabled
        if self._latest_snapshot is not None:
            self._render_snapshot(self._latest_snapshot, include_log_entries=False)
        if self._history_table is not None:
            self._refresh_history()

    @staticmethod
    def _history_display_symbol(symbol: str) -> str:
        """Keep broker suffixes internal while showing one GOLD family label."""
        upper = str(symbol or "").upper()
        if upper.startswith("XAUUSD"):
            return "XAUUSD"
        if upper.startswith("BTCUSD"):
            return "BTCUSD"
        return upper

    @staticmethod
    def _format_history_comment(comment: str) -> str:
        """Trim decimal price/money values in broker comments for display only."""
        def format_number(match: re.Match[str]) -> str:
            return f"{float(match.group(0)):.2f}"

        return re.sub(r"(?<![\w.])[+-]?\d+\.\d+(?!\w)", format_number, str(comment or ""))

    def _toggle_theme(self) -> None:
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        UiPalette.apply_mode(self._theme_mode)
        self.configure(background=UiPalette.APP)
        self._configure_style()
        if self.shell is not None:
            self.shell.destroy()
        self._build_ui()
        if self._latest_snapshot is not None:
            self._render_snapshot(self._latest_snapshot, include_log_entries=False)
        else:
            self._update_run_button(None)
        self._replay_logs()

    def _metric(self, parent: tk.Misc, column: int, caption: str, value: tk.StringVar, color: str) -> None:
        card = self._card(parent, padding=14)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 0 if column == 2 else 5))
        self._label(card, text=caption, font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        self._label(card, textvariable=value, font=("Segoe UI", 14, "bold"), fg=color).pack(anchor="w", pady=(5, 0))

    def _account_metric(self, parent: tk.Misc, column: int) -> None:
        card = self._card(parent, padding=14)
        card.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 5, 5))
        self._label(card, text="TÀI KHOẢN MT5", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        account_row = tk.Frame(card, bg=UiPalette.CARD)
        account_row.pack(fill="x", pady=(5, 0))
        self._label(account_row, textvariable=self._account_value, font=("Segoe UI", 14, "bold"), fg=UiPalette.TEXT, bg=UiPalette.CARD).pack(side="left")
        self._label(account_row, textvariable=self._mt5_status_value, font=("Consolas", 9), fg=UiPalette.MUTED, bg=UiPalette.CARD, anchor="e").pack(side="right", padx=(12, 0))

    def _safe_float(self, value: str, fallback: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return fallback

    def _selected_symbol(self) -> str:
        return str(self._current_profile()["symbol"])

    @staticmethod
    def _python_executable() -> str:
        return sys.executable or "python"

    @staticmethod
    def _runner_log_path(strategy_key: str) -> Path:
        log_dir = PROJECT_ROOT / "outputs" / "runtime_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir / f"{strategy_key}.log"

    @staticmethod
    def _build_runner_command(strategy_key: str) -> list[str]:
        runner_module = RUNNER_MODULE_BY_STRATEGY[strategy_key]
        config_path = STRATEGY_SELECTIONS[strategy_key].config_path
        return [RsiquiV3MonitorApp._python_executable(), "-m", runner_module, "--config", str(config_path)]

    def _selected_strategy_runtime_config(self):
        strategy_key = self._strategy_choice.get()
        profile = self._current_profile()
        prepare_frame, evaluate_signal, config_for_preset = _strategy_runtime(strategy_key)
        config = config_for_preset(
            str(profile["preset"]),
            volume_lots=self._safe_float(self._volume_value.get(), float(profile["volume"])),
            price_value_per_lot=float(profile.get("price_value_per_lot", 100.0)),
            risk_usd=self._safe_float(self._risk_value.get(), float(profile["risk_usd"])),
            reward_usd=self._safe_float(self._reward_value.get(), float(profile["reward_usd"])),
            max_spread=float(profile["max_spread"]),
            trade_side=str(profile["side"]),
        )
        return strategy_key, profile, config, prepare_frame, evaluate_signal

    def _has_open_position_for_selected_symbol(self) -> bool:
        snapshot = self._latest_snapshot
        if snapshot is None:
            return False
        selected_symbol = self._selected_symbol().casefold()
        return any(position.symbol.casefold() == selected_symbol for position in snapshot.positions)

    @staticmethod
    def _summarize_bot_status(selected_strategy_key: str, runner_detection_available: bool, running_runners: tuple[RunnerView, ...]) -> tuple[str, str, str]:
        selected_label = selected_strategy_key
        if not runner_detection_available:
            return "UNKNOWN", UiPalette.WARNING, "Không dò được process runner trên máy này."
        matching = [runner for runner in running_runners if runner.strategy_key == selected_strategy_key]
        if matching:
            runner = matching[0]
            return "RUNNING", UiPalette.SUCCESS, f"{selected_label} đang chạy • PID {runner.pid}"
        if running_runners:
            other_labels = ", ".join(sorted({runner.label for runner in running_runners}))
            return "STOPPED", UiPalette.WARNING, f"Chưa thấy {selected_label} chạy. Runner đang thấy: {other_labels}"
        return "STOPPED", UiPalette.WARNING, f"Chưa thấy runner {selected_label} đang chạy."

    @staticmethod
    def _summarize_selected_bot_status(selected_strategy_keys: tuple[str, ...], runner_detection_available: bool, running_runners: tuple[RunnerView, ...]) -> tuple[str, str, str]:
        if not selected_strategy_keys:
            return "STOPPED", UiPalette.WARNING, "Chưa chọn profile theo dõi."
        if not runner_detection_available:
            return "UNKNOWN", UiPalette.WARNING, "Không dò được process runner trên máy này."
        selected = set(selected_strategy_keys)
        matching = [runner for runner in running_runners if runner.strategy_key in selected]
        labels = ", ".join(STRATEGY_SELECTIONS[key].label for key in selected_strategy_keys)
        if matching:
            pids = ", ".join(str(runner.pid) for runner in matching)
            return "RUNNING", UiPalette.SUCCESS, f"Đang chạy: {labels} • PID {pids}"
        if running_runners:
            other_labels = ", ".join(sorted({runner.label for runner in running_runners}))
            return "STOPPED", UiPalette.WARNING, f"Chưa thấy {labels} chạy. Runner đang thấy: {other_labels}"
        return "STOPPED", UiPalette.WARNING, f"Chưa thấy runner: {labels}."

    def _set_last_signal_state(self, badge: str, detail: str, color: str) -> None:
        self._last_signal_value.set(badge)
        self._last_signal_detail_value.set(detail)
        self._last_signal_badge_color = color
        if self._last_signal_badge_widget is not None:
            self._last_signal_badge_widget.destroy()
            parent = self._last_signal_badge_widget.master
            self._last_signal_badge_widget = self._badge(parent, badge, color, UiPalette.CARD)
            self._last_signal_badge_widget.pack(side="left", before=parent.winfo_children()[0] if parent.winfo_children() else None)

    @staticmethod
    def _ellipsize(text: str, max_chars: int = 72) -> str:
        text = " ".join(str(text).split())
        return text if len(text) <= max_chars else f"{text[: max_chars - 3].rstrip()}..."

    def _set_bot_status_state(self, badge: str, detail: str, color: str) -> None:
        self._bot_status_value.set(badge)
        self._bot_status_detail_value.set(self._ellipsize(detail))
        self._bot_status_badge_color = color
        if self._bot_status_badge_widget is not None:
            self._bot_status_badge_widget.destroy()
            parent = self._bot_status_badge_widget.master
            self._bot_status_badge_widget = self._badge(parent, badge, color, UiPalette.CARD)
            self._bot_status_badge_widget.pack(side="left", before=parent.winfo_children()[0] if parent.winfo_children() else None)

    def _apply_bot_status(self, snapshot: MonitorSnapshot) -> None:
        status_text, color, detail = self._summarize_selected_bot_status(tuple(self._selected_strategy_keys), snapshot.runner_detection_available, snapshot.running_runners)
        self._set_bot_status_state(status_text, detail, color)
        self._update_run_button(snapshot)

    def _update_run_button(self, snapshot: MonitorSnapshot | None = None) -> None:
        if self._run_button is None:
            return
        snapshot = snapshot or self._latest_snapshot
        selected_strategy_keys = set(self._selected_strategy_keys)
        running = False
        if snapshot is not None and snapshot.runner_detection_available:
            running = any(runner.strategy_key in selected_strategy_keys for runner in snapshot.running_runners)
        self._run_button_label.set("RUNNING" if running else "RUN")
        self._run_button.configure(
            state="disabled" if running else "normal",
            bg=UiPalette.SUCCESS if not running else UiPalette.BORDER,
            activebackground=UiPalette.SUCCESS if not running else UiPalette.BORDER,
            disabledforeground=UiPalette.MUTED,
        )

    def _run_selected_strategy(self) -> None:
        strategy_key = self._strategy_choice.get()
        if self._latest_snapshot is not None and any(runner.strategy_key == strategy_key for runner in self._latest_snapshot.running_runners):
            self._append_log(f"{STRATEGY_SELECTIONS[strategy_key].label} đã đang chạy rồi.", badge="INFO")
            self._update_run_button(self._latest_snapshot)
            return
        command = self._build_runner_command(strategy_key)
        log_path = self._runner_log_path(strategy_key)
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(f"\n[{self._clock_gmt7():%Y-%m-%d %H:%M:%S}] Launch from monitor GUI\n")
                handle.flush()
                process = subprocess.Popen(
                    command,
                    cwd=str(PROJECT_ROOT),
                    stdout=handle,
                    stderr=handle,
                    creationflags=creationflags,
                )
        except Exception as exc:
            self._append_log(f"Không chạy được {STRATEGY_SELECTIONS[strategy_key].label}: {exc}", badge="ERROR")
            self._set_bot_status_state("ERROR", f"Launch runner thất bại: {exc}", UiPalette.DANGER)
            return
        self._set_bot_status_state("STARTING", f"Đã gọi chạy {STRATEGY_SELECTIONS[strategy_key].label} • PID {process.pid} • log {log_path.name}", UiPalette.INFO)
        self._append_log(f"Đã chạy {STRATEGY_SELECTIONS[strategy_key].label} • PID {process.pid} • log: {log_path}", badge="INFO")
        self._run_button_label.set("STARTING")
        if self._run_button is not None:
            self._run_button.configure(state="disabled", bg=UiPalette.BORDER, activebackground=UiPalette.BORDER, disabledforeground=UiPalette.MUTED)
        self._schedule_refresh(800)

    def _build_signal_preview_request(self, side: str, config) -> dict | None:
        mt5 = self.monitor.mt5
        symbol = self._selected_symbol()
        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None or tick is None or info.trade_tick_size <= 0 or info.trade_tick_value <= 0:
            self._append_log("Không đủ dữ liệu tick/symbol để dựng kèo preview.", badge="INFO")
            return None
        spread = float(tick.ask - tick.bid)
        if spread > float(getattr(config, "max_spread", spread)):
            self._append_log(f"Signal bị chặn vì spread {spread:.3f} > cap {float(config.max_spread):.3f}.", badge="INFO")
            return None
        entry = float(tick.ask if side == "long" else tick.bid)
        stop_distance = float(config.stop_distance)
        reward_distance = float(config.take_profit_distance)
        digits = int(info.digits)
        is_long = side == "long"
        return {
            "price": round(entry, digits),
            "sl": round(entry - stop_distance if is_long else entry + stop_distance, digits),
            "tp": round(entry + reward_distance if is_long else entry - reward_distance, digits),
        }

    def _evaluate_signal_preview(self) -> None:
        mt5 = self.monitor.mt5
        strategy_key, profile, config, prepare_frame, evaluate_signal = self._selected_strategy_runtime_config()
        self._last_signal_check_value.set(f"{self._clock_gmt7():%H:%M:%S}")
        timeframe = getattr(mt5, f"TIMEFRAME_{profile['timeframe']}")
        rates = mt5.copy_rates_from_pos(self._selected_symbol(), timeframe, 0, 200)
        if rates is None or len(rates) < 121:
            self._set_last_signal_state("WAIT", f"{STRATEGY_SELECTIONS[strategy_key].label}: chưa đủ dữ liệu nến để đánh giá signal.", UiPalette.WARNING)
            return
        frame = pd.DataFrame(rates)
        frame["timestamp"] = pd.to_datetime(frame["time"], unit="s", utc=True)
        symbol_info = mt5.symbol_info(self._selected_symbol())
        point = float(symbol_info.point) if symbol_info is not None else 0.0
        frame["spread"] = frame["spread"] * point
        prepared = prepare_frame(frame, config)
        entry_mode = str(profile.get("entry_mode", "close_confirm"))
        row = prepared.iloc[-1] if entry_mode == "immediate_signal" else prepared.iloc[-2]
        side = evaluate_signal(row, config)
        if side is None:
            self._set_last_signal_state("NONE", f"{STRATEGY_SELECTIONS[strategy_key].label}: chưa có signal mới ở nến đã đóng gần nhất.", UiPalette.INFO)
            return
        bar_time = int(row["time"])
        if self._has_open_position_for_selected_symbol():
            plan = side.upper()
            if self._last_signal_bar_by_strategy.get(strategy_key) != bar_time:
                self._last_signal_bar_by_strategy[strategy_key] = bar_time
                request = self._build_signal_preview_request(side, config)
                if request is not None:
                    entry_time = datetime.fromtimestamp(bar_time, tz=UTC) + timedelta(minutes=5 if str(profile["timeframe"]) == "M5" else 15)
                    eta = entry_time.astimezone(GMT_PLUS_7).strftime("%H:%M")
                    blocked_telegram_message = format_telegram_signal_message(
                        symbol=self._selected_symbol(),
                        timeframe=str(profile["timeframe"]),
                        eta=eta,
                        side=side,
                        request=request,
                        blocked=True,
                    )
                    self._append_log(
                        f"Blocked Signal {plan} • Entry {request['price']:.2f} • SL {request['sl']:.2f} • TP {request['tp']:.2f} • ETA {eta} • position {self._selected_symbol()} running; one-position guard.",
                        badge=plan,
                        telegram_message=blocked_telegram_message,
                    )
                else:
                    self._append_log(
                        f"Blocked Signal {plan} • position {self._selected_symbol()} running; one-position guard.",
                        badge="INFO",
                    )
            self._set_last_signal_state(
                "WAIT",
                f"{STRATEGY_SELECTIONS[strategy_key].label}: plan {plan} blocked because {self._selected_symbol()} position running.",
                UiPalette.WARNING,
            )
            return
        if self._last_signal_bar_by_strategy.get(strategy_key) == bar_time:
            return
        self._last_signal_bar_by_strategy[strategy_key] = bar_time
        request = self._build_signal_preview_request(side, config)
        if request is None:
            self._set_last_signal_state("BLOCKED", f"{STRATEGY_SELECTIONS[strategy_key].label}: có tín hiệu nhưng bị chặn bởi dữ liệu tick/spread hiện tại.", UiPalette.WARNING)
            return
        entry_time = datetime.fromtimestamp(bar_time, tz=UTC) + timedelta(minutes=5 if str(profile["timeframe"]) == "M5" else 15)
        eta = entry_time.astimezone(GMT_PLUS_7).strftime("%H:%M")
        badge = "LONG" if side == "long" else "SHORT"
        self._set_last_signal_state(badge, f"{STRATEGY_SELECTIONS[strategy_key].label} • Entry {request['price']:.2f} • SL {request['sl']:.2f} • TP {request['tp']:.2f} • ETA {eta}", UiPalette.SUCCESS if badge == "LONG" else UiPalette.DANGER)
        # {STRATEGY_SELECTIONS[strategy_key].label}
        telegram_message = format_telegram_signal_message(
            symbol=self._selected_symbol(),
            timeframe=str(profile["timeframe"]),
            eta=eta,
            side=side,
            request=request,
        )
        self._append_log(
            f"Signal {side.upper()} • Entry {request['price']:.2f} • SL {request['sl']:.2f} • TP {request['tp']:.2f} • ETA {eta}",
            badge=badge,
            telegram_message=telegram_message,
        )
    def _schedule_refresh(self, delay_ms: int | None = None) -> None:
        """Keep exactly one Tk refresh callback queued to prevent refresh storms."""
        if self._closed:
            return
        if self._refresh_after_id is not None:
            try:
                self.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
        self._refresh_after_id = self.after(self.refresh_ms if delay_ms is None else delay_ms, self._refresh)

    def _refresh(self) -> None:
        self._refresh_after_id = None
        if self._closed:
            return
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True
        try:
            # MetaTrader5 exposes one active terminal connection.  History owns
            # that connection while its background read is in progress.
            if self._history_loading:
                return
            if self._selected_local_accounts:
                snapshots: list[tuple[Mt5TerminalCandidate, MonitorSnapshot]] = []
                for candidate in self._selected_local_accounts:
                    try:
                        snapshot, _deals, _raw = self._read_local_account(candidate)
                        snapshots.append((candidate, snapshot))
                    except Exception as exc:
                        self._append_log(f"Không đọc được MT5 local {candidate.login or candidate.path.name}: {exc}", badge="ERROR")
                self._selected_account_snapshots = snapshots
                if not snapshots:
                    raise RuntimeError("Không đọc được account MT5 local nào đã chọn.")
                self._render_snapshot(snapshots[0][1], include_log_entries=False)
                self._render_selected_account_cards()
                self._render_open_positions(
                    [
                        (str(snapshot.login), position)
                        for _candidate, snapshot in snapshots
                        for position in snapshot.positions
                    ]
                )
                # Signal preview remains bound to the primary runner only; do not
                # evaluate against whichever terminal happened to be read last.
                self._set_last_signal_state("NONE", "Đang theo dõi nhiều MT5 local read-only.", UiPalette.INFO)
            else:
                self._selected_account_snapshots = []
                self._render_selected_account_cards()
                self._render_snapshot(self.monitor.refresh())
                self._evaluate_signal_preview()
        except Exception as exc:
            self._mt5_status_value.set("MT5 OFFLINE")
            self._set_bot_status_state("ERROR", "Không cập nhật được trạng thái bot vì refresh MT5 lỗi.", UiPalette.DANGER)
            self._append_log(f"Lỗi kết nối: {exc}", badge="ERROR")
        finally:
            self._refresh_in_progress = False
            self._schedule_refresh()

    def _render_snapshot(self, snapshot: MonitorSnapshot, *, include_log_entries: bool = True) -> None:
        self._latest_snapshot = snapshot
        self._raw_currency = str(snapshot.currency or "USD").upper()
        # Tạm hard-code thông tin hiển thị trên GUI theo yêu cầu.
        self._account_value.set(f"#{snapshot.login}")
        # self._account_value.set(DISPLAY_ACCOUNT_LOGIN)
        self._equity_value.set(f"{snapshot.equity:,.2f} {self._display_currency(snapshot.currency)}")
        self._position_count_value.set(str(len(snapshot.positions)))
        self._mt5_status_value.set(f"ONLINE • {snapshot.server}")
        # self._mt5_status_value.set(f"ONLINE • {DISPLAY_ACCOUNT_SERVER}")
        if snapshot.positions and snapshot.positions[0].opened_at is not None:
            opened = self._format_gmt7_datetime(snapshot.positions[0].opened_at)
            self._position_day_value.set(f"{opened[8:10]}/{opened[5:7]}")
        else:
            self._position_day_value.set(self._clock_gmt7().strftime("%d/%m"))
        self._updated_value.set(self._clock_gmt7().strftime("%H:%M:%S"))
        self._updated_button_value.set(f"CẬP NHẬT\n{self._updated_value.get()}")
        self._apply_bot_status(snapshot)
        self._render_open_positions([(str(snapshot.login), position) for position in snapshot.positions])
        if include_log_entries:
            for entry in snapshot.log_entries:
                self._append_log(entry)

    def _render_open_positions(self, records: list[tuple[str, object]]) -> None:
        """Render all observed positions with account provenance; display only."""
        self.positions.delete(*self.positions.get_children())
        def opened_timestamp(record: tuple[str, object]) -> float:
            opened_at = getattr(record[1], "opened_at", None)
            if opened_at is None:
                return 0.0
            if opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=UTC)
            return opened_at.timestamp()

        ordered = sorted(records, key=opened_timestamp, reverse=True)
        for account, position in ordered:
            profit = float(getattr(position, "profit", 0.0) or 0.0)
            tag = "profit" if profit >= 0 else "loss"
            self.positions.insert(
                "",
                "end",
                tags=(tag,),
                values=(
                    self._format_gmt7_timestamp(getattr(position, "opened_at", None)),
                    f"#{account}",
                    self._display_symbol(str(getattr(position, "symbol", ""))),
                    str(getattr(position, "side", "")),
                    f"{float(getattr(position, 'volume', 0.0) or 0.0):.2f}",
                    f"{float(getattr(position, 'price_open', 0.0) or 0.0):.2f}",
                    f"{float(getattr(position, 'stop_loss', 0.0) or 0.0):.2f}",
                    f"{float(getattr(position, 'take_profit', 0.0) or 0.0):.2f}",
                    f"{profit:+.2f}",
                ),
            )

    @staticmethod
    def _classify_log_badge(message: str, explicit_badge: str | None = None) -> tuple[str, str]:
        badge = (explicit_badge or "").strip().upper()
        if badge in {"LONG", "SHORT", "CLOSE", "ERROR", "INFO"}:
            return {
                "LONG": ("LONG", UiPalette.SUCCESS),
                "SHORT": ("SHORT", UiPalette.DANGER),
                "CLOSE": ("CLOSE", UiPalette.CLOSE),
                "ERROR": ("ERROR", UiPalette.DANGER),
                "INFO": ("INFO", UiPalette.INFO),
            }[badge]
        upper = message.upper()
        if any(token in upper for token in ("ĐÃ ĐÓNG", " ĐÓNG /", " CLOSED", " CLOSE")):
            return "CLOSE", UiPalette.CLOSE
        if any(token in upper for token in (" SELL", "SHORT", " BÁN")):
            return "SHORT", UiPalette.DANGER
        if any(token in upper for token in (" BUY", "LONG", " MUA")):
            return "LONG", UiPalette.SUCCESS
        if any(token in upper for token in ("LỖI", "ERROR", "FAILED", "REJECTED")):
            return "ERROR", UiPalette.DANGER
        return "INFO", UiPalette.INFO

    def _append_log(self, message: str, *, badge: str | None = None, telegram_message: str | None = None) -> None:
        badge_text, badge_color = self._classify_log_badge(message, badge)
        entry = LogEntry(
            timestamp=f"{self._clock_gmt7():%H:%M:%S}",
            badge=badge_text,
            message=message,
            badge_color=badge_color,
            telegram_message=telegram_message,
        )
        self._log_history.insert(0, entry)
        self._log_history = self._log_history[:500]
        self._replay_logs()

    def _replay_logs(self) -> None:
        for child in self._log_rows.winfo_children():
            child.destroy()
        message_wrap = max(560, self._log_canvas.winfo_width() - 170)
        for index, entry in enumerate(self._log_history):
            row_bg = UiPalette.TABLE if index % 2 == 0 else UiPalette.TABLE_ALT
            row = tk.Frame(self._log_rows, bg=row_bg, padx=10, pady=6)
            row.pack(fill="x", padx=0, pady=(0, 6))
            self._label(row, text=entry.timestamp, font=("Consolas", 9), fg=UiPalette.MUTED, bg=row_bg, width=9, anchor="w").pack(side="left")
            badge = (
                self._signal_badge(row, entry)
                if entry.telegram_message and entry.badge in {"LONG", "SHORT"}
                else self._badge(row, entry.badge, entry.badge_color, row_bg)
            )
            badge.pack(side="left", padx=(10, 8))
            self._label(row, text=entry.message, font=("Segoe UI", 9), bg=row_bg, justify="left", wraplength=message_wrap, anchor="w").pack(side="left", fill="x", expand=True)
        self._on_log_frame_configure(None)
        self._log_canvas.yview_moveto(0)

    def _badge(self, parent: tk.Misc, text: str, fill: str, bg: str) -> tk.Canvas:
        width = max(54, 16 + len(text) * 8)
        canvas = tk.Canvas(parent, width=width, height=24, bg=bg, highlightthickness=0, bd=0)
        radius = 10
        x1, y1, x2, y2 = 2, 2, width - 2, 22
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline=fill, tags=("badge_fill",))
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline=fill, tags=("badge_fill",))
        canvas.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, fill=fill, outline=fill, tags=("badge_fill",))
        canvas.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, fill=fill, outline=fill, tags=("badge_fill",))
        canvas.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, fill=fill, outline=fill, tags=("badge_fill",))
        canvas.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, fill=fill, outline=fill, tags=("badge_fill",))
        canvas.create_text(
            width / 2,
            12,
            text=text,
            fill=UiPalette.BADGE_FG,
            font=("Segoe UI", 8, "bold"),
            tags=("badge_text",),
        )
        return canvas

    def _signal_badge(self, parent: tk.Misc, entry: LogEntry) -> tk.Canvas:
        """One compact directional badge that becomes the Tele action on hover."""
        badge = self._badge(parent, entry.badge, entry.badge_color, parent.cget("bg"))
        badge.configure(cursor="hand2")
        badge.bind("<Button-1>", lambda _event, message=entry.telegram_message: self._confirm_telegram_signal(message))
        badge.bind("<Enter>", lambda _event: (badge.itemconfigure("badge_text", text="TELE"), badge.itemconfigure("badge_fill", fill=UiPalette.TELEGRAM_BLUE, outline=UiPalette.TELEGRAM_BLUE)))
        badge.bind("<Leave>", lambda _event: (badge.itemconfigure("badge_text", text=entry.badge), badge.itemconfigure("badge_fill", fill=entry.badge_color, outline=entry.badge_color)))
        return badge

    def _confirm_telegram_signal(self, telegram_message: str | None) -> None:
        if not telegram_message:
            return
        popup = tk.Toplevel(self)
        popup.title("Xác nhận gửi Telegram")
        popup.transient(self)
        popup.grab_set()
        popup.configure(background=UiPalette.CARD)
        popup.resizable(False, False)
        shell = tk.Frame(popup, bg=UiPalette.CARD, padx=18, pady=16)
        shell.pack(fill="both", expand=True)
        self._label(shell, text="Gửi signal này qua Telegram?", font=("Segoe UI", 11, "bold"), bg=UiPalette.CARD).pack(anchor="w")
        preview = tk.Label(
            shell,
            text=telegram_message,
            font=("Consolas", 10),
            fg=UiPalette.TEXT,
            bg=UiPalette.TABLE,
            justify="left",
            anchor="w",
            padx=12,
            pady=10,
        )
        preview.pack(fill="x", pady=(12, 14))
        actions = tk.Frame(shell, bg=UiPalette.CARD)
        actions.pack(fill="x")
        tk.Button(
            actions,
            text="Cancel",
            command=popup.destroy,
            font=("Segoe UI", 9, "bold"),
            fg=UiPalette.TEXT,
            bg=UiPalette.CARD_ALT,
            activeforeground=UiPalette.TEXT,
            activebackground=UiPalette.BORDER,
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side="right")
        tk.Button(
            actions,
            text="Send",
            command=lambda: self._send_telegram_signal(popup, telegram_message),
            font=("Segoe UI", 9, "bold"),
            fg="#101722",
            bg=UiPalette.ACCENT,
            activeforeground="#101722",
            activebackground="#E8C270",
            relief="flat",
            bd=0,
            padx=14,
            pady=6,
            cursor="hand2",
        ).pack(side="right", padx=(0, 8))
        self._center_window(popup)

    def _send_telegram_signal(self, popup: tk.Toplevel, telegram_message: str) -> None:
        sent = self._telegram_notifier.send(telegram_message)
        popup.destroy()
        self._append_log(
            "Đã gửi signal Telegram." if sent else self._telegram_notifier.last_status,
            badge="INFO" if sent else "ERROR",
        )

    def _on_log_frame_configure(self, _event) -> None:
        self._log_canvas.configure(scrollregion=self._log_canvas.bbox("all"))

    def _on_log_canvas_configure(self, event) -> None:
        self._log_canvas.itemconfigure(self._log_canvas_window, width=event.width)

    def _clear_logs(self) -> None:
        self._log_history.clear()
        self._replay_logs()

    def _on_strategy_selection(self, _event) -> None:
        selected_label = self._profile_choice.get()
        matching = next((selection for selection in STRATEGY_SELECTIONS.values() if selection.label == selected_label), None)
        if matching is None:
            return
        self._strategy_choice.set(matching.key)
        self._apply_strategy_profile(matching.key)
        self._set_last_signal_state("NONE", f"Đã đổi sang {matching.label}. Chờ lần quét signal kế tiếp.", UiPalette.INFO)
        if self._latest_snapshot is not None:
            self._apply_bot_status(self._latest_snapshot)
        else:
            self._update_run_button(None)
        self._append_log(f"Đã chuyển chiến lược sang {matching.label} và nạp preset TP/SL/volume mặc định.", badge="INFO")

    def _on_close(self) -> None:
        self._closed = True
        if self._refresh_after_id is not None:
            try:
                self.after_cancel(self._refresh_after_id)
            except tk.TclError:
                pass
            self._refresh_after_id = None
        self.monitor.stop()
        self.destroy()

    def _open_local_accounts_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("MT5 LOCAL — chọn account read-only")
        window.geometry("1180x570")
        window.minsize(1040, 520)
        window.configure(bg=UiPalette.APP)
        shell = tk.Frame(window, bg=UiPalette.APP, padx=20, pady=18)
        shell.pack(fill="both", expand=True)
        self._label(shell, text="MT5 LOCAL ACCOUNTS", font=("Segoe UI", 15, "bold"), bg=UiPalette.APP).pack(anchor="w")
        self._label(shell, text="Quét terminal MT5 local và đọc account_info() — không gửi lệnh.", fg=UiPalette.MUTED, bg=UiPalette.APP).pack(anchor="w", pady=(4, 12))
        tree = ttk.Treeview(shell, columns=("selected", "account", "name", "server", "path"), show="headings", style="LocalAccount.Treeview", height=8)
        for key, title, width in (("selected", "CHỌN", 108), ("account", "ACCOUNT", 118), ("name", "ACCOUNT NAME", 210), ("server", "SERVER", 220), ("path", "TERMINAL", 390)):
            tree.heading(key, text=title); tree.column(key, width=width, anchor="w")
        tree.tag_configure("local_selected", background=UiPalette.CARD_ALT, foreground=UiPalette.TEXT)
        tree.tag_configure("local_alt", background=UiPalette.TABLE_ALT, foreground=UiPalette.TEXT)
        tree.pack(fill="both", expand=True)
        status = tk.StringVar(value="Chưa quét")
        self._label(shell, textvariable=status, fg=UiPalette.MUTED, bg=UiPalette.APP).pack(anchor="w", pady=(8, 6))
        actions = tk.Frame(shell, bg=UiPalette.APP); actions.pack(fill="x")

        candidates_by_iid: dict[str, Mt5TerminalCandidate] = {}
        selected_paths = {str(candidate.path).casefold() for candidate in self._selected_local_accounts}

        def render_candidates(candidates, message: str) -> None:
            tree.delete(*tree.get_children())
            candidates_by_iid.clear()
            for index, candidate in enumerate(candidates):
                iid = str(index)
                candidates_by_iid[iid] = candidate
                checked = "☑" if str(candidate.path).casefold() in selected_paths else "☐"
                tag = "local_selected" if checked == "☑" else ("local_alt" if index % 2 else "")
                tree.insert("", "end", iid=iid, values=(checked, candidate.login or "—", candidate.name or "—", candidate.server or "—", self._display_terminal_path(candidate.path)), tags=(tag,) if tag else ())
            status.set(message)

        def toggle_candidate(event) -> str | None:
            row = tree.identify_row(event.y)
            column = tree.identify_column(event.x)
            if not row or column != "#1":
                return None
            candidate = candidates_by_iid.get(row)
            if candidate is None:
                return None
            key = str(candidate.path).casefold()
            if key in selected_paths:
                selected_paths.remove(key)
            else:
                selected_paths.add(key)
            values = list(tree.item(row, "values"))
            values[0] = "☑" if key in selected_paths else "☐"
            tree.item(row, values=values)
            tree.item(row, tags=("local_selected",) if key in selected_paths else ())
            return "break"

        tree.bind("<Button-1>", toggle_candidate)

        def refresh() -> None:
            status.set("Đang cập nhật account MT5 local…"); window.update_idletasks()

            def worker() -> None:
                try:
                    import MetaTrader5 as mt5
                    candidates = scan_mt5_terminals(mt5)
                    error = None
                except Exception as exc:
                    candidates = ()
                    error = exc

                def render() -> None:
                    if not window.winfo_exists():
                        return
                    if error is not None:
                        status.set(f"Không quét được MT5: {error}")
                    else:
                        connected = sum(1 for candidate in candidates if candidate.login)
                        render_candidates(candidates, f"Tìm thấy {len(candidates)} terminal, đọc được {connected} account. Tick các account cần theo dõi read-only.")

                window.after(0, render)

            threading.Thread(target=worker, name="mt5-local-scan", daemon=True).start()

        def apply_selection() -> None:
            selected = [candidate for candidate in candidates_by_iid.values() if str(candidate.path).casefold() in selected_paths]
            if not selected:
                status.set("Tick ít nhất một account để theo dõi.")
                return
            self._selected_local_accounts = selected
            self._selected_account_snapshots = []
            status.set(f"Đã áp dụng {len(selected)} account theo dõi read-only.")
            self._append_log(f"Đang theo dõi {len(selected)} MT5 local: {', '.join(candidate.login or candidate.path.name for candidate in selected)}.", badge="INFO")
            self._schedule_refresh(0)

        tk.Button(actions, text="QUÉT LẠI", command=refresh, bg=UiPalette.ACCENT, fg="#101722", relief="flat", bd=0, padx=14, pady=7).pack(side="left")
        tk.Button(actions, text="ÁP DỤNG THEO DÕI", command=apply_selection, bg=UiPalette.CARD_ALT, fg=UiPalette.TEXT, relief="flat", bd=0, padx=14, pady=7).pack(side="left", padx=8)
        cached = load_cached_candidates()
        if cached:
            render_candidates(cached, f"Hiển thị nhanh {len(cached)} account từ lần quét trước; đang cập nhật live…")
        refresh()

    def _open_vps_window(self) -> None:
        window = tk.Toplevel(self)
        window.title("VPS / RDP")
        window.geometry("720x420")
        window.minsize(640, 380)
        window.configure(bg=UiPalette.APP)
        shell = tk.Frame(window, bg=UiPalette.APP, padx=28, pady=26)
        shell.pack(fill="both", expand=True)

        header = tk.Frame(shell, bg=UiPalette.APP)
        header.pack(fill="x", pady=(0, 18))
        self._label(header, text="VPS / REMOTE DESKTOP", font=("Segoe UI", 17, "bold"), bg=UiPalette.APP).pack(anchor="w")
        self._label(header, text="Kết nối phiên Windows Remote Desktop tới VPS đã được cấp quyền.", fg=UiPalette.MUTED, bg=UiPalette.APP).pack(anchor="w", pady=(5, 0))

        host = tk.StringVar()
        username = tk.StringVar()
        result = tk.StringVar(value="Password được nhập trong hộp thoại Windows native; GOLD Trader không lưu password.")

        form = self._card(shell, padding=18)
        form.pack(fill="x")
        form.grid_columnconfigure(0, weight=1, uniform="rdp_field")
        form.grid_columnconfigure(1, weight=1, uniform="rdp_field")
        self._labeled_entry(form, 0, 0, "IP / HOSTNAME VPS", host)
        self._labeled_entry(form, 0, 1, "USERNAME", username)
        self._label(form, text="Ví dụ: 192.0.2.10 hoặc vps.example.com", font=("Segoe UI", 8), fg=UiPalette.MUTED).grid(row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 0))

        notice = tk.Frame(shell, bg=UiPalette.CARD_ALT, padx=14, pady=12, highlightbackground=UiPalette.BORDER, highlightthickness=1)
        notice.pack(fill="x", pady=(14, 0))
        self._label(notice, text="THÔNG TIN BẢO MẬT", font=("Segoe UI", 8, "bold"), fg=UiPalette.ACCENT, bg=UiPalette.CARD_ALT).pack(anchor="w")
        self._label(notice, textvariable=result, font=("Segoe UI", 9), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT, wraplength=620, justify="left").pack(anchor="w", pady=(4, 0))

        actions = tk.Frame(shell, bg=UiPalette.APP)
        actions.pack(fill="x", pady=(18, 0))

        def connect() -> None:
            try:
                open_remote_desktop(host.get(), username.get())
                result.set("Đã mở Windows Remote Desktop. Nhập password trong cửa sổ RDP native.")
            except Exception as exc:
                result.set(f"Không mở được RDP: {exc}")

        tk.Button(
            actions,
            text="MỞ REMOTE DESKTOP  →",
            command=connect,
            font=("Segoe UI", 10, "bold"),
            fg="#101722",
            bg=UiPalette.ACCENT,
            activeforeground="#101722",
            activebackground="#E8C270",
            relief="flat",
            bd=0,
            padx=18,
            pady=10,
            cursor="hand2",
        ).pack(side="left")
        self._label(actions, text="Mở mstsc trên máy này", font=("Segoe UI", 9), fg=UiPalette.MUTED, bg=UiPalette.APP).pack(side="right", pady=(8, 0))
        self._center_window(window)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only GOLD Trader dashboard for RSIQUI V3 paper positions.")
    parser.add_argument("--config", default=str(CONFIG_ROOT / "final_x_m5.json"), help="RSIQUI V3 JSON read for display only")
    parser.add_argument("--symbol", default="XAUUSD", help="MT5 symbol to observe")
    parser.add_argument("--refresh-seconds", type=float, default=2.0)
    args = parser.parse_args()
    profile = load_read_only_profile(args.config)
    profile["symbol"] = args.symbol
    import MetaTrader5 as mt5

    monitor = RsiquiV3PositionMonitor(symbol=args.symbol, mt5=mt5)
    RsiquiV3MonitorApp(monitor, profile, refresh_ms=max(500, int(args.refresh_seconds * 1000))).mainloop()


if __name__ == "__main__":
    main()
