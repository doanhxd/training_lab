from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
import tkinter as tk
from tkinter import ttk
from typing import Callable

import pandas as pd

from trading_lab.monitoring.rsiqui.position_monitor import MonitorSnapshot, RsiquiV3PositionMonitor, RunnerView
from trading_lab.telegram_notifier import TelegramNotifier, TelegramSettings, format_signal_message


APP_TITLE = "RSIQUI V3 • GOLD Trader"
REFRESH_MILLISECONDS = 2_000
CONFIG_ROOT = Path(__file__).resolve().parents[2] / "configs" / "strategies" / "rsiqui"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TELEGRAM_TARGETS_PATH = CONFIG_ROOT / "telegram_targets.json"
TELEGRAM_TEMP_DISABLED = True
DEFAULT_TELEGRAM_TARGETS = (
    {"key": "portfolio_managers", "label": "Portfolio Managers", "chat_id": "-5043082181", "message_thread_id": None},
)


class UiPalette:
    """Semantic palettes for the GOLD TRADER dashboard."""

    DARK = {
        "mode": "dark", "app": "#09111F", "sidebar": "#0D192B", "card": "#111F33", "card_alt": "#14263D",
        "border": "#233853", "text": "#F3F7FC", "muted": "#91A5BD", "nav_text": "#F3F7FC", "nav_muted": "#A8BDD6", "accent": "#D9AA54",
        "accent_dark": "#B9842B", "success": "#35C78A", "danger": "#F0727F", "warning": "#E3A64D", "info": "#5AA8FF",
        "table": "#101C2D", "table_alt": "#132238", "badge_fg": "#F8FBFF",
    }
    LIGHT = {
        "mode": "light", "app": "#F5F7FB", "sidebar": "#10233F", "card": "#FFFFFF", "card_alt": "#EAF0F8",
        "border": "#D2DCE9", "text": "#14253C", "muted": "#60758F", "nav_text": "#F3F7FC", "nav_muted": "#A8BDD6", "accent": "#B7791F",
        "accent_dark": "#926017", "success": "#168A60", "danger": "#CC4151", "warning": "#B7791F", "info": "#2563EB",
        "table": "#FFFFFF", "table_alt": "#F0F4F9", "badge_fg": "#FFFFFF",
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
class TelegramTarget:
    key: str
    label: str
    chat_id: str
    message_thread_id: int | None = None


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    badge: str
    message: str
    badge_color: str


STRATEGY_SELECTIONS = {
    "rsiqui_v3_ori": StrategySelection("rsiqui_v3_ori", "rsiqui_v3_ori", CONFIG_ROOT / "ori_m5_demo.json"),
    "rsiqui_v3_neg": StrategySelection("rsiqui_v3_neg", "rsiqui_v3_neg", CONFIG_ROOT / "neg_m5_demo.json"),
    "rsiqui_v3_final": StrategySelection("rsiqui_v3_final", "rsiqui_v3_final", CONFIG_ROOT / "final_m5_demo.json"),
}

RUNNER_SCRIPT_BY_STRATEGY = {
    "rsiqui_v3_ori": PROJECT_ROOT / "runners" / "mt5" / "rsiqui_ori_demo.py",
    "rsiqui_v3_neg": PROJECT_ROOT / "runners" / "mt5" / "rsiqui_neg_demo.py",
    "rsiqui_v3_final": PROJECT_ROOT / "runners" / "mt5" / "rsiqui_final_demo.py",
}

RUNNER_MODULE_BY_STRATEGY = {
    "rsiqui_v3_ori": "trading_lab.runners.mt5.rsiqui_ori_demo",
    "rsiqui_v3_neg": "trading_lab.runners.mt5.rsiqui_neg_demo",
    "rsiqui_v3_final": "trading_lab.runners.mt5.rsiqui_final_demo",
}


def _load_json(path: str | Path) -> dict:
    config_path = Path(path)
    return json.loads(config_path.read_text(encoding="utf-8"))


def _strategy_loader_for_payload(payload: dict):
    strategy = str(payload.get("strategy", "")).strip().lower()
    if strategy == "rsiqui-v3-ori":
        from trading_lab.runners.mt5.rsiqui_ori_demo import load_demo_config

        return "rsiqui_v3_ori", load_demo_config
    if strategy == "rsiqui-v3-neg":
        from trading_lab.runners.mt5.rsiqui_neg_demo import load_demo_config

        return "rsiqui_v3_neg", load_demo_config
    if strategy == "rsiqui-v3-final":
        from trading_lab.runners.mt5.rsiqui_final_demo import load_demo_config

        return "rsiqui_v3_final", load_demo_config
    raise ValueError(f"Unsupported RSIQUI strategy payload: {strategy or '<missing>'}")


def _strategy_runtime(strategy_key: str):
    if strategy_key == "rsiqui_v3_ori":
        from trading_lab.strategies.builtins.rsiqui.ori import evaluate_rsiqui_v3_signal, prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset

        return prepare_rsiqui_v3_frame, evaluate_rsiqui_v3_signal, rsiqui_v3_config_for_preset
    if strategy_key == "rsiqui_v3_neg":
        from trading_lab.strategies.builtins.rsiqui.neg import evaluate_rsiqui_v3_signal, prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset

        return prepare_rsiqui_v3_frame, evaluate_rsiqui_v3_signal, rsiqui_v3_config_for_preset
    if strategy_key == "rsiqui_v3_final":
        from trading_lab.strategies.builtins.rsiqui.final import evaluate_rsiqui_v3_signal, prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset

        return prepare_rsiqui_v3_frame, evaluate_rsiqui_v3_signal, rsiqui_v3_config_for_preset
    raise ValueError(f"Unsupported strategy runtime: {strategy_key}")


def load_read_only_profile(config_path: str | Path) -> dict[str, str | float]:
    """Read the RSIQUI contract for display and signal preview only."""
    payload = _load_json(config_path)
    strategy_key, load_demo_config = _strategy_loader_for_payload(payload)
    config = load_demo_config(config_path)
    return {
        "strategy_key": strategy_key,
        "strategy_name": payload["strategy"],
        "timeframe": config.timeframe,
        "preset": config.preset,
        "side": config.trade_side,
        "symbol": str(payload.get("symbol", getattr(config, "symbol", "XAUUSD"))),
        "volume": float(payload["volume"]),
        "risk_usd": float(payload["risk_usd"]),
        "reward_usd": float(payload["reward_usd"]),
        "max_spread": float(payload.get("max_spread", 0.0)),
    }


def load_telegram_targets(path: str | Path = TELEGRAM_TARGETS_PATH) -> list[TelegramTarget]:
    target_path = Path(path)
    if target_path.exists():
        payload = json.loads(target_path.read_text(encoding="utf-8"))
        items = payload if isinstance(payload, list) else payload.get("targets", [])
    else:
        items = list(DEFAULT_TELEGRAM_TARGETS)
    targets: list[TelegramTarget] = []
    for index, item in enumerate(items):
        if not item.get("chat_id"):
            continue
        thread_id = item.get("message_thread_id")
        try:
            parsed_thread_id = int(thread_id) if thread_id not in (None, "") else None
        except (TypeError, ValueError):
            parsed_thread_id = None
        targets.append(
            TelegramTarget(
                key=str(item.get("key", f"target_{index}")),
                label=str(item.get("label", item.get("chat_id"))),
                chat_id=str(item["chat_id"]),
                message_thread_id=parsed_thread_id,
            )
        )
    return targets


class RsiquiV3MonitorApp(tk.Tk):
    """Modern GOLD TRADER observer shell with signal preview + Telegram routing."""

    def __init__(
        self,
        monitor: RsiquiV3PositionMonitor,
        profile: dict[str, str | float],
        *,
        refresh_ms: int = REFRESH_MILLISECONDS,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        super().__init__()
        self.monitor = monitor
        self.refresh_ms = refresh_ms
        self.clock = clock
        self._closed = False
        self._theme_mode = "light"
        self._latest_snapshot: MonitorSnapshot | None = None
        self._log_history: list[LogEntry] = []
        self._last_signal_bar_by_strategy: dict[str, int] = {}
        self._last_telegram_signal_key: tuple[str, int, str, str] | None = None
        self.shell: tk.Frame | None = None
        self._strategy_profiles = {key: load_read_only_profile(selection.config_path) for key, selection in STRATEGY_SELECTIONS.items()}
        current_strategy_key = str(profile.get("strategy_key", "rsiqui_v3_ori"))
        if current_strategy_key not in self._strategy_profiles:
            current_strategy_key = "rsiqui_v3_ori"
        self._telegram_targets = load_telegram_targets()
        self._telegram_target_map = {target.label: target for target in self._telegram_targets}

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
        self._profile_line_value = tk.StringVar(value="")
        self._strategy_choice = tk.StringVar(value=current_strategy_key)
        self._timeframe_value = tk.StringVar(value="M5")
        self._preset_value = tk.StringVar(value="gold-loose")
        self._side_value = tk.StringVar(value="both")
        self._volume_value = tk.StringVar(value="0.01")
        self._risk_value = tk.StringVar(value="5.00")
        self._reward_value = tk.StringVar(value="5.00")
        self._telegram_target_value = tk.StringVar(value="")
        self._telegram_enabled = tk.BooleanVar(value=False)
        self._telegram_toggle_label = tk.StringVar(value="TELEGRAM OFF")
        self._run_button_label = tk.StringVar(value="RUN")
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
        self._update_telegram_controls()
        self._append_log("Observer mode active. No trade orders are sent from this app.", badge="INFO")
        self._refresh()

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "Monitor.Treeview",
            background=UiPalette.TABLE,
            fieldbackground=UiPalette.TABLE,
            foreground=UiPalette.TEXT,
            borderwidth=0,
            rowheight=34,
            font=("Segoe UI", 10),
        )
        style.map("Monitor.Treeview", background=[("selected", UiPalette.ACCENT_DARK)], foreground=[("selected", UiPalette.TEXT)])
        style.configure(
            "Monitor.Treeview.Heading",
            background=UiPalette.CARD_ALT,
            foreground=UiPalette.MUTED,
            borderwidth=0,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
        )

    def _label(self, parent: tk.Misc, text: str = "", *, font: tuple, fg: str | None = None, bg: str | None = None, **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, font=font, fg=fg or UiPalette.TEXT, bg=bg or UiPalette.CARD, bd=0, highlightthickness=0, **kwargs)

    def _card(self, parent: tk.Misc, *, padding: int = 18, bg: str | None = None) -> tk.Frame:
        card = tk.Frame(parent, bg=bg or UiPalette.CARD, highlightbackground=UiPalette.BORDER, highlightthickness=1, bd=0)
        card.configure(padx=padding, pady=padding)
        return card

    def _current_profile(self) -> dict[str, str | float]:
        return self._strategy_profiles[self._strategy_choice.get()]

    def _apply_strategy_profile(self, strategy_key: str) -> None:
        profile = self._strategy_profiles[strategy_key]
        self._timeframe_value.set(str(profile["timeframe"]))
        self._preset_value.set(str(profile["preset"]))
        self._side_value.set(str(profile["side"]))
        self._volume_value.set(f"{float(profile['volume']):.2f}")
        self._risk_value.set(f"{float(profile['risk_usd']):.2f}")
        self._reward_value.set(f"{float(profile['reward_usd']):.2f}")
        self._profile_line_value.set(
            f"{STRATEGY_SELECTIONS[strategy_key].label.upper()}  /  {profile['symbol']} {profile['timeframe']}  /  PRESET {str(profile['preset']).upper()}  /  {str(profile['side']).upper()}"
        )

    def _build_ui(self) -> None:
        shell = tk.Frame(self, bg=UiPalette.APP)
        shell.pack(fill="both", expand=True)
        self.shell = shell

        sidebar = tk.Frame(shell, bg=UiPalette.SIDEBAR, width=230, padx=20, pady=24)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._label(sidebar, text="GOLD", font=("Segoe UI", 20, "bold"), fg=UiPalette.ACCENT, bg=UiPalette.SIDEBAR).pack(anchor="w")
        self._label(sidebar, text="TRADER", font=("Segoe UI", 20, "bold"), fg=UiPalette.NAV_TEXT, bg=UiPalette.SIDEBAR).pack(anchor="w", pady=(0, 6))
        self._label(sidebar, text="RSIQUI V3 • SIGNAL DESK", font=("Segoe UI", 9, "bold"), fg=UiPalette.NAV_MUTED, bg=UiPalette.SIDEBAR).pack(anchor="w")

        nav_line = tk.Frame(sidebar, bg=UiPalette.ACCENT, height=2)
        nav_line.pack(fill="x", pady=(26, 18))
        nav_item = tk.Frame(sidebar, bg=UiPalette.CARD_ALT, padx=12, pady=11)
        nav_item.pack(fill="x")
        self._label(nav_item, text="◉  THEO DÕI LỆNH & KÈO", font=("Segoe UI", 10, "bold"), bg=UiPalette.CARD_ALT).pack(anchor="w")

        sidebar_bottom = tk.Frame(sidebar, bg=UiPalette.SIDEBAR)
        sidebar_bottom.pack(side="bottom", fill="x")
        safe = self._card(sidebar_bottom, padding=14, bg="#10263A")
        safe.pack(fill="x")
        self._label(safe, text="CHẾ ĐỘ AN TOÀN", font=("Segoe UI", 9, "bold"), fg=UiPalette.SUCCESS, bg="#10263A").pack(anchor="w")
        self._label(
            safe,
            text="Không gửi lệnh MT5.\nChỉ hiển thị dữ liệu và gửi cảnh báo Telegram nếu bật.",
            font=("Segoe UI", 9),
            fg=UiPalette.NAV_MUTED,
            bg="#10263A",
            justify="left",
        ).pack(anchor="w", pady=(6, 0))
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
        self._label(title_group, text="TRẠM QUAN SÁT RSIQUI V3", font=("Segoe UI", 19, "bold"), bg=UiPalette.APP).pack(anchor="w")
        self._label(title_group, text="GOLD Trader • theo dõi lệnh, preset và cảnh báo Telegram theo tín hiệu", font=("Segoe UI", 10), fg=UiPalette.MUTED, bg=UiPalette.APP).pack(anchor="w", pady=(4, 0))
        updated = tk.Frame(header, bg=UiPalette.CARD_ALT, padx=12, pady=9)
        updated.pack(side="right", anchor="s")
        self._label(updated, text="CẬP NHẬT", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT).pack(anchor="e")
        self._label(updated, textvariable=self._updated_value, font=("Consolas", 10, "bold"), fg=UiPalette.ACCENT, bg=UiPalette.CARD_ALT).pack(anchor="e", pady=(2, 0))

        profile = self._card(content, padding=15, bg=UiPalette.CARD_ALT)
        profile.pack(fill="x", pady=(0, 14))
        self._label(profile, text="PROFILE ĐANG THEO DÕI", font=("Segoe UI", 9, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT).pack(anchor="w")
        self._label(profile, textvariable=self._profile_line_value, font=("Segoe UI", 11, "bold"), fg=UiPalette.ACCENT, bg=UiPalette.CARD_ALT).pack(anchor="w", pady=(5, 0))

        metrics = tk.Frame(content, bg=UiPalette.APP)
        metrics.pack(fill="x", pady=(0, 14))
        for column in range(3):
            metrics.grid_columnconfigure(column, weight=1, uniform="metric")
        self._metric(metrics, 0, "TÀI KHOẢN MT5", self._account_value, UiPalette.TEXT)
        self._metric(metrics, 1, "EQUITY", self._equity_value, UiPalette.SUCCESS)
        self._metric(metrics, 2, "LỆNH XAUUSD", self._position_count_value, UiPalette.ACCENT)

        strategy_card = self._card(content, padding=16)
        strategy_card.pack(fill="x", pady=(0, 14))
        self._label(strategy_card, text="CHIẾN LƯỢC & CẢNH BÁO", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self._label(strategy_card, text="Chọn biến thể RSIQUI, xem sẵn preset TP/SL/volume và bật nơi gửi kèo Telegram.", font=("Segoe UI", 9), fg=UiPalette.MUTED).pack(anchor="w", pady=(4, 12))

        fields = tk.Frame(strategy_card, bg=UiPalette.CARD)
        fields.pack(fill="x")
        for column in range(4):
            fields.grid_columnconfigure(column, weight=1)
        self._labeled_combobox(fields, 0, 0, "CHIẾN LƯỢC", self._strategy_choice, [item.label for item in STRATEGY_SELECTIONS.values()], self._on_strategy_selection)
        run_wrap = tk.Frame(fields, bg=UiPalette.CARD)
        run_wrap.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        self._label(run_wrap, text="CHẠY BOT", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        self._run_button = tk.Button(
            run_wrap,
            textvariable=self._run_button_label,
            command=self._run_selected_strategy,
            font=("Segoe UI", 9, "bold"),
            fg=UiPalette.BADGE_FG,
            bg=UiPalette.SUCCESS,
            activeforeground=UiPalette.BADGE_FG,
            activebackground=UiPalette.SUCCESS,
            relief="flat",
            bd=0,
            padx=18,
            pady=8,
            cursor="hand2",
        )
        self._run_button.pack(fill="x", pady=(5, 0))
        self._labeled_entry(fields, 0, 2, "TIMEFRAME", self._timeframe_value, state="readonly")
        self._labeled_entry(fields, 0, 3, "PRESET", self._preset_value, state="readonly")
        self._labeled_entry(fields, 1, 0, "SIDE", self._side_value, state="readonly")
        self._labeled_entry(fields, 1, 1, "VOLUME LOT", self._volume_value)
        self._labeled_entry(fields, 1, 2, "SL USD", self._risk_value)
        self._labeled_entry(fields, 1, 3, "TP USD", self._reward_value)
        self._labeled_combobox(fields, 2, 0, "GỬI KÈO TELEGRAM", self._telegram_target_value, [target.label for target in self._telegram_targets], self._on_telegram_target_change, allow_blank=True)

        telegram_row = tk.Frame(strategy_card, bg=UiPalette.CARD)
        telegram_row.pack(fill="x", pady=(12, 0))
        self._telegram_toggle = tk.Checkbutton(
            telegram_row,
            textvariable=self._telegram_toggle_label,
            variable=self._telegram_enabled,
            command=self._on_telegram_toggle,
            indicatoron=False,
            relief="flat",
            bd=0,
            cursor="hand2",
            padx=18,
            pady=8,
            font=("Segoe UI", 9, "bold"),
            fg=UiPalette.BADGE_FG,
            selectcolor=UiPalette.SUCCESS,
            activeforeground=UiPalette.BADGE_FG,
            activebackground=UiPalette.SUCCESS,
        )
        self._telegram_toggle.pack(side="left")
        self._label(telegram_row, text="Telegram đang tắt tạm thời trong app. Chỉ xem signal nội bộ, chưa gửi ra chat.", font=("Segoe UI", 9), fg=UiPalette.MUTED).pack(side="left", padx=(12, 0))

        status = self._card(content, padding=16)
        status.pack(fill="x", pady=(0, 14))
        self._label(status, text="TRẠNG THÁI HỆ THỐNG", font=("Segoe UI", 9, "bold"), fg=UiPalette.MUTED).pack(anchor="w")
        status_grid = tk.Frame(status, bg=UiPalette.CARD)
        status_grid.pack(fill="x", pady=(10, 0))
        status_grid.grid_columnconfigure(1, weight=1)

        self._label(status_grid, text="MT5 STATUS", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 8))
        self._label(status_grid, textvariable=self._mt5_status_value, font=("Consolas", 10), fg=UiPalette.TEXT).grid(row=0, column=1, sticky="e", pady=(0, 8))

        self._label(status_grid, text="BOT STATUS", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).grid(row=1, column=0, sticky="nw", padx=(0, 12), pady=(0, 8))
        bot_status_wrap = tk.Frame(status_grid, bg=UiPalette.CARD)
        bot_status_wrap.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        self._bot_status_badge_widget = self._badge(bot_status_wrap, self._bot_status_value.get(), self._bot_status_badge_color, UiPalette.CARD)
        self._bot_status_badge_widget.pack(side="left")
        self._label(bot_status_wrap, textvariable=self._bot_status_detail_value, font=("Segoe UI", 9), fg=UiPalette.TEXT, bg=UiPalette.CARD, wraplength=760, justify="left", anchor="w").pack(side="left", padx=(10, 0), fill="x", expand=True)

        self._label(status_grid, text="LAST CHECK", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).grid(row=2, column=0, sticky="w", padx=(0, 12), pady=(0, 8))
        self._label(status_grid, textvariable=self._last_signal_check_value, font=("Consolas", 10), fg=UiPalette.TEXT).grid(row=2, column=1, sticky="w", pady=(0, 8))

        self._label(status_grid, text="LAST SIGNAL", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED).grid(row=3, column=0, sticky="nw", padx=(0, 12))
        signal_wrap = tk.Frame(status_grid, bg=UiPalette.CARD)
        signal_wrap.grid(row=3, column=1, sticky="ew")
        self._last_signal_badge_widget = self._badge(signal_wrap, self._last_signal_value.get(), self._last_signal_badge_color, UiPalette.CARD)
        self._last_signal_badge_widget.pack(side="left")
        self._label(signal_wrap, textvariable=self._last_signal_detail_value, font=("Segoe UI", 9), fg=UiPalette.TEXT, bg=UiPalette.CARD, wraplength=760, justify="left", anchor="w").pack(side="left", padx=(10, 0), fill="x", expand=True)

        body = tk.Frame(content, bg=UiPalette.APP)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=3, uniform="main")
        body.grid_columnconfigure(1, weight=2, uniform="main")
        body.grid_rowconfigure(0, weight=1)

        positions_card = self._card(body, padding=0)
        positions_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        section = tk.Frame(positions_card, bg=UiPalette.CARD, padx=18, pady=15)
        section.pack(fill="x")
        self._label(section, text="LỆNH ĐANG MỞ", font=("Segoe UI", 11, "bold")).pack(side="left")
        self._label(section, text="Tất cả vị thế XAUUSD • chỉ xem", font=("Segoe UI", 9), fg=UiPalette.MUTED).pack(side="right")
        columns = ("ticket", "source", "side", "volume", "entry", "sl", "tp", "profit")
        self.positions = ttk.Treeview(positions_card, columns=columns, show="headings", style="Monitor.Treeview", height=9)
        specs = (("ticket", 78, "TICKET"), ("source", 94, "NGUỒN"), ("side", 58, "LOẠI"), ("volume", 56, "LOT"), ("entry", 76, "ENTRY"), ("sl", 70, "SL"), ("tp", 70, "TP"), ("profit", 76, "P/L $"))
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
        self._label(log_table_header, text="GIỜ", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT, width=10, anchor="w").pack(side="left")
        self._label(log_table_header, text="TAG", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT, width=9, anchor="w").pack(side="left", padx=(8, 4))
        self._label(log_table_header, text="NỘI DUNG", font=("Segoe UI", 8, "bold"), fg=UiPalette.MUTED, bg=UiPalette.CARD_ALT, anchor="w").pack(side="left", fill="x", expand=True)

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
        refresh = tk.Button(footer, text="↻  LÀM MỚI NGAY", command=self._refresh, font=("Segoe UI", 10, "bold"), fg="#101722", bg=UiPalette.ACCENT, activeforeground="#101722", activebackground="#E8C270", relief="flat", bd=0, padx=16, pady=9, cursor="hand2")
        refresh.pack(side="left")
        self._label(footer, text="Không gửi lệnh MT5 • chỉ giám sát và gửi kèo Telegram khi operator bật", font=("Segoe UI", 9), fg=UiPalette.MUTED, bg=UiPalette.APP).pack(side="right")

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
        combo = ttk.Combobox(wrap, textvariable=variable, values=combo_values, state="readonly", font=("Segoe UI", 10))
        combo.pack(fill="x", pady=(6, 0), ipady=5)
        combo.bind("<<ComboboxSelected>>", handler)
        return combo

    def _toggle_theme(self) -> None:
        self._theme_mode = "light" if self._theme_mode == "dark" else "dark"
        UiPalette.apply_mode(self._theme_mode)
        self.configure(background=UiPalette.APP)
        self._configure_style()
        if self.shell is not None:
            self.shell.destroy()
        self._build_ui()
        self._update_telegram_controls()
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
            price_value_per_lot=100.0,
            risk_usd=self._safe_float(self._risk_value.get(), float(profile["risk_usd"])),
            reward_usd=self._safe_float(self._reward_value.get(), float(profile["reward_usd"])),
            max_spread=float(profile["max_spread"]),
            trade_side=str(profile["side"]),
        )
        return strategy_key, profile, config, prepare_frame, evaluate_signal

    @staticmethod
    def _summarize_bot_status(selected_strategy_key: str, runner_detection_available: bool, running_runners: tuple[RunnerView, ...]) -> tuple[str, str, str]:
        selected_label = STRATEGY_SELECTIONS[selected_strategy_key].label
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
        status_text, color, detail = self._summarize_bot_status(self._strategy_choice.get(), snapshot.runner_detection_available, snapshot.running_runners)
        self._set_bot_status_state(status_text, detail, color)
        self._update_run_button(snapshot)

    def _update_run_button(self, snapshot: MonitorSnapshot | None = None) -> None:
        if self._run_button is None:
            return
        snapshot = snapshot or self._latest_snapshot
        strategy_key = self._strategy_choice.get()
        running = False
        if snapshot is not None and snapshot.runner_detection_available:
            running = any(runner.strategy_key == strategy_key for runner in snapshot.running_runners)
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
                handle.write(f"\n[{self.clock():%Y-%m-%d %H:%M:%S}] Launch from monitor GUI\n")
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
        self.after(800, self._refresh)

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
        self._last_signal_check_value.set(f"{self.clock():%H:%M:%S}")
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
        row = prepared.iloc[-2]
        side = evaluate_signal(row, config)
        if side is None:
            self._set_last_signal_state("NONE", f"{STRATEGY_SELECTIONS[strategy_key].label}: chưa có signal mới ở nến đã đóng gần nhất.", UiPalette.INFO)
            return
        bar_time = int(row["time"])
        if self._last_signal_bar_by_strategy.get(strategy_key) == bar_time:
            return
        self._last_signal_bar_by_strategy[strategy_key] = bar_time
        request = self._build_signal_preview_request(side, config)
        if request is None:
            self._set_last_signal_state("BLOCKED", f"{STRATEGY_SELECTIONS[strategy_key].label}: có tín hiệu nhưng bị chặn bởi dữ liệu tick/spread hiện tại.", UiPalette.WARNING)
            return
        entry_time = datetime.fromtimestamp(bar_time, tz=UTC) + timedelta(minutes=5 if str(profile["timeframe"]) == "M5" else 15)
        badge = "LONG" if side == "long" else "SHORT"
        self._set_last_signal_state(badge, f"{STRATEGY_SELECTIONS[strategy_key].label} • Entry {request['price']:.2f} • SL {request['sl']:.2f} • TP {request['tp']:.2f} • ETA {entry_time.astimezone().strftime('%H:%M')}", UiPalette.SUCCESS if badge == "LONG" else UiPalette.DANGER)
        self._append_log(
            f"{STRATEGY_SELECTIONS[strategy_key].label} signal {side.upper()} • Entry {request['price']:.2f} • SL {request['sl']:.2f} • TP {request['tp']:.2f} • ETA {entry_time.astimezone().strftime('%H:%M')}",
            badge=badge,
        )
        self._maybe_send_signal_to_telegram(strategy_key, bar_time, side, request, str(profile["timeframe"]))

    def _maybe_send_signal_to_telegram(self, strategy_key: str, bar_time: int, side: str, request: dict, timeframe: str) -> None:
        if TELEGRAM_TEMP_DISABLED:
            return
        target = self._telegram_target_map.get(self._telegram_target_value.get())
        if target is None or not self._telegram_enabled.get():
            return
        signal_key = (strategy_key, bar_time, side, target.key)
        if signal_key == self._last_telegram_signal_key:
            return
        settings = TelegramSettings(
            enabled=True,
            bot_token=TelegramSettings.from_environment(enabled=True).bot_token,
            chat_id=target.chat_id,
            message_thread_id=target.message_thread_id,
        )
        notifier = TelegramNotifier(settings)
        message = format_signal_message(symbol=self._selected_symbol(), side=side, request=request, timeframe=timeframe)
        if notifier.send(message):
            self._last_telegram_signal_key = signal_key
            self._append_log(f"Đã gửi kèo {side.upper()} tới Telegram: {target.label}.", badge="INFO")
        else:
            self._append_log(f"Telegram chưa gửi được tới {target.label}: {notifier.last_status}", badge="INFO")

    def _refresh(self) -> None:
        if self._closed:
            return
        try:
            self._render_snapshot(self.monitor.refresh())
            self._evaluate_signal_preview()
        except Exception as exc:
            self._mt5_status_value.set(f"MT5 CHƯA SẴN SÀNG • {exc}")
            self._set_bot_status_state("ERROR", "Không cập nhật được trạng thái bot vì refresh MT5 lỗi.", UiPalette.DANGER)
            self._append_log(f"Lỗi kết nối: {exc}", badge="ERROR")
        finally:
            if not self._closed:
                self.after(self.refresh_ms, self._refresh)

    def _render_snapshot(self, snapshot: MonitorSnapshot, *, include_log_entries: bool = True) -> None:
        self._latest_snapshot = snapshot
        self._account_value.set(f"#{snapshot.login}")
        self._equity_value.set(f"{snapshot.equity:,.2f} {snapshot.currency}")
        self._position_count_value.set(str(len(snapshot.positions)))
        self._mt5_status_value.set(f"ONLINE • {snapshot.server} • Balance {snapshot.balance:,.2f} {snapshot.currency}")
        self._updated_value.set(self.clock().strftime("%H:%M:%S"))
        self._apply_bot_status(snapshot)
        self.positions.delete(*self.positions.get_children())
        for position in snapshot.positions:
            tag = "profit" if position.profit >= 0 else "loss"
            self.positions.insert("", "end", tags=(tag,), values=(position.ticket, position.source, position.side, f"{position.volume:.2f}", f"{position.price_open:.2f}", f"{position.stop_loss:.2f}", f"{position.take_profit:.2f}", f"{position.profit:+.2f}"))
        if include_log_entries:
            for entry in snapshot.log_entries:
                self._append_log(entry)

    @staticmethod
    def _classify_log_badge(message: str, explicit_badge: str | None = None) -> tuple[str, str]:
        badge = (explicit_badge or "").strip().upper()
        if badge in {"LONG", "SHORT", "ERROR", "INFO"}:
            return {
                "LONG": ("LONG", UiPalette.SUCCESS),
                "SHORT": ("SHORT", UiPalette.DANGER),
                "ERROR": ("ERROR", UiPalette.DANGER),
                "INFO": ("INFO", UiPalette.INFO),
            }[badge]
        upper = message.upper()
        if any(token in upper for token in (" SELL", "SHORT", " BÁN")):
            return "SHORT", UiPalette.DANGER
        if any(token in upper for token in (" BUY", "LONG", " MUA")):
            return "LONG", UiPalette.SUCCESS
        if any(token in upper for token in ("LỖI", "ERROR", "FAILED", "REJECTED")):
            return "ERROR", UiPalette.DANGER
        return "INFO", UiPalette.INFO

    def _append_log(self, message: str, *, badge: str | None = None) -> None:
        badge_text, badge_color = self._classify_log_badge(message, badge)
        entry = LogEntry(timestamp=f"{self.clock():%H:%M:%S}", badge=badge_text, message=message, badge_color=badge_color)
        self._log_history.insert(0, entry)
        self._log_history = self._log_history[:500]
        self._replay_logs()

    def _replay_logs(self) -> None:
        for child in self._log_rows.winfo_children():
            child.destroy()
        for index, entry in enumerate(self._log_history):
            row_bg = UiPalette.TABLE if index % 2 == 0 else UiPalette.TABLE_ALT
            row = tk.Frame(self._log_rows, bg=row_bg, padx=10, pady=6)
            row.pack(fill="x", padx=0, pady=(0, 6))
            self._label(row, text=entry.timestamp, font=("Consolas", 9), fg=UiPalette.MUTED, bg=row_bg, width=9, anchor="w").pack(side="left")
            self._badge(row, entry.badge, entry.badge_color, row_bg).pack(side="left", padx=(10, 8))
            self._label(row, text=entry.message, font=("Segoe UI", 9), bg=row_bg, justify="left", wraplength=410, anchor="w").pack(side="left", fill="x", expand=True)
        self._on_log_frame_configure(None)
        self._log_canvas.yview_moveto(0)

    def _badge(self, parent: tk.Misc, text: str, fill: str, bg: str) -> tk.Canvas:
        width = max(54, 16 + len(text) * 8)
        canvas = tk.Canvas(parent, width=width, height=24, bg=bg, highlightthickness=0, bd=0)
        radius = 10
        x1, y1, x2, y2 = 2, 2, width - 2, 22
        canvas.create_rectangle(x1 + radius, y1, x2 - radius, y2, fill=fill, outline=fill)
        canvas.create_rectangle(x1, y1 + radius, x2, y2 - radius, fill=fill, outline=fill)
        canvas.create_oval(x1, y1, x1 + radius * 2, y1 + radius * 2, fill=fill, outline=fill)
        canvas.create_oval(x2 - radius * 2, y1, x2, y1 + radius * 2, fill=fill, outline=fill)
        canvas.create_oval(x1, y2 - radius * 2, x1 + radius * 2, y2, fill=fill, outline=fill)
        canvas.create_oval(x2 - radius * 2, y2 - radius * 2, x2, y2, fill=fill, outline=fill)
        canvas.create_text(width / 2, 12, text=text, fill=UiPalette.BADGE_FG, font=("Segoe UI", 8, "bold"))
        return canvas

    def _on_log_frame_configure(self, _event) -> None:
        self._log_canvas.configure(scrollregion=self._log_canvas.bbox("all"))

    def _on_log_canvas_configure(self, event) -> None:
        self._log_canvas.itemconfigure(self._log_canvas_window, width=event.width)

    def _clear_logs(self) -> None:
        self._log_history.clear()
        self._replay_logs()

    def _on_strategy_selection(self, _event) -> None:
        selected_label = self._strategy_choice.get()
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

    def _on_telegram_target_change(self, _event) -> None:
        self._update_telegram_controls()
        if TELEGRAM_TEMP_DISABLED:
            self._append_log("Telegram đang bị tắt tạm thời trong app.", badge="INFO")
            return
        target = self._telegram_target_value.get().strip()
        if target:
            self._append_log(f"Đã chọn đích Telegram: {target}. Bật toggle nếu muốn gửi kèo khi có signal.", badge="INFO")

    def _on_telegram_toggle(self) -> None:
        if TELEGRAM_TEMP_DISABLED:
            self._telegram_enabled.set(False)
            self._update_telegram_controls()
            self._append_log("Telegram đang tắt tạm thời nên chưa thể bật gửi tin nhắn.", badge="INFO")
            return
        self._update_telegram_controls()
        if self._telegram_enabled.get():
            target = self._telegram_target_value.get().strip()
            self._append_log(f"Telegram ON • tín hiệu mới sẽ gửi tới {target}.", badge="INFO")
        else:
            self._append_log("Telegram OFF • app chỉ hiển thị log nội bộ.", badge="INFO")

    def _update_telegram_controls(self) -> None:
        if TELEGRAM_TEMP_DISABLED:
            self._telegram_enabled.set(False)
            self._telegram_toggle_label.set("TELEGRAM TẠM TẮT")
            self._telegram_toggle.configure(
                state="disabled",
                bg=UiPalette.BORDER,
                activebackground=UiPalette.BORDER,
                disabledforeground=UiPalette.MUTED,
            )
            return
        enabled = bool(self._telegram_target_value.get().strip())
        if not enabled:
            self._telegram_enabled.set(False)
        self._telegram_toggle_label.set("TELEGRAM ON" if self._telegram_enabled.get() else "TELEGRAM OFF")
        self._telegram_toggle.configure(
            state="normal" if enabled else "disabled",
            bg=UiPalette.SUCCESS if enabled else UiPalette.BORDER,
            activebackground=UiPalette.SUCCESS if enabled else UiPalette.BORDER,
            disabledforeground=UiPalette.MUTED,
        )

    def _on_close(self) -> None:
        self._closed = True
        self.monitor.stop()
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only GOLD Trader dashboard for RSIQUI V3 demo positions.")
    parser.add_argument("--config", default=str(CONFIG_ROOT / "ori_m5_demo.json"), help="RSIQUI V3 JSON read for display only")
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
