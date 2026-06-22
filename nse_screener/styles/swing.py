"""
Style 5 — Stock Swing Setups  (days–weeks, 100% technical)

Three rule-based daily-chart setups from the playbook, scanned across Nifty-500:

  A  20-EMA Pullback     uptrend (20>50>200 EMA), price pulls back to the rising
                         20-EMA and prints a bullish reversal bar.
  B  Bollinger Squeeze   band-width at a 20-session low, then a close above the
                         upper band on ≥1.5× volume (volatility expansion).
  C  RS Consolidation    Nifty soft / sideways while the stock holds above its
                         20-EMA with rising relative strength, then breaks its
                         recent range on volume (RS leader).

Entry = trigger level + a tick. Stop per setup. Targets 1:2 (t1) and 1:3 (t2).
"""
import numpy as np
import pandas as pd
from . import lib

STYLE = "swing"
MIN_HISTORY = 210


def _nifty_soft(prices):
    g = prices[prices.symbol == "NIFTYBEES"]
    if len(g) < 12:
        return False
    c = g["close"].values
    return c[-1] < c[-6] or c[-1] < lib.sma(c, 20)[-1]      # pulling back / below 20-DMA


def scan(prices, fund, universe=None):
    soft = _nifty_soft(prices)
    have = set(prices["symbol"].unique())
    syms = [s for s in (universe or sorted(have)) if s in have]
    rows = []
    for sym in syms:
        g = prices[prices.symbol == sym]
        if len(g) < MIN_HISTORY or g["series"].iloc[-1] != "EQ":
            continue
        c = g["close"].values; h = g["high"].values; l = g["low"].values
        o = g["open"].values; v = g["volume"].values; to = g["turnover_lacs"].values
        px = c[-1]
        if px < 30 or np.mean(to[-20:]) < 200:
            continue
        e20, e50, e200 = lib.ema(c, 20)[-1], lib.ema(c, 50)[-1], lib.ema(c, 200)[-1]
        e20_prev = lib.ema(c, 20)[-6]
        sma20 = lib.sma(c, 20)
        std20 = pd.Series(c).rolling(20).std().values
        upper, lower, mid = sma20 + 2 * std20, sma20 - 2 * std20, sma20
        bw = (upper - lower) / mid
        vol20 = v[-20:].mean()
        vsurge = v[-1] / vol20 if vol20 > 0 else 0
        rows.append(dict(
            sym=sym, px=px, blend=lib.rs_blend(c),
            e20=e20, e50=e50, e200=e200, e20_rising=e20 > e20_prev,
            up=l[-1], dn=l[-1],
            trig_hi=h[-1], trig_lo=l[-1], green=c[-1] > o[-1] and (c[-1] - l[-1]) > 0.5 * (h[-1] - l[-1]),
            near20=abs(px / e20 - 1) <= 0.03 or (l[-1] <= e20 * 1.01),
            bw_now=bw[-1], bw_min=np.nanmin(bw[-20:]),
            up_band=upper[-1], mid_band=mid[-1], vsurge=vsurge,
            range_hi=h[-11:-1].max(), range_lo=l[-11:-1].min(),
            tight=(h[-11:-1].max() / l[-11:-1].min() - 1) <= 0.10,
            atr=lib.atr(h, l, c, 14)[-1],
        ))
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["rs"] = lib.rs_rating(df["blend"])

    out = []
    for _, r in df.iterrows():
        rs = int(r["rs"]) if not pd.isna(r["rs"]) else 0
        setup, entry, sl, reason = None, None, None, None
        uptrend = r["e20"] > r["e50"] > r["e200"]

        # A — 20-EMA pullback
        if uptrend and r["e20_rising"] and r["near20"] and r["green"]:
            setup = "20EMA-pullback"
            entry = round(r["trig_hi"] * 1.002, 2)
            sl = round(min(r["trig_lo"], r["e50"]) * 0.997, 2)
            reason = f"Uptrend (20>50>200 EMA); pullback to rising 20-EMA + bullish bar; RS {rs}"
        # B — Bollinger squeeze breakout
        elif r["bw_now"] <= r["bw_min"] * 1.02 and r["px"] > r["up_band"] and r["vsurge"] >= 1.5:
            setup = "BB-squeeze"
            entry = round(r["px"] * 1.002, 2)
            sl = round(r["mid_band"], 2)
            reason = f"Bollinger squeeze (20-session low BW) breakout > upper band on {r['vsurge']:.1f}× vol; RS {rs}"
        # C — RS consolidation leader (when Nifty is soft)
        elif soft and rs >= 80 and r["tight"] and r["px"] > r["e50"] and r["px"] >= r["range_hi"] * 0.995 and r["vsurge"] >= 1.3:
            setup = "RS-consolidation"
            entry = round(max(r["range_hi"], r["px"]) * 1.002, 2)
            sl = round(r["range_lo"] * 0.99, 2)
            reason = f"RS leader {rs} holding range while Nifty soft; breakout on {r['vsurge']:.1f}× vol"

        if not setup or entry is None or sl is None or entry - sl <= 0:
            continue
        if 100 * (entry - sl) / entry > 12:
            continue
        risk = entry - sl
        t1, t2 = entry + 2 * risk, entry + 3 * risk
        score = rs + (15 if setup == "BB-squeeze" else 10 if setup == "20EMA-pullback" else 12)
        sec = lib.sector_of(r["sym"], fund)
        out.append(lib.signal(
            r["sym"], STYLE, "BUY", entry, sl, t1, t2, r["px"], score, sec, reason,
            horizon="days-weeks",
            metrics={"setup": setup, "rs": rs, "ema20": round(float(r["e20"]), 2),
                     "ema50": round(float(r["e50"]), 2), "vol_surge": round(float(r["vsurge"]), 2)},
        ))
    out.sort(key=lambda s: s["score"], reverse=True)
    return out
