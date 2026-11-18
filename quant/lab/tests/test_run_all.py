"""run_all: discovery rules and tolerance to broken strategy modules."""
import textwrap

import pandas as pd
import pytest

from lab import run_all as RA

GOOD = '''
    import numpy as np, pandas as pd
    NAME = "good_one"; TIMEFRAME = "4h"; REGIME = "trending"; ONE_LINER = "test module"
    DEFAULT_PARAMS = {"n": 30}
    def signals(df, p, ctx=None):
        ma = df["close"].rolling(p["n"]).mean()
        atr = (df["high"] - df["low"]).rolling(14).mean()
        up = (df["close"] > ma) & (df["close"].shift(1) <= ma.shift(1))
        side = np.where(up & atr.notna(), 1, 0)
        return pd.DataFrame({"side": side, "entry": np.nan, "entry_valid": 1, "stop": df["close"] - 2 * atr,
                             "target": df["close"] + 3 * atr, "max_hold": 20, "exit": False}, index=df.index)
'''
TWO_ARGS = GOOD.replace('"good_one"', '"two_args"').replace("def signals(df, p, ctx=None):", "def signals(df, p):")
BOOM = '''
    NAME = "boom"; TIMEFRAME = "1h"; REGIME = "ranging"; ONE_LINER = ""; DEFAULT_PARAMS = {}
    def signals(df, p, ctx=None):
        raise RuntimeError("kaboom in signals")
'''
NO_STOP = '''
    import pandas as pd
    NAME = "no_stop"; TIMEFRAME = "1h"; REGIME = "ranging"; ONE_LINER = ""; DEFAULT_PARAMS = {}
    def signals(df, p, ctx=None):
        return pd.DataFrame({"side": 1}, index=df.index)
'''
NO_SIGNALS_FN = 'NAME = "empty"; TIMEFRAME = "1h"; DEFAULT_PARAMS = {}\n'
SYNTAX = "def signals(df, p, ctx=None)\n    return 1\n"


@pytest.fixture
def strat_dir(tmp_path):
    d = tmp_path / "fake_strategies"
    d.mkdir()
    for name, src in (("good", GOOD), ("two_args", TWO_ARGS), ("boom", BOOM), ("no_stop", NO_STOP),
                      ("no_fn", NO_SIGNALS_FN), ("syntax_error", SYNTAX), ("_private", BOOM), ("data", BOOM)):
        (d / f"{name}.py").write_text(textwrap.dedent(src))
    (d / "__init__.py").write_text("")
    (d / "notes.txt").write_text("not a module")
    return d


def test_discover_skips_underscore_names_and_data(strat_dir):
    assert RA.discover(strat_dir) == ["boom", "good", "no_fn", "no_stop", "syntax_error", "two_args"]
    real = RA.discover()
    assert "data" not in real and not any(n.startswith("_") for n in real)


def test_run_all_survives_broken_modules_and_writes_outputs(strat_dir, tmp_path):
    out = tmp_path / "out"
    s = RA.run_all(strat_dir, out, symbols=["BTCUSDT", "SOLUSDT"], start="2024-01-01", end="2024-06-30 23:59",
                   verbose=False)
    assert len(s) == 12                                             # 6 modules x 2 symbols, none skipped
    status = s.set_index(["module", "symbol"])["status"]
    assert (status.loc["good"] == "ok").all() and (status.loc["two_args"] == "ok").all()
    for bad in ("boom", "no_stop", "no_fn", "syntax_error"):
        assert (status.loc[bad] == "error").all()
    err = s.set_index(["module", "symbol"])["error"]
    assert "kaboom in signals" in err.loc[("boom", "BTCUSDT")]
    assert "stop" in err.loc[("no_stop", "BTCUSDT")]
    assert err.loc[("syntax_error", "SOLUSDT")].startswith("import: SyntaxError")
    assert "signals" in err.loc[("no_fn", "BTCUSDT")]

    disk = pd.read_csv(out / "summary.csv")
    assert len(disk) == 12 and list(disk.columns[:7]) == ["module", "name", "symbol", "timeframe", "regime_tag",
                                                          "status", "error"]
    ok = disk[disk["status"] == "ok"].set_index(["module", "symbol"])
    for col in ("net_return", "cagr", "max_dd", "sharpe", "profit_factor", "win_rate", "avg_R", "trades",
                "longest_losing_streak", "worst_month", "exposure", "fees_paid", "funding_paid", "slippage_paid",
                "bh_net_return", "bh_max_dd", "excess_vs_bh", "dev_net_return"):
        assert col in disk.columns and ok[col].notna().all(), col
    assert ok["holdout_net_return"].isna().all()                    # this window ends before the holdout
    g = ok.loc[("good", "BTCUSDT")]
    assert g["name"] == "good_one" and g["timeframe"] == "4h" and g["trades"] > 0
    assert g["window_start"].startswith("2024-01-01") and g["window_end"].startswith("2024-06-30 20:00")

    trades = pd.read_csv(out / "trades" / "good__BTCUSDT.csv")
    assert len(trades) == g["trades"] and trades["net"].sum() == pytest.approx(1000 * g["net_return"])
    assert (out / "trades" / "two_args__SOLUSDT.csv").exists()
    assert not (out / "trades" / "boom__BTCUSDT.csv").exists()
    eq = pd.read_csv(out / "equity" / "good__BTCUSDT.csv")
    assert {"equity", "bh_equity"} <= set(eq.columns) and len(eq) == 182          # daily, 1 Jan .. 30 Jun 2024
    assert (out / "yearly.csv").exists() and (out / "regime.csv").exists()
    assert set(pd.read_csv(out / "regime.csv")["regime"]) <= {"bull", "bear", "sideways", "unknown"}


def test_cli_runs_and_reports(strat_dir, tmp_path, capsys):
    rc = RA.main(["--strategies", str(strat_dir), "--out", str(tmp_path / "o"), "--symbols", "ETHUSDT",
                  "--start", "2025-01-01", "--end", "2025-01-31 23:59"])
    assert rc == 0
    printed = capsys.readouterr().out
    assert "6 runs (2 ok, 4 failed)" in printed and "by chance" in printed
    assert (tmp_path / "o" / "summary.csv").exists()


def test_empty_strategy_dir_writes_header_only(tmp_path):
    d = tmp_path / "none"
    d.mkdir()
    (d / "data.py").write_text("x = 1\n")
    s = RA.run_all(d, tmp_path / "o2", verbose=False)
    assert len(s) == 0 and "net_return" in pd.read_csv(tmp_path / "o2" / "summary.csv").columns
