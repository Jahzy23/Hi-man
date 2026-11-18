"""Fade a new extreme that is made while open interest is shrinking.

Family : open-interest divergence
Measure: OI change over `n` bars, in contracts (so a price move does not change it mechanically).
         OI is cleaned first: zero prints dropped, then a trailing 3-bar median against partial prints.
Entry  : first close above the previous `n`-bar high while OI fell >= oi_drop over those n bars -> short
         (rally made of short covering). First close below the previous `n`-bar low with the same OI
         drop -> long (decline made of long liquidation).
Stop   : `stop_atr` x ATR from the close.   Exit: time stop after `max_hold` bars.
No look-ahead: levels are shifted one bar; the OI median is trailing.
"""
import numpy as np
import pandas as pd

NAME = "oi_divergence_fade"
FAMILY = "oi_divergence"
TIMEFRAME = "4h"
REGIME = "ranging"
ONE_LINER = "Fade a new 30-bar extreme that is made while open interest has dropped 5%+ over the same window."
WHY = ("A move made of position closing (short covering or long liquidation) has no new money behind it and tends "
       "to stall when the closing ends. The losers are breakout traders chasing a move that only reflects exits.")
RISK = "OI falls in every sell-off, so the long side is knife-catching with a 2-ATR stop."
DEFAULT_PARAMS = {"n": 30, "oi_drop": 0.05, "atr_n": 14, "stop_atr": 2.0, "max_hold": 30}
PARAM_GRID = {"n": [20, 30, 42], "oi_drop": [0.03, 0.05, 0.08], "stop_atr": [1.5, 2.0, 3.0]}
WARMUP_BARS = 80
REQUIRED_COLUMNS = ('high', 'low', 'close', 'oi')   # every column this module reads

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


def _clean_oi(oi):
    return oi.where(oi > 0).rolling(3, min_periods=2).median()


def signals(df, p, ctx=None):
    p = {**DEFAULT_PARAMS, **(p or {})}
    c, h, l = df["close"], df["high"], df["low"]
    atr = _atr(df, p["atr_n"])
    oi = _clean_oi(df["oi"])
    oi_chg = oi / oi.shift(p["n"]) - 1.0
    shrinking = oi_chg <= -p["oi_drop"]
    hi_c = shrinking & (c > h.rolling(p["n"]).max().shift(1))
    lo_c = shrinking & (c < l.rolling(p["n"]).min().shift(1))
    short_ = hi_c & ~hi_c.shift(1, fill_value=False)
    long_ = lo_c & ~lo_c.shift(1, fill_value=False)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    return _finish(df, side, stop, max_hold=p["max_hold"])
