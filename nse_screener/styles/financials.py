"""
Multi-year financial history via yfinance — the fundamental backbone for the
Coffee-Can (quality/value) and CANSLIM (earnings growth) styles.

yfinance free tier returns ~5 fiscal years of income statement + balance sheet.
True 10-year forensic depth (Marcellus-style) isn't available for free, so the
Coffee-Can filter is applied over *every available year* (≥3) and labelled with
how many years were checked — honest about the limitation rather than faking a
decade of data.

Per symbol we cache:
  years        list of fiscal-year-ends (newest first)
  revenue      total revenue per year (₹)
  ebit         EBIT per year (₹)
  net_income   net income per year (₹)
  equity       shareholders' equity per year (₹)
  capital_emp  capital employed = total assets − current liabilities (₹)
  roce         EBIT / capital employed, % per year
  roe          net income / equity, % per year
  rev_growth   YoY revenue growth, % per year (newest first, len = years-1)
  eps_ttm_g    most-recent annual net-income growth %  (CANSLIM 'A' proxy)
  fin_firm     True if banking/financial (use ROE not ROCE)

Cache: data/financials.json, refreshed when older than --max-age days (default 30 —
annual numbers only change at results time).
"""
import os, sys, json, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CACHE = os.path.join(ROOT, "data", "financials.json")
TODAY = dt.date.today().isoformat()


def _row(df, *names):
    """First matching row of a yfinance statement as a list (newest→oldest), or None."""
    if df is None or df.empty:
        return None
    for n in names:
        if n in df.index:
            return [None if v != v else float(v) for v in df.loc[n].values]
    return None


def _growth(series):
    """YoY % growth for a newest-first series (returns newest-first, len-1)."""
    out = []
    for i in range(len(series) - 1):
        a, b = series[i], series[i + 1]
        out.append(round((a / b - 1) * 100, 1) if (a is not None and b not in (None, 0)) else None)
    return out


def fetch_one(sym):
    import yfinance as yf
    try:
        t = yf.Ticker(sym + ".NS")
        inc, bs = t.income_stmt, t.balance_sheet
        info = t.get_info()
    except Exception:
        return sym, None
    if inc is None or inc.empty or bs is None or bs.empty:
        return sym, None

    years = [str(c.date()) for c in inc.columns]
    revenue = _row(inc, "Total Revenue", "Operating Revenue")
    ebit    = _row(inc, "EBIT", "Operating Income")
    netinc  = _row(inc, "Net Income", "Net Income Common Stockholders")
    assets  = _row(bs, "Total Assets")
    curliab = _row(bs, "Current Liabilities", "Current Liabilities Net Minority Interest")
    equity  = _row(bs, "Stockholders Equity", "Total Equity Gross Minority Interest")

    if not revenue or not equity:
        return sym, None

    n = len(years)
    def at(arr, i):
        return arr[i] if arr and i < len(arr) and arr[i] is not None else None

    cap_emp, roce, roe = [], [], []
    sector = (info or {}).get("sector") or ""
    fin_firm = sector in ("Financial Services", "Financials") or "Bank" in ((info or {}).get("industry") or "")
    for i in range(n):
        a, cl, eq, eb, ni = at(assets, i), at(curliab, i), at(equity, i), at(ebit, i), at(netinc, i)
        ce = (a - cl) if (a is not None and cl is not None) else None
        cap_emp.append(ce)
        roce.append(round(eb / ce * 100, 1) if (eb is not None and ce not in (None, 0)) else None)
        roe.append(round(ni / eq * 100, 1) if (ni is not None and eq not in (None, 0)) else None)

    rev_g = _growth(revenue)
    rec = {
        "years": years, "revenue": revenue, "ebit": ebit, "net_income": netinc,
        "equity": equity, "capital_emp": cap_emp, "roce": roce, "roe": roe,
        "rev_growth": rev_g,
        "eps_ttm_g": rev_g[0] if rev_g else None,            # placeholder; refined below
        "ni_growth": _growth(netinc),
        "fin_firm": bool(fin_firm), "sector": sector, "_ts": TODAY,
    }
    # CANSLIM 'A' = annual earnings growth — use net-income growth (latest)
    ni_g = rec["ni_growth"]
    rec["eps_ttm_g"] = ni_g[0] if ni_g else None
    return sym, rec


def get(symbols, max_age=30, force=False, workers=8, quiet=False):
    """Return {sym: record} for `symbols`, fetching/refreshing the cache as needed."""
    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    def stale(s):
        if force or s not in cache or not cache[s]:
            return True
        ts = cache[s].get("_ts")
        if not ts:
            return True
        return (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(ts)).days >= max_age

    todo = [s for s in symbols if stale(s)]
    if not quiet:
        print(f"financials: universe={len(symbols)} fresh={len(symbols)-len(todo)} to-fetch={len(todo)}")
    ok = 0
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(fetch_one, s): s for s in todo}
            for i, f in enumerate(as_completed(futs), 1):
                s, rec = f.result()
                cache[s] = rec            # store None too, so we don't re-hammer dead tickers
                if rec:
                    ok += 1
                if not quiet and (i % 50 == 0 or i == len(todo)):
                    print(f"  fetched {i}/{len(todo)} (ok {ok})")
        json.dump(cache, open(CACHE, "w"), separators=(",", ":"), sort_keys=True)
        if not quiet:
            print(f"financials: cache updated -> {CACHE} ({ok}/{len(todo)} new, {len(cache)} total)")
    return {s: cache[s] for s in symbols if cache.get(s)}


if __name__ == "__main__":
    import sys
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    name = args[0] if args else "nifty100"
    sys.path.insert(0, HERE)
    import lib
    syms = lib.universe(name)
    force = "--force" in sys.argv
    get(syms, force=force)
