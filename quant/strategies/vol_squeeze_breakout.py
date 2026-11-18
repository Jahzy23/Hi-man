"""Volatility-squeeze breakout (baseline).

Family : baseline / volatility breakout
Setup  : squeeze = Bollinger bands (n, bb_k) inside Keltner channels (n, kc_k x ATR): the standard
         "TTM squeeze" definition, no fitted threshold.
Entry  : squeeze was on within the previous `recent` bars and this is the first close outside the
         Bollinger band -> go with it at next open.
Stop   : the band mid-line (n-bar SMA), kept between `min_stop_atr` and `max_stop_atr` ATR from the close.
Exit   : time stop after `max_hold` bars.
No look-ahead: bands end at the signal bar; the squeeze flag is read from earlier bars only.
"""
import numpy as np
import pandas as pd

NAME = "vol_squeeze_breakout"
FAMILY = "baseline"
TIMEFRAME = "4h"
REGIME = "ranging"
ONE_LINER = "After Bollinger bands sat inside Keltner channels (a squeeze), take the first close outside the bands."
WHY = ("Volatility clusters and mean-reverts, so very quiet stretches end in expansion, and stops pile up on both "
       "sides of a tight range. The losers are short-volatility range traders whose stops fuel the first leg.")
RISK = "The first break out of a squeeze is often a head-fake, and the mid-band stop is close."
DEFAULT_PARAMS = {"n": 20, "bb_k": 2.0, "kc_k": 1.5, "recent": 6, "min_stop_atr": 1.0, "max_stop_atr": 3.0, "max_hold": 30}
PARAM_GRID = {"n": [20, 30], "kc_k": [1.25, 1.5, 2.0], "recent": [3, 6, 12]}
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
    c = df["close"]
    n = p["n"]
    atr = _atr(df, n)
    mid = c.rolling(n).mean()
    sd = c.rolling(n).std(ddof=0)
    bb_u, bb_l = mid + p["bb_k"] * sd, mid - p["bb_k"] * sd
    kc_u, kc_l = mid + p["kc_k"] * atr, mid - p["kc_k"] * atr
    squeeze = ((bb_u < kc_u) & (bb_l > kc_l)).astype(float)
    was_squeezed = squeeze.shift(1).rolling(p["recent"]).max() == 1.0
    above, below = c > bb_u, c < bb_l
    up = was_squeezed & above & ~above.shift(1, fill_value=False)
    dn = was_squeezed & below & ~below.shift(1, fill_value=False)
    side = np.where(up, 1, np.where(dn, -1, 0))
    stop_l = np.minimum(np.maximum(mid, c - p["max_stop_atr"] * atr), c - p["min_stop_atr"] * atr)
    stop_s = np.maximum(np.minimum(mid, c + p["max_stop_atr"] * atr), c + p["min_stop_atr"] * atr)
    stop = np.where(up, stop_l, stop_s)
    return _finish(df, side, stop, max_hold=p["max_hold"])
