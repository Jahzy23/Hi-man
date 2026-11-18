"""Sanity checks on the real data set (the numbers quoted in the phase-1 report come from here).

  1. always-long must reproduce the buy-and-hold benchmark, minus its exit cost;
  2. random entries must lose roughly their costs on average;
  3. information from the bar that just closed must not be monetisable.
"""
import time

import numpy as np
import pandas as pd
import pytest

from lab import metrics as M
from lab import refstrats as R
from lab.engine import ExecConfig, get_frame, run_backtest, window_bounds
from strategies.data import BACKTEST_END, BACKTEST_START, SYMBOLS

approx = pytest.approx
TAKER = 0.0005


@pytest.mark.parametrize("sym", SYMBOLS)
def test_data_contract(sym):
    for tf, step in (("1h", pd.Timedelta(hours=1)), ("4h", pd.Timedelta(hours=4))):
        df = get_frame(sym, tf)
        w = df.loc[BACKTEST_START:BACKTEST_END]
        assert str(df.index.tz) == "UTC" and df.index.is_monotonic_increasing and df.index.is_unique
        assert (np.diff(w.index.values) == np.timedelta64(step)).all()               # no missing bars
        assert w[["open", "high", "low", "close", "funding_paid"]].notna().all().all()
        assert (w["high"] >= w[["open", "close"]].max(axis=1)).all() and (w["low"] <= w[["open", "close"]].min(axis=1)).all()
        assert (w["low"] > 0).all()
        assert df.loc[:BACKTEST_START].index[0] <= pd.Timestamp("2022-08-01", tz="UTC")   # warm-up is there
    w = get_frame(sym, "1h").loc[BACKTEST_START:BACKTEST_END]
    assert len(w) == 35056 and w.index[-1] == pd.Timestamp("2026-10-31 15:00", tz="UTC")
    assert 4300 < (w["funding_paid"] != 0).sum() < 4600          # ~3 settlements a day for 4 years
    assert w["funding_paid"].abs().max() < 0.03


@pytest.mark.parametrize("sym", SYMBOLS)
def test_always_long_matches_buy_and_hold_minus_costs(sym):
    df = get_frame(sym, "1h")
    sig = R.ALWAYS_LONG.signals(df, {"stop_frac": 0.9, "max_hold": 0})
    cfg = ExecConfig(risk_frac=0.9)                      # 90% "risk" on a 90% stop = 1x notional
    res = run_backtest(df, sig, sym, BACKTEST_START, BACKTEST_END, cfg)
    bh = M.buy_and_hold(df, sym, BACKTEST_START, BACKTEST_END)
    assert len(res.trades) == 1
    t = res.trades.iloc[0]
    assert t.exit_reason == "end_of_data" and t.leverage == approx(1.0, abs=1e-3)
    assert res.in_position.all()

    # independent arithmetic from the raw frame
    i0, i1 = window_bounds(df.index, BACKTEST_START, BACKTEST_END)
    s = cfg.slip_for(sym)
    o0, c_last = df["open"].iloc[i0], df["close"].iloc[i1]
    qty = 0.9 * 1000 / (o0 - 0.1 * df["close"].iloc[i0 - 1])
    funding = qty * (df["close"].iloc[i0:i1 + 1] * df["funding_paid"].iloc[i0:i1 + 1]).sum()
    fees = qty * (o0 * (1 + s) + c_last * (1 - s)) * TAKER
    final = 1000 + qty * (c_last * (1 - s) - o0 * (1 + s)) - fees - funding
    assert res.final_equity == approx(final, rel=1e-10)
    assert t.funding == approx(funding, rel=1e-10) and t.fees == approx(fees, rel=1e-10)

    # versus the benchmark: same thing except that the benchmark never pays to get out
    exit_cost = t.qty * c_last * s + t.qty * c_last * (1 - s) * TAKER
    scale = t.qty / bh.stats["qty"]                      # 1.0 up to the open-vs-previous-close tick
    assert scale == approx(1.0, abs=1e-3)
    assert res.final_equity - 1000 == approx(scale * (bh.equity.iloc[-1] - 1000) - exit_cost, rel=1e-9)
    assert abs(res.net_return - (bh.stats["net_return"] - exit_cost / 1000)) < 2e-3
    assert res.net_return < bh.stats["net_return"]


def test_daily_reentered_always_long_compounds_exactly_like_first_principles():
    sym = "BTCUSDT"
    df = get_frame(sym, "1h")
    sig = R.ALWAYS_LONG.signals(df, {"stop_frac": 0.9, "max_hold": 24})
    cfg = ExecConfig(risk_frac=0.9)
    res = run_backtest(df, sig, sym, BACKTEST_START, BACKTEST_END, cfg)
    i0, i1 = window_bounds(df.index, BACKTEST_START, BACKTEST_END)
    O, C, F = df["open"].to_numpy(), df["close"].to_numpy(), df["funding_paid"].to_numpy()
    s, eq, b, n = cfg.slip_for(sym), 1000.0, i0, 0
    while b <= i1:
        last = min(b + 23, i1)
        qty = 0.9 * eq / (O[b] - 0.1 * C[b - 1])
        exit_ref = O[last + 1] if last + 1 <= i1 else C[i1]              # next open, or the final close
        eq += qty * (exit_ref * (1 - s) - O[b] * (1 + s)) - qty * (O[b] * (1 + s) + exit_ref * (1 - s)) * TAKER \
            - qty * float((C[b:last + 1] * F[b:last + 1]).sum())
        b, n = last + 1, n + 1
    assert len(res.trades) == n == 1461
    assert res.final_equity == approx(eq, rel=1e-9)
    bh = M.buy_and_hold(df, sym, BACKTEST_START, BACKTEST_END).stats["net_return"]
    assert res.net_return < bh - 1.0                     # 1,461 round trips of ~0.12% each are not free


def _random_runs(extra, seeds):
    rows, per_run = [], []
    for sym in SYMBOLS:
        df = get_frame(sym, "1h")
        for seed in seeds:
            sig = R.RANDOM.signals(df, {**R.RANDOM.DEFAULT_PARAMS, "seed": seed, **extra})
            res = run_backtest(df, sig, sym, BACKTEST_START, BACKTEST_END)
            t = res.trades
            rows.append(pd.DataFrame({"gross_R": t.gross / t.risk_usd,
                                      "cost_R": (t.fees + t.slippage + t.funding) / t.risk_usd, "net_R": t.R}))
            per_run.append(res.net_return)
    return pd.concat(rows, ignore_index=True), np.array(per_run)


def test_random_entries_lose_roughly_their_costs():
    """Coin-flip entries with a symmetric 3-ATR stop and target: no edge by construction."""
    a, runs = _random_runs({"tgt_atr": 3.0}, seeds=range(10))
    n = len(a)
    gross, cost, net = a["gross_R"].mean(), a["cost_R"].mean(), a["net_R"].mean()
    se = a["gross_R"].std(ddof=1) / np.sqrt(n)
    assert n > 15_000
    assert net == approx(gross - cost)
    assert 0.03 < cost < 0.08                             # ~0.05R of fees + slippage (+ a little funding)
    assert abs(gross) < 4 * se and abs(gross) < 0.3 * cost      # nothing before costs...
    assert -1.3 * cost < net < -0.7 * cost                # ...so it loses what it pays
    assert (runs < 0).mean() > 0.85 and runs.mean() < -0.05


def test_random_entries_without_target_still_lose_after_costs():
    """Stop + time exit only ("cut losers, let winners run"): the sample has drift and serial
    dependence, so gross is slightly positive, but far less than the costs."""
    a, runs = _random_runs({}, seeds=range(10))
    gross, cost, net = a["gross_R"].mean(), a["cost_R"].mean(), a["net_R"].mean()
    assert 0 < gross < cost and net < 0 and runs.mean() < 0 and (runs < 0).mean() > 0.7


def _replay_market_trades(df, sig, i0, i1):
    """Second, deliberately naive implementation of the SPEC rules for marketable entries with
    stop / target / max_hold: one trade at a time, bar by bar. Returns (signal_row, entry_bar,
    exit_bar, exit_ref, reason) tuples."""
    O, H, L, C = (df[c].to_numpy() for c in ("open", "high", "low", "close"))
    side, stop, tgt, hold = (sig[c].to_numpy() for c in ("side", "stop", "target", "max_hold"))
    out, s = [], i0 - 1
    while s < i1:
        if side[s] == 0:
            s += 1
            continue
        d, st, tg, e = int(side[s]), stop[s], tgt[s], s + 1
        has_tg = tg == tg
        if d * (O[e] - st) <= 0 or (has_tg and d * (tg - O[e]) <= 0):      # stop / target on the wrong side
            s += 1
            continue
        n = int(hold[s])
        j, done = e, None
        while done is None:
            first = j == e
            if not first and d * (O[j] - st) <= 0:
                done = (j, O[j], "stop_gap", j - 1 if j - e >= n else j)   # next signal row allowed
            elif j - e >= n:                                              # scheduled time exit at this open
                done = (j, tg, "target", j - 1) if has_tg and d * (O[j] - tg) > 0 else (j, O[j], "time", j - 1)
            elif (L[j] <= st) if d > 0 else (H[j] >= st):
                done = (j, st, "stop", j)
            elif has_tg and ((H[j] > tg) if d > 0 else (L[j] < tg)):
                done = (j, tg, "target", j)
            elif j == i1:
                done = (j, C[j], "end_of_data", i1)
            else:
                j += 1
        out.append((s, e, done[0], done[1], done[2]))
        s = done[3]
    return out


@pytest.mark.parametrize("sym,extra", [("BTCUSDT", {}), ("ETHUSDT", {"tgt_atr": 2.0}), ("SOLUSDT", {"tgt_atr": 3.0, "max_hold": 6}),
                                       ("SOLUSDT", {"stop_atr": 0.7, "p_entry": 0.5})])
def test_engine_agrees_trade_by_trade_with_a_naive_replay(sym, extra):
    df = get_frame(sym, "1h")
    sig = R.RANDOM.signals(df, {**R.RANDOM.DEFAULT_PARAMS, "seed": 123, "p_entry": 0.2, **extra})
    res = run_backtest(df, sig, sym, BACKTEST_START, BACKTEST_END)
    i0, i1 = window_bounds(df.index, BACKTEST_START, BACKTEST_END)
    ref = _replay_market_trades(df, sig, i0, i1)
    t = res.trades
    assert len(t) == len(ref) > 1500
    got = list(zip(df.index.get_indexer(t["signal_time"]).tolist(), t["entry_bar"].tolist(), t["exit_bar"].tolist(),
                   t["exit_ref"].tolist(), t["exit_reason"].tolist()))
    assert got == [(a, b, c, float(d), e) for a, b, c, d, e in ref]
    assert {"stop", "time"} <= set(t["exit_reason"])
    np.testing.assert_allclose(t["gross"], t["side"] * t["qty"] * (t["exit_ref"] - df["open"].to_numpy()[t["entry_bar"]]),
                               rtol=1e-12, atol=1e-12)


@pytest.mark.parametrize("sym", SYMBOLS)
def test_same_bar_information_is_not_monetisable_on_real_data(sym):
    df = get_frame(sym, "1h")
    sig = R.PEEK.signals(df, R.PEEK.DEFAULT_PARAMS)
    free = run_backtest(df, sig, sym, BACKTEST_START, BACKTEST_END, ExecConfig(slippage=0.0, maker_fee=0.0, taker_fee=0.0))
    paid = run_backtest(df, sig, sym, BACKTEST_START, BACKTEST_END)
    w = df.loc[BACKTEST_START:BACKTEST_END]
    cheat_per_unit = float((w["close"] - w["open"]).abs().sum() / w["close"].mean())     # same-bar fill "edge"
    assert cheat_per_unit > 50                            # filling inside the signal bar would be worth >5000%
    assert paid.net_return < -0.5 and paid.net_return < free.net_return
    gross_R = (free.trades.gross / free.trades.risk_usd).mean()
    assert abs(gross_R) < 0.02                            # per trade, before costs: nothing


def test_one_backtest_is_fast_enough_for_hundreds_of_runs():
    df = get_frame("BTCUSDT", "1h")
    sig = R.RANDOM.signals(df, {**R.RANDOM.DEFAULT_PARAMS, "tgt_atr": 3.0})
    run_backtest(df, sig, "BTCUSDT", BACKTEST_START, BACKTEST_END)
    t0 = time.perf_counter()
    for _ in range(5):
        res = run_backtest(df, sig, "BTCUSDT", BACKTEST_START, BACKTEST_END)
    per_run = (time.perf_counter() - t0) / 5
    assert len(res.equity) == 35056 and per_run < 0.5
