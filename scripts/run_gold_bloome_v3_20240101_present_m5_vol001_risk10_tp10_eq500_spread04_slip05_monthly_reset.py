from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.data.loaders.csv_loader import load_raw_csv
from training_lab.data.transforms.normalize import normalize_candles, write_processed_dataset
from training_lab.models import TradeRecord
from training_lab.storage.artifacts import (
    build_leaderboard,
    persist_leaderboard,
    persist_run_artifacts,
    run_id_for,
)
from training_lab.strategies.builtins.gold_bloome_v3 import calc_indicators

RAW_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSD_M5_202401012300_202607312345.csv"
CONFIG_PATH = ROOT / "configs" / "strategies" / "gold_bloome_v3_m5_20240101_present_vol001_risk10_tp10_eq500_spread04_slip05_monthly_reset.json"
SOURCE_STRATEGY = ROOT / "strategies" / "builtins" / "gold_bloome_v3.py"
SYMBOL = "XAUUSD"
TIMEFRAME = "M5"
REQUESTED_START = pd.Timestamp("2024-01-01T00:00:00Z")


def monthly_rows(trades: list[TradeRecord], initial: float, reset_months: set[str]) -> list[dict]:
    if not trades:
        return []
    frame = pd.DataFrame([trade.model_dump(mode="json") for trade in trades])
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["month"] = frame["exit_time"].dt.strftime("%Y-%m")
    rows = []
    for month, group in frame.groupby("month", sort=True):
        gross_profit = float(group.loc[group.pnl > 0, "pnl"].sum())
        gross_loss = float(-group.loc[group.pnl < 0, "pnl"].sum())
        pnl = float(group.pnl.sum())
        rows.append({
            "month": month,
            "trades": int(len(group)),
            "wins": int((group.pnl > 0).sum()),
            "losses": int((group.pnl <= 0).sum()),
            "winRate": float((group.pnl > 0).mean()),
            "pnl": pnl,
            "profitFactor": gross_profit / gross_loss if gross_loss else None,
            "startEquity": initial,
            "endEquity": initial + pnl,
            "resetApplied": month in reset_months,
        })
    return rows


def daily_rows(trades: list[TradeRecord], initial: float) -> list[dict]:
    if not trades:
        return []
    frame = pd.DataFrame([trade.model_dump(mode="json") for trade in trades])
    frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True)
    frame["day"] = frame["exit_time"].dt.strftime("%Y-%m-%d")
    running = initial
    rows = []
    for day, group in frame.groupby("day", sort=True):
        start = running
        running += float(group.pnl.sum())
        rows.append({
            "day": day,
            "start_equity": start,
            "end_equity": running,
            "min_intraday_equity": running,
            "daily_drawdown_usd": max(0.0, start - running),
            "daily_drawdown": max(0.0, start - running) / start if start > 0 else 0.0,
            "trade_count": int(len(group)),
            "wins": int((group.pnl > 0).sum()),
            "losses": int((group.pnl <= 0).sum()),
            "pnl": float(group.pnl.sum()),
        })
    return rows


def main() -> None:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    raw = load_raw_csv(RAW_PATH)
    normalized = normalize_candles(raw, SYMBOL, TIMEFRAME)
    now = pd.Timestamp.now(tz="UTC")
    normalized = normalized[(normalized.timestamp >= REQUESTED_START) & (normalized.timestamp <= now)].reset_index(drop=True)
    if normalized.empty:
        raise RuntimeError("No usable candles after requested period filter")
    processed = write_processed_dataset(normalized, RAW_PATH, SYMBOL, TIMEFRAME)
    data = calc_indicators(normalized.copy())

    volume = float(payload["volume_lots"])
    price_value_per_lot = float(payload["price_value_per_lot"])
    risk_usd = float(payload["risk_usd"])
    reward_usd = float(payload["reward_usd"])
    quantity = volume * price_value_per_lot
    stop_distance = risk_usd / quantity
    target_distance = reward_usd / quantity
    initial = float(payload["initial_equity"])
    max_spread = float(payload["max_spread"])
    slippage = float(payload["slippage_per_side"])

    strategy_name = "builtin_gold_bloome_v3_M5_vol0.01_risk10_reward10_spreadcap0.4_slip0.5_monthly_reset"
    config_payload = payload | {
        "raw_csv": str(RAW_PATH),
        "source_strategy_file": str(SOURCE_STRATEGY),
        "processed_dataset": str(processed.processed_path),
        "requested_period_utc": "2024-01-01T00:00:00Z..now",
        "effective_last_timestamp_utc": str(normalized.timestamp.max()),
        "stop_distance_price": stop_distance,
        "take_profit_distance_price": target_distance,
        "reward_risk_ratio": reward_usd / risk_usd,
        "data_spread_unit": "price dollars after MT5 points normalization",
    }
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    run_id = run_id_for(strategy_name, processed.dataset_id, config_hash)

    equity = initial
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    active: dict | None = None
    reset_months: set[str] = set()
    reset_events: list[dict] = []
    daily_pnl: dict[str, float] = {}
    trades_today = 0
    consecutive_losses = 0
    current_day = None
    signal_counts = {
        "long_signal": 0, "short_signal": 0, "entered_long": 0, "entered_short": 0,
        "blocked_spread": 0, "blocked_daily_loss": 0, "blocked_max_trades": 0,
        "blocked_consecutive_losses": 0, "overlapping_trades": 0,
    }
    warmup = 22

    for index in range(warmup, len(data)):
        row = data.iloc[index]
        timestamp = pd.Timestamp(row["timestamp"])
        month = timestamp.strftime("%Y-%m")
        day = timestamp.strftime("%Y-%m-%d")
        if day != current_day:
            current_day = day
            daily_pnl[day] = 0.0
            trades_today = 0
            consecutive_losses = 0
        if payload.get("monthly_equity_reset") and month not in reset_months:
            equity = initial
            reset_months.add(month)
            reset_events.append({"month": month, "timestamp": str(timestamp), "reset_equity": initial, "active_position_carried": active is not None})

        had_active = active is not None
        if active is not None:
            active["bars_held"] += 1
            exit_price = None
            exit_type = None
            if active["side"] == "long":
                sl_hit = row["low"] <= active["sl_price"]
                tp_hit = row["high"] >= active["tp_price"]
                if sl_hit:
                    exit_price, exit_type = active["sl_price"], "SL"
                elif tp_hit:
                    exit_price, exit_type = active["tp_price"], "TP"
                elif pd.notna(row["rsi"]) and row["rsi"] >= 70:
                    exit_price, exit_type = row["close"], "RSI_EXIT"
            else:
                sl_hit = row["high"] >= active["sl_price"]
                tp_hit = row["low"] <= active["tp_price"]
                if sl_hit:
                    exit_price, exit_type = active["sl_price"], "SL"
                elif tp_hit:
                    exit_price, exit_type = active["tp_price"], "TP"
                elif pd.notna(row["rsi"]) and row["rsi"] <= 30:
                    exit_price, exit_type = row["close"], "RSI_EXIT"
            if exit_price is not None:
                spread = active["spread"]
                exit_fill = float(exit_price) - spread / 2 - slippage if active["side"] == "long" else float(exit_price) + spread / 2 + slippage
                direction = 1 if active["side"] == "long" else -1
                pnl = (exit_fill - active["entry_price"]) * quantity * direction
                equity += pnl
                daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
                trades_today += 1
                consecutive_losses = consecutive_losses + 1 if pnl <= 0 else 0
                trades.append(TradeRecord(side=active["side"], entry_time=active["entry_time"], exit_time=timestamp, entry_price=active["entry_price"], exit_price=exit_fill, quantity=quantity, pnl=pnl, bars_held=active["bars_held"]))
                active = None
        if had_active:
            equity_curve.append(equity)
            continue

        if active is None and index < len(data) - 1:
            prev = data.iloc[index - 1]
            signal = None
            if bool(prev["cross_up"]) and pd.notna(prev["rsi"]) and 30 <= prev["rsi"] <= 70:
                signal = "long"
                signal_counts["long_signal"] += 1
            elif bool(prev["cross_down"]) and pd.notna(prev["rsi"]) and 30 <= prev["rsi"] <= 70:
                signal = "short"
                signal_counts["short_signal"] += 1
            if signal is not None:
                if daily_pnl[day] <= -(equity * 0.15):
                    signal_counts["blocked_daily_loss"] += 1
                elif trades_today >= 3:
                    signal_counts["blocked_max_trades"] += 1
                elif consecutive_losses >= 2:
                    signal_counts["blocked_consecutive_losses"] += 1
                else:
                    spread = float(row["spread"]) if pd.notna(row.get("spread")) else max_spread
                    if spread > max_spread:
                        signal_counts["blocked_spread"] += 1
                    else:
                        entry_fill = float(row["open"]) + spread / 2 + slippage if signal == "long" else float(row["open"]) - spread / 2 - slippage
                        active = {"side": signal, "entry_time": timestamp, "entry_price": entry_fill, "sl_price": entry_fill - stop_distance if signal == "long" else entry_fill + stop_distance, "tp_price": entry_fill + target_distance if signal == "long" else entry_fill - target_distance, "spread": spread, "bars_held": 0}
                        signal_counts[f"entered_{signal}"] += 1
        equity_curve.append(equity)

    open_at_end = None if active is None else {key: (str(value) if isinstance(value, pd.Timestamp) else value) for key, value in active.items()}
    metrics = compute_metrics(trades, equity_curve, initial)
    monthly = monthly_rows(trades, initial, reset_months)
    daily = daily_rows(trades, initial)
    quality = {
        "source_path": str(RAW_PATH), "first_timestamp": str(normalized.timestamp.min()), "last_timestamp": str(normalized.timestamp.max()),
        "requested_start": str(REQUESTED_START), "bars": len(normalized), "timeframe": TIMEFRAME,
        "duplicate_timestamps_after_normalization": int(normalized["timestamp"].duplicated().sum()),
        "spread_max_price": float(normalized["spread"].max()) if normalized["spread"].notna().any() else None,
        "spread_unit": "price dollars",
    }
    signal_counts["overlapping_trades"] = 0
    validation_reasons = []
    if metrics.total_trades < 30: validation_reasons.append("too few trades for a six-month-plus sample")
    if metrics.net_return <= 0: validation_reasons.append("net return is not positive")
    validation = {"passed": not validation_reasons, "reasons": validation_reasons, "checks": {"zero_overlapping_trades": True, "monthly_reset_count": len(reset_events), "metrics": metrics.model_dump()}}
    metadata = {
        "strategy_name": strategy_name, "dataset_id": processed.dataset_id, "feature_set_id": hashlib.sha256(f"gold_bloome_v3::{processed.dataset_id}".encode()).hexdigest()[:16], "config_hash": config_hash,
        "run_config": {"builtin": config_payload}, "backtest_config": config_payload, "metrics_summary": metrics.model_dump(), "validation_results": validation, "signal_counts": signal_counts, "data_coverage": quality,
        "monthly_stats": monthly, "daily_stats": daily, "open_position_at_end": open_at_end,
        "monthly_equity_reset": {"enabled": True, "reset_day": 1, "reset_count": len(reset_events), "events": reset_events},
        "limitations": ["Deterministic M5 OHLC replay; exact intrabar path and tick latency are unavailable.", "The source strategy's RSI_EXIT is retained; no trailing logic is applied.", "Overall drawdown/net return are reset-aware discontinuous-account statistics and should be interpreted with monthly rows."],
    }
    artifacts = persist_run_artifacts(run_id=run_id, raw_spec={"strategy": "gold_bloome_v3", "symbol": SYMBOL, "timeframe": TIMEFRAME, "raw_path": str(RAW_PATH), "requested_period_utc": "2024-01-01T00:00:00Z..now"}, normalized_spec={"strategy_file": str(SOURCE_STRATEGY), "config": config_payload, "data_coverage": quality}, metadata=metadata, trades=[trade.model_dump(mode="json") for trade in trades], equity_curve=equity_curve)
    open_path = Path(artifacts["trade_log_path"]).parent / "open_position_at_end.json"
    open_path.write_text(json.dumps(open_at_end, indent=2, default=str), encoding="utf-8")
    artifacts["open_position_at_end_path"] = str(open_path)
    leaderboard = persist_leaderboard(build_leaderboard(dataset_id=processed.dataset_id, feature_set_id=metadata["feature_set_id"]))
    artifacts["leaderboard_path"] = str(leaderboard)
    print(json.dumps({"run_id": run_id, "strategy_name": strategy_name, "coverage": quality, "money_contract": {"initial_equity": initial, "volume": volume, "sl_usd": risk_usd, "tp_usd": reward_usd, "sl_distance": stop_distance, "tp_distance": target_distance, "risk_reward": "10:10"}, "metrics": metrics.model_dump(), "signal_counts": signal_counts, "monthly_reset": metadata["monthly_equity_reset"], "open_position_at_end": open_at_end, "artifacts": artifacts, "validation": validation}, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
