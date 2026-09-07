from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase

from training_lab.monitoring.gold_monitor.adapter import GoldPositionMonitor, HistoryDealView
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

    def test_refresh_keeps_unicode_label_outside_locale_sensitive_strftime_format(self):
        source = Path("monitoring/gold_monitor/app.py").read_text(encoding="utf-8")
        self.assertNotIn('strftime("CẬP NHẬT', source)
        self.assertIn('self._updated_value.set(f"CẬP NHẬT\\n{now:%H:%M:%S}")', source)

    def test_rdp_rejects_blank_host(self):
        with self.assertRaises(ValueError):
            open_remote_desktop("")

    def test_standalone_launcher_targets_only_the_standalone_module(self):
        launcher = Path("GOLD_MONITOR.bat").read_text(encoding="utf-8").lower()
        self.assertIn("training_lab.monitoring.gold_monitor.app", launcher)
        self.assertNotIn("--config", launcher)
        self.assertNotIn("rsiqui", launcher)
