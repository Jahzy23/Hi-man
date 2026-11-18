"""Re-enter a trend after leverage has been flushed out of it.

Family : open-interest divergence
Trend  : up when SMA(fast) > SMA(slow) and close > SMA(slow); down when both reversed.
Reset  : cleaned OI (contracts) fell >= oi_drop over the last `reset_bars` bars (6 x 4h = 24 hours)
         while price moved AGAINST the trend over the same bars.
Entry  : first bar where trend and reset are both true -> trade with the trend at next open.
Stop   : beyond the extreme of the last `reset_bars` bars by `stop_buffer_atr` ATR, kept between
         `min_stop_atr` and `max_stop_atr` ATR from the close.
Exit   : the trend state changes.
No look-ahead: all windows end at the signal bar; the OI median is trailing.
"""
import numpy as np
import pandas as pd

NAME = "oi_reset_trend"
FAMILY = "oi_divergence"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "In a 50/200 trend, enter with the trend after a 24h counter-move that cut open interest by 5%+."
WHY = ("The counter-move has forced out the weak leveraged hands, so the trend can resume without their overhang. "
       "The losers are over-levered trend followers who were stopped at the worst price.")
RISK = "A 5% drop in open interest can be the start of a regime change rather than a reset."
DEFAULT_PARAMS = {"fast": 50, "slow": 200, "reset_bars": 6, "oi_drop": 0.05, "atr_n": 14,
                  "stop_buffer_atr": 1.0, "min_stop_atr": 1.5, "max_stop_atr": 4.0}
PARAM_GRID = {"reset_bars": [6, 12], "oi_drop": [0.04, 0.05, 0.07], "slow": [150, 200, 250]}
WARMUP_BARS = 270
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
    fast = c.rolling(p["fast"]).mean()
    slow = c.rolling(p["slow"]).mean()
    state = pd.Series(np.where((fast > slow) & (c > slow), 1, np.where((fast < slow) & (c < slow), -1, 0)), index=df.index)
    prev = state.shift(1).fillna(0)
    oi = _clean_oi(df["oi"])
    k = p["reset_bars"]
    flushed = (oi / oi.shift(k) - 1.0) <= -p["oi_drop"]
    long_c = (state == 1) & flushed & (c < c.shift(k))
    short_c = (state == -1) & flushed & (c > c.shift(k))
    long_ = long_c & ~long_c.shift(1, fill_value=False)
    short_ = short_c & ~short_c.shift(1, fill_value=False)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    lo, hi = l.rolling(k).min(), h.rolling(k).max()
    stop_l = np.minimum(np.maximum(lo - p["stop_buffer_atr"] * atr, c - p["max_stop_atr"] * atr), c - p["min_stop_atr"] * atr)
    stop_s = np.maximum(np.minimum(hi + p["stop_buffer_atr"] * atr, c + p["max_stop_atr"] * atr), c + p["min_stop_atr"] * atr)
    stop = np.where(long_, stop_l, stop_s)
    exit_ = (state != prev) & (prev != 0)
    return _finish(df, side, stop, exit_=exit_)
