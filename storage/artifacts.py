from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from trading_lab.config import OUTPUT_BACKTESTS_DIR, OUTPUT_REPORTS_DIR, OUTPUT_SPECS_DIR
from trading_lab.io import write_json


def run_id_for(strategy_name: str, dataset_id: str, config_hash: str = "default") -> str:
    return hashlib.sha256(f"{strategy_name}::{dataset_id}::{config_hash}".encode("utf-8")).hexdigest()[:16]


def persist_run_artifacts(
    run_id: str,
    raw_spec: dict[str, Any],
    normalized_spec: dict[str, Any],
    metadata: dict[str, Any],
    trades: list[dict[str, Any]],
    equity_curve: list[float],
) -> dict[str, str]:
    spec_path = OUTPUT_SPECS_DIR / f"{run_id}.json"
    backtest_dir = OUTPUT_BACKTESTS_DIR / run_id
    report_path = OUTPUT_REPORTS_DIR / f"{run_id}.json"
    markdown_path = OUTPUT_REPORTS_DIR / f"{run_id}.md"
    trade_path = backtest_dir / "trades.csv"
    equity_path = backtest_dir / "equity_curve.csv"
    write_json(spec_path, {"raw": raw_spec, "normalized": normalized_spec})
    backtest_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(trades).to_csv(trade_path, index=False)
    pd.DataFrame({"equity": equity_curve}).to_csv(equity_path, index=False)
    write_json(report_path, metadata)
    markdown_path.write_text(render_run_markdown(run_id, metadata), encoding="utf-8")
    return {
        "spec_path": str(spec_path),
        "trade_log_path": str(trade_path),
        "equity_curve_path": str(equity_path),
        "report_path": str(report_path),
        "markdown_report_path": str(markdown_path),
    }


def build_leaderboard(dataset_id: str | None = None, feature_set_id: str | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for report_path in OUTPUT_REPORTS_DIR.glob("*.json"):
        if report_path.name == "leaderboard.json":
            continue
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if dataset_id and report.get("dataset_id") != dataset_id:
            continue
        if feature_set_id and report.get("feature_set_id") != feature_set_id:
            continue
        metrics = report.get("metrics_summary", {})
        validation = report.get("validation_results", {})
        net_return = _finite_or_default(metrics.get("net_return"), 0.0)
        max_drawdown = _finite_or_default(metrics.get("max_drawdown"), 0.0)
        profit_factor = _finite_or_default(metrics.get("profit_factor"), None)
        rows.append(
            {
                "run_id": report_path.stem,
                "strategy_name": report.get("strategy_name"),
                "dataset_id": report.get("dataset_id"),
                "feature_set_id": report.get("feature_set_id"),
                "passed": validation.get("passed", False),
                "reasons": validation.get("reasons", []),
                "net_return": net_return,
                "max_drawdown": max_drawdown,
                "profit_factor": profit_factor,
                "total_trades": metrics.get("total_trades", 0),
                "report_path": str(report_path),
            }
        )
    rows.sort(key=lambda item: (bool(item["passed"]), float(item["net_return"])), reverse=True)
    return rows


def persist_leaderboard(rows: list[dict[str, Any]]) -> Path:
    leaderboard_path = OUTPUT_REPORTS_DIR / "leaderboard.json"
    write_json(leaderboard_path, {"leaderboard": rows})
    (OUTPUT_REPORTS_DIR / "leaderboard.md").write_text(render_leaderboard_markdown(rows), encoding="utf-8")
    return leaderboard_path


def render_run_markdown(run_id: str, metadata: dict[str, Any]) -> str:
    metrics = metadata.get("metrics_summary", {})
    validation = metadata.get("validation_results", {})
    reasons = validation.get("reasons", [])
    lines = [
        f"# Backtest Run {run_id}",
        "",
        f"- Strategy: `{metadata.get('strategy_name')}`",
        f"- Dataset: `{metadata.get('dataset_id')}`",
        f"- Feature Set: `{metadata.get('feature_set_id')}`",
        f"- Passed: `{validation.get('passed', False)}`",
        "",
        "## Metrics",
        "",
        f"- Net return: `{metrics.get('net_return')}`",
        f"- Max drawdown: `{metrics.get('max_drawdown')}`",
        f"- Sharpe: `{metrics.get('sharpe_ratio')}`",
        f"- Profit factor: `{metrics.get('profit_factor')}`",
        f"- Total trades: `{metrics.get('total_trades')}`",
        "",
        "## Rejection Reasons",
        "",
    ]
    lines.extend([f"- {reason}" for reason in reasons] or ["- none"])
    return "\n".join(lines) + "\n"


def render_leaderboard_markdown(rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Strategy Leaderboard",
        "",
        "| Rank | Strategy | Passed | Net Return | Max DD | Trades | Reasons |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for rank, row in enumerate(rows, start=1):
        reasons = "; ".join(row.get("reasons", [])) or "none"
        lines.append(
            f"| {rank} | `{row.get('strategy_name')}` | {row.get('passed')} | {row.get('net_return')} | "
            f"{row.get('max_drawdown')} | {row.get('total_trades')} | {reasons} |"
        )
    return "\n".join(lines) + "\n"


def _finite_or_default(value: Any, default: float | None) -> float | None:
    if value is None:
        return default
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return default
    return numeric if math.isfinite(numeric) else default
