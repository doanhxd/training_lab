from __future__ import annotations

from datetime import UTC, datetime
import inspect
from types import SimpleNamespace
import unittest

import trading_lab.runners.mt5.rsiqui_btcusd_demo as btcusd_demo
import trading_lab.runners.mt5.rsiqui_final_demo as final_demo


class FakeResult:
    retcode = 10009
    order = 123


class FakeMt5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    TRADE_RETCODE_DONE = 10009

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.positions: tuple[object, ...] = ()
        self.order_requests: list[dict] = []
        self.selected_symbols: list[str] = []

    def initialize(self) -> bool:
        return True

    def last_error(self):
        return (0, "ok")

    def shutdown(self) -> None:
        pass

    def account_info(self):
        return SimpleNamespace(trade_mode=self.ACCOUNT_TRADE_MODE_DEMO, equity=10_000.0)

    def symbol_select(self, symbol: str, enabled: bool) -> bool:
        self.selected_symbols.append(symbol)
        return True

    def symbol_info(self, symbol: str):
        return SimpleNamespace(
            volume_min=0.01,
            volume_max=30.0,
            volume_step=0.01,
            point=0.01,
            digits=2,
            trade_tick_size=0.01,
            trade_tick_value=0.01 if symbol == "BTCUSD" else 1.0,
            trade_stops_level=0,
            filling_mode=1,
        )

    def symbol_info_tick(self, symbol: str):
        return SimpleNamespace(bid=2300.0, ask=2300.1, time=1_767_312_296)

    def positions_get(self, symbol: str):
        return self.positions

    def order_send(self, request: dict):
        self.order_requests.append(request)
        return FakeResult()


VARIANTS = (
    ("FINAL", final_demo, "XAUUSD"),
    ("BTCUSD", btcusd_demo, "BTCUSD"),
)


def make_config(module, symbol: str):
    kwargs = {"symbol": symbol, "timeframe": "M5", "telegram_enabled": False}
    try:
        return module.Mt5DemoConfig(**kwargs, max_open_positions=1)
    except TypeError:
        return module.Mt5DemoConfig(**kwargs)


class RsiquiAllVariantEntryContractTests(unittest.TestCase):
    def test_all_variants_schedule_from_wall_clock_not_stale_tick_time(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                config = make_config(module, symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(config, mt5=FakeMt5(symbol))
                source = inspect.getsource(runner.poll_once)

                self.assertEqual(1.0, config.poll_seconds)
                self.assertIn("datetime.now(tz=UTC)", source)
                self.assertNotIn("self._current_tick_time() if now_utc is None", source)

    def test_all_variants_terminal_status_lines_include_time_only(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=FakeMt5(symbol))
                runner.last_status = "waiting: test status"

                status_line = runner._terminal_status_line()

                self.assertRegex(status_line, r"^\[\d{2}:\d{2}:\d{2}\] waiting: test status$")
                self.assertNotRegex(status_line, r"\d{4}-\d{2}-\d{2}")

    def test_all_variants_preview_before_close_but_do_not_submit(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                calls: list[int] = []
                runner.evaluate_preclose_bar = lambda bar_time: (calls.append(bar_time) or ("long", bar_time))

                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=UTC)))

                self.assertEqual([1_767_312_000], calls)
                self.assertEqual((1_767_312_000, "long"), runner._pending_preclose_signal)
                self.assertEqual([], mt5.order_requests)
                self.assertIn("preview only", runner.last_status)

    def test_all_variants_submit_after_close_only_when_closed_signal_matches_preview(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                preclose_calls: list[int] = []
                close_calls: list[int] = []
                runner.evaluate_preclose_bar = lambda bar_time: (preclose_calls.append(bar_time) or ("long", bar_time))
                runner.evaluate_confirmed_close_bar = lambda bar_time: (close_calls.append(bar_time) or ("long", bar_time))
                runner._build_request = lambda side: {"symbol": symbol, "price": 100.0, "sl": 90.0, "tp": 110.0}

                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=UTC)))
                self.assertTrue(runner.poll_once(datetime(2026, 1, 2, 0, 5, 1, tzinfo=UTC)))

                self.assertEqual([1_767_312_000], preclose_calls)
                self.assertEqual([1_767_312_000], close_calls)
                self.assertEqual(1, len(mt5.order_requests))
                self.assertIn("after M5 candle close", runner.last_status)

    def test_all_variants_block_when_closed_signal_differs_from_preview(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                runner.evaluate_preclose_bar = lambda bar_time: ("short", bar_time)
                runner.evaluate_confirmed_close_bar = lambda bar_time: ("long", bar_time)
                runner._build_request = lambda side: {"symbol": symbol, "price": 100.0, "sl": 90.0, "tp": 110.0}

                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=UTC)))
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 5, 1, tzinfo=UTC)))

                self.assertEqual([], mt5.order_requests)
                self.assertIn("does not match closed-candle signal", runner.last_status)

    def test_all_variants_do_not_submit_after_close_without_matching_preview(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                runner.evaluate_confirmed_close_bar = lambda bar_time: ("long", bar_time)
                runner._build_request = lambda side: {"symbol": symbol, "price": 100.0, "sl": 90.0, "tp": 110.0}

                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 5, 1, tzinfo=UTC)))

                self.assertEqual([], mt5.order_requests)
                self.assertIn("no matching pre-close preview", runner.last_status)

    def test_all_variants_open_position_consumes_preclose_bar(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                mt5.positions = (object(),)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                preclose_calls: list[int] = []
                close_calls: list[int] = []
                runner.evaluate_preclose_bar = lambda bar_time: (preclose_calls.append(bar_time) or ("long", bar_time))
                runner.evaluate_confirmed_close_bar = lambda bar_time: (close_calls.append(bar_time) or ("long", bar_time))
                runner._build_request = lambda side: {"symbol": symbol, "price": 100.0, "sl": 90.0, "tp": 110.0}

                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=UTC)))
                mt5.positions = ()
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 5, 1, tzinfo=UTC)))

                self.assertEqual([], preclose_calls)
                self.assertEqual([], close_calls)
                self.assertEqual([], mt5.order_requests)
                self.assertIn("no matching pre-close preview", runner.last_status)

    def test_all_variants_confirm_only_once_per_closed_bar(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)
                runner.evaluate_confirmed_close_bar = lambda bar_time: ("long", bar_time)
                runner._build_request = lambda side: {"symbol": symbol, "price": 100.0, "sl": 90.0, "tp": 110.0}

                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=UTC)))
                self.assertTrue(runner.poll_once(datetime(2026, 1, 2, 0, 5, 1, tzinfo=UTC)))
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 5, 2, tzinfo=UTC)))

                self.assertEqual(1, len(mt5.order_requests))
                self.assertIn("duplicate close confirmation", runner.last_status)


if __name__ == "__main__":
    unittest.main()
