"""
Style 1 — Coffee Can Portfolio  (long-term quality/value, Saurabh Mukherjea)

Buy ultra-high-quality, moated businesses and hold for years. Mechanical filters,
applied over *every available fiscal year* (yfinance gives ~5y free; the classic
rule is 10y — we check what we have and report the count):

  Non-financials:  ROCE > 15% every year  AND  revenue growth > 10% every year
  Financials:      ROE  > 15% every year  (banks/NBFCs)
  Plus: positive net income, sane leverage (debt/equity < 1 for non-financials).

This is buy-and-forget: there is no price stop. The journal marks these to market
and only the *fundamental* break (a year failing the filter) is an exit reason —
so t1/t2 here are informational compounding milestones (+15% / +50%), not targets
the journal will auto-close on.
"""
import numpy as np
from . import lib

STYLE = "coffee_can"
MIN_YEARS = 3          # need at least this many fiscal years to judge consistency
ROCE_MIN, ROE_MIN, REVG_MIN, DE_MAX = 15.0, 15.0, 10.0, 1.0


def _clean(xs):
    return [x for x in (xs or []) if x is not None]


def scan(last_px, fund, fin, min_years=MIN_YEARS):
    out = []
    for sym, f in fin.items():
        px = last_px.get(sym)
        if not px:
            continue
        snap = fund.get(sym, {})
        is_fin = f.get("fin_firm")
        roce = _clean(f.get("roce"))[:5]
        roe  = _clean(f.get("roe"))[:5]
        revg = _clean(f.get("rev_growth"))[:5]
        ni   = _clean(f.get("net_income"))[:5]

        if is_fin:
            if len(roe) < min_years:
                continue
            quality_pass = all(x > ROE_MIN for x in roe)
            growth_pass = True            # financials judged on ROE, not revenue
            key_metric, key_series = "ROE", roe
        else:
            if len(roce) < min_years or len(revg) < min_years:
                continue
            quality_pass = all(x > ROCE_MIN for x in roce)
            growth_pass = all(x > REVG_MIN for x in revg)
            key_metric, key_series = "ROCE", roce

        profitable = all(x > 0 for x in ni) if ni else True
        de = snap.get("de")
        de_ratio = (de / 100.0) if (de is not None and de > 5) else de   # normalise % vs ratio
        leverage_ok = is_fin or de_ratio is None or de_ratio < DE_MAX

        if not (quality_pass and growth_pass and profitable and leverage_ok):
            continue

        avg_q = round(float(np.mean(key_series)), 1)
        avg_g = round(float(np.mean(revg)), 1) if revg else None
        yrs = len(key_series)
        # score: quality margin over threshold + growth margin + consistency (years)
        score = (avg_q - (ROE_MIN if is_fin else ROCE_MIN)) + (avg_g or 0) * 0.5 + yrs * 2

        entry = px
        sl = round(entry * 0.50, 2)          # nominal review floor; journal won't auto-stop coffee_can
        t1, t2 = entry * 1.15, entry * 1.50  # compounding milestones, informational
        sec = lib.sector_of(sym, fund)
        gtxt = f", rev g {avg_g}%/yr" if avg_g is not None else ""
        reason = (f"{yrs}y consistent {key_metric} {avg_q}% (>{ROE_MIN if is_fin else ROCE_MIN}%)"
                  f"{gtxt}; {'financial' if is_fin else 'low-debt'} quality compounder")
        out.append(lib.signal(
            sym, STYLE, "BUY", entry, sl, t1, t2, px, score, sec, reason,
            horizon="10y hold",
            metrics={"avg_roce_roe": avg_q, "avg_rev_growth": avg_g, "years": yrs,
                     "fin_firm": bool(is_fin), "pe": snap.get("pe"), "de": de_ratio,
                     "roce_series": roce, "roe_series": roe, "revg_series": revg},
            qty=int(lib.CAP * 0.10 / entry),     # ~10% allocation per quality name
        ))
    out.sort(key=lambda s: s["score"], reverse=True)
    return out
