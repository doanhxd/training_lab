from __future__ import annotations

import csv
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))

from training_lab.data.loaders.csv_loader import load_raw_csv
from training_lab.strategies.builtins.risk_one_troll import RiskOneTrollConfig, backtest_risk_one_troll

CONFIG_PATH = ROOT / "configs" / "strategies" / "risk_one_troll_xauusdc_20260101_present_eq1000_vol005.json"
M5_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSDc_M5_202601012305_202608130240.csv"
H1_PATH = ROOT / "data" / "raw" / "mt5_exports" / "XAUUSDc_H1_202601012300_202608130200.csv"
OUTPUT_ROOT = Path.home() / "outputs" / "risk_one_troll"
START = pd.Timestamp("2026-01-01T00:00:00Z")


def quality(frame: pd.DataFrame) -> dict:
    d = frame["timestamp"].diff().dropna()
    invalid = ((frame["low"] > frame["high"]) | (frame["high"] < frame[["open", "close"]].max(axis=1)) | (frame["low"] > frame[["open", "close"]].min(axis=1)))
    return {
        "rows": int(len(frame)), "first_utc": str(frame["timestamp"].min()), "last_utc": str(frame["timestamp"].max()),
        "duplicates": int(frame["timestamp"].duplicated().sum()), "invalid_ohlc": int(invalid.sum()),
        "median_cadence_minutes": float(d.median().total_seconds() / 60) if not d.empty else None,
        "max_gap_minutes": float(d.max().total_seconds() / 60) if not d.empty else None,
        "missing_spread_fraction": float(frame["spread"].isna().mean()) if "spread" in frame else 1.0,
        "spread_min": float(frame["spread"].min()) if "spread" in frame else None,
        "spread_median": float(frame["spread"].median()) if "spread" in frame else None,
        "spread_max": float(frame["spread"].max()) if "spread" in frame else None,
    }


def metrics(trades: pd.DataFrame, initial: float) -> dict:
    if trades.empty:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "gross_profit": 0.0, "gross_loss": 0.0, "profit_factor": None, "net_pnl": 0.0, "final_equity": initial, "net_return": 0.0, "expectancy": 0.0}
    pnl = pd.to_numeric(trades["pnl"], errors="coerce").fillna(0.0)
    wins = int((pnl > 0).sum()); losses = int((pnl < 0).sum())
    gp = float(pnl[pnl > 0].sum()); gl = float(-pnl[pnl < 0].sum())
    net = float(pnl.sum())
    return {"total_trades": int(len(pnl)), "wins": wins, "losses": losses, "win_rate": wins / len(pnl), "gross_profit": gp, "gross_loss": gl, "profit_factor": gp / gl if gl else None, "net_pnl": net, "final_equity": initial + net, "net_return": net / initial, "expectancy": net / len(pnl)}


def main() -> None:
    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    m5 = load_raw_csv(M5_PATH); h1 = load_raw_csv(H1_PATH)
    for frame in (m5, h1):
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
        for col in ["open", "high", "low", "close", "spread"]:
            if col in frame: frame[col] = pd.to_numeric(frame[col], errors="coerce")
    m5 = m5[m5["timestamp"] >= START].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    h1 = h1[h1["timestamp"] >= START - pd.Timedelta(hours=3)].sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    if m5.empty or h1.empty: raise RuntimeError("empty filtered M5/H1 data")
    cfg = RiskOneTrollConfig(sl_price_distance=payload["sl_price_distance"], tp_price_distance=payload["tp_price_distance"], volume_lots=payload["volume_lots"], price_value_per_lot=payload["price_value_per_lot"], initial_equity=payload["initial_equity"], commission_per_trade_usd=0.0, same_bar_collision=payload["same_bar_collision"])
    raw_trades = backtest_risk_one_troll(m5, h1, cfg)
    spread_series = m5.set_index("timestamp")["spread"] if "spread" in m5 else pd.Series(dtype=float)
    rows=[]
    for t in raw_trades:
        entry_spread = float(spread_series.get(t.entry_time, 0.0) or 0.0); exit_spread = float(spread_series.get(t.exit_time, entry_spread) or entry_spread)
        spread_cost = ((entry_spread + exit_spread) / 2.0) * payload["volume_lots"] * payload["price_value_per_lot"]
        commission = payload["commission_per_trade_usd"]
        net_pnl = t.pnl - spread_cost - commission
        rows.append({"side":t.side,"entry_time":t.entry_time,"exit_time":t.exit_time,"entry_price":t.entry_price,"exit_price":t.exit_price,"pnl":net_pnl,"gross_pnl":t.pnl,"spread_cost":spread_cost,"commission":commission,"exit_reason":t.exit_reason,"reversal_index":t.reversal_index})
    trades=pd.DataFrame(rows, columns=["side","entry_time","exit_time","entry_price","exit_price","pnl","gross_pnl","spread_cost","commission","exit_reason","reversal_index"])
    blocked_after_bankruptcy = 0
    if not trades.empty and payload.get("bankruptcy_policy") == "stop_new_entries_after_equity_lte_0":
        running_equity = float(payload["initial_equity"])
        keep = []
        bankrupt = False
        for _, trade in trades.iterrows():
            if bankrupt:
                blocked_after_bankruptcy += 1
                continue
            keep.append(trade.to_dict())
            running_equity += float(trade["pnl"])
            if running_equity <= 0:
                bankrupt = True
        trades = pd.DataFrame(keep, columns=trades.columns)
    run_payload=payload | {"engine_version":"risk_one_troll_replay_v2_bankruptcy_finalized","source_m5":str(M5_PATH),"source_h1":str(H1_PATH),"m5_coverage":quality(m5),"h1_coverage":quality(h1),"entry_policy":"first M5 at/after 08:00 GMT+7; next available bar after exit","h1_availability":"H1 open + 1 hour","cost_policy":"observed CSV spread averaged at entry/exit plus commission per completed trade","source_strategy_file":str(ROOT/'strategies'/'builtins'/'risk_one_troll.py')}
    digest=hashlib.sha256(json.dumps(run_payload,sort_keys=True,default=str).encode()).hexdigest()[:16]
    run_id=f"risk_one_troll_{digest}"
    out=OUTPUT_ROOT/run_id; out.mkdir(parents=True,exist_ok=True)
    trades.to_csv(out/'trades.csv',index=False)
    equity=[payload["initial_equity"]];
    for value in trades["pnl"].tolist() if not trades.empty else []: equity.append(equity[-1]+float(value))
    pd.DataFrame({"equity":equity}).to_csv(out/'equity_curve.csv',index=False)
    result={"run_id":run_id,"strategy":"risk_one_troll","config":run_payload,"m5_quality":quality(m5),"h1_quality":quality(h1),"metrics":metrics(trades,payload["initial_equity"]),"signal_counts":{"completed_trades":len(trades),"sl_exits":int((trades['exit_reason']=='sl').sum()) if not trades.empty else 0,"tp_exits":int((trades['exit_reason']=='tp').sum()) if not trades.empty else 0,"session_cutoff_exits":int((trades['exit_reason']=='session_cutoff').sum()) if not trades.empty else 0,"blocked_after_bankruptcy":blocked_after_bankruptcy},"artifacts":{"trades":str(out/'trades.csv'),"equity":str(out/'equity_curve.csv'),"report":str(out/'report.json')}}
    (out/'report.json').write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    print(json.dumps(result,indent=2,default=str))

if __name__ == '__main__': main()
