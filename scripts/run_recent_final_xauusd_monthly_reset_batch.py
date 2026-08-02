from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = Path(r"C:/Users/Maple Razer/outputs/reports")
CONFIG_DIR = ROOT / "configs" / "strategies" / "rsiqui"
RUNNER_DIR = ROOT / "scripts"
BASE_RUNNER = ROOT / "scripts" / "run_rsiqui_final_20260101_present_m5_sl24_tp9_vol003_eq700_spread04_backtest.py"
SKIP_IDS = {"e9344289533a8f91", "3de29dd9125af2c8"}


def build_payload(report: dict, report_path: Path) -> dict:
    source = report["backtest_config"]
    raw_csv = Path(source["raw_csv"])
    return {
        "strategy": "rsiqui-v3-final",
        "preset": "gold-loose",
        "side": source.get("trade_side", "both"),
        "symbol": source["symbol"],
        "timeframe": source["timeframe"],
        "initial_equity": float(source["initial_equity"]),
        "volume": float(source["volume_lots"]),
        "price_value_per_lot": float(source["price_value_per_lot"]),
        "risk_usd": float(source["risk_usd"]),
        "reward_usd": float(source["reward_usd"]),
        "equity_risk_cap_pct": None,
        "synthetic_spread": float(source["synthetic_spread"]),
        "max_spread": float(source["max_spread"]),
        "spread_multiplier": float(source["spread_multiplier"]),
        "slippage_per_side": float(source["slippage_per_side"]),
        "commission_per_trade_usd": float(source["commission_per_trade_usd"]),
        "commission_basis": "Preserved from source run; monthly reset only",
        "monthly_equity_reset": True,
        "monthly_reset_day": 1,
        "blocked_entry_hours_gmt7": source.get("blocked_entry_hours_gmt7", [5, 6, 7, 20, 21]),
        "telegram_enabled": False,
        "status_log_interval_seconds": 300,
        "source_config_file": source.get("source_config_file", ""),
        "raw_csv": str(raw_csv),
        "requested_period_utc": source.get("requested_period_utc", "2026-01-01T00:00:00Z..now"),
        "source_run_id": report_path.stem,
    }


def build_runner(config_path: Path, payload: dict, source_run_id: str) -> str:
    source = BASE_RUNNER.read_text(encoding="utf-8")
    raw_name = Path(payload["raw_csv"]).name
    raw_literals = re.findall(r"XAUUSD_M5_[0-9]+_[0-9]+\.csv", source)
    if len(raw_literals) != 1:
        raise RuntimeError(f"Expected one raw filename literal, found {raw_literals}")
    old_raw = raw_literals[0]
    old_config_literals = re.findall(r"final_m5_backtest_[0-9A-Za-z_]+\.json", source)
    if len(old_config_literals) != 1:
        raise RuntimeError(f"Expected one config filename literal, found {old_config_literals}")
    old_config = old_config_literals[0]
    strategy_match = re.search(r'strategy_name = "([^"]+)"', source)
    if not strategy_match:
        raise RuntimeError("Could not find base strategy name")
    old_strategy = strategy_match.group(1)
    strategy = f"builtin_rsiqui-v3-final_gold-loose_both_vol{payload['volume']:.2f}_risk{payload['risk_usd']:.0f}_reward{payload['reward_usd']:.0f}_spreadcap{payload['max_spread']:.1f}_comm{payload['commission_per_trade_usd']:.2f}_monthly_reset_source_{source_run_id}_M5"
    source = source.replace(old_raw, raw_name, 1)
    source = source.replace(old_config, config_path.name, 1)
    source = source.replace(old_strategy, strategy, 1)
    source = source.replace(
        '"optimization_policy": "none",',
        '"optimization_policy": "none",\n        "monthly_equity_reset": bool(payload.get("monthly_equity_reset", False)),\n        "monthly_reset_day": int(payload.get("monthly_reset_day", 1)),\n        "source_run_id": payload.get("source_run_id"),',
        1,
    )
    source = source.replace(
        'equity = config.initial_equity\n    equity_curve: list[float] = []',
        'equity = config.initial_equity\n    monthly_reset_enabled = bool(payload.get("monthly_equity_reset", False))\n    monthly_reset_day = int(payload.get("monthly_reset_day", 1))\n    monthly_reset_months: set[str] = set()\n    monthly_reset_events: list[dict] = []\n    equity_curve: list[float] = []',
        1,
    )
    source = source.replace(
        '        had_active_at_start = active is not None',
        '        row_month = row["timestamp"].strftime("%Y-%m")\n        if monthly_reset_enabled and row_month not in monthly_reset_months:\n            equity = config.initial_equity\n            monthly_reset_months.add(row_month)\n            monthly_reset_events.append({"month": row_month, "timestamp": str(row["timestamp"]), "reset_equity": config.initial_equity, "active_position_carried": active is not None})\n        had_active_at_start = active is not None',
        1,
    )
    source = source.replace(
        '"open_position_at_end": open_at_end, "limitations":',
        '"open_position_at_end": open_at_end, "monthly_equity_reset": {"enabled": monthly_reset_enabled, "reset_day": monthly_reset_day, "reset_count": len(monthly_reset_events), "events": monthly_reset_events}, "limitations":',
        1,
    )
    return source


def main() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    RUNNER_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now().date()
    targets = []
    for report_path in REPORT_DIR.glob("*.json"):
        if report_path.stem in SKIP_IDS or report_path.name == "leaderboard.json":
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        strategy_name = report.get("strategy_name", "")
        cfg = report.get("backtest_config", {})
        if not strategy_name.startswith("builtin_rsiqui-v3-final") or cfg.get("symbol") != "XAUUSD":
            continue
        if datetime.fromtimestamp(report_path.stat().st_mtime).date() != today:
            continue
        raw_csv = str(cfg.get("raw_csv", ""))
        if "202401012305_202607312355_merged" in raw_csv:
            continue
        targets.append((report_path, report))
    targets.sort(key=lambda item: item[0].stat().st_mtime)
    print(f"TARGET_COUNT={len(targets)}")
    for report_path, report in targets:
        run_id = report_path.stem
        payload = build_payload(report, report_path)
        config_path = CONFIG_DIR / f"final_m5_monthly_reset_source_{run_id}.json"
        runner_path = RUNNER_DIR / f"run_final_m5_monthly_reset_source_{run_id}.py"
        config_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        runner_path.write_text(build_runner(config_path, payload, run_id), encoding="utf-8")
        compile_result = subprocess.run([sys.executable, "-m", "py_compile", str(runner_path)], cwd=ROOT, capture_output=True, text=True)
        if compile_result.returncode:
            raise RuntimeError(f"py_compile failed for {run_id}: {compile_result.stderr}")
        result = subprocess.run([sys.executable, str(runner_path)], cwd=ROOT, capture_output=True, text=True, timeout=900)
        if result.returncode:
            raise RuntimeError(f"backtest failed for {run_id}: {result.stdout[-2000:]}\n{result.stderr[-2000:]}")
        output = json.loads(result.stdout)
        metrics = output["metrics"]
        reset_count = output.get("metadata", {}).get("monthly_equity_reset", {}).get("reset_count") if "metadata" in output else None
        print(json.dumps({"source_run_id": run_id, "new_run_id": output["run_id"], "reset_count": reset_count, "trades": metrics["total_trades"], "validation": output["validation"]["passed"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
