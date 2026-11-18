"""Forge's reference strategy for bot tests and the smoke run. NOT a research candidate.

Donchian breakout with an ATR stop. It exists so the bot can be exercised end to end before Radar's
modules are ready; it follows the SPEC interface exactly and uses only rows <= i (rolling windows
that end at row i, shift(+1) only), so signals(df.iloc[:k]) == signals(df).iloc[:k].
"""
import numpy as np
import pandas as pd

NAME = "_reference_breakout"
TIMEFRAME = "1h"
REGIME = "trending"
ONE_LINER = "Test fixture: close breaks the prior N-bar high/low, ATR stop, fixed R target."
DEFAULT_PARAMS = {
    "lookback": 24,        # bars in the channel (excluding the signal bar)
    "atr_n": 14,
    "atr_mult": 2.0,       # stop distance in ATRs
    "rr": 2.0,             # target distance in stop distances
    "max_hold": 48,        # bars
    "entry": "market",     # "market" -> entry NaN (taker at next open); "limit" -> pullback limit
    "pullback_atr": 0.25,  # limit = close -/+ pullback_atr * ATR
    "entry_valid": 2,
}
WARMUP_BARS = 100          # bars that must be contiguous before the bot may trade this


def signals(df: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    p = {**DEFAULT_PARAMS, **(p or {})}
    n = int(p["lookback"])
    high, low, close = df["high"], df["low"], df["close"]
    upper = high.rolling(n).max().shift(1)          # channel of the n bars BEFORE the signal bar
    lower = low.rolling(n).min().shift(1)
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(int(p["atr_n"])).mean()
    ok = atr.notna() & upper.notna() & (atr > 0)
    side = pd.Series(0, index=df.index, dtype="int64")
    side[ok & (close > upper)] = 1
    side[ok & (close < lower)] = -1
    active = side != 0

    if p["entry"] == "market":
        entry = pd.Series(np.nan, index=df.index)
        ref = close
    else:
        entry = (close - side * float(p["pullback_atr"]) * atr).where(active)
        ref = entry
    dist = float(p["atr_mult"]) * atr
    stop = (ref - side * dist).where(active)
    target = (ref + side * float(p["rr"]) * dist).where(active)

    mid = (upper + lower) / 2.0
    above = close > mid
    crossed = (above != above.shift(1)) & mid.notna() & mid.shift(1).notna()   # close crossed the channel midpoint

    out = pd.DataFrame(index=df.index)
    out["side"] = side
    out["entry"] = entry
    out["entry_valid"] = int(p["entry_valid"])
    out["stop"] = stop
    out["target"] = target
    out["max_hold"] = float(p["max_hold"])
    out["exit"] = crossed.astype(bool)
    return out
