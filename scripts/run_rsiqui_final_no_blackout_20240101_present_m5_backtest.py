from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from trading_lab.data.loaders.csv_loader import load_raw_csv
from trading_lab.data.transforms.normalize import dataset_id_for, normalize_candles, write_processed_dataset
from trading_lab.storage.artifacts import build_leaderboard, persist_leaderboard, persist_run_artifacts, run_id_for
from trading_lab.strategies.builtins.rsiqui.final import rsiqui_v3_config_for_preset, run_rsiqui_v3_backtest

RAW_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSD_M5_202401012305_202607312355_merged.csv"
CONFIG_PATH = ROOT / "configs" / "strategies" / "rsiqui" / "backtests" / "final_no_blackout_20240101_present_m5.json"
SYMBOL = "XAUUSD"
TIMEFRAME = "5m"
START_UTC = pd.Timestamp("2024-01-01T00:00:00Z")
NOW_UTC = pd.Timestamp(datetime.now(UTC))


def finite_float(value):
    try:
        value = float(value)
    except Exception:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


def inspect_quality(frame: pd.DataFrame) -> dict:
    cadence = frame["timestamp"].diff().dropna()
    invalid = (
        (frame["low"] > frame["high"])
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
    )
    return {
        "first_timestamp": str(frame["timestamp"].min()),
        "last_timestamp": str(frame["timestamp"].max()),
        "row_count": int(len(frame)),
        "duplicate_timestamps": int(frame["timestamp"].duplicated().sum()),
        "missing_ohlc": int(frame[["open", "high", "low", "close"]].isna().any(axis=1).sum()),
        "invalid_ohlc": int(invalid.sum()),
        "missing_spread_fraction": float(frame["spread"].isna().mean()) if "spread" in frame else 1.0,
        "median_cadence_minutes": finite_float(cadence.median().total_seconds() / 60) if not cadence.empty else None,
        "max_gap_minutes": finite_float(cadence.max().total_seconds() / 60) if not cadence.empty else None,
    }


def validation_for(metrics, signal_counts: dict, min_trades: int = 30, max_drawdown: float = 0.25) -> dict:
    reasons: list[str] = []
    if metrics.total_trades < min_trades:
        reasons.append("too few trades")
    if metrics.max_drawdown > max_drawdown:
        reasons.append("excessive drawdown")
    if metrics.net_return <= 0:
        reasons.append("net return is not positive")
    return {
        "passed": not reasons,
        "reasons": reasons,
        "checks": metrics.model_dump() | {"signal_counts": signal_counts},
    }


def write_xml_summary(path: Path, run_id: str, metadata: dict, artifacts: dict) -> None:
    root = ET.Element("backtest_run", attrib={"run_id": run_id})
    for key in ["strategy_name", "dataset_id", "feature_set_id", "config_hash"]:
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
    raw = load_raw_csv(RAW_PATH)
    normalized = normalize_candles(raw, SYMBOL, TIMEFRAME)
    normalized = normalized[(normalized["timestamp"] >= START_UTC) & (normalized["timestamp"] <= NOW_UTC)].reset_index(drop=True)
    if normalized.empty:
        raise RuntimeError("No candles after requested date filter")

    dataset_meta = write_processed_dataset(normalized, RAW_PATH, SYMBOL, TIMEFRAME)
    dataset_id = dataset_meta.dataset_id
    feature_payload = f"rsiqui_final_inline::{dataset_id}::{TIMEFRAME}::{len(normalized)}"
    feature_set_id = hashlib.sha256(feature_payload.encode("utf-8")).hexdigest()[:16]

    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
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
    config_payload = config.__dict__ | {
        "strategy": payload["strategy"],
        "timeframe": payload["timeframe"],
        "source_strategy_file": str(ROOT / "strategies" / "builtins" / "rsiqui" / "final.py"),
        "source_config_file": str(CONFIG_PATH),
        "blocked_entry_hours_gmt7": [],
        "blackout_policy": "temporarily disabled for comparison run",
        "cost_model_note": "Uses final.py original backtest engine; slippage_per_side field is passed exactly as configured.",
    }
    strategy_name = (
        f"builtin_rsiqui-v3-final_{payload['preset']}_{payload['side']}_"
        f"vol{float(payload['volume']):g}_risk{float(payload['risk_usd']):g}_reward{float(payload['reward_usd']):g}_"
        f"original_unfiltered_no_blackout_M5_2024_to_present_spreadx{float(payload['spread_multiplier']):g}_"
        f"slip{float(payload['slippage_per_side']):g}_comm{float(payload['commission_per_trade_usd']):g}"
    )
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    run_id = run_id_for(strategy_name, dataset_id, config_hash)

    result = run_rsiqui_v3_backtest(normalized, config)
    validation = validation_for(result.metrics, result.signal_counts)
    quality = inspect_quality(normalized)
    metadata = {
        "strategy_name": strategy_name,
        "dataset_id": dataset_id,
        "feature_set_id": feature_set_id,
        "config_hash": config_hash,
        "run_config": {"builtin": config_payload},
        "backtest_config": config_payload,
        "metrics_summary": result.metrics.model_dump(),
        "validation_results": validation,
        "signal_counts": result.signal_counts,
        "data_coverage": quality,
        "limitations": [
            "final.py is the original/unfiltered RSIQUI implementation and uses numpy.gradient centered-gradient behavior; label as FORENSIC/original, not causal live-ready evidence.",
            "GMT+7 entry blackout intentionally disabled in the duplicated comparison config.",
            "M5 OHLC replay cannot verify tick-level latency or within-bar fill order beyond the strategy engine collision rules.",
        ],
    }
    artifacts = persist_run_artifacts(
        run_id=run_id,
        raw_spec={"strategy": payload["strategy"], "source": "final.py_original_unmodified_no_blackout", "raw_path": str(RAW_PATH)},
        normalized_spec={"strategy": payload["strategy"], "config": config_payload, "data_coverage": quality},
        metadata=metadata,
        trades=[trade.model_dump() for trade in result.trades],
        equity_curve=result.equity_curve,
    )
    xml_path = Path(artifacts["report_path"]).with_suffix(".xml")
    write_xml_summary(xml_path, run_id, metadata, artifacts)
    artifacts["xml_report_path"] = str(xml_path)
    leaderboard = build_leaderboard(dataset_id=dataset_id, feature_set_id=feature_set_id)
    leaderboard_path = persist_leaderboard(leaderboard)

    trades_df = pd.DataFrame([trade.model_dump() for trade in result.trades])
    summary = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "dataset_id": dataset_id,
        "feature_set_id": feature_set_id,
        "config_hash": config_hash,
        "coverage": quality,
        "metrics": result.metrics.model_dump(),
        "signal_counts": result.signal_counts,
        "trade_rows": int(len(trades_df)),
        "initial_equity": config.initial_equity,
        "final_equity": float(result.equity_curve[-1]) if result.equity_curve else config.initial_equity,
        "net_pnl": (float(result.equity_curve[-1]) - config.initial_equity) if result.equity_curve else 0.0,
        "risk_reward": f"{payload['risk_usd']}:{payload['reward_usd']}",
        "artifacts": artifacts | {"leaderboard_path": str(leaderboard_path)},
        "validation": validation,
    }
    print(json.dumps(summary, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
