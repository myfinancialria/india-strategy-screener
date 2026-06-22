"""
Multi-Style Journal — records every signal each style produces and tracks its
whole life so you can see PAST, PRESENT and FUTURE performance:

  FUTURE   WATCHING  — fresh pick, entry not yet triggered
  PRESENT  OPEN      — price tagged the entry; marked to market
  PAST     CLOSED    — hit stop / target (or, for coffee_can, a recorded exit)

Per style we keep: realized + unrealized P&L, win rate, monthly/yearly breakdown,
a cumulative-equity curve, and a rolling-3-month health flag. A portfolio rollup
aggregates all four styles.

Exit rules by style:
  coffee_can      buy-and-hold; OPEN on day 1, never auto-stopped on price
                  (only a fundamental break is an exit — recorded manually via --close).
  canslim         O'Neil: stop at SL, book at t1 (+20%), final at t2; SL→breakeven after t1.
  turtle          2N stop, milestones t1/t2 (trend trailer).
  supertrend_ema  swing: SL, t1 (1:2), t2 (1:3); SL→breakeven after t1.

Reads data/styles/<style>.json (today's picks) + data/prices.parquet (price updates).
Writes docs/style_journal.json for the dashboard.

Usage:
  python3 nse_screener/style_journal.py                 # ingest today's picks + update all
  python3 nse_screener/style_journal.py --no-add        # just re-price existing entries
  python3 nse_screener/style_journal.py --close coffee_can:HDFCBANK "moat broke"
"""
import os, sys, json, datetime as dt
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from styles import lib

STYLES_DIR = os.path.join(ROOT, "data", "styles")
JFILE = os.path.join(ROOT, "docs", "style_journal.json")
DASH = os.path.join(ROOT, "docs", "styles.html")
TODAY = dt.date.today().isoformat()
BUY_HOLD = {"coffee_can"}          # styles that don't auto-stop on price


# ---------------------------------------------------------------- ingest picks
def load_today():
    picks = {}
    for st in ("coffee_can", "canslim", "turtle", "supertrend_ema", "swing", "cpr"):
        path = os.path.join(STYLES_DIR, f"{st}.json")
        if os.path.exists(path):
            picks[st] = json.load(open(path)).get("signals", [])
    return picks


def add_new(journal, picks):
    """Append signals whose symbol isn't already live (WATCHING/OPEN) in that style."""
    added = 0
    for st, sigs in picks.items():
        book = journal.setdefault(st, {"entries": []})
        live = {e["symbol"] for e in book["entries"] if e["status"] in ("WATCHING", "OPEN", "T1_HIT")}
        for s in sigs:
            if s["symbol"] in live:
                continue
            s = dict(s)
            if st in BUY_HOLD:               # coffee_can is "bought" at the signal
                s["status"] = "OPEN"; s["entry_date"] = s["created"]
            book["entries"].append(s)
            added += 1
    return added


# ---------------------------------------------------------------- price update
def price_index(journal):
    """Load only the symbols we track, as {symbol: DataFrame(date,high,low,close)}."""
    syms = {e["symbol"] for book in journal.values() for e in book["entries"]}
    if not syms:
        return {}, TODAY
    p = lib.load_prices(["symbol", "date", "high", "low", "close"])
    p = p[p.symbol.isin(syms)]
    asof = str(p["date"].max().date()) if len(p) else TODAY
    return {s: g for s, g in p.groupby("symbol")}, asof


def update_entry(e, g, style):
    """Advance one entry's status using its price history g (DataFrame)."""
    if g is None or g.empty:
        e["note"] = "no price data"; return
    last = float(g["close"].values[-1])
    e["last"] = round(last, 2)
    buyhold = style in BUY_HOLD

    if e["status"] == "WATCHING":
        after = g[g["date"] > pd.Timestamp(e["created"])]
        hit = after[after["high"] >= e["entry"]]
        if not hit.empty:
            e["status"] = "OPEN"; e["entry_date"] = str(hit["date"].iloc[0].date())

    if buyhold and e["status"] == "OPEN":
        e["pnl"] = round(e["qty"] * (last - e["entry"]))      # mark-to-market, no auto-exit
        return

    if e["status"] in ("OPEN", "T1_HIT") and e["entry_date"]:
        held = g[g["date"] >= pd.Timestamp(e["entry_date"])]
        sl = e["sl"]
        for _, bar in held.iterrows():
            hi, lo = bar["high"], bar["low"]
            if hi >= e["t1"] and e["status"] == "OPEN":
                e["status"] = "T1_HIT"; sl = max(sl, e["entry"])     # book half / breakeven stop
            if lo <= sl:
                e["status"] = "CLOSED"; e["exit"] = round(sl, 2)
                e["exit_date"] = str(bar["date"].date())
                e["pnl"] = round(e["qty"] * (sl - e["entry"])); return
            if hi >= e["t2"]:
                e["status"] = "CLOSED"; e["exit"] = e["t2"]
                e["exit_date"] = str(bar["date"].date())
                e["pnl"] = round(e["qty"] * (e["t2"] - e["entry"])); return
        e["pnl"] = round(e["qty"] * (last - e["entry"]))           # unrealized mark


# ---------------------------------------------------------------- stats
def stats_for(entries):
    closed = [e for e in entries if e["status"] == "CLOSED"]
    openp = [e for e in entries if e["status"] in ("OPEN", "T1_HIT")]
    watch = [e for e in entries if e["status"] == "WATCHING"]
    realized = sum(e["pnl"] for e in closed)
    unreal = sum(e["pnl"] for e in openp)
    wins = [e for e in closed if e["pnl"] > 0]
    monthly, yearly, equity = {}, {}, []
    cum = 0
    for e in sorted(closed, key=lambda x: x["exit_date"] or ""):
        if not e["exit_date"]:
            continue
        m, y = e["exit_date"][:7], e["exit_date"][:4]
        monthly[m] = monthly.get(m, 0) + e["pnl"]; yearly[y] = yearly.get(y, 0) + e["pnl"]
        cum += e["pnl"]; equity.append({"date": e["exit_date"], "equity": round(cum)})
    cut = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    rec = [e for e in closed if e["exit_date"] and e["exit_date"] >= cut]
    rec_pnl = sum(e["pnl"] for e in rec)
    status = "QUIET" if len(rec) < 3 else ("HEALTHY" if rec_pnl > 0 else "WEAK")
    return {"watching": len(watch), "open": len(openp), "closed": len(closed),
            "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0,
            "realized": round(realized), "unrealized": round(unreal),
            "total": round(realized + unreal),
            "monthly": monthly, "yearly": yearly, "equity_curve": equity,
            "health": {"status": status, "pnl_3mo": round(rec_pnl),
                       "win_3mo": round(100 * sum(1 for e in rec if e["pnl"] > 0) / len(rec), 1) if rec else 0,
                       "trades_3mo": len(rec)}}


# ---------------------------------------------------------------- main
def manual_close(journal):
    """--close style:SYMBOL "reason"  → close a (typically coffee_can) position at last price."""
    if "--close" not in sys.argv:
        return
    tag = sys.argv[sys.argv.index("--close") + 1]
    reason = sys.argv[sys.argv.index("--close") + 2] if len(sys.argv) > sys.argv.index("--close") + 2 else "manual exit"
    st, _, sym = tag.partition(":")
    for e in journal.get(st, {}).get("entries", []):
        if e["symbol"] == sym and e["status"] in ("OPEN", "T1_HIT", "WATCHING"):
            e["status"] = "CLOSED"; e["exit"] = e.get("last", e["entry"])
            e["exit_date"] = TODAY; e["note"] = reason
            e["pnl"] = round(e["qty"] * (e["exit"] - e["entry"]))
            print(f"Closed {tag} @ {e['exit']} ({reason}) pnl ₹{e['pnl']:,}")


def _inject_dashboard(out):
    if not os.path.exists(DASH):
        return
    import re
    html = open(DASH).read()
    blob = json.dumps(out, separators=(",", ":"), default=str).replace("\\", "\\\\")
    h2 = re.sub(r"/\*JOURNAL\*/.*?/\*ENDJOURNAL\*/", "/*JOURNAL*/" + blob + "/*ENDJOURNAL*/",
                html, count=1, flags=re.S)
    if h2 != html:
        open(DASH, "w").write(h2)
        print(f"Refreshed {DASH} (journal)")


def main():
    meta = {}
    if os.path.exists(os.path.join(ROOT, "data", "style_screens.json")):
        meta = json.load(open(os.path.join(ROOT, "data", "style_screens.json"))).get("meta", {})

    journal = {}
    if os.path.exists(JFILE):
        journal = json.load(open(JFILE)).get("styles", {})

    if "--no-add" not in sys.argv:
        added = add_new(journal, load_today())
        print(f"Added {added} new signals across styles.")

    manual_close(journal)

    idx, asof = price_index(journal)
    for st, book in journal.items():
        for e in book["entries"]:
            update_entry(e, idx.get(e["symbol"]), st)
        book["stats"] = stats_for(book["entries"])
        book["meta"] = meta.get(st, book.get("meta", {}))

    # portfolio rollup
    allent = [e for book in journal.values() for e in book["entries"]]
    port = stats_for(allent)
    port["by_style"] = {st: {"total": b["stats"]["total"], "closed": b["stats"]["closed"],
                             "win_rate": b["stats"]["win_rate"], "open": b["stats"]["open"],
                             "watching": b["stats"]["watching"],
                             "health": b["stats"]["health"]["status"]}
                        for st, b in journal.items()}

    os.makedirs(os.path.dirname(JFILE), exist_ok=True)
    out = {"as_of": asof, "capital": lib.CAP, "portfolio": port, "styles": journal}
    json.dump(out, open(JFILE, "w"), indent=1, default=str)
    _inject_dashboard(out)

    print(f"\nMULTI-STYLE JOURNAL (as of {asof})  capital ₹{lib.CAP:,}")
    print(f"  {'STYLE':<16}{'Watch':>6}{'Open':>6}{'Closed':>7}{'Win%':>7}{'Realized':>12}{'Unreal':>12}{'Health':>9}")
    for st, b in journal.items():
        s = b["stats"]
        print(f"  {st:<16}{s['watching']:>6}{s['open']:>6}{s['closed']:>7}{s['win_rate']:>7}"
              f"{s['realized']:>12,}{s['unrealized']:>12,}{s['health']['status']:>9}")
    print(f"  {'PORTFOLIO':<16}{port['watching']:>6}{port['open']:>6}{port['closed']:>7}"
          f"{port['win_rate']:>7}{port['realized']:>12,}{port['unrealized']:>12,}")
    print(f"\n  Saved -> {JFILE}")


if __name__ == "__main__":
    main()
