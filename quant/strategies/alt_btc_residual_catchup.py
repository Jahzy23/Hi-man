"""The alt has lagged its BTC-implied move: trade the gap closing.

Family : ETH / SOL lagging BTC   (needs ctx["BTCUSDT"]; returns no signals without it or on BTC itself)
Model  : hourly log return of the alt = beta x BTC return + residual, beta from a trailing `beta_n`-bar
         window (30 days), lagged one bar. z = sum of the last `resid_n` residuals divided by its
         expected size (trailing residual st.dev. x sqrt(resid_n), measured before the window).
Entry  : BTC is up over `resid_n` bars and z first drops to <= -z_entry (alt has NOT followed) -> long.
         BTC is down and z first rises to >= +z_entry -> short.
Stop   : `stop_atr` x ATR from the close.
Exit   : z crosses zero (the gap has closed), or time stop after `max_hold` bars.
This is ONE leg only, not a hedged pair: the position carries full BTC direction risk.
No look-ahead: beta and the residual scale are trailing and lagged; BTC is re-indexed onto the alt's rows.
"""
import numpy as np
import pandas as pd

NAME = "alt_btc_residual_catchup"
FAMILY = "btc_lead_lag"
TIMEFRAME = "1h"
REGIME = "ranging"
ONE_LINER = "When ETH/SOL has lagged its BTC-implied 24h move by 2+ sigma in the direction BTC moved, trade the alt to close the gap."
WHY = ("Alts are priced off BTC plus noise, so a large beta-adjusted gap after a BTC move is usually temporary flow "
       "in the alt. The loser is whoever pushed the alt away from BTC with a large one-off order.")
RISK = "Single leg, not a hedged pair: the result is mostly BTC direction. A gap can also be real news about the alt."
DEFAULT_PARAMS = {"leader": "BTCUSDT", "beta_n": 720, "resid_n": 24, "z_entry": 2.0, "atr_n": 24,
                  "stop_atr": 2.5, "max_hold": 48}
PARAM_GRID = {"resid_n": [12, 24, 48], "z_entry": [1.5, 2.0, 2.5], "stop_atr": [2.0, 2.5, 3.5]}
WARMUP_BARS = 1600
REQUIRED_COLUMNS = ('high', 'low', 'close')   # every column this module reads (own frame and, for lead-lag, the leader's)

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
    c = df["close"]
    if c.equals(b["close"]):                                  # the leader itself: nothing to catch up to
        return _finish(df, 0, np.nan, max_hold=p["max_hold"])
    atr = _atr(df, p["atr_n"])
    ra = np.log(c).diff()
    rb = np.log(b["close"]).diff()
    beta = ra.rolling(p["beta_n"]).cov(rb) / rb.rolling(p["beta_n"]).var()
    res = ra - beta.shift(1) * rb
    k = p["resid_n"]
    scale = res.rolling(p["beta_n"]).std().shift(k) * np.sqrt(k)
    z = res.rolling(k).sum() / scale.where(scale > 0)
    btc_move = np.log(b["close"] / b["close"].shift(k))
    long_c = (z <= -p["z_entry"]) & (btc_move > 0)
    short_c = (z >= p["z_entry"]) & (btc_move < 0)
    long_ = long_c & ~long_c.shift(1, fill_value=False)
    short_ = short_c & ~short_c.shift(1, fill_value=False)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    exit_ = (z * z.shift(1)) < 0
    return _finish(df, side, stop, exit_=exit_, max_hold=p["max_hold"])
