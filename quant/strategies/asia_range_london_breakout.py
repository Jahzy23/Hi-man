"""London-morning breakout of the Asian-session range.

Family : session opens
Range  : high and low of the bars that open from 00:00 up to (not including) `asia_end_hour` UTC.
Entry  : the FIRST hourly close outside that range on bars opening `london_start`..`london_end` UTC
         -> go with it at next open. One signal per day at most.
Stop   : the middle of the Asian range, kept between `min_stop_atr` and `max_stop_atr` ATR from the close.
Exit   : time stop after `max_hold` bars (about the end of the European afternoon).
No look-ahead: the range is a running max/min inside the day and is only used after the Asian
session is complete (all its bars present).
"""
import numpy as np
import pandas as pd

NAME = "asia_range_london_breakout"
FAMILY = "session_open"
TIMEFRAME = "1h"
REGIME = "ranging"
ONE_LINER = "First hourly close outside the 00:00-07:00 UTC range during 07:00-10:59 UTC: go with it for up to 8 hours."
WHY = ("Europe brings the first real volume of the day into a range set in thin Asian hours, and stops cluster just "
       "outside that range. The losers are overnight range traders and their stops.")
RISK = "Crypto trades 24/7, so the session edge may be folklore borrowed from FX; false breaks are frequent."
DEFAULT_PARAMS = {"asia_end_hour": 7, "london_start": 7, "london_end": 10, "atr_n": 24,
                  "min_stop_atr": 1.0, "max_stop_atr": 3.0, "max_hold": 8}
PARAM_GRID = {"asia_end_hour": [6, 7], "london_end": [9, 10, 12], "max_hold": [6, 8, 12]}
WARMUP_BARS = 80
REQUIRED_COLUMNS = ('high', 'low', 'close')   # every column this module reads

# ---- helpers (repeated in every strategy file on purpose: each module stands alone) ----
def _atr(df, n):
    """Average true range: plain mean of the last n true ranges (finite memory, no look-ahead)."""
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _finish(df, side, stop, entry=np.nan, target=np.nan, exit_=False, entry_valid=1, max_hold=np.nan):
    """Build the SPEC output frame.

    Safety net: a signal is dropped unless its stop is a number on the losing side of the entry
    reference (the limit price, or the signal bar's close for market entries) and, for limit
    entries, the limit is passive (not through the market). A target on the wrong side is blanked.
    """
    idx = df.index
    close = df["close"]
    side = pd.Series(side, index=idx).fillna(0).astype("int64")
    stop = pd.Series(stop, index=idx, dtype="float64")
    entry = pd.Series(entry, index=idx, dtype="float64")
    target = pd.Series(target, index=idx, dtype="float64")
    ref = entry.fillna(close)
    ok = ((side == 1) & (stop < ref) & (ref <= close)) | ((side == -1) & (stop > ref) & (ref >= close))
    side = side.where(ok, 0)
    on = side != 0
    target_ok = ((side == 1) & (target > ref)) | ((side == -1) & (target < ref))
    ex = pd.Series(exit_, index=idx)
    return pd.DataFrame({
        "side": side,
        "entry": entry.where(on),
        "entry_valid": int(entry_valid),
        "stop": stop.where(on),
        "target": target.where(on & target_ok),
        "max_hold": float(max_hold),
        "exit": ex.where(ex.notna(), False).astype(bool),
    }, index=idx)


def signals(df, p, ctx=None):
    p = {**DEFAULT_PARAMS, **(p or {})}
    c, h, l = df["close"], df["high"], df["low"]
    atr = _atr(df, p["atr_n"])
    hour = pd.Series(df.index.hour, index=df.index)
    day = pd.Series(df.index.floor("D"), index=df.index)
    in_asia = hour < p["asia_end_hour"]
    asia_hi = h.where(in_asia).groupby(day).cummax().groupby(day).ffill()
    asia_lo = l.where(in_asia).groupby(day).cummin().groupby(day).ffill()
    complete = in_asia.astype(int).groupby(day).cumsum() == p["asia_end_hour"]   # every Asian bar of the day seen
    window = (hour >= max(p["london_start"], p["asia_end_hour"])) & (hour <= p["london_end"]) & complete
    up = window & (c > asia_hi)
    dn = window & (c < asia_lo)
    brk = up | dn
    first = brk & (brk.astype(int).groupby(day).cumsum() == 1)
    side = np.where(first & up, 1, np.where(first & dn, -1, 0))
    mid = (asia_hi + asia_lo) / 2.0
    stop_l = np.minimum(np.maximum(mid, c - p["max_stop_atr"] * atr), c - p["min_stop_atr"] * atr)
    stop_s = np.maximum(np.minimum(mid, c + p["max_stop_atr"] * atr), c + p["min_stop_atr"] * atr)
    stop = np.where(up, stop_l, stop_s)
    return _finish(df, side, stop, max_hold=p["max_hold"])
