"""Buy the first sign of recovery after a liquidation flush.

Family : reversal after a liquidation flush
Proxy  : flush = 1h bar with body >= body_atr x prior ATR, volume >= vol_mult x prior average,
         open interest down >= oi_drop over the bar (same proxy as liq_flush_reversal).
Entry  : within `confirm_within` bars after the flush, the FIRST up bar that closes above the previous
         bar's high -> long at next open.
Stop   : below the lowest low since the flush by `stop_buffer_atr` ATR, kept between `min_stop_atr`
         and `max_stop_atr` ATR from the close.
Exit   : stop or time stop after `max_hold` bars.
No look-ahead: "since the flush" is a running minimum from the flush bar up to the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "liq_flush_reclaim"
FAMILY = "liquidation_flush_reversal"
TIMEFRAME = "1h"
REGIME = "volatile"
ONE_LINER = "After a liquidation flush, wait up to 6 bars for an up bar that closes above the prior bar's high, then buy with a stop under the flush low."
WHY = ("Same forced-selling logic as the plain flush trade, but it waits for proof that the sellers are done. "
       "The losers are late shorts who sold the flush low and now have to cover.")
RISK = "Confirmation gives away part of the bounce, and in a real downtrend the reclaim bar is only a pause."
DEFAULT_PARAMS = {"atr_n": 24, "body_atr": 2.0, "vol_n": 48, "vol_mult": 3.0, "oi_drop": 0.02, "confirm_within": 6,
                  "stop_buffer_atr": 0.5, "min_stop_atr": 1.0, "max_stop_atr": 4.0, "max_hold": 24}
PARAM_GRID = {"body_atr": [1.5, 2.0, 3.0], "oi_drop": [0.015, 0.02, 0.03], "confirm_within": [3, 6, 12]}
WARMUP_BARS = 110
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
    pos = pd.Series(np.arange(len(df), dtype=float), index=df.index)
    since = pos - pos.where(flush).ffill()                    # bars since the most recent flush (NaN before the first)
    fid = flush.cumsum()                                      # id of the most recent flush
    confirm = (since >= 1) & (since <= p["confirm_within"]) & (c > h.shift(1)) & (c > o)
    first = confirm & (confirm.astype(int).groupby(fid).cumsum() == 1)
    low_since = l.groupby(fid).cummin()                       # lowest low from the flush bar to now
    side = np.where(first, 1, 0)
    stop = np.minimum(np.maximum(low_since - p["stop_buffer_atr"] * atr, c - p["max_stop_atr"] * atr),
                      c - p["min_stop_atr"] * atr)
    return _finish(df, side, stop, max_hold=p["max_hold"])
