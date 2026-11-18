"""Resting limit at the 20-bar average inside a 20/100 trend (limit entry).

Family : trend pullback to support
Trend  : up when SMA(fast) > SMA(slow), down when below.
Order  : while the trend is up and price closed at least `gap_atr` ATR above the fast SMA, rest a buy
         limit AT the fast SMA for the next bar (re-posted every bar, like a trailing bid; maker fee).
         Mirror for down-trends.
Stop   : `stop_atr` x ATR beyond the average.   Target: the prior `target_n`-bar extreme (the swing high
         for longs), only if it is at least `min_room_atr` ATR away from the entry.
Exit   : target, stop, the fast/slow cross, or time stop after `max_hold` bars.
Signal count is NOT trade count here: a signal is a re-posted order, most of them never fill.
No look-ahead: the limit, stop and target are all known at the signal bar's close.
"""
import numpy as np
import pandas as pd

NAME = "pullback_limit_sma"
FAMILY = "trend_pullback"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "In a 20/100 trend, rest a limit at the 20-bar average each bar; target the prior 20-bar extreme, stop 2 ATR beyond the average."
WHY = ("Buying where trend followers habitually reload, and earning the maker fee for it, is cheaper than chasing. "
       "The losers are impatient momentum traders who pay taker fees at the extreme.")
RISK = "Adverse selection: fills cluster on the bars where the trend is breaking, not where it is pausing."
DEFAULT_PARAMS = {"fast": 20, "slow": 100, "atr_n": 14, "gap_atr": 0.5, "stop_atr": 2.0, "target_n": 20,
                  "min_room_atr": 1.0, "max_hold": 30}
PARAM_GRID = {"fast": [20, 30], "slow": [100, 150], "stop_atr": [1.5, 2.0, 3.0]}
WARMUP_BARS = 180
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
    fast = c.rolling(p["fast"]).mean()
    slow = c.rolling(p["slow"]).mean()
    trend = pd.Series(np.where(fast > slow, 1, np.where(fast < slow, -1, 0)), index=df.index)
    prev = trend.shift(1).fillna(0)
    swing_hi = h.rolling(p["target_n"]).max()
    swing_lo = l.rolling(p["target_n"]).min()
    long_ = (trend == 1) & (c >= fast + p["gap_atr"] * atr) & (swing_hi >= fast + p["min_room_atr"] * atr)
    short_ = (trend == -1) & (c <= fast - p["gap_atr"] * atr) & (swing_lo <= fast - p["min_room_atr"] * atr)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, fast - p["stop_atr"] * atr, fast + p["stop_atr"] * atr)
    target = np.where(long_, swing_hi, swing_lo)
    exit_ = (trend != prev) & (prev != 0)
    return _finish(df, side, stop, entry=fast, target=target, exit_=exit_, entry_valid=1, max_hold=p["max_hold"])
