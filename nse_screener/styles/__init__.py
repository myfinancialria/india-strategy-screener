"""
Multi-style screener package.

Four strategy styles from the trading playbook, each a self-contained scanner
that returns signals in one common schema (see lib.signal):

  coffee_can       long-term quality/value  (Saurabh Mukherjea)   — fundamentals
  canslim          medium-term growth       (William O'Neil)      — techno-fundamental
  turtle           trend following Donchian (Dennis/Eckhardt)     — technical
  supertrend_ema   momentum Supertrend+EMA  (retail MCX/equity)   — technical
  swing            stock swing setups       (pullback/squeeze/RS) — technical
  cpr              CPR intraday index levels(Nifty/BankNifty)     — technical

Each module exposes `scan(...) -> list[signal-dict]`. style_screener.py runs all
six and style_journal.py tracks every signal's life (watching -> open -> closed)
with per-style P&L so you can see past, present and future performance.
"""
STYLES = ["coffee_can", "canslim", "turtle", "supertrend_ema", "swing", "cpr"]
