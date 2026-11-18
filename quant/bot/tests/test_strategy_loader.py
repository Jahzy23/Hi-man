"""Strategy loading, the SPEC contract checks, and the column-dependency probe."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.strategy_loader import StrategyError, evaluate, extract, load_strategy, probe, run_signals

from .conftest import synth_frame
from .harness import STRATEGIES

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


@pytest.fixture
def sdir(tmp_path):
    for name, code in STRATEGIES.items():
        (tmp_path / f"{name}.py").write_text(code)
    return tmp_path


def write(sdir, name, body, tf="1h", extra=""):
    (sdir / f"{name}.py").write_text(
        f'import numpy as np, pandas as pd\nNAME="{name}"; TIMEFRAME="{tf}"; REGIME="x"; ONE_LINER="x"\n'
        f'DEFAULT_PARAMS = {{"a": 1}}\n{extra}\ndef signals(df, p, ctx=None):\n{body}\n')
    return sdir


def frames():
    return {s: synth_frame(n=400, seed=i + 1) for i, s in enumerate(SYMS)}


def test_reference_strategy_loads_from_the_strategies_package_by_name():
    s = load_strategy("_reference_breakout", {"lookback": 10})
    assert s.tf == "1h" and s.warmup == 100 and s.params["lookback"] == 10 and s.params["atr_n"] == 14
    out = run_signals(s, synth_frame(), None)
    assert list(out.columns) == ["side", "entry", "entry_valid", "stop", "target", "max_hold", "exit"]
    assert set(out["side"].unique()) <= {-1, 0, 1} and (out["side"] != 0).sum() > 5
    sig = out[out["side"] != 0]
    assert sig["stop"].notna().all() and sig["entry"].isna().all()              # market entries by default
    df = synth_frame().loc[sig.index]
    assert ((sig["stop"] < df["close"]) == (sig["side"] > 0)).all()             # stop on the right side
    assert ((sig["target"] > df["close"]) == (sig["side"] > 0)).all()
    lim = run_signals(load_strategy("_reference_breakout", {"entry": "limit"}), synth_frame(), None)
    ls = lim[lim["side"] != 0]
    assert ls["entry"].notna().all() and ((ls["entry"] < synth_frame().loc[ls.index, "close"]) == (ls["side"] > 0)).all()


def test_reference_strategy_has_no_lookahead():
    """SPEC: signals(df.iloc[:k]) must equal signals(df).iloc[:k] row for row."""
    s = load_strategy("_reference_breakout", {"entry": "limit"})
    df = synth_frame(n=500)
    full = run_signals(s, df, None)
    for k in (60, 137, 301, 499):
        pd.testing.assert_frame_equal(run_signals(s, df.iloc[:k], None), full.iloc[:k])


@pytest.mark.parametrize("name", ["", "../etc/passwd", "a-b", "a.b", "1abc", "a b"])
def test_names_that_are_not_module_names_are_rejected(name):
    with pytest.raises(StrategyError, match="not a module name"):
        load_strategy(name)


def test_missing_and_malformed_modules(sdir):
    with pytest.raises(StrategyError, match="not found"):
        load_strategy("does_not_exist_anywhere")
    with pytest.raises(StrategyError, match="not found"):
        load_strategy("nope", search_dir=sdir)
    write(sdir, "bad_tf", "    return df", tf="15m")
    with pytest.raises(StrategyError, match="TIMEFRAME"):
        load_strategy("bad_tf", search_dir=sdir)
    (sdir / "no_fn.py").write_text('TIMEFRAME="1h"\nDEFAULT_PARAMS={}\n')
    with pytest.raises(StrategyError, match="signals"):
        load_strategy("no_fn", search_dir=sdir)
    (sdir / "no_params.py").write_text('TIMEFRAME="1h"\ndef signals(df, p, ctx=None): return df\n')
    with pytest.raises(StrategyError, match="DEFAULT_PARAMS"):
        load_strategy("no_params", search_dir=sdir)
    write(sdir, "bad_warm", "    return df", extra="WARMUP_BARS = 0")
    with pytest.raises(StrategyError, match="WARMUP_BARS"):
        load_strategy("bad_warm", search_dir=sdir)
    with pytest.raises(StrategyError, match="not in DEFAULT_PARAMS"):
        load_strategy("always", {"typo_param": 3}, search_dir=sdir)
    assert load_strategy("always", {"side": -1}, default_warmup=77, search_dir=sdir).warmup == 50
    assert load_strategy("declares_oi", search_dir=sdir).warmup == 300          # module default when absent


def test_contract_violations_in_the_output_are_errors(sdir):
    df = synth_frame(n=100)
    write(sdir, "ret_none", "    return None")
    write(sdir, "short_out", "    return pd.DataFrame({'side': 0}, index=df.index[:-1])")
    write(sdir, "no_side", "    return pd.DataFrame({'stop': 1.0}, index=df.index)")
    write(sdir, "side2", "    return pd.DataFrame({'side': 2, 'stop': 1.0}, index=df.index)")
    write(sdir, "side_half", "    return pd.DataFrame({'side': 0.5, 'stop': 1.0}, index=df.index)")
    for name, msg in (("ret_none", "not a DataFrame"), ("short_out", "same index"), ("no_side", "no 'side'"),
                      ("side2", "side must be"), ("side_half", "side must be")):
        with pytest.raises(StrategyError, match=msg):
            evaluate(load_strategy(name, search_dir=sdir), df, None)


def test_decision_is_the_last_row_with_spec_defaults(sdir):
    df = synth_frame(n=100)
    write(sdir, "minimal", "    return pd.DataFrame({'side': 1, 'stop': df['close'] * 0.99}, index=df.index)")
    d = evaluate(load_strategy("minimal", search_dir=sdir), df, None)
    assert d.bar_ms == int(df.index[-1].timestamp() * 1000) and d.side == 1
    assert d.entry is None and d.entry_valid == 1 and d.target is None and d.max_hold is None and d.exit is False
    assert d.stop == pytest.approx(df["close"].iloc[-1] * 0.99)
    write(sdir, "nan_side", "    return pd.DataFrame({'side': np.nan, 'stop': np.nan, 'exit': np.nan, "
                            "'max_hold': 0.0, 'entry_valid': np.nan, 'entry': 'x'}, index=df.index)")
    d = evaluate(load_strategy("nan_side", search_dir=sdir), df, None)
    assert d.side == 0 and d.stop is None and d.exit is False and d.max_hold is None and d.entry_valid == 1 and d.entry is None
    out = run_signals(load_strategy("always", {"max_hold": 7, "entry_valid": 3, "limit_pct": 0.01}, search_dir=sdir), df, None)
    d = extract(load_strategy("always", search_dir=sdir), out, 10)
    assert d.bar_ms == int(df.index[10].timestamp() * 1000) and d.max_hold == 7 and d.entry_valid == 3
    assert d.entry == pytest.approx(df["close"].iloc[10] * 0.99)


def test_a_strategy_that_mutates_its_inputs_cannot_corrupt_history(sdir):
    df = synth_frame(n=100)
    before = df.copy()
    ctx = {"BTCUSDT": df}
    write(sdir, "vandal", "    df['close'] = 0.0\n    df.drop(df.index[:5], inplace=True)\n"
                          "    ctx['BTCUSDT']['high'] = -1.0\n    p['a'] = 99\n"
                          "    return pd.DataFrame({'side': 0, 'stop': np.nan}, index=ctx['BTCUSDT'].index)")
    s = load_strategy("vandal", search_dir=sdir)
    run_signals(s, df, ctx)
    pd.testing.assert_frame_equal(df, before)
    assert s.params == {"a": 1}


def test_probe_finds_the_columns_a_strategy_reads(sdir):
    f = frames()
    assert probe(load_strategy("always", search_dir=sdir), "BTCUSDT", f) == ({}, False)
    needs, ctx = probe(load_strategy("needs_oi", search_dir=sdir), "BTCUSDT", f)
    assert set(needs) == {"oi"} and "removed" in needs["oi"] and not ctx
    needs, _ = probe(load_strategy("needs_funding", search_dir=sdir), "ETHUSDT", f)
    assert set(needs) == {"funding_rate"}
    needs, _ = probe(load_strategy("declares_oi", search_dir=sdir), "BTCUSDT", f)
    assert needs == {"oi_value": "declared in REQUIRED_COLUMNS"}
    assert probe(load_strategy("_reference_breakout"), "SOLUSDT", f) == ({}, False)


def test_probe_catches_optional_and_wholesale_column_use(sdir):
    f = frames()
    # reads the column only if it is there
    write(sdir, "optional", "    s = (df['oi'].diff() > 0).astype(int) if 'oi' in df.columns else 0\n"
                            "    return pd.DataFrame({'side': s, 'stop': df['close'] * 0.99}, index=df.index)")
    needs, _ = probe(load_strategy("optional", search_dir=sdir), "BTCUSDT", f)
    assert set(needs) == {"oi"} and "removed" in needs["oi"]
    # drops every row with a NaN anywhere: live NaNs would silently mute it
    write(sdir, "dropna", "    ok = df.notna().all(axis=1)\n"
                          "    return pd.DataFrame({'side': ok.astype(int), 'stop': df['close'] * 0.99}, index=df.index)")
    needs, _ = probe(load_strategy("dropna", search_dir=sdir), "BTCUSDT", f)
    assert set(needs) == {"funding_paid", "funding_rate", "oi", "oi_value", "top_ls_ratio", "acct_ls_ratio"}
    assert all("blank" in v for v in needs.values())
    # uses only the level of a column: removing fails loudly
    write(sdir, "level", "    return pd.DataFrame({'side': (df['top_ls_ratio'] > 1.7).astype(int), "
                         "'stop': df['close'] * 0.99}, index=df.index)")
    needs, _ = probe(load_strategy("level", search_dir=sdir), "BTCUSDT", f)
    assert set(needs) == {"top_ls_ratio"} and "KeyError" in needs["top_ls_ratio"]


def test_probe_detects_use_of_other_symbols(sdir):
    f = frames()
    _, ctx = probe(load_strategy("uses_btc", search_dir=sdir), "ETHUSDT", f)
    assert ctx
    # reads another symbol's OI through ctx
    write(sdir, "ctx_oi", "    o = ctx['BTCUSDT']['oi'].reindex(df.index) if ctx else pd.Series(0.0, index=df.index)\n"
                          "    return pd.DataFrame({'side': (o.diff() > 0).astype(int), 'stop': df['close'] * 0.99}, "
                          "index=df.index)")
    needs, ctx = probe(load_strategy("ctx_oi", search_dir=sdir), "ETHUSDT", f)
    assert set(needs) == {"oi"} and ctx
    # a strategy that needs ctx to exist at all
    write(sdir, "ctx_req", "    b = ctx['BTCUSDT']\n"
                           "    return pd.DataFrame({'side': 0, 'stop': np.nan}, index=df.index)")
    assert probe(load_strategy("ctx_req", search_dir=sdir), "ETHUSDT", f)[1] is True


def test_probe_reports_a_strategy_that_cannot_run_on_archive_data(sdir):
    write(sdir, "broken", "    raise ValueError('nope')")
    with pytest.raises(StrategyError, match="failed on archive data"):
        probe(load_strategy("broken", search_dir=sdir), "BTCUSDT", frames())
    write(sdir, "wrong_shape", "    return 5")
    with pytest.raises(StrategyError, match="not a DataFrame"):
        probe(load_strategy("wrong_shape", search_dir=sdir), "BTCUSDT", frames())
    assert np.isfinite(1.0)
