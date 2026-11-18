"""Trade the unwind of a crowded funding position once price breaks against it.

Family : funding extremes
Measure: F = funding settled over the last `sum_bars` bars (72 x 1h = 3 days), from `funding_paid`.
Crowded longs  : F in the top (1 - hi_pct) of its trailing `rank_bars` bars (90 days) AND F >= pos_floor.
Crowded shorts : F in the bottom (1 - hi_pct) AND F <= neg_floor (strictly negative).
Entry  : crowded longs AND first close below the previous `break_n`-bar low -> short at next open.
         Crowded shorts AND first close above the previous `break_n`-bar high -> long.
Stop   : `stop_atr` x ATR from the close.   Exit: time stop after `max_hold` bars.
Freshness guard: no signal unless a funding settlement was seen in the last `fresh_bars` bars, so a
stretch with no funding data (zeros) can never signal.
No look-ahead: trailing rolling rank; break levels are shifted one bar.
"""
import numpy as np
import pandas as pd

NAME = "funding_crowd_unwind"
FAMILY = "funding_extremes"
TIMEFRAME = "1h"
REGIME = "volatile"
ONE_LINER = "When 3-day funding is in the top decile and price closes below the 72-hour low, short the unwind (mirror for crowded shorts)."
WHY = ("A crowded, levered side has to cut once price moves against it, and its exits push price further. "
       "The losers are leveraged longs (or shorts) who paid up to hold and are now offside.")
RISK = "The trigger bar is often the capitulation itself, so the entry is late; overlaps with plain breakdown selling."
DEFAULT_PARAMS = {"sum_bars": 72, "rank_bars": 2160, "hi_pct": 0.90, "pos_floor": 0.0012, "neg_floor": -0.0003,
                  "fresh_bars": 9, "break_n": 72, "atr_n": 24, "stop_atr": 2.0, "max_hold": 72}
PARAM_GRID = {"hi_pct": [0.80, 0.90, 0.95], "break_n": [48, 72, 120], "stop_atr": [1.5, 2.0, 3.0]}
WARMUP_BARS = 2300
REQUIRED_COLUMNS = ('high', 'low', 'close', 'funding_paid')   # every column this module reads

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
    f = df["funding_paid"].rolling(p["sum_bars"]).sum().round(8)
    pr = f.rolling(p["rank_bars"]).rank(pct=True)
    fp = df["funding_paid"]
    fresh = (fp.notna() & (fp != 0)).astype(float).rolling(p["fresh_bars"]).max() == 1.0   # a settlement was seen recently
    crowded_long = fresh & (pr >= p["hi_pct"]) & (f >= p["pos_floor"])
    crowded_short = fresh & (pr <= 1.0 - p["hi_pct"]) & (f <= p["neg_floor"])
    broke_dn = c < l.rolling(p["break_n"]).min().shift(1)
    broke_up = c > h.rolling(p["break_n"]).max().shift(1)
    short_c = crowded_long & broke_dn
    long_c = crowded_short & broke_up
    short_ = short_c & ~short_c.shift(1, fill_value=False)
    long_ = long_c & ~long_c.shift(1, fill_value=False)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    return _finish(df, side, stop, max_hold=p["max_hold"])
