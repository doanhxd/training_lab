from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from trading_lab.strategies.builtins import rsiqui_v3_root as root
from trading_lab.strategies.builtins.rsiqui import btcusd, final, neg, ori


VARIANTS = (final, ori, neg, btcusd)


def sample_frame() -> pd.DataFrame:
    index = np.arange(180, dtype=float)
    close = 100 + np.sin(index / 6.0) * 5 + np.cos(index / 17.0) * 2
    timestamps = pd.date_range("2026-08-01T00:00:00Z", periods=len(close), freq="5min")
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": close - 0.2,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 100,
            "spread": 0.1,
        }
    )


class RsiquiRootParityTests(unittest.TestCase):
    def test_variants_reuse_root_prepare_and_signal_functions(self) -> None:
        for module in VARIANTS:
            with self.subTest(module=module.__name__):
                self.assertIs(root.prepare_rsiqui_v3_frame, module.prepare_rsiqui_v3_frame)
                self.assertIs(root.evaluate_rsiqui_v3_signal, module.evaluate_rsiqui_v3_signal)

    def test_variant_signals_match_root_for_all_presets(self) -> None:
        frame = sample_frame()
        for preset in ("original", "gold-balanced", "gold-loose"):
            root_config = root.rsiqui_v3_config_for_preset(preset)
            root_prepared = root.prepare_rsiqui_v3_frame(frame, root_config)
            root_signals = [root.evaluate_rsiqui_v3_signal(row, root_config) for _, row in root_prepared.iterrows()]
            for module in VARIANTS:
                with self.subTest(preset=preset, module=module.__name__):
                    config = module.rsiqui_v3_config_for_preset(preset)
                    prepared = module.prepare_rsiqui_v3_frame(frame, config)
                    signals = [module.evaluate_rsiqui_v3_signal(row, config) for _, row in prepared.iterrows()]
                    self.assertEqual(root_signals, signals)

    def test_ori_and_neg_keep_blackout_as_execution_only_field(self) -> None:
        for module in (ori, neg):
            with self.subTest(module=module.__name__):
                config = module.rsiqui_v3_config_for_preset("gold-loose", blocked_entry_hours_gmt7=(5, 6, 7, 20, 21))
                root_config = root.rsiqui_v3_config_for_preset("gold-loose")
                self.assertEqual((5, 6, 7, 20, 21), config.blocked_entry_hours_gmt7)
                self.assertEqual(root_config.rsi_entry_long, config.rsi_entry_long)
                self.assertEqual(root_config.rsi_entry_short, config.rsi_entry_short)
                self.assertEqual(root_config.gradient_periods, config.gradient_periods)


if __name__ == "__main__":
    unittest.main()
