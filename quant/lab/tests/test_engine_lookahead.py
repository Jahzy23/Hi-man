"""The engine must never let a strategy trade on the bar that produced its signal."""
import numpy as np
import pandas as pd
import pytest

from lab.engine import ExecConfig, run_backtest
from .helpers import CFG, bars, run, sigs

approx = pytest.approx


def _same_bar_peeker(df, hold=1, stop_dist=50.0):
    """side = direction of the bar that just closed (legal: known at the close)."""
    side = np.sign(df["close"] - df["open"]).astype(int)
    return pd.DataFrame({"side": side, "entry": np.nan, "entry_valid": 1, "stop": df["close"] - side * stop_dist,
                         "target": np.nan, "max_hold": hold, "exit": False}, index=df.index)


def test_strategy_that_peeks_at_the_same_bar_close_must_not_profit():
    # up bar 100->110, down bar 110->100, forever. Knowing a bar's direction is worth +10 per unit
    # ONLY if you can still trade at that bar's open. Executed one bar later it loses 10 per unit.
    up, down = (100, 110, 100, 110), (110, 110, 100, 100)
    df = bars([up, down] * 10)
    res = run(df, _same_bar_peeker(df), cfg=ExecConfig(slippage=0.0, maker_fee=0.0, taker_fee=0.0))
    t = res.trades
    assert len(t) == 19                                   # one per bar from bar 1; the last one is cut at the end
    first = t.iloc[0]
    assert first.side == 1 and first.entry_ref == 110 and first.exit_ref == 100      # bought the NEXT open
    assert first.qty == approx(0.005 * 1000 / 50) and first.gross == approx(-1.0)
    timed = t[t.exit_reason == "time"]
    assert len(timed) == 18 and (timed.gross < 0).all()   # every single trade loses
    assert res.net_return < -0.015                        # ~ -0.1% of equity per trade, 18 times
    # with costs it is worse still
    assert run(df, _same_bar_peeker(df)).net_return < res.net_return


def test_same_bar_peeker_does_not_profit_on_a_random_walk():
    rng = np.random.default_rng(11)
    n = 6000
    r = rng.normal(0, 0.004, n)
    c = 100 * np.exp(np.cumsum(r))
    o = np.concatenate(([100.0], c[:-1]))
    df = bars(list(zip(o, np.maximum(o, c) * 1.0005, np.minimum(o, c) * 0.9995, c)))
    sg = _same_bar_peeker(df, hold=1, stop_dist=0.0)
    sg["stop"] = df["close"] * (1 - sg["side"] * 0.05)
    free = run_backtest(df, sg, "BTCUSDT", cfg=ExecConfig(slippage=0.0, maker_fee=0.0, taker_fee=0.0))
    paid = run_backtest(df, sg, "BTCUSDT")
    cheat_pnl_per_unit = np.abs(c - o).sum()              # what filling at the signal bar's open would earn
    assert cheat_pnl_per_unit / c.mean() > 10             # >1000% of price: the cheat is enormous...
    assert abs(free.net_return) < 0.05                    # ...and the engine gives nothing of it away
    assert paid.net_return < free.net_return and paid.net_return < 0


def test_signal_values_from_row_i_are_not_used_before_bar_i_plus_1():
    # identical signal frames, different prices on the signal bar only -> identical fills
    a = bars([(100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 100, 100, 100)])
    b = bars([(100, 180, 20, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 100, 100, 100)])
    spec = {0: dict(side=1, entry=99.5, stop=95, target=100.5, entry_valid=2)}
    ta, tb = run(a, sigs(a, spec)).trades, run(b, sigs(b, spec)).trades
    assert len(ta) == len(tb) == 1
    pd.testing.assert_frame_equal(ta, tb)                 # the wild signal bar changed nothing


def _lattice(n_bars, steps, seed, p0=1_000_000):
    """+-1 tick random walk: an exact martingale that cannot jump over a price level."""
    rng = np.random.default_rng(seed)
    inc = rng.integers(0, 2, size=n_bars * steps, dtype=np.int8) * 2 - 1
    p = np.concatenate(([p0], p0 + np.cumsum(inc, dtype=np.int64))).astype(float)
    seg = p[:-1].reshape(n_bars, steps)
    c = p[steps::steps]
    idx = pd.date_range("2000-01-01", periods=n_bars, freq="1h", tz="UTC")
    return pd.DataFrame({"open": seg[:, 0], "high": np.maximum(seg.max(axis=1), c),
                         "low": np.minimum(seg.min(axis=1), c), "close": c, "funding_paid": 0.0}, index=idx)


def _random_entries(df, seed, stop_ticks, hold, target_ticks=None, limit_ticks=None):
    n = len(df)
    u1, u2 = np.random.default_rng([seed, 1]).random(n), np.random.default_rng([seed, 2]).random(n)
    side = np.where(u1 < 0.5, np.where(u2 < 0.5, 1, -1), 0)
    sg = pd.DataFrame({"side": side, "entry": np.nan, "entry_valid": 3, "stop": df["close"] - side * stop_ticks,
                       "target": np.nan, "max_hold": hold, "exit": False}, index=df.index)
    if target_ticks:
        sg["target"] = df["close"] + side * target_ticks
    if limit_ticks is not None:
        sg["entry"] = df["close"] - side * limit_ticks
    return sg


def _gross_R(**kw):
    free = ExecConfig(slippage=0.0, maker_fee=0.0, taker_fee=0.0)
    out = []
    for seed in range(6):
        df = _lattice(40_000, 50, seed)
        t = run_backtest(df, _random_entries(df, seed, **kw), "X", cfg=free).trades
        out.append((t.gross / t.risk_usd).to_numpy())
    r = np.concatenate(out)
    return r.mean(), r.std(ddof=1) / np.sqrt(len(r)), len(r)


def test_random_entries_on_a_martingale_have_zero_gross_expectancy():
    """Market entry + stop + time exit on a driftless walk: any bias in fill timing or stop
    handling would show up as a non-zero mean. Exact theory says 0."""
    mean, se, n = _gross_R(stop_ticks=30, hold=24)
    assert n > 10_000
    assert abs(mean) < 3 * se and abs(mean) < 0.03


def test_conservative_limit_and_target_rules_never_create_edge_on_a_martingale():
    """Trade-through fills, stop-first and the fill-bar rule may only cost money."""
    for kw in (dict(stop_ticks=30, hold=24, target_ticks=30),
               dict(stop_ticks=30, hold=24, limit_ticks=5),
               dict(stop_ticks=30, hold=24, target_ticks=30, limit_ticks=5)):
        mean, se, n = _gross_R(**kw)
        assert n > 5_000
        assert mean < 2 * se, (kw, mean, se)
