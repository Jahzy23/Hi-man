"""Two-bar RSI pullback in the direction of the 200-bar trend.

Family : trend pullback to support
RSI    : simple (Cutler) RSI over `rsi_n` bars = up moves / (up moves + down moves). With rsi_n = 2 a
         reading below 10 means the last two closes were almost entirely down.
Entry  : close above SMA(trend_n) and RSI first drops below `lo` -> long at next open.
         Close below the SMA and RSI first rises above 100 - `lo` -> short.
Stop   : `stop_atr` x ATR from the close.
Exit   : RSI crosses 50 (either way: after an entry at an extreme the first cross is always the
         right one), or time stop after `max_hold` bars.
Timeframe is 4h on purpose: on 1h the average move is too small next to taker fees.
No look-ahead: RSI and SMA end at the signal bar.
"""
import numpy as np
import pandas as pd

NAME = "rsi2_trend_pullback"
FAMILY = "trend_pullback"
TIMEFRAME = "4h"
REGIME = "trending"
ONE_LINER = "Above the 200-bar average buy a 2-bar RSI below 10 (two hard down closes), mirror below it; leave when RSI crosses 50."
WHY = ("Short, sharp counter-trend drops inside an established trend are mostly profit-taking and stop-runs, not "
       "new information. The losers are short-term sellers who hit bids at the low of a dip in a rising market.")
RISK = "Small average win: fees and one trend-ending drop eat many wins. Very cost-sensitive."
DEFAULT_PARAMS = {"rsi_n": 2, "lo": 10.0, "trend_n": 200, "atr_n": 14, "stop_atr": 2.5, "max_hold": 12}
PARAM_GRID = {"rsi_n": [2, 3], "lo": [5.0, 10.0, 15.0], "trend_n": [100, 200]}
WARMUP_BARS = 230
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
    hi = 100.0 - p["lo"]                                    # short threshold mirrors the long one
    c = df["close"]
    atr = _atr(df, p["atr_n"])
    d = c.diff()
    gain = d.clip(lower=0.0).rolling(p["rsi_n"]).sum()
    loss = (-d).clip(lower=0.0).rolling(p["rsi_n"]).sum()
    rsi = 100.0 * gain / (gain + loss)                      # NaN when price did not move at all
    trend = c.rolling(p["trend_n"]).mean()
    dip = (c > trend) & (rsi < p["lo"])
    pop = (c < trend) & (rsi > hi)
    long_ = dip & ~dip.shift(1, fill_value=False)
    short_ = pop & ~pop.shift(1, fill_value=False)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    exit_ = ((rsi - 50.0) * (rsi.shift(1) - 50.0)) < 0
    return _finish(df, side, stop, exit_=exit_, max_hold=p["max_hold"])
