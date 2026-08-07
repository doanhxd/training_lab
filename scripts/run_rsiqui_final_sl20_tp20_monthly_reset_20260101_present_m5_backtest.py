from __future__ import annotations

import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.data.loaders.csv_loader import load_raw_csv
from training_lab.data.transforms.normalize import normalize_candles, write_processed_dataset
from training_lab.models import TradeRecord
from training_lab.storage.artifacts import build_leaderboard, persist_leaderboard, persist_run_artifacts, run_id_for
from training_lab.strategies.builtins.rsiqui.final import (
    evaluate_rsiqui_v3_signal,
    prepare_rsiqui_v3_frame,
    rsiqui_v3_config_for_preset,
)

RAW_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSD_M5_202501012300_202607312345.csv"
CONFIG_PATH = ROOT / "configs" / "strategies" / "rsiqui" / "final_m5.json"
SYMBOL = "XAUUSD"
TIMEFRAME = "5m"
REQUESTED_START_UTC = pd.Timestamp("2026-01-01T00:00:00Z")
MONTHLY_EQUITY_RESET = True
MONTHLY_RESET_DAY = 1


def finite_float(value):
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if pd.notna(numeric) and numeric not in (float("inf"), float("-inf")) else None


def inspect_quality(raw: pd.DataFrame, normalized: pd.DataFrame) -> dict:
    cadence = normalized["timestamp"].diff().dropna()
    invalid = (
        (normalized["low"] > normalized["high"])
        | (normalized["high"] < normalized[["open", "close"]].max(axis=1))
        | (normalized["low"] > normalized[["open", "close"]].min(axis=1))
    )
    spread = normalized["spread"].dropna() if "spread" in normalized else pd.Series(dtype=float)
    return {
        "source_path": str(RAW_PATH),
        "raw_row_count": int(len(raw)),
        "usable_row_count": int(len(normalized)),
        "first_timestamp": str(normalized["timestamp"].min()),
        "last_timestamp": str(normalized["timestamp"].max()),
        "duplicate_timestamps_after_normalization": int(normalized["timestamp"].duplicated().sum()),
        "missing_ohlc": int(normalized[["open", "high", "low", "close"]].isna().any(axis=1).sum()),
        "invalid_ohlc": int(invalid.sum()),
        "missing_spread_fraction": float(normalized["spread"].isna().mean()) if "spread" in normalized else 1.0,
        "spread_price_min": finite_float(spread.min()) if not spread.empty else None,
        "spread_price_median": finite_float(spread.median()) if not spread.empty else None,
        "spread_price_max": finite_float(spread.max()) if not spread.empty else None,
        "median_cadence_minutes": finite_float(cadence.median().total_seconds() / 60) if not cadence.empty else None,
        "max_gap_minutes": finite_float(cadence.max().total_seconds() / 60) if not cadence.empty else None,
        "estimated_missing_m5_bars": int(max(0, round((cadence.sum().total_seconds() / 300) - max(0, len(normalized) - 1)))) if not cadence.empty else None,
    }


def evaluate_close_confirm_entry_condition(row: pd.Series, config) -> tuple[str | None, str | None, str | None]:
    preview_side = evaluate_rsiqui_v3_signal(row, config)
    confirmed_side = evaluate_rsiqui_v3_signal(row.copy(), config)
    matched_side = preview_side if preview_side and preview_side == confirmed_side else None
    return preview_side, confirmed_side, matched_side


def monthly_stats(trades: list[TradeRecord]) -> list[dict]:
    grouped: dict[str, list[TradeRecord]] = defaultdict(list)
    for trade in trades:
        grouped[trade.entry_time.strftime("%Y-%m")].append(trade)
    rows = []
    for month in sorted(grouped):
        month_trades = grouped[month]
        pnl = sum(trade.pnl for trade in month_trades)
        rows.append({
            "month": month,
            "trades": len(month_trades),
            "wins": sum(trade.pnl > 0 for trade in month_trades),
            "losses": sum(trade.pnl <= 0 for trade in month_trades),
            "net_pnl": pnl,
        })
    return rows


def write_xml_summary(path: Path, run_id: str, metadata: dict, artifacts: dict) -> None:
    root = ET.Element("backtest_run", attrib={"run_id": run_id})
    for key in ("strategy_name", "dataset_id", "feature_set_id", "config_hash"):
        ET.SubElement(root, key).text = str(metadata.get(key, ""))
    metrics_el = ET.SubElement(root, "metrics")
    for key, value in metadata.get("metrics_summary", {}).items():
        ET.SubElement(metrics_el, key).text = str(value)
    config_el = ET.SubElement(root, "backtest_config")
    for key, value in metadata.get("backtest_config", {}).items():
        ET.SubElement(config_el, key).text = json.dumps(value, default=str, ensure_ascii=False)
    artifacts_el = ET.SubElement(root, "artifacts")
    for key, value in artifacts.items():
        ET.SubElement(artifacts_el, key).text = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def main() -> None:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if payload["strategy"] != "rsiqui-v3-final" or payload.get("symbol_base", "XAUUSD") != SYMBOL or str(payload["timeframe"]).upper() not in {"M5", "5M"}:
        raise RuntimeError("This runner only accepts FINAL XAUUSD-family M5")

    raw = load_raw_csv(RAW_PATH)
    normalized = normalize_candles(raw, SYMBOL, TIMEFRAME)
    now_utc = pd.Timestamp.now(tz="UTC")
    normalized = normalized[(normalized["timestamp"] >= REQUESTED_START_UTC) & (normalized["timestamp"] <= now_utc)].reset_index(drop=True)
    if normalized.empty:
        raise RuntimeError("No usable candles after requested date filter")

    dataset_meta = write_processed_dataset(normalized, RAW_PATH, SYMBOL, TIMEFRAME)
    dataset_id = dataset_meta.dataset_id
    feature_payload = f"rsiqui_final_close_confirm::{dataset_id}::{TIMEFRAME}::{len(normalized)}"
    feature_set_id = hashlib.sha256(feature_payload.encode("utf-8")).hexdigest()[:16]

    config = rsiqui_v3_config_for_preset(
        payload["preset"],
        initial_equity=float(payload["initial_equity"]),
        volume_lots=float(payload["volume"]),
        price_value_per_lot=float(payload["price_value_per_lot"]),
        risk_usd=float(payload["risk_usd"]),
        reward_usd=float(payload["reward_usd"]),
        synthetic_spread=float(payload["synthetic_spread"]),
        spread_multiplier=float(payload["spread_multiplier"]),
        slippage_per_side=float(payload["slippage_per_side"]),
        commission_per_trade_usd=float(payload["commission_per_trade_usd"]),
        max_spread=float(payload["max_spread"]),
        session_filter="all",
        trade_side=str(payload["side"]),
        allowed_entry_hours=(),
    )
    blocked_hours = tuple(int(hour) for hour in payload["blocked_entry_hours_gmt7"])
    config_payload = config.__dict__ | {
        "strategy": payload["strategy"],
        "symbol": SYMBOL,
        "timeframe": payload["timeframe"],
        "raw_csv": str(RAW_PATH),
        "source_strategy_file": str(ROOT / "strategies" / "builtins" / "rsiqui" / "final.py"),
        "canonical_root_strategy_file": str(ROOT / "strategies" / "builtins" / "rsiqui_v3_root.py"),
        "source_config_file": str(CONFIG_PATH),
        "requested_period_utc": "2026-01-01T00:00:00Z..now",
        "blocked_entry_hours_gmt7": list(blocked_hours),
        "blackout_policy": "entry proxy timestamp converted from UTC to GMT+7; blocks new entries only",
        "entry_timing_contract": "close-confirm M5: evaluate canonical signal for preview and confirmed closed-bar pass; enter only when both non-empty sides match; OHLC proxy uses row close and row timestamp + 5 minutes",
        "entry_condition_check": "enforced: evaluate_close_confirm_entry_condition() requires preview_side == confirmed_side and non-empty",
        "one_position_guard": "enforced: one XAUUSD active position; a bar starting with an open position is consumed even if it exits within that bar",
        "collision_rule": "SL-first when both SL and TP are touched in the same M5 bar",
        "cost_model_note": "OHLC replay; entry and exit each charge adverse half-spread plus slippage_per_side, equivalent to one full spread plus 2x per-side slippage; commission is charged per completed trade",
        "optimization_policy": "none",
        "monthly_equity_reset": MONTHLY_EQUITY_RESET,
        "monthly_reset_day": MONTHLY_RESET_DAY,
    }
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    strategy_name = "builtin_rsiqui-v3-final_gold-loose_both_vol0.03_risk30_reward30_closeconfirm_no_blackout_monthly_reset_M5_20260101_present"
    run_id = run_id_for(strategy_name, dataset_id, config_hash)

    data = prepare_rsiqui_v3_frame(normalized, config)
    equity = config.initial_equity
    monthly_reset_months: set[str] = set()
    monthly_reset_events: list[dict] = []
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    active: dict | None = None
    signal_counts = {
        "preview_long": 0,
        "preview_short": 0,
        "confirmed_long": 0,
        "confirmed_short": 0,
        "blocked_no_matching_close_confirm": 0,
        "blocked_blackout_gmt7": 0,
        "blocked_spread": 0,
        "entered_long": 0,
        "entered_short": 0,
        "overlapping_trades": 0,
    }
    warmup = max(120, config.rsi_length + config.gradient_periods)
    for index in range(warmup, len(data)):
        row = data.iloc[index]
        row_month = row["timestamp"].strftime("%Y-%m")
        if MONTHLY_EQUITY_RESET and row["timestamp"].day >= MONTHLY_RESET_DAY and row_month not in monthly_reset_months:
            equity = config.initial_equity
            monthly_reset_months.add(row_month)
            monthly_reset_events.append({"month": row_month, "timestamp": str(row["timestamp"]), "reset_equity": config.initial_equity, "active_position_carried": active is not None})
        had_active_at_start = active is not None
        if active is not None:
            active["bars_held"] += 1
            exit_price = None
            if active["side"] == "long":
                if row["low"] <= active["stop_price"]:
                    exit_price = active["stop_price"]
                elif row["high"] >= active["take_profit_price"]:
                    exit_price = active["take_profit_price"]
            else:
                if row["high"] >= active["stop_price"]:
                    exit_price = active["stop_price"]
                elif row["low"] <= active["take_profit_price"]:
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
                if side in {"long", "short"}:
                    signal_counts[f"{label}_{side}"] += 1
            if matched_side is None:
                signal_counts["blocked_no_matching_close_confirm"] += 1
            else:
                entry_time = row["timestamp"] + pd.Timedelta(minutes=5)
                entry_hour_gmt7 = int((entry_time + pd.Timedelta(hours=7)).hour)
                if entry_hour_gmt7 in blocked_hours:
                    signal_counts["blocked_blackout_gmt7"] += 1
                else:
                    spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
                    stressed_spread = spread * config.spread_multiplier
                    if stressed_spread > config.max_spread:
                        signal_counts["blocked_spread"] += 1
                    else:
                        half_spread = stressed_spread * 0.5
                        raw_entry = float(row["close"])
                        entry_fill = raw_entry + half_spread + config.slippage_per_side if matched_side == "long" else raw_entry - half_spread - config.slippage_per_side
                        stop = entry_fill - config.stop_distance if matched_side == "long" else entry_fill + config.stop_distance
                        target = entry_fill + config.take_profit_distance if matched_side == "long" else entry_fill - config.take_profit_distance
                        active = {"side": matched_side, "entry_time": entry_time, "entry_price": entry_fill, "quantity": config.quantity, "stop_price": stop, "take_profit_price": target, "bars_held": 0, "spread": stressed_spread}
                        signal_counts[f"entered_{matched_side}"] += 1
        equity_curve.append(equity)

    open_at_end = None
    if active is not None:
        open_at_end = {key: (str(value) if isinstance(value, (pd.Timestamp, datetime)) else value) for key, value in active.items()}
    signal_counts["long_signal"] = signal_counts["preview_long"]
    signal_counts["short_signal"] = signal_counts["preview_short"]
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    quality = inspect_quality(raw, normalized)
    validation_reasons = []
    if metrics.total_trades < 30:
        validation_reasons.append("too few trades for a six-month-plus sample")
    if metrics.net_return <= 0:
        validation_reasons.append("net return is not positive")
    validation = {"passed": not validation_reasons, "reasons": validation_reasons, "checks": metrics.model_dump() | {"signal_counts": signal_counts}}
    metadata = {
        "strategy_name": strategy_name,
        "dataset_id": dataset_id,
        "feature_set_id": feature_set_id,
        "config_hash": config_hash,
        "run_config": {"builtin": config_payload},
        "backtest_config": config_payload,
        "metrics_summary": metrics.model_dump(),
        "validation_results": validation,
        "signal_counts": signal_counts,
        "data_coverage": quality,
        "monthly_stats": monthly_stats(trades),
        "open_position_at_end": open_at_end,
        "monthly_equity_reset": {"enabled": MONTHLY_EQUITY_RESET, "reset_day": MONTHLY_RESET_DAY, "reset_count": len(monthly_reset_events), "events": monthly_reset_events},
        "limitations": [
            "The canonical root uses numpy.gradient centered-gradient behavior; this is FORENSIC/original live-sim approximation, not causal live-ready evidence.",
            "M5 OHLC cannot verify tick-level T-5/T+0 latency or exact intra-bar path; entry uses closed-bar close and +5-minute timestamp proxy.",
            "Same-bar SL/TP ambiguity is resolved deterministically with SL-first.",
        ],
    }
    artifacts = persist_run_artifacts(
        run_id=run_id,
        raw_spec={"strategy": payload["strategy"], "variant": "FINAL", "symbol": SYMBOL, "timeframe": "M5", "raw_path": str(RAW_PATH), "requested_period_utc": "2026-01-01..now"},
        normalized_spec={"strategy": payload["strategy"], "config": config_payload, "data_coverage": quality},
        metadata=metadata,
        trades=[trade.model_dump() for trade in trades],
        equity_curve=equity_curve,
    )
    open_path = Path(artifacts["trade_log_path"]).parent / "open_position_at_end.json"
    open_path.write_text(json.dumps(open_at_end, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    artifacts["open_position_at_end_path"] = str(open_path)
    xml_path = Path(artifacts["report_path"]).with_suffix(".xml")
    write_xml_summary(xml_path, run_id, metadata, artifacts)
    artifacts["xml_report_path"] = str(xml_path)
    leaderboard_path = persist_leaderboard(build_leaderboard(dataset_id=dataset_id, feature_set_id=feature_set_id))
    artifacts["leaderboard_path"] = str(leaderboard_path)
    print(json.dumps({"run_id": run_id, "strategy_name": strategy_name, "coverage": quality, "money_contract": {"initial_equity": config.initial_equity, "volume": config.volume_lots, "sl_usd": config.risk_usd, "tp_usd": config.reward_usd, "sl_distance": config.stop_distance, "tp_distance": config.take_profit_distance, "reward_risk": config.reward_usd / config.risk_usd, "risk_reward": f"{config.risk_usd}:{config.reward_usd}"}, "metrics": metrics.model_dump(), "signal_counts": signal_counts, "open_position_at_end": open_at_end, "artifacts": artifacts, "validation": validation}, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
