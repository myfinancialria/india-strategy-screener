"""Consolidate NSE sec_bhavdata_full daily files into one adjusted price panel."""
import pandas as pd, numpy as np, glob, os, sys

SRC = sys.argv[1] if len(sys.argv) > 1 else "/tmp/bhav"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/prices.parquet"

frames = []
for f in sorted(glob.glob(os.path.join(SRC, "sec_bhavdata_full_*.csv"))):
    df = pd.read_csv(f, skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    df = df[df["SERIES"].isin(["EQ", "BE"])].copy()
    frames.append(df[["SYMBOL","SERIES","DATE1","PREV_CLOSE","OPEN_PRICE","HIGH_PRICE",
                      "LOW_PRICE","CLOSE_PRICE","TTL_TRD_QNTY","TURNOVER_LACS","DELIV_PER"]])

panel = pd.concat(frames, ignore_index=True)
panel["DATE"] = pd.to_datetime(panel["DATE1"].str.strip(), format="%d-%b-%Y")
for c in ["PREV_CLOSE","OPEN_PRICE","HIGH_PRICE","LOW_PRICE","CLOSE_PRICE","TTL_TRD_QNTY","TURNOVER_LACS"]:
    panel[c] = pd.to_numeric(panel[c], errors="coerce")
panel = panel.dropna(subset=["CLOSE_PRICE"]).sort_values(["SYMBOL","DATE"])
panel = panel.drop_duplicates(subset=["SYMBOL","SERIES","DATE"], keep="last")
# if a symbol has both EQ and BE on the same date keep EQ
panel = panel.sort_values(["SYMBOL","DATE","SERIES"]).drop_duplicates(subset=["SYMBOL","DATE"], keep="first")

# Corporate-action adjustment, two detectors:
# (a) official: PREV_CLOSE != prior day's CLOSE (mismatch > 2%) -> factor = PREV_CLOSE/prior
# (b) split/bonus NOT reflected in PREV_CLOSE (seen in sec_bhavdata_full): the open gaps
#     down >45% vs prior close. NSE price bands make genuine -45% days virtually impossible,
#     so treat as CA with factor = OPEN/prior (open carries the adjustment, little drift).
panel["prior_close"] = panel.groupby("SYMBOL")["CLOSE_PRICE"].shift(1)
ratio_prev = panel["PREV_CLOSE"] / panel["prior_close"]
ratio_open = panel["OPEN_PRICE"] / panel["prior_close"]
official = (ratio_prev - 1).abs() > 0.02
gap_split = (~official) & (ratio_open < 0.55) & panel["prior_close"].notna()
panel["ca_factor"] = np.where(official, ratio_prev, np.where(gap_split, ratio_open, 1.0))
panel["ca_factor"] = panel["ca_factor"].fillna(1.0)
# cumulative product of FUTURE factors applied to past prices
panel["cum_adj"] = (panel[::-1].groupby("SYMBOL")["ca_factor"].cumprod()[::-1]
                    .groupby(panel["SYMBOL"]).shift(-1).fillna(1.0))
for c in ["OPEN_PRICE","HIGH_PRICE","LOW_PRICE","CLOSE_PRICE"]:
    panel["ADJ_" + c] = panel[c] * panel["cum_adj"]

out = panel[["SYMBOL","SERIES","DATE","ADJ_OPEN_PRICE","ADJ_HIGH_PRICE","ADJ_LOW_PRICE",
             "ADJ_CLOSE_PRICE","CLOSE_PRICE","TTL_TRD_QNTY","TURNOVER_LACS","DELIV_PER"]]
out.columns = ["symbol","series","date","open","high","low","close","close_raw","volume","turnover_lacs","deliv_pct"]
out.to_parquet(OUT, index=False)
print(f"rows={len(out)} symbols={out.symbol.nunique()} dates={out.date.nunique()} "
      f"range={out.date.min().date()}..{out.date.max().date()}")
ca = panel[panel.ca_factor != 1.0]
print(f"corporate-action adjustments applied: {len(ca)} events on {ca.SYMBOL.nunique()} symbols")
