"""Donchian channel trend following (baseline).

Family : baseline / trend following
Entry  : close above the highest high of the previous `entry_n` bars -> long at next open
         (close below the lowest low -> short). Every new breakout close is a signal, so the
         setup can re-enter after a stop-out.
Stop   : `stop_atr` x ATR from the signal close.
Exit   : close through the opposite `exit_n`-bar channel (trend state ends).
No look-ahead: channels are shifted one bar, so bar i is compared with bars i-n .. i-1.
"""
import numpy as np
import pandas as pd

NAME = "donchian_trend"
FAMILY = "baseline"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "Go with a close beyond the 55-bar channel, leave on a close through the opposite 20-bar channel, 2-ATR stop."
WHY = ("Large crypto moves arrive as multi-day trends because big holders build and unload slowly and leveraged "
       "traders are forced to chase. The losers are range faders and under-margined counter-trend positions that "
       "must be closed into the move.")
RISK = "Whipsaw: most breakouts fail, so it bleeds small losses plus fees until a real trend pays for them."
DEFAULT_PARAMS = {"entry_n": 55, "exit_n": 20, "atr_n": 14, "stop_atr": 2.0}
PARAM_GRID = {"entry_n": [40, 55, 70], "exit_n": [15, 20, 25], "stop_atr": [2.0, 3.0]}
WARMUP_BARS = 400
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
    hh = h.rolling(p["entry_n"]).max().shift(1)
    ll = l.rolling(p["entry_n"]).min().shift(1)
    xh = h.rolling(p["exit_n"]).max().shift(1)
    xl = l.rolling(p["exit_n"]).min().shift(1)
    up, dn = c > hh, c < ll
    # trend state: switched on by an entry breakout, switched off by a close through the exit channel
    long_on = pd.Series(np.where(up, 1.0, np.where(c < xl, 0.0, np.nan)), index=df.index).ffill().fillna(0.0)
    short_on = pd.Series(np.where(dn, 1.0, np.where(c > xh, 0.0, np.nan)), index=df.index).ffill().fillna(0.0)
    state = long_on - short_on
    prev = state.shift(1).fillna(0.0)
    side = np.where(up, 1, np.where(dn, -1, 0))
    stop = np.where(up, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    exit_ = (state != prev) & (prev != 0)
    return _finish(df, side, stop, exit_=exit_)
