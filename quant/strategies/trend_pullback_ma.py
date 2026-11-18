"""Pullback to the 50-bar average inside a 50/200 trend.

Family : trend pullback to support
Trend  : up when SMA(fast) > SMA(slow) and close > SMA(slow); down when both reversed.
Entry  : in an up-trend, a bar trades down to the fast SMA (low <= SMA) and closes back above it as an
         up bar -> long at next open. Mirror in a down-trend.
Stop   : beyond the pullback bar's extreme by `stop_buffer_atr` ATR, kept between `min_stop_atr` and
         `max_stop_atr` ATR from the close.
Exit   : the trend state changes (side-agnostic: a long can only exist while the state is +1).
No look-ahead: averages end at the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "trend_pullback_ma"
FAMILY = "trend_pullback"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "In a 50/200 trend, enter when a bar dips to the 50-bar average and closes back beyond it in the trend direction."
WHY = ("Under-invested trend participants use the first dip to a widely watched average to add, after early "
       "profit-takers have sold. The losers are counter-trend traders who read the dip as a reversal.")
RISK = "The last pullback of every trend is the one that fails; a stop under the dip low is easily run in volatile markets."
DEFAULT_PARAMS = {"fast": 50, "slow": 200, "atr_n": 14, "stop_buffer_atr": 1.0, "min_stop_atr": 1.5, "max_stop_atr": 4.0}
PARAM_GRID = {"fast": [30, 50], "slow": [150, 200, 250], "stop_buffer_atr": [0.5, 1.0, 1.5]}
WARMUP_BARS = 270
REQUIRED_COLUMNS = ('open', 'high', 'low', 'close')   # every column this module reads

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
    o, c, h, l = df["open"], df["close"], df["high"], df["low"]
    atr = _atr(df, p["atr_n"])
    fast = c.rolling(p["fast"]).mean()
    slow = c.rolling(p["slow"]).mean()
    state = pd.Series(np.where((fast > slow) & (c > slow), 1, np.where((fast < slow) & (c < slow), -1, 0)), index=df.index)
    prev = state.shift(1).fillna(0)
    long_ = (state == 1) & (l <= fast) & (c > fast) & (c > o)
    short_ = (state == -1) & (h >= fast) & (c < fast) & (c < o)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop_l = np.minimum(np.maximum(l - p["stop_buffer_atr"] * atr, c - p["max_stop_atr"] * atr), c - p["min_stop_atr"] * atr)
    stop_s = np.maximum(np.minimum(h + p["stop_buffer_atr"] * atr, c + p["max_stop_atr"] * atr), c + p["min_stop_atr"] * atr)
    stop = np.where(long_, stop_l, stop_s)
    exit_ = (state != prev) & (prev != 0)
    return _finish(df, side, stop, exit_=exit_)
