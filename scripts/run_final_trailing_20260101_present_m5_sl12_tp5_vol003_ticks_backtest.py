from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(ROOT / "scripts"))

from trading_lab.backtest.metrics.performance import compute_metrics
from trading_lab.data.loaders.csv_loader import load_raw_csv
from trading_lab.data.transforms.normalize import normalize_candles, write_processed_dataset
from trading_lab.models import TradeRecord
from trading_lab.storage.artifacts import build_leaderboard, persist_leaderboard, persist_run_artifacts, run_id_for
from trading_lab.strategies.builtins.rsiqui.final import prepare_rsiqui_v3_frame, rsiqui_v3_config_for_preset
from run_rsiqui_final_20260101_present_m5_sl27_tp9_vol003_eq1000_backtest import (
    evaluate_close_confirm_entry_condition,
    inspect_quality as base_inspect_quality,
    monthly_stats,
    write_xml_summary,
)

RAW_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSD_M5_202601020105_202608041330.csv"
TICK_PATH = ROOT / "data" / "raw" / "mt5_ticks" / "XAUUSD_202607130105_202608032349.csv"
CONFIG_PATH = ROOT / "configs" / "strategies" / "rsiqui" / "final_trailing_m5_demo.json"
SYMBOL = "XAUUSD"
TIMEFRAME = "5m"
REQUESTED_START_UTC = pd.Timestamp("2026-01-01T00:00:00Z")


def load_tick_arrays(path: Path, start: pd.Timestamp, end: pd.Timestamp) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    ticks = pd.read_csv(path, sep="\t", dtype=str)
    ticks["timestamp"] = pd.to_datetime(ticks["<DATE>"] + " " + ticks["<TIME>"], format="%Y.%m.%d %H:%M:%S.%f", utc=True)
    ticks["bid"] = pd.to_numeric(ticks["<BID>"], errors="coerce")
    ticks["ask"] = pd.to_numeric(ticks["<ASK>"], errors="coerce")
    ticks = ticks.dropna(subset=["timestamp", "bid", "ask"])
    ticks = ticks[(ticks["timestamp"] >= start) & (ticks["timestamp"] < end)].sort_values("timestamp")
    return (ticks["timestamp"].astype("int64").to_numpy(), ticks["bid"].to_numpy(dtype=float), ticks["ask"].to_numpy(dtype=float))


def tick_slice(times: np.ndarray, bids: np.ndarray, asks: np.ndarray, start: pd.Timestamp, end: pd.Timestamp):
    lo = int(np.searchsorted(times, start.value, side="left"))
    hi = int(np.searchsorted(times, end.value, side="left"))
    if hi <= lo:
        return None
    return bids[lo:hi], asks[lo:hi]

def inspect_quality(raw, normalized):
    quality = base_inspect_quality(raw, normalized)
    quality["source_path"] = str(RAW_PATH)
    return quality


def main() -> None:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if payload["strategy"] != "rsiqui-v3-final-trailing" or payload.get("symbol_base", payload.get("symbol")) != SYMBOL or str(payload["timeframe"]).upper() not in {"M5", "5M"}:
        raise RuntimeError("This runner only accepts FINAL_TRAILING XAUUSD M5")
    raw = load_raw_csv(RAW_PATH)
    normalized = normalize_candles(raw, SYMBOL, TIMEFRAME)
    normalized = normalized[(normalized["timestamp"] >= REQUESTED_START_UTC) & (normalized["timestamp"] <= pd.Timestamp.now(tz="UTC"))].reset_index(drop=True)
    if normalized.empty:
        raise RuntimeError("No usable candles after requested date filter")
    tick_times, tick_bids, tick_asks = load_tick_arrays(TICK_PATH, normalized["timestamp"].iloc[0], normalized["timestamp"].iloc[-1] + pd.Timedelta(minutes=5))
    dataset_meta = write_processed_dataset(normalized, RAW_PATH, SYMBOL, TIMEFRAME)
    dataset_id = dataset_meta.dataset_id
    feature_set_id = hashlib.sha256(f"rsiqui_final_close_confirm::{dataset_id}::{TIMEFRAME}::{len(normalized)}".encode()).hexdigest()[:16]
    config = rsiqui_v3_config_for_preset(
        payload["preset"], initial_equity=float(payload["initial_equity"]), volume_lots=float(payload["volume"]),
        price_value_per_lot=float(payload["price_value_per_lot"]), risk_usd=float(payload["risk_usd"]),
        reward_usd=float(payload["reward_usd"]), synthetic_spread=float(payload["synthetic_spread"]),
        spread_multiplier=float(payload["spread_multiplier"]), slippage_per_side=float(payload["slippage_per_side"]),
        commission_per_trade_usd=float(payload["commission_per_trade_usd"]), max_spread=float(payload["max_spread"]),
        session_filter="all", trade_side=str(payload["side"]), allowed_entry_hours=(),
    )
    blocked_hours = tuple(int(hour) for hour in payload["blocked_entry_hours_gmt7"])
    config_payload = config.__dict__ | {
        "strategy": payload["strategy"], "variant": "FINAL_TRAILING", "symbol": SYMBOL, "timeframe": "M5",
        "raw_csv": str(RAW_PATH), "tick_csv": str(TICK_PATH), "source_strategy_file": str(ROOT / "strategies/builtins/rsiqui/final.py"),
        "canonical_root_strategy_file": str(ROOT / "strategies/builtins/rsiqui_v3_root.py"),
        "source_config_file": str(CONFIG_PATH), "requested_period_utc": "2026-01-01T00:00:00Z..now",
        "monthly_equity_reset": bool(payload.get("monthly_equity_reset", False)),
        "monthly_reset_day": int(payload.get("monthly_reset_day", 1)),
        "trailing_policy": {"activation_price_distance": float(payload["trailing_activation_price_distance"]), "locked_profit_usd": float(payload["trailing_locked_profit_usd"]), "step_price": float(payload["trailing_step_price"]), "contract": "strictly above activation; lock +$4 then ratchet +0.5 price per favorable 0.5 price"},
        "blocked_entry_hours_gmt7": list(blocked_hours),
        "blackout_policy": "entry proxy timestamp converted from UTC to GMT+7; blocks new entries only",
        "entry_timing_contract": "close-confirm M5 signal; tick Bid/Ask entry when available, otherwise M5 close plus 5-minute proxy",
        "entry_condition_check": "enforced: evaluate_close_confirm_entry_condition() requires preview_side == confirmed_side and non-empty",
        "one_position_guard": "enforced: one XAUUSD active position; bar starting with a position is consumed even if it exits within that bar",
        "collision_rule": "SL-first when both SL and TP are touched in the same M5 bar; tick path uses chronological Bid/Ask extrema within each bar",
        "cost_model_note": "Tick Bid/Ask execution where available; M5 OHLC fallback before tick coverage; entry and exit charge adverse half-spread plus slippage_per_side; commission per completed trade",
        "optimization_policy": "none",
        "tick_execution_policy": "tick Bid/Ask used for entry/SL/TP/trailing when available; M5 OHLC fallback before tick coverage",
        "tick_coverage_utc": [str(pd.to_datetime(tick_times[0], unit="ns", utc=True)) if len(tick_times) else None, str(pd.to_datetime(tick_times[-1], unit="ns", utc=True)) if len(tick_times) else None],
    }
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True, default=str).encode()).hexdigest()[:16]
    strategy_name = "builtin_rsiqui-v3-final-trailing_gold-loose_both_vol0.03_risk36_reward15_activation2_lock4_step0.5_blackout_gmt7_M5_20260101_present"
    run_id = run_id_for(strategy_name, dataset_id, config_hash)

    data = prepare_rsiqui_v3_frame(normalized, config)
    equity = config.initial_equity
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    active: dict | None = None
    monthly_reset_enabled = bool(payload.get("monthly_equity_reset", False))
    monthly_reset_months: set[str] = set()
    monthly_reset_events: list[dict] = []
    signal_counts = {"preview_long": 0, "preview_short": 0, "confirmed_long": 0, "confirmed_short": 0, "blocked_no_matching_close_confirm": 0, "blocked_blackout_gmt7": 0, "blocked_spread": 0, "entered_long": 0, "entered_short": 0, "overlapping_trades": 0, "trailing_activated": 0, "trailing_moves": 0, "trailing_milestone_20": 0, "trailing_milestone_25": 0, "tp_disabled_at_25": 0}
    warmup = max(120, config.rsi_length + config.gradient_periods)
    for index in range(warmup, len(data)):
        row = data.iloc[index]
        row_month = row["timestamp"].strftime("%Y-%m")
        if monthly_reset_enabled and row_month not in monthly_reset_months:
            equity = config.initial_equity
            monthly_reset_months.add(row_month)
            monthly_reset_events.append({"month": row_month, "timestamp": str(row["timestamp"]), "reset_equity": config.initial_equity, "active_position_carried": active is not None})
        had_active_at_start = active is not None
        bar_start = row["timestamp"]
        bar_end = bar_start + pd.Timedelta(minutes=5)
        tick_data = tick_slice(tick_times, tick_bids, tick_asks, bar_start, bar_end)
        bid_low, bid_high = (float(np.min(tick_data[0])), float(np.max(tick_data[0]))) if tick_data is not None else (float(row["low"]), float(row["high"]))
        ask_low, ask_high = (float(np.min(tick_data[1])), float(np.max(tick_data[1]))) if tick_data is not None else (float(row["low"]), float(row["high"]))
        if active is not None:
            active["bars_held"] += 1
            favorable_distance = max(0.0, (bid_high - active["entry_price"]) if active["side"] == "long" else (active["entry_price"] - ask_low))
            activation_distance = float(payload["trailing_activation_price_distance"])
            step_price = max(float(payload["trailing_step_price"]), 1e-12)
            if favorable_distance > activation_distance:
                steps = max(0, int((favorable_distance - activation_distance) / step_price + 1e-12))
                locked_profit = float(payload["trailing_locked_profit_usd"])
                lock_distance = locked_profit / active["quantity"] + steps * step_price
                candidate_sl = active["entry_price"] + lock_distance if active["side"] == "long" else active["entry_price"] - lock_distance
                if not active.get("trailing_activated", False):
                    active["trailing_activated"] = True
                    signal_counts["trailing_activated"] += 1
                if (active["side"] == "long" and candidate_sl > active["stop_price"]) or (active["side"] == "short" and candidate_sl < active["stop_price"]):
                    active["stop_price"] = candidate_sl
                    active["locked_profit_usd"] = locked_profit + steps * step_price * active["quantity"]
                    active["trailing_step_price"] = step_price
                    signal_counts["trailing_moves"] += 1
            exit_price = None
            if active["side"] == "long":
                if bid_low <= active["stop_price"]:
                    exit_price = active["stop_price"]
                elif active.get("take_profit_enabled", True) and bid_high >= active["take_profit_price"]:
                    exit_price = active["take_profit_price"]
            else:
                if ask_high >= active["stop_price"]:
                    exit_price = active["stop_price"]
                elif active.get("take_profit_enabled", True) and ask_low <= active["take_profit_price"]:
                    exit_price = active["take_profit_price"]
            if exit_price is not None:
                half_spread = active["spread"] * 0.5
                exit_fill = float(exit_price) - half_spread - config.slippage_per_side if active["side"] == "long" else float(exit_price) + half_spread + config.slippage_per_side
                direction = 1 if active["side"] == "long" else -1
                pnl = (exit_fill - active["entry_price"]) * active["quantity"] * direction - config.commission_per_trade_usd
                trades.append(TradeRecord(side=active["side"], entry_time=active["entry_time"], exit_time=row["timestamp"], entry_price=active["entry_price"], exit_price=exit_fill, quantity=active["quantity"], pnl=pnl, bars_held=active["bars_held"]))
                equity += pnl
                active = None
        if had_active_at_start:
            equity_curve.append(equity)
            continue
        if active is None and index < len(data) - 1:
            preview_side, confirmed_side, matched_side = evaluate_close_confirm_entry_condition(row, config)
            for label, side in (("preview", preview_side), ("confirmed", confirmed_side)):
                if side in {"long", "short"}: signal_counts[f"{label}_{side}"] += 1
            if matched_side is None:
                signal_counts["blocked_no_matching_close_confirm"] += 1
            else:
                entry_time = row["timestamp"] + pd.Timedelta(minutes=5)
                if int((entry_time + pd.Timedelta(hours=7)).hour) in blocked_hours:
                    signal_counts["blocked_blackout_gmt7"] += 1
                else:
                    entry_ns = entry_time.value
                    tick_entry_index = int(np.searchsorted(tick_times, entry_ns, side="left"))
                    has_tick_entry = tick_entry_index < len(tick_times) and tick_times[tick_entry_index] < (entry_time + pd.Timedelta(seconds=5)).value
                    if has_tick_entry:
                        tick_bid = float(tick_bids[tick_entry_index])
                        tick_ask = float(tick_asks[tick_entry_index])
                        stressed_spread = (tick_ask - tick_bid) * config.spread_multiplier
                        raw_entry = (tick_bid + tick_ask) * 0.5
                    else:
                        spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
                        stressed_spread = spread * config.spread_multiplier
                        raw_entry = float(row["close"])
                    if stressed_spread > config.max_spread:
                        signal_counts["blocked_spread"] += 1
                    else:
                        half_spread = stressed_spread * 0.5
                        entry_fill = raw_entry + half_spread + config.slippage_per_side if matched_side == "long" else raw_entry - half_spread - config.slippage_per_side
                        stop = entry_fill - config.stop_distance if matched_side == "long" else entry_fill + config.stop_distance
                        target = entry_fill + config.take_profit_distance if matched_side == "long" else entry_fill - config.take_profit_distance
                        active = {"side": matched_side, "entry_time": entry_time, "entry_price": entry_fill, "quantity": config.quantity, "stop_price": stop, "take_profit_price": target, "take_profit_enabled": True, "bars_held": 0, "spread": stressed_spread, "trailing_activated": False, "locked_profit_usd": 0.0}
                        signal_counts[f"entered_{matched_side}"] += 1
        equity_curve.append(equity)
    open_at_end = None if active is None else {key: (str(value) if isinstance(value, (pd.Timestamp, datetime)) else value) for key, value in active.items()}
    signal_counts["long_signal"] = signal_counts["preview_long"]
    signal_counts["short_signal"] = signal_counts["preview_short"]
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    quality = inspect_quality(raw, normalized)
    reasons = []
    if metrics.total_trades < 30: reasons.append("too few trades for a six-month-plus sample")
    if metrics.net_return <= 0: reasons.append("net return is not positive")
    validation = {"passed": not reasons, "reasons": reasons, "checks": metrics.model_dump() | {"signal_counts": signal_counts}}
    metadata = {"strategy_name": strategy_name, "dataset_id": dataset_id, "feature_set_id": feature_set_id, "config_hash": config_hash, "run_config": {"builtin": config_payload}, "backtest_config": config_payload, "metrics_summary": metrics.model_dump(), "validation_results": validation, "signal_counts": signal_counts, "data_coverage": quality, "monthly_stats": monthly_stats(trades), "open_position_at_end": open_at_end, "monthly_equity_reset": {"enabled": monthly_reset_enabled, "reset_count": len(monthly_reset_events), "events": monthly_reset_events}, "trailing_policy": config_payload["trailing_policy"], "limitations": ["Canonical root uses numpy.gradient centered-gradient behavior; forensic/original live-sim approximation, not causal live-ready evidence.", "M5 OHLC is used before tick coverage; tick Bid/Ask is used where available, but exact T-5/T+0 latency remains unavailable.", "Same-bar SL/TP ambiguity is resolved with SL-first."]}
    artifacts = persist_run_artifacts(run_id=run_id, raw_spec={"strategy": payload["strategy"], "variant": "FINAL_TRAILING", "symbol": SYMBOL, "timeframe": "M5", "raw_path": str(RAW_PATH), "tick_path": str(TICK_PATH), "requested_period_utc": "2026-01-01..now"}, normalized_spec={"strategy": payload["strategy"], "config": config_payload, "data_coverage": quality}, metadata=metadata, trades=[trade.model_dump() for trade in trades], equity_curve=equity_curve)
    open_path = Path(artifacts["trade_log_path"]).parent / "open_position_at_end.json"
    open_path.write_text(json.dumps(open_at_end, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    artifacts["open_position_at_end_path"] = str(open_path)
    xml_path = Path(artifacts["report_path"]).with_suffix(".xml")
    write_xml_summary(xml_path, run_id, metadata, artifacts)
    artifacts["xml_report_path"] = str(xml_path)
    artifacts["leaderboard_path"] = str(persist_leaderboard(build_leaderboard(dataset_id=dataset_id, feature_set_id=feature_set_id)))
    print(json.dumps({"run_id": run_id, "strategy_name": strategy_name, "coverage": quality, "money_contract": {"initial_equity": config.initial_equity, "volume": config.volume_lots, "sl_usd": config.risk_usd, "tp_usd": config.reward_usd, "sl_distance": config.stop_distance, "tp_distance": config.take_profit_distance, "reward_risk": config.reward_usd / config.risk_usd, "risk_reward": f"{config.risk_usd}:{config.reward_usd}"}, "metrics": metrics.model_dump(), "signal_counts": signal_counts, "open_position_at_end": open_at_end, "artifacts": artifacts, "validation": validation}, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()



