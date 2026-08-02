from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

import trading_lab.runners.mt5.rsiqui_btcusd_demo as btcusd_demo
import trading_lab.runners.mt5.rsiqui_final_demo as final_demo
import trading_lab.runners.mt5.rsiqui_neg_demo as neg_demo
import trading_lab.runners.mt5.rsiqui_ori_demo as ori_demo


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
    ("ORI", ori_demo, "XAUUSD"),
    ("NEG", neg_demo, "XAUUSD"),
    ("BTCUSD", btcusd_demo, "BTCUSD"),
)


def make_config(module, symbol: str):
    kwargs = {"symbol": symbol, "timeframe": "M5", "telegram_enabled": False}
    try:
        return module.Mt5DemoConfig(**kwargs, max_open_positions=1)
    except TypeError:
        return module.Mt5DemoConfig(**kwargs)


class RsiquiAllVariantEntryContractTests(unittest.TestCase):
    def test_all_variants_wait_outside_preclose_window(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                called = 0

                def evaluate_preclose_bar(bar_time: int):
                    nonlocal called
                    called += 1
                    return "long", bar_time

                runner.evaluate_preclose_bar = evaluate_preclose_bar
                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 54, tzinfo=UTC)))
                self.assertEqual(0, called)
                self.assertEqual([], mt5.order_requests)
                self.assertIn("outside M5 pre-close entry window", runner.last_status)

    def test_all_variants_check_only_once_per_m5_bar(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                calls: list[int] = []
                runner.evaluate_preclose_bar = lambda bar_time: (calls.append(bar_time) or ("long", bar_time))
                runner._build_request = lambda side: {"symbol": symbol, "price": 100.0, "sl": 90.0, "tp": 110.0}

                self.assertTrue(runner.start())
                self.assertTrue(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=UTC)))
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 58, tzinfo=UTC)))

                self.assertEqual(1, len(calls))
                self.assertEqual(1, len(mt5.order_requests))
                self.assertIn("duplicate pre-close check", runner.last_status)

    def test_all_variants_open_position_consumes_current_bar(self) -> None:
        for label, module, symbol in VARIANTS:
            with self.subTest(label=label):
                mt5 = FakeMt5(symbol)
                mt5.positions = (object(),)
                runner = module.DemoOnlyRsiquiMt5Runner(make_config(module, symbol), mt5=mt5)
                calls: list[int] = []
                runner.evaluate_preclose_bar = lambda bar_time: (calls.append(bar_time) or ("long", bar_time))
                runner._build_request = lambda side: {"symbol": symbol, "price": 100.0, "sl": 90.0, "tp": 110.0}

                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=UTC)))
                mt5.positions = ()
                self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 58, tzinfo=UTC)))

                self.assertEqual([], calls)
                self.assertEqual([], mt5.order_requests)
                self.assertIn("duplicate pre-close check", runner.last_status)


if __name__ == "__main__":
    unittest.main()
