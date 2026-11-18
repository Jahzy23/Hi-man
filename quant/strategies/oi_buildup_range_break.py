"""Break of a tight range in which open interest has been building.

Family : open-interest divergence
Setup  : previous `range_n` bars form a tight range ((high - low) <= width_mult x sqrt(range_n) x ATR)
         while cleaned OI (contracts) rose >= oi_build over the same bars: price went nowhere but
         leverage was added.
Entry  : close outside the range -> go with the break at next open.
Stop   : `stop_atr` x ATR from the close.   Exit: time stop after `max_hold` bars.
No look-ahead: range, ATR and the OI build are measured on bars before the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "oi_buildup_range_break"
FAMILY = "oi_divergence"
TIMEFRAME = "1h"
REGIME = "ranging"
ONE_LINER = "Open interest builds 5%+ inside a tight 48-hour range; go with the break of that range."
WHY = ("Leverage added inside a narrow range means one side is wrong by construction when the range breaks and "
       "must exit in the breakout direction. The losers are the new positions that are immediately offside.")
RISK = "OI builds can be hedged basis trades that never unwind; the signal is rare, so the sample will be small."
DEFAULT_PARAMS = {"range_n": 48, "width_mult": 1.0, "oi_build": 0.05, "atr_n": 24, "stop_atr": 2.0, "max_hold": 48}
PARAM_GRID = {"range_n": [36, 48, 72], "width_mult": [0.8, 1.0, 1.25], "oi_build": [0.03, 0.05, 0.08]}
WARMUP_BARS = 140
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
    oi = _clean_oi(df["oi"])
    build = (oi.shift(1) / oi.shift(1 + p["range_n"]) - 1.0) >= p["oi_build"]
    hh = h.rolling(p["range_n"]).max().shift(1)
    ll = l.rolling(p["range_n"]).min().shift(1)
    tight = (hh - ll) <= p["width_mult"] * np.sqrt(p["range_n"]) * atr.shift(1)
    up = tight & build & (c > hh)
    dn = tight & build & (c < ll)
    side = np.where(up, 1, np.where(dn, -1, 0))
    stop = np.where(up, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    return _finish(df, side, stop, max_hold=p["max_hold"])
