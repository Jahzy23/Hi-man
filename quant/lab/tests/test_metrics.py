"""Metrics, benchmark arithmetic and regime labels on numbers small enough to check by hand."""
import numpy as np
import pandas as pd
import pytest

from lab import metrics as M
from lab.engine import ExecConfig, run_backtest
from .helpers import CFG, FLAT, SLIP, TAKER, bars, run, sigs

approx = pytest.approx


def _eq(values, start="2024-01-01", freq="1h"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq=freq, tz="UTC"), dtype=float)


def test_max_drawdown():
    assert M.max_drawdown([100, 120, 90, 130, 104]) == approx(0.25)          # 120 -> 90
    assert M.max_drawdown([90, 95], start_equity=100) == approx(0.10)        # the start is the first peak
    assert M.max_drawdown([100, 101, 102]) == 0.0


def test_sharpe_from_daily_equity():
    # 3 days of hourly bars; only the day-end values matter: 1010, 1000, 1030
    v = [1000.0] * 72
    v[23], v[47], v[71] = 1010.0, 1000.0, 1030.0
    v[24:47] = [1010.0] * 23
    v[48:71] = [1000.0] * 23
    eq = _eq(v)
    r = np.array([1010 / 1000 - 1, 1000 / 1010 - 1, 1030 / 1000 - 1])
    assert M.daily_returns(eq, 1000.0).tolist() == approx(r.tolist())
    assert M.sharpe(eq, 1000.0) == approx(r.mean() / r.std(ddof=1) * np.sqrt(365))
    assert M.sharpe(_eq([1000.0] * 72), 1000.0) == 0.0


def test_cagr_and_window_years():
    assert M.cagr(2.0, 2.0) == approx(2 ** 0.5 - 1)
    assert M.cagr(0.5, 1.0) == approx(-0.5)
    assert M.cagr(0.0, 3.0) == -1.0
    eq = _eq([1000.0] * (24 * 365), "2023-01-01")                            # exactly 365 days of 1h bars
    assert M.window_years(eq, pd.Timedelta(hours=1)) == approx(365 / 365.25)


def test_worst_calendar_month():
    idx = pd.date_range("2024-01-01", "2024-03-31", freq="1D", tz="UTC")
    v = pd.Series(1000.0, index=idx)
    v[idx.month == 1] = 1100.0        # Jan +10%
    v[idx.month == 2] = 990.0         # Feb -10%
    v[idx.month == 3] = 1039.5        # Mar +5%
    st = M.equity_stats(v, 1000.0, pd.Timedelta(days=1))
    assert M.monthly_returns(v, 1000.0).tolist() == approx([0.10, -0.10, 0.05])
    assert st["worst_month"] == approx(-0.10) and st["worst_month_label"] == "2024-02"
    assert st["net_return"] == approx(0.0395) and st["max_dd"] == approx(0.10)


def test_trade_stats_by_hand():
    net = [10, -5, -5, 20, -2, -3, -4]
    t = pd.DataFrame({"net": net, "R": [2, -1, -1, 4, -0.4, -0.6, -0.8], "bars_held": 3, "side": [1, 1, -1, 1, -1, -1, 1],
                      "gross": [11, -4, -4, 21, -1, -2, -3], "fees": 0.5, "slippage": 0.25, "funding": 0.25,
                      "exit_reason": ["target", "stop", "stop_gap", "time", "exit_signal", "stop", "end_of_data"]})
    s = M.trade_stats(t)
    assert s["trades"] == 7 and s["win_rate"] == approx(2 / 7)
    assert s["profit_factor"] == approx(30 / 19)
    assert s["avg_R"] == approx(sum([2, -1, -1, 4, -0.4, -0.6, -0.8]) / 7)
    assert s["longest_losing_streak"] == 3
    assert s["fees_paid"] == approx(3.5) and s["slippage_paid"] == approx(1.75) and s["funding_paid"] == approx(1.75)
    assert s["net_pnl"] == approx(11) and s["gross_pnl"] == approx(18)
    assert (s["stop_exits"], s["target_exits"], s["time_exits"], s["signal_exits"]) == (3, 1, 1, 1)
    assert M.longest_losing_streak([-1, -1, 1, -1, -1, -1, -1, 1]) == 4
    assert M.longest_losing_streak([]) == 0 and M.trade_stats(t.iloc[:0])["trades"] == 0
    assert M.profit_factor([1, 2]) == float("inf") and np.isnan(M.profit_factor([]))


def test_buy_and_hold_benchmark_arithmetic():
    df = bars([(100, 100, 100, 100), (100, 110, 100, 110), (110, 120, 110, 120)], funding=[0.001, 0.0, -0.002])
    b = M.buy_and_hold(df, "BTCUSDT", cfg=CFG)
    qty, fill = 1000 / 100, 100 * (1 + SLIP)                  # 1x notional at the first open
    fee = qty * fill * TAKER                                  # 0.5005
    f0, f2 = qty * 100 * 0.001, qty * 120 * -0.002            # pays 1.00, receives 2.40
    e0 = 1000 - fee + qty * (100 - fill) - f0
    e1 = 1000 - fee + qty * (110 - fill) - f0
    e2 = 1000 - fee + qty * (120 - fill) - f0 - f2
    assert (e0, e1, e2) == approx((997.4995, 1097.4995, 1199.8995))
    assert b.equity.tolist() == approx([e0, e1, e2])
    assert b.stats["fee_paid"] == approx(0.5005) and b.stats["funding_paid"] == approx(-1.4)
    assert b.stats["net_return"] == approx(0.1998995) and b.stats["price_return"] == approx(0.20)
    assert b.stats["max_dd"] == approx(1 - 997.4995 / 1000)


def test_always_long_engine_run_equals_benchmark_minus_exit_cost():
    rng = np.random.default_rng(5)
    n = 500
    c = 100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n)))
    o = np.concatenate(([100.0], c[:-1]))
    df = bars(list(zip(o, np.maximum(o, c) * 1.002, np.minimum(o, c) * 0.998, c)),
              funding=rng.normal(0.0001, 0.0003, n) * (np.arange(n) % 8 == 7))
    sg = sigs(df)
    sg["side"], sg["stop"] = 1, 1e-9                           # stop effectively at zero
    # risk_frac = 1 with the stop at ~0 -> qty = equity / open = exactly 1x notional
    res = run_backtest(df, sg, "BTCUSDT", start=df.index[1], cfg=ExecConfig(slippage=SLIP, risk_frac=1.0, max_leverage=1.0))
    b = M.buy_and_hold(df, "BTCUSDT", start=df.index[1], cfg=CFG)
    t = res.trades.iloc[0]
    assert len(res.trades) == 1 and t.exit_reason == "end_of_data" and t.qty == approx(b.stats["qty"])
    exit_cost = t.qty * c[-1] * SLIP + t.qty * c[-1] * (1 - SLIP) * TAKER
    assert res.final_equity == approx(b.equity.iloc[-1] - exit_cost, rel=1e-12)
    np.testing.assert_allclose(res.equity.to_numpy()[:-1], b.equity.to_numpy()[:-1], rtol=1e-12)   # bar by bar
    assert t.funding == approx(b.stats["funding_paid"])


def test_compute_metrics_exposure_costs_and_benchmark_fields():
    df = bars([FLAT, (100, 101, 99, 100), (100, 102, 99, 101), (101, 102, 100, 102), FLAT, FLAT, FLAT, FLAT])
    res = run(df, sigs(df, {0: dict(side=1, stop=90, max_hold=2)}))
    m = M.compute_metrics(res, df)
    t = res.trades.iloc[0]
    assert m["trades"] == 1 and m["exposure"] == approx(2 / 8)
    assert m["fees_paid"] == approx(t.fees) and m["slippage_paid"] == approx(t.slippage)
    assert m["net_return"] == approx(t.net / 1000) and m["win_rate"] == 1.0
    assert m["cost_pct_of_start"] == approx((t.fees + t.slippage + t.funding) / 1000)
    assert m["bh_net_return"] == approx(M.buy_and_hold(df, "BTCUSDT", cfg=CFG).stats["net_return"])
    assert m["excess_vs_bh"] == approx(m["net_return"] - m["bh_net_return"])
    assert "sharpe" in m and "cagr" in m and "worst_month" in m and "longest_losing_streak" in m


def _daily(closes):
    idx = pd.date_range("2020-01-01", periods=len(closes), freq="1D", tz="UTC")
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"open": c, "high": c, "low": c, "close": c, "funding_paid": 0.0}, index=idx)


def test_regime_rule():
    flat = [100.0] * 200
    rise = [100.0 * 1.01 ** k for k in range(1, 101)]
    fall = [rise[-1] * 0.985 ** k for k in range(1, 101)]
    lab = M.regime_labels(_daily(flat + rise + fall))
    assert (lab.iloc[:91] == "unknown").all()                 # no 90-day return yet
    assert (lab.iloc[91:200] == "sideways").all()             # flat: |ret90| < 15%
    assert lab.iloc[201] == "sideways"                        # one +1% day is not a bull market
    # rise[k-1] sits at row 199+k; the label of row i looks at close[i-1] versus close[i-91] (= 100)
    assert lab.iloc[214] == "sideways"                        # close[213] = 100 * 1.01^14 = +14.9%
    assert lab.iloc[215] == "bull"                            # close[214] = 100 * 1.01^15 = +16.1%, above the MA
    assert (lab.iloc[215:301] == "bull").all()
    assert lab.iloc[-1] == "bear"                             # -74% over 90 days and far below the 200-day mean
    assert set(lab.unique()) == {"unknown", "sideways", "bull", "bear"}


def test_regime_labels_have_no_lookahead():
    rng = np.random.default_rng(1)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.03, 600)))
    df = _daily(c)
    full = M.regime_labels(df)
    for k in (250, 400, 599):
        assert M.regime_labels(df.iloc[:k]).equals(full.iloc[:k])            # prefix-invariant
        shocked = df.copy()
        shocked.iloc[k:, :4] *= 10.0                                         # change bar k and everything after
        assert M.regime_labels(shocked).iloc[:k + 1].equals(full.iloc[:k + 1])   # label of bar k itself unchanged


def test_yearly_and_regime_split_add_up():
    rng = np.random.default_rng(2)
    n = 900
    c = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, n)))
    idx = pd.date_range("2022-06-01", periods=n, freq="1D", tz="UTC")
    o = np.concatenate(([100.0], c[:-1]))
    df = pd.DataFrame({"open": o, "high": np.maximum(o, c) * 1.01, "low": np.minimum(o, c) * 0.99, "close": c,
                       "funding_paid": 0.0001}, index=idx)
    side = np.where(rng.random(n) < 0.15, np.where(rng.random(n) < 0.5, 1, -1), 0)
    sg = pd.DataFrame({"side": side, "entry": np.nan, "entry_valid": 1, "stop": c * (1 - side * 0.08),
                       "target": np.nan, "max_hold": 5, "exit": False}, index=idx)
    res = run_backtest(df, sg, "BTCUSDT", start="2022-12-01")
    y = M.yearly_split(res, df)
    assert list(y.index) == [2022, 2023, 2024] and y["trades"].sum() == len(res.trades)
    assert np.prod(1 + y["net_return"]) - 1 == approx(res.net_return)        # years compound to the total
    assert y["net_pnl"].sum() == approx(res.trades["net"].sum())
    bh = M.buy_and_hold(df, "BTCUSDT", start="2022-12-01")
    assert np.prod(1 + y["bh_return"]) - 1 == approx(bh.stats["net_return"])
    g = M.regime_split(res, df)
    assert g["bars"].sum() == len(res.equity) and g["share_of_bars"].sum() == approx(1.0)
    assert g["trades"].sum() == len(res.trades) and g["net_pnl"].sum() == approx(res.trades["net"].sum())
    assert np.prod(1 + g["strat_return"]) - 1 == approx(res.net_return)      # regimes compound to the total
    assert np.prod(1 + g["bh_return"]) - 1 == approx(bh.stats["net_return"])


def test_segment_return():
    eq = _eq([1000, 1100, 1210, 1331], freq="1D")
    assert M.segment_return(eq, eq.index[0], eq.index[1], 1000.0) == approx(0.10)
    assert M.segment_return(eq, eq.index[2], eq.index[3], 1000.0) == approx(1331 / 1100 - 1)
