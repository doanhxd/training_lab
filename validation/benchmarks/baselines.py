from __future__ import annotations

import pandas as pd


def buy_hold_return(features: pd.DataFrame) -> float:
    if len(features) < 2 or features.iloc[0]["close"] == 0:
        return 0.0
    return float((features.iloc[-1]["close"] - features.iloc[0]["close"]) / features.iloc[0]["close"])


def no_trade_return(_: pd.DataFrame) -> float:
    return 0.0


def sma_trend_baseline_return(features: pd.DataFrame) -> float:
    if len(features) < 2 or "sma_20" not in features:
        return 0.0
    returns = features["close"].pct_change().fillna(0.0)
    signal = (features["close"].shift(1) > features["sma_20"].shift(1)).fillna(False).astype(float)
    return float((signal * returns).sum())


def benchmark_summary(features: pd.DataFrame) -> dict[str, float]:
    return {
        "buy_hold_return": buy_hold_return(features),
        "sma_trend_return": sma_trend_baseline_return(features),
        "no_trade_return": no_trade_return(features),
    }
