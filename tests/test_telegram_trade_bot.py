from __future__ import annotations

import unittest

from training_lab.telegram_trade_bot import (
    TradeCommand,
    build_market_order_request,
    parse_analytics_command,
    parse_control_command,
    parse_period,
    parse_trade_command,
    resolve_trade_symbol,
    volume_for_symbol,
)


class TelegramTradeBotTests(unittest.TestCase):
    def test_parse_short_gold_command_with_bot_mention(self) -> None:
        command = parse_trade_command("/short gold @rich_vjp_bot")
        self.assertEqual(TradeCommand(side="short", symbol="gold"), command)

    def test_parse_long_gold_command_maps_to_xauusdc(self) -> None:
        command = parse_trade_command("/long gold @rich_vjp_bot")
        self.assertEqual(TradeCommand(side="long", symbol="gold"), command)

    def test_gold_resolves_to_available_broker_symbol(self) -> None:
        class GoldMt5(FakeMt5):
            def __init__(self, available):
                self.available = available

            def symbol_info(self, symbol: str):
                return type("Info", (), {"digits": 2})() if symbol in self.available else None

            def symbol_select(self, symbol: str, enable: bool):
                return symbol in self.available and enable

        self.assertEqual("XAUUSDc", resolve_trade_symbol(GoldMt5({"XAUUSDc"}), "gold"))
        self.assertEqual("XAUUSD", resolve_trade_symbol(GoldMt5({"XAUUSD"}), "gold"))
        with self.assertRaises(ValueError):
            resolve_trade_symbol(GoldMt5(set()), "gold")

    def test_only_xauusdc_uses_010_lot(self) -> None:
        self.assertEqual(0.10, volume_for_symbol("XAUUSDc"))
        self.assertEqual(0.03, volume_for_symbol("XAUUSD"))

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
            mt5=FakeMt5(), symbol="XAUUSD", side="short", bid=2350.25,
            ask=2350.45, volume=0.03, distance=10.0,
        )
        self.assertEqual(0.03, request["volume"])
        self.assertEqual(2350.25, request["price"])
        self.assertEqual(2360.25, request["sl"])
        self.assertEqual(2340.25, request["tp"])
        self.assertEqual(FakeMt5.ORDER_TYPE_SELL, request["type"])

    def test_accepts_configured_group_and_private_chat_ids(self) -> None:
        from training_lab.telegram_trade_bot import TelegramTradeBot
        bot = TelegramTradeBot(
            mt5=FakeMt5(), token="token", chat_id="-5043082181",
            allowed_chat_ids={"5165617890"}, allowed_user_ids={5165617890},
        )
        self.assertEqual({"-5043082181", "5165617890"}, bot.allowed_chat_ids)

    def test_parses_control_commands(self) -> None:
        self.assertEqual(("status", None), parse_control_command("/status"))
        self.assertEqual(("positions", None), parse_control_command("/positions"))
        self.assertEqual(("close", 123456), parse_control_command("/close 123456"))
        self.assertEqual(("closeall", "confirm"), parse_control_command("/closeall confirm"))
        self.assertEqual(("disable", None), parse_control_command("/disable"))
        with self.assertRaises(ValueError):
            parse_control_command("/closeall")

    def test_parses_analytics_commands_and_periods(self) -> None:
        self.assertEqual(("stats", 7), parse_analytics_command("/stats 7d"))
        self.assertEqual(("equity", None), parse_analytics_command("/equity all"))
        self.assertEqual(("history", 25), parse_analytics_command("/history 25"))
        self.assertEqual(("drawdown", None), parse_analytics_command("/drawdown"))
        self.assertEqual(("daily", 3), parse_analytics_command("/daily 3"))
        self.assertEqual(None, parse_period("all"))
        with self.assertRaises(ValueError):
            parse_analytics_command("/history 51")

    def test_status_includes_today_trade_count_and_net_pnl(self) -> None:
        from training_lab.telegram_trade_bot import TelegramTradeBot

        class StatusMt5(FakeMt5):
            def account_info(self):
                return type("Account", (), {"login": 5043011, "trade_mode": 0, "balance": 10000.0, "equity": 10002.0})()

            def positions_get(self):
                return []

            def history_deals_get(self, start, end):
                return [
                    type("Deal", (), {"profit": 12.5, "commission": -1.0, "swap": 0.0})(),
                    type("Deal", (), {"profit": -3.0, "commission": 0.0, "swap": 0.0})(),
                ]

        bot = TelegramTradeBot(
            mt5=StatusMt5(), token="token", chat_id="-5043082181",
            allowed_user_ids={5165617890},
        )
        status = bot._format_status()
        self.assertIn("Trades today: 2", status)
        self.assertIn("Today's net P/L: $+8.50", status)


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
