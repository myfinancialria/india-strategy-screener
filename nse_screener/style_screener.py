"""
Multi-Style Screener — one run, four playbook styles, clean per-style data.

  coffee_can      long-term quality/value   (fundamentals: multi-year ROCE/ROE + growth)
  canslim         growth/momentum           (earnings growth + RS + breakout + volume)
  turtle          trend-following Donchian   (20/55 breakout, ATR sizing — indices/ETFs/stocks)
  supertrend_ema  momentum                   (Supertrend 10,3 + 20-EMA)

Live prices come from data/prices.parquet (refreshed intraday by fyers_data.py);
fundamentals from yfinance (fundamentals.json snapshot + styles/financials.py history).

Outputs:
  data/styles/<style>.json     latest picks for one style (with as_of, params, count)
  data/style_screens.json      combined latest snapshot for the dashboard
  docs/styles.html             refreshed via /*STYLES*/ ... /*ENDSTYLES*/ markers

Then style_journal.py records every signal and tracks it over time.

Usage:
  python3 nse_screener/style_screener.py                 # all styles, default universes
  python3 nse_screener/style_screener.py --styles canslim,turtle
  python3 nse_screener/style_screener.py --no-financials # skip yfinance refresh (technical only)
  python3 nse_screener/style_screener.py --top 25
"""
import os, sys, json, datetime as dt, re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

from styles import lib, coffee_can, canslim, turtle, supertrend_ema, swing, cpr, financials

OUT_DIR = os.path.join(ROOT, "data", "styles")
COMBINED = os.path.join(ROOT, "data", "style_screens.json")
DASH = os.path.join(ROOT, "docs", "styles.html")

META = {
    "coffee_can":     {"title": "Coffee Can", "desc": "Long-term quality compounders — multi-year ROCE/ROE>15% & revenue growth>10%", "horizon": "10-year hold", "kind": "fundamental"},
    "canslim":        {"title": "CANSLIM", "desc": "Growth leaders — earnings growth + RS≥80 + breakout on volume", "horizon": "weeks–months", "kind": "techno-fundamental"},
    "turtle":         {"title": "Turtle (Donchian)", "desc": "Trend-following 20/55-day breakouts with ATR position sizing", "horizon": "weeks–months", "kind": "technical"},
    "supertrend_ema": {"title": "Supertrend + 20 EMA", "desc": "Momentum — Supertrend(10,3) flip green above the 20-EMA", "horizon": "days–weeks", "kind": "technical"},
    "swing":          {"title": "Swing Setups", "desc": "20-EMA pullback · Bollinger squeeze · RS-consolidation breakout", "horizon": "days–weeks", "kind": "technical"},
    "cpr":            {"title": "CPR (Intraday)", "desc": "Central Pivot Range levels + breakout/range plan for Nifty/Bank Nifty proxies", "horizon": "intraday", "kind": "technical"},
}


def _opt(name, default):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def run(styles=None, top=20, do_financials=True, quiet=False):
    styles = styles or list(META.keys())
    os.makedirs(OUT_DIR, exist_ok=True)

    if not quiet:
        print("Loading prices.parquet …")
    prices = lib.load_prices()
    as_of = str(prices["date"].max().date())
    fund = lib.load_fundamentals()
    last_px = (prices.sort_values("date").groupby("symbol")["close"].last().to_dict())

    n500 = lib.universe("nifty500")
    n100 = lib.universe("nifty100") or n500

    fin = {}
    if do_financials and ({"coffee_can", "canslim"} & set(styles)):
        fin_univ = sorted(set(n100) | set(n500))
        if not quiet:
            print(f"Refreshing multi-year financials for {len(fin_univ)} symbols (cached, monthly)…")
        fin = financials.get(fin_univ, quiet=quiet)

    results = {}
    for st in styles:
        if not quiet:
            print(f"\n=== {META[st]['title']} ===")
        if st == "coffee_can":
            sigs = coffee_can.scan(last_px, fund, fin)
        elif st == "canslim":
            sigs = canslim.scan(prices[prices.symbol.isin(set(n500) | {"NIFTYBEES"})], fund, fin)
        elif st == "turtle":
            sigs = turtle.scan(prices, fund, extra_universe=n100)
        elif st == "supertrend_ema":
            sigs = supertrend_ema.scan(prices[prices.symbol.isin(set(n500))], fund, universe=n500)
        elif st == "swing":
            sigs = swing.scan(prices[prices.symbol.isin(set(n500) | {"NIFTYBEES"})], fund, universe=n500)
        elif st == "cpr":
            sigs = cpr.scan(prices, fund)
        else:
            continue
        sigs = sigs[:top]
        results[st] = sigs
        payload = {"style": st, "as_of": as_of, "generated": dt.date.today().isoformat(),
                   "meta": META[st], "count": len(sigs), "signals": sigs}
        json.dump(payload, open(os.path.join(OUT_DIR, f"{st}.json"), "w"),
                  separators=(",", ":"), default=str)
        if not quiet:
            for s in sigs[:top]:
                print(f"  {s['symbol']:<14}{s['side']:<5} entry {s['entry']:>10}  sl {s['sl']:>10}"
                      f"  score {s['score']:>6}  {s['reason'][:60]}")
            print(f"  -> {len(sigs)} signals")

    combined = {"as_of": as_of, "generated": dt.date.today().isoformat(),
                "meta": META, "styles": {st: results.get(st, []) for st in META}}
    json.dump(combined, open(COMBINED, "w"), separators=(",", ":"), default=str)
    if not quiet:
        print(f"\nWrote {COMBINED}")

    _inject_dashboard(combined, quiet)
    return combined


def _inject_dashboard(combined, quiet=False):
    if not os.path.exists(DASH):
        return
    html = open(DASH).read()
    blob = json.dumps(combined, separators=(",", ":"), default=str).replace("\\", "\\\\")
    h2 = re.sub(r"/\*STYLES\*/.*?/\*ENDSTYLES\*/", "/*STYLES*/" + blob + "/*ENDSTYLES*/",
                html, count=1, flags=re.S)
    if h2 != html:
        open(DASH, "w").write(h2)
        if not quiet:
            print(f"Refreshed {DASH}")


def main():
    styles = _opt("--styles", "")
    styles = [s.strip() for s in styles.split(",") if s.strip()] or None
    top = int(_opt("--top", 20))
    do_fin = "--no-financials" not in sys.argv
    run(styles=styles, top=top, do_financials=do_fin)


if __name__ == "__main__":
    main()
