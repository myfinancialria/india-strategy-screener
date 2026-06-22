"""
Style 4 — Supertrend + 20 EMA Momentum  (intraday/swing, retail MCX & high-beta equity)

Trend filter (20 EMA) + volatility breakout (Supertrend 10,3). On the daily chart
this is a swing signal:

  BUY  : close > 20 EMA  AND  Supertrend just flipped GREEN (dir −1 → +1).
  Also reports stocks already in a fresh green run (flip within last 3 bars) that
  remain above the 20 EMA, so you catch the move slightly late but still in trend.

Stop  : the Supertrend line (or 1.5× recent swing low, whichever is tighter).
Target: 1:2 risk-reward (t1) and 1:3 (t2), then trail the Supertrend line.
"""
import numpy as np
import pandas as pd
from . import lib

STYLE = "supertrend_ema"
MIN_HISTORY = 60
PERIOD, MULT, EMA_LEN = 10, 3.0, 20
FRESH_BARS = 3            # how recently the Supertrend must have flipped green


def scan(prices, fund, universe=None, side="long"):
    have = set(prices["symbol"].unique())
    syms = [s for s in (universe or sorted(have)) if s in have]
    rows = []
    for sym in syms:
        g = prices[prices.symbol == sym]
        if len(g) < MIN_HISTORY or g["series"].iloc[-1] != "EQ":
            continue
        c = g["close"].values; h = g["high"].values; l = g["low"].values
        to = g["turnover_lacs"].values
        px = c[-1]
        if px < 30 or np.mean(to[-20:]) < 200:
            continue
        e20 = lib.ema(c, EMA_LEN)
        st_line, st_dir = lib.supertrend(h, l, c, PERIOD, MULT)
        above_ema = px > e20[-1]
        # bars since the latest green flip
        flip_age = None
        for k in range(1, min(FRESH_BARS + 1, len(st_dir))):
            if st_dir[-k] == 1 and st_dir[-k - 1] == -1:
                flip_age = k - 1
                break
        green_now = st_dir[-1] == 1
        if not (above_ema and green_now and flip_age is not None):
            continue
        rows.append(dict(sym=sym, px=px, blend=lib.rs_blend(c),
                         st=st_line[-1], ema=e20[-1], flip_age=flip_age,
                         swlow=l[-5:].min(),
                         ema_rising=e20[-1] > e20[-6]))
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["rs"] = lib.rs_rating(df["blend"])

    out = []
    for _, r in df.iterrows():
        entry = round(r["px"], 2)
        sl = round(min(r["st"], r["swlow"]) * 0.998, 2)
        risk = entry - sl
        if risk <= 0 or 100 * risk / entry > 12:
            continue
        t1, t2 = entry + 2 * risk, entry + 3 * risk
        rs = int(r["rs"]) if not pd.isna(r["rs"]) else 0
        score = rs + (10 if r["ema_rising"] else 0) + (8 - r["flip_age"] * 2)
        sec = lib.sector_of(r["sym"], fund)
        freshness = "today" if r["flip_age"] == 0 else f"{int(r['flip_age'])}d ago"
        reason = (f"Supertrend({PERIOD},{MULT:g}) green ({freshness}) + close > 20-EMA"
                  f"{' (rising)' if r['ema_rising'] else ''}; RS {rs}")
        out.append(lib.signal(
            r["sym"], STYLE, "BUY", entry, sl, t1, t2, r["px"], score, sec, reason,
            horizon="days-weeks",
            metrics={"rs": rs, "supertrend": round(float(r["st"]), 2),
                     "ema20": round(float(r["ema"]), 2), "flip_age_bars": int(r["flip_age"]),
                     "ema_rising": bool(r["ema_rising"])},
        ))
    out.sort(key=lambda s: s["score"], reverse=True)
    return out
