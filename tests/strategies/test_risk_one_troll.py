from __future__ import annotations

import unittest

import pandas as pd

from training_lab.strategies.builtins.risk_one_troll import (
    RiskOneTrollConfig,
    backtest_risk_one_troll,
    prepare_m5_with_previous_h1_direction,
)


class RiskOneTrollTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = RiskOneTrollConfig(
            sl_price_distance=10,
            tp_price_distance=10,
            commission_per_trade_usd=0,
        )

    def h1(self) -> pd.DataFrame:
        return pd.DataFrame({
            "timestamp": pd.to_datetime(["2025-12-31 21:00Z", "2025-12-31 22:00Z", "2025-12-31 23:00Z", "2026-01-01 00:00Z"]),
            "open": [100, 100, 100, 100], "high": [101, 101, 112, 112], "low": [99, 99, 99, 99], "close": [100, 100, 110, 110],
        })

    def m5(self, rows: list[dict]) -> pd.DataFrame:
        return pd.DataFrame(rows)

    def test_previous_closed_h1_is_causal(self) -> None:
        m5 = self.m5([{"timestamp": "2026-01-01 01:00Z", "open": 110, "high": 111, "low": 109, "close": 110}])
        enriched = prepare_m5_with_previous_h1_direction(m5, self.h1(), self.config)
        self.assertEqual("long", enriched.iloc[0]["h1_direction"])
        altered = self.h1().copy()
        altered.loc[altered.index == 1, "close"] = 50
        altered_enriched = prepare_m5_with_previous_h1_direction(m5, altered, self.config)
        self.assertEqual("long", altered_enriched.iloc[0]["h1_direction"])

    def test_sl_reverses_and_tp_continues(self) -> None:
        rows = [
            {"timestamp": "2026-01-01 01:00Z", "open": 110, "high": 111, "low": 99, "close": 105},  # SL long
            {"timestamp": "2026-01-01 01:05Z", "open": 100, "high": 111, "low": 99, "close": 108},  # SL short
            {"timestamp": "2026-01-01 01:10Z", "open": 90, "high": 111, "low": 89, "close": 99},   # SL short
            {"timestamp": "2026-01-01 01:15Z", "open": 90, "high": 100.5, "low": 89, "close": 99},   # TP long
            {"timestamp": "2026-01-01 01:20Z", "open": 100, "high": 111, "low": 99, "close": 105},
            {"timestamp": "2026-01-01 01:25Z", "open": 100, "high": 111, "low": 99, "close": 105},
        ]
        trades = backtest_risk_one_troll(self.m5(rows), self.h1(), self.config)
        self.assertEqual(["long", "short", "long"], [t.side for t in trades])
        self.assertEqual(["sl", "sl", "tp"], [t.exit_reason for t in trades])

    def test_session_cutoff_closes_and_prevents_overnight(self) -> None:
        rows = [
            {"timestamp": "2026-01-01 12:55Z", "open": 110, "high": 111, "low": 109, "close": 110},  # 19:55 GMT+7
            {"timestamp": "2026-01-01 13:00Z", "open": 110, "high": 111, "low": 109, "close": 110},  # 20:00 cutoff
            {"timestamp": "2026-01-01 13:05Z", "open": 110, "high": 111, "low": 109, "close": 110},
        ]
        trades = backtest_risk_one_troll(self.m5(rows), self.h1(), self.config)
        self.assertEqual(1, len(trades))
        self.assertEqual("session_cutoff", trades[0].exit_reason)


if __name__ == "__main__":
    unittest.main()
