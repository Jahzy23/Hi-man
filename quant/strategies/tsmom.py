"""Time-series momentum (baseline).

Family : baseline / trend following
Entry  : once a day, at the close of the bar that ends at 00:00 UTC, take the sign of the trailing
         `lookback`-bar return (180 x 4h = 30 days). The signal is re-issued every day while the sign
         holds, so the setup re-enters the day after a stop-out.
Stop   : `stop_atr` x ATR from the signal close (wide: it is a disaster stop, not a trading stop).
Exit   : the daily evaluation where the sign has changed.
No look-ahead: the return ends at the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "tsmom"
FAMILY = "baseline"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "Once a day, hold the sign of the trailing 30-day return; 4-ATR disaster stop."
WHY = ("Time-series momentum is the best documented futures anomaly: under-reaction to news and trend-chasing "
       "flows make last month's sign persist. The losers are contrarians and late hedgers trading against the drift.")
RISK = "V-shaped reversals: the sign only flips after much of the reversal has already happened."
DEFAULT_PARAMS = {"lookback": 180, "eval_hour": 20, "atr_n": 14, "stop_atr": 4.0}
PARAM_GRID = {"lookback": [120, 180, 360], "stop_atr": [3.0, 4.0, 5.0]}
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
    c = df["close"]
    atr = _atr(df, p["atr_n"])
    mom = c / c.shift(p["lookback"]) - 1.0
    is_eval = pd.Series(df.index.hour == p["eval_hour"], index=df.index)
    sign = pd.Series(np.sign(mom), index=df.index).where(is_eval & mom.notna())   # NaN between evaluations
    held = sign.ffill().shift(1).fillna(0.0)                                      # sign of the previous evaluation
    side = sign.fillna(0.0)
    stop = np.where(side > 0, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    exit_ = sign.notna() & (sign != held) & (held != 0)
    return _finish(df, side, stop, exit_=exit_)
