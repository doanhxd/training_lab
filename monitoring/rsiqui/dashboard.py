"""DASHBOARD - read-only MT5 finance dashboard.

This is intentionally separate from monitor_gui.py. It does not expose strategy
controls or send/modify/close orders; it only reads account, positions, and history.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
import tkinter as tk
from tkinter import ttk
from typing import Iterable

from training_lab.monitoring.rsiqui.position_monitor import HistoryDealView, RsiquiV3PositionMonitor


APP_TITLE = "DASHBOARD"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_ROOT = PROJECT_ROOT / "configs" / "strategies" / "rsiqui"
GMT_PLUS_7 = timedelta(hours=7)


class Colors:
    BG = "#F4F6F8"
    SIDEBAR = "#172233"
    SIDEBAR_2 = "#1E2C40"
    CARD = "#FFFFFF"
    CARD_ALT = "#F8FAFC"
    BORDER = "#E4E9EF"
    TEXT = "#172235"
    MUTED = "#7C8999"
    BLUE = "#4D8EDB"
    BLUE_LIGHT = "#D9EAFE"
    GREEN = "#19A875"
    GREEN_LIGHT = "#DDF5EB"
    RED = "#DF6671"
    RED_LIGHT = "#FBE4E7"
    GOLD = "#C8932E"
    PURPLE = "#9372D8"
    NAV = "#B9C6D7"


def money(value: float, digits: int = 2) -> str:
    value = float(value)
    return f"{'-' if value < 0 else ''}${abs(value):,.{digits}f}"


def compact_lot(value: float) -> str:
    return f"{float(value):g}"


def display_symbol(symbol: str) -> str:
    """Show broker symbol families without suffixes in the dashboard."""
    upper = str(symbol or "").upper()
    if upper.startswith("XAUUSD"):
        return "XAU"
    if upper.startswith("BTCUSD"):
        return "BTC"
    return upper


RANGE_OPTIONS = ("1h", "1d", "1w", "14d", "30d", "60d", "90d", "180d", "1y")


def range_delta(value: str) -> timedelta:
    if value == "1h":
        return timedelta(hours=1)
    if value == "1d":
        return timedelta(days=1)
    if value == "1w":
        return timedelta(days=7)
    if value == "1y":
        return timedelta(days=365)
    return timedelta(days=int(value[:-1]))


def flow_series(deals: Iterable[HistoryDealView], range_value: str) -> dict[str, float]:
    result: dict[str, float] = defaultdict(float)
    for deal in deals:
        fmt = "%H:%M" if range_value == "1h" else "%d/%m"
        result[deal.time.strftime(fmt)] += float(deal.net_profit)
    return dict(result)


class DashboardApp(tk.Tk):
    def __init__(self, monitor: RsiquiV3PositionMonitor, *, refresh_ms: int = 5000) -> None:
        super().__init__()
        self.monitor = monitor
        self.refresh_ms = refresh_ms
        self.closed = False
        self.raw_currency = "USC"
        self.currency_alias = False
        self.snapshot = None
        self.deals: tuple[HistoryDealView, ...] = ()
        self.chart_range = tk.StringVar(value="14d")
        self.current_page = "overview"
        self.nav_buttons: dict[str, tk.Button] = {}
        self.transaction_table = None
        self._vars: dict[str, tk.StringVar] = {}
        self._build_window()
        self.after(100, self.refresh_dashboard)

    def _build_window(self) -> None:
        self.title(APP_TITLE)
        self.geometry("1380x860")
        self.minsize(1120, 720)
        self.configure(bg=Colors.BG)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._build_style()

        root = tk.Frame(self, bg=Colors.BG)
        root.pack(fill="both", expand=True)
        self.sidebar = tk.Frame(root, bg=Colors.SIDEBAR, width=190, padx=20, pady=22)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self._label(self.sidebar, "◉  S31", 16, Colors.CARD, bold=True, bg=Colors.SIDEBAR).pack(anchor="w")
        self._label(self.sidebar, "Finance dashboard", 8, Colors.NAV, bg=Colors.SIDEBAR).pack(anchor="w", pady=(5, 28))
        self._nav("▣  Overview", page="overview", active=True, command=self.show_overview)
        self._nav("▤  Transactions", page="transactions", command=self.show_transactions)
        self._nav("▣  Payment")
        self._nav("▣  Card")
        self._nav("▥  Insights")
        self._nav("⚙  Settings")
        tk.Frame(self.sidebar, bg=Colors.SIDEBAR).pack(fill="both", expand=True)
        self._nav("↪  Logout")

        self.content = tk.Frame(root, bg=Colors.BG, padx=28, pady=20)
        self.content.pack(side="left", fill="both", expand=True)
        self._build_header()
        self._build_summary_cards()
        self._build_main_grid()

    def _reset_content(self) -> None:
        self.content.destroy()
        self.content = tk.Frame(self.sidebar.master, bg=Colors.BG, padx=28, pady=20)
        self.content.pack(side="left", fill="both", expand=True)
        self.history_table = None
        self.flow_canvas = None
        self.expense_canvas = None

    def show_overview(self) -> None:
        self.current_page = "overview"
        self._set_active_nav("overview")
        self._reset_content()
        self._build_header()
        self._build_summary_cards()
        self._build_main_grid()
        self.render_data()

    def show_transactions(self) -> None:
        self.current_page = "transactions"
        self._set_active_nav("transactions")
        self._reset_content()
        self._build_header()
        self._build_transactions_page()
        self.render_transactions()

    def _build_transactions_page(self) -> None:
        summary = tk.Frame(self.content, bg=Colors.BG)
        summary.pack(fill="x", pady=(0, 16))
        for col in range(4):
            summary.grid_columnconfigure(col, weight=1, uniform="transactions")
        self._metric_card(summary, 0, "CURRENT BALANCE", "tx_balance", Colors.GREEN, "$")
        self._metric_card(summary, 1, "TOTAL DEPOSIT", "tx_deposit", Colors.BLUE, "↗")
        self._metric_card(summary, 2, "TOTAL WITHDRAWAL", "tx_withdrawal", Colors.RED, "↘")
        self._metric_card(summary, 3, "NET MOVEMENT", "tx_net", Colors.PURPLE, "▣")

        card = self._card(self.content, padx=16, pady=14)
        card.pack(fill="both", expand=True)
        self._panel_title(card, "Account Transactions", "Deposits & withdrawals")
        columns = ("time", "type", "amount", "comment")
        self.transaction_table = ttk.Treeview(card, columns=columns, show="headings", style="Dashboard.Treeview", height=16)
        specs = (("time", 170, "TIME"), ("type", 150, "TYPE"), ("amount", 150, "AMOUNT"), ("comment", 420, "REFERENCE"))
        for key, width, title in specs:
            self.transaction_table.heading(key, text=title)
            self.transaction_table.column(key, width=width, anchor="center" if key != "comment" else "w", stretch=True)
        self.transaction_table.tag_configure("deposit", foreground=Colors.GREEN)
        self.transaction_table.tag_configure("withdrawal", foreground=Colors.RED)
        self.transaction_table.pack(fill="both", expand=True, pady=(10, 0))

    def _account_transactions(self) -> tuple[tuple[datetime, str, float, str], ...]:
        end = datetime.now(tz=UTC) + timedelta(seconds=1)
        start = end - timedelta(days=3650)
        raw = self.monitor.mt5.history_deals_get(start, end) or ()
        balance_type = int(getattr(self.monitor.mt5, "DEAL_TYPE_BALANCE", 2))
        credit_type = int(getattr(self.monitor.mt5, "DEAL_TYPE_CREDIT", 3))
        rows = []
        for deal in raw:
            if int(getattr(deal, "type", -1)) not in {balance_type, credit_type}:
                continue
            timestamp = int(getattr(deal, "time", 0) or 0)
            if not timestamp:
                continue
            amount = float(getattr(deal, "profit", 0.0) or 0.0)
            kind = "DEPOSIT" if amount >= 0 else "WITHDRAWAL"
            stamp = datetime.fromtimestamp(timestamp, tz=UTC) + GMT_PLUS_7
            rows.append((stamp, kind, amount, str(getattr(deal, "comment", "") or "Account transaction")))
        return tuple(sorted(rows, key=lambda row: row[0], reverse=True))

    def render_transactions(self) -> None:
        if self.transaction_table is None:
            return
        rows = self._account_transactions()
        currency = self.display_currency()
        deposits = sum(amount for _, kind, amount, _ in rows if kind == "DEPOSIT")
        withdrawals = sum(amount for _, kind, amount, _ in rows if kind == "WITHDRAWAL")
        self._vars["tx_balance"].set(f"{self.snapshot.balance:,.2f} {currency}" if self.snapshot else "—")
        self._vars["tx_deposit"].set(f"{deposits:,.2f} {currency}")
        self._vars["tx_withdrawal"].set(f"{withdrawals:,.2f} {currency}")
        self._vars["tx_net"].set(f"{deposits + withdrawals:+,.2f} {currency}")
        self.transaction_table.delete(*self.transaction_table.get_children())
        if not rows:
            self.transaction_table.insert("", "end", values=("—", "NO DATA", "—", "No deposit/withdrawal records returned by MT5"))
            return
        for stamp, kind, amount, comment in rows:
            self.transaction_table.insert("", "end", tags=("deposit" if kind == "DEPOSIT" else "withdrawal",), values=(
                stamp.strftime("%Y-%m-%d %H:%M:%S"), kind, f"{amount:+,.2f} {currency}", comment,
            ))

    def _build_style(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Dashboard.Treeview", background=Colors.CARD, fieldbackground=Colors.CARD,
                        foreground=Colors.TEXT, rowheight=31, borderwidth=0, font=("Segoe UI", 9))
        style.configure("Dashboard.Treeview.Heading", background=Colors.CARD_ALT,
                        foreground=Colors.MUTED, relief="flat", borderwidth=0,
                        font=("Segoe UI", 8, "bold"))
        style.map("Dashboard.Treeview", background=[("selected", Colors.BLUE_LIGHT)], foreground=[("selected", Colors.TEXT)])

    def _var(self, name: str, value: str = "—") -> tk.StringVar:
        self._vars[name] = tk.StringVar(value=value)
        return self._vars[name]

    def _label(self, parent, text="", size=10, fg=None, *, bold=False, bg=None, **kwargs):
        return tk.Label(parent, text=text, font=("Segoe UI", size, "bold" if bold else "normal"),
                        fg=fg or Colors.TEXT, bg=bg or Colors.CARD, bd=0, **kwargs)

    def _card(self, parent, *, bg=None, padx=16, pady=14):
        frame = tk.Frame(parent, bg=bg or Colors.CARD, highlightbackground=Colors.BORDER,
                         highlightthickness=1, bd=0, padx=padx, pady=pady)
        return frame

    def _set_active_nav(self, page: str) -> None:
        for key, button in self.nav_buttons.items():
            active = key == page
            button.configure(
                bg=Colors.CARD if active else Colors.SIDEBAR,
                fg=Colors.TEXT if active else Colors.NAV,
            )

    def _nav(self, text: str, *, page: str | None = None, active: bool = False, command=None) -> None:
        bg = Colors.CARD if active else Colors.SIDEBAR
        fg = Colors.TEXT if active else Colors.NAV
        button = tk.Button(self.sidebar, text=text, anchor="w", relief="flat", bd=0,
                           bg=bg, fg=fg, activebackground=Colors.SIDEBAR_2,
                           activeforeground=Colors.CARD, font=("Segoe UI", 9, "bold"),
                           padx=12, pady=9, cursor="hand2", command=command)
        button.pack(fill="x", pady=(0, 5))
        if page is not None:
            self.nav_buttons[page] = button

    def _build_header(self) -> None:
        header = tk.Frame(self.content, bg=Colors.BG)
        header.pack(fill="x", pady=(0, 16))
        left = tk.Frame(header, bg=Colors.BG)
        left.pack(side="left")
        self._label(left, "Dashboard", 18, Colors.TEXT, bold=True, bg=Colors.BG).pack(anchor="w")
        self._label(left, "Your financial overview", 9, Colors.MUTED, bg=Colors.BG).pack(anchor="w", pady=(3, 0))
        actions = tk.Frame(header, bg=Colors.BG)
        actions.pack(side="right")
        self._alias_button = tk.Button(actions, text="UX", command=self.toggle_currency,
                                       bg=Colors.CARD, fg=Colors.TEXT, activebackground=Colors.BLUE_LIGHT,
                                       relief="flat", bd=0, padx=13, pady=7, cursor="hand2",
                                       font=("Segoe UI", 9, "bold"))
        self._alias_button.pack(side="left", padx=(0, 8))
        self._label(actions, "◌", 17, Colors.MUTED, bg=Colors.BG).pack(side="left", padx=8)
        self._label(actions, "●", 18, Colors.BLUE, bg=Colors.BG).pack(side="left", padx=8)

    def _metric_card(self, parent, column: int, title: str, key: str, color: str, icon: str) -> None:
        card = self._card(parent, padx=14, pady=13)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 5, 5))
        top = tk.Frame(card, bg=Colors.CARD)
        top.pack(fill="x")
        self._label(top, icon, 11, color, bold=True).pack(side="left")
        self._label(top, title, 8, Colors.MUTED, bold=True).pack(side="left", padx=7)
        self._label(card, size=16, fg=Colors.TEXT, textvariable=self._var(key), bold=True).pack(anchor="w", pady=(10, 0))

    def _build_summary_cards(self) -> None:
        summary = tk.Frame(self.content, bg=Colors.BG)
        summary.pack(fill="x", pady=(0, 16))
        for col in range(4):
            summary.grid_columnconfigure(col, weight=1, uniform="summary")
        self._metric_card(summary, 0, "BALANCE", "balance", Colors.GREEN, "$")
        self._metric_card(summary, 1, "INCOME (NET P/L)", "income", Colors.BLUE, "↗")
        self._metric_card(summary, 2, "EXPENSES (DD)", "expenses", Colors.RED, "↘")
        self._metric_card(summary, 3, "SAVINGS", "savings", Colors.PURPLE, "▣")

    def _build_main_grid(self) -> None:
        grid = tk.Frame(self.content, bg=Colors.BG)
        grid.pack(fill="both", expand=True)
        grid.grid_columnconfigure(0, weight=3)
        grid.grid_columnconfigure(1, weight=2)
        grid.grid_rowconfigure(0, weight=3)
        grid.grid_rowconfigure(1, weight=2)

        flow = self._card(grid, padx=16, pady=14)
        flow.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 10))
        flow_header = tk.Frame(flow, bg=Colors.CARD)
        flow_header.pack(fill="x")
        self._label(flow_header, "Money Flow", 11, Colors.TEXT, bold=True).pack(side="left")
        self.range_menu = ttk.Combobox(flow_header, textvariable=self.chart_range,
                                       values=RANGE_OPTIONS, state="readonly", width=7,
                                       font=("Segoe UI", 8))
        self.range_menu.pack(side="right")
        self.range_menu.bind("<<ComboboxSelected>>", self._on_range_change)
        self.flow_canvas = tk.Canvas(flow, bg=Colors.CARD, highlightthickness=0, height=250)
        self.flow_canvas.pack(fill="both", expand=True, pady=(8, 0))

        right = tk.Frame(grid, bg=Colors.BG)
        right.grid(row=0, column=1, sticky="nsew", pady=(0, 10))
        savings = self._card(right, padx=16, pady=14)
        savings.pack(fill="both", expand=True)
        self._panel_title(savings, "My Savings", "View all")
        self._goal_rows = []
        for title in ("Trading reserve", "Emergency fund", "New equipment", "Top-up goal"):
            row = tk.Frame(savings, bg=Colors.CARD)
            row.pack(fill="x", pady=(12, 0))
            self._label(row, title, 9, Colors.TEXT, bold=True).pack(side="left")
            self._label(row, size=8, fg=Colors.MUTED, textvariable=self._var("goal_" + title)).pack(side="right")
            bar = tk.Frame(savings, bg=Colors.BORDER, height=5)
            bar.pack(fill="x", pady=(5, 0))
            fill = tk.Frame(bar, bg=Colors.BLUE, height=5, width=80)
            fill.pack(side="left", fill="y")
            self._goal_rows.append(fill)

        expenses = self._card(grid, padx=16, pady=14)
        expenses.grid(row=1, column=0, sticky="nsew", padx=(0, 8))
        self._panel_title(expenses, "All Expenses", "Last 14 days")
        self.expense_canvas = tk.Canvas(expenses, bg=Colors.CARD, highlightthickness=0, height=120)
        self.expense_canvas.pack(fill="both", expand=True)

        history = self._card(grid, padx=16, pady=14)
        history.grid(row=1, column=1, sticky="nsew")
        self._panel_title(history, "History Transactions", "Live")
        columns = ("time", "symbol", "side", "volume", "net")
        self.history_table = ttk.Treeview(history, columns=columns, show="headings", style="Dashboard.Treeview", height=5)
        specs = (("time", 72, "TIME"), ("symbol", 60, "SYMBOL"), ("side", 55, "SIDE"), ("volume", 55, "LOT"), ("net", 80, "NET"))
        for key, width, title in specs:
            self.history_table.heading(key, text=title)
            self.history_table.column(key, width=width, anchor="center", stretch=True)
        self.history_table.tag_configure("profit", foreground=Colors.GREEN)
        self.history_table.tag_configure("loss", foreground=Colors.RED)
        self.history_table.pack(fill="both", expand=True, pady=(7, 0))

    def _panel_title(self, parent, title: str, trailing: str) -> None:
        row = tk.Frame(parent, bg=Colors.CARD)
        row.pack(fill="x")
        self._label(row, title, 11, Colors.TEXT, bold=True).pack(side="left")
        self._label(row, trailing, 8, Colors.MUTED).pack(side="right")

    def display_currency(self) -> str:
        return "USD" if self.currency_alias and self.raw_currency.upper() == "USC" else self.raw_currency

    def display_money(self, value: float, digits: int = 2) -> str:
        currency = self.display_currency()
        return f"{'-' if value < 0 else ''}${abs(float(value)):,.{digits}f} {currency}"

    def toggle_currency(self) -> None:
        self.currency_alias = not self.currency_alias
        self._alias_button.configure(text="UX" if self.currency_alias else "UI")
        if self.current_page == "transactions":
            self.render_transactions()
        else:
            self.render_data()

    def refresh_dashboard(self) -> None:
        if self.closed:
            return
        try:
            self.snapshot = self.monitor.refresh()
            self.raw_currency = str(self.snapshot.currency or "USC").upper()
            self._alias_button.configure(text="UX" if self.currency_alias else "UI")
            start = datetime.now(tz=UTC) - range_delta(self.chart_range.get())
            end = datetime.now(tz=UTC) + timedelta(seconds=1)
            self.deals, _ = self.monitor.history(start, end)
            if self.current_page == "transactions":
                self.render_transactions()
            else:
                self.render_data()
        except Exception as exc:
            self._vars["balance"].set("OFFLINE")
            self._vars["income"].set(str(exc)[:22])
        finally:
            if not self.closed:
                self.after(self.refresh_ms, self.refresh_dashboard)

    def render_data(self) -> None:
        if self.snapshot is None:
            return
        currency = self.display_currency()
        positive = sum(max(0.0, deal.net_profit) for deal in self.deals)
        negative = sum(min(0.0, deal.net_profit) for deal in self.deals)
        net_profit = sum(float(deal.net_profit) for deal in self.deals)
        savings = max(0.0, float(self.snapshot.equity) - net_profit)
        self._vars["balance"].set(f"{self.snapshot.balance:,.2f} {currency}")
        self._vars["income"].set(f"{net_profit:+,.2f} {currency}")
        self._vars["expenses"].set(f"{negative:,.2f} {currency}")
        self._vars["savings"].set(f"{savings:,.2f} {currency}")
        self._vars["goal_Trading reserve"].set(self.display_money(self.snapshot.equity))
        self._vars["goal_Emergency fund"].set(self.display_money(max(0.0, self.snapshot.balance * 0.1)))
        self._vars["goal_New equipment"].set(self.display_money(sum(deal.volume for deal in self.deals)))
        self._vars["goal_Top-up goal"].set(f"{len(self.snapshot.positions)} open")
        self.draw_flow()
        self.draw_expenses(positive, negative)
        self.history_table.delete(*self.history_table.get_children())
        for deal in self.deals[:8]:
            tag = "profit" if deal.net_profit >= 0 else "loss"
            self.history_table.insert("", "end", tags=(tag,), values=(
                deal.time.strftime("%d/%m %H:%M"), display_symbol(deal.symbol),
                deal.side, compact_lot(deal.volume), money(deal.net_profit),
            ))

    def draw_flow(self) -> None:
        canvas = self.flow_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 500)
        height = max(canvas.winfo_height(), 220)
        canvas.create_text(12, 8, anchor="nw", text="NET CASH FLOW", fill=Colors.MUTED, font=("Segoe UI", 8, "bold"))
        flows = flow_series(self.deals, self.chart_range.get())
        labels = list(flows.keys())[-14:]
        values = [flows[label] for label in labels]
        if not values:
            labels, values = ["—"], [0.0]
        max_abs = max(max(abs(value) for value in values), 1.0)
        left, right, top, bottom = 42, width - 16, 28, height - 28
        for i in range(4):
            y = top + (bottom - top) * i / 3
            canvas.create_line(left, y, right, y, fill=Colors.BORDER)
        points = []
        for i, value in enumerate(values):
            x = left + (right - left) * (i / max(1, len(values) - 1))
            y = (top + bottom) / 2 - (value / max_abs) * ((bottom - top) / 2 - 8)
            points.append((x, y))
            canvas.create_oval(x - 3, y - 3, x + 3, y + 3, fill=Colors.BLUE, outline=Colors.BLUE)
            if i == 0 or i == len(values) - 1:
                canvas.create_text(x, bottom + 8, text=labels[i], fill=Colors.MUTED, font=("Segoe UI", 8))
        if len(points) > 1:
            canvas.create_line(*[coord for point in points for coord in point], fill=Colors.BLUE, width=3, smooth=True)
        canvas.create_line(left, (top + bottom) / 2, right, (top + bottom) / 2, fill=Colors.BORDER, dash=(3, 4))

    def _on_range_change(self, _event=None) -> None:
        self.refresh_dashboard()

    def draw_expenses(self, income: float, expenses: float) -> None:
        canvas = self.expense_canvas
        canvas.delete("all")
        width = max(canvas.winfo_width(), 400)
        height = max(canvas.winfo_height(), 110)
        total = max(income + abs(expenses), 1.0)
        cx, cy, radius = 78, height / 2, 42
        start = 90
        for value, color in ((income, Colors.BLUE), (abs(expenses), Colors.RED)):
            extent = value / total * 360
            canvas.create_arc(cx - radius, cy - radius, cx + radius, cy + radius, start=start, extent=-extent, style="arc", outline=color, width=16)
            start -= extent
        canvas.create_text(cx, cy, text=f"{len(self.deals)}\ndeals", fill=Colors.TEXT, font=("Segoe UI", 9, "bold"))
        self._legend(canvas, 160, cy - 17, Colors.BLUE, "Income", self.display_money(income))
        self._legend(canvas, 160, cy + 17, Colors.RED, "Expense", self.display_money(expenses))

    def _legend(self, canvas, x, y, color, label, value) -> None:
        """Keep labels and currency values in separate responsive columns."""
        value_right = max(x + 185, canvas.winfo_width() - 24)
        canvas.create_oval(x, y - 4, x + 8, y + 4, fill=color, outline=color)
        canvas.create_text(x + 16, y, anchor="w", text=label, fill=Colors.MUTED, font=("Segoe UI", 8))
        canvas.create_text(value_right, y, anchor="e", text=value, fill=Colors.TEXT, font=("Segoe UI", 9, "bold"))

    def close(self) -> None:
        self.closed = True
        self.monitor.stop()
        self.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="DASHBOARD read-only MT5 finance overview")
    parser.add_argument("--symbol", default="XAUUSD")
    parser.add_argument("--refresh-seconds", type=float, default=5.0)
    args = parser.parse_args()
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        monitor = RsiquiV3PositionMonitor(symbol=args.symbol, mt5=mt5)
        DashboardApp(monitor, refresh_ms=max(1000, int(args.refresh_seconds * 1000))).mainloop()
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
