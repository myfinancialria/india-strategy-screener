"""
Multi-Style Backtest — replay the six styles across the ~2 years of price history
in data/prices.parquet to produce REAL, already-triggered trades, push them into the
journal, and build a monthly P&L heatmap (styles × months) for overall performance.

Each style's live entry rule is replayed walk-forward (signal as-of each bar; the trade
is simulated to its exit on later bars). Signals are then run through a realistic
PORTFOLIO: one ₹10L book per style, max K concurrent positions, fixed 1%-of-book risk
per trade (capped at 25% notional), one position per symbol at a time — so the equity
is capital-constrained, not an unbounded sum of overlapping bets.

Honest approximations (shown on the dashboard too):
  • CANSLIM/Coffee-Can use the CURRENT fundamentals (yfinance has no point-in-time
    history), so the fundamental gate is held static across the window.
  • CPR is intraday — replayed as a 1-day-bar proxy (enter the TC/PDH breakout,
    square off at the close).

Outputs:
  data/style_backtest.json   monthly matrix + per-style stats + equity curves
  docs/style_journal.json     journal updated with the backtested trades (capped for display)
  docs/styles.html            Performance heatmap (/*BACKTEST*/) + journal (/*JOURNAL*/) injected

Usage:  python3 nse_screener/style_backtest.py
"""
import os, sys, json
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from styles import lib, financials, STYLES
import style_journal as sj

BT_FILE = os.path.join(ROOT, "data", "style_backtest.json")
DASH = os.path.join(ROOT, "docs", "styles.html")
CAP, RISK = lib.CAP, lib.RISK_PCT
DISPLAY_CAP = 60                     # closed trades kept per style in the embedded journal
MAXPOS_FRAC = 0.25                   # cap any single position at 25% of the book
SLOTS = {"canslim": 8, "turtle": 6, "supertrend_ema": 8, "swing": 8, "cpr": 2}


# ---------------------------------------------------------------- trade simulator
def simulate(h, l, c, i, entry, sl, t1, t2, trail_low=None, flip=None):
    """Walk forward from bar i+1; return (exit_idx, exit_px, status)."""
    s, n = sl, len(c)
    for j in range(i + 1, n):
        if l[j] <= s:
            return j, s, "CLOSED"
        if t1 and s < entry and h[j] >= t1:
            s = entry                                   # breakeven stop after t1
        if t2 and h[j] >= t2:
            return j, t2, "CLOSED"
        if trail_low is not None and not np.isnan(trail_low[j]) and l[j] <= trail_low[j]:
            return j, trail_low[j], "CLOSED"
        if flip is not None and flip[j]:
            return j, c[j], "CLOSED"
    return n - 1, c[-1], "OPEN"


def cand(sym, style, entry, sl, t1, t2, didx, exit_idx, exit_px, status, dates, reason, sector):
    return {"symbol": sym, "style": style, "entry": float(entry), "sl": float(sl),
            "t1": float(t1), "t2": float(t2), "exit_px": float(exit_px), "status": status,
            "entry_date": str(pd.Timestamp(dates[didx]).date()),
            "exit_date": str(pd.Timestamp(dates[exit_idx]).date()),
            "reason": reason, "sector": sector}


# ---------------------------------------------------------------- per-style replays → candidates
def bt_turtle(prices, fund, syms):
    out = []
    for sym in syms:
        g = prices[prices.symbol == sym]
        if len(g) < 70:
            continue
        dates = g["date"].values; h, l, c = g["high"].values, g["low"].values, g["close"].values
        if np.mean(g["turnover_lacs"].values[-20:]) < 100:
            continue
        N = lib.atr(h, l, c, 20); hi55, _ = lib.donchian(h, l, 55); _, lo20 = lib.donchian(h, l, 20)
        sec = lib.sector_of(sym, fund); i = 55
        while i < len(c) - 1:
            if not np.isnan(hi55[i]) and c[i] > hi55[i] and N[i] > 0:
                entry = c[i]; sl = entry - 2 * N[i]
                ej, ep, st = simulate(h, l, c, i, entry, sl, None, None, trail_low=lo20)
                out.append(cand(sym, "turtle", entry, sl, entry + 4 * N[i], entry + 8 * N[i],
                                i, ej, ep, st, dates, "55-day Donchian breakout; 2N stop, 20-day-low trail", sec))
                i = ej + 1
            else:
                i += 1
    return out


def bt_supertrend(prices, fund, syms):
    out = []
    for sym in syms:
        g = prices[prices.symbol == sym]
        if len(g) < 60 or g["series"].iloc[-1] != "EQ":
            continue
        dates = g["date"].values; h, l, c = g["high"].values, g["low"].values, g["close"].values
        if c[-1] < 30 or np.mean(g["turnover_lacs"].values[-20:]) < 200:
            continue
        e20 = lib.ema(c, 20); line, dirn = lib.supertrend(h, l, c, 10, 3.0); flip = dirn == -1
        sec = lib.sector_of(sym, fund); i = 20
        while i < len(c) - 1:
            if dirn[i] == 1 and dirn[i - 1] == -1 and c[i] > e20[i]:
                entry = c[i]; sl = min(line[i], l[max(0, i - 4):i + 1].min()) * 0.998
                risk = entry - sl
                if risk <= 0 or 100 * risk / entry > 12:
                    i += 1; continue
                ej, ep, st = simulate(h, l, c, i, entry, sl, entry + 2 * risk, entry + 3 * risk, flip=flip)
                out.append(cand(sym, "supertrend_ema", entry, sl, entry + 2 * risk, entry + 3 * risk,
                                i, ej, ep, st, dates, "Supertrend(10,3) green above 20-EMA", sec))
                i = ej + 1
            else:
                i += 1
    return out


def bt_swing(prices, fund, syms):
    out = []
    for sym in syms:
        g = prices[prices.symbol == sym]
        if len(g) < 210 or g["series"].iloc[-1] != "EQ":
            continue
        dates = g["date"].values; o = g["open"].values; h = g["high"].values
        l = g["low"].values; c = g["close"].values; v = g["volume"].values
        if c[-1] < 30 or np.mean(g["turnover_lacs"].values[-20:]) < 200:
            continue
        e20, e50, e200 = lib.ema(c, 20), lib.ema(c, 50), lib.ema(c, 200)
        sma20 = pd.Series(c).rolling(20).mean().values
        std20 = pd.Series(c).rolling(20).std().values
        upper = sma20 + 2 * std20; bw = (upper - (sma20 - 2 * std20)) / sma20
        vol20 = pd.Series(v).rolling(20).mean().values
        sec = lib.sector_of(sym, fund); i = 205
        while i < len(c) - 1:
            entry = sl = setup = None
            green = c[i] > o[i] and (c[i] - l[i]) > 0.5 * (h[i] - l[i])
            near20 = abs(c[i] / e20[i] - 1) <= 0.03 or l[i] <= e20[i] * 1.01
            vs = v[i] / vol20[i] if vol20[i] > 0 else 0
            if e20[i] > e50[i] > e200[i] and e20[i] > e20[i - 5] and near20 and green:
                setup = "20EMA-pullback"; entry = h[i] * 1.002; sl = min(l[i], e50[i]) * 0.997
            elif bw[i] <= np.nanmin(bw[max(0, i - 19):i + 1]) * 1.02 and c[i] > upper[i] and vs >= 1.5:
                setup = "BB-squeeze"; entry = c[i] * 1.002; sl = sma20[i]
            if not setup or entry - sl <= 0 or 100 * (entry - sl) / entry > 12:
                i += 1; continue
            risk = entry - sl
            ej, ep, st = simulate(h, l, c, i, entry, sl, entry + 2 * risk, entry + 3 * risk)
            out.append(cand(sym, "swing", entry, sl, entry + 2 * risk, entry + 3 * risk,
                            i, ej, ep, st, dates, setup, sec))
            i = ej + 1
    return out


def bt_canslim(prices, fund, fin, syms):
    out = []
    for sym in syms:
        f = fin.get(sym, {}); epsg = f.get("eps_ttm_g")
        if epsg is None or epsg < 25:
            continue
        g = prices[prices.symbol == sym]
        if len(g) < 160 or g["series"].iloc[-1] != "EQ":
            continue
        dates = g["date"].values; h = g["high"].values; l = g["low"].values
        c = g["close"].values; v = g["volume"].values
        if c[-1] < 30 or np.mean(g["turnover_lacs"].values[-20:]) < 300:
            continue
        prior20hi = pd.Series(h).shift(1).rolling(20).max().values
        hi252 = pd.Series(h).rolling(252, min_periods=120).max().values
        vol50 = pd.Series(v).rolling(50).mean().values
        sec = lib.sector_of(sym, fund); i = 150
        while i < len(c) - 1:
            mom6 = c[i] / c[i - 126] - 1 if i >= 126 else 0
            breakout = not np.isnan(prior20hi[i]) and c[i] > prior20hi[i]
            near_high = not np.isnan(hi252[i]) and c[i] >= hi252[i] * 0.92
            vs = v[i] / vol50[i] if vol50[i] > 0 else 0
            if breakout and near_high and mom6 >= 0.15 and vs >= 1.4:
                entry = c[i] * 1.002; sl = entry * 0.925
                ej, ep, st = simulate(h, l, c, i, entry, sl, entry * 1.20, entry * 1.25)
                out.append(cand(sym, "canslim", entry, sl, entry * 1.20, entry * 1.25,
                                i, ej, ep, st, dates, f"breakout+vol; earnings g {epsg}% (static)", sec))
                i = ej + 1
            else:
                i += 1
    return out


def bt_cpr(prices, fund):
    """Intraday CPR replay on index proxies — per the playbook, only trade the breakout on a
    NARROW-CPR day (low prior-day volatility → trending day). Enter TC/PDH breakout, target R1,
    stop BC, else square off at the close. (Daily-bar approximation of an intraday strategy.)"""
    from styles.cpr import INDEX_PROXIES, _levels, NARROW
    out = []
    for sym, name in INDEX_PROXIES.items():
        g = prices[prices.symbol == sym]
        if len(g) < 30:
            continue
        dates = g["date"].values; h = g["high"].values; l = g["low"].values; c = g["close"].values
        for i in range(1, len(c)):
            lv = _levels(h[i - 1], l[i - 1], c[i - 1])
            width = abs(lv["TC"] - lv["BC"]) / lv["P"] if lv["P"] else 1
            trig, bc, r1 = max(lv["TC"], lv["PDH"]), lv["BC"], lv["R1"]
            if width > NARROW or trig - bc <= 0 or h[i] < trig:   # narrow-CPR breakout days only
                continue
            exit_px = bc if l[i] <= bc else (r1 if h[i] >= r1 else c[i])
            out.append(cand(sym, "cpr", trig, bc, r1, lv["R2"], i, i, exit_px, "CLOSED",
                            dates, f"{name}: narrow-CPR breakout (T R1 / SL BC / square-off)", name))
    return out


def bt_coffee_can(prices, fund, fin):
    """Buy the current quality basket at the window start, hold; OPEN trades + monthly MTM."""
    import styles.coffee_can as cc
    last_px = prices.sort_values("date").groupby("symbol")["close"].last().to_dict()
    picks = cc.scan(last_px, fund, fin)[:15]
    trades, monthly, equity = [], {}, {}
    for s in picks:
        g = prices[prices.symbol == s["symbol"]].sort_values("date")
        if len(g) < 30:
            continue
        d = g["date"].values; c = g["close"].values
        entry, last = float(c[0]), float(c[-1]); qty = int(CAP * 0.10 / entry) or 1
        trades.append({**s, "qty": qty, "entry": round(entry, 2), "status": "OPEN",
                       "entry_date": str(pd.Timestamp(d[0]).date()), "created": str(pd.Timestamp(d[0]).date()),
                       "exit": None, "exit_date": None, "last": round(last, 2),
                       "pnl": round(qty * (last - entry)), "note": "backtest (held from window start)"})
        ser = pd.Series(c, index=pd.to_datetime(d)).resample("ME").last()
        prev = entry
        for ts, px in ser.items():
            m = ts.strftime("%Y-%m")
            monthly[m] = monthly.get(m, 0) + qty * (px - prev); prev = px
    cum = 0
    for m in sorted(monthly):
        cum += monthly[m]; equity[m] = round(cum)
    return trades, {k: round(v) for k, v in monthly.items()}, equity


# ---------------------------------------------------------------- portfolio scheduler
def portfolio(cands, K):
    """Capacity-constrained, fixed-fractional-risk book. Returns taken trades w/ qty + pnl."""
    cands = sorted(cands, key=lambda x: x["entry_date"])
    open_pos, held, taken = [], set(), []
    for c in cands:
        still = []                                      # release positions that exited before this entry
        for p in open_pos:
            if p["exit_date"] > c["entry_date"]:
                still.append(p)
            else:
                held.discard(p["symbol"])
        open_pos = still
        if len(open_pos) >= K or c["symbol"] in held:
            continue
        risk_ps = c["entry"] - c["sl"]
        if risk_ps <= 0:
            continue
        qty = int(min(CAP * RISK / risk_ps, MAXPOS_FRAC * CAP / c["entry"]))
        if qty <= 0:
            continue
        pnl = round(qty * (c["exit_px"] - c["entry"]))
        t = {"symbol": c["symbol"], "style": c["style"], "side": "BUY",
             "entry": round(c["entry"], 2), "sl": round(c["sl"], 2),
             "t1": round(c["t1"], 2), "t2": round(c["t2"], 2), "qty": qty, "score": 0,
             "sector": c["sector"], "reason": c["reason"], "horizon": "", "rr": None, "metrics": {},
             "created": c["entry_date"], "entry_date": c["entry_date"], "status": c["status"],
             "exit": round(c["exit_px"], 2) if c["status"] == "CLOSED" else None,
             "exit_date": c["exit_date"] if c["status"] == "CLOSED" else None,
             "last": round(c["exit_px"], 2), "pnl": pnl, "note": "backtest"}
        taken.append(t)
        open_pos.append({"symbol": c["symbol"], "exit_date": c["exit_date"]})
        held.add(c["symbol"])
    return taken


def monthly_equity(trades):
    monthly, equity, cum = {}, {}, 0
    for t in sorted([t for t in trades if t["status"] == "CLOSED" and t["exit_date"]],
                    key=lambda x: x["exit_date"]):
        m = t["exit_date"][:7]; monthly[m] = monthly.get(m, 0) + t["pnl"]
    for m in sorted(monthly):
        cum += monthly[m]; equity[m] = round(cum)
    return {k: round(v) for k, v in monthly.items()}, equity


def main():
    print("Loading prices…")
    prices = lib.load_prices()
    as_of = str(prices["date"].max().date())
    fund = lib.load_fundamentals()
    n500 = lib.universe("nifty500"); n100 = lib.universe("nifty100") or n500
    fin = financials.get(sorted(set(n100) | set(n500)), quiet=True)
    proxies = ["NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES", "SILVERBEES"]
    p500 = prices[prices.symbol.isin(set(n500) | set(proxies))]

    print("Replaying styles over", f"{str(prices['date'].min().date())} → {as_of}…")
    # NOTE: CPR is intraday — it cannot be honestly P&L-backtested on daily (EOD) bars, so it is
    # excluded from the performance numbers (its live levels/plan stay on the CPR tab).
    raw = {
        "turtle": bt_turtle(p500, fund, list(dict.fromkeys(proxies + n100))),
        "supertrend_ema": bt_supertrend(p500, fund, n500),
        "swing": bt_swing(p500, fund, n500),
        "canslim": bt_canslim(p500, fund, fin, n500),
    }
    bt = {st: portfolio(c, SLOTS[st]) for st, c in raw.items()}
    cc_trades, cc_monthly, cc_equity = bt_coffee_can(prices, fund, fin)
    bt["coffee_can"] = cc_trades

    style_monthly, style_equity, stats = {}, {}, {}
    for st, trades in bt.items():
        if st == "coffee_can":
            mm, eq = cc_monthly, cc_equity
        else:
            mm, eq = monthly_equity(trades)
        style_monthly[st], style_equity[st] = mm, eq
        closed = [t for t in trades if t["status"] == "CLOSED"]
        wins = [t for t in closed if t["pnl"] > 0]
        gp = sum(t["pnl"] for t in wins); gl = -sum(t["pnl"] for t in closed if t["pnl"] < 0)
        total = sum(mm.values()) if st == "coffee_can" else sum(t["pnl"] for t in trades)
        stats[st] = {"trades": len(trades), "closed": len(closed),
                     "open": sum(1 for t in trades if t["status"] == "OPEN"),
                     "win_rate": round(100 * len(wins) / len(closed), 1) if closed else 0,
                     "total": round(total), "ret_pct": round(100 * total / CAP, 1),
                     "profit_factor": round(gp / gl, 2) if gl > 0 else None,
                     "max_dd": _max_dd(style_equity[st])}
        print(f"  {st:<16} trades {len(trades):>4}  win {stats[st]['win_rate']:>5}%  "
              f"PF {str(stats[st]['profit_factor']):>5}  ret {stats[st]['ret_pct']:>6}%  "
              f"total ₹{stats[st]['total']:>11,}")

    order = [s for s in STYLES if s in bt]
    months = sorted({m for st in bt for m in style_monthly[st]})
    matrix = [[round(style_monthly[st].get(m, 0)) for m in months] for st in order]
    col_tot = [sum(style_monthly[st].get(m, 0) for st in bt) for m in months]
    grand = round(sum(col_tot))
    backtest = {"as_of": as_of, "window": f"{str(prices['date'].min().date())} → {as_of}",
                "months": months, "order": order, "matrix": matrix,
                "col_totals": [round(x) for x in col_tot], "stats": stats, "grand_total": grand,
                "excluded": {"cpr": "intraday — not P&L-backtested on daily bars; see the CPR tab for live levels"}}
    json.dump(backtest, open(BT_FILE, "w"), separators=(",", ":"), default=str)
    print(f"\nGrand total (2y portfolio, all styles): ₹{grand:,}  ({round(100*grand/CAP,1)}% of one ₹10L book)")

    _push_journal(bt, stats)
    _inject_backtest(backtest)


def _max_dd(equity):
    if not equity:
        return 0
    vals = [equity[m] for m in sorted(equity)]
    peak, dd = vals[0], 0
    for v in vals:
        peak = max(peak, v); dd = min(dd, v - peak)
    return round(dd)


def _push_journal(bt, stats):
    journal, meta = {}, {}
    if os.path.exists(sj.JFILE):
        journal = json.load(open(sj.JFILE)).get("styles", {})
    scr = os.path.join(ROOT, "data", "style_screens.json")
    if os.path.exists(scr):
        meta = json.load(open(scr)).get("meta", {})
    # strip any prior backtest entries from EVERY style (so excluded styles, e.g. CPR, get cleaned)
    for st, book in journal.items():
        book["entries"] = [e for e in book["entries"] if "backtest" not in (e.get("note") or "")]
    allfull = []
    for st, book in journal.items():
        live = book["entries"]
        trades = bt.get(st, [])
        full = live + trades
        book["stats"] = sj.stats_for(full)
        if st in stats:
            book["stats"]["backtest"] = stats[st]
        opens = [t for t in trades if t["status"] == "OPEN"]
        closed = sorted([t for t in trades if t["status"] == "CLOSED"],
                        key=lambda x: x["exit_date"] or "", reverse=True)[:DISPLAY_CAP]
        book["entries"] = live + opens + closed
        book["meta"] = meta.get(st, book.get("meta", {}))
        allfull += full
    port = sj.stats_for(allfull)
    port["by_style"] = {st: {"total": b["stats"]["total"], "closed": b["stats"]["closed"],
                             "win_rate": b["stats"]["win_rate"], "open": b["stats"]["open"],
                             "watching": b["stats"]["watching"], "health": b["stats"]["health"]["status"]}
                        for st, b in journal.items()}
    out = {"as_of": port.get("as_of") or "", "capital": CAP, "portfolio": port, "styles": journal}
    json.dump(out, open(sj.JFILE, "w"), indent=1, default=str)
    sj._inject_dashboard(out)
    print(f"Journal updated — portfolio realized ₹{port['realized']:,} | unrealized ₹{port['unrealized']:,}")


def _inject_backtest(bt):
    if not os.path.exists(DASH):
        return
    import re
    html = open(DASH).read()
    blob = json.dumps(bt, separators=(",", ":"), default=str).replace("\\", "\\\\")
    h2 = re.sub(r"/\*BACKTEST\*/.*?/\*ENDBACKTEST\*/", "/*BACKTEST*/" + blob + "/*ENDBACKTEST*/",
                html, count=1, flags=re.S)
    if h2 != html:
        open(DASH, "w").write(h2); print(f"Refreshed {DASH} (heatmap)")


if __name__ == "__main__":
    main()
