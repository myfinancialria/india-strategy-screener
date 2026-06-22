"""
Shared helpers for the four strategy-style scanners.

Price source : data/prices.parquet  (corp-action adjusted daily OHLCV, ~2y, 2900 syms)
               built by consolidate.py and refreshed live by fyers_data.py.
Fundamentals : data/fundamentals.json (yfinance snapshot)  +  financials.py (multi-year).

Everything operates on plain numpy arrays per symbol so the scanners stay O(n)
and have no dependency on openalgo / TA-Lib. One common signal schema (`signal`)
keeps the journal and dashboard style-agnostic.
"""
import os, csv, json, datetime as dt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
DATA = os.path.join(ROOT, "data")
PRICES = os.path.join(DATA, "prices.parquet")
INDICES = os.path.join(DATA, "indices")
FUND_CACHE = os.path.join(DATA, "fundamentals.json")

CAP, RISK_PCT = 1_000_000, 0.01     # ₹10L book, 1% risk per trade — same as journal.py


# ---------------------------------------------------------------- data loading
def load_prices(columns=None):
    cols = columns or ["symbol", "series", "date", "open", "high", "low",
                       "close", "volume", "turnover_lacs"]
    p = pd.read_parquet(PRICES, engine="fastparquet", columns=cols)
    return p.sort_values(["symbol", "date"])


def universe(name):
    """Symbols from an index CSV in data/indices (e.g. 'nifty500', 'nifty100', 'nifty50')."""
    path = os.path.join(INDICES, f"ind_{name}list.csv")
    if not os.path.exists(path):
        return []
    out = []
    for r in csv.DictReader(open(path)):
        s = (r.get("Symbol") or r.get("symbol") or "").strip().upper()
        if s:
            out.append(s)
    return out


def load_fundamentals():
    return json.load(open(FUND_CACHE)) if os.path.exists(FUND_CACHE) else {}


def sector_of(sym, fund):
    return (fund.get(sym) or {}).get("sector") or "?"


# ---------------------------------------------------------------- indicators
def ema(arr, span):
    return pd.Series(arr).ewm(span=span, adjust=False).mean().values


def sma(arr, n):
    return pd.Series(arr).rolling(n).mean().values


def atr(high, low, close, n=14):
    """Wilder ATR as a full array (NaN until enough bars)."""
    h, l, c = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    pc = np.roll(c, 1); pc[0] = c[0]
    tr = np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))
    return pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean().values


def supertrend(high, low, close, period=10, mult=3.0):
    """Classic Supertrend. Returns (line, dir) where dir=+1 uptrend (green), -1 down (red)."""
    h, l, c = np.asarray(high, float), np.asarray(low, float), np.asarray(close, float)
    a = atr(high, low, close, period)
    hl2 = (h + l) / 2.0
    upper = hl2 + mult * a
    lower = hl2 - mult * a
    n = len(c)
    fu, fl = upper.copy(), lower.copy()
    for i in range(1, n):
        fl[i] = max(lower[i], fl[i - 1]) if c[i - 1] > fl[i - 1] else lower[i]
        fu[i] = min(upper[i], fu[i - 1]) if c[i - 1] < fu[i - 1] else upper[i]
    direction = np.ones(n, dtype=int)
    line = np.full(n, np.nan)
    for i in range(1, n):
        if c[i] > fu[i - 1]:
            direction[i] = 1
        elif c[i] < fl[i - 1]:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
        line[i] = fl[i] if direction[i] == 1 else fu[i]
    return line, direction


def donchian(high, low, n):
    """Upper (rolling n-day high) and lower (rolling n-day low) channels, EXCLUDING today."""
    hi = pd.Series(high).shift(1).rolling(n).max().values
    lo = pd.Series(low).shift(1).rolling(n).min().values
    return hi, lo


def rs_blend(close):
    """O'Neil-style weighted momentum: 40% 3m + 20% 6m + 40% 12m (falls back gracefully)."""
    c = np.asarray(close, float)
    def r(n):
        return c[-1] / c[-(n + 1)] - 1 if len(c) > n else np.nan
    r63, r126, r252 = r(63), r(126), r(252)
    parts, wts = [], []
    for v, w in [(r63, 0.4), (r126, 0.2), (r252, 0.4)]:
        if v == v:               # not NaN
            parts.append(v * w); wts.append(w)
    return sum(parts) / sum(wts) if wts else np.nan


def rs_rating(blend_series):
    """IBD-style 1-99 percentile rank of a pandas Series of rs_blend values."""
    return (blend_series.rank(pct=True) * 98 + 1).round().astype("Int64")


# ---------------------------------------------------------------- signal schema
def signal(symbol, style, side, entry, sl, t1, t2, last, score, sector, reason,
           horizon, metrics=None, qty=None):
    """One common record every scanner returns and the journal consumes."""
    risk = abs(entry - sl)
    if qty is None:
        qty = int(CAP * RISK_PCT / risk) if risk > 0 else 0
    return {
        "symbol": symbol, "style": style, "side": side,
        "entry": round(float(entry), 2), "sl": round(float(sl), 2),
        "t1": round(float(t1), 2), "t2": round(float(t2), 2),
        "last": round(float(last), 2), "score": round(float(score), 1),
        "sector": sector, "reason": reason, "horizon": horizon,
        "qty": int(qty), "rr": round(abs(t1 - entry) / risk, 2) if risk > 0 else None,
        "metrics": metrics or {},
        "created": dt.date.today().isoformat(),
        "status": "WATCHING", "entry_date": None,
        "exit": None, "exit_date": None, "pnl": 0.0, "note": "",
    }
