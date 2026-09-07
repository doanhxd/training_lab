from __future__ import annotations

from datetime import datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from training_lab.monitoring.gold_monitor.adapter import AccountSnapshot, GoldPositionMonitor, HistoryDealView
from training_lab.monitoring.gold_monitor.app import GoldMonitorApp
from training_lab.monitoring.gold_monitor.mt5_accounts import open_remote_desktop


class FakeMt5:
    POSITION_TYPE_BUY = 0
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_INOUT = 2

    def initialize(self, **_kwargs): return True
    def shutdown(self): pass
    def last_error(self): return (0, "ok")
    def account_info(self): return SimpleNamespace(login=99, server="Demo", balance=500.0, equity=510.0, currency="USD")
    def positions_get(self): return (SimpleNamespace(ticket=1, time=0, symbol="XAUUSDm", type=0, volume=0.01, price_open=2300.0, sl=2290.0, tp=2320.0, profit=10.0, comment="Manual", magic=0),)
    def history_deals_get(self, _start, _end): return ()
    def symbol_info_tick(self, _symbol): return None


class GoldMonitorTests(TestCase):
    def test_adapter_projects_current_account_without_strategy_or_runner(self):
        snapshot = GoldPositionMonitor(mt5=FakeMt5()).refresh()
        self.assertEqual(99, snapshot.login)
        self.assertEqual("XAUUSDm", snapshot.positions[0].symbol)
        self.assertEqual("BUY", snapshot.positions[0].side)
        self.assertEqual("Manual", snapshot.positions[0].source)

    def test_history_stats_uses_closed_net_profit_and_daily_drawdown(self):
        deals = (
            HistoryDealView(datetime(2026, 1, 1, 8, tzinfo=timezone.utc), "XAUUSD", "BUY", 0.01, 1.0, 10.0, 0.0, 0.0, 10.0, ""),
            HistoryDealView(datetime(2026, 1, 1, 9, tzinfo=timezone.utc), "XAUUSD", "SELL", 0.01, 1.0, -4.0, 0.0, 0.0, -4.0, ""),
        )
        stats = GoldPositionMonitor.history_stats(deals, raw_deals=2)
        self.assertEqual(2, stats.deals)
        self.assertEqual(50.0, stats.winrate)
        self.assertEqual(6.0, stats.net_profit)
        self.assertEqual(4.0, stats.max_daily_drawdown)

    def test_standalone_gui_has_no_rsiqui_runner_signal_telegram_or_trade_apis(self):
        source = Path("monitoring/gold_monitor/app.py").read_text(encoding="utf-8").lower()
        for forbidden in (
            "from training_lab.monitoring.rsiqui",
            "from training_lab.runners",
            "telegramnotifier",
            "order_send",
            "mt5.login",
            "subprocess.popen",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("class goldmonitorapp", source)
        self.assertIn("áp dụng theo dõi", source)
        self.assertIn("read-only", source)

    def test_display_helpers_preserve_account_path_and_broker_aliases(self):
        self.assertEqual("MetaTrader 5\\terminal64.exe", GoldMonitorApp._display_terminal_path(r"C:\Program Files\MetaTrader 5\terminal64.exe"))
        self.assertEqual("XAUUSD", GoldMonitorApp._display_symbol("XAUUSDm"))
        self.assertEqual("USD", GoldMonitorApp._display_currency("USC"))
        self.assertEqual("USC", GoldMonitorApp._display_currency("USC", preserve_broker_currency=True))

    def test_refresh_keeps_unicode_label_outside_locale_sensitive_strftime_format(self):
        source = Path("monitoring/gold_monitor/app.py").read_text(encoding="utf-8")
        self.assertNotIn('strftime("CẬP NHẬT', source)
        self.assertIn('self._updated_value.set(f"CẬP NHẬT\\n{now:%H:%M:%S}")', source)

    def test_update_button_toggles_usc_alias_without_trading_side_effects(self):
        source = Path("monitoring/gold_monitor/app.py").read_text(encoding="utf-8")
        self.assertIn("def _toggle_currency_alias(self)", source)
        self.assertIn("self._show_broker_currency = not self._show_broker_currency", source)
        self.assertIn("command=self._toggle_currency_alias", source)
        self.assertIn("preserve_broker_currency=self._show_broker_currency", source)
        app = object.__new__(GoldMonitorApp)
        app._show_broker_currency, app._history_table = False, None
        calls = []
        app._schedule_refresh = lambda delay: calls.append(delay)
        app._toggle_currency_alias()
        self.assertTrue(app._show_broker_currency)
        app._toggle_currency_alias()
        self.assertFalse(app._show_broker_currency)
        self.assertEqual([0, 0], calls)

    def test_total_equity_sums_selected_accounts_per_currency(self):
        app = object.__new__(GoldMonitorApp)
        accounts = [
            AccountSnapshot(1, "A", 0.0, 500.0, "USC", (), ()),
            AccountSnapshot(2, "B", 0.0, 250.0, "USC", (), ()),
            AccountSnapshot(3, "C", 0.0, 20.0, "USD", (), ()),
        ]
        app._show_broker_currency = False
        self.assertEqual("750.00 USD • 20.00 USD", app._format_total_equity(accounts))
        app._show_broker_currency = True
        self.assertEqual("750.00 USC • 20.00 USD", app._format_total_equity(accounts))

    def test_history_money_adds_usd_equivalent_only_in_raw_usc_mode(self):
        app = object.__new__(GoldMonitorApp)
        app._show_broker_currency = True
        self.assertEqual("1,329.60 USC ($13.296)", app._format_history_money(1329.60, "USC"))
        self.assertEqual("+6,727.80 USC ($67.278)", app._format_history_money(6727.80, "USC", signed=True))
        app._show_broker_currency = False
        self.assertEqual("1,329.60 USD", app._format_history_money(1329.60, "USC"))

    def test_history_time_range_normalizes_and_recovers_invalid_values(self):
        class Value:
            def __init__(self, value): self.value = value
            def get(self): return self.value
            def set(self, value): self.value = value

        app = object.__new__(GoldMonitorApp)
        app._history_start_time_value = Value("08:15")
        app._history_end_time_value = Value("17:30")
        self.assertEqual((time(8, 15), time(17, 30, 59, 999999)), app._history_time_range())
        app._history_start_time_value.value = "invalid"
        self.assertEqual((time(0, 0), time(23, 59, 59, 999999)), app._history_time_range())
        self.assertEqual("00:00", app._history_start_time_value.value)
        self.assertEqual("23:59", app._history_end_time_value.value)

    def test_history_ui_uses_english_time_labels_and_unqualified_volume_metric(self):
        source = Path("monitoring/gold_monitor/app.py").read_text(encoding="utf-8")
        self.assertIn('"START DATE"', source)
        self.assertIn('"END DATE"', source)
        self.assertIn('"START TIME"', source)
        self.assertIn('"END TIME"', source)
        self.assertNotIn('"TỪ GIỜ (GMT+7)"', source)
        self.assertNotIn('"ĐẾN GIỜ (GMT+7)"', source)
        self.assertIn('"FOLLOW TREND RATIO"', source)
        self.assertIn('"MAX DD"', source)
        self.assertNotIn('"MAX DD NGÀY"', source)
        self.assertIn('for column, weight in enumerate((3, 3, 5, 5, 4))', source)
        self.assertIn('text="TỔNG VỐN"', source)
        self.assertIn('textvariable=self._total_equity_value', source)
        self.assertIn('records = [(account, deal) for account, deal in records if start_time <= deal.time.replace(tzinfo=None).time() <= end_time]', source)
        self.assertIn('self._history_volume_value.set(f"{sum(float(deal.volume) for deal in deals):,.2f}")', source)
        self.assertNotIn('sum(float(deal.volume) for deal in deals):,.2f} LOT', source)

    def test_local_position_log_baseline_prevents_refresh_spam_but_keeps_real_changes(self):
        app = object.__new__(GoldMonitorApp)
        app._local_position_tickets = {}
        entries = []
        app._append_log = lambda message, level: entries.append((message, level))
        candidate = SimpleNamespace(path=Path("C:/MT5/terminal64.exe"))
        initial = AccountSnapshot(123, "Demo", 500.0, 500.0, "USD", (SimpleNamespace(ticket=7),), ())
        app._append_local_position_changes([(candidate, initial)])
        app._append_local_position_changes([(candidate, initial)])
        self.assertEqual([], entries)
        changed = AccountSnapshot(123, "Demo", 500.0, 500.0, "USD", (SimpleNamespace(ticket=8),), ())
        app._append_local_position_changes([(candidate, changed)])
        self.assertEqual([("MỞ position #8 • #123", "OPEN"), ("ĐÓNG position #7 • #123", "CLOSE")], entries)

    def test_requested_home_and_dialog_visual_contracts_are_present(self):
        source = Path("monitoring/gold_monitor/app.py").read_text(encoding="utf-8")
        self.assertNotIn('text="XAU / BTC • read-only"', source)
        self.assertIn('self._center_window(self)', source)
        self.assertIn('ACCOUNT_VIEWPORT_HEIGHT = 256', source)
        self.assertIn('account_viewport = tk.Frame(content, bg=Palette.APP, height=ACCOUNT_VIEWPORT_HEIGHT)', source)
        self.assertNotIn('account_scrollbar = ttk.Scrollbar', source)
        self.assertIn('account_canvas = tk.Canvas(account_viewport, bg=Palette.APP, highlightthickness=0, bd=0)', source)
        self.assertIn('def scroll_account_cards(event: tk.Event)', source)
        self.assertIn('self.bind_all("<MouseWheel>", scroll_account_cards, add="+")', source)
        self.assertIn('account_canvas.create_window((0, 0), window=self._account_cards_host, anchor="nw")', source)
        self.assertIn('body.pack(fill="both", expand=True); body.grid_propagate(False)', source)
        self.assertIn('left_width = round(available * 0.60)', source)
        self.assertIn('body.grid_columnconfigure(1, minsize=available - left_width)', source)
        self.assertIn('row.pack(fill="x", pady=(0, 3))', source)
        self.assertIn('card = self._card(row, padding=0); card.configure(padx=10, pady=7)', source)
        self.assertIn('account_line = tk.Frame(card, bg=Palette.CARD); account_line.pack(anchor="w")', source)
        self.assertIn('value.pack(side="left"); detail.pack(side="left", padx=(8, 0), pady=(2, 0))', source)
        self.assertIn('value.pack(anchor="w")', source)
        self.assertIn('self._schedule_refresh(0); window.destroy()', source)
        self.assertIn('text="QUÉT LẠI", command=scan, bg=Palette.INFO, fg="#FFFFFF"', source)
        self.assertIn('text="ÁP DỤNG THEO DÕI", command=apply, bg=Palette.SUCCESS, fg="#FFFFFF"', source)
        self.assertIn('text="MỞ REMOTE DESKTOP  →", command=connect', source)
        self.assertIn('field = tk.Frame(wrap, bg=Palette.INFO, padx=1, pady=1)', source)
        self.assertIn('highlightthickness=0, insertbackground=Palette.TEXT).pack(fill="x", padx=10, pady=1, ipady=8)', source)

    def test_rdp_rejects_blank_host(self):
        with self.assertRaises(ValueError):
            open_remote_desktop("")

    def test_standalone_launcher_targets_only_the_standalone_module(self):
        launcher = Path("GOLD_MONITOR.bat").read_text(encoding="utf-8").lower()
        self.assertIn("training_lab.monitoring.gold_monitor.app", launcher)
        self.assertNotIn("--config", launcher)
        self.assertNotIn("rsiqui", launcher)
