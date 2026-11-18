"""Buy the retest of a broken range edge (limit entry).

Family : range breakout on rising volume
Signal : close beyond the previous `range_n`-bar high (low) on volume >= vol_mult x the `vol_n`-bar average.
Entry  : do not chase. Rest a limit AT the broken level for `entry_valid` bars (maker fee).
Stop   : `stop_atr` x ATR beyond the level.   Target: `target_atr` x ATR beyond the level (2R by default).
Exit   : target, stop, or time stop after `max_hold` bars.
No look-ahead: the level and average volume come from bars before the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "breakout_retest"
FAMILY = "range_breakout_volume"
TIMEFRAME = "1h"
REGIME = "trending"
ONE_LINER = "After a volume-backed break of the 72-hour range, bid passively at the broken level and trade the retest."
WHY = ("Traders who missed the break and those trapped on the wrong side both want to deal at the old range edge, "
       "which turns resistance into support. The losers are late chasers shaken out on the retest.")
RISK = "Adverse selection: the limit fills exactly when the breakout is failing, and the best breakouts never come back."
DEFAULT_PARAMS = {"range_n": 72, "vol_n": 48, "vol_mult": 1.5, "atr_n": 24, "stop_atr": 1.5, "target_atr": 3.0,
                  "entry_valid": 12, "max_hold": 48}
PARAM_GRID = {"range_n": [48, 72, 96], "vol_mult": [1.5, 2.0], "entry_valid": [6, 12, 24]}
WARMUP_BARS = 150
REQUIRED_COLUMNS = ('high', 'low', 'close', 'volume')   # every column this module reads

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
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]
    atr = _atr(df, p["atr_n"])
    hh = h.rolling(p["range_n"]).max().shift(1)
    ll = l.rolling(p["range_n"]).min().shift(1)
    vol_ok = v >= p["vol_mult"] * v.rolling(p["vol_n"]).mean().shift(1)
    up = vol_ok & (c > hh)
    dn = vol_ok & (c < ll)
    side = np.where(up, 1, np.where(dn, -1, 0))
    entry = np.where(up, hh, ll)
    stop = np.where(up, hh - p["stop_atr"] * atr, ll + p["stop_atr"] * atr)
    target = np.where(up, hh + p["target_atr"] * atr, ll - p["target_atr"] * atr)
    return _finish(df, side, stop, entry=entry, target=target, entry_valid=p["entry_valid"], max_hold=p["max_hold"])
