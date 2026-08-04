from __future__ import annotations

from types import SimpleNamespace
import unittest

from trading_lab.runners.mt5.rsiqui_final_trailing_demo import (
    DemoOnlyRsiquiFinalTrailingMt5Runner,
    load_demo_config,
)


class FakeMt5:
    ACCOUNT_TRADE_MODE_DEMO = 0
    TIMEFRAME_M5 = 5
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_SLTP = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self, bid: float) -> None:
        self.bid = bid
        self.orders: list[dict] = []
        self.position = SimpleNamespace(
            ticket=123,
            type=self.ORDER_TYPE_BUY,
            price_open=2300.0,
            volume=0.03,
            sl=2290.0,
            tp=2303.0,
            magic=573504,
        )

    def initialize(self) -> bool:
        return True

    def account_info(self):
        return SimpleNamespace(trade_mode=self.ACCOUNT_TRADE_MODE_DEMO, equity=600.0)

    def symbol_select(self, symbol: str, enabled: bool) -> bool:
        return True

    def symbol_info(self, symbol: str):
        return SimpleNamespace(
            point=0.01,
            digits=2,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_stops_level=0,
        )

    def symbol_info_tick(self, symbol: str):
        return SimpleNamespace(bid=self.bid, ask=self.bid + 0.1)

    def positions_get(self, symbol: str):
        return (self.position,)

    def order_send(self, request: dict):
        self.orders.append(request)
        self.position.sl = request["sl"]
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE)


class FinalTrailingTests(unittest.TestCase):
    def test_config_is_distinct_and_has_requested_contract(self) -> None:
        config = load_demo_config("configs/strategies/rsiqui/final_trailing_m5_demo.json")
        self.assertEqual("0.02", f"{config.volume_lots:.2f}")
        self.assertEqual(24.0, config.risk_usd)
        self.assertEqual(10.0, config.reward_usd)
        self.assertEqual("XAUUSD", config.symbol)
        self.assertEqual(573504, config.magic)

        self.assertEqual(2.0, config.trailing_activation_price_distance)
        self.assertEqual(4.0, config.trailing_locked_profit_usd)
        self.assertEqual(0.5, config.trailing_step_price)

    def test_trigger_locks_4_and_ratchets_by_half_price_steps(self) -> None:
        mt5 = FakeMt5(bid=2302.01)  # just above +2.0 price / +$6.00
        runner = DemoOnlyRsiquiFinalTrailingMt5Runner(load_demo_config(), mt5=mt5)
        self.assertTrue(runner.start())

        self.assertTrue(runner._trail_open_position())
        self.assertEqual(1, len(mt5.orders))
        self.assertAlmostEqual(2301.33, mt5.orders[0]["sl"], places=2)
        self.assertGreater(mt5.orders[0]["sl"], mt5.position.price_open)

        mt5.bid = 2302.51  # one additional 0.5-price trailing step
        self.assertTrue(runner._trail_open_position())
        raised_sl = mt5.position.sl
        self.assertAlmostEqual(2301.83, raised_sl, places=2)

        mt5.bid = 2302.40
        self.assertFalse(runner._trail_open_position())
        self.assertEqual(raised_sl, mt5.position.sl)

    def test_at_or_below_activation_does_not_move_sl(self) -> None:
        mt5 = FakeMt5(bid=2302.00)  # exactly +2.0 price / +$6.00: strict trigger is not met
        runner = DemoOnlyRsiquiFinalTrailingMt5Runner(load_demo_config(), mt5=mt5)
        self.assertTrue(runner.start())

        self.assertFalse(runner._trail_open_position())
        self.assertEqual([], mt5.orders)
        self.assertEqual(2290.0, mt5.position.sl)

    def test_does_not_modify_foreign_magic_position(self) -> None:
        mt5 = FakeMt5(bid=2302.20)
        mt5.position.magic = 573503
        runner = DemoOnlyRsiquiFinalTrailingMt5Runner(load_demo_config(), mt5=mt5)
        self.assertTrue(runner.start())

        self.assertFalse(runner._trail_open_position())
        self.assertEqual([], mt5.orders)


if __name__ == "__main__":
    unittest.main()
