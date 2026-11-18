"""Fade a large late-US move into the Asian open.

Family : session opens
Entry  : at the close of the bar that opens at `decision_hour` UTC (default 23:00, i.e. at 00:00 UTC),
         if price moved >= move_atr x ATR over the last `move_bars` bars (ATR measured before the move)
         -> trade against it at next open.
Target : `retrace` of the move (half by default).   Stop: `stop_atr` x ATR beyond the close.
Exit   : target, stop, or time stop after `max_hold` bars (the Asian morning).
No look-ahead: the move ends at the signal bar and ATR is taken from before it.
"""
import numpy as np
import pandas as pd

NAME = "us_close_asia_fade"
FAMILY = "session_open"
TIMEFRAME = "1h"
REGIME = "ranging"
ONE_LINER = "At 00:00 UTC, if the last 4 hours moved 2.5+ ATR, fade half of it during the Asian morning."
WHY = ("Moves made in the thinnest hours of the day (late US) overshoot because little liquidity is there to absorb "
       "them. The losers are late-session momentum chasers and liquidations into a thin book.")
RISK = "Weakest hypothesis in the set: thin-hour moves are sometimes the informed ones, and the sample is small."
DEFAULT_PARAMS = {"decision_hour": 23, "move_bars": 4, "move_atr": 2.5, "retrace": 0.5, "atr_n": 24,
                  "stop_atr": 1.5, "max_hold": 7}
PARAM_GRID = {"move_bars": [3, 4, 6], "move_atr": [2.0, 2.5, 3.0], "stop_atr": [1.0, 1.5, 2.0]}
WARMUP_BARS = 60
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
    k = p["move_bars"]
    atr = _atr(df, p["atr_n"])
    is_decision = pd.Series(df.index.hour == p["decision_hour"], index=df.index)
    move = c - c.shift(k)
    base_atr = atr.shift(k)
    short_ = is_decision & (move >= p["move_atr"] * base_atr)
    long_ = is_decision & (move <= -p["move_atr"] * base_atr)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    target = c - p["retrace"] * move
    return _finish(df, side, stop, target=target, max_hold=p["max_hold"])
