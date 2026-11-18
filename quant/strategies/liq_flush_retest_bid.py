"""Bid at the low of a liquidation flush (limit entry).

Family : reversal after a liquidation flush
Proxy  : flush = 1h bar with body >= body_atr x prior ATR, volume >= vol_mult x prior average,
         open interest down >= oi_drop over the bar (same proxy as liq_flush_reversal).
Entry  : rest a buy limit AT the flush bar's low for `entry_valid` bars (maker fee). It only fills
         if price comes back and trades through that low.
Stop   : `stop_atr` x ATR below the low.   Target: the middle of the flush bar's range.
Exit   : target, stop, or time stop after `max_hold` bars.
No look-ahead: everything is known at the flush bar's close.
"""
import numpy as np
import pandas as pd

NAME = "liq_flush_retest_bid"
FAMILY = "liquidation_flush_reversal"
TIMEFRAME = "1h"
REGIME = "volatile"
ONE_LINER = "After a liquidation flush, rest a bid at the flush low for 12 bars; target the middle of the flush bar."
WHY = ("A second visit to a liquidation low meets less forced supply than the first, and the passive buyer earns "
       "the maker fee. The losers are breakdown sellers hitting a low that has already been cleared of stops.")
RISK = "The order only fills if price comes back down, i.e. after the first bounce has already failed."
DEFAULT_PARAMS = {"atr_n": 24, "body_atr": 2.0, "vol_n": 48, "vol_mult": 3.0, "oi_drop": 0.02,
                  "stop_atr": 1.5, "entry_valid": 12, "max_hold": 24}
PARAM_GRID = {"oi_drop": [0.015, 0.02, 0.03], "stop_atr": [1.0, 1.5, 2.0], "entry_valid": [6, 12, 24]}
WARMUP_BARS = 100
REQUIRED_COLUMNS = ('open', 'high', 'low', 'close', 'volume', 'oi')   # every column this module reads

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
    o, c, h, l, v = df["open"], df["close"], df["high"], df["low"], df["volume"]
    atr = _atr(df, p["atr_n"])
    oi = df["oi"].where(df["oi"] > 0)
    oi_chg = oi / oi.shift(1) - 1.0
    flush = ((o - c) >= p["body_atr"] * atr.shift(1)) \
        & (v >= p["vol_mult"] * v.rolling(p["vol_n"]).mean().shift(1)) \
        & (oi_chg <= -p["oi_drop"])
    side = np.where(flush, 1, 0)
    stop = l - p["stop_atr"] * atr
    target = (h + l) / 2.0
    return _finish(df, side, stop, entry=l, target=target, entry_valid=p["entry_valid"], max_hold=p["max_hold"])
