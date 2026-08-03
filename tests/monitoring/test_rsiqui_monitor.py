from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from trading_lab.monitoring.rsiqui.position_monitor import RsiquiV3PositionMonitor


class FakeMt5:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    DEAL_TYPE_BUY = 0
    DEAL_TYPE_SELL = 1
    DEAL_ENTRY_IN = 0
    DEAL_ENTRY_OUT = 1
    DEAL_ENTRY_INOUT = 2

    def __init__(self) -> None:
        self.initialized = False
        self.shutdown_called = False
        self.positions = ()
        self.positions_by_symbol: dict[str, tuple] = {}
        self.broker_symbols = ()
        self.deals = ()
        self.history_args = None
        self.account = SimpleNamespace(login=7123456, server="Demo-Server", balance=10_000.0, equity=10_012.5, currency="USD")

    def initialize(self) -> bool:
        self.initialized = True
        return True

    def shutdown(self) -> None:
        self.shutdown_called = True

    def last_error(self):
        return (0, "ok")

    def account_info(self):
        return self.account

    def positions_get(self, symbol: str | None = None):
        if symbol is None:
            return self.positions
        return self.positions_by_symbol.get(symbol, ())

    def symbols_get(self):
        return self.broker_symbols

    def history_deals_get(self, start: datetime, end: datetime):
        self.history_args = (start, end)
        return self.deals


class FakeProcess:
    def __init__(self, pid: int, *cmdline: str) -> None:
        self.info = {"pid": pid, "cmdline": list(cmdline)}


def position(*, ticket: int, side: int, comment: str, profit: float, symbol: str = "XAUUSD") -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        time=int(datetime(2026, 8, 1, 6, 30, tzinfo=UTC).timestamp()),
        symbol=symbol,
        type=side,
        volume=0.02,
        price_open=2300.50,
        sl=2295.50,
        tp=2305.50,
        profit=profit,
        comment=comment,
    )


def deal(*, ticket: int, when: datetime, side: int, symbol: str, profit: float, commission: float = 0.0, swap: float = 0.0, comment: str = "", entry: int = FakeMt5.DEAL_ENTRY_OUT) -> SimpleNamespace:
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return SimpleNamespace(
        ticket=ticket,
        time=int(when.timestamp()),
        type=side,
        entry=entry,
        symbol=symbol,
        volume=0.03,
        price=2400.25,
        profit=profit,
        commission=commission,
        swap=swap,
        comment=comment,
    )


class RsiquiV3PositionMonitorTests(unittest.TestCase):
    def test_snapshot_marks_rsiqui_v3_and_manual_positions_without_trading(self) -> None:
        mt5 = FakeMt5()
        mt5.positions = (
            position(ticket=1001, side=mt5.POSITION_TYPE_BUY, comment="RSIQUI V3 DEMO", profit=4.5),
            position(ticket=1002, side=mt5.POSITION_TYPE_SELL, comment="manual", profit=-2.0),
            position(ticket=1003, side=mt5.POSITION_TYPE_BUY, comment="DoanhHD_Trader B", profit=1.95, symbol="BTCUSD"),
            position(ticket=1004, side=mt5.POSITION_TYPE_BUY, comment="other", profit=0.5, symbol="EURUSD"),
        )
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())

        snapshot = monitor.refresh()

        self.assertTrue(mt5.initialized)
        self.assertEqual(7123456, snapshot.login)
        self.assertEqual(3, len(snapshot.positions))
        self.assertEqual("BUY", snapshot.positions[0].side)
        self.assertEqual("2026-08-01 13:30:00", snapshot.positions[0].opened_at.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual("DoanhHD_GOLD", snapshot.positions[0].source)
        self.assertEqual("TAY", snapshot.positions[1].source)
        self.assertEqual("BTCUSD", snapshot.positions[2].symbol)
        self.assertEqual([], [entry for entry in snapshot.log_entries if "order_send" in entry])
        self.assertEqual((), snapshot.running_runners)

    def test_refresh_logs_open_and_close_transitions_once(self) -> None:
        mt5 = FakeMt5()
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())
        mt5.positions = (position(ticket=1001, side=mt5.POSITION_TYPE_BUY, comment="RSIQUI V3 DEMO", profit=1.0),)

        first = monitor.refresh()
        second = monitor.refresh()
        mt5.positions = ()
        third = monitor.refresh()

        self.assertTrue(any("RSIQUI V3 mở BUY #1001" in entry for entry in first.log_entries))
        self.assertFalse(any("mở BUY #1001" in entry for entry in second.log_entries))
        self.assertTrue(any("đã đóng / không còn hiển thị" in entry for entry in third.log_entries))

    def test_refresh_falls_back_to_per_symbol_positions_when_mt5_requires_symbol(self) -> None:
        mt5 = FakeMt5()
        mt5.positions = None
        mt5.positions_by_symbol = {
            "XAUUSD": (position(ticket=1101, side=mt5.POSITION_TYPE_BUY, comment="gold", profit=1.0, symbol="XAUUSD"),),
            "BTCUSD": (position(ticket=1102, side=mt5.POSITION_TYPE_BUY, comment="btc", profit=1.95, symbol="BTCUSD"),),
        }
        def positions_get(symbol: str | None = None):
            if symbol is None:
                raise TypeError("symbol required")
            return mt5.positions_by_symbol.get(symbol, ())
        mt5.positions_get = positions_get
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())

        snapshot = monitor.refresh()

        self.assertEqual(["XAUUSD", "BTCUSD"], [item.symbol for item in snapshot.positions])

    def test_refresh_includes_broker_symbol_suffixes(self) -> None:
        mt5 = FakeMt5()
        mt5.positions = (
            position(ticket=1111, side=mt5.POSITION_TYPE_BUY, comment="gold", profit=1.0, symbol="XAUUSDm"),
            position(ticket=1112, side=mt5.POSITION_TYPE_SELL, comment="gold", profit=-1.0, symbol="XAUUSD.c"),
            position(ticket=1113, side=mt5.POSITION_TYPE_BUY, comment="btc", profit=2.0, symbol="BTCUSDm"),
            position(ticket=1114, side=mt5.POSITION_TYPE_BUY, comment="other", profit=2.0, symbol="EURUSD"),
        )
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())

        snapshot = monitor.refresh()

        self.assertEqual(["XAUUSDm", "XAUUSD.c", "BTCUSDm"], [item.symbol for item in snapshot.positions])

    def test_fallback_discovers_suffixes_from_broker_symbol_catalog(self) -> None:
        mt5 = FakeMt5()
        mt5.positions = None
        mt5.broker_symbols = (SimpleNamespace(name="XAUUSDm"), SimpleNamespace(name="BTCUSD.r"))
        mt5.positions_by_symbol = {
            "XAUUSDm": (position(ticket=1121, side=mt5.POSITION_TYPE_BUY, comment="gold", profit=1.0, symbol="XAUUSDm"),),
            "BTCUSD.r": (position(ticket=1122, side=mt5.POSITION_TYPE_BUY, comment="btc", profit=2.0, symbol="BTCUSD.r"),),
        }

        def positions_get(symbol: str | None = None):
            if symbol is None:
                raise TypeError("symbol required")
            return mt5.positions_by_symbol.get(symbol, ())

        mt5.positions_get = positions_get
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())

        snapshot = monitor.refresh()

        self.assertEqual(["XAUUSDm", "BTCUSD.r"], [item.symbol for item in snapshot.positions])

    def test_detects_running_rsiqui_runner_processes(self) -> None:
        mt5 = FakeMt5()
        monitor = RsiquiV3PositionMonitor(
            symbol="XAUUSD",
            mt5=mt5,
            process_iter=lambda: (
                FakeProcess(1201, "python", "runners/mt5/rsiqui_final_demo.py", "--config", "final_m5_demo.json"),
                FakeProcess(1203, "python", "-m", "trading_lab.runners.mt5.rsiqui_btcusd_demo", "--config", "btcusd_m5_demo.json"),
                FakeProcess(1202, "python", "something_else.py"),
            ),
        )

        snapshot = monitor.refresh()

        self.assertTrue(snapshot.runner_detection_available)
        self.assertEqual(2, len(snapshot.running_runners))
        detected = {runner.strategy_key: runner.pid for runner in snapshot.running_runners}
        self.assertEqual(1201, detected["rsiqui_v3_final"])
        self.assertEqual(1203, detected["rsiqui_v3_btcusd"])

    def test_stop_releases_mt5_connection(self) -> None:
        mt5 = FakeMt5()
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())
        monitor.refresh()

        monitor.stop()

        self.assertTrue(mt5.shutdown_called)

    def test_history_reads_account_deals_and_calculates_winrate_and_daily_dd(self) -> None:
        mt5 = FakeMt5()
        mt5.deals = (
            deal(ticket=2001, when=datetime(2026, 8, 1, 9, 0), side=mt5.DEAL_TYPE_BUY, symbol="XAUUSD", profit=12.0, commission=-1.0, comment="win", entry=mt5.DEAL_ENTRY_OUT),
            deal(ticket=2004, when=datetime(2026, 8, 1, 12, 0), side=mt5.DEAL_TYPE_BUY, symbol="BTCUSD", profit=8.0, comment="btc win", entry=mt5.DEAL_ENTRY_OUT),
            deal(ticket=2003, when=datetime(2026, 8, 1, 11, 0), side=mt5.DEAL_TYPE_BUY, symbol="EURUSD", profit=0.0, comment="opening deal", entry=mt5.DEAL_ENTRY_IN),
        )
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())

        deals, stats = monitor.history(datetime(2026, 8, 1), datetime(2026, 8, 2))

        self.assertEqual((datetime(2026, 8, 1), datetime(2026, 8, 2)), mt5.history_args)
        self.assertEqual(2, len(deals))
        self.assertEqual("BTCUSD", deals[0].symbol)
        self.assertEqual("XAUUSD", deals[1].symbol)
        self.assertEqual("2026-08-01 19:00:00", deals[0].time.strftime("%Y-%m-%d %H:%M:%S"))
        self.assertEqual(2, stats.deals)
        self.assertEqual(2, stats.wins)
        self.assertEqual(0, stats.losses)
        self.assertAlmostEqual(100.0, stats.winrate)
        self.assertAlmostEqual(19.0, stats.net_profit)
        self.assertAlmostEqual(0.0, stats.max_daily_drawdown)


if __name__ == "__main__":
    unittest.main()
