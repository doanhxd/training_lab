from __future__ import annotations

from types import SimpleNamespace
import unittest

from trading_lab.monitoring.rsiqui.position_monitor import RsiquiV3PositionMonitor


class FakeMt5:
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1

    def __init__(self) -> None:
        self.initialized = False
        self.shutdown_called = False
        self.positions = ()
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

    def positions_get(self, symbol: str):
        assert symbol == "XAUUSD"
        return self.positions


class FakeProcess:
    def __init__(self, pid: int, *cmdline: str) -> None:
        self.info = {"pid": pid, "cmdline": list(cmdline)}


def position(*, ticket: int, side: int, comment: str, profit: float) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=ticket,
        type=side,
        volume=0.02,
        price_open=2300.50,
        sl=2295.50,
        tp=2305.50,
        profit=profit,
        comment=comment,
    )


class RsiquiV3PositionMonitorTests(unittest.TestCase):
    def test_snapshot_marks_rsiqui_v3_and_manual_positions_without_trading(self) -> None:
        mt5 = FakeMt5()
        mt5.positions = (
            position(ticket=1001, side=mt5.POSITION_TYPE_BUY, comment="RSIQUI V3 DEMO", profit=4.5),
            position(ticket=1002, side=mt5.POSITION_TYPE_SELL, comment="manual", profit=-2.0),
        )
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())

        snapshot = monitor.refresh()

        self.assertTrue(mt5.initialized)
        self.assertEqual(7123456, snapshot.login)
        self.assertEqual(2, len(snapshot.positions))
        self.assertEqual("BUY", snapshot.positions[0].side)
        self.assertEqual("RSIQUI V3", snapshot.positions[0].source)
        self.assertEqual("THỦ CÔNG", snapshot.positions[1].source)
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

    def test_detects_running_rsiqui_runner_processes(self) -> None:
        mt5 = FakeMt5()
        monitor = RsiquiV3PositionMonitor(
            symbol="XAUUSD",
            mt5=mt5,
            process_iter=lambda: (
                FakeProcess(1201, "python", "runners/mt5/rsiqui_final_demo.py", "--config", "final_m5_demo.json"),
                FakeProcess(1202, "python", "something_else.py"),
            ),
        )

        snapshot = monitor.refresh()

        self.assertTrue(snapshot.runner_detection_available)
        self.assertEqual(1, len(snapshot.running_runners))
        self.assertEqual("rsiqui_v3_final", snapshot.running_runners[0].strategy_key)
        self.assertEqual(1201, snapshot.running_runners[0].pid)

    def test_stop_releases_mt5_connection(self) -> None:
        mt5 = FakeMt5()
        monitor = RsiquiV3PositionMonitor(symbol="XAUUSD", mt5=mt5, process_iter=lambda: ())
        monitor.refresh()

        monitor.stop()

        self.assertTrue(mt5.shutdown_called)


if __name__ == "__main__":
    unittest.main()
