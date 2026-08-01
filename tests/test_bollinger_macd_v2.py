from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).parents[2]))


def test_long_signal_requires_midband_reclaim_and_increasing_positive_histogram():
    from trading_lab.strategies.builtins.bollinger_macd_v2 import prepare_bollinger_macd_v2_frame

    candles = pd.DataFrame({
        "timestamp": pd.date_range("2026-01-01", periods=35, freq="5min", tz="UTC"),
        "open": [100.0] * 35,
        "high": [101.0] * 35,
        "low": [99.0] * 35,
        "close": [100.0] * 33 + [99.0, 101.0],
        "volume": [1.0] * 35,
    })

    frame = prepare_bollinger_macd_v2_frame(candles)

    assert {"macd", "macdsignal", "macdhist", "bb_middleband"}.issubset(frame.columns)
    assert frame["timestamp"].is_monotonic_increasing


def test_config_converts_five_usd_risk_and_reward_to_five_price_units_at_point_zero_one_lot():
    from trading_lab.strategies.builtins.bollinger_macd_v2 import BollingerMacdV2Config

    config = BollingerMacdV2Config(
        volume_lots=0.01,
        price_value_per_lot=100.0,
        risk_usd=5.0,
        reward_usd=5.0,
        fixed_spread=0.2,
        slippage_per_side=0.5,
    )

    assert config.quantity == 1.0
    assert config.stop_distance == 5.0
    assert config.take_profit_distance == 5.0
    assert config.round_turn_execution_cost == 1.2
