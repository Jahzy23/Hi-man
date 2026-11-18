"""Long when shorts are paying and price is not falling (long only).

Family : funding extremes
Measure: F = funding settled over the last `sum_bars` bars (6 x 4h = 24 hours), from `funding_paid`.
Entry  : F <= neg_floor (shorts paid longs over the last day) AND close above SMA(trend_n) AND close
         above the close `sum_bars` bars ago -> long at next open, on the first bar this becomes true.
Stop   : `stop_atr` x ATR below the close.
Exit   : F turns positive, or close falls below the SMA, or time stop after `max_hold` bars.
Freshness guard: no signal unless a funding settlement was seen in the last `fresh_bars` bars, so a
stretch with no funding data (zeros) can never signal.
No look-ahead: all windows end at the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "negative_funding_uptrend"
FAMILY = "funding_extremes"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "Long when 24h funding is negative while price is above its 50-bar average and up on the day; out when funding turns positive or price loses the average."
WHY = ("Shorts are paying to hold a position the tape is not rewarding, which makes them fuel for the next leg up. "
       "The losers are those shorts; the long is paid to wait.")
RISK = "Negative funding with a firm price can also be spot selling hedged in perps: informed flow, not trapped shorts."
DEFAULT_PARAMS = {"sum_bars": 6, "neg_floor": -0.0001, "fresh_bars": 3, "trend_n": 50, "atr_n": 14, "stop_atr": 2.5, "max_hold": 30}
PARAM_GRID = {"neg_floor": [-0.00005, -0.0001, -0.0002], "trend_n": [30, 50, 100], "stop_atr": [2.0, 2.5, 3.5]}
WARMUP_BARS = 130
REQUIRED_COLUMNS = ('high', 'low', 'close', 'funding_paid')   # every column this module reads

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
    f = df["funding_paid"].rolling(p["sum_bars"]).sum().round(8)
    sma = c.rolling(p["trend_n"]).mean()
    fp = df["funding_paid"]
    fresh = (fp.notna() & (fp != 0)).astype(float).rolling(p["fresh_bars"]).max() == 1.0   # a settlement was seen recently
    cond = fresh & (f <= p["neg_floor"]) & (c > sma) & (c > c.shift(p["sum_bars"]))
    long_ = cond & ~cond.shift(1, fill_value=False)
    side = np.where(long_, 1, 0)
    stop = c - p["stop_atr"] * atr
    exit_ = (f > 0) | (c < sma)
    return _finish(df, side, stop, exit_=exit_, max_hold=p["max_hold"])
