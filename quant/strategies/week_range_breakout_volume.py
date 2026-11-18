"""Breakout of a tight 7-day range while participation is rising.

Family : range breakout on rising volume
Setup  : previous `range_n` bars (42 x 4h = 7 days) form a tight range:
         (high - low) <= width_mult x sqrt(range_n) x ATR.
Volume : average volume of the last `vol_fast` bars (1 day) >= vol_ratio x the average of the
         `vol_slow` bars before them (the prior week).
Entry  : close outside the range with rising volume -> go with it at next open.
Stop   : `stop_atr` x ATR from the close.   Exit: time stop after `max_hold` bars.
No look-ahead: range and ATR come from earlier bars; the volume baseline excludes the recent day.
"""
import numpy as np
import pandas as pd

NAME = "week_range_breakout_volume"
FAMILY = "range_breakout_volume"
TIMEFRAME = "4h"
REGIME = "ranging"
ONE_LINER = "Close outside a tight 7-day range while the last day's volume runs 30% above the prior week: go with it."
WHY = ("A week-long balance area holds a lot of inventory; when price leaves it on rising participation the side "
       "caught inside has to exit over several days. The losers are swing traders positioned for the range to hold.")
RISK = "Few signals, and a false break of a weekly range costs a full 2-ATR stop."
DEFAULT_PARAMS = {"range_n": 42, "width_mult": 1.0, "vol_fast": 6, "vol_slow": 42, "vol_ratio": 1.3,
                  "atr_n": 14, "stop_atr": 2.0, "max_hold": 42}
PARAM_GRID = {"range_n": [30, 42, 60], "width_mult": [0.8, 1.0, 1.25], "vol_ratio": [1.15, 1.3, 1.5]}
WARMUP_BARS = 130
REQUIRED_COLUMNS = ('high', 'low', 'close', 'volume')   # every column this module reads

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
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    atr = _atr(df, p["atr_n"])
    hh = h.rolling(p["range_n"]).max().shift(1)
    ll = l.rolling(p["range_n"]).min().shift(1)
    tight = (hh - ll) <= p["width_mult"] * np.sqrt(p["range_n"]) * atr.shift(1)
    vol_rising = v.rolling(p["vol_fast"]).mean() >= p["vol_ratio"] * v.rolling(p["vol_slow"]).mean().shift(p["vol_fast"])
    up = tight & vol_rising & (c > hh)
    dn = tight & vol_rising & (c < ll)
    side = np.where(up, 1, np.where(dn, -1, 0))
    stop = np.where(up, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    return _finish(df, side, stop, max_hold=p["max_hold"])
