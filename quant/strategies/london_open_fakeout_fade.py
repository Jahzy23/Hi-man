"""Fade a failed poke outside the Asian range at the London open.

Family : session opens
Range  : high and low of the bars that open from 00:00 up to (not including) `asia_end_hour` UTC.
Entry  : on a bar opening `london_start`..`london_end` UTC, the high trades above the Asian high but
         the bar closes back inside the range -> short at next open (mirror for the low). Needs at
         least `min_room_atr` ATR between the close and the range middle. First such bar of the day only.
Stop   : beyond the poke's extreme by `stop_buffer_atr` ATR, kept between `min_stop_atr` and
         `max_stop_atr` ATR from the close.
Target : the middle of the Asian range.   Exit: target, stop, or time stop after `max_hold` bars.
No look-ahead: the range is a running max/min inside the day, used only once the Asian session is complete.
"""
import numpy as np
import pandas as pd

NAME = "london_open_fakeout_fade"
FAMILY = "session_open"
TIMEFRAME = "1h"
REGIME = "ranging"
ONE_LINER = "A 07:00/08:00 UTC bar pokes outside the Asian range but closes back inside: fade it toward the middle of the range."
WHY = ("The start of a new session often runs the obvious stops beyond the overnight range before the real "
       "direction is set. The losers are breakout traders who entered on the poke.")
RISK = "Small targets next to costs, and a wick on a 1h bar is a crude proxy for a stop-run."
DEFAULT_PARAMS = {"asia_end_hour": 7, "london_start": 7, "london_end": 8, "atr_n": 24, "min_room_atr": 0.5,
                  "stop_buffer_atr": 0.25, "min_stop_atr": 0.75, "max_stop_atr": 3.0, "max_hold": 8}
PARAM_GRID = {"london_end": [8, 9, 10], "min_room_atr": [0.5, 1.0], "stop_buffer_atr": [0.25, 0.5]}
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
    complete = in_asia.astype(int).groupby(day).cumsum() == p["asia_end_hour"]
    window = (hour >= max(p["london_start"], p["asia_end_hour"])) & (hour <= p["london_end"]) & complete
    mid = (asia_hi + asia_lo) / 2.0
    poke_up = (h > asia_hi) & (c < asia_hi)
    poke_dn = (l < asia_lo) & (c > asia_lo)
    fade_s = window & poke_up & ~poke_dn & ((c - mid) >= p["min_room_atr"] * atr)
    fade_l = window & poke_dn & ~poke_up & ((mid - c) >= p["min_room_atr"] * atr)
    any_ = fade_s | fade_l
    first = any_ & (any_.astype(int).groupby(day).cumsum() == 1)
    side = np.where(first & fade_l, 1, np.where(first & fade_s, -1, 0))
    stop_l = np.minimum(np.maximum(l - p["stop_buffer_atr"] * atr, c - p["max_stop_atr"] * atr), c - p["min_stop_atr"] * atr)
    stop_s = np.maximum(np.minimum(h + p["stop_buffer_atr"] * atr, c + p["max_stop_atr"] * atr), c + p["min_stop_atr"] * atr)
    stop = np.where(fade_l, stop_l, stop_s)
    return _finish(df, side, stop, target=mid, max_hold=p["max_hold"])
