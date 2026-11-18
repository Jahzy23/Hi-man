"""Single leak-free loader shared by Radar, Lab and the bot.

Bar convention: index `time` = bar OPEN time (UTC). A bar opened at t is complete at t+tf.
Every non-price column on row t holds only information known at that bar's CLOSE (t+tf):
  funding_rate : last funding rate SETTLED at or before bar close (ffilled)
  funding_paid : funding rate settled inside (t, t+tf]  (0 if none) -> used for P&L accounting
  oi, oi_value : open interest snapshot stamped at or before bar close
Source: data.binance.vision (see data/MANIFEST.json for fetch timestamp).
"""
from pathlib import Path
import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
# Funding archive ends 2026-10-31 16:00 UTC (November monthly file not yet published at fetch time).
BACKTEST_START = "2022-11-01"
BACKTEST_END = "2026-10-31 15:59"

def load(sym: str, tf: str = "1h") -> pd.DataFrame:
    k = pd.read_csv(DATA / f"{sym}_1h.csv.gz", parse_dates=["time"]).set_index("time")
    f = pd.read_csv(DATA / f"{sym}_funding.csv.gz", parse_dates=["time"]).set_index("time")
    o = pd.read_csv(DATA / f"{sym}_oi_1h.csv.gz", parse_dates=["time"]).set_index("time")
    close_t = k.index + pd.Timedelta(hours=1)
    fr = f["funding_rate"]
    k["funding_paid"] = fr.reindex(close_t).fillna(0.0).values          # settled exactly at this bar's close
    k["funding_rate"] = fr.reindex(fr.index.union(close_t)).ffill().reindex(close_t).values
    oo = o[["sum_open_interest", "sum_open_interest_value", "sum_toptrader_long_short_ratio",
            "count_long_short_ratio", "sum_taker_long_short_vol_ratio"]]
    oo = oo.reindex(close_t)
    oo = oo.mask(oo["sum_open_interest"] <= 0)   # archive prints OI=0 on a few hours: treat as missing
    oo = oo.ffill(limit=3)
    k["oi"] = oo["sum_open_interest"].values
    k["oi_value"] = oo["sum_open_interest_value"].values
    k["top_ls_ratio"] = oo["sum_toptrader_long_short_ratio"].values
    k["acct_ls_ratio"] = oo["count_long_short_ratio"].values
    if tf == "1h":
        return k
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
           "quote_volume": "sum", "trades": "sum", "taker_buy_base": "sum", "taker_buy_quote": "sum",
           "funding_paid": "sum", "funding_rate": "last", "oi": "last", "oi_value": "last",
           "top_ls_ratio": "last", "acct_ls_ratio": "last"}
    r = k.resample(tf, label="left", closed="left").agg(agg)
    n = k["close"].resample(tf, label="left", closed="left").count()
    return r[n == pd.Timedelta(tf) / pd.Timedelta(hours=1)]       # drop incomplete bars
