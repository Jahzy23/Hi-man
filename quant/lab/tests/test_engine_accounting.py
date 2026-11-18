"""Money: fees, slippage, funding sign, sizing and cap, compounding, stress multipliers, equity curve."""
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from lab.engine import ExecConfig, run_backtest
from .helpers import CFG, FLAT, MAKER, SLIP, STOP_SLIP, TAKER, bars, one, run, sigs

approx = pytest.approx
ROWS = [FLAT, (100, 101, 99, 100), (100, 102, 99, 101), (101, 102, 100, 102), (102, 102, 102, 102)]


def test_funding_longs_pay_positive_and_only_for_bars_held():
    # huge funding on the signal bar (0) and on the exit bar (3, exit at its open) must NOT be charged
    df = bars(ROWS, funding=[0.01, 0.0001, -0.0003, 0.02, 0.0])
    res = run(df, sigs(df, {0: dict(side=1, stop=90, max_hold=2)}))
    t = one(res)
    qty = 5 / 10
    funding = qty * (100 * 0.0001 + 101 * -0.0003)          # notional at each bar close x rate
    assert t.funding == approx(funding) and funding < 0     # net receiver here
    entry_fill, exit_fill = 100 * (1 + SLIP), 101 * (1 - SLIP)
    fees = qty * (entry_fill + exit_fill) * TAKER
    net = qty * (exit_fill - entry_fill) - fees - funding
    assert t.net == approx(net) and t.net == approx(t.gross - t.slippage - t.fees - t.funding)
    # equity is hit by funding on the bar it settles
    e1 = 1000 - qty * entry_fill * TAKER + qty * (100 - entry_fill) - qty * 100 * 0.0001
    e2 = 1000 - qty * entry_fill * TAKER + qty * (101 - entry_fill) - funding
    assert res.equity.tolist() == approx([1000, e1, e2, 1000 + net, 1000 + net])


def test_funding_sign_for_shorts_is_the_opposite():
    df = bars(ROWS, funding=[0.01, 0.0001, -0.0003, 0.02, 0.0])
    t = one(run(df, sigs(df, {0: dict(side=-1, stop=110, max_hold=2)})))
    assert t.funding == approx(-0.5 * (100 * 0.0001 + 101 * -0.0003)) and t.funding > 0


def test_positive_funding_costs_longs_and_pays_shorts():
    df = bars(ROWS, funding=[0, 0.001, 0, 0, 0])
    base = bars(ROWS)
    long_f = one(run(df, sigs(df, {0: dict(side=1, stop=90, max_hold=2)})))
    long_0 = one(run(base, sigs(base, {0: dict(side=1, stop=90, max_hold=2)})))
    short_f = one(run(df, sigs(df, {0: dict(side=-1, stop=110, max_hold=2)})))
    short_0 = one(run(base, sigs(base, {0: dict(side=-1, stop=110, max_hold=2)})))
    assert long_f.funding == approx(0.5 * 100 * 0.001) and long_f.net == approx(long_0.net - 0.05)
    assert short_f.funding == approx(-0.05) and short_f.net == approx(short_0.net + 0.05)


@pytest.mark.parametrize("side,stop,bar,rate,expected", [
    (1, 95, (100, 101, 94, 96), +0.001, 1 * 95 * 0.001),     # long stopped in a bar that charges longs: pay
    (1, 95, (100, 101, 94, 96), -0.001, 0.0),                # ...that would pay longs: NOT credited
    (-1, 105, (100, 106, 99, 104), +0.001, 0.0),             # short, would receive: NOT credited
    (-1, 105, (100, 106, 99, 104), -0.001, 1 * 105 * 0.001), # short, has to pay: pay
])
def test_funding_on_intrabar_exit_bar_is_charged_but_never_credited(side, stop, bar, rate, expected):   # R10
    df = bars([FLAT, (100, 101, 99, 100), bar, FLAT], funding=[0, 0, rate, 0])
    t = one(run(df, sigs(df, {0: dict(side=side, stop=stop)})))
    assert t.exit_reason == "stop" and t.funding == approx(expected)
    full = one(run(df, sigs(df, {0: dict(side=side, stop=stop)}), cfg=replace(CFG, funding_partial_bar="full")))
    assert full.funding == approx(side * stop * rate)
    none = one(run(df, sigs(df, {0: dict(side=side, stop=stop)}), cfg=replace(CFG, funding_partial_bar="none")))
    assert none.funding == 0.0


def test_position_size_is_half_percent_risk_over_stop_distance():
    df = bars(ROWS)
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95)})))
    assert t.qty == approx(1.0) and t.risk_usd == approx(5.0) and t.notional == approx(100.0)
    assert t.leverage == approx(0.1) and t.equity_at_entry == 1000.0
    t = one(run(df, sigs(df, {0: dict(side=1, stop=98)})))
    assert t.qty == approx(2.5) and t.risk_usd == approx(5.0)
    t = one(run(df, sigs(df, {0: dict(side=1, entry=99.5, stop=97.5)})))   # limit: sized off the limit price
    assert t.qty == approx(2.5)


def test_notional_is_capped_at_three_times_equity():
    df = bars(ROWS)
    t = one(run(df, sigs(df, {0: dict(side=1, stop=99.9)})))      # uncapped would be 50 units = 5x
    assert t.qty == approx(30.0) and t.notional == approx(3000.0) and t.leverage == approx(3.0)
    assert t.risk_usd == approx(3.0)                              # less than 0.5% because of the cap
    t = one(run(df, sigs(df, {0: dict(side=-1, stop=100.1)})))
    assert t.qty == approx(30.0)


def test_second_trade_is_sized_on_current_equity():
    rows = [FLAT, (100, 101, 99, 100), (110, 111, 109, 110), (110, 111, 109, 110), (110, 110, 110, 110)]
    df = bars(rows)
    res = run(df, sigs(df, {0: dict(side=1, stop=90, max_hold=1), 2: dict(side=1, stop=100)}))
    a, b = res.trades.iloc[0], res.trades.iloc[1]
    net_a = 0.5 * (110 * (1 - SLIP) - 100 * (1 + SLIP)) - 0.5 * (110 * (1 - SLIP) + 100 * (1 + SLIP)) * TAKER
    assert a.net == approx(net_a)
    assert b.equity_at_entry == approx(1000 + net_a)
    assert b.qty == approx(0.005 * (1000 + net_a) / 10)


def test_fee_and_slippage_multipliers_scale_costs_exactly():
    df = bars([FLAT, (100, 101, 99, 100), (100, 101, 94, 96), FLAT])
    sg = sigs(df, {0: dict(side=1, stop=95)})
    base = one(run(df, sg))
    fee2 = one(run(df, sg, cfg=CFG.stressed(fee_mult=2, slip_mult=1)))
    slip2 = one(run(df, sg, cfg=CFG.stressed(fee_mult=1, slip_mult=2)))
    both = one(run(df, sg, cfg=CFG.stressed()))
    assert fee2.fees == approx(2 * base.fees) and fee2.slippage == approx(base.slippage)
    assert slip2.slippage == approx(2 * base.slippage)
    assert slip2.entry_price == approx(100 * (1 + 2 * SLIP)) and slip2.exit_price == approx(95 * (1 - 2 * STOP_SLIP))
    assert both.slippage == approx(2 * base.slippage)
    assert both.fees == approx(2 * TAKER * (100 * (1 + 2 * SLIP) + 95 * (1 - 2 * STOP_SLIP)))
    assert both.net < fee2.net < base.net and both.net < slip2.net < base.net
    assert base.gross == fee2.gross == slip2.gross == both.gross == approx(-5.0)


def test_net_identity_and_equity_reconciles_with_trades():
    rng = np.random.default_rng(3)
    n = 400
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    o = np.concatenate(([100.0], c[:-1]))
    h, l = np.maximum(o, c) * (1 + rng.random(n) * 0.005), np.minimum(o, c) * (1 - rng.random(n) * 0.005)
    df = bars(list(zip(o, h, l, c)), funding=rng.normal(0, 0.0005, n) * (np.arange(n) % 8 == 7))
    side = np.where(rng.random(n) < 0.2, np.where(rng.random(n) < 0.5, 1, -1), 0)
    sg = pd.DataFrame({"side": side, "entry": np.where(rng.random(n) < 0.5, c * (1 - side * 0.002), np.nan),
                       "entry_valid": 2, "stop": c * (1 - side * 0.02), "target": c * (1 + side * 0.03),
                       "max_hold": 10, "exit": rng.random(n) < 0.02}, index=df.index)
    res = run(df, sg)
    t = res.trades
    assert len(t) > 20
    assert set(t.exit_reason) <= {"stop", "stop_gap", "target", "time", "exit_signal", "end_of_data"}
    np.testing.assert_allclose(t.net, t.gross - t.slippage - t.fees - t.funding, rtol=0, atol=1e-9)
    assert res.equity.iloc[-1] == approx(1000 + t.net.sum())      # every dollar is in the trade list
    assert (t.entry_time > t.signal_time).all() and (t.exit_time >= t.entry_time).all()
    assert (t.entry_bar.to_numpy()[1:] >= t.exit_bar.to_numpy()[:-1]).all()   # never two positions at once
    assert (t.notional <= 3 * t.equity_at_entry * (1 + 1e-12)).all()
    assert len(res.equity) == n and res.equity.notna().all()


def test_flat_account_keeps_start_equity_and_exposure_is_zero():
    df = bars([FLAT] * 6)
    res = run(df, sigs(df))
    assert res.equity.tolist() == [1000.0] * 6 and not res.in_position.any() and len(res.trades) == 0
    assert res.net_return == 0.0 and res.final_equity == 1000.0
