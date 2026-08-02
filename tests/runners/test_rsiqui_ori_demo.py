from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import unittest

import numpy as np

import trading_lab.runners.mt5.rsiqui_ori_demo as ori_demo
from trading_lab.runners.mt5.rsiqui_ori_demo import DemoOnlyRsiquiMt5Runner, Mt5DemoConfig, load_demo_config
from trading_lab.telegram_notifier import TelegramNotifier, TelegramSettings, format_filled_order_message, format_signal_message


@dataclass
class FakeResult:
    retcode: int
    order: int = 123
    comment: str = "filled"


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

    def __init__(self, *, demo: bool = True, equity: float = 10_000.0) -> None:
        self.demo = demo
        self.equity = equity
        self.order_requests: list[dict] = []
        self.positions: tuple[object, ...] = ()
        self.copy_rates_calls = 0
        self.last_timeframe = None

    def initialize(self) -> bool:
        return True

    def last_error(self):
        return (0, "ok")

    def shutdown(self) -> None:
        pass

    def account_info(self):
        return SimpleNamespace(trade_mode=self.ACCOUNT_TRADE_MODE_DEMO if self.demo else 2, equity=self.equity)

    def symbol_info(self, symbol: str):
        return SimpleNamespace(
            visible=True,
            trade_mode=4,
            volume_min=0.01,
            volume_max=10.0,
            volume_step=0.01,
            point=0.01,
            digits=2,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_stops_level=0,
            filling_mode=1,
        )

    def symbol_select(self, symbol: str, enabled: bool) -> bool:
        return True

    def symbol_info_tick(self, symbol: str):
        return SimpleNamespace(bid=2300.00, ask=2300.10, time=1_700_000_000)

    def copy_rates_from_pos(self, symbol: str, timeframe: int, start: int, count: int):
        self.copy_rates_calls += 1
        self.last_timeframe = timeframe
        rows = np.zeros(100, dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"), ("tick_volume", "i8"), ("spread", "i8")])
        rows["time"] = np.arange(100) * 900 + 1_700_000_000
        rows["open"] = 2300.0
        rows["high"] = 2300.2
        rows["low"] = 2299.8
        rows["close"] = 2300.0
        return rows

    def order_send(self, request: dict):
        self.order_requests.append(request)
        return FakeResult(self.TRADE_RETCODE_DONE)

    def positions_get(self, symbol: str):
        assert symbol == "XAUUSD"
        return self.positions


class FakeNotifier:
    def __init__(self, delivered: bool = True) -> None:
        self.delivered = delivered
        self.messages: list[str] = []
        self.last_status = "Telegram test failure"

    def send(self, text: str) -> bool:
        self.messages.append(text)
        return self.delivered


class DemoOnlyRsiquiRunnerTests(unittest.TestCase):
    def test_loads_user_m5_gold_loose_contract(self) -> None:
        config = load_demo_config("configs/strategies/rsiqui/ori_m5_demo.json")

        self.assertEqual("M5", config.timeframe)
        self.assertEqual("gold-loose", config.preset)
        self.assertEqual("both", config.trade_side)
        self.assertEqual(0.2, config.max_spread_price)
        self.assertEqual(0.01, config.volume_lots)
        self.assertEqual(5.0, config.risk_usd)
        self.assertEqual(5.0, config.reward_usd)
        self.assertEqual((5, 6, 7, 20, 21), config.blocked_entry_hours_gmt7)
        self.assertEqual(3, config.max_open_positions)
        self.assertFalse(config.telegram_enabled)
        self.assertEqual(300.0, config.status_log_interval_seconds)

    def test_m5_config_requests_m5_rates(self) -> None:
        mt5 = FakeMt5()
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(timeframe="M5"), mt5=mt5)

        runner.evaluate_preclose_bar(1_700_000_000)

        self.assertEqual(mt5.TIMEFRAME_M5, mt5.last_timeframe)

    def test_rejects_non_demo_account_before_market_data_or_order(self) -> None:
        mt5 = FakeMt5(demo=False)
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(symbol="XAUUSD"), mt5=mt5)

        self.assertFalse(runner.start())
        self.assertEqual([], mt5.order_requests)
        self.assertEqual(0, mt5.copy_rates_calls)
        self.assertIn("demo", runner.last_status.lower())

    def test_blocks_new_entries_in_gmt7_blackout_windows(self) -> None:
        mt5 = FakeMt5()
        runner = DemoOnlyRsiquiMt5Runner(
            Mt5DemoConfig(symbol="XAUUSD", blocked_entry_hours_gmt7=(5, 6, 7, 20, 21)),
            mt5=mt5,
        )
        gmt7 = timezone(timedelta(hours=7))
        for local_time in (datetime(2026, 1, 2, 5, 0, tzinfo=gmt7), datetime(2026, 1, 2, 20, 0, tzinfo=gmt7)):
            with self.subTest(local_time=local_time):
                signal_bar = int(local_time.timestamp()) - runner._seconds_per_bar() + 1
                runner.evaluate_preclose_bar = lambda bar_time, t=signal_bar: ("long", t)
                self.assertTrue(runner.start())
                self.assertFalse(runner.poll_once(datetime.fromtimestamp(signal_bar + runner._seconds_per_bar() - 4, tz=timezone.utc)))
                self.assertEqual([], mt5.order_requests)
                self.assertIn("GMT+7 blackout", runner.last_status)

    def test_uses_next_entry_bar_time_for_gmt7_blackout_boundary(self) -> None:
        runner = DemoOnlyRsiquiMt5Runner(
            Mt5DemoConfig(symbol="XAUUSD", timeframe="M5", blocked_entry_hours_gmt7=(5, 6, 7, 20, 21)),
            mt5=FakeMt5(),
        )
        gmt7 = timezone(timedelta(hours=7))
        self.assertTrue(runner._is_gmt7_entry_blackout(int(datetime(2026, 1, 2, 5, 0, tzinfo=gmt7).timestamp()) - runner._seconds_per_bar() + 1))
        self.assertFalse(runner._is_gmt7_entry_blackout(int(datetime(2026, 1, 2, 8, 0, tzinfo=gmt7).timestamp()) - runner._seconds_per_bar() + 1))

    def test_blocks_when_open_position_cap_is_reached(self) -> None:
        mt5 = FakeMt5()
        mt5.positions = (object(), object(), object())
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(symbol="XAUUSD", max_open_positions=3), mt5=mt5)
        runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)

        self.assertTrue(runner.start())
        self.assertFalse(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc)))
        self.assertEqual([], mt5.order_requests)
        self.assertIn("waiting:", runner.last_status)
        self.assertIn("cap 3", runner.last_status)

    def test_run_forever_keeps_polling_while_position_cap_is_reached(self) -> None:
        mt5 = FakeMt5()
        mt5.positions = (object(), object(), object())
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(symbol="XAUUSD", max_open_positions=3, poll_seconds=0.01), mt5=mt5)

        sleep_calls: list[float] = []
        original_sleep = ori_demo.time.sleep

        def fake_sleep(seconds: float) -> None:
            sleep_calls.append(seconds)
            if len(sleep_calls) >= 2:
                raise KeyboardInterrupt("stop test loop")

        ori_demo.time.sleep = fake_sleep
        try:
            with self.assertRaises(KeyboardInterrupt):
                runner.run_forever()
        finally:
            ori_demo.time.sleep = original_sleep

        self.assertGreaterEqual(len(sleep_calls), 2)
        self.assertEqual([], mt5.order_requests)

    def test_submits_one_risk_capped_order_for_new_preclose_signal(self) -> None:
        mt5 = FakeMt5()
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(symbol="XAUUSD", volume_lots=0.01), mt5=mt5)
        runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)

        self.assertTrue(runner.start())
        self.assertTrue(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc)))
        self.assertEqual(1, len(mt5.order_requests))
        request = mt5.order_requests[0]
        self.assertEqual(mt5.ORDER_TYPE_BUY, request["type"])
        self.assertEqual(mt5.ORDER_FILLING_FOK, request["type_filling"])
        self.assertLess(request["sl"], request["price"])
        self.assertGreater(request["tp"], request["price"])
        self.assertLessEqual(request["volume"], 0.01)
        self.assertIn("pre-close bar", runner.last_status)

    def test_notifies_only_broker_fill_with_order_levels(self) -> None:
        mt5 = FakeMt5()
        notifier = FakeNotifier()
        runner = DemoOnlyRsiquiMt5Runner(
            Mt5DemoConfig(symbol="XAUUSD", volume_lots=0.01, telegram_enabled=True),
            mt5=mt5,
            notifier=notifier,
        )
        runner.evaluate_preclose_bar = lambda bar_time: ("short", bar_time)

        self.assertTrue(runner.start())
        self.assertTrue(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc)))
        self.assertEqual(1, len(notifier.messages))
        filled_message = notifier.messages[0]
        self.assertNotIn("✅ LỆNH ĐÃ KHỚP", filled_message)
        self.assertIn("SHORT", filled_message)
        self.assertIn("Entry: 2300.00", filled_message)
        self.assertIn("SL: 2310.00", filled_message)
        self.assertIn("TP: 2280.00", filled_message)
        self.assertNotIn("RSIQUI V3", filled_message)
        self.assertNotIn("LỆNH DEMO", filled_message)
        self.assertNotIn("Volume", filled_message)
        self.assertNotIn("Ticket", filled_message)

    def test_notifies_only_one_fill_for_a_preclose_bar(self) -> None:
        mt5 = FakeMt5()
        notifier = FakeNotifier()
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(symbol="XAUUSD", volume_lots=0.01), mt5=mt5, notifier=notifier)
        runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)

        self.assertTrue(runner.start())
        runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc))
        runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc))
        self.assertEqual(1, len(notifier.messages))  # one fill alert; duplicate bar is blocked
        self.assertNotIn("✅ LỆNH ĐÃ KHỚP", notifier.messages[0])
        self.assertIn("XAUUSD | M5 | LONG 🟢", notifier.messages[0])

    def test_telegram_formatter_never_contains_a_token(self) -> None:
        message = format_filled_order_message(
            symbol="XAUUSD",
            side="long",
            request={"price": 2300.1, "sl": 2290.1, "tp": 2310.1, "volume": 0.01},
            ticket=123,
            timeframe="M5",
        )
        self.assertNotIn("TELEGRAM_BOT_TOKEN", message)
        self.assertIn("LONG", message)

        signal_message = format_signal_message(
            symbol="XAUUSD",
            side="short",
            request={"price": 2300.0, "sl": 2310.0, "tp": 2290.0, "volume": 0.01},
            timeframe="M5",
        )
        self.assertNotIn("TELEGRAM_BOT_TOKEN", signal_message)
        self.assertNotIn("Trạng thái: đang gửi lệnh MT5", signal_message)

    def test_disabled_telegram_never_attempts_network(self) -> None:
        calls: list[object] = []

        def opener(*args, **kwargs):
            calls.append((args, kwargs))
            raise AssertionError("network must not be called")

        notifier = TelegramNotifier(TelegramSettings(enabled=False), opener=opener)
        self.assertFalse(notifier.send("test"))
        self.assertEqual([], calls)

    def test_telegram_sends_to_the_configured_forum_topic(self) -> None:
        captured: dict[str, object] = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, *, timeout):
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return FakeResponse()

        notifier = TelegramNotifier(
            TelegramSettings(enabled=True, bot_token="test-token", chat_id="-100123", message_thread_id=42),
            opener=opener,
        )
        self.assertTrue(notifier.send("topic message"))
        self.assertEqual("-100123", captured["payload"]["chat_id"])
        self.assertEqual(42, captured["payload"]["message_thread_id"])

    def test_rate_limits_repetitive_status_logs_to_five_minutes(self) -> None:
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(status_log_interval_seconds=300), mt5=FakeMt5())
        runner.last_status = "no pre-close RSIQUI V3 signal"

        self.assertTrue(runner.should_print_status(now=0))
        self.assertFalse(runner.should_print_status(now=299.9))
        self.assertTrue(runner.should_print_status(now=300))

    def test_prints_filled_order_status_without_waiting_for_rate_limit(self) -> None:
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(status_log_interval_seconds=300), mt5=FakeMt5())
        runner.last_status = "no pre-close RSIQUI V3 signal"
        self.assertTrue(runner.should_print_status(now=0))
        runner.last_status = "order filled: long ticket 123 on M5 pre-close bar 1700100000"
        self.assertTrue(runner.should_print_status(now=1))

    def test_caps_trade_risk_at_quarter_percent_of_demo_equity(self) -> None:
        mt5 = FakeMt5(equity=1_000.0)
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(symbol="XAUUSD"), mt5=mt5)
        runner.evaluate_preclose_bar = lambda bar_time: ("long", bar_time)

        runner.start()
        self.assertTrue(runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc)))
        request = mt5.order_requests[0]
        self.assertAlmostEqual(2.5, request["price"] - request["sl"], places=2)

    def test_does_not_submit_duplicate_order_for_same_preclose_bar(self) -> None:
        mt5 = FakeMt5()
        runner = DemoOnlyRsiquiMt5Runner(Mt5DemoConfig(symbol="XAUUSD"), mt5=mt5)
        runner.evaluate_preclose_bar = lambda bar_time: ("short", bar_time)

        runner.start()
        runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc))
        runner.poll_once(datetime(2026, 1, 2, 0, 4, 56, tzinfo=timezone.utc))

        self.assertEqual(1, len(mt5.order_requests))


if __name__ == "__main__":
    unittest.main()
