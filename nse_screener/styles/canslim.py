"""
Style 2 — CANSLIM  (medium-term growth/momentum, William O'Neil)

Techno-fundamental. We score every CANSLIM letter we can measure:
  C/A  Current & annual earnings growth   — yfinance net-income growth (>20% / >25%)
  N    New high                            — within 5% of 52-week high / fresh breakout
  S    Supply/Demand                       — breakout candle volume ≥ 1.4× 50-day avg
  L    Leader                              — RS rating ≥ 80 (vs the screened universe)
  M    Market direction                    — NIFTYBEES above its 50-DMA (uptrend gate)

Entry  : breakout pivot (prior 20-day high) + a tick.
Stop   : O'Neil's hard 7-8% rule below entry.
Targets: +20% (book) and +25% trail, per the playbook.
"""
import numpy as np
import pandas as pd
from . import lib

STYLE = "canslim"
MIN_HISTORY = 200
EPSG_C, EPSG_A = 20.0, 25.0       # quarterly-proxy and annual earnings growth thresholds
RS_MIN, VOL_SURGE = 80, 1.4


def _market_uptrend(prices):
    g = prices[prices.symbol == "NIFTYBEES"]
    if len(g) < 50:
        return True
    c = g["close"].values
    return c[-1] > lib.sma(c, 50)[-1]


def scan(prices, fund, fin, rs_min=RS_MIN):
    mkt_up = _market_uptrend(prices)
    rows = []
    for sym, g in prices.groupby("symbol", sort=False):
        if len(g) < MIN_HISTORY or g["series"].iloc[-1] != "EQ":
            continue
        c = g["close"].values; h = g["high"].values; v = g["volume"].values
        to = g["turnover_lacs"].values
        px = c[-1]
        if px < 30 or np.mean(to[-20:]) < 300:        # liquidity + price floor
            continue
        hi52 = c[-252:].max() if len(c) >= 252 else c.max()
        pivot = h[-21:-1].max()                       # prior 20-day high
        vol50 = v[-50:].mean()
        vol_surge = v[-1] / vol50 if vol50 > 0 else 0
        rows.append(dict(
            sym=sym, px=px, blend=lib.rs_blend(c),
            pct_from_hi=100 * (px / hi52 - 1), pivot=pivot,
            breakout=px > pivot, vol_surge=vol_surge,
            atr=lib.atr(h, g["low"].values, c, 14)[-1],
        ))
    if not rows:
        return []
    df = pd.DataFrame(rows)
    df["rs"] = lib.rs_rating(df["blend"])

    out = []
    for _, r in df.iterrows():
        if pd.isna(r["rs"]) or int(r["rs"]) < rs_min:
            continue
        f = fin.get(r["sym"], {})
        epsg = f.get("eps_ttm_g")                     # annual net-income growth (A)
        ni_g = [x for x in (f.get("ni_growth") or []) if x is not None]
        # C/A earnings gate — require measurable, accelerating growth
        earnings_ok = epsg is not None and epsg >= EPSG_A
        # N — near highs or breaking out;  S — volume confirmation on breakout
        near_high = r["pct_from_hi"] >= -8
        breaking = bool(r["breakout"]) and r["vol_surge"] >= VOL_SURGE
        if not (earnings_ok and near_high):
            continue

        score = (int(r["rs"]) - rs_min) + min(epsg, 100) * 0.5 \
            + (15 if breaking else 0) + (10 if mkt_up else -10)
        entry = round(max(r["pivot"], r["px"]) * 1.002, 2)
        sl = round(entry * 0.925, 2)                  # 7.5% hard stop
        t1, t2 = entry * 1.20, entry * 1.25           # book at +20%, trail toward +25%
        sec = lib.sector_of(r["sym"], fund)
        flags = []
        if breaking: flags.append("breakout+vol")
        if r["pct_from_hi"] >= -2: flags.append("at 52w-high")
        if not mkt_up: flags.append("MKT WEAK")
        reason = (f"RS {int(r['rs'])}; earnings g {epsg}% (A>{EPSG_A:.0f}%); "
                  f"{'; '.join(flags) if flags else 'basing near highs'}")
        out.append(lib.signal(
            r["sym"], STYLE, "BUY", entry, sl, t1, t2, r["px"], score, sec, reason,
            horizon="weeks-months",
            metrics={"rs": int(r["rs"]), "eps_growth": epsg, "ni_growth": ni_g[:4],
                     "pct_from_52w_high": round(r["pct_from_hi"], 1),
                     "vol_surge": round(r["vol_surge"], 2), "breaking_out": breaking,
                     "market_uptrend": mkt_up},
        ))
    out.sort(key=lambda s: s["score"], reverse=True)
    return out
