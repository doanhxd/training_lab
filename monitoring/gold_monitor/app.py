from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
import queue
import re
import threading
import tkinter as tk
from tkinter import ttk
from typing import Callable

from training_lab.monitoring.gold_monitor.adapter import AccountSnapshot, GoldPositionMonitor, HistoryDealView
from training_lab.monitoring.gold_monitor.mt5_accounts import Mt5TerminalCandidate, load_cached_candidates, open_remote_desktop, scan_mt5_terminals

APP_TITLE = "GOLD Monitor • Read-only"
GMT_PLUS_7 = timezone(timedelta(hours=7))
REFRESH_MILLISECONDS = 2_000


class Palette:
    APP = "#F5F7FB"
    SIDEBAR = "#111C2C"
    CARD = "#FFFFFF"
    CARD_ALT = "#EEF2F6"
    TABLE = "#FFFFFF"
    TABLE_ALT = "#F5F7FA"
    BORDER = "#DCE3EB"
    TEXT = "#142235"
    MUTED = "#687B91"
    NAV = "#F3F7FC"
    ACCENT = "#B7791F"
    SUCCESS = "#118A61"
    DANGER = "#D04454"
    INFO = "#2563EB"


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: str
    message: str


class GoldMonitorApp(tk.Tk):
    """Standalone read-only Local MT5 account monitor.

    The UI only calls the narrow GoldPositionMonitor and MT5 terminal scanner.
    It contains no trading strategy, signal evaluator, runner, credential, or
    trade-submit path.
    """

    def __init__(
        self,
        monitor: GoldPositionMonitor,
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
        self._latest_snapshot: AccountSnapshot | None = None
        self._selected_local_accounts: list[Mt5TerminalCandidate] = []
        self._selected_account_snapshots: list[tuple[Mt5TerminalCandidate, AccountSnapshot]] = []
        self._local_position_tickets: dict[str, set[int]] = {}
        self._card_keys: tuple[str, ...] = ()
        self._card_values: list[tuple[tk.Label, tk.Label, tk.Label, tk.Label, tk.Label, tk.Label]] = []
        self._log_history: list[LogEntry] = []
        self._history_window: tk.Toplevel | None = None
        self._history_table: ttk.Treeview | None = None
        self._history_results: queue.Queue = queue.Queue()
        self._history_loading = False
        self._history_request_id = 0
        self._history_pending = False
        self._history_filter_value = tk.StringVar(value="Hôm nay")
        self._history_symbol_value = tk.StringVar(value="Tất cả")
        today = self._clock_gmt7().date()
        self._history_start_date_value = tk.StringVar(value=(today - timedelta(days=30)).isoformat())
        self._history_end_date_value = tk.StringVar(value=today.isoformat())
        self._history_start_time_value = tk.StringVar(value="00:00")
        self._history_end_time_value = tk.StringVar(value="23:59")
        self._history_status_value = tk.StringVar(value="Sẵn sàng quét lịch sử read-only.")
        self._history_range_value = tk.StringVar(value="—")
        self._history_deals_value = tk.StringVar(value="0")
        self._history_winrate_value = tk.StringVar(value="0.0%")
        self._history_dd_value = tk.StringVar(value="0.00 USD")
        self._history_net_value = tk.StringVar(value="0.00 USD")
        self._history_volume_value = tk.StringVar(value="0.00")
        self._account_cards_host: tk.Frame | None = None
        self._show_broker_currency = False
        self._total_equity_value = tk.StringVar(value="—")
        self._position_day_value = tk.StringVar(value=today.strftime("%d/%m"))
        self._updated_value = tk.StringVar(value="CHƯA CẬP NHẬT")

        self.title(APP_TITLE)
        self.geometry("1320x820")
        self.minsize(1100, 680)
        self.configure(bg=Palette.APP)
        self._configure_style()
        self._build_ui()
        self._center_window(self)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._append_log("GOLD Monitor khởi động. Chỉ đọc dữ liệu MT5.", "INFO")
        self._schedule_refresh(0)

    def _configure_style(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Gold.Treeview", background=Palette.TABLE, fieldbackground=Palette.TABLE, foreground=Palette.TEXT, borderwidth=0, rowheight=32, font=("Segoe UI", 9))
        style.map("Gold.Treeview", background=[("selected", "#E6EEF8")], foreground=[("selected", Palette.TEXT)])
        style.configure("Gold.Treeview.Heading", background=Palette.SIDEBAR, foreground=Palette.NAV, borderwidth=0, relief="flat", font=("Segoe UI", 9, "bold"), padding=(10, 8))
        style.configure("Local.Treeview", rowheight=38, font=("Segoe UI Symbol", 11))
        style.configure("Local.Treeview.Heading", font=("Segoe UI", 9, "bold"), padding=(12, 9))
        style.configure("Gold.TCombobox", fieldbackground=Palette.TABLE, background=Palette.CARD_ALT, foreground=Palette.TEXT, arrowcolor=Palette.ACCENT, bordercolor=Palette.BORDER, padding=(10, 6), font=("Segoe UI", 10))
        style.map("Gold.TCombobox", bordercolor=[("focus", Palette.ACCENT), ("active", Palette.ACCENT)])

    def _label(self, parent: tk.Misc, text: str = "", *, font: tuple = ("Segoe UI", 10), fg: str | None = None, bg: str | None = None, **kwargs) -> tk.Label:
        return tk.Label(parent, text=text, font=font, fg=fg or Palette.TEXT, bg=bg or Palette.CARD, bd=0, highlightthickness=0, **kwargs)

    def _card(self, parent: tk.Misc, *, padding: int = 18, bg: str | None = None) -> tk.Frame:
        card = tk.Frame(parent, bg=bg or Palette.CARD, highlightbackground=Palette.BORDER, highlightthickness=1, bd=0)
        card.configure(padx=padding, pady=padding)
        return card

    @staticmethod
    def _center_window(window: tk.Toplevel) -> None:
        window.update_idletasks()
        x = max(0, (window.winfo_screenwidth() - window.winfo_width()) // 2)
        y = max(0, (window.winfo_screenheight() - window.winfo_height()) // 2)
        window.geometry(f"{window.winfo_width()}x{window.winfo_height()}+{x}+{y}")

    @staticmethod
    def _display_symbol(symbol: str) -> str:
        upper = str(symbol or "").upper()
        if upper.startswith("XAUUSD"):
            return "XAUUSD"
        if upper.startswith("BTCUSD"):
            return "BTCUSD"
        return upper

    @staticmethod
    def _display_currency(currency: str, *, preserve_broker_currency: bool = False) -> str:
        value = str(currency or "USD").upper()
        return value if preserve_broker_currency else ("USD" if value == "USC" else value)

    @staticmethod
    def _display_terminal_path(path: Path | str) -> str:
        text = str(path)
        for root in ("C:\\Program Files (x86)\\", "C:\\Program Files\\"):
            if text.casefold().startswith(root.casefold()):
                return text[len(root):]
        return text

    @staticmethod
    def _format_time(value: datetime | None) -> str:
        if value is None:
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(GMT_PLUS_7).strftime("%H:%M:%S")

    @staticmethod
    def _format_datetime(value: datetime | None) -> str:
        if value is None:
            return "--"
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(GMT_PLUS_7).strftime("%Y-%m-%d %H:%M:%S")

    def _clock_gmt7(self) -> datetime:
        now = self.clock()
        return now.replace(tzinfo=GMT_PLUS_7) if now.tzinfo is None else now.astimezone(GMT_PLUS_7)

    def _toggle_currency_alias(self) -> None:
        """Toggle the display-only USC→USD alias without changing broker data."""
        self._show_broker_currency = not self._show_broker_currency
        self._schedule_refresh(0)
        if self._history_table is not None:
            if self._history_loading:
                self._history_pending = True
            else:
                self._refresh_history()

    def _format_total_equity(self, snapshots: list[AccountSnapshot]) -> str:
        totals: dict[str, float] = {}
        for snapshot in snapshots:
            currency = str(snapshot.currency or "USD").upper()
            totals[currency] = totals.get(currency, 0.0) + float(snapshot.equity)
        if not totals:
            return "—"
        parts = []
        for raw_currency, equity in totals.items():
            displayed = self._display_currency(raw_currency, preserve_broker_currency=self._show_broker_currency)
            parts.append(f"{equity:,.2f} {displayed}")
        return " • ".join(parts)

    def _format_history_money(self, amount: float, broker_currency: str, *, signed: bool = False) -> str:
        raw_currency = str(broker_currency or "USD").upper()
        displayed_currency = self._display_currency(raw_currency, preserve_broker_currency=self._show_broker_currency)
        value = f"{float(amount):+,.2f}" if signed else f"{float(amount):,.2f}"
        if self._show_broker_currency and raw_currency == "USC":
            return f"{value} USC (${float(amount) / 100:,.3f})"
        return f"{value} {displayed_currency}"

    def _build_ui(self) -> None:
        shell = tk.Frame(self, bg=Palette.APP)
        shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg=Palette.SIDEBAR, width=230, padx=20, pady=24)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        self._label(sidebar, text="GOLD\nMONITOR", font=("Segoe UI", 17, "bold"), fg=Palette.NAV, bg=Palette.SIDEBAR, justify="left").pack(anchor="w")
        self._label(sidebar, text="LOCAL MT5 • READ-ONLY", font=("Segoe UI", 8, "bold"), fg="#B7C5D8", bg=Palette.SIDEBAR).pack(anchor="w", pady=(6, 24))
        for text, command in (("▣  TÀI KHOẢN LOCAL", self._open_local_accounts_window), ("◷  LỊCH SỬ LỆNH", self._open_history_window), ("▤  VPS / RDP", self._open_vps_window)):
            tk.Button(sidebar, text=text, command=command, font=("Segoe UI", 10, "bold"), fg=Palette.NAV, bg=Palette.SIDEBAR, activeforeground=Palette.NAV, activebackground="#1C2B3F", relief="flat", bd=0, padx=12, pady=11, anchor="w", cursor="hand2").pack(fill="x", pady=(8, 0))
        self._label(sidebar, text="Không gửi lệnh\nKhông lưu password\nKhông gọi đăng nhập MT5", font=("Segoe UI", 8), fg="#B7C5D8", bg=Palette.SIDEBAR, justify="left").pack(side="bottom", anchor="w")

        content = tk.Frame(shell, bg=Palette.APP, padx=24, pady=22)
        content.pack(side="left", fill="both", expand=True)
        header = tk.Frame(content, bg=Palette.APP)
        header.pack(fill="x", pady=(0, 18))
        self._label(header, text="GOLD MONITOR", font=("Segoe UI", 20, "bold"), bg=Palette.APP).pack(side="left")
        self._label(header, text="Theo dõi Local MT5 account, vị thế và lịch sử đóng lệnh.", font=("Segoe UI", 10), fg=Palette.MUTED, bg=Palette.APP).pack(side="left", padx=(16, 0), pady=(7, 0))
        header_actions = tk.Frame(header, bg=Palette.APP); header_actions.pack(side="right")
        total_card = self._card(header_actions, padding=0, bg=Palette.CARD_ALT); total_card.configure(padx=12, pady=6); total_card.pack(side="left", padx=(0, 10))
        self._label(total_card, text="TỔNG VỐN", font=("Segoe UI", 8, "bold"), fg=Palette.MUTED, bg=Palette.CARD_ALT).pack(anchor="w")
        self._label(total_card, textvariable=self._total_equity_value, font=("Segoe UI", 12, "bold"), fg=Palette.SUCCESS, bg=Palette.CARD_ALT).pack(anchor="w", pady=(2, 0))
        tk.Button(header_actions, textvariable=self._updated_value, command=self._toggle_currency_alias, font=("Consolas", 9, "bold"), fg=Palette.ACCENT, bg=Palette.CARD_ALT, activeforeground=Palette.ACCENT, activebackground=Palette.BORDER, relief="flat", bd=0, padx=12, pady=9, cursor="hand2").pack(side="left")
        self._account_cards_host = tk.Frame(content, bg=Palette.APP)
        self._account_cards_host.pack(fill="x", pady=(0, 14))

        body = tk.Frame(content, bg=Palette.APP)
        body.pack(fill="both", expand=True)
        body.grid_columnconfigure(0, weight=6)
        body.grid_columnconfigure(1, weight=4)
        body.grid_rowconfigure(0, weight=1)
        positions_card = self._card(body, padding=0)
        positions_card.grid(row=0, column=0, sticky="nsew", padx=(0, 7))
        section = tk.Frame(positions_card, bg=Palette.CARD, padx=18, pady=15)
        section.pack(fill="x")
        self._label(section, text="LỆNH ĐANG MỞ", font=("Segoe UI", 11, "bold")).pack(side="left")
        self._label(section, textvariable=self._position_day_value, font=("Segoe UI", 9, "bold"), fg=Palette.MUTED).pack(side="left", padx=(10, 0), pady=(1, 0))
        columns = ("time", "account", "symbol", "side", "volume", "entry", "sl", "tp", "profit")
        self.positions = ttk.Treeview(positions_card, columns=columns, show="headings", style="Gold.Treeview", height=12)
        for key, width, title in (("time", 70, "TIME"), ("account", 96, "ACCOUNT"), ("symbol", 64, "SYMBOL"), ("side", 52, "TYPE"), ("volume", 44, "LOT"), ("entry", 76, "ENTRY"), ("sl", 70, "SL"), ("tp", 70, "TP"), ("profit", 74, "PnL")):
            self.positions.heading(key, text=title); self.positions.column(key, width=width, anchor="center", stretch=True)
        self.positions.tag_configure("profit", foreground=Palette.SUCCESS); self.positions.tag_configure("loss", foreground=Palette.DANGER)
        self.positions.pack(fill="both", expand=True, padx=1, pady=(0, 1))

        log_card = self._card(body, padding=0)
        log_card.grid(row=0, column=1, sticky="nsew", padx=(7, 0))
        log_header = tk.Frame(log_card, bg=Palette.CARD, padx=18, pady=12)
        log_header.pack(fill="x")
        self._label(log_header, text="NHẬT KÝ GIÁM SÁT", font=("Segoe UI", 11, "bold")).pack(side="left")
        tk.Button(log_header, text="XÓA", command=self._clear_logs, font=("Segoe UI", 8, "bold"), fg=Palette.TEXT, bg=Palette.CARD_ALT, activeforeground=Palette.TEXT, activebackground=Palette.BORDER, relief="flat", bd=0, padx=10, pady=4).pack(side="right")
        self._log_host = tk.Frame(log_card, bg=Palette.TABLE)
        self._log_host.pack(fill="both", expand=True, padx=1, pady=(0, 1))
        footer = tk.Frame(content, bg=Palette.APP)
        footer.pack(fill="x", pady=(14, 0))
        tk.Button(footer, text="↻  LÀM MỚI NGAY", command=lambda: self._schedule_refresh(0), font=("Segoe UI", 10, "bold"), fg="#101722", bg=Palette.ACCENT, activeforeground="#101722", activebackground="#E8C270", relief="flat", bd=0, padx=16, pady=9, cursor="hand2").pack(side="left")
        self._label(footer, text="Read-only observer • MT5 account đã đăng nhập sẵn", font=("Segoe UI", 9), fg=Palette.MUTED, bg=Palette.APP).pack(side="right")

    def _render_cards(self) -> None:
        host = self._account_cards_host
        if host is None:
            return
        rows = list(self._selected_account_snapshots)
        if not rows and self._latest_snapshot is not None:
            rows = [(Mt5TerminalCandidate(path=Path(f"current:{self._latest_snapshot.login}"), source="Current"), self._latest_snapshot)]
        self._total_equity_value.set(self._format_total_equity([snapshot for _candidate, snapshot in rows]))
        keys = tuple(str(candidate.path).casefold() for candidate, _ in rows)
        if keys != self._card_keys:
            for child in host.winfo_children(): child.destroy()
            self._card_keys, self._card_values = keys, []
            for _candidate, _snapshot in rows:
                row = tk.Frame(host, bg=Palette.APP); row.pack(fill="x", pady=(0, 3))
                for column in range(3): row.grid_columnconfigure(column, weight=1, uniform="account")
                fields: list[tk.Label] = []
                for column, caption in enumerate(("TÀI KHOẢN MT5", "EQUITY", "LỆNH XAU/BTC")):
                    card = self._card(row, padding=0); card.configure(padx=10, pady=7); card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 5))
                    self._label(card, text=caption, font=("Segoe UI", 8, "bold"), fg=Palette.MUTED).pack(anchor="w")
                    if column == 0:
                        account_line = tk.Frame(card, bg=Palette.CARD); account_line.pack(anchor="w")
                        value = self._label(account_line, font=("Segoe UI", 14, "bold"), fg=Palette.TEXT)
                        detail = self._label(account_line, text="", font=("Segoe UI", 8), fg=Palette.MUTED)
                        value.pack(side="left"); detail.pack(side="left", padx=(8, 0), pady=(2, 0))
                    else:
                        value = self._label(card, font=("Segoe UI", 12 if column == 2 else 14, "bold"), fg=(Palette.TEXT, Palette.SUCCESS, Palette.ACCENT)[column])
                        detail = self._label(card, text="", font=("Segoe UI", 8), fg=Palette.MUTED)
                        value.pack(anchor="w")
                    fields.extend((value, detail))
                self._card_values.append(tuple(fields))
        for index, (candidate, snapshot) in enumerate(rows):
            account, account_detail, equity, _equity_detail, positions, _positions_detail = self._card_values[index]
            account.configure(text=f"#{snapshot.login}"); account_detail.configure(text=f"ONLINE • {snapshot.server}")
            equity.configure(text=f"{snapshot.equity:,.2f} {self._display_currency(snapshot.currency, preserve_broker_currency=self._show_broker_currency)}")
            positions.configure(text=f"{len(snapshot.positions)} ({candidate.name or 'Read-only'})")

    def _render_positions(self, records: list[tuple[str, object]]) -> None:
        self.positions.delete(*self.positions.get_children())
        for account, position in sorted(records, key=lambda item: getattr(item[1], "opened_at", None) or datetime.min.replace(tzinfo=UTC), reverse=True):
            profit = float(getattr(position, "profit", 0.0) or 0.0)
            self.positions.insert("", "end", tags=("profit" if profit >= 0 else "loss",), values=(self._format_time(getattr(position, "opened_at", None)), f"#{account}", self._display_symbol(str(getattr(position, "symbol", ""))), getattr(position, "side", ""), f"{float(getattr(position, 'volume', 0.0) or 0.0):.2f}", f"{float(getattr(position, 'price_open', 0.0) or 0.0):.2f}", f"{float(getattr(position, 'stop_loss', 0.0) or 0.0):.2f}", f"{float(getattr(position, 'take_profit', 0.0) or 0.0):.2f}", f"{profit:+.2f}"))

    def _append_log(self, message: str, level: str = "INFO") -> None:
        self._log_history.insert(0, LogEntry(self._clock_gmt7().strftime("%H:%M:%S"), level, message))
        self._log_history = self._log_history[:250]
        if not hasattr(self, "_log_host"): return
        for child in self._log_host.winfo_children(): child.destroy()
        colors = {"INFO": Palette.INFO, "ERROR": Palette.DANGER, "OPEN": Palette.SUCCESS, "CLOSE": Palette.MUTED}
        for index, entry in enumerate(self._log_history):
            bg = Palette.TABLE if index % 2 == 0 else Palette.TABLE_ALT
            row = tk.Frame(self._log_host, bg=bg, padx=12, pady=8); row.pack(fill="x", pady=(0, 1))
            self._label(row, text=entry.timestamp, font=("Consolas", 9), fg=Palette.MUTED, bg=bg, width=9, anchor="w").pack(side="left")
            self._label(row, text=entry.level, font=("Segoe UI", 8, "bold"), fg=colors.get(entry.level, Palette.INFO), bg=bg, width=7, anchor="w").pack(side="left")
            self._label(row, text=entry.message, font=("Segoe UI", 9), bg=bg, anchor="w", justify="left", wraplength=370).pack(side="left", fill="x", expand=True)

    def _append_local_position_changes(self, snapshots: list[tuple[Mt5TerminalCandidate, AccountSnapshot]]) -> None:
        """Append only genuine Local position transitions, not a fresh-probe replay."""
        active_paths = {str(candidate.path).casefold() for candidate, _snapshot in snapshots}
        self._local_position_tickets = {path: tickets for path, tickets in self._local_position_tickets.items() if path in active_paths}
        for candidate, snapshot in snapshots:
            path_key = str(candidate.path).casefold()
            current = {int(getattr(position, "ticket", 0) or 0) for position in snapshot.positions}
            previous = self._local_position_tickets.get(path_key)
            if previous is not None:
                for ticket in sorted(current - previous):
                    self._append_log(f"MỞ position #{ticket} • #{snapshot.login}", "OPEN")
                for ticket in sorted(previous - current):
                    self._append_log(f"ĐÓNG position #{ticket} • #{snapshot.login}", "CLOSE")
            self._local_position_tickets[path_key] = current

    def _clear_logs(self) -> None:
        self._log_history.clear(); self._append_log("Đã xóa nhật ký hiển thị.", "INFO")

    def _schedule_refresh(self, delay_ms: int | None = None) -> None:
        if self._closed: return
        if self._refresh_after_id is not None:
            try: self.after_cancel(self._refresh_after_id)
            except tk.TclError: pass
        self._refresh_after_id = self.after(self.refresh_ms if delay_ms is None else delay_ms, self._refresh)

    def _read_local(self, candidate: Mt5TerminalCandidate, *, history_range: tuple[datetime, datetime] | None = None) -> tuple[AccountSnapshot | None, tuple[HistoryDealView, ...], int]:
        import MetaTrader5 as mt5
        mt5.shutdown()
        if not mt5.initialize(path=str(candidate.path)):
            raise RuntimeError(f"{candidate.login or candidate.path.name}: {mt5.last_error()}")
        probe = GoldPositionMonitor(mt5=mt5); probe._connected = True
        try:
            if history_range is not None:
                deals, stats = probe.history(*history_range)
                return None, deals, stats.raw_deals
            return probe.refresh(), (), 0
        finally:
            probe.stop()

    def _refresh(self) -> None:
        self._refresh_after_id = None
        if self._closed or self._refresh_in_progress or self._history_loading: return
        self._refresh_in_progress = True
        try:
            if self._selected_local_accounts:
                snapshots: list[tuple[Mt5TerminalCandidate, AccountSnapshot]] = []
                for candidate in self._selected_local_accounts:
                    try: snapshots.append((candidate, self._read_local(candidate)[0]))
                    except Exception as exc: self._append_log(f"Không đọc được {candidate.login or candidate.path.name}: {exc}", "ERROR")
                self._selected_account_snapshots = [(candidate, snapshot) for candidate, snapshot in snapshots if snapshot is not None]
                if not self._selected_account_snapshots: raise RuntimeError("Không đọc được account MT5 Local đã chọn.")
                primary = self._selected_account_snapshots[0][1]
            else:
                self._selected_account_snapshots = []
                primary = self.monitor.refresh()
            self._latest_snapshot = primary
            now = self._clock_gmt7()
            self._position_day_value.set(now.strftime("%d/%m")); self._updated_value.set(f"CẬP NHẬT\n{now:%H:%M:%S}")
            self._render_cards()
            records = [(str(snapshot.login), position) for _candidate, snapshot in (self._selected_account_snapshots or [(Mt5TerminalCandidate(Path("current"), "Current"), primary)]) for position in snapshot.positions]
            self._render_positions(records)
            if self._selected_local_accounts:
                self._append_local_position_changes(self._selected_account_snapshots)
            else:
                for entry in primary.log_entries: self._append_log(entry, "OPEN" if entry.startswith("MỞ") else "CLOSE")
        except Exception as exc:
            self._append_log(f"Không cập nhật được MT5: {exc}", "ERROR")
        finally:
            self._refresh_in_progress = False; self._schedule_refresh()

    def _open_local_accounts_window(self) -> None:
        window = tk.Toplevel(self); window.title("MT5 Local — chọn account read-only"); window.geometry("1180x570"); window.minsize(1040, 520); window.configure(bg=Palette.APP)
        shell = tk.Frame(window, bg=Palette.APP, padx=20, pady=18); shell.pack(fill="both", expand=True)
        self._label(shell, text="MT5 LOCAL ACCOUNTS", font=("Segoe UI", 15, "bold"), bg=Palette.APP).pack(anchor="w")
        self._label(shell, text="Quét terminal Local và đọc account_info() tuần tự — không gửi lệnh.", fg=Palette.MUTED, bg=Palette.APP).pack(anchor="w", pady=(4, 12))
        tree = ttk.Treeview(shell, columns=("selected", "account", "name", "server", "path"), show="headings", style="Local.Treeview", height=8)
        for key, title, width in (("selected", "THEO DÕI", 108), ("account", "ACCOUNT", 118), ("name", "ACCOUNT NAME", 210), ("server", "SERVER", 220), ("path", "TERMINAL", 390)):
            tree.heading(key, text=title); tree.column(key, width=width, anchor="w")
        tree.pack(fill="both", expand=True); status = tk.StringVar(value="Chưa quét")
        self._label(shell, textvariable=status, fg=Palette.MUTED, bg=Palette.APP).pack(anchor="w", pady=(8, 6))
        actions = tk.Frame(shell, bg=Palette.APP); actions.pack(fill="x")
        candidates_by_iid: dict[str, Mt5TerminalCandidate] = {}; selected_paths = {str(item.path).casefold() for item in self._selected_local_accounts}
        def render(candidates: tuple[Mt5TerminalCandidate, ...], message: str) -> None:
            tree.delete(*tree.get_children()); candidates_by_iid.clear()
            for index, candidate in enumerate(candidates):
                iid = str(index); candidates_by_iid[iid] = candidate; checked = "☑" if str(candidate.path).casefold() in selected_paths else "☐"
                tree.insert("", "end", iid=iid, values=(checked, candidate.login or "—", candidate.name or "—", candidate.server or "—", self._display_terminal_path(candidate.path)))
            status.set(message)
        def toggle(event):
            row = tree.identify_row(event.y)
            if not row or tree.identify_column(event.x) != "#1": return None
            candidate = candidates_by_iid[row]; key = str(candidate.path).casefold()
            if key in selected_paths: selected_paths.remove(key)
            else: selected_paths.add(key)
            values = list(tree.item(row, "values")); values[0] = "☑" if key in selected_paths else "☐"; tree.item(row, values=values)
            return "break"
        tree.bind("<Button-1>", toggle)
        def scan() -> None:
            status.set("Đang quét MT5 Local…")
            def worker():
                try:
                    import MetaTrader5 as mt5
                    candidates, error = scan_mt5_terminals(mt5), None
                except Exception as exc: candidates, error = (), exc
                window.after(0, lambda: render(candidates, f"Tìm thấy {len(candidates)} terminal. Tick account cần theo dõi read-only." if error is None else f"Không quét được MT5: {error}"))
            threading.Thread(target=worker, name="gold-monitor-scan", daemon=True).start()
        def apply():
            selected = [candidate for candidate in candidates_by_iid.values() if str(candidate.path).casefold() in selected_paths]
            if not selected: status.set("Tick ít nhất một account để theo dõi."); return
            self._selected_local_accounts = selected; self._selected_account_snapshots = []; self._local_position_tickets = {}; status.set(f"Đã áp dụng {len(selected)} account read-only.")
            self._append_log(f"Đang theo dõi {len(selected)} MT5 Local account.", "INFO"); self._schedule_refresh(0); window.destroy()
        tk.Button(actions, text="QUÉT LẠI", command=scan, bg=Palette.INFO, fg="#FFFFFF", activebackground="#1D4ED8", activeforeground="#FFFFFF", relief="flat", bd=0, padx=14, pady=7, cursor="hand2").pack(side="left")
        tk.Button(actions, text="ÁP DỤNG THEO DÕI", command=apply, bg=Palette.SUCCESS, fg="#FFFFFF", activebackground="#0F6D4D", activeforeground="#FFFFFF", relief="flat", bd=0, padx=14, pady=7, cursor="hand2").pack(side="left", padx=8)
        cached = load_cached_candidates()
        if cached: render(cached, f"Hiển thị {len(cached)} account cache; đang cập nhật live…")
        scan(); self._center_window(window)

    def _history_range(self) -> tuple[datetime, datetime]:
        today = self._clock_gmt7().date(); mode = self._history_filter_value.get().lower()
        days = {"hôm nay": 1, "7 ngày qua": 7, "30 ngày qua": 30, "90 ngày qua": 90, "1 năm qua": 365}.get(mode)
        if days:
            start_date, end_date = today - timedelta(days=days - 1), today
        else:
            try: start_date, end_date = date.fromisoformat(self._history_start_date_value.get()), date.fromisoformat(self._history_end_date_value.get())
            except ValueError: start_date, end_date = today - timedelta(days=29), today
        return datetime.combine(min(start_date, end_date), time.min), datetime.combine(max(start_date, end_date) + timedelta(days=1), time.min)

    def _open_history_window(self) -> None:
        if self._history_window and self._history_window.winfo_exists(): self._history_window.lift(); return
        window = tk.Toplevel(self); self._history_window = window; window.title("Lịch sử lệnh"); window.geometry("1180x720"); window.minsize(980, 620); window.configure(bg=Palette.APP); window.protocol("WM_DELETE_WINDOW", self._close_history)
        shell = tk.Frame(window, bg=Palette.APP, padx=22, pady=18); shell.pack(fill="both", expand=True)
        header = tk.Frame(shell, bg=Palette.APP); header.pack(fill="x", pady=(0, 14)); self._label(header, text="LỊCH SỬ LỆNH", font=("Segoe UI", 18, "bold"), bg=Palette.APP).pack(side="left"); self._label(header, textvariable=self._history_status_value, font=("Segoe UI", 9), fg=Palette.MUTED, bg=Palette.APP).pack(side="left", padx=(14, 0), pady=(6, 0))
        controls = self._card(shell, padding=14); controls.pack(fill="x", pady=(0, 12))
        for column in range(6): controls.grid_columnconfigure(column, weight=1)
        self._combo(controls, 0, "KHOẢNG THỜI GIAN", self._history_filter_value, ["Hôm nay", "7 ngày qua", "30 ngày qua", "90 ngày qua", "1 năm qua", "Tùy chỉnh"])
        self._combo(controls, 1, "MÃ GIAO DỊCH", self._history_symbol_value, ["Tất cả", "XAUUSD", "BTCUSD"])
        self._entry(controls, 2, "START DATE", self._history_start_date_value); self._entry(controls, 3, "END DATE", self._history_end_date_value)
        self._entry(controls, 4, "START TIME", self._history_start_time_value); self._entry(controls, 5, "END TIME", self._history_end_time_value)
        tk.Button(controls, text="LỌC / QUÉT", command=self._refresh_history, font=("Segoe UI", 9, "bold"), fg="#101722", bg=Palette.ACCENT, relief="flat", bd=0, padx=18, pady=9).grid(row=1, column=5, sticky="e", padx=6, pady=(6, 0))
        stats = tk.Frame(shell, bg=Palette.APP); stats.pack(fill="x", pady=(0, 12))
        for column, weight in enumerate((3, 3, 5, 5, 4)): stats.grid_columnconfigure(column, weight=weight)
        for column, caption, value, color in ((0, "DEALS", self._history_deals_value, Palette.ACCENT), (1, "WINRATE", self._history_winrate_value, Palette.SUCCESS), (2, "MAX DD", self._history_dd_value, Palette.DANGER), (3, "NET P/L", self._history_net_value, Palette.SUCCESS), (4, "FOLLOW TREND RATIO", self._history_volume_value, Palette.ACCENT)):
            card = self._card(stats, padding=12); card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 5)); self._label(card, text=caption, font=("Segoe UI", 8, "bold"), fg=Palette.MUTED).pack(anchor="w"); self._label(card, textvariable=value, font=("Segoe UI", 14, "bold"), fg=color).pack(anchor="w", pady=(5, 0))
        history_card = self._card(shell, padding=0); history_card.pack(fill="both", expand=True)
        columns = ("time", "account", "symbol", "side", "volume", "price", "net")
        self._history_table = ttk.Treeview(history_card, columns=columns, show="headings", style="Gold.Treeview", height=12)
        for key, width, title in (("time", 154, "TIME"), ("account", 102, "ACCOUNT"), ("symbol", 108, "SYMBOL"), ("side", 64, "SIDE"), ("volume", 72, "LOT"), ("price", 92, "PRICE"), ("net", 92, "NET $")):
            self._history_table.heading(key, text=title); self._history_table.column(key, width=width, anchor="center", stretch=True)
        self._history_table.tag_configure("profit", foreground=Palette.SUCCESS); self._history_table.tag_configure("loss", foreground=Palette.DANGER); self._history_table.pack(fill="both", expand=True, padx=1, pady=1)
        self._refresh_history(); self._center_window(window)

    def _combo(self, parent: tk.Misc, column: int, caption: str, variable: tk.StringVar, values: list[str]) -> None:
        wrap = tk.Frame(parent, bg=Palette.CARD); wrap.grid(row=0, column=column, sticky="ew", padx=6, pady=6); self._label(wrap, text=caption, font=("Segoe UI", 8, "bold"), fg=Palette.MUTED).pack(anchor="w"); ttk.Combobox(wrap, textvariable=variable, values=values, state="readonly", style="Gold.TCombobox").pack(fill="x", pady=(6, 0), ipady=5)

    def _entry(self, parent: tk.Misc, column: int, caption: str, variable: tk.StringVar) -> None:
        wrap = tk.Frame(parent, bg=Palette.CARD); wrap.grid(row=0, column=column, sticky="ew", padx=6, pady=6); self._label(wrap, text=caption, font=("Segoe UI", 8, "bold"), fg=Palette.MUTED).pack(anchor="w"); tk.Entry(wrap, textvariable=variable, font=("Segoe UI", 10), fg=Palette.TEXT, bg=Palette.TABLE, relief="flat", bd=0, insertbackground=Palette.TEXT).pack(fill="x", pady=(6, 0), ipady=8)

    def _close_history(self) -> None:
        self._history_request_id += 1; self._history_loading = False
        if self._history_window: self._history_window.destroy()
        self._history_window, self._history_table = None, None

    def _history_time_range(self) -> tuple[time, time]:
        try:
            start = time.fromisoformat(self._history_start_time_value.get().strip())
            end = time.fromisoformat(self._history_end_time_value.get().strip())
        except ValueError:
            start, end = time(0, 0), time(23, 59)
            self._history_start_time_value.set("00:00")
            self._history_end_time_value.set("23:59")
        return start.replace(second=0, microsecond=0), end.replace(second=59, microsecond=999999)

    def _refresh_history(self) -> None:
        if self._history_table is None: return
        if self._history_loading: self._history_pending = True; return
        start, end = self._history_range(); start_time, end_time = self._history_time_range(); self._history_table.delete(*self._history_table.get_children()); self._history_loading = True; self._history_request_id += 1; request_id = self._history_request_id
        self._history_range_value.set(f"{start:%Y-%m-%d} → {(end - timedelta(seconds=1)):%Y-%m-%d} • {start_time:%H:%M}–{end_time:%H:%M}"); candidates = tuple(self._selected_local_accounts); self._history_status_value.set(f"Đang quét lịch sử read-only • {len(candidates) or 1} MT5 account…")
        def worker():
            try:
                if candidates:
                    records: list[tuple[str, HistoryDealView]] = []; raw = 0
                    for candidate in candidates:
                        _snapshot, deals, count = self._read_local(candidate, history_range=(start, end)); records.extend((candidate.login or "CURRENT", deal) for deal in deals); raw += count
                else:
                    deals, stats = self.monitor.history(start, end); records, raw = [(str(self._latest_snapshot.login) if self._latest_snapshot else "CURRENT", deal) for deal in deals], stats.raw_deals
                self._history_results.put((request_id, sorted(records, key=lambda item: item[1].time, reverse=True), raw, None))
            except Exception as exc: self._history_results.put((request_id, [], 0, exc))
        threading.Thread(target=worker, name="gold-monitor-history", daemon=True).start(); self.after(40, self._poll_history)

    def _poll_history(self) -> None:
        if not self._history_loading: return
        try: request_id, records, raw, error = self._history_results.get_nowait()
        except queue.Empty: self.after(40, self._poll_history); return
        if request_id != self._history_request_id: self.after(0, self._poll_history); return
        self._history_loading = False
        if self._history_table is None: return
        if error is not None:
            self._history_table.insert("", "end", values=("LỖI", "", "", "", "", "", str(error)), tags=("loss",)); self._history_status_value.set("Không quét được lịch sử MT5.")
        else: self._render_history(records, raw); self._history_status_value.set(f"Đã quét {len(records)} deal đóng • read-only history_deals_get.")
        if self._history_pending: self._history_pending = False; self._refresh_history()

    def _render_history(self, records: list[tuple[str, HistoryDealView]], raw: int) -> None:
        selected = self._history_symbol_value.get().strip().upper()
        if selected != "TẤT CẢ": records = [(account, deal) for account, deal in records if str(deal.symbol).upper().startswith(selected)]
        start_time, end_time = self._history_time_range()
        records = [(account, deal) for account, deal in records if start_time <= deal.time.replace(tzinfo=None).time() <= end_time]
        deals = tuple(deal for _, deal in records); stats = GoldPositionMonitor.history_stats(deals, raw_deals=raw); broker_currency = self._latest_snapshot.currency if self._latest_snapshot else "USD"
        self._history_deals_value.set(str(stats.deals)); self._history_winrate_value.set(f"{stats.winrate:.1f}%"); self._history_dd_value.set(self._format_history_money(stats.max_daily_drawdown, broker_currency)); self._history_net_value.set(self._format_history_money(stats.net_profit, broker_currency, signed=True)); self._history_volume_value.set(f"{sum(float(deal.volume) for deal in deals):,.2f}")
        if not deals: self._history_table.insert("", "end", values=("KHÔNG CÓ DỮ LIỆU", "", "", "", "", "", "Không có deal BUY/SELL đã đóng trong khoảng đã chọn.")); return
        for account, deal in records:
            self._history_table.insert("", "end", tags=("profit" if deal.net_profit >= 0 else "loss",), values=(self._format_datetime(deal.time), f"#{account}", self._display_symbol(deal.symbol), deal.side, f"{deal.volume:.2f}", f"{deal.price:.2f}", f"{deal.net_profit:+.2f}"))

    def _open_vps_window(self) -> None:
        window = tk.Toplevel(self); window.title("VPS / RDP"); window.geometry("720x420"); window.minsize(640, 380); window.configure(bg=Palette.APP)
        shell = tk.Frame(window, bg=Palette.APP, padx=28, pady=26); shell.pack(fill="both", expand=True)
        self._label(shell, text="VPS / REMOTE DESKTOP", font=("Segoe UI", 17, "bold"), bg=Palette.APP).pack(anchor="w")
        self._label(shell, text="Kết nối Windows Remote Desktop tới VPS đã được cấp quyền.", fg=Palette.MUTED, bg=Palette.APP).pack(anchor="w", pady=(5, 18))
        host, username, result = tk.StringVar(), tk.StringVar(), tk.StringVar(value="Password được nhập trong cửa sổ Windows native; GOLD Monitor không lưu password.")
        form = self._card(shell, padding=18); form.pack(fill="x"); form.grid_columnconfigure(0, weight=1); form.grid_columnconfigure(1, weight=1)
        self._rdp_entry(form, 0, "IP / HOSTNAME VPS", host); self._rdp_entry(form, 1, "USERNAME", username)
        notice = tk.Frame(shell, bg=Palette.CARD_ALT, padx=14, pady=12, highlightbackground=Palette.BORDER, highlightthickness=1); notice.pack(fill="x", pady=(14, 0)); self._label(notice, text="THÔNG TIN BẢO MẬT", font=("Segoe UI", 8, "bold"), fg=Palette.ACCENT, bg=Palette.CARD_ALT).pack(anchor="w"); self._label(notice, textvariable=result, font=("Segoe UI", 9), fg=Palette.MUTED, bg=Palette.CARD_ALT, wraplength=620, justify="left").pack(anchor="w", pady=(4, 0))
        def connect():
            try: open_remote_desktop(host.get(), username.get()); result.set("Đã mở Windows Remote Desktop. Nhập password trong cửa sổ RDP native.")
            except Exception as exc: result.set(f"Không mở được RDP: {exc}")
        tk.Button(shell, text="MỞ REMOTE DESKTOP  →", command=connect, font=("Segoe UI", 10, "bold"), fg="#FFFFFF", bg=Palette.INFO, activeforeground="#FFFFFF", activebackground="#1D4ED8", relief="flat", bd=0, padx=18, pady=10, cursor="hand2").pack(anchor="w", pady=(18, 0)); self._center_window(window)

    def _rdp_entry(self, parent: tk.Misc, column: int, caption: str, variable: tk.StringVar) -> None:
        wrap = tk.Frame(parent, bg=Palette.CARD)
        wrap.grid(row=0, column=column, sticky="ew", padx=6, pady=6)
        self._label(wrap, text=caption, font=("Segoe UI", 8, "bold"), fg=Palette.MUTED).pack(anchor="w")
        field = tk.Frame(wrap, bg=Palette.INFO, padx=1, pady=1)
        field.pack(fill="x", pady=(6, 0))
        tk.Entry(field, textvariable=variable, font=("Segoe UI", 10, "bold"), fg=Palette.TEXT, bg="#E8EEF7", relief="flat", bd=0, highlightthickness=0, insertbackground=Palette.TEXT).pack(fill="x", padx=10, pady=1, ipady=8)

    def _on_close(self) -> None:
        self._closed = True
        if self._refresh_after_id:
            try: self.after_cancel(self._refresh_after_id)
            except tk.TclError: pass
        self.monitor.stop(); self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone read-only GOLD Monitor for Local MT5 terminals.")
    parser.add_argument("--refresh-seconds", type=float, default=2.0, help="Read-only snapshot interval")
    args = parser.parse_args()
    import MetaTrader5 as mt5
    GoldMonitorApp(GoldPositionMonitor(mt5=mt5), refresh_ms=max(500, int(args.refresh_seconds * 1000))).mainloop()


if __name__ == "__main__":
    main()
