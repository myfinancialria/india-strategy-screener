"""
Style 6 — Central Pivot Range (CPR)  (intraday index/levels, 100% technical)

From the previous session's High/Low/Close we project the next session's CPR and
pivots:

  P  = (H + L + C)/3        BC = (H + L)/2        TC = P + (P − BC)
  R1 = 2P − L   R2 = P + (H − L)   R3 = H + 2(P − L)
  S1 = 2P − H   S2 = P − (H − L)   S3 = L − 2(H − P)

CPR width = |TC − BC| / P:
  narrow → low prior-day volatility → expect a TRENDING day (trade breakouts)
  wide   → high prior-day volatility → expect a RANGE day (fade the edges)

Default universe: index proxies NIFTYBEES (Nifty), BANKBEES (Bank Nifty),
JUNIORBEES (Nifty Next-50). A breakout-long plan is emitted (entry above the
TC/PDH trigger, stop at BC, targets R1/R2). NOTE: this is an EOD-bar approximation
of an intraday strategy — the levels are exact, the journal fill/exit is a daily
proxy (intraday entries can't be reconstructed from daily candles).
"""
import numpy as np
from . import lib

STYLE = "cpr"
INDEX_PROXIES = {"NIFTYBEES": "Nifty 50", "BANKBEES": "Bank Nifty", "JUNIORBEES": "Nifty Next-50"}
NARROW, WIDE = 0.0015, 0.0040          # CPR-width thresholds (fraction of pivot)


def _levels(h, l, c):
    p = (h + l + c) / 3.0
    bc = (h + l) / 2.0
    tc = p + (p - bc)
    tc, bc = max(tc, bc), min(tc, bc)        # TC is the upper line by convention
    return {
        "P": p, "BC": bc, "TC": tc,
        "R1": 2 * p - l, "R2": p + (h - l), "R3": h + 2 * (p - l),
        "S1": 2 * p - h, "S2": p - (h - l), "S3": l - 2 * (h - p),
        "PDH": h, "PDL": l, "PDC": c,
    }


def scan(prices, fund, extra_universe=None):
    syms = list(INDEX_PROXIES) + list(extra_universe or [])
    have = set(prices["symbol"].unique())
    out = []
    for sym in syms:
        if sym not in have:
            continue
        g = prices[prices.symbol == sym].tail(60)
        if len(g) < 5:
            continue
        h, l, c = float(g["high"].iloc[-1]), float(g["low"].iloc[-1]), float(g["close"].iloc[-1])
        lv = _levels(h, l, c)
        width = abs(lv["TC"] - lv["BC"]) / lv["P"] if lv["P"] else 0
        kind = "narrow" if width <= NARROW else "wide" if width >= WIDE else "medium"
        day_type = "trending" if kind == "narrow" else "range" if kind == "wide" else "neutral"

        # breakout-long plan (the canonical narrow-CPR trending-day trade)
        trigger = max(lv["TC"], lv["PDH"])
        entry = round(trigger * 1.001, 2)
        sl = round(lv["BC"], 2)
        t1, t2 = round(lv["R1"], 2), round(lv["R2"], 2)
        if entry - sl <= 0:
            sl = round(entry * 0.99, 2)
        name = INDEX_PROXIES.get(sym, lib.sector_of(sym, fund))
        score = {"narrow": 3, "medium": 2, "wide": 1}[kind] * 10 + round(width * 1000, 1)
        reason = (f"{name}: {kind} CPR ({width*100:.2f}%) → expect {day_type} day; "
                  f"long above TC/PDH {entry}, SL BC {sl}, T R1 {t1}/R2 {t2}")
        out.append(lib.signal(
            sym, STYLE, "BUY", entry, sl, t1, t2, c, score, name, reason,
            horizon="intraday",
            metrics={"name": name, "cpr_width_pct": round(width * 100, 3),
                     "cpr_type": kind, "day_type": day_type,
                     **{k: round(v, 2) for k, v in lv.items()}},
        ))
    out.sort(key=lambda s: s["score"], reverse=True)
    return out
