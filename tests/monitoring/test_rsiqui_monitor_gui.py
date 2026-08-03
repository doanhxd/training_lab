from __future__ import annotations

from pathlib import Path
import inspect
import unittest

from trading_lab.monitoring.rsiqui.monitor_gui import RsiquiV3MonitorApp, load_read_only_profile
from trading_lab.monitoring.rsiqui.position_monitor import RunnerView


class RsiquiV3MonitorGuiContractTests(unittest.TestCase):
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

    def test_read_only_profile_loader_supports_ori_neg_final_and_btcusd_variants(self) -> None:
        ori = load_read_only_profile("configs/strategies/rsiqui/ori_m5_demo.json")
        neg = load_read_only_profile("configs/strategies/rsiqui/neg_m5_demo.json")
        final = load_read_only_profile("configs/strategies/rsiqui/final_m5_demo.json")
        btcusd = load_read_only_profile("configs/strategies/rsiqui/btcusd_m5_demo.json")

        self.assertEqual("rsiqui_v3_ori", ori["strategy_key"])
        self.assertEqual("rsiqui_v3_neg", neg["strategy_key"])
        self.assertEqual("rsiqui_v3_final", final["strategy_key"])
        self.assertEqual("rsiqui_v3_btcusd", btcusd["strategy_key"])
        self.assertEqual(0.01, ori["volume"])
        self.assertEqual(20.0, neg["risk_usd"])
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

    def test_bot_status_summary_distinguishes_running_and_stopped(self) -> None:
        running = RsiquiV3MonitorApp._summarize_bot_status(
            "rsiqui_v3_final",
            True,
            (RunnerView(strategy_key="rsiqui_v3_final", label="rsiqui_v3_final", pid=4321, command="python runners/mt5/rsiqui_final_demo.py"),),
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
        self.assertEqual("trading_lab.runners.mt5.rsiqui_final_demo", command[2])
        self.assertEqual("--config", command[3])
        self.assertTrue(command[4].endswith("final_m5_demo.json"))
        self.assertEqual("trading_lab.runners.mt5.rsiqui_btcusd_demo", btcusd_command[2])
        self.assertTrue(btcusd_command[4].endswith("btcusd_m5_demo.json"))

    def test_monitor_defaults_to_final_config_and_wider_log_column(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn('default=str(CONFIG_ROOT / "final_m5_demo.json")', source)
        self.assertIn('profile.get("strategy_key", "rsiqui_v3_final")', source)
        self.assertIn('body.grid_columnconfigure(0, weight=5, uniform="main")', source)
        self.assertIn('body.grid_columnconfigure(1, weight=6, uniform="main")', source)
        self.assertIn('message_wrap = max(560, self._log_canvas.winfo_width() - 170)', source)

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
        self.assertIn('TỪ GIỜ (GMT+7)', source)
        self.assertIn('ĐẾN GIỜ (GMT+7)', source)
        self.assertIn("_filter_history_deals_by_time_gmt7", source)
        self.assertIn("deal.time.replace(tzinfo=None).time()", source)
        self.assertIn("def _history_display_symbol", source)
        self.assertIn('return "XAUUSD" if upper.startswith("XAUUSD") else upper', source)
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

    def test_monitor_source_mentions_strategy_selector_without_trade_or_telegram_actions(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn("class UiPalette", source)
        self.assertIn("rsiqui_v3_ori", source)
        self.assertIn("rsiqui_v3_neg", source)
        self.assertIn("rsiqui_v3_final", source)
        self.assertIn("rsiqui_v3_btcusd", source)
        self.assertNotIn("CHẠY BOT", source)
        self.assertIn("BẢNG LOG TÍN HIỆU", source)
        self.assertIn("LỆNH XAU/BTC", source)
        self.assertIn("Tất cả vị thế XAUUSD / BTCUSD • read-only", source)
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
        self.assertNotIn("GỬI KÈO TELEGRAM", source)
        self.assertNotIn("TELEGRAM TẠM TẮT", source)
        self.assertNotIn("TelegramNotifier", source)
        self.assertNotIn("TelegramSettings", source)
        self.assertNotIn("order_send(", source)
        self.assertNotIn("TRADE_ACTION_DEAL", source)
        self.assertNotIn("login(", source)
        self.assertNotIn("ĐÓNG LỆNH", source)
        self.assertNotIn("CHẾ ĐỘ AN TOÀN", source)


if __name__ == "__main__":
    unittest.main()
