# 🎯 India Strategy Screener & Journal

**🔗 Live dashboard: https://myfinancialria.github.io/india-strategy-screener/**

Six legendary investing/trading playbooks, screened on the **Indian market** with
**live FYERS prices** + **yfinance fundamentals**, each in its **own dashboard tab**,
each tracked in a **journal** so you can see **past, present and future** performance.
Styled to match [myfinancial.in](https://myfinancial.in) — *Know Your Money. Own Your Future.*

> **Educational tooling for your own account. Not investment advice.** Always confirm a
> live trigger, honour the stop, and consult a SEBI-registered advisor before risking capital.

---

## The six styles (one tab each)

| Tab | Style | Logic | Data | Horizon |
|---|---|---|---|---|
| 1 | **Coffee Can** | multi-year **ROCE/ROE > 15% every year** + revenue growth > 10%, low debt | yfinance financials | 10-yr hold (buy & forget) |
| 2 | **CANSLIM** | earnings growth (C/A) + **RS ≥ 80** (L) + 52w-high/breakout (N) + volume (S) + market gate (M) | financials + prices | weeks–months |
| 3 | **Turtle** | **Donchian 20/55-day** breakout, N = ATR(20), **2N stop**, unit sizing; indices/ETFs (incl. GOLDBEES/SILVERBEES) + Nifty-100 | prices | weeks–months |
| 4 | **Supertrend + 20 EMA** | **Supertrend(10,3)** flips green **above the 20-EMA**; SL at the line, 1:2 / 1:3 targets | prices | days–weeks |
| 5 | **Swing Setups** | **20-EMA pullback** · **Bollinger squeeze** · **RS-consolidation** breakout | prices | days–weeks |
| 6 | **CPR (Intraday)** | **Central Pivot Range** levels + narrow/wide day-type + breakout/range plan for Nifty / Bank Nifty proxies | prices | intraday |

A **portfolio rollup** aggregates all six. See [`STRATEGIES.md`](STRATEGIES.md) for the full playbook rules.

---

## Dashboard

Open **[`docs/styles.html`](docs/styles.html)** — it opens on a **📊 Performance (2y)** tab
(monthly **P&L heatmap** by style + per-style win-rate / profit-factor / return / max-DD), then
a tab per style with:
- **Current signals**: entry / SL / T1 / T2 / R:R / qty / score / why
- **Journal (past & present)**: every signal's life — `WATCHING → OPEN → CLOSED` — with P&L
- **Equity curve** of realized P&L, win rate, monthly/yearly breakdown, 3-month health flag

The page ships with data embedded, so it works straight from a clone or **GitHub Pages**
(Settings → Pages → deploy from `main` / `/docs`).

---

## How it works

```
NSE bhavcopy (EOD)  ──update_data.py──►  data/bhavcopy/
                    ──consolidate.py──►  data/prices.parquet   (corp-action adjusted OHLCV)
FYERS live bar      ──fyers_data.py───►  (overlays today's forming candle — optional, intraday-fresh)
yfinance            ──fundamentals.py─►  data/fundamentals.json
                    ──styles/financials.py►  data/financials.json   (multi-year ROCE/ROE/revenue)

style_screener.py  ──►  data/styles/<style>.json + data/style_screens.json  ──►  docs/styles.html
style_journal.py   ──►  docs/style_journal.json (tracks every signal)        ──►  docs/styles.html
```

## Quick start

```bash
pip install -r requirements.txt

# 1) build the price history (one-time, then daily)
python3 nse_screener/update_data.py
python3 nse_screener/consolidate.py data/bhavcopy data/prices.parquet

# 2) (optional) intraday-fresh live prices via FYERS — needs data/.fyers.json
#    python3 nse_screener/fyers_data.py --auth        # one-time login
#    python3 nse_screener/fyers_data.py               # overlay today's live bar

# 3) screen all six styles, then record + track them
python3 nse_screener/style_screener.py --top 20
python3 nse_screener/style_journal.py

# 4) (optional) backtest the last ~2 years -> real triggered trades in the journal + the heatmap
python3 nse_screener/style_backtest.py

# open docs/styles.html
```

### 2-year backtest & heatmap
`style_backtest.py` replays each style's live rule across the price history and runs the
signals through a realistic **capacity-constrained portfolio** (one ₹10L book per style, max K
concurrent positions, 1% fixed-fractional risk, one position per symbol). It pushes the resulting
closed/open trades into the journal and builds the monthly **P&L heatmap** (`data/style_backtest.json`).
**CPR is intraday and is deliberately NOT P&L-backtested** on daily bars (its tab shows live levels only).

Useful flags:
```bash
python3 nse_screener/style_screener.py --styles canslim,turtle   # only some styles
python3 nse_screener/style_screener.py --no-financials           # technical styles only (skip yfinance)
python3 nse_screener/style_journal.py  --no-add                  # re-price existing entries only
python3 nse_screener/style_journal.py  --close coffee_can:HDFCBANK "moat broke"   # record a fundamental exit
```

### FYERS credentials (optional — only for intraday-fresh prices)
`data/.fyers.json` (git-ignored, never commit):
```json
{ "app_id":"XXXX-100", "secret":"YYYY", "redirect":"https://127.0.0.1",
  "pin":"1234", "fy_id":"AA0000", "totp_key":"BASE32SEED" }
```
`fy_id` + `totp_key` + `pin` enable fully headless daily login. The screener works on
**EOD bhavcopy alone** without FYERS — the live overlay just makes a mid-session re-run reflect today's prices.

---

## Journal lifecycle

`WATCHING` (future — entry not yet triggered) → `OPEN` (present — price tagged the entry, marked
to market) → `CLOSED` (past — hit stop / target). **Coffee-Can is buy-and-hold**: opened on day one,
marked to market, and only a **fundamental** break is an exit (record it with `--close`).

## Notes & honest limitations
- **Coffee-Can checks ~4–5 years, not 10.** Free yfinance returns ~5 fiscal years, so the
  "every year" quality filter is applied over what's available (≥3 yrs) and labels the count —
  it does not fabricate a decade of data.
- **CPR is an EOD-bar approximation** of an intraday strategy: the levels are exact, but the
  journal fill/exit is a daily-bar proxy (intraday entries can't be reconstructed from daily candles).
- Index/commodity exposure uses **ETF proxies** (NIFTYBEES, BANKBEES, GOLDBEES, SILVERBEES) that
  trade on NSE and appear in the price history.
