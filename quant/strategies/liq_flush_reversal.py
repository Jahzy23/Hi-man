"""Buy straight after a liquidation flush.

Family : reversal after a liquidation flush
Proxy  : a "flush" is one 1h bar with ALL of
           - body (open - close) >= body_atr x ATR of the bars before it,
           - volume >= vol_mult x the average of the previous `vol_n` bars,
           - open interest down >= oi_drop over the bar (contracts; zero prints treated as missing).
         Binance does not publish historical liquidations, so this is a proxy, not a measurement.
Entry  : long at the next open.
Stop   : below the flush low by `stop_buffer_atr` ATR, kept between `min_stop_atr` and `max_stop_atr`
         ATR from the close.
Target : half-way back up the flush body.   Exit: target, stop, or time stop after `max_hold` bars.
No look-ahead: ATR and average volume come from earlier bars; OI is the snapshot at the bar's close.
"""
import numpy as np
import pandas as pd

NAME = "liq_flush_reversal"
FAMILY = "liquidation_flush_reversal"
TIMEFRAME = "1h"
REGIME = "volatile"
ONE_LINER = "After a 1h bar that falls 2+ ATR on 3x volume with open interest down 2%+, buy the next open for a half retrace."
WHY = ("A liquidation cascade is forced selling at any price, and once the forced flow ends there is an air pocket "
       "above. The losers are the liquidated longs; the winner is whoever supplies them liquidity.")
RISK = "Flushes come in series: the first is often not the last, and the stop sits under a low that is easily taken."
DEFAULT_PARAMS = {"atr_n": 24, "body_atr": 2.0, "vol_n": 48, "vol_mult": 3.0, "oi_drop": 0.02,
                  "stop_buffer_atr": 0.5, "min_stop_atr": 1.0, "max_stop_atr": 4.0, "max_hold": 24}
PARAM_GRID = {"body_atr": [1.5, 2.0, 3.0], "vol_mult": [2.0, 3.0, 4.0], "oi_drop": [0.015, 0.02, 0.03]}
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
    o, c, l, v = df["open"], df["close"], df["low"], df["volume"]
    atr = _atr(df, p["atr_n"])
    oi = df["oi"].where(df["oi"] > 0)                         # archive prints 0 on some hours: treat as missing
    oi_chg = oi / oi.shift(1) - 1.0
    flush = ((o - c) >= p["body_atr"] * atr.shift(1)) \
        & (v >= p["vol_mult"] * v.rolling(p["vol_n"]).mean().shift(1)) \
        & (oi_chg <= -p["oi_drop"])
    side = np.where(flush, 1, 0)
    stop = np.minimum(np.maximum(l - p["stop_buffer_atr"] * atr, c - p["max_stop_atr"] * atr), c - p["min_stop_atr"] * atr)
    target = (o + c) / 2.0
    return _finish(df, side, stop, target=target, max_hold=p["max_hold"])
