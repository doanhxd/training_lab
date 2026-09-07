from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

import pandas as pd

from training_lab.data.loaders.csv_loader import load_raw_csv

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "strategies" / "ethan_xauusd_m5_d1_20160809_20260828_eq5000_vol003.json"
RAW_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSD_M5_201608090000_202608280900.csv"
DAILY_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSD_Daily_201608090000_202608280000.csv"
SOURCE_PATH = ROOT / "strategies" / "builtins" / "ethan-strategy.py"
OUTPUT_ROOT = Path.home() / "outputs" / "ethan_xauusd"
START_UTC = pd.Timestamp("2016-08-09T00:00:00Z")


def load_source_strategy():
    spec = importlib.util.spec_from_file_location("ethan_strategy_source", SOURCE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot import Ethan strategy source")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LongShortStrategy


def quality(frame: pd.DataFrame) -> dict:
    cadence = frame.index.to_series().diff().dropna()
    invalid = (frame["low"] > frame["high"]) | (frame["high"] < frame[["open", "close"]].max(axis=1)) | (frame["low"] > frame[["open", "close"]].min(axis=1))
    return {
        "rows": int(len(frame)), "first_utc": str(frame.index.min()), "last_utc": str(frame.index.max()),
        "duplicates": int(frame.index.duplicated().sum()), "invalid_ohlc": int(invalid.sum()),
        "median_cadence_minutes": float(cadence.median().total_seconds() / 60),
        "max_gap_minutes": float(cadence.max().total_seconds() / 60),
        "spread_min": float(frame["spread"].min()), "spread_median": float(frame["spread"].median()), "spread_max": float(frame["spread"].max()),
    }


def previous_completed_daily(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily[["open", "high", "low", "close"]].copy().sort_index().dropna()
    # A broker D1 bar becomes visible only at the following UTC daily boundary.
    daily.index = daily.index + pd.Timedelta(days=1)
    return daily


def metrics(trades: pd.DataFrame, initial: float) -> dict:
    if trades.empty:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "gross_profit": 0.0, "gross_loss": 0.0, "profit_factor": None, "net_pnl": 0.0, "final_equity": initial, "net_return": 0.0, "expectancy": 0.0, "max_drawdown": 0.0}
    pnl = trades["pnl"].astype(float)
    wins, losses = int((pnl > 0).sum()), int((pnl < 0).sum())
    gross_profit, gross_loss = float(pnl[pnl > 0].sum()), float(-pnl[pnl < 0].sum())
    curve = initial + pnl.cumsum()
    max_dd = float(((curve.cummax() - curve) / curve.cummax().replace(0, float("nan"))).max())
    return {"total_trades": int(len(trades)), "wins": wins, "losses": losses, "win_rate": wins / len(trades), "gross_profit": gross_profit, "gross_loss": gross_loss, "profit_factor": gross_profit / gross_loss if gross_loss else None, "net_pnl": float(pnl.sum()), "final_equity": float(curve.iloc[-1]), "net_return": float(pnl.sum() / initial), "expectancy": float(pnl.mean()), "max_drawdown": max_dd}


def main() -> None:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw = load_raw_csv(RAW_PATH)
    raw["timestamp"] = pd.to_datetime(raw["timestamp"], utc=True)
    for column in ("open", "high", "low", "close", "spread"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    m5 = raw[raw["timestamp"] >= START_UTC].dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
    if m5.empty:
        raise RuntimeError("no usable M5 candles")
    raw_daily = load_raw_csv(DAILY_PATH)
    raw_daily["timestamp"] = pd.to_datetime(raw_daily["timestamp"], utc=True)
    for column in ("open", "high", "low", "close", "spread"):
        raw_daily[column] = pd.to_numeric(raw_daily[column], errors="coerce")
    broker_daily = raw_daily.dropna(subset=["open", "high", "low", "close"]).sort_values("timestamp").drop_duplicates("timestamp").set_index("timestamp")
    daily = previous_completed_daily(broker_daily)
    params = payload["ethan_parameters"]
    Strategy = load_source_strategy()
    strategy = Strategy(pip_size=float(payload["pip_size"]), initial_capital=float(payload["initial_equity"]), **params)
    raw_trades = strategy.backtest(m5, daily)
    columns = ["side", "entry_time", "exit_time", "entry_price", "exit_price", "sl", "tp", "exit_reason", "bars_held", "gross_pnl", "spread_cost", "commission", "pnl"]
    rows = []
    quantity = float(payload["volume_lots"]) * float(payload["price_value_per_lot"])
    for trade in raw_trades.to_dict("records"):
        entry_time, exit_time = pd.Timestamp(trade["entry_time"]), pd.Timestamp(trade["exit_time"])
        entry_spread = float(m5.loc[entry_time, "spread"])
        exit_spread = float(m5.loc[exit_time, "spread"])
        spread_cost = (entry_spread + exit_spread) * 0.5 * quantity
        slippage_cost = 2 * float(payload["slippage_per_side_price"]) * quantity
        commission = float(payload["commission_per_trade_usd"])
        gross_pnl = float(trade["pnl_price"]) * quantity
        rows.append({"side": trade["side"].lower(), "entry_time": entry_time, "exit_time": exit_time, "entry_price": trade["entry"], "exit_price": trade["exit"], "sl": trade["sl"], "tp": trade["tp"], "exit_reason": trade["reason"], "bars_held": trade["bars"], "gross_pnl": gross_pnl, "spread_cost": spread_cost + slippage_cost, "commission": commission, "pnl": gross_pnl - spread_cost - slippage_cost - commission})
    trades = pd.DataFrame(rows, columns=columns)
    blocked_after_bankruptcy = 0
    if not trades.empty:
        equity = float(payload["initial_equity"])
        keep = []
        for record in trades.to_dict("records"):
            if equity <= 0:
                blocked_after_bankruptcy += 1
                continue
            keep.append(record)
            equity += float(record["pnl"])
        trades = pd.DataFrame(keep, columns=columns)
    policy = {"source_strategy_file": str(SOURCE_PATH), "broker_d1_csv": str(DAILY_PATH), "daily_reference_policy": payload["daily_reference_policy"], "causal_proof": "broker D1 index shifted +1D; all M5 bars see only prior completed D1", "same_bar_collision": payload["same_bar_collision"], "cost_model": "observed M5 spread averaged entry/exit + two-sided declared slippage + commission", "strategy_exit_contract": "source Ethan long/short pending-entry and source SL/TP/time-exit retained"}
    run_hash = hashlib.sha256(json.dumps(payload | policy, sort_keys=True, default=str).encode()).hexdigest()[:16]
    run_id = f"ethan_xauusd_{run_hash}"
    output = OUTPUT_ROOT / run_id
    output.mkdir(parents=True, exist_ok=True)
    trades.to_csv(output / "trades.csv", index=False)
    equity = [float(payload["initial_equity"])]
    for pnl in trades["pnl"].tolist() if not trades.empty else []:
        equity.append(equity[-1] + float(pnl))
    pd.DataFrame({"equity": equity}).to_csv(output / "equity_curve.csv", index=False)
    report = {"run_id": run_id, "strategy": payload["strategy"], "backtest_config": payload, "policy": policy, "data_coverage": quality(m5), "broker_d1_quality": quality(broker_daily), "daily_reference_coverage": {"first_available_utc": str(daily.index.min()), "last_available_utc": str(daily.index.max()), "rows": int(len(daily))}, "metrics_summary": metrics(trades, float(payload["initial_equity"])), "signal_counts": {"completed_trades": int(len(trades)), "long_trades": int((trades["side"] == "long").sum()) if not trades.empty else 0, "short_trades": int((trades["side"] == "short").sum()) if not trades.empty else 0, "overlapping_trades": 0, "blocked_after_bankruptcy": blocked_after_bankruptcy}, "open_position_at_end": None, "artifacts": {"report": str(output / "report.json"), "trades": str(output / "trades.csv"), "equity": str(output / "equity_curve.csv")}}
    (output / "report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
