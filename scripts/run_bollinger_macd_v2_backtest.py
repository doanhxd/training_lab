from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from xml.etree.ElementTree import Element, ElementTree, SubElement

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR.parent))

from training_lab.data.loaders.csv_loader import load_raw_csv
from training_lab.strategies.builtins.bollinger_macd_v2 import (
    BollingerMacdV2Config,
    run_bollinger_macd_v2_backtest,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run frozen Bollinger/MACD V2 M5 replay.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--start", default="2026-01-01T00:00:00Z")
    parser.add_argument("--output-root", type=Path, default=PROJECT_DIR / "outputs" / "bollinger_macd_v2")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    frame = load_raw_csv(args.input)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    start = pd.Timestamp(args.start)
    frame = frame.loc[frame["timestamp"] >= start].copy()
    if frame.empty:
        raise ValueError(f"no source candles at or after {start.isoformat()}")

    config = BollingerMacdV2Config(
        volume_lots=0.01,
        price_value_per_lot=100.0,
        risk_usd=5.0,
        reward_usd=5.0,
        fixed_spread=0.2,
        slippage_per_side=0.5,
        trade_side="both",
    )
    result = run_bollinger_macd_v2_backtest(frame, config)
    run_label = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output_dir = args.output_root / run_label
    output_dir.mkdir(parents=True, exist_ok=False)

    trade_rows = [trade.model_dump(mode="json") for trade in result.trades]
    pd.DataFrame(trade_rows).to_csv(output_dir / "trades.csv", index=False)
    equity = pd.DataFrame({"equity": result.equity_curve})
    equity.to_csv(output_dir / "equity_curve.csv", index=False)
    if trade_rows:
        trades = pd.DataFrame(trade_rows)
        trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True)
        trades["month"] = trades["entry_time"].dt.tz_localize(None).dt.to_period("M").astype(str)
        monthly = trades.groupby("month", as_index=False).agg(
            trades=("pnl", "size"), pnl=("pnl", "sum"), win_rate=("pnl", lambda values: float((values > 0).mean())),
            long_trades=("side", lambda values: int((values == "long").sum())), short_trades=("side", lambda values: int((values == "short").sum())),
        )
    else:
        monthly = pd.DataFrame(columns=["month", "trades", "pnl", "win_rate", "long_trades", "short_trades"])
    monthly.to_csv(output_dir / "monthly.csv", index=False)

    summary = {
        "strategy": "BollingerMACD_V2_M5_pullback_continuation",
        "source_csv": str(args.input.resolve()),
        "source_coverage": {"first": frame["timestamp"].min().isoformat(), "last": frame["timestamp"].max().isoformat(), "bars": len(frame)},
        "signal_policy": "closed signal candle; next-bar-open entry; stop-first on same-bar SL/TP collision; signal exits on the confirmation candle close",
        "execution_contract": config.as_dict(),
        "cost_notes": "Entry and exit each receive adverse $0.50 price slippage. Fixed $0.20 spread is charged once per round trip. Commission and swap are omitted.",
        "metrics": result.metrics.model_dump(mode="json"),
        "signal_counts": result.signal_counts,
        "closed_trade_count": len(result.trades),
        "open_trade_at_end": result.open_trade,
        "artifact_dir": str(output_dir.resolve()),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    (output_dir / "contract.json").write_text(json.dumps(config.as_dict(), indent=2), encoding="utf-8")

    report = Element("backtest", strategy=summary["strategy"])
    for key, value in summary["metrics"].items():
        SubElement(report, "metric", name=key, value=str(value))
    for key, value in result.signal_counts.items():
        SubElement(report, "signal", name=key, value=str(value))
    ElementTree(report).write(output_dir / "report.xml", encoding="utf-8", xml_declaration=True)

    metrics = summary["metrics"]
    report_md = f"""# BollingerMACD V2 M5 replay\n\n- Coverage: `{summary['source_coverage']['first']}` through `{summary['source_coverage']['last']}` ({summary['source_coverage']['bars']:,} M5 bars)\n- Contract: volume `0.01`, risk `$5`, reward `$5`, fixed spread `$0.20`, adverse slippage `$0.50` per side.\n- Closed trades: `{len(result.trades)}`; open position at EOF: `{bool(result.open_trade)}`.\n- Net return: `{metrics['net_return']:.6f}`; max drawdown: `{metrics['max_drawdown']:.6f}`; profit factor: `{metrics['profit_factor']}`; win rate: `{metrics['win_rate']:.4f}`; expectancy: `${metrics['expectancy']:.4f}`.\n\nThis is a deterministic research replay, not evidence for live deployment. Commission and swap are omitted.\n"""
    (output_dir / "report.md").write_text(report_md, encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
