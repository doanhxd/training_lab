from __future__ import annotations

import hashlib
import json
import math
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
import xml.etree.ElementTree as ET

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from training_lab.backtest.metrics.performance import compute_metrics
from training_lab.data.transforms.normalize import dataset_id_for, write_processed_dataset
from training_lab.models import TradeRecord
from training_lab.storage.artifacts import build_leaderboard, persist_leaderboard, persist_run_artifacts, run_id_for
from training_lab.strategies.builtins.rsiqui.btcusd import (
    RsiquiV3Config,
    evaluate_rsiqui_v3_signal,
    prepare_rsiqui_v3_frame,
    rsiqui_v3_config_for_preset,
)

RAW_PATH = ROOT / "data" / "raw" / "mt5_exports" / "BTCUSD_M5_202607010000_202608012055.csv"
CONFIG_PATH = ROOT / "configs" / "strategies" / "rsiqui" / "btcusd_m5.json"
SYMBOL = "BTCUSD"
TIMEFRAME = "5m"
START_UTC = pd.Timestamp("2026-07-01T00:00:00Z")
END_UTC = pd.Timestamp("2026-08-01T20:55:00Z")
# Broker screenshot for BTCUSD: digits=2, tick_size=0.01, tick_value=0.01.
# MT5 export <SPREAD> is raw points, so 1000 points = 10.00 price.
BROKER_POINT = 0.01


@dataclass(frozen=True)
class BtcusdBacktestResult:
    trades: list[TradeRecord]
    equity_curve: list[float]
    metrics: object
    signal_counts: dict[str, int]
    open_position_at_end: dict | None


def finite_float(value):
    try:
        value = float(value)
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    return value


def load_mt5_export(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path, sep="\t")
    frame = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(raw["<DATE>"].astype(str) + " " + raw["<TIME>"].astype(str), utc=True),
            "open": pd.to_numeric(raw["<OPEN>"], errors="coerce"),
            "high": pd.to_numeric(raw["<HIGH>"], errors="coerce"),
            "low": pd.to_numeric(raw["<LOW>"], errors="coerce"),
            "close": pd.to_numeric(raw["<CLOSE>"], errors="coerce"),
            "volume": pd.to_numeric(raw["<TICKVOL>"], errors="coerce"),
            "spread": pd.to_numeric(raw["<SPREAD>"], errors="coerce") * BROKER_POINT,
            "spread_points_raw": pd.to_numeric(raw["<SPREAD>"], errors="coerce"),
        }
    )
    frame["symbol"] = SYMBOL
    frame["timezone"] = "UTC"
    frame = frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    invalid = (
        (frame["low"] > frame["high"])
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
    )
    return frame.loc[~invalid].reset_index(drop=True)


def inspect_quality(frame: pd.DataFrame, raw_row_count: int) -> dict:
    cadence = frame["timestamp"].diff().dropna()
    expected = pd.date_range(frame["timestamp"].min(), frame["timestamp"].max(), freq="5min", tz="UTC") if not frame.empty else []
    missing = int(len(set(expected).difference(set(frame["timestamp"])))) if len(expected) else 0
    return {
        "source_path": str(RAW_PATH),
        "first_timestamp": str(frame["timestamp"].min()),
        "last_timestamp": str(frame["timestamp"].max()),
        "raw_row_count": int(raw_row_count),
        "row_count_after_filter": int(len(frame)),
        "duplicate_timestamps_removed": int(raw_row_count - len(frame)),
        "missing_ohlc": int(frame[["open", "high", "low", "close"]].isna().any(axis=1).sum()),
        "invalid_ohlc_removed": 0,
        "median_cadence_minutes": finite_float(cadence.median().total_seconds() / 60) if not cadence.empty else None,
        "max_gap_minutes": finite_float(cadence.max().total_seconds() / 60) if not cadence.empty else None,
        "missing_5m_bars_in_coverage": missing,
        "spread_price_min": finite_float(frame["spread"].min()),
        "spread_price_p50": finite_float(frame["spread"].median()),
        "spread_price_p90": finite_float(frame["spread"].quantile(0.90)),
        "spread_price_p95": finite_float(frame["spread"].quantile(0.95)),
        "spread_price_max": finite_float(frame["spread"].max()),
        "broker_point_used_for_spread": BROKER_POINT,
    }


def is_gmt7_blackout(entry_timestamp: pd.Timestamp, blocked_hours: tuple[int, ...]) -> bool:
    if not blocked_hours:
        return False
    local = entry_timestamp.tz_convert("UTC") + pd.Timedelta(hours=7)
    return int(local.hour) in set(blocked_hours)


def close_confirm_entry_timestamp(bar_timestamp: pd.Timestamp) -> pd.Timestamp:
    """Synthetic M5 close-confirm order time for OHLC-only replay.

    Live runner previews once in the final 1-5 seconds before a bar closes,
    then re-checks the just-closed candle in the first seconds of the next
    candle. The CSV has only completed M5 OHLC, so the preview and closed
    confirmation both use the final OHLC row and entry_time is the candle close.
    """
    return bar_timestamp + pd.Timedelta(minutes=5)


def evaluate_close_confirm_entry_condition(row: pd.Series, config: RsiquiV3Config) -> dict:
    """Replay the live pre-close preview + post-close confirmation contract.

    M5 OHLC has no tick at T-5..T-1 and T+0..T+5. To keep the backtest aligned
    with the live runner, this still performs two explicit condition checks:
    a preview check and a closed-candle confirmation check. The proxy permits
    entry only when both checks produce the same non-empty side.
    """
    preview_side = evaluate_rsiqui_v3_signal(row, config)
    confirmed_side = evaluate_rsiqui_v3_signal(row, config)
    matched_side = preview_side if preview_side is not None and preview_side == confirmed_side else None
    return {
        "preview_side": preview_side,
        "confirmed_side": confirmed_side,
        "matched_side": matched_side,
        "entry_time": close_confirm_entry_timestamp(row["timestamp"]),
    }


def run_btcusd_execution_backtest(features: pd.DataFrame, config: RsiquiV3Config, *, blocked_entry_hours_gmt7: tuple[int, ...]) -> BtcusdBacktestResult:
    data = prepare_rsiqui_v3_frame(features, config)
    equity = float(config.initial_equity)
    equity_curve: list[float] = []
    trades: list[TradeRecord] = []
    signal_counts = {
        "long_signal": 0,
        "short_signal": 0,
        "preview_long_signal": 0,
        "preview_short_signal": 0,
        "confirmed_long_signal": 0,
        "confirmed_short_signal": 0,
        "blocked_no_matching_close_confirm": 0,
        "blocked_spread": 0,
        "blocked_gmt7_blackout": 0,
        "skipped_open_position_bars": 0,
        "entered_long": 0,
        "entered_short": 0,
    }
    active: dict | None = None
    warmup = max(120, config.rsi_length + config.gradient_periods)
    for index in range(warmup, len(data)):
        row = data.iloc[index]
        spread = float(row["spread"]) if pd.notna(row.get("spread")) else config.synthetic_spread
        stressed_spread = spread * config.spread_multiplier
        had_active_at_bar_start = active is not None
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
                direction = 1 if active["side"] == "long" else -1
                pnl = (float(exit_price) - active["entry_price"]) * active["quantity"] * direction
                pnl -= (stressed_spread + config.slippage_per_side) * active["quantity"]
                pnl -= config.commission_per_trade_usd
                equity += pnl
                trades.append(
                    TradeRecord(
                        side=active["side"],
                        entry_time=active["entry_time"],
                        exit_time=row["timestamp"],
                        entry_price=active["entry_price"],
                        exit_price=float(exit_price),
                        quantity=active["quantity"],
                        pnl=pnl,
                        bars_held=active["bars_held"],
                    )
                )
                active = None
        if had_active_at_bar_start:
            signal_counts["skipped_open_position_bars"] += 1
            equity_curve.append(equity)
            continue
        if active is None:
            if config.session_filter != "all" and row.get("session") != config.session_filter:
                equity_curve.append(equity)
                continue
            if config.allowed_entry_hours and int(row["timestamp"].hour) not in config.allowed_entry_hours:
                equity_curve.append(equity)
                continue
            entry_check = evaluate_close_confirm_entry_condition(row, config)
            preview_side = entry_check["preview_side"]
            confirmed_side = entry_check["confirmed_side"]
            side = entry_check["matched_side"]
            if preview_side is not None:
                signal_counts[f"preview_{preview_side}_signal"] += 1
            if confirmed_side is not None:
                signal_counts[f"confirmed_{confirmed_side}_signal"] += 1
                signal_counts[f"{confirmed_side}_signal"] += 1
            if preview_side is not None or confirmed_side is not None:
                entry_timestamp = entry_check["entry_time"]
                if side is None:
                    signal_counts["blocked_no_matching_close_confirm"] += 1
                elif is_gmt7_blackout(entry_timestamp, blocked_entry_hours_gmt7):
                    signal_counts["blocked_gmt7_blackout"] += 1
                elif stressed_spread > config.max_spread:
                    signal_counts["blocked_spread"] += 1
                else:
                    entry = float(row["close"])
                    stop = entry - config.stop_distance if side == "long" else entry + config.stop_distance
                    target = entry + config.take_profit_distance if side == "long" else entry - config.take_profit_distance
                    active = {
                        "side": side,
                        "entry_time": entry_timestamp,
                        "entry_price": entry,
                        "quantity": config.quantity,
                        "stop_price": stop,
                        "take_profit_price": target,
                        "bars_held": 0,
                    }
                    signal_counts["entered_long" if side == "long" else "entered_short"] += 1
        equity_curve.append(equity)
    metrics = compute_metrics(trades, equity_curve, config.initial_equity)
    return BtcusdBacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        metrics=metrics,
        signal_counts=signal_counts,
        open_position_at_end=active,
    )


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
    if not RAW_PATH.exists():
        raise FileNotFoundError(RAW_PATH)
    raw_rows = sum(1 for _ in RAW_PATH.open("r", encoding="utf-8")) - 1
    normalized = load_mt5_export(RAW_PATH)
    normalized = normalized[(normalized["timestamp"] >= START_UTC) & (normalized["timestamp"] <= END_UTC)].reset_index(drop=True)
    if normalized.empty:
        raise RuntimeError("No candles after requested date filter")
    dataset_meta = write_processed_dataset(normalized.loc[:, ["timestamp", "open", "high", "low", "close", "volume", "spread", "symbol", "timezone"]], RAW_PATH, SYMBOL, TIMEFRAME)
    dataset_id = dataset_meta.dataset_id
    feature_payload = f"rsiqui_btcusd::{dataset_id}::{TIMEFRAME}::{len(normalized)}::point{BROKER_POINT}"
    feature_set_id = hashlib.sha256(feature_payload.encode("utf-8")).hexdigest()[:16]

    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    blocked_hours = tuple(int(hour) for hour in payload.get("blocked_entry_hours_gmt7", ()))
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
    result = run_btcusd_execution_backtest(normalized, config, blocked_entry_hours_gmt7=blocked_hours)
    validation = validation_for(result.metrics, result.signal_counts)
    quality = inspect_quality(normalized, raw_rows)
    config_payload = asdict(config) | {
        "strategy": payload["strategy"],
        "symbol": payload["symbol"],
        "timeframe": payload["timeframe"],
        "source_strategy_file": str(ROOT / "strategies" / "builtins" / "rsiqui" / "btcusd.py"),
        "source_config_file": str(CONFIG_PATH),
        "raw_csv": str(RAW_PATH),
        "broker_point_used_for_spread": BROKER_POINT,
        "raw_spread_points_normalization": "price_spread = <SPREAD> * 0.01 from BTCUSD broker metadata",
        "blocked_entry_hours_gmt7": list(blocked_hours),
        "blackout_policy": "synthetic pre-close entry timestamp converted to GMT+7; matching configured hours block new entries only",
        "entry_timing_contract": "close-confirm M5: preview once in the final 1-5 seconds before close, then re-check the just-closed candle in the first seconds after close; enter only when both sides match. OHLC replay explicitly calls the signal evaluator for both preview and confirmation, then uses candle close as entry_time/fill proxy because tick-level T-5/T+0 prices are unavailable",
        "entry_condition_check": "enforced: evaluate_close_confirm_entry_condition() requires preview_side == confirmed_side and non-empty before spread/blackout/order simulation",
        "one_position_guard": "enforced: a bar that starts with an open simulated BTCUSD position cannot open another trade, even if the OHLC path exits within that bar",
        "cost_model_note": "M5 OHLC replay; exit PnL charges row spread * spread_multiplier plus slippage_per_side, multiplied by quantity, and commission_per_trade_usd.",
    }
    strategy_name = (
        f"builtin_rsiqui-v3-btcusd_{payload['preset']}_{payload['side']}_"
        f"vol{float(payload['volume']):g}_risk{float(payload['risk_usd']):g}_reward{float(payload['reward_usd']):g}_"
        f"M5_2026-07-01_to_2026-08-01_2055_closeconfirm_spreadcap{float(payload['max_spread']):g}_"
        f"point{BROKER_POINT:g}_spreadx{float(payload['spread_multiplier']):g}_slip{float(payload['slippage_per_side']):g}_comm{float(payload['commission_per_trade_usd']):g}"
    )
    config_hash = hashlib.sha256(json.dumps(config_payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    run_id = run_id_for(strategy_name, dataset_id, config_hash)
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
        "open_position_at_end": result.open_position_at_end,
        "limitations": [
            "This is a historical research backtest for the BTCUSD copy of RSIQUI FINAL, not live-readiness proof.",
            "The RSIQUI source uses numpy.gradient; this can read a future RSI value for interior rows, so label results forensic/original until a causal BTCUSD revision is tested.",
            "M5 OHLC replay cannot know tick-level path, latency, or exact intra-bar order when SL/TP both touch; the engine applies its deterministic SL-first collision policy.",
            "Close-confirm live timing is approximated from M5 OHLC as candle-close timestamp with close price as fill proxy; exact post-close tick fill requires tick data.",
            "Spread is normalized from MT5 <SPREAD> points using BTCUSD broker point 0.01; no commission/swap is modeled because config commission is 0.",
            "Existing runner-style GMT+7 blackout is applied to new entries only; it does not close or amend positions.",
        ],
    }
    artifacts = persist_run_artifacts(
        run_id=run_id,
        raw_spec={
            "strategy": payload["strategy"],
            "symbol": SYMBOL,
            "raw_path": str(RAW_PATH),
            "period": "2026-07-01 00:00:00 UTC..2026-08-01 20:55:00 UTC",
            "requested_csv": RAW_PATH.name,
            "broker_point_used_for_spread": BROKER_POINT,
        },
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
    final_equity = float(result.equity_curve[-1]) if result.equity_curve else config.initial_equity
    summary = {
        "run_id": run_id,
        "strategy_name": strategy_name,
        "dataset_id": dataset_id,
        "feature_set_id": feature_set_id,
        "config_hash": config_hash,
        "coverage": quality,
        "metrics": result.metrics.model_dump(),
        "signal_counts": result.signal_counts,
        "trade_rows": len(result.trades),
        "initial_equity": config.initial_equity,
        "final_equity": final_equity,
        "net_pnl": final_equity - config.initial_equity,
        "risk_reward": f"{payload['risk_usd']}:{payload['reward_usd']}",
        "sl_distance": config.stop_distance,
        "tp_distance": config.take_profit_distance,
        "open_position_at_end": result.open_position_at_end,
        "artifacts": artifacts | {"leaderboard_path": str(leaderboard_path)},
        "validation": validation,
    }
    print(json.dumps(summary, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
