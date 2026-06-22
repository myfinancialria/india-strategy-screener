"""
Fundamentals snapshot for the Discover app — via Yahoo Finance (yfinance).

Bhavcopy has NO fundamentals, and broker APIs (Fyers/Kite) don't carry them
either. This module fills that gap: for every screened symbol it pulls a small,
high-signal set of fundamentals and writes them keyed by symbol, exactly like
marketcap.json, so the app merges them onto each card client-side.

Fields per symbol (all optional — Yahoo's NSE coverage is good but not total):
  name      long company name
  sector    GICS-ish sector
  pe        trailing P/E
  pb        price / book
  roe       return on equity (%)
  de        debt / equity
  epsg      earnings growth YoY (%)
  revg      revenue growth YoY (%)
  pm        net profit margin (%)
  dy        dividend yield (%)
  mcap_cr   market cap (₹ crore)

Caching: results are merged into data/fundamentals.json with a per-symbol
fetch date. A symbol is only re-fetched if it's missing or older than
--max-age days (default 7) — fundamentals barely move day to day, so a weekly
refresh is plenty and keeps Yahoo's rate limits happy. Use --force to refetch all.

Output: screens_output/<date>/fundamentals.json   (and the merged data/fundamentals.json cache)
Injected into ../Discover.html between /*FUND*/ ... /*ENDFUND*/

Usage:
  python3 nse_screener/fundamentals.py [out_dir] [universe_csv] [--max-age 7] [--force] [--workers 8]
"""
import os, sys, csv, json, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
OUTDIR = ARGS[0] if len(ARGS) > 0 else "/tmp"
UNIVERSE = ARGS[1] if len(ARGS) > 1 else os.path.join(OUTDIR, "patterns_master.csv")
CACHE = os.path.join(ROOT, "data", "fundamentals.json")

def _opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
MAX_AGE = int(_opt("--max-age", 7))
WORKERS = int(_opt("--workers", 8))
FORCE   = "--force" in sys.argv
TODAY   = dt.date.today().isoformat()


def num(v, scale=1.0, nd=2):
    try:
        if v is None: return None
        f = float(v) * scale
        if f != f: return None              # NaN
        return round(f, nd)
    except (TypeError, ValueError):
        return None


def fetch_one(sym):
    """Return (sym, record) using yfinance; record is None on failure."""
    import yfinance as yf
    try:
        info = yf.Ticker(sym + ".NS").get_info()
    except Exception:
        return sym, None
    if not info or not (info.get("longName") or info.get("shortName") or info.get("marketCap")):
        return sym, None
    rec = {
        "name":    info.get("longName") or info.get("shortName"),
        "sector":  info.get("sector"),
        "industry":info.get("industry"),
        "pe":      num(info.get("trailingPE")),
        "pb":      num(info.get("priceToBook")),
        "roe":     num(info.get("returnOnEquity"), 100),
        "de":      num(info.get("debtToEquity")),
        "epsg":    num(info.get("earningsGrowth"), 100, 1),
        "revg":    num(info.get("revenueGrowth"), 100, 1),
        "pm":      num(info.get("profitMargins"), 100, 1),
        "dy":      num(info.get("dividendYield"), 1, 2),
        "mcap_cr": num(info.get("marketCap"), 1 / 1e7, 0),
        "_ts": TODAY,
    }
    # drop all-empty records (Yahoo had the ticker but no usable numbers)
    if all(v is None for k, v in rec.items() if k != "_ts" and k not in ("name", "sector")):
        if not rec["name"]:
            return sym, None
    return sym, rec


def main():
    try:
        import yfinance  # noqa: F401
    except ImportError:
        print("yfinance not installed.  Run:  pip install yfinance")
        sys.exit(1)

    universe = [r["symbol"].strip().upper() for r in csv.DictReader(open(UNIVERSE))] \
        if os.path.exists(UNIVERSE) else []
    if not universe:
        print(f"No universe found at {UNIVERSE} — run patterns.py first.")
        sys.exit(1)

    cache = json.load(open(CACHE)) if os.path.exists(CACHE) else {}

    def stale(sym):
        if FORCE or sym not in cache:
            return True
        if "industry" not in cache[sym]:     # upgrade older records that lack the sub-sector
            return True
        ts = cache[sym].get("_ts")
        if not ts:
            return True
        age = (dt.date.fromisoformat(TODAY) - dt.date.fromisoformat(ts)).days
        return age >= MAX_AGE

    todo = [s for s in universe if stale(s)]
    print(f"Universe={len(universe)}  cached-fresh={len(universe)-len(todo)}  to-fetch={len(todo)}")

    ok = 0
    if todo:
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futs = {ex.submit(fetch_one, s): s for s in todo}
            for i, f in enumerate(as_completed(futs), 1):
                sym, rec = f.result()
                if rec:
                    cache[sym] = rec; ok += 1
                if i % 50 == 0 or i == len(todo):
                    print(f"  fetched {i}/{len(todo)}  (ok so far: {ok})")
        json.dump(cache, open(CACHE, "w"), separators=(",", ":"), sort_keys=True)
        print(f"Updated cache {CACHE}: {ok}/{len(todo)} new, {len(cache)} total")

    # build the app payload: only universe symbols, strip the _ts bookkeeping
    payload = {}
    for s in universe:
        r = cache.get(s)
        if not r:
            continue
        payload[s] = {k: v for k, v in r.items() if k != "_ts" and v is not None}

    path = os.path.join(OUTDIR, "fundamentals.json")
    os.makedirs(OUTDIR, exist_ok=True)
    json.dump(payload, open(path, "w"), separators=(",", ":"), sort_keys=True)
    print(f"wrote {path}: {len(payload)}/{len(universe)} symbols have fundamentals")

    # inject straight into the app so this command alone refreshes it
    app = os.path.join(ROOT, "Discover.html")
    if os.path.exists(app) and payload:
        import re
        html = open(app).read()
        blob = json.dumps(payload, separators=(",", ":"), sort_keys=True).replace("\\", "\\\\")
        h2 = re.sub(r"/\*FUND\*/.*?/\*ENDFUND\*/", "/*FUND*/" + blob + "/*ENDFUND*/",
                    html, count=1, flags=re.S)
        if h2 != html:
            open(app, "w").write(h2)
            print("Injected fundamentals into Discover.html")
        elif "/*FUND*/" not in html:
            print("NOTE: no /*FUND*/.../*ENDFUND*/ markers in Discover.html — add them to enable injection.")


if __name__ == "__main__":
    main()
