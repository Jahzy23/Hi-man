"""Fade an extreme in funding (plain contrarian carry, no price trigger).

Family : funding extremes
Measure: F = funding actually settled over the last `sum_bars` bars (18 x 4h = 3 days), summed from
         `funding_paid`, so it is correct for 8h, 4h and 2h funding intervals alike.
Crowded longs  : F in the top (1 - hi_pct) of its own trailing `rank_bars` bars (90 days) AND F >= pos_floor.
Crowded shorts : F in the bottom (1 - hi_pct) AND F <= neg_floor (strictly negative: shorts are paying).
Entry  : the first bar the condition becomes true -> trade AGAINST the paying side at next open.
Stop   : `stop_atr` x ATR from the close.
Exit   : the funding percentile crosses its median (either way), or time stop after `max_hold` bars.
The floors (0.15% and -0.05% per 3 days, about +18% / -6% annualised) keep "extreme for a quiet
quarter" from counting. Freshness guard: no signal unless a funding settlement was seen in the last
`fresh_bars` bars, so a stretch with no funding data (zeros) can never signal.
No look-ahead: the percentile is a trailing rolling rank, never a full-sample quantile.
"""
import numpy as np
import pandas as pd

NAME = "funding_extreme_fade"
FAMILY = "funding_extremes"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "When 3-day funding is in the top/bottom 5% of the last 90 days and beyond a floor, trade against the paying side for up to 3 days."
WHY = ("Extreme funding means one side is crowded and paying heavily to stay; the carry bleeds them and any "
       "adverse move forces exits. The losers are late leveraged traders on the paying side, and the fade also collects the funding.")
RISK = "Funding can stay extreme for weeks in a strong trend; with no price trigger this stands in front of the move."
DEFAULT_PARAMS = {"sum_bars": 18, "rank_bars": 540, "hi_pct": 0.95, "pos_floor": 0.0015,
                  "neg_floor": -0.0005, "fresh_bars": 3, "atr_n": 14, "stop_atr": 3.0, "max_hold": 18}
PARAM_GRID = {"hi_pct": [0.90, 0.95, 0.98], "pos_floor": [0.0012, 0.0015, 0.0020], "stop_atr": [2.0, 3.0, 4.0]}
WARMUP_BARS = 600
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
    lo_pct = 1.0 - p["hi_pct"]                                          # symmetric tail
    c = df["close"]
    atr = _atr(df, p["atr_n"])
    f = df["funding_paid"].rolling(p["sum_bars"]).sum().round(8)        # rounding makes ties exact
    pr = f.rolling(p["rank_bars"]).rank(pct=True)
    fp = df["funding_paid"]
    fresh = (fp.notna() & (fp != 0)).astype(float).rolling(p["fresh_bars"]).max() == 1.0   # a settlement was seen recently
    crowded_long = fresh & (pr >= p["hi_pct"]) & (f >= p["pos_floor"])
    crowded_short = fresh & (pr <= lo_pct) & (f <= p["neg_floor"])
    short_ = crowded_long & ~crowded_long.shift(1, fill_value=False)
    long_ = crowded_short & ~crowded_short.shift(1, fill_value=False)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    exit_ = ((pr - 0.5) * (pr.shift(1) - 0.5)) < 0
    return _finish(df, side, stop, exit_=exit_, max_hold=p["max_hold"])
