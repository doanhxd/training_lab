from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = Path.home() / "outputs"
REPORTS = OUTPUTS / "reports"
BACKTESTS = OUTPUTS / "backtests"
MANIFEST_JS = ROOT / "backtest_report_manifest.js"


def fmt_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat().replace("+00:00", "Z")


def compute_monthly(trades: pd.DataFrame, initial_equity: float, monthly_reset: bool = False) -> list[dict]:
    if trades.empty:
        return []
    work = trades.copy()
    work["exit_time"] = pd.to_datetime(work["exit_time"], utc=True, errors="coerce")
    work["pnl"] = pd.to_numeric(work["pnl"], errors="coerce")
    work["bars_held"] = pd.to_numeric(work.get("bars_held"), errors="coerce")
    work = work.dropna(subset=["exit_time", "pnl"])
    if work.empty:
        return []
    work["month"] = work["exit_time"].dt.strftime("%Y-%m")
    rows: list[dict] = []
    running = float(initial_equity)
    for month, group in work.groupby("month", sort=True):
        if monthly_reset:
            running = float(initial_equity)
        pnl = float(group["pnl"].sum())
        wins = int((group["pnl"] >= 0).sum())
        losses = int((group["pnl"] < 0).sum())
        gross_win = float(group.loc[group["pnl"] >= 0, "pnl"].sum())
        gross_loss = float((-group.loc[group["pnl"] < 0, "pnl"]).sum())
        trades = int(len(group))
        running += pnl
        rows.append(
            {
                "month": month,
                "trades": trades,
                "wins": wins,
                "losses": losses,
                "winRate": wins / trades if trades else 0.0,
                "pnl": pnl,
                "profitFactor": (gross_win / gross_loss) if gross_loss > 0 else None,
                "avgBars": float(group["bars_held"].mean()) if trades else 0.0,
                "endEquity": running,
            }
        )
    return rows


def compute_daily_drawdown_stats(trades: pd.DataFrame, initial_equity: float, monthly_reset: bool = False) -> dict:
    if trades.empty:
        return {}
    work = trades.copy()
    work["exit_time"] = pd.to_datetime(work["exit_time"], utc=True, errors="coerce")
    work["pnl"] = pd.to_numeric(work["pnl"], errors="coerce")
    work["bars_held"] = pd.to_numeric(work.get("bars_held"), errors="coerce")
    work["side"] = work.get("side", "").astype(str).str.lower()
    work = work.dropna(subset=["exit_time", "pnl"]).sort_values("exit_time")
    if work.empty:
        return {}
    running = float(initial_equity)
    rows: list[dict] = []
    for day, group in work.groupby(work["exit_time"].dt.strftime("%Y-%m-%d"), sort=True):
        if monthly_reset and (not rows or day[:7] != rows[-1]["day"][:7]):
            running = float(initial_equity)
        day_start = running
        intraday_equity = day_start
        intraday_min = day_start
        for pnl in group["pnl"].tolist():
            intraday_equity += float(pnl)
            intraday_min = min(intraday_min, intraday_equity)
        running = intraday_equity
        trades_count = int(len(group))
        wins = int((group["pnl"] >= 0).sum())
        losses = int((group["pnl"] < 0).sum())
        gross_win = float(group.loc[group["pnl"] >= 0, "pnl"].sum())
        gross_loss = float((-group.loc[group["pnl"] < 0, "pnl"]).sum())
        pnl_sum = float(group["pnl"].sum())
        dd_usd = max(0.0, day_start - intraday_min)
        dd = 0.0 if day_start <= 0 else dd_usd / day_start
        rows.append(
            {
                "day": day,
                "start_equity": day_start,
                "end_equity": running,
                "min_intraday_equity": intraday_min,
                "daily_drawdown_usd": dd_usd,
                "daily_drawdown": dd,
                "trade_count": trades_count,
                "trades": trades_count,
                "wins": wins,
                "losses": losses,
                "winRate": wins / trades_count if trades_count else 0.0,
                "pnl": pnl_sum,
                "profitFactor": (gross_win / gross_loss) if gross_loss > 0 else None,
                "expectancy": pnl_sum / trades_count if trades_count else 0.0,
                "avgBars": float(group["bars_held"].mean()) if trades_count else 0.0,
                "long": int((group["side"] == "long").sum()),
                "short": int((group["side"] == "short").sum()),
            }
        )
    if not rows:
        return {}
    worst = max(rows, key=lambda x: x["daily_drawdown"])
    latest = rows[-1]
    return {
        "latest_day": latest["day"],
        "latest_daily_drawdown": latest["daily_drawdown"],
        "latest_daily_drawdown_usd": latest["daily_drawdown_usd"],
        "max_daily_drawdown": worst["daily_drawdown"],
        "max_daily_drawdown_usd": worst["daily_drawdown_usd"],
        "worst_day": worst["day"],
        "daily_rows": rows,
    }


def attach_monthly_drawdown(monthly: list[dict], daily_rows: list[dict]) -> list[dict]:
    """Add month-level DD plus worst daily-DD context to each monthly row."""
    by_month: dict[str, list[dict]] = {}
    for row in daily_rows:
        month = str(row.get("day", ""))[:7]
        if month:
            by_month.setdefault(month, []).append(row)

    for month in monthly:
        days = by_month.get(month["month"], [])
        if not days:
            month.update(
                {
                    "drawdownUsd": 0.0,
                    "drawdownPct": 0.0,
                    "maxDailyDrawdownUsd": 0.0,
                    "maxDailyDrawdownPct": 0.0,
                    "worstDay": None,
                }
            )
            continue

        start_equity = float(days[0]["start_equity"])
        min_intraday_equity = min(float(day["min_intraday_equity"]) for day in days)
        drawdown_usd = max(0.0, start_equity - min_intraday_equity)
        drawdown_pct = drawdown_usd / start_equity if start_equity > 0 else 0.0
        worst = max(days, key=lambda day: float(day.get("daily_drawdown", 0.0)))
        month.update(
            {
                "drawdownUsd": drawdown_usd,
                "drawdownPct": drawdown_pct,
                "maxDailyDrawdownUsd": float(worst.get("daily_drawdown_usd", 0.0)),
                "maxDailyDrawdownPct": float(worst.get("daily_drawdown", 0.0)),
                "worstDay": worst.get("day"),
            }
        )
    return monthly


def sample_equity(df: pd.DataFrame, max_points: int = 720) -> list[dict]:
    if df.empty or "equity" not in df.columns:
        return []
    work = df.copy()
    work["equity"] = pd.to_numeric(work["equity"], errors="coerce")
    work = work.dropna(subset=["equity"]).reset_index(drop=True)
    if work.empty:
        return []
    if len(work) <= max_points:
        idxs = list(range(len(work)))
    else:
        step = len(work) / (max_points - 1)
        idxs = sorted({min(len(work) - 1, round(i * step)) for i in range(max_points)})
        if idxs[-1] != len(work) - 1:
            idxs.append(len(work) - 1)
    return [{"index": int(i), "equity": float(work.iloc[i]["equity"])} for i in idxs]


def trade_sample(trades: pd.DataFrame, count: int = 10) -> list[dict]:
    if trades.empty:
        return []
    cols = [c for c in ["side", "entry_time", "exit_time", "entry_price", "exit_price", "quantity", "pnl", "bars_held"] if c in trades.columns]
    sample = trades.loc[:, cols].head(count).copy()
    for col in ["entry_price", "exit_price", "quantity", "pnl", "bars_held"]:
        if col in sample.columns:
            sample[col] = pd.to_numeric(sample[col], errors="coerce")
    return json.loads(sample.to_json(orient="records", date_format="iso"))


def main() -> None:
    entries: list[dict] = []
    for report_path in REPORTS.glob("*.json"):
        if report_path.name == "leaderboard.json":
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        run_id = report_path.stem
        backtest_dir = BACKTESTS / run_id
        trades_path = backtest_dir / "trades.csv"
        equity_path = backtest_dir / "equity_curve.csv"
        trades_df = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
        equity_df = pd.read_csv(equity_path) if equity_path.exists() else pd.DataFrame()
        initial_equity = float(report.get("backtest_config", {}).get("initial_equity", 0.0) or 0.0)
        monthly_reset = bool(report.get("monthly_equity_reset", {}).get("enabled", False))
        daily_drawdown = compute_daily_drawdown_stats(trades_df, initial_equity, monthly_reset)
        monthly = attach_monthly_drawdown(
            compute_monthly(trades_df, initial_equity, monthly_reset),
            daily_drawdown.get("daily_rows", []),
        )
        metrics = report.get("metrics_summary", {})
        created_ts = report_path.stat().st_mtime
        entries.append(
            {
                "run_id": run_id,
                "report_file": str(report_path),
                "trades_file": str(trades_path) if trades_path.exists() else None,
                "equity_file": str(equity_path) if equity_path.exists() else None,
                "modified_at": fmt_iso(created_ts),
                "modified_ts": created_ts,
                "strategy_name": report.get("strategy_name"),
                "dataset_id": report.get("dataset_id"),
                "feature_set_id": report.get("feature_set_id"),
                "backtest_config": report.get("backtest_config", {}),
                "data_coverage": report.get("data_coverage", {}),
                "metrics_summary": metrics,
                "validation_results": report.get("validation_results", {}),
                "signal_counts": report.get("signal_counts", {}),
                "daily_drawdown": daily_drawdown,
                "trade_rows": int(len(trades_df)),
                "equity_rows": int(len(equity_df)),
                "monthly": monthly,
                "trade_sample": trade_sample(trades_df),
                "equity_sample": sample_equity(equity_df),
                "label": f"{fmt_iso(created_ts)[:19].replace('T', ' ')} · {run_id} · {report.get('strategy_name', 'unknown')}",
            }
        )
    entries.sort(key=lambda item: item["modified_ts"], reverse=True)
    payload = {
        "generated_at": fmt_iso(datetime.now(tz=UTC).timestamp()),
        "root_outputs": str(OUTPUTS),
        "count": len(entries),
        "runs": entries,
    }
    MANIFEST_JS.write_text("window.BACKTEST_REPORT_MANIFEST = " + json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + ";\n", encoding="utf-8")
    print(json.dumps({"manifest": str(MANIFEST_JS), "count": len(entries), "latest_run": entries[0]["run_id"] if entries else None}, ensure_ascii=False))


if __name__ == "__main__":
    main()
