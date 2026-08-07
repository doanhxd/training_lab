from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from training_lab.config import DATA_RAW_DIR


YAHOO_XAUUSD_SYMBOL = "XAUUSD=X"
YAHOO_GOLD_FUTURES_PROXY = "GC=F"


def fetch_yahoo_xauusd(period: str = "730d", interval: str = "1h", ticker: str = YAHOO_GOLD_FUTURES_PROXY) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as exc:
        raise RuntimeError("Install data dependencies with: python3 -m pip install -e '.[data]'") from exc
    frame = yf.download(
        ticker,
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if frame.empty:
        raise RuntimeError(f"Yahoo returned no rows for {ticker} period={period} interval={interval}")
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = [column[0] for column in frame.columns]
    frame = frame.reset_index()
    timestamp_column = "Datetime" if "Datetime" in frame.columns else "Date"
    normalized = pd.DataFrame(
        {
            "timestamp": pd.to_datetime(frame[timestamp_column], utc=True),
            "open": frame["Open"],
            "high": frame["High"],
            "low": frame["Low"],
            "close": frame["Close"],
            "volume": frame["Volume"] if "Volume" in frame else pd.NA,
            "spread": pd.NA,
        }
    )
    return normalized.dropna(subset=["timestamp", "open", "high", "low", "close"]).reset_index(drop=True)


def write_raw_yahoo_xauusd(frame: pd.DataFrame, period: str, interval: str, ticker: str = YAHOO_GOLD_FUTURES_PROXY) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    ticker_slug = ticker.lower().replace("=", "").replace("^", "")
    out_path = DATA_RAW_DIR / f"xauusd_yahoo_{ticker_slug}_{period}_{interval}_{stamp}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(out_path, index=False)
    return out_path
