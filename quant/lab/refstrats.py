"""Reference "strategies" used to prove the engine and the validation tools.

They follow the SPEC strategy interface (NAME, TIMEFRAME, REGIME, ONE_LINER, DEFAULT_PARAMS,
signals) plus PARAM_GRID, so everything in lab/ can be exercised before Radar's modules exist.
None of them is a trading idea. Two of them (LEAKY_*) cheat on purpose: the look-ahead audit
must catch them.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd


def blank(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({"side": 0, "entry": np.nan, "entry_valid": 1, "stop": np.nan,
                         "target": np.nan, "max_hold": np.nan, "exit": False}, index=df.index)


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(int(n)).mean()


def _mk(name, tf, regime, one_liner, defaults, fn, grid=None):
    ns = SimpleNamespace(NAME=name, TIMEFRAME=tf, REGIME=regime, ONE_LINER=one_liner,
                         DEFAULT_PARAMS=defaults, signals=fn, __name__=name)
    if grid is not None:
        ns.PARAM_GRID = grid
    return ns


# -- always long ------------------------------------------------------------------------
def _always_long(df, p, ctx=None):
    s = blank(df)
    s["side"] = 1
    s["stop"] = df["close"] * (1.0 - p["stop_frac"])
    if p.get("max_hold"):
        s["max_hold"] = int(p["max_hold"])
    return s


ALWAYS_LONG = _mk("ref_always_long", "1h", "trending", "long on every bar, far stop",
                  {"stop_frac": 0.9, "max_hold": 0}, _always_long)


# -- random entries ---------------------------------------------------------------------
def _random_entry(df, p, ctx=None):
    n = len(df)
    u1 = np.random.default_rng([int(p["seed"]), 1]).random(n)      # prefix-stable streams
    u2 = np.random.default_rng([int(p["seed"]), 2]).random(n)
    a = atr(df, p["atr_n"])
    side = np.where(u1 < p["p_entry"], np.where(u2 < 0.5, 1, -1), 0)
    side = np.where(a.notna().to_numpy(), side, 0)
    s = blank(df)
    s["side"] = side
    s["stop"] = df["close"] - side * p["stop_atr"] * a
    if p.get("tgt_atr"):
        s["target"] = df["close"] + side * p["tgt_atr"] * a
    if p.get("limit_atr") is not None:
        s["entry"] = df["close"] - side * p["limit_atr"] * a
        s["entry_valid"] = int(p.get("entry_valid", 1))
    s["max_hold"] = int(p["max_hold"])
    return s


RANDOM = _mk("ref_random", "1h", "ranging", "coin-flip entries, ATR stop, time exit",
             {"seed": 0, "p_entry": 0.03, "atr_n": 24, "stop_atr": 3.0, "max_hold": 24,
              "tgt_atr": None, "limit_atr": None, "entry_valid": 1}, _random_entry)


# -- same-bar information, used honestly (decided at the close) -----------------------------
def _peek_same_bar(df, p, ctx=None):
    s = blank(df)
    side = np.sign(df["close"] - df["open"]).fillna(0).astype(int)
    a = atr(df, p["atr_n"])
    side = side.where(a.notna(), 0)
    s["side"] = side
    s["stop"] = df["close"] - side * p["stop_atr"] * a
    s["max_hold"] = 1
    return s


PEEK = _mk("ref_peek_same_bar", "1h", "volatile",
           "trade in the direction of the bar that just closed; only profits if filled inside that bar",
           {"atr_n": 24, "stop_atr": 5.0}, _peek_same_bar)


# -- deliberate cheats ------------------------------------------------------------------
def _leaky_future(df, p, ctx=None):
    s = blank(df)
    nxt = df["close"].shift(-1)                                  # LOOK-AHEAD on purpose
    side = np.sign(nxt - df["close"]).fillna(0).astype(int)
    a = atr(df, 24)
    side = side.where(a.notna(), 0)
    s["side"] = side
    s["stop"] = df["close"] - side * 5.0 * a
    s["max_hold"] = 1
    return s


LEAKY_FUTURE = _mk("ref_leaky_future", "1h", "volatile", "uses close.shift(-1)", {}, _leaky_future)


def _leaky_norm(df, p, ctx=None):
    s = blank(df)
    z = (df["close"] - df["close"].mean()) / df["close"].std()   # full-sample normalisation on purpose
    a = atr(df, 24)
    side = np.where(z < -p["z"], 1, np.where(z > p["z"], -1, 0))
    side = np.where(a.notna().to_numpy(), side, 0)
    s["side"] = side
    s["stop"] = df["close"] - side * 3.0 * a
    s["max_hold"] = 48
    return s


LEAKY_NORM = _mk("ref_leaky_norm", "1h", "ranging", "full-sample z-score", {"z": 1.0}, _leaky_norm)


# -- plain SMA cross with a grid (for walk-forward / neighbourhood plumbing) ---------------
def _sma_cross(df, p, ctx=None):
    s = blank(df)
    fast = df["close"].rolling(int(p["fast"])).mean()
    slow = df["close"].rolling(int(p["slow"])).mean()
    a = atr(df, 24)
    up = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    dn = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    side = np.where(up, 1, np.where(dn, -1, 0))
    side = np.where((a.notna() & slow.shift(1).notna()).to_numpy(), side, 0)
    s["side"] = side
    s["stop"] = df["close"] - side * p["stop_atr"] * a
    s["exit"] = (up | dn).to_numpy()
    return s


SMA_CROSS = _mk("ref_sma_cross", "1h", "trending", "SMA cross, stop-and-reverse, ATR stop",
                {"fast": 24, "slow": 168, "stop_atr": 3.0}, _sma_cross,
                grid={"fast": [12, 24, 48], "slow": [96, 168, 240], "stop_atr": [2.0, 3.0, 4.0]})

ALL = [ALWAYS_LONG, RANDOM, PEEK, LEAKY_FUTURE, LEAKY_NORM, SMA_CROSS]
