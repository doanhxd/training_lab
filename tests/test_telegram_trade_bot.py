from __future__ import annotations

import unittest

from training_lab.telegram_trade_bot import (
    TradeCommand,
    build_market_order_request,
    parse_trade_command,
    volume_for_symbol,
)


class TelegramTradeBotTests(unittest.TestCase):
    def test_parse_short_gold_command_with_bot_mention(self) -> None:
        command = parse_trade_command("/short gold @rich_vjp_bot")
        self.assertEqual(TradeCommand(side="short", symbol="XAUUSD"), command)

    def test_parse_xauusdc_command_and_use_larger_fixed_volume(self) -> None:
        command = parse_trade_command("/short xauusdc @rich_vjp_bot")
        self.assertEqual(TradeCommand(side="short", symbol="XAUUSDc"), command)
        self.assertEqual(0.1, volume_for_symbol(command.symbol))
        self.assertEqual(0.03, volume_for_symbol("XAUUSD"))

    def test_rejects_unknown_symbol_and_missing_mention(self) -> None:
        with self.assertRaises(ValueError):
            parse_trade_command("/long btc")
        with self.assertRaises(ValueError):
            parse_trade_command("/long gold", bot_username="rich_vjp_bot")
        with self.assertRaises(ValueError):
            parse_trade_command("/buy gold @rich_vjp_bot")

    def test_builds_short_request_with_fixed_volume_and_10_price_sl_tp(self) -> None:
        request = build_market_order_request(
            mt5=FakeMt5(),
            symbol="XAUUSD",
            side="short",
            bid=2350.25,
            ask=2350.45,
            volume=0.03,
            distance=10.0,
        )
        self.assertEqual(0.03, request["volume"])
        self.assertEqual(2350.25, request["price"])
        self.assertEqual(2360.25, request["sl"])
        self.assertEqual(2340.25, request["tp"])
        self.assertEqual(FakeMt5.ORDER_TYPE_SELL, request["type"])


class FakeMt5:
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1

    def symbol_info(self, symbol: str):
        return type("Info", (), {"digits": 2})()


if __name__ == "__main__":
    unittest.main()
