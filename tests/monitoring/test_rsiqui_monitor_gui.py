from __future__ import annotations

from pathlib import Path
import json
import inspect
import unittest
from datetime import UTC, datetime

from trading_lab.monitoring.rsiqui.monitor_gui import (
    GMT_PLUS_7,
    RsiquiV3MonitorApp,
    format_telegram_signal_message,
    load_read_only_profile,
)
from trading_lab.monitoring.rsiqui.position_monitor import RunnerView


class RsiquiV3MonitorGuiContractTests(unittest.TestCase):
    def test_gui_timezone_is_gmt_plus_7_for_clock_and_timestamp_display(self) -> None:
        self.assertEqual(7 * 60 * 60, GMT_PLUS_7.utcoffset(None).total_seconds())
        utc_value = datetime(2026, 8, 5, 4, 45, 10, tzinfo=UTC)

        self.assertEqual("11:45:10", RsiquiV3MonitorApp._format_gmt7_timestamp(utc_value))
        self.assertEqual("2026-08-05 11:45:10", RsiquiV3MonitorApp._format_gmt7_datetime(utc_value))

    def test_palette_exposes_a_real_light_theme(self) -> None:
        from trading_lab.monitoring.rsiqui.monitor_gui import UiPalette

        light = UiPalette.for_mode("light")
        dark = UiPalette.for_mode("dark")
        self.assertEqual("light", light["mode"])
        self.assertEqual("#F5F7FB", light["app"])
        self.assertEqual("#F3F7FC", light["nav_text"])
        self.assertNotEqual(light["app"], dark["app"])

    def test_widget_helpers_resolve_theme_colours_at_runtime(self) -> None:
        label_signature = inspect.signature(RsiquiV3MonitorApp._label)
        card_signature = inspect.signature(RsiquiV3MonitorApp._card)

        self.assertIsNone(label_signature.parameters["fg"].default)
        self.assertIsNone(label_signature.parameters["bg"].default)
        self.assertIsNone(card_signature.parameters["bg"].default)

    def test_label_helper_allows_textvariable_only_labels(self) -> None:
        signature = inspect.signature(RsiquiV3MonitorApp._label)

        self.assertEqual("", signature.parameters["text"].default)

    def test_gui_aliases_broker_gold_suffix_and_cent_currency(self) -> None:
        self.assertEqual("XAUUSD", RsiquiV3MonitorApp._display_symbol("XAUUSDc"))
        self.assertEqual("XAUUSD", RsiquiV3MonitorApp._display_symbol("XAUUSDm"))
        self.assertEqual("USD", RsiquiV3MonitorApp._display_currency("USC"))
        self.assertEqual("USD", RsiquiV3MonitorApp._display_currency("USD"))

    def test_history_comment_trims_decimal_values_to_two_places_for_display(self) -> None:
        self.assertEqual("[tp 4163.30]", RsiquiV3MonitorApp._format_history_comment("[tp 4163.29700]"))
        self.assertEqual("[sl 4167.67 / tp 4165.78]", RsiquiV3MonitorApp._format_history_comment("[sl 4167.67300 / tp 4165.78000]"))

    def test_read_only_profile_loader_supports_final_trailing_final_and_btcusd_aliases(self) -> None:
        final = load_read_only_profile("configs/strategies/rsiqui/final_m5.json")
        final_tr = load_read_only_profile("configs/strategies/rsiqui/final_trailing_m5.json")
        final_x = load_read_only_profile("configs/strategies/rsiqui/final_x_m5.json")
        btcusd = load_read_only_profile("configs/strategies/rsiqui/btcusd_m5.json")

        self.assertEqual("rsiqui_v3_final", final["strategy_key"])
        self.assertEqual("rsiqui_v3_final_trailing", final_tr["strategy_key"])
        self.assertEqual("rsiqui_v3_final_x", final_x["strategy_key"])
        self.assertEqual("XAUUSDc", final_x["symbol"])
        self.assertEqual(0.10, final_x["volume"])
        self.assertEqual(100.0, final_x["risk_usd"])
        self.assertEqual(30.0, final_x["reward_usd"])
        self.assertEqual(0.3, final_x["max_spread"])
        self.assertEqual(5000.0, json.loads(Path("configs/strategies/rsiqui/final_x_m5.json").read_text(encoding="utf-8"))["initial_equity"])
        self.assertEqual("immediate_signal", final_tr["entry_mode"])
        self.assertEqual("rsiqui_v3_btcusd", btcusd["strategy_key"])
        self.assertEqual(0.03, final["volume"])
        self.assertEqual("BTCUSD", btcusd["symbol"])
        self.assertEqual(100.0, final["price_value_per_lot"])
        self.assertEqual(1.0, btcusd["price_value_per_lot"])

    def test_log_badge_classifier_supports_long_short_and_error(self) -> None:
        long_badge = RsiquiV3MonitorApp._classify_log_badge("RSIQUI BUY signal")
        short_badge = RsiquiV3MonitorApp._classify_log_badge("RSIQUI SELL signal")
        error_badge = RsiquiV3MonitorApp._classify_log_badge("Lỗi kết nối MT5")

        self.assertEqual("LONG", long_badge[0])
        self.assertEqual("SHORT", short_badge[0])
        self.assertEqual("ERROR", error_badge[0])

    def test_telegram_signal_message_uses_the_log_eta_and_trade_prices(self) -> None:
        message = format_telegram_signal_message(
            symbol="XAUUSD",
            timeframe="M5",
            eta="16:05",
            side="short",
            request={"price": 4071.53, "sl": 4076.53, "tp": 4066.53},
        )

        self.assertEqual(
            "XAUUSD | Next M5 (16:05) | SHORT 🔴\n"
            "Entry: 4071.53\n"
            "SL: 4076.53\n"
            "TP: 4066.53",
            message,
        )

    def test_blocked_signal_message_keeps_the_signal_payload_without_a_blocked_prefix(self) -> None:
        message = format_telegram_signal_message(
            symbol="XAUUSD",
            timeframe="M5",
            eta="16:05",
            side="long",
            request={"price": 4071.53, "sl": 4066.53, "tp": 4076.53},
            blocked=True,
        )

        self.assertEqual(
            "XAUUSD | Next M5 (16:05) | LONG 🟢\n"
            "Entry: 4071.53\n"
            "SL: 4066.53\n"
            "TP: 4076.53",
            message,
        )

    def test_confirmation_and_history_windows_use_shared_screen_centering(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn("def _center_window", source)
        self.assertIn("window.winfo_screenwidth()", source)
        self.assertIn("window.winfo_screenheight()", source)
        self.assertIn("self._center_window(window)", source)
        self.assertIn("self._center_window(popup)", source)

    def test_signal_logs_render_a_hoverable_tele_action_inside_long_short_badges(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")
        badge_start = source.index("    def _signal_badge")
        badge_block = source[badge_start:source.index("    def _confirm_telegram_signal", badge_start)]

        self.assertIn("def _signal_badge", source)
        self.assertIn("<Enter>", source)
        self.assertIn('text="TELE"', source)
        self.assertIn("def _confirm_telegram_signal", source)
        self.assertIn('text="Send"', source)
        self.assertIn('text="Cancel"', source)
        self.assertIn("telegram_message=telegram_message", source)
        self.assertIn("badge = self._badge(parent, entry.badge", badge_block)
        self.assertIn('badge.itemconfigure("badge_text", text="TELE")', badge_block)
        self.assertIn('badge.itemconfigure("badge_text", text=entry.badge)', badge_block)
        self.assertIn('tags=("badge_text",)', source)
        self.assertIn('tags=("badge_fill",)', source)
        self.assertIn('badge.itemconfigure("badge_fill", fill=UiPalette.TELEGRAM_BLUE, outline=UiPalette.TELEGRAM_BLUE)', badge_block)
        self.assertIn('badge.itemconfigure("badge_fill", fill=entry.badge_color, outline=entry.badge_color)', badge_block)
        self.assertNotIn('text="✈"', badge_block)
        self.assertNotIn("self._label(badge", badge_block)
        self.assertNotIn("tk.Button(", badge_block)

    def test_one_position_blocked_signal_keeps_long_short_badge_and_tele_action(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn('f"Blocked Signal {plan}', source)
        self.assertIn("blocked=True", source)
        self.assertIn("telegram_message=blocked_telegram_message", source)
        self.assertIn("badge=plan", source)

    def test_bot_status_summary_distinguishes_running_and_stopped(self) -> None:
        running = RsiquiV3MonitorApp._summarize_bot_status(
            "rsiqui_v3_final",
            True,
            (RunnerView(strategy_key="rsiqui_v3_final", label="rsiqui_v3_final", pid=4321, command="python runners/mt5/rsiqui_final.py"),),
        )
        stopped = RsiquiV3MonitorApp._summarize_bot_status("rsiqui_v3_final", True, ())

        self.assertEqual("RUNNING", running[0])
        self.assertIn("PID 4321", running[2])
        self.assertEqual("STOPPED", stopped[0])
        self.assertIn("rsiqui_v3_final", stopped[2])

    def test_runner_command_points_to_expected_script_and_config(self) -> None:
        command = RsiquiV3MonitorApp._build_runner_command("rsiqui_v3_final")
        btcusd_command = RsiquiV3MonitorApp._build_runner_command("rsiqui_v3_btcusd")

        self.assertTrue(command[0])
        self.assertEqual("-m", command[1])
        self.assertEqual("trading_lab.runners.mt5.rsiqui_final", command[2])
        self.assertEqual("--config", command[3])
        self.assertTrue(command[4].endswith("final_m5.json"))
        self.assertEqual("trading_lab.runners.mt5.rsiqui_btcusd", btcusd_command[2])
        self.assertTrue(btcusd_command[4].endswith("btcusd_m5.json"))

    def test_monitor_defaults_to_final_config_and_wider_log_column(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn('default=str(CONFIG_ROOT / "final_trailing_m5.json")', source)
        self.assertIn('profile.get("strategy_key", "rsiqui_v3_final_trailing")', source)
        self.assertIn('body.grid_columnconfigure(0, weight=5, uniform="main")', source)
        self.assertIn('body.grid_columnconfigure(1, weight=6, uniform="main")', source)
        self.assertIn('message_wrap = max(560, self._log_canvas.winfo_width() - 170)', source)

    def test_settings_fit_profile_timeframe_volume_sl_tp_in_one_row(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        settings_start = source.index('strategy_card = self._card(content, padding=16)')
        settings_block = source[settings_start:source.index('body = tk.Frame(content, bg=UiPalette.APP)', settings_start)]
        self.assertIn('for column in range(5):', settings_block)
        self.assertIn('uniform="settings"', settings_block)
        self.assertIn('"PROFILE THEO DÕI"', settings_block)
        self.assertIn('"TIMEFRAME", self._timeframe_value)', settings_block)
        self.assertNotIn('"TIMEFRAME", self._timeframe_value, state="readonly"', settings_block)
        self.assertIn('"VOLUME LOT", self._volume_value)', settings_block)
        self.assertIn('"SL USD", self._risk_value)', settings_block)
        self.assertIn('"TP USD", self._reward_value)', settings_block)
        self.assertNotIn('"SIDE", self._side_value)', settings_block)

    def test_signal_preview_uses_profile_price_value_and_blocks_when_position_open(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn('"price_value_per_lot": float(payload.get("price_value_per_lot", 100.0))', source)
        self.assertIn('price_value_per_lot=float(profile.get("price_value_per_lot", 100.0))', source)
        self.assertIn("def _has_open_position_for_selected_symbol", source)
        self.assertIn("plan {plan} blocked because", source)
        self.assertIn("self._has_open_position_for_selected_symbol()", source)

    def test_history_tab_contract_has_nav_filters_stats_and_read_only_history(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn("LỊCH SỬ LỆNH", source)
        self.assertIn("command=self._show_monitor_page", source)
        self.assertIn("command=self._open_history_window", source)
        self.assertIn("history_deals_get", source)
        self.assertIn("KHOẢNG THỜI GIAN", source)
        self.assertIn("MÃ GIAO DỊCH", source)
        self.assertIn("BTCUSD", source)
        self.assertIn("_filter_history_deals_by_symbol", source)
        self.assertIn('self._history_start_time_value = tk.StringVar(value="08:00")', source)
        self.assertIn('self._history_end_time_value = tk.StringVar(value="23:59")', source)
        self.assertIn('text="TỪ GIỜ"', source)
        self.assertIn('text="ĐẾN GIỜ"', source)
        self.assertIn("_filter_history_deals_by_time_gmt7", source)
        self.assertIn("deal.time.replace(tzinfo=None).time()", source)
        self.assertIn("def _history_display_symbol", source)
        self.assertIn('if upper.startswith("XAUUSD"):', source)
        self.assertIn('if upper.startswith("BTCUSD"):', source)
        self.assertIn('return "BTCUSD"', source)
        self.assertIn("plan {plan} blocked", source)
        self.assertIn("one-position guard", source)
        self.assertIn('if mode == "hôm nay":', source)
        self.assertIn('"Hôm nay"', source)
        self.assertIn("7 ngày qua", source)
        self.assertIn("30 ngày qua", source)
        self.assertIn("90 ngày qua", source)
        self.assertIn("1 năm qua", source)
        self.assertIn("Tùy chỉnh", source)
        self.assertIn("START DATE", source)
        self.assertIn("END DATE", source)
        self.assertIn("_set_custom_history_controls_visible", source)
        self.assertIn("MAX DD NGÀY", source)
        self.assertIn("WINRATE", source)
        self.assertIn("NET P/L", source)

    def test_flr_history_filter_is_today_gmt7_profit_only_and_recomputes_stats(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn('text="RESET LỌC"', source)
        self.assertIn("command=self._toggle_flr_filter", source)
        self.assertIn('self._history_filter_value.set("Hôm nay")', source)
        self.assertIn('self._history_start_time_value.set("08:00")', source)
        self.assertIn('self._history_end_time_value.set("23:59")', source)
        self.assertIn("self._history_flr_enabled", source)
        self.assertIn('float(getattr(deal, "net_profit", 0.0) or 0.0) > 0.0', source)
        self.assertIn("stats = RsiquiV3PositionMonitor.history_stats(deals, raw_deals=stats.raw_deals)", source)

    def test_monitor_source_keeps_order_submission_out_of_the_dashboard(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn("class UiPalette", source)
        self.assertIn("rsiqui_v3_final", source)
        self.assertIn("rsiqui_v3_btcusd", source)
        self.assertIn('"F Root"', source)
        self.assertIn('"F TRL"', source)
        self.assertIn('"BTC"', source)
        self.assertNotIn("CHẠY BOT", source)
        self.assertIn("BẢNG LOG TÍN HIỆU", source)
        self.assertIn("LỆNH XAU/BTC", source)
        self.assertIn("Tất cả vị thế XAU / BTC • read-only", source)
        self.assertIn('("time", 62, "TIME")', source)
        self.assertIn('self._position_day_value', source)
        self.assertIn('upper.startswith("BTC")', source)
        self.assertIn('upper.startswith("XAU")', source)
        self.assertNotIn('"TICKET"', source)

        self.assertIn('f"ONLINE • {snapshot.server}"', source)
        self.assertNotIn("TRẠNG THÁI HỆ THỐNG", source)
        self.assertNotIn('text="BOT STATUS"', source)
        self.assertNotIn('text="LAST CHECK"', source)
        self.assertNotIn('text="LAST SIGNAL"', source)
        self.assertNotIn("order_send(", source)
        self.assertNotIn("TRADE_ACTION_DEAL", source)
        self.assertNotIn("login(", source)
        self.assertNotIn("ĐÓNG LỆNH", source)
        self.assertNotIn("CHẾ ĐỘ AN TOÀN", source)


if __name__ == "__main__":
    unittest.main()
