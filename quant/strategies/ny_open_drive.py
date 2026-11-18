"""Follow a strong New York open.

Family : session opens
Window : the `drive_bars` hourly bars ending with the bar that opens at `decision_hour` UTC
         (default 13:00 and 14:00 UTC: covers the US cash open in both summer and winter time).
Entry  : at the close of the `decision_hour` bar, if the move from the window's first open to that
         close is >= drive_atr x ATR (ATR measured before the window) and the window's average volume
         is >= vol_mult x the average of the `vol_n` bars before it -> follow the move at next open.
Stop   : `stop_atr` x ATR from the close.   Exit: time stop after `max_hold` bars (about the US close).
No look-ahead: ATR and the volume baseline are taken from before the window.
"""
import numpy as np
import pandas as pd

NAME = "ny_open_drive"
FAMILY = "session_open"
TIMEFRAME = "1h"
REGIME = "volatile"
ONE_LINER = "If 13:00-15:00 UTC moves 1.5+ ATR one way on elevated volume, follow it for up to 6 hours."
WHY = ("US cash-session and ETF-related flows are the largest directional flows of the day and are worked over "
       "hours, not minutes. The losers are traders who fade the US-open move too early.")
RISK = "US data releases at 13:30 UTC cause spikes that fully reverse, and the entry comes after the move."
DEFAULT_PARAMS = {"decision_hour": 14, "drive_bars": 2, "drive_atr": 1.5, "vol_n": 48, "vol_mult": 1.5,
                  "atr_n": 24, "stop_atr": 1.5, "max_hold": 6}
PARAM_GRID = {"drive_atr": [1.0, 1.5, 2.0], "vol_mult": [1.0, 1.5, 2.0], "max_hold": [4, 6, 9]}
WARMUP_BARS = 90
REQUIRED_COLUMNS = ('open', 'high', 'low', 'close', 'volume')   # every column this module reads

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
    o, c, v = df["open"], df["close"], df["volume"]
    k = p["drive_bars"]
    atr = _atr(df, p["atr_n"])
    is_decision = pd.Series(df.index.hour == p["decision_hour"], index=df.index)
    drive = c - o.shift(k - 1)
    base_atr = atr.shift(k)
    vol_ok = v.rolling(k).mean() >= p["vol_mult"] * v.rolling(p["vol_n"]).mean().shift(k)
    up = is_decision & vol_ok & (drive >= p["drive_atr"] * base_atr)
    dn = is_decision & vol_ok & (drive <= -p["drive_atr"] * base_atr)
    side = np.where(up, 1, np.where(dn, -1, 0))
    stop = np.where(up, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    return _finish(df, side, stop, max_hold=p["max_hold"])
