"""Moving-average trend with an ATR stop (baseline).

Family : baseline / trend following
Entry  : fast SMA crosses above the slow SMA and is still above on the following bar -> long at the
         next open; mirror for shorts. The one-bar wait keeps the exit of the old position (flagged on
         the cross bar) and the entry of the new one on separate rows, so the result does not depend
         on whether the engine can reverse on a single bar.
Stop   : `stop_atr` x ATR from the signal close (disaster stop; no re-entry until the next cross).
Exit   : the opposite cross.
No look-ahead: both averages end at the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "ma_trend_atr_stop"
FAMILY = "baseline"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "Long while the 20-bar average is above the 100-bar average, short below; flip on the cross, 3-ATR disaster stop."
WHY = ("Positions that fight a persistent move are closed over days, not minutes, so direction persists. "
       "The losers are traders who fade a trend early and anyone forced out by margin.")
RISK = "Late in and late out; a sideways market produces a string of crosses that each lose a little."
DEFAULT_PARAMS = {"fast": 20, "slow": 100, "atr_n": 14, "stop_atr": 3.0}
PARAM_GRID = {"fast": [20, 30], "slow": [100, 150, 200], "stop_atr": [2.0, 3.0, 4.0]}
WARMUP_BARS = 260
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
    fast = c.rolling(p["fast"]).mean()
    slow = c.rolling(p["slow"]).mean()
    state = pd.Series(np.where(fast > slow, 1, np.where(fast < slow, -1, 0)), index=df.index)
    prev = state.shift(1).fillna(0)
    flip = (state != prev) & (prev != 0) & (state != 0)      # a real cross, not the end of warm-up
    confirmed = flip.shift(1, fill_value=False) & (state == prev)   # the bar after the cross, cross still holds
    side = state.where(confirmed, 0)
    stop = np.where(state == 1, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    exit_ = (state != prev) & (prev != 0)
    return _finish(df, side, stop, exit_=exit_)
