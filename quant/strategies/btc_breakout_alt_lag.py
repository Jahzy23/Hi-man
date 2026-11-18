"""BTC breaks its range, the alt has not yet: trade the alt's catch-up.

Family : ETH / SOL lagging BTC   (needs ctx["BTCUSDT"]; returns no signals without it or on BTC itself)
Leader : BTC's first close outside its previous `n`-bar range.
Entry  : on that bar, if the alt is still at least `room_atr` ATR inside its OWN `n`-bar range edge
         -> take the alt in BTC's direction at next open.
Target : the alt's own range edge (the catch-up).   Stop: `stop_atr` x ATR from the close.
Exit   : target, stop, or time stop after `max_hold` bars (if it has not followed in 6 hours the idea is wrong).
No look-ahead: BTC is re-indexed onto the alt's own rows, so only BTC bars up to the signal bar are used;
all levels are shifted one bar.
"""
import numpy as np
import pandas as pd

NAME = "btc_breakout_alt_lag"
FAMILY = "btc_lead_lag"
TIMEFRAME = "1h"
REGIME = "trending"
ONE_LINER = "BTC closes outside its 48-hour range while ETH/SOL is still inside its own: take the alt in BTC's direction to its range edge."
WHY = ("BTC is the price-discovery leader and alt order books reprice after it. The losers are alt market makers "
       "and passive orders still quoting stale levels.")
RISK = "At 1h resolution most of the lag is already arbitraged; what is left may be the alt being weak for a reason."
DEFAULT_PARAMS = {"leader": "BTCUSDT", "n": 48, "atr_n": 24, "room_atr": 0.5, "stop_atr": 1.5, "max_hold": 6}
PARAM_GRID = {"n": [24, 48, 72], "room_atr": [0.5, 1.0], "max_hold": [3, 6, 12]}
WARMUP_BARS = 110
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
    b = ctx[p["leader"]].reindex(df.index)                    # leader on the alt's rows: nothing later than row i
    c, h, l = df["close"], df["high"], df["low"]
    n = p["n"]
    atr = _atr(df, p["atr_n"])
    b_up = b["close"] > b["high"].rolling(n).max().shift(1)
    b_dn = b["close"] < b["low"].rolling(n).min().shift(1)
    b_up = b_up & ~b_up.shift(1, fill_value=False)
    b_dn = b_dn & ~b_dn.shift(1, fill_value=False)
    hh = h.rolling(n).max().shift(1)
    ll = l.rolling(n).min().shift(1)
    long_ = b_up & (c <= hh - p["room_atr"] * atr)
    short_ = b_dn & (c >= ll + p["room_atr"] * atr)
    side = np.where(long_, 1, np.where(short_, -1, 0))
    stop = np.where(long_, c - p["stop_atr"] * atr, c + p["stop_atr"] * atr)
    target = np.where(long_, hh, ll)
    return _finish(df, side, stop, target=target, max_hold=p["max_hold"])
