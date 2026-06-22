"""
Style 3 — Turtle Trading  (medium-term trend following, Dennis/Eckhardt)

Mechanical Donchian breakout with ATR (N) volatility position sizing. Classic for
trending markets — indices, ETFs (incl. commodity proxies GOLDBEES/SILVERBEES) and
liquid high-beta stocks.

  System 1 (short-term)  : enter on 20-day breakout, exit on 10-day reverse extreme.
  System 2 (long-term)   : enter on 55-day breakout, exit on 20-day reverse extreme.
  N = ATR(20). Unit size = (equity × 1%) / N. Hard stop = 2N from entry.

A signal fires when today's close pushes through the Donchian channel. We report
System 2 (55-day) when present (always-take), else System 1 (20-day).
"""
import numpy as np
from . import lib

STYLE = "turtle"
# default trend universe: indices/ETFs (incl. commodity proxies) + room for liquid stocks
CORE = ["NIFTYBEES", "BANKBEES", "JUNIORBEES", "GOLDBEES", "SILVERBEES"]
ACCT_RISK = lib.CAP * lib.RISK_PCT


def scan(prices, fund, extra_universe=None, side="long"):
    syms = list(dict.fromkeys(CORE + (extra_universe or [])))
    have = set(prices["symbol"].unique())
    out = []
    for sym in syms:
        if sym not in have:
            continue
        g = prices[prices.symbol == sym]
        if len(g) < 70:
            continue
        c = g["close"].values; h = g["high"].values; l = g["low"].values
        to = g["turnover_lacs"].values
        px = c[-1]
        if np.mean(to[-20:]) < 100:            # liquidity floor (ETFs/indices pass)
            continue
        n = lib.atr(h, l, c, 20)[-1]
        if not n or n <= 0:
            continue
        hi20, lo20 = lib.donchian(h, l, 20)
        hi55, lo55 = lib.donchian(h, l, 55)

        long_55 = px > hi55[-1] if hi55[-1] == hi55[-1] else False
        long_20 = px > hi20[-1] if hi20[-1] == hi20[-1] else False
        sys_used, channel_hi = (2, hi55[-1]) if long_55 else (1, hi20[-1]) if long_20 else (None, None)
        if sys_used is None:
            continue

        entry = round(px, 2)
        sl = round(entry - 2 * n, 2)                       # 2N hard stop
        exit_ch = lo20[-1] if sys_used == 2 else lo10(l)   # System exit reference
        # targets are trend-following trailers; expose 2N / 4N as informational milestones
        t1, t2 = entry + 4 * n, entry + 8 * n
        unit = int(ACCT_RISK / n) if n > 0 else 0          # Turtle unit sizing (1N = 1% equity)
        sec = lib.sector_of(sym, fund)
        reason = (f"System {sys_used}: {('55' if sys_used==2 else '20')}-day Donchian breakout; "
                  f"N(ATR20)={round(n,2)}; 2N stop; trail {('20' if sys_used==2 else '10')}-day low")
        out.append(lib.signal(
            sym, STYLE, "BUY", entry, sl, t1, t2, px, 1.0, sec, reason,
            horizon="weeks-months",
            metrics={"system": sys_used, "N_atr20": round(n, 2),
                     "donchian_hi": round(float(channel_hi), 2),
                     "exit_channel": round(float(exit_ch), 2) if exit_ch == exit_ch else None,
                     "unit_qty": unit, "pyramid_add_at": round(entry + 0.5 * n, 2)},
            qty=unit or 1,
        ))
    # score by how decisively price cleared the channel (in N units)
    for s in out:
        n = s["metrics"]["N_atr20"]
        s["score"] = round((s["last"] - s["metrics"]["donchian_hi"]) / n, 2) if n else 0
    out.sort(key=lambda s: (s["metrics"]["system"], s["score"]), reverse=True)
    return out


def lo10(low):
    return lib.donchian(low, low, 10)[1][-1] if len(low) >= 11 else float("nan")
