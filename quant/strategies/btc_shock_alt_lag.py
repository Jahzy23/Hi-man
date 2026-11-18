"""BTC prints a shock bar and the alt under-reacts: take the alt in BTC's direction.

Family : ETH / SOL lagging BTC   (needs ctx["BTCUSDT"]; returns no signals without it or on BTC itself)
Shock  : BTC's hourly body (close - open) is >= shock_atr x BTC's ATR of the bars before it.
Lag    : the alt's body on the same bar, in units of the alt's own prior ATR, is less than `lag_ratio`
         of BTC's (it moved less than half as far, or the other way).
Entry  : take the alt in BTC's direction at next open.
Stop   : `stop_atr` x ATR from the close.   Exit: time stop after `max_hold` bars (a lag that has not
         closed within a few hours is not a lag).
No look-ahead: both ATRs are taken from before the shock bar; BTC is re-indexed onto the alt's rows.
"""
import numpy as np
import pandas as pd

NAME = "btc_shock_alt_lag"
FAMILY = "btc_lead_lag"
TIMEFRAME = "1h"
REGIME = "volatile"
ONE_LINER = "BTC prints a 2+ ATR hourly bar and ETH/SOL moved less than half as much in ATR terms: take the alt in BTC's direction for 3 bars."
WHY = ("Under-reaction: the alt has not finished repricing a BTC shock by the end of the hour. The losers are "
       "liquidity providers in the alt who have not yet moved their quotes.")
RISK = "Rare, and alts normally over-react to BTC shocks; an alt that did not move may be pinned by a large resting order."
DEFAULT_PARAMS = {"leader": "BTCUSDT", "atr_n": 24, "shock_atr": 2.0, "lag_ratio": 0.5, "stop_atr": 1.5, "max_hold": 3}
PARAM_GRID = {"shock_atr": [1.5, 2.0, 3.0], "lag_ratio": [0.33, 0.5, 0.67], "max_hold": [2, 3, 6]}
WARMUP_BARS = 60
REQUIRED_COLUMNS = ('open', 'high', 'low', 'close')   # every column this module reads (own frame and, for lead-lag, the leader's)

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
    if not ctx or p["leader"] not in ctx:
        return _finish(df, 0, np.nan, max_hold=p["max_hold"])
    b = ctx[p["leader"]].reindex(df.index)
    o, c = df["open"], df["close"]
    atr = _atr(df, p["atr_n"])
    b_move = (b["close"] - b["open"]) / _atr(b, p["atr_n"]).shift(1)
    a_move = (c - o) / atr.shift(1)
    long_ = (b_move >= p["shock_atr"]) & (a_move < p["lag_ratio"] * b_move)
    short_ = (b_move <= -p["shock_atr"]) & (a_move > p["lag_ratio"] * b_move)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    return _finish(df, side, stop, max_hold=p["max_hold"])
