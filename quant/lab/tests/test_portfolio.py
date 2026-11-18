"""Shared-account simulator: must equal the engine for one stream, then the sharing and Shield rules."""
import importlib
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from lab import portfolio as P
from lab import refstrats as R
from lab.engine import ExecConfig, exchange_filters, get_frame, module_signals_1h, run_backtest, spec_data
from .helpers import CFG, FLAT, SLIP, STOP_SLIP, TAKER, bars, run, sigs

approx = pytest.approx
COMPARE = ["signal_time", "entry_time", "exit_time", "side", "entry_type", "qty", "entry_ref", "entry_price",
           "exit_ref", "exit_price", "stop", "notional", "equity_at_entry", "gross", "fees", "slippage",
           "funding", "net", "exit_reason", "bars_held"]


def _assert_same(res_engine, res_port):
    a, b = res_engine.trades[COMPARE].reset_index(drop=True), res_port.trades[COMPARE].reset_index(drop=True)
    assert len(a) == len(b)
    for c in COMPARE:
        if a[c].dtype.kind == "f":
            np.testing.assert_allclose(a[c].to_numpy(float), b[c].to_numpy(float), rtol=1e-9, atol=1e-9, err_msg=c)
        else:
            assert (a[c].to_numpy() == b[c].to_numpy()).all(), c
    np.testing.assert_allclose(res_engine.equity.to_numpy(), res_port.equity.to_numpy(), rtol=1e-9, atol=1e-7)
    assert (res_engine.in_position.to_numpy() == res_port.in_position.to_numpy()).all()


def _strategy_modules():
    """Whatever Radar has in strategies/ right now, plus Lab's own references."""
    from lab import run_all as RA
    mods = []
    for name in RA.discover():
        try:
            mods.append(RA.load_module(name))
        except Exception:                      # a broken module is run_all's problem, not this test's
            pass
    return mods


@pytest.mark.parametrize("filters", [False, True], ids=["spec_sizing", "exchange_filters"])
def test_single_stream_equals_engine_on_reference_strategies(filters):
    d = spec_data()
    cfg = ExecConfig(filters={"BTCUSDT": (0.001, 0.001, 100.0), "SOLUSDT": (0.01, 0.01, 5.0)} if filters else None)
    cases = [(R.SMA_CROSS, {}, "BTCUSDT"), (R.RANDOM, {"tgt_atr": 2.0, "p_entry": 0.3}, "SOLUSDT"),
             (R.RANDOM, {"tgt_atr": 2.0, "limit_atr": 0.4, "entry_valid": 3, "p_entry": 0.3}, "SOLUSDT"),
             (R.RANDOM, {"limit_atr": -0.2, "entry_valid": 2, "p_entry": 0.2}, "BTCUSDT")]     # limits through the market
    for mod, extra, sym in cases:
        df = get_frame(sym, "1h")
        sig = mod.signals(df, {**mod.DEFAULT_PARAMS, **extra})
        e = run_backtest(df, sig, sym, d.BACKTEST_START, "2024-12-31 23:59", cfg)
        p = P.run_portfolio([P.StreamSpec(mod.NAME, sym, sig)], {sym: df}, d.BACKTEST_START, "2024-12-31 23:59", cfg)
        assert len(e.trades) > 100
        _assert_same(e, p)


def test_single_stream_equals_engine_on_every_strategy_module_including_4h_on_1h_bars():
    d = spec_data()
    cfg = ExecConfig(filters=exchange_filters())
    checked = trades = 0
    for mod in _strategy_modules():
        for sym in ("ETHUSDT", "BTCUSDT"):
            df1, sig1, mult = module_signals_1h(mod, sym, None, upto="2025-10-31 23:59")
            e = run_backtest(df1, sig1, sym, d.BACKTEST_START, "2025-10-31 23:59", cfg, hold_mult=mult)
            p = P.run_portfolio([P.StreamSpec(mod.NAME, sym, sig1, mult)], {sym: df1}, d.BACKTEST_START,
                                "2025-10-31 23:59", cfg)
            _assert_same(e, p)
            checked += 1
            trades += len(e.trades)
    if checked:                                # strategies/ belongs to Radar; if it is empty the references above cover it
        assert trades > 1000


def _two(df_a, sg_a, df_b, sg_b, cfg=CFG, shield=None, **kw):
    specs = [P.StreamSpec("a", "BTCUSDT", sg_a), P.StreamSpec("b", "ETHUSDT", sg_b)]
    return P.run_portfolio(specs, {"BTCUSDT": df_a, "ETHUSDT": df_b}, cfg=cfg, shield=shield, **kw)


def test_second_stream_is_sized_on_equity_after_the_first_streams_loss():
    a = bars([FLAT, (100, 101, 99, 100), (100, 101, 94, 96), (96, 96, 96, 96), (96, 96, 96, 96)])   # long stopped in bar 2
    b = bars([FLAT] * 3 + [(100, 101, 99, 100), (100, 100, 100, 100)])
    res = _two(a, sigs(a, {0: dict(side=1, stop=95)}), b, sigs(b, {2: dict(side=1, stop=90)}))
    ta = res.trades[res.trades.setup == "a"].iloc[0]
    tb = res.trades[res.trades.setup == "b"].iloc[0]
    loss = -5.0 - (100 * SLIP + 95 * STOP_SLIP) - (100 * (1 + SLIP) + 95 * (1 - STOP_SLIP)) * TAKER
    assert ta.net == approx(loss)
    assert tb.equity_at_entry == approx(1000 + loss)            # realised at the close of bar 2, used at the open of bar 3
    assert tb.qty == approx(0.005 * (1000 + loss) / 10)
    assert res.equity.iloc[-1] == approx(1000 + ta.net + tb.net)


def test_profit_made_inside_a_bar_is_not_available_at_that_bars_open():
    a = bars([FLAT, (100, 101, 99, 100), (100, 106, 99.5, 105), (105, 105, 105, 105)])     # target hit inside bar 2
    b = bars([FLAT, FLAT, (100, 101, 99, 100), (100, 100, 100, 100)])
    res = _two(a, sigs(a, {0: dict(side=1, stop=95, target=104)}), b, sigs(b, {1: dict(side=1, stop=90)}))
    tb = res.trades[res.trades.setup == "b"].iloc[0]
    fee_a = 1.0 * 100 * (1 + SLIP) * TAKER                       # only the entry fee of "a" is known at the open of bar 2
    assert tb.entry_time == b.index[2] and tb.equity_at_entry == approx(1000 - fee_a)


def test_gross_cap_blocks_the_order_that_would_exceed_three_times_equity():
    a = bars([FLAT, (100, 100.05, 99.95, 100), (100, 100.05, 99.95, 100), (100, 100, 100, 100)])
    sg_a = sigs(a, {0: dict(side=1, stop=99.9)})                 # capped at 3x: uses the whole allowance
    sg_b = sigs(a, {1: dict(side=1, stop=95)})                   # a normal 0.1x order one bar later
    res = _two(a, sg_a, a, sg_b)
    assert len(res.trades) == 1 and res.trades.iloc[0].setup == "a" and res.trades.iloc[0].leverage == approx(3.0)
    assert res.diagnostics["b/ETHUSDT"]["blocked_gross_cap"] == 1
    res = _two(a, sigs(a, {0: dict(side=1, stop=98)}), a, sg_b)  # 0.25x + 0.1x: fine
    assert len(res.trades) == 2


def _three_losers():
    """Three longs in a row, each stopped out inside the bar after its entry; then a 4th signal."""
    rows, spec = [FLAT], {}
    for k in range(3):
        spec[len(rows) - 1] = dict(side=1, stop=95)
        rows += [(100, 101, 99, 100), (100, 101, 94, 100)]
    spec[len(rows) - 1] = dict(side=1, stop=95, max_hold=1)      # decided at the close of the third loss
    rows += [(100, 101, 99, 100)] * 30
    return rows, spec


def test_three_straight_losses_halt_until_the_next_utc_day():
    rows, spec = _three_losers()
    spec[20] = dict(side=1, stop=95, max_hold=1)                 # 20:00, same day: still halted
    spec[23] = dict(side=1, stop=95, max_hold=1)                 # decided 24:00 -> fills 00:00 next day: allowed
    df = bars(rows)
    sg = sigs(df, spec)
    free = P.run_portfolio([P.StreamSpec("a", "BTCUSDT", sg)], {"BTCUSDT": df}, cfg=CFG)
    assert len(free.trades) == 6 and len(free.halts) == 0
    res = P.run_portfolio([P.StreamSpec("a", "BTCUSDT", sg)], {"BTCUSDT": df}, cfg=CFG, shield=P.ShieldRules())
    assert res.halts["kind"].tolist() == ["loss_streak"] and res.halts["time"].iloc[0] == df.index[6]
    assert len(res.trades) == 4
    assert (res.trades.exit_reason.iloc[:3] == "stop").all()
    assert res.trades.entry_time.iloc[3] == df.index[24]         # first trade after the halt: next UTC day
    assert res.diagnostics["a/BTCUSDT"]["blocked_halt"] == 2
    m = P.portfolio_metrics(res)
    assert m["halts"] == 1 and m["halts_loss_streak"] == 1 and m["blocked_halt"] == 2


def test_a_winner_resets_the_loss_counter():
    rows, spec = [FLAT], {}
    for k, win in enumerate([False, False, True, False, False]):
        spec[len(rows) - 1] = dict(side=1, stop=95, target=104)
        rows += [(100, 101, 99, 100), (100, 105, 99.5, 100) if win else (100, 101, 94, 100)]
    df = bars(rows + [FLAT])
    res = P.run_portfolio([P.StreamSpec("a", "BTCUSDT", sigs(df, spec))], {"BTCUSDT": df}, cfg=CFG, shield=P.ShieldRules())
    assert len(res.trades) == 5 and len(res.halts) == 0


def test_halt_flattens_other_positions_at_the_next_open_and_cancels_resting_orders():
    rows, spec = _three_losers()
    a = bars(rows)
    b_rows = [FLAT, (100, 101, 99, 100)] + [(100, 103, 99, 102)] * (len(rows) - 2)       # a healthy long on another symbol
    b = bars(b_rows)
    c = bars([FLAT] * len(rows))
    specs = [P.StreamSpec("a", "BTCUSDT", sigs(a, spec)),
             P.StreamSpec("b", "ETHUSDT", sigs(b, {0: dict(side=1, stop=80)})),
             P.StreamSpec("c", "SOLUSDT", sigs(c, {4: dict(side=1, entry=90.0, stop=80, entry_valid=20)}))]
    res = P.run_portfolio(specs, {"BTCUSDT": a, "ETHUSDT": b, "SOLUSDT": c}, cfg=CFG, shield=P.ShieldRules())
    tb = res.trades[res.trades.setup == "b"].iloc[0]
    assert res.halts["time"].iloc[0] == a.index[6]               # third stop-out happens inside bar 6
    assert tb.exit_reason == "shield" and tb.exit_time == a.index[7]          # closed at the next open
    assert tb.exit_ref == 100.0 and tb.exit_price == approx(100 * (1 - SLIP))   # at market: normal slippage
    assert res.diagnostics["b/ETHUSDT"]["flattened_by_halt"] == 1
    assert res.diagnostics["c/SOLUSDT"]["limit_cancelled_by_halt"] == 1
    assert len(res.trades[res.trades.setup == "c"]) == 0


def test_daily_loss_limit_halts_on_mark_to_market_equity():
    # ten positions is not possible with one stream, so use a big-risk config: one trade losing ~4% of equity
    cfg = replace(CFG, risk_frac=0.04)
    rows = [FLAT, (100, 101, 99, 100), (100, 101, 95.5, 96), (96, 97, 95.5, 96)] + [(96, 97, 95.5, 96)] * 30
    df = bars(rows)
    spec = {0: dict(side=1, stop=95), 10: dict(side=1, stop=90, max_hold=1), 23: dict(side=1, stop=90, max_hold=1)}
    res = P.run_portfolio([P.StreamSpec("a", "BTCUSDT", sigs(df, spec))], {"BTCUSDT": df}, cfg=cfg, shield=P.ShieldRules())
    # bar 2 closes at 96 with the stop (95) untouched: open loss = 8 units x ~4 = ~3.2% of equity -> halt on the mark
    assert res.halts["kind"].tolist() == ["daily_loss"] and res.halts["time"].iloc[0] == df.index[2]
    first = res.trades.iloc[0]
    assert first.exit_reason == "shield" and first.exit_time == df.index[3] and first.exit_ref == 96.0
    assert len(res.trades) == 2 and res.trades.entry_time.iloc[1] == df.index[24]        # bar-10 signal blocked
    assert res.diagnostics["a/BTCUSDT"]["blocked_halt"] == 1
    no_shield = P.run_portfolio([P.StreamSpec("a", "BTCUSDT", sigs(df, spec))], {"BTCUSDT": df}, cfg=cfg)
    assert len(no_shield.halts) == 0 and len(no_shield.trades) == 1          # without Shield it just sits there
    assert no_shield.trades.exit_reason.iloc[0] == "end_of_data"


def test_portfolio_equity_is_cash_plus_open_pnl_and_reconciles_with_trades():
    d = spec_data()
    cfg = ExecConfig(filters=exchange_filters())
    specs, frames = [], {}
    for sym, seed in (("BTCUSDT", 1), ("ETHUSDT", 2), ("SOLUSDT", 3)):
        df = get_frame(sym, "1h")
        frames[sym] = df
        specs.append(P.StreamSpec("rnd", sym, R.RANDOM.signals(df, {**R.RANDOM.DEFAULT_PARAMS, "seed": seed, "tgt_atr": 3.0})))
    df = frames["ETHUSDT"]
    specs.append(P.StreamSpec("sma", "ETHUSDT", R.SMA_CROSS.signals(df, R.SMA_CROSS.DEFAULT_PARAMS)))
    for shield in (None, P.ShieldRules()):
        res = P.run_portfolio(specs, frames, d.BACKTEST_START, "2024-06-30 23:59", cfg, shield)
        t = res.trades
        assert len(t) > 300 and res.equity.notna().all()
        assert res.final_equity == approx(1000 + t["net"].sum(), rel=1e-9)
        np.testing.assert_allclose(t.net, t.gross - t.slippage - t.fees - t.funding, atol=1e-9)
        for (_, _), g in t.groupby(["setup", "symbol"]):                     # one position per (setup, symbol)
            g = g.sort_values("entry_bar")
            assert (g.entry_bar.to_numpy()[1:] >= g.exit_bar.to_numpy()[:-1]).all()
        if shield is not None:
            assert len(res.halts) > 0 and (t.exit_reason == "shield").any()
            days = pd.DatetimeIndex(res.halts["time"]).normalize()
            # no trade is opened on a halted day after the halt
            for ht, day in zip(res.halts["time"], days):
                later_same_day = t[(t.entry_time > ht) & (pd.DatetimeIndex(t.entry_time).normalize() == day)]
                assert len(later_same_day) == 0
