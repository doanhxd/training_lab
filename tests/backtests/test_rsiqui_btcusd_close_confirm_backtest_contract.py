from __future__ import annotations

from pathlib import Path
import json
import unittest

import pandas as pd

from trading_lab.scripts import run_rsiqui_btcusd_20260101_m5_backtest as btc2026


ROOT = Path(__file__).resolve().parents[2]
BACKTEST_SCRIPTS = sorted((ROOT / "scripts").glob("run_rsiqui_btcusd_*_m5_backtest.py"))


class RsiquiBtcusdBacktestCloseConfirmContractTests(unittest.TestCase):
    def test_close_confirm_helper_requires_preview_and_confirmation_match(self) -> None:
        row = pd.Series({"timestamp": pd.Timestamp("2026-01-01T00:00:00Z")})
        original = btc2026.evaluate_rsiqui_v3_signal
        calls: list[str | None] = []

        def fake_signal(_row, _config):
            return calls.pop(0)

        btc2026.evaluate_rsiqui_v3_signal = fake_signal
        try:
            calls[:] = ["long", "long"]
            matched = btc2026.evaluate_close_confirm_entry_condition(row, object())
            self.assertEqual("long", matched["preview_side"])
            self.assertEqual("long", matched["confirmed_side"])
            self.assertEqual("long", matched["matched_side"])
            self.assertEqual(pd.Timestamp("2026-01-01T00:05:00Z"), matched["entry_time"])

            calls[:] = ["short", "long"]
            mismatch = btc2026.evaluate_close_confirm_entry_condition(row, object())
            self.assertEqual("short", mismatch["preview_side"])
            self.assertEqual("long", mismatch["confirmed_side"])
            self.assertIsNone(mismatch["matched_side"])

            calls[:] = [None, "long"]
            no_preview = btc2026.evaluate_close_confirm_entry_condition(row, object())
            self.assertIsNone(no_preview["preview_side"])
            self.assertEqual("long", no_preview["confirmed_side"])
            self.assertIsNone(no_preview["matched_side"])
        finally:
            btc2026.evaluate_rsiqui_v3_signal = original

    def test_all_btcusd_backtest_scripts_record_entry_condition_contract(self) -> None:
        self.assertGreaterEqual(len(BACKTEST_SCRIPTS), 5)
        for script in BACKTEST_SCRIPTS:
            with self.subTest(script=script.name):
                source = script.read_text(encoding="utf-8")
                self.assertIn("def evaluate_close_confirm_entry_condition", source)
                self.assertIn('preview_side = evaluate_rsiqui_v3_signal(row, config)', source)
                self.assertIn('confirmed_side = evaluate_rsiqui_v3_signal(row, config)', source)
                self.assertIn('matched_side = preview_side if preview_side is not None and preview_side == confirmed_side else None', source)
                self.assertIn('"blocked_no_matching_close_confirm": 0', source)
                self.assertIn('"entry_condition_check": "enforced:', source)
                self.assertNotIn("MetaTrader5", source)
                self.assertNotIn("order_send", source)
                self.assertNotIn(".initialize(", source)

    def test_negative_r_config_is_separate_btcusd_contract(self) -> None:
        base = json.loads((ROOT / "configs/strategies/rsiqui/btcusd_m5_demo.json").read_text(encoding="utf-8"))
        negative = json.loads((ROOT / "configs/strategies/rsiqui/btcusd_negative_r_m5_demo.json").read_text(encoding="utf-8"))
        script = (ROOT / "scripts/run_rsiqui_btcusd_negative_r_20260101_m5_backtest.py").read_text(encoding="utf-8")

        self.assertEqual("BTCUSD", negative["symbol"])
        self.assertEqual("rsiqui-v3-btcusd", negative["strategy"])
        self.assertEqual(0.13, negative["volume"])
        self.assertEqual(1, negative["price_value_per_lot"])
        self.assertEqual(15, negative["risk_usd"])
        self.assertEqual(5, negative["reward_usd"])
        self.assertEqual(10.0, negative["max_spread"])
        self.assertEqual(10, base["risk_usd"])
        self.assertEqual(10, base["reward_usd"])
        self.assertIn("btcusd_negative_r_m5_demo.json", script)
        self.assertIn("negativeR_closeconfirm", script)


if __name__ == "__main__":
    unittest.main()
