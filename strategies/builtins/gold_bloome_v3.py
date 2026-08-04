"""
Gold 5-Min Scalping Strategy Backtest v3
Custom params: 0.01 lot fixed, SL=$10, TP=$10, R:R 1:1
"""

import pandas as pd
import numpy as np
from datetime import datetime
import sys, warnings
warnings.filterwarnings("ignore")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CONFIG
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
INITIAL_CAPITAL        = 200.0
RISK_PER_TRADE         = 1.00        # not used (fixed lot)
RISK_REWARD_RATIO      = 1.0         # 1:1
FIXED_LOT              = 0.01        # fixed lot size
FIXED_SL_DOLLARS       = 10.0        # $10 stop loss
FIXED_TP_DOLLARS       = 10.0        # $10 take profit
MAX_TRADES_PER_SESSION = 3
MAX_DAILY_LOSS_PCT     = 0.15        # 15% daily loss → stop
CONSECUTIVE_LOSS_LIMIT = 2

# Trading costs
SPREAD_DOLLARS         = 0.25
SLIPPAGE_DOLLARS       = 0.05
COMMISSION_PER_TRADE   = 0.0

# Gold specs
PIP             = 0.01
PIP_VALUE_PER_LOT = 1.0
LOT_STEP        = 0.01
LOT_MIN         = 0.01
LOT_MAX         = 100.0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  DATA FETCH
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_data():
    print("📥 Fetching XAU/USD 5-min data …")
    import yfinance as yf
    ticker = yf.Ticker("GC=F")
    df = ticker.history(period="60d", interval="5m")
    if df.empty or len(df) < 500:
        df = ticker.history(period="60d", interval="15m")
    if df.empty:
        sys.exit("❌ No data.")
    df = df.reset_index()
    ren = {"Datetime": "datetime", "Open": "open", "High": "high",
           "Low": "low", "Close": "close", "Volume": "volume"}
    df.rename(columns=ren, inplace=True)
    if df["datetime"].dt.tz is not None:
        df["datetime"] = df["datetime"].dt.tz_localize(None)
    df["date"] = df["datetime"].dt.date
    print(f"   {len(df)} candles  |  {df['close'].min():.0f} – {df['close'].max():.0f}")
    return df

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  INDICATORS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def calc_indicators(df):
    df["ema9"]  = df["close"].ewm(span=9,  adjust=False).mean()
    df["ema21"] = df["close"].ewm(span=21, adjust=False).mean()

    delta = df["close"].diff()
    gain  = delta.where(delta > 0, 0.0)
    loss  = -delta.where(delta < 0, 0.0)
    ag = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    al = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
    df["rsi"] = 100 - 100 / (1 + ag / al)

    df["ema9_above"]   = (df["ema9"] > df["ema21"]).astype(float).fillna(0)
    df["cross_up"]     = (df["ema9_above"] == 1) & (df["ema9_above"].shift(1) == 0)
    df["cross_down"]   = (df["ema9_above"] == 0) & (df["ema9_above"].shift(1) == 1)
    return df

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  BACKTEST ENGINE
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def run_backtest(df, mode="conservative", label=""):
    capital       = INITIAL_CAPITAL
    trades        = []
    daily_pnl     = {}
    consec_losses = 0
    trades_today  = 0
    current_date  = None
    in_position   = False
    exit_idx      = 0

    total_spread = 0.0; total_slippage = 0.0; total_commission = 0.0
    skipped_overlap = 0

    sl_pips = FIXED_SL_DOLLARS / PIP   # 1000 pips for $10 SL
    tp_pips = FIXED_TP_DOLLARS / PIP   # 1000 pips for $10 TP

    for i in range(22, len(df)):
        row  = df.iloc[i]
        prev = df.iloc[i - 1]

        if row["date"] != current_date:
            current_date  = row["date"]
            daily_pnl[current_date] = 0.0
            trades_today  = 0
            consec_losses = 0

        if in_position and i < exit_idx:
            skipped_overlap += 1
            continue
        if in_position and i >= exit_idx:
            in_position = False

        if daily_pnl.get(current_date, 0) <= -(capital * MAX_DAILY_LOSS_PCT):
            continue
        if trades_today >= MAX_TRADES_PER_SESSION:
            continue
        if consec_losses >= CONSECUTIVE_LOSS_LIMIT:
            continue
        if capital <= 0:
            break

        # ── signal ──────────────────────────────────────────
        signal = None
        if prev["cross_up"]   and pd.notna(prev["rsi"]) and 30 <= prev["rsi"] <= 70:
            signal = "BUY"
        elif prev["cross_down"] and pd.notna(prev["rsi"]) and 30 <= prev["rsi"] <= 70:
            signal = "SELL"
        if signal is None:
            continue

        entry_price = row["open"]

        # ── spread + slippage on entry ──────────────────────
        if signal == "BUY":
            entry_price += SPREAD_DOLLARS / 2 + SLIPPAGE_DOLLARS
        else:
            entry_price -= SPREAD_DOLLARS / 2 + SLIPPAGE_DOLLARS

        total_spread   += SPREAD_DOLLARS / 2
        total_slippage += SLIPPAGE_DOLLARS

        # ── SL/TP (fixed) ──────────────────────────────────
        if signal == "BUY":
            sl_price = entry_price - FIXED_SL_DOLLARS
            tp_price = entry_price + FIXED_TP_DOLLARS
        else:
            sl_price = entry_price + FIXED_SL_DOLLARS
            tp_price = entry_price - FIXED_TP_DOLLARS

        lot = FIXED_LOT

        # ── simulate forward ────────────────────────────────
        exit_price = None
        exit_type  = None
        found_idx  = None

        for j in range(i + 1, min(i + 500, len(df))):
            hi = df.iloc[j]["high"]
            lo = df.iloc[j]["low"]

            sl_hit = (lo <= sl_price) if signal == "BUY" else (hi >= sl_price)
            tp_hit = (hi >= tp_price) if signal == "BUY" else (lo <= tp_price)

            if sl_hit and tp_hit:
                if mode == "conservative":
                    exit_price, exit_type, found_idx = sl_price, "SL", j
                else:
                    exit_price, exit_type, found_idx = tp_price, "TP", j
                break
            elif sl_hit:
                exit_price, exit_type, found_idx = sl_price, "SL", j
                break
            elif tp_hit:
                exit_price, exit_type, found_idx = tp_price, "TP", j
                break

            rsi_val = df.iloc[j]["rsi"]
            if pd.notna(rsi_val):
                if signal == "BUY" and rsi_val >= 70:
                    exit_price, exit_type, found_idx = df.iloc[j]["close"], "RSI_EXIT", j
                    break
                if signal == "SELL" and rsi_val <= 30:
                    exit_price, exit_type, found_idx = df.iloc[j]["close"], "RSI_EXIT", j
                    break

        if exit_price is None:
            found_idx  = min(i + 499, len(df) - 1)
            exit_price = df.iloc[found_idx]["close"]
            exit_type  = "TIME_EXIT"

        # ── exit spread + slippage ──────────────────────────
        if signal == "BUY":
            exit_price -= SLIPPAGE_DOLLARS
        else:
            exit_price += SLIPPAGE_DOLLARS
        total_slippage   += SLIPPAGE_DOLLARS
        total_commission += COMMISSION_PER_TRADE

        # ── P&L ─────────────────────────────────────────────
        if signal == "BUY":
            pnl = (exit_price - entry_price) / PIP * PIP_VALUE_PER_LOT * lot
        else:
            pnl = (entry_price - exit_price) / PIP * PIP_VALUE_PER_LOT * lot

        pnl -= COMMISSION_PER_TRADE
        capital      += pnl
        daily_pnl[current_date] = daily_pnl.get(current_date, 0) + pnl
        trades_today += 1
        consec_losses = consec_losses + 1 if pnl <= 0 else 0

        trades.append({
            "entry_time":  row["datetime"],
            "exit_time":   df.iloc[found_idx]["datetime"],
            "signal":      signal,
            "lot":         lot,
            "entry":       round(entry_price, 2),
            "exit":        round(exit_price, 2),
            "sl":          round(sl_price, 2),
            "tp":          round(tp_price, 2),
            "pnl":         round(pnl, 2),
            "exit_type":   exit_type,
            "capital":     round(capital, 2),
            "date":        str(current_date),
        })

        in_position = True
        exit_idx    = found_idx + 1

    cost_summary = {
        "spread":    round(total_spread, 2),
        "slippage":  round(total_slippage, 2),
        "commission": round(total_commission, 2),
        "total":     round(total_spread + total_slippage + total_commission, 2),
    }
    return trades, capital, cost_summary, skipped_overlap

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  REPORTING
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def report(trades, final_cap, costs, skipped_overlap, label=""):
    hdr = f"{'='*55}\n  {label}\n{'='*55}"
    print(hdr)

    if not trades:
        print("  No trades.\n")
        return

    tdf = pd.DataFrame(trades)
    wins   = tdf[tdf["pnl"] > 0]
    losses = tdf[tdf["pnl"] <= 0]

    n        = len(tdf)
    wr       = len(wins) / n * 100
    total_pnl = tdf["pnl"].sum()
    avg_w    = wins["pnl"].mean()   if len(wins)   else 0
    avg_l    = losses["pnl"].mean() if len(losses) else 0
    pf       = abs(wins["pnl"].sum() / losses["pnl"].sum()) if len(losses) and losses["pnl"].sum() else float("inf")

    rm = tdf["capital"].cummax()
    dd = (tdf["capital"] - rm) / rm * 100
    max_dd = dd.min()

    mcl = 0; cl = 0
    for t in trades:
        if t["pnl"] <= 0:
            cl += 1; mcl = max(mcl, cl)
        else:
            cl = 0

    days = tdf["date"].nunique()

    print(f"""
  Config:   0.01 lot  |  SL $10  |  TP $10  |  R:R 1:1
  ─────────────────────────────────────────
  Starting Capital   ${INITIAL_CAPITAL:>10,.2f}
  Final Capital      ${final_cap:>10,.2f}
  Net P&L            ${total_pnl:>10,.2f}
  Return             {total_pnl / INITIAL_CAPITAL * 100:>9.1f} %
  ─────────────────────────────────────────
  Trades             {n:>10}
  Win Rate           {wr:>9.1f} %
  Profit Factor      {pf:>10.2f}
  Avg Win            ${avg_w:>10,.2f}
  Avg Loss           ${avg_l:>10,.2f}
  Max Drawdown       {max_dd:>9.1f} %
  Max Consec Losses  {mcl:>10}
  Trading Days       {days:>10}
  ─────────────────────────────────────────
  TRADE COSTS
    Spread           ${costs['spread']:>10,.2f}
    Slippage         ${costs['slippage']:>10,.2f}
    Commission       ${costs['commission']:>10,.2f}
    TOTAL COST       ${costs['total']:>10,.2f}
  ─────────────────────────────────────────
  Signals skipped (overlap)  {skipped_overlap:>6}
""")

    print("  Exit Types:")
    for et, g in tdf.groupby("exit_type"):
        print(f"    {et:15}  {len(g):>4} trades   avg ${g['pnl'].mean():>7.2f}")

    print(f"\n  Last 10 trades:")
    cols = ["entry_time", "signal", "lot", "entry", "exit", "pnl", "exit_type"]
    print(tdf[cols].tail(10).to_string(index=False))

    print(f"\n  Daily Summary:")
    ds = tdf.groupby("date").agg(
        trades=("pnl", "count"),
        pnl=("pnl", "sum"),
        wins=("pnl", lambda x: (x > 0).sum()),
    )
    ds["wr"] = (ds["wins"] / ds["trades"] * 100).round(0).astype(int)
    ds["pnl"] = ds["pnl"].round(2)
    print(ds[["trades", "wins", "wr", "pnl"]].to_string())

    return tdf

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MAIN
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    df = fetch_data()
    df = calc_indicators(df)

    split = int(len(df) * 0.70)
    train = df.iloc[:split].copy().reset_index(drop=True)
    test  = df.iloc[split:].copy().reset_index(drop=True)

    print(f"\n📊 Dataset split  |  Train: {len(train)} candles  |  Test: {len(test)} candles")
    print(f"   Train: {train['datetime'].iloc[0]} → {train['datetime'].iloc[-1]}")
    print(f"   Test:  {test['datetime'].iloc[0]} → {test['datetime'].iloc[-1]}\n")

    all_tdfs = []

    for mode in ["conservative", "optimistic"]:
        print(f"\n{'━'*55}")
        print(f"  MODE: {mode.upper()}")
        print(f"{'━'*55}\n")

        print(f"── TRAINING SET ──")
        tr, cap_tr, cost_tr, sk_tr = run_backtest(train, mode=mode)
        tdf_tr = report(tr, cap_tr, cost_tr, sk_tr, label=f"TRAIN {mode.upper()}")
        if tdf_tr is not None: all_tdfs.append(tdf_tr)

        print(f"── TESTING SET (out-of-sample) ──")
        te, cap_te, cost_te, sk_te = run_backtest(test, mode=mode)
        tdf_te = report(te, cap_te, cost_te, sk_te, label=f"TEST  {mode.upper()}")
        if tdf_te is not None: all_tdfs.append(tdf_te)

    # ── sensitivity ─────────────────────────────────────────
    cons_tr, _, _, _ = run_backtest(train, mode="conservative")
    opt_tr,  _, _, _ = run_backtest(train, mode="optimistic")
    cons_te, _, _, _ = run_backtest(test,  mode="conservative")
    opt_te,  _, _, _ = run_backtest(test,  mode="optimistic")

    def net(t): return sum(x["pnl"] for x in t) if t else 0

    print(f"{'='*55}")
    print(f"  SENSITIVITY CHECK")
    print(f"{'='*55}")
    print(f"  Train gap (conservative vs optimistic): ${abs(net(cons_tr)-net(opt_tr)):.2f}")
    print(f"  Test  gap (conservative vs optimistic): ${abs(net(cons_te)-net(opt_te)):.2f}")
    if abs(net(cons_tr)-net(opt_tr)) > 5 or abs(net(cons_te)-net(opt_te)) > 5:
        print(f"  ⚠️  Strategy sensitive to intrabar execution.")
    else:
        print(f"  ✅ Low sensitivity — execution assumption has minor impact.")
    print()

    # ── save combined ───────────────────────────────────────
    if all_tdfs:
        combined = pd.concat(all_tdfs, ignore_index=True)
        combined.to_csv("/home/user/.reson/agents/ec81560c-e712-4689-a7a8-d72921b031ff/backtest_v3_results.csv", index=False)
        print(f"  💾 Saved → backtest_v3_results.csv\n")
