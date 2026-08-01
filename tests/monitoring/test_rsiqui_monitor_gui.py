from __future__ import annotations

from pathlib import Path
import inspect
import unittest

from trading_lab.monitoring.rsiqui.monitor_gui import RsiquiV3MonitorApp, load_read_only_profile, load_telegram_targets
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

    def test_read_only_profile_loader_supports_ori_and_neg_variants(self) -> None:
        ori = load_read_only_profile("configs/strategies/rsiqui/ori_m5_demo.json")
        neg = load_read_only_profile("configs/strategies/rsiqui/neg_m5_demo.json")
        final = load_read_only_profile("configs/strategies/rsiqui/final_m5_demo.json")

        self.assertEqual("rsiqui_v3_ori", ori["strategy_key"])
        self.assertEqual("rsiqui_v3_neg", neg["strategy_key"])
        self.assertEqual("rsiqui_v3_final", final["strategy_key"])
        self.assertEqual(0.01, ori["volume"])
        self.assertEqual(20.0, neg["risk_usd"])
        self.assertEqual(0.03, final["volume"])

    def test_default_telegram_targets_expose_portfolio_managers_destination(self) -> None:
        targets = load_telegram_targets(path="configs/strategies/rsiqui/__missing_targets__.json")

        self.assertTrue(any(target.label == "Portfolio Managers" for target in targets))
        self.assertTrue(any(target.chat_id == "-5043082181" for target in targets))

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

        self.assertTrue(command[0])
        self.assertEqual("-m", command[1])
        self.assertEqual("trading_lab.runners.mt5.rsiqui_final_demo", command[2])
        self.assertEqual("--config", command[3])
        self.assertTrue(command[4].endswith("final_m5_demo.json"))

    def test_monitor_source_mentions_strategy_selector_and_telegram_toggle_without_trade_actions(self) -> None:
        source = Path("monitoring/rsiqui/monitor_gui.py").read_text(encoding="utf-8")

        self.assertIn("class UiPalette", source)
        self.assertIn("rsiqui_v3_ori", source)
        self.assertIn("rsiqui_v3_neg", source)
        self.assertIn("rsiqui_v3_final", source)
        self.assertIn("BOT STATUS", source)
        self.assertIn("LAST SIGNAL", source)
        self.assertIn("CHẠY BOT", source)
        self.assertIn("_run_selected_strategy", source)
        self.assertIn("TELEGRAM_TEMP_DISABLED = True", source)
        self.assertIn("TELEGRAM TẠM TẮT", source)
        self.assertIn("GỬI KÈO TELEGRAM", source)
        self.assertIn("BẢNG LOG TÍN HIỆU", source)
        self.assertNotIn("order_send(", source)
        self.assertNotIn("TRADE_ACTION_DEAL", source)
        self.assertNotIn("login(", source)
        self.assertNotIn("ĐÓNG LỆNH", source)


if __name__ == "__main__":
    unittest.main()
