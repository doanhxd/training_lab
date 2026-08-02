from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
import unittest

from trading_lab.runners.mt5.rsiqui_btcusd_demo import DemoOnlyRsiquiMt5Runner, load_demo_config


class FakeMt5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TIMEFRAME_M5 = 5
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.selected_symbols: list[str] = []
        self.sent_orders: list[dict] = []

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
        assert symbol == "BTCUSD"
        return SimpleNamespace(
            volume_min=0.01,
            volume_max=30.0,
            volume_step=0.01,
            point=0.01,
            digits=2,
            trade_tick_size=0.01,
            trade_tick_value=0.01,
            trade_stops_level=0,
            filling_mode=1,
        )

    def symbol_info_tick(self, symbol: str):
        assert symbol == "BTCUSD"
        return SimpleNamespace(bid=62_918.50, ask=62_928.50)

    def positions_get(self, symbol: str):
        assert symbol == "BTCUSD"
        return ()

    def order_send(self, request: dict):
        self.sent_orders.append(request)
        raise AssertionError("order_send must not be called by offline request tests")


class FillMt5(FakeMt5):
    def __init__(self) -> None:
        super().__init__()
        self.has_open_position = False

    def positions_get(self, symbol: str):
        assert symbol == "BTCUSD"
        return (SimpleNamespace(ticket=123),) if self.has_open_position else ()

    def order_send(self, request: dict):
        self.sent_orders.append(request)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, order=123456)


class RsiquiBtcusdContractTests(unittest.TestCase):
    def test_loads_btcusd_config_as_distinct_strategy(self) -> None:
        config = load_demo_config("configs/strategies/rsiqui/btcusd_m5_demo.json")

        self.assertEqual("BTCUSD", config.symbol)
        self.assertEqual(("BTCUSD",), DemoOnlyRsiquiMt5Runner(config, mt5=FakeMt5())._symbols_to_guard())
        self.assertEqual("M5", config.timeframe)
        self.assertEqual("gold-loose", config.preset)
        self.assertEqual(0.13, config.volume_lots)
        self.assertEqual(1.0, config.price_value_per_lot)
        self.assertEqual(10.0, config.risk_usd)
        self.assertEqual(10.0, config.reward_usd)
        self.assertEqual(10.0, config.max_spread_price)
        self.assertIsNone(config.equity_risk_cap_pct)
        self.assertFalse(config.telegram_enabled)

    def test_btcusd_build_request_uses_btc_symbol_and_contract(self) -> None:
        config = load_demo_config("configs/strategies/rsiqui/btcusd_m5_demo.json")
        mt5 = FakeMt5()
        runner = DemoOnlyRsiquiMt5Runner(config, mt5=mt5)

        self.assertTrue(runner.start())
        request = runner._build_request("long")
        strategy_config = runner._strategy_config()

        self.assertEqual(["BTCUSD"], mt5.selected_symbols)
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual("BTCUSD", request["symbol"])
        self.assertEqual(0.13, request["volume"])
        self.assertAlmostEqual(76.92, request["price"] - request["sl"], places=2)
        self.assertAlmostEqual(76.92, request["tp"] - request["price"], places=2)
        self.assertEqual("DoanhHD_BTC", request["comment"])
        self.assertEqual(0.13, strategy_config.volume_lots)
        self.assertEqual(1.0, strategy_config.price_value_per_lot)
        self.assertEqual(10.0, strategy_config.risk_usd)
        self.assertEqual(10.0, strategy_config.reward_usd)
        self.assertEqual(10.0, strategy_config.max_spread)
        self.assertEqual([], mt5.sent_orders)

    def test_poll_once_waits_until_m5_preclose_window(self) -> None:
        config = load_demo_config("configs/strategies/rsiqui/btcusd_m5_demo.json")
        mt5 = FillMt5()
        runner = DemoOnlyRsiquiMt5Runner(config, mt5=mt5)
        self.assertTrue(runner.start())
        runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)  # type: ignore[method-assign]
        runner._build_request = lambda side: {"symbol": "BTCUSD", "price": 100.0, "sl": 90.0, "tp": 110.0}  # type: ignore[method-assign]

        filled = runner.poll_once(datetime(2026, 8, 2, 8, 24, 54, tzinfo=UTC))

        self.assertFalse(filled)
        self.assertIn("outside M5 close-confirm windows", runner.last_status)
        self.assertEqual([], mt5.sent_orders)

    def test_poll_once_previews_then_submits_after_candle_close(self) -> None:
        config = load_demo_config("configs/strategies/rsiqui/btcusd_m5_demo.json")
        mt5 = FillMt5()
        runner = DemoOnlyRsiquiMt5Runner(config, mt5=mt5)
        self.assertTrue(runner.start())
        runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)  # type: ignore[method-assign]
        runner.evaluate_confirmed_close_bar = lambda bar_time: ("long", bar_time)  # type: ignore[method-assign]
        runner._build_request = lambda side: {"symbol": "BTCUSD", "price": 100.0, "sl": 90.0, "tp": 110.0}  # type: ignore[method-assign]

        self.assertFalse(runner.poll_once(datetime(2026, 8, 2, 8, 24, 56, tzinfo=UTC)))
        self.assertTrue(runner.poll_once(datetime(2026, 8, 2, 8, 25, 1, tzinfo=UTC)))
        self.assertFalse(runner.poll_once(datetime(2026, 8, 2, 8, 25, 2, tzinfo=UTC)))

        self.assertEqual(1, len(mt5.sent_orders))
        self.assertIn("duplicate close confirmation", runner.last_status)

    def test_open_position_skips_the_current_preclose_bar(self) -> None:
        config = load_demo_config("configs/strategies/rsiqui/btcusd_m5_demo.json")
        mt5 = FillMt5()
        mt5.has_open_position = True
        runner = DemoOnlyRsiquiMt5Runner(config, mt5=mt5)
        self.assertTrue(runner.start())
        runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)  # type: ignore[method-assign]
        runner._build_request = lambda side: {"symbol": "BTCUSD", "price": 100.0, "sl": 90.0, "tp": 110.0}  # type: ignore[method-assign]

        self.assertFalse(runner.poll_once(datetime(2026, 8, 2, 8, 24, 56, tzinfo=UTC)))
        mt5.has_open_position = False
        self.assertFalse(runner.poll_once(datetime(2026, 8, 2, 8, 24, 58, tzinfo=UTC)))

        self.assertEqual([], mt5.sent_orders)
        self.assertIn("duplicate pre-close preview", runner.last_status)


if __name__ == "__main__":
    unittest.main()
