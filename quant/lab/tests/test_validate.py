"""Validation tools: walk-forward discipline, Monte Carlo, grid, stress, look-ahead audit, multiple testing."""
import math

import numpy as np
import pandas as pd
import pytest

from lab import metrics as M
from lab import refstrats as R
from lab import validate as V
from lab.engine import ExecConfig

approx = pytest.approx
SYM = "BTCUSDT"


# ---------------------------------------------------------------- windows / folds
def test_holdout_is_the_last_12_months_and_dev_stops_before_it():
    start, end = V.spec_window()
    h0 = V.holdout_start()
    assert h0 == pd.Timestamp("2025-11-01", tz="UTC")
    assert V.dev_window() == (start, h0 - pd.Timedelta(seconds=1))
    assert V.holdout_window() == (h0, end)


def test_walk_forward_folds_are_out_of_sample_and_never_reach_the_holdout():
    folds = V.wf_folds(*V.dev_window(), is_months=12, oos_months=3)
    assert len(folds) == 8
    assert folds[0]["is_start"] == pd.Timestamp("2022-11-01", tz="UTC")
    assert folds[0]["oos_start"] == pd.Timestamp("2023-11-01", tz="UTC")
    for a, b in zip(folds, folds[1:]):
        assert a["oos_end"] == b["oos_start"]                    # OOS blocks tile, no overlap, no gap
    for f in folds:
        assert f["is_end"] == f["oos_start"] and f["is_start"] < f["is_end"] < f["oos_end"]
        assert f["is_end"] - pd.DateOffset(months=12) == f["is_start"]       # rolling 12 months
        assert f["oos_end"] <= V.holdout_start()
    anchored = V.wf_folds(*V.dev_window(), mode="anchored")
    assert all(f["is_start"] == folds[0]["is_start"] for f in anchored)


# ---------------------------------------------------------------- synthetic frames
def _two_regime_frame():
    """12 months straight up, then 3 months straight down (1h bars, small noise-free steps)."""
    idx = pd.date_range("2023-01-01", "2024-03-31 23:00", freq="1h", tz="UTC")
    up = idx < pd.Timestamp("2024-01-01", tz="UTC")
    r = np.where(up, 0.0002, -0.0004)
    c = 100 * np.cumprod(1 + r)
    o = np.concatenate(([100.0], c[:-1]))
    return pd.DataFrame({"open": o, "high": np.maximum(o, c), "low": np.minimum(o, c), "close": c,
                         "funding_paid": 0.0}, index=idx)


def _directional(df, p, ctx=None):
    s = R.blank(df)
    go = (df.index.hour == 0).astype(int) * int(p["dir"])
    s["side"] = go
    s["stop"] = df["close"] * (1 - go * 0.10)
    s["max_hold"] = 12
    return s


DIRECTIONAL = R._mk("t_directional", "1h", "trending", "long or short once a day", {"dir": 1}, _directional,
                    grid={"dir": [-1, 1]})


@pytest.fixture
def two_regime(monkeypatch):
    df = _two_regime_frame()
    monkeypatch.setattr(V, "get_frame", lambda sym, tf: df)
    monkeypatch.setattr(V, "get_ctx", lambda tf, upto=None: {})
    return df


def test_walk_forward_picks_on_in_sample_only_and_reports_out_of_sample_only(two_regime):
    df = two_regime
    wf = V.walk_forward(DIRECTIONAL, "X", start="2023-01-01", end="2024-03-31 23:59", is_months=12, oos_months=3)
    assert len(wf.folds) == 1
    f = wf.folds.iloc[0]
    # in-sample (2023) only goes up -> long must be chosen, although short is what wins out-of-sample
    assert f["chosen"]["dir"] == 1
    assert f["oos_net_return"] < 0 and wf.metrics["net_return"] == approx(f["oos_net_return"])
    # the reported curve and trades are strictly the OOS block
    assert wf.result.equity.index[0] == pd.Timestamp("2024-01-01", tz="UTC")
    assert (pd.DatetimeIndex(wf.result.trades["entry_time"]) >= pd.Timestamp("2024-01-01", tz="UTC")).all()
    # had it peeked at the OOS block it would have gone short and made money
    cheat = V.SignalBank(DIRECTIONAL, "X").run({"dir": -1}, "2024-01-01", "2024-03-31 23:59")
    assert cheat.net_return > 0


def test_parameter_neighbourhood_share_profitable_and_spread(two_regime):
    tab, s = V.param_neighbourhood(DIRECTIONAL, "X", start="2023-01-01", end="2023-12-31 23:59")
    assert len(tab) == 2 and s["n_combos"] == 2 and s["grid_source"] == "PARAM_GRID"
    assert s["share_profitable"] == approx(0.5)                # long wins in 2023, short loses
    long_ret = float(tab.loc[tab["params"].map(lambda p: p["dir"] == 1), "net_return"].iloc[0])
    short_ret = float(tab.loc[tab["params"].map(lambda p: p["dir"] == -1), "net_return"].iloc[0])
    assert long_ret > 0 > short_ret
    assert s["net_return_spread"] == approx(long_ret - short_ret)
    assert s["default_net_return"] == approx(long_ret) and s["default_percentile"] == 1.0


def test_stress_doubles_fees_and_slippage(two_regime):
    st = V.stress(DIRECTIONAL, "X", start="2023-01-01", end="2023-01-31 23:59")
    assert list(st.index) == ["base", "fees_x2", "slippage_x2", "fees_x2_slippage_x2"]
    assert st.loc["fees_x2", "fees_paid"] / st.loc["base", "fees_paid"] == approx(2.0, rel=0.01)
    assert st.loc["slippage_x2", "slippage_paid"] / st.loc["base", "slippage_paid"] == approx(2.0, rel=0.01)
    nr = st["net_return"]
    assert nr["fees_x2_slippage_x2"] < nr["fees_x2"] < nr["base"]
    assert nr["fees_x2_slippage_x2"] < nr["slippage_x2"] < nr["base"]
    assert (st["trades"] == st.loc["base", "trades"]).all()


# ---------------------------------------------------------------- grid
def test_param_grid_forms():
    combos, src, coords = V.param_grid(R.SMA_CROSS)
    assert len(combos) == 27 and src == "PARAM_GRID" and len(set(coords)) == 27
    assert all(set(c) == {"fast", "slow", "stop_atr"} for c in combos)
    m = R._mk("x", "1h", "ranging", "", {"a": 1, "b": 2.0}, None, grid=[{"a": 3}, {"b": 5.0}])
    combos, src, coords = V.param_grid(m)
    assert combos == [{"a": 3, "b": 2.0}, {"a": 1, "b": 5.0}] and coords is None
    m = R._mk("y", "1h", "ranging", "", {"n": 20, "k": 2.0, "flag": True, "name": "z"}, None)
    combos, src, coords = V.param_grid(m)                          # no PARAM_GRID -> +/-25% neighbourhood
    assert src.startswith("auto") and len(combos) == 9
    assert sorted({c["n"] for c in combos}) == [15, 20, 25] and sorted({c["k"] for c in combos}) == [1.5, 2.0, 2.5]
    assert all(c["flag"] is True and c["name"] == "z" for c in combos)


def test_neighbour_smoothing_prefers_a_plateau_to_a_spike():
    coords = [(i,) for i in range(6)]
    scores = np.array([0.0, 9.0, 0.0, 5.0, 5.0, 5.0])              # lonely spike at 1, plateau at 3..5
    sm = V._smooth(scores, coords)
    assert sm[1] == approx((2 * 9 + 0 + 0) / 4) and sm[4] == approx((2 * 5 + 5 + 5) / 4)
    assert int(np.nanargmax(sm)) == 4                                # 5.0 on the plateau beats 4.5 on the spike
    sm = V._smooth(np.array([np.nan, 1.0, np.nan]), [(0,), (1,), (2,)])
    assert np.isnan(sm[0]) and sm[1] == 1.0
    sm = V._smooth(np.array([-1.0, 2.0]), [(0,), (1,)])              # two-point grid must not tie
    assert sm[1] > sm[0]


# ---------------------------------------------------------------- Monte Carlo
def test_monte_carlo_known_cases():
    mc = V.monte_carlo([0.10, -0.10], n_paths=20000, seed=1)
    # 4 equally likely paths: 1.21, 0.99, 0.99, 0.81 -> P(loss) = 3/4
    assert mc["prob_loss"] == approx(0.75, abs=0.015)
    assert mc["final_return_p05"] == approx(-0.19) and mc["final_return_p95"] == approx(0.21)
    assert mc["final_return_mean"] == approx(0.0, abs=0.005)
    wins = V.monte_carlo([0.01] * 50, n_paths=2000)
    assert wins["prob_loss"] == 0.0 and wins["max_dd_p95"] == 0.0
    assert wins["final_return_p05"] == approx(1.01 ** 50 - 1)
    losers = V.monte_carlo([-0.01] * 50, n_paths=2000)
    assert losers["prob_loss"] == 1.0 and losers["max_dd_p95"] == approx(1 - 0.99 ** 50)


def test_monte_carlo_is_reproducible_and_shuffle_keeps_the_final_return():
    rng = np.random.default_rng(0)
    rets = rng.normal(0.002, 0.01, 300)
    a, b = V.monte_carlo(rets, n_paths=3000, seed=7), V.monte_carlo(rets, n_paths=3000, seed=7)
    assert a == b and a["n_paths"] == 3000 and a["n_trades"] == 300
    assert a["final_return_p05"] < a["final_return_p50"] < a["final_return_p95"]
    assert a["max_dd_p95"] >= a["max_dd_p50"] > 0
    sh = V.monte_carlo(rets, n_paths=3000, seed=7, method="shuffle")
    total = float(np.prod(1 + rets) - 1)
    assert sh["final_return_p05"] == approx(total) and sh["final_return_p95"] == approx(total)
    assert sh["max_dd_p95"] > sh["max_dd_p50"] > 0
    assert sh["observed_return"] == approx(total)
    empty = V.monte_carlo([], n_paths=2000)
    assert empty["n_trades"] == 0 and math.isnan(empty["prob_loss"])
    t = pd.DataFrame({"ret": rets})
    assert V.monte_carlo(t, n_paths=3000, seed=7) == a             # accepts the engine's trade list


# ---------------------------------------------------------------- multiple testing
def test_multiple_testing_arithmetic():
    mt = V.multiple_testing(20, years=4.0, alpha=0.05)
    assert mt["expected_false_positives"] == approx(1.0)
    assert mt["prob_at_least_one_false_positive"] == approx(1 - 0.95 ** 20)
    assert mt["bonferroni_alpha"] == approx(0.0025)
    assert mt["sharpe_bar_single_test"] == approx(1.6449 / 2, abs=1e-3)
    assert mt["sharpe_bar_bonferroni"] == approx(2.8070 / 2, abs=1e-3)
    assert mt["sharpe_bar_bonferroni"] > mt["expected_best_sharpe_by_luck"] > mt["sharpe_bar_single_test"] > 0
    assert "by chance" in mt["note"]
    assert V.expected_max_sharpe(1, 0.5) == 0.0
    assert V.expected_max_sharpe(100, 0.5) > V.expected_max_sharpe(10, 0.5) > 0


def test_sharpe_haircut():
    one = V.haircut_sharpe(1.0, years=4.0, n_tests=1)
    assert one["t_stat"] == approx(2.0) and one["p_value"] == approx(0.02275, abs=1e-4)
    assert one["haircut_sharpe"] == approx(1.0) and one["significant_5pct"]
    many = V.haircut_sharpe(1.0, years=4.0, n_tests=27)
    assert many["p_bonferroni"] == approx(27 * one["p_value"]) and not many["significant_5pct"]
    assert many["haircut_sharpe"] == 0.0 and many["haircut"] == 1.0          # 0.61 > 0.5: nothing left
    strong = V.haircut_sharpe(2.0, years=4.0, n_tests=27)
    assert 0 < strong["haircut_sharpe"] < 2.0 and strong["significant_5pct"]
    assert strong["haircut_sharpe"] == approx(V._N.inv_cdf(1 - 27 * (1 - V._N.cdf(4.0))) / 2.0)


def test_deflated_sharpe_falls_with_more_trials():
    rng = np.random.default_rng(4)
    r = rng.normal(0.001, 0.01, 1400)                              # daily Sharpe ~0.1 -> ~1.9 annualised
    d1, d50, d5000 = (V.deflated_sharpe(r, n)["dsr"] for n in (1, 50, 5000))
    assert d1 > d50 > d5000 and d1 > 0.99
    noise = V.deflated_sharpe(rng.normal(0.0, 0.01, 1400), 50)
    assert noise["dsr"] < 0.5
    assert math.isnan(V.deflated_sharpe(np.zeros(100), 5)["dsr"])


# ---------------------------------------------------------------- look-ahead audit on real data
@pytest.mark.parametrize("strat", [R.SMA_CROSS, R.RANDOM, R.PEEK, R.ALWAYS_LONG], ids=lambda s: s.NAME)
def test_prefix_invariance_passes_for_honest_strategies(strat):
    pi = V.prefix_invariance(strat, SYM, n_random=3)
    assert pi["ok"] and pi["strictly_identical"] and pi["cuts_checked"] >= 5


@pytest.mark.parametrize("strat", [R.LEAKY_FUTURE, R.LEAKY_NORM], ids=lambda s: s.NAME)
def test_prefix_invariance_catches_lookahead(strat):
    pi = V.prefix_invariance(strat, SYM, n_random=3)
    assert not pi["ok"] and pi["rows_material"] > 0 and pi["mismatches"]
    assert "side" in pi["mismatches"][0]["columns"]


def test_shift_minus_one_leak_is_caught_at_the_last_row_of_the_prefix():
    pi = V.prefix_invariance(R.LEAKY_FUTURE, SYM, n_random=0)
    assert all(m["bars_before_cut"] == 0 for m in pi["mismatches"])           # only the final row differs
    assert len(pi["mismatches"]) > 0


@pytest.fixture(scope="module")
def audits():
    return {s.NAME: V.lookahead_audit(s, SYM) for s in (R.LEAKY_FUTURE, R.SMA_CROSS, R.PEEK)}


def test_lookahead_audit_verdicts(audits):
    leak, honest, peek = audits["ref_leaky_future"], audits["ref_sma_cross"], audits["ref_peek_same_bar"]
    assert not leak["passed"] and honest["passed"] and peek["passed"]
    # the cheat "earns" a fortune that one bar of delay destroys: both facts are on the report
    d = leak["delay"]
    assert d["net_return_delay0"] > 5.0 and d["net_return_delay1"] < 0.5 * d["net_return_delay0"]
    assert d["collapses_when_delayed"] and "edge collapses with one bar of delay" not in honest["warnings"]
    # an honest same-bar-information strategy executed by the engine does not make money
    assert peek["delay"]["net_return_delay0"] < 0


def test_delay_flag_logic(monkeypatch):
    class FakeBank:
        def __init__(self, rets):
            self.rets = rets

        def run(self, params, start, end, cfg):
            eq = pd.Series([1000.0, 1000.0 * (1 + self.rets[cfg.extra_delay])],
                           index=pd.date_range("2024-01-01", periods=2, freq="1D", tz="UTC"))
            return V.BacktestResult("X", pd.DataFrame(columns=["net"]), eq, eq > 0, {}, cfg, eq.index[0],
                                    eq.index[-1], pd.Timedelta(days=1))
    better = V.delay_test(None, "X", bank=FakeBank([0.10, 0.30, 0.30]))
    assert better["improves_when_delayed"] and not better["collapses_when_delayed"]
    worse = V.delay_test(None, "X", bank=FakeBank([0.40, 0.10, 0.05]))
    assert worse["collapses_when_delayed"] and not worse["improves_when_delayed"]
    gentle = V.delay_test(None, "X", bank=FakeBank([0.40, 0.35, 0.30]))
    assert not gentle["collapses_when_delayed"] and not gentle["improves_when_delayed"]
    loser = V.delay_test(None, "X", bank=FakeBank([-0.12, -0.04, -0.05]))       # still a loser: warning, not a leak
    assert loser["less_bad_when_delayed"] and not loser["improves_when_delayed"]
    assert not better["less_bad_when_delayed"] and not gentle["less_bad_when_delayed"]


# ---------------------------------------------------------------- full battery on real data
@pytest.fixture(scope="module")
def wf_real():
    return V.walk_forward(R.SMA_CROSS, SYM)


def test_walk_forward_on_real_data_stays_inside_dev(wf_real):
    wf = wf_real
    h0 = V.holdout_start()
    assert len(wf.folds) == 8 and wf.n_combos == 27 and wf.grid_source == "PARAM_GRID"
    eq = wf.result.equity
    assert eq.index[0] == pd.Timestamp("2023-11-01", tz="UTC") and eq.index[-1] < h0
    assert eq.index.is_monotonic_increasing and eq.index.is_unique
    assert len(eq) == 24 * (h0 - eq.index[0]).days                 # every OOS bar exactly once
    t = wf.result.trades
    assert (pd.DatetimeIndex(t["entry_time"]) >= eq.index[0]).all() and (pd.DatetimeIndex(t["exit_time"]) < h0).all()
    assert wf.metrics["trades"] == len(t) == wf.folds["oos_trades"].sum()
    assert wf.result.final_equity == approx(1000 + t["net"].sum())             # equity carried across folds
    assert np.prod(1 + wf.folds["oos_net_return"]) - 1 == approx(wf.metrics["net_return"])
    assert all(c in V.param_grid(R.SMA_CROSS)[0] for c in wf.folds["chosen"])


def test_walk_forward_selection_is_identical_when_future_data_is_physically_removed(wf_real):
    strict = V.walk_forward(R.SMA_CROSS, SYM, strict=True)          # signals recomputed on truncated frames
    assert [repr(c) for c in strict.folds["chosen"]] == [repr(c) for c in wf_real.folds["chosen"]]
    np.testing.assert_allclose(strict.folds["oos_net_return"], wf_real.folds["oos_net_return"], rtol=1e-9)
    np.testing.assert_allclose(strict.folds["is_score"], wf_real.folds["is_score"], rtol=1e-9)


def test_holdout_run_covers_exactly_the_last_12_months():
    r, m = V.holdout(R.SMA_CROSS, SYM)
    assert r.equity.index[0] == V.holdout_start() and r.equity.index[-1] == pd.Timestamp("2026-10-31 15:00", tz="UTC")
    assert (pd.DatetimeIndex(r.trades["entry_time"]) >= V.holdout_start()).all()
    assert m["trades"] == len(r.trades) and "bh_net_return" in m


def test_validate_setup_kills_the_cheat_for_the_right_reason():
    rep = V.validate_setup(R.LEAKY_FUTURE, SYM, n_tests=10, mc_paths=2000, with_holdout=False)
    assert rep["dev"]["net_return"] > 5                             # looks fantastic...
    assert not rep["verdict"]["survives"]                           # ...and is rejected
    assert "look-ahead audit failed" in rep["verdict"]["failed_rules"]
    assert "holdout" not in rep


def test_verdict_applies_every_kill_rule():
    good = dict(dev=dict(trades=200, net_return=0.4, profit_factor=1.5),
                audit=dict(passed=True, warnings=[]),
                stress=pd.DataFrame({"net_return": [0.4, 0.2]}, index=["base", "fees_x2_slippage_x2"]),
                walk_forward=dict(net_return=0.2), neighbourhood=dict(share_profitable=0.9),
                monte_carlo=dict(prob_loss=0.05, max_dd_p95=0.15),
                haircut=dict(haircut_sharpe=0.8, sharpe=1.9), multiple_testing=dict(n_tests=12),
                holdout=dict(net_return=0.05))
    assert V.verdict(good) == dict(survives=True, tier="survivor", failed_rules=[], warnings=[])
    breakers = [("dev", dict(trades=10, net_return=0.4, profit_factor=1.5)),
                ("dev", dict(trades=200, net_return=-0.1, profit_factor=1.5)),
                ("dev", dict(trades=200, net_return=0.4, profit_factor=1.05)),
                ("audit", dict(passed=False, warnings=[])),
                ("stress", pd.DataFrame({"net_return": [0.4, -0.01]}, index=["base", "fees_x2_slippage_x2"])),
                ("walk_forward", dict(net_return=-0.02)), ("neighbourhood", dict(share_profitable=0.4)),
                ("monte_carlo", dict(prob_loss=0.35, max_dd_p95=0.15)),
                ("monte_carlo", dict(prob_loss=0.05, max_dd_p95=0.40)),
                ("monte_carlo", dict(prob_loss=float("nan"), max_dd_p95=float("nan"))),
                ("holdout", dict(net_return=-0.03))]
    for key, bad in breakers:
        v = V.verdict({**good, key: bad})
        assert not v["survives"] and v["tier"] == "killed" and len(v["failed_rules"]) >= 1, key
    # alive on every hard rule but statistically indistinguishable from luck: not a survivor either
    v = V.verdict({**good, "haircut": dict(haircut_sharpe=0.0, sharpe=0.7)})
    assert v["tier"] == "unproven" and not v["survives"] and v["failed_rules"] == []
    assert "multiple-testing haircut" in v["warnings"][0]
    assert V.verdict({**good, "audit": dict(passed=False, warnings=[]),
                      "haircut": dict(haircut_sharpe=0.0, sharpe=0.7)})["tier"] == "killed"


# ---------------------------------------------------------------- random-entry baseline
def _trend_frame(n=3000, drift=0.0004, seed=9):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.006, n)))
    o = np.concatenate(([100.0], c[:-1]))
    idx = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.001, "low": np.minimum(o, c) * 0.999, "close": c,
                         "funding_paid": 0.0}, index=idx)


def test_random_entry_null_does_not_credit_a_long_only_setup_for_market_drift():
    from lab.engine import run_backtest
    df = _trend_frame()
    a = R.atr(df, 24)
    sig = R.blank(df)
    on = (np.arange(len(df)) % 48 == 30) & a.notna().to_numpy()          # a long every two days, no skill at all
    sig["side"] = on.astype(int)
    sig["stop"] = np.where(on, df["close"] - 4 * a, np.nan)
    sig["max_hold"] = 24
    res = run_backtest(df, sig, "BTCUSDT", cfg=ExecConfig(slippage=0.0, maker_fee=0.0, taker_fee=0.0))
    assert res.net_return > 0.02                                         # looks like an edge: it is only the drift
    null = V.random_entry_null(res, df, n=100)
    assert null["null_mean"] > 0.5 * res.net_return                      # random longs earn about the same
    assert null["p_value"] > 0.10 and 0 < null["p_value"] <= 1
    assert null["n_shifts"] == 100 and abs(null["null_trades"] - len(res.trades)) <= 0.1 * len(res.trades)


def test_random_entry_null_recognises_real_timing_skill():
    from lab.engine import run_backtest
    df = _trend_frame(drift=0.0)
    a = R.atr(df, 24)
    fwd = df["close"].shift(-12) / df["close"] - 1                       # cheat on purpose: knows the next 12 bars
    sig = R.blank(df)
    on = (np.arange(len(df)) % 24 == 7) & a.notna().to_numpy() & fwd.notna().to_numpy()
    side = np.where(on, np.sign(fwd.fillna(0)), 0).astype(int)
    sig["side"] = side
    sig["stop"] = df["close"] - side * 6 * a
    sig["max_hold"] = 12
    res = run_backtest(df, sig, "BTCUSDT")
    null = V.random_entry_null(res, df, n=100)
    assert res.net_return > null["null_p95"] and null["p_value"] < 0.02
    assert null["actual_avg_R"] > null["null_avg_R"] + 0.2
