"""Bollinger-band mean reversion in a choppy market (baseline for ranging regimes).

Family : baseline / mean reversion
Filter : Kaufman efficiency ratio over `er_n` bars below `er_max` (net move is a small share of the
         path travelled = chop). A driftless random walk gives about 1/sqrt(er_n).
Entry  : first close outside the (n, k) Bollinger band -> fade it at next open.
Target : the band mid-line at signal time.   Stop: `stop_atr` x ATR beyond the close.
Exit   : target, stop, or time stop after `max_hold` bars.
No look-ahead: all windows end at the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "bollinger_mean_reversion"
FAMILY = "baseline"
TIMEFRAME = "1h"
REGIME = "ranging"
ONE_LINER = "In a choppy market (low efficiency ratio), fade the first close outside the 2-sigma band back to the 20-bar mean."
WHY = ("Without a trend, excursions beyond two sigma are mostly liquidity-driven noise that market makers are paid "
       "to absorb. The losers are breakout traders who buy the top and sell the bottom of a range.")
RISK = "The one excursion that is a real breakout costs several average wins; the chop filter switches off late."
DEFAULT_PARAMS = {"n": 20, "k": 2.0, "er_n": 48, "er_max": 0.25, "atr_n": 24, "stop_atr": 2.0, "max_hold": 24}
PARAM_GRID = {"k": [2.0, 2.5], "er_max": [0.15, 0.25, 0.35], "stop_atr": [1.5, 2.0, 3.0]}
WARMUP_BARS = 100
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
    c = df["close"]
    atr = _atr(df, p["atr_n"])
    mid = c.rolling(p["n"]).mean()
    sd = c.rolling(p["n"]).std(ddof=0)
    upper, lower = mid + p["k"] * sd, mid - p["k"] * sd
    er = (c - c.shift(p["er_n"])).abs() / c.diff().abs().rolling(p["er_n"]).sum()
    chop = er < p["er_max"]
    below, above = c < lower, c > upper
    long_ = chop & below & ~below.shift(1, fill_value=False)
    short_ = chop & above & ~above.shift(1, fill_value=False)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    return _finish(df, side, stop, target=mid, max_hold=p["max_hold"])
