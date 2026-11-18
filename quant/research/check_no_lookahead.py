"""Radar: structural checks for every strategy module. No P&L is computed anywhere in this file.

Run from quant/:   python research/check_no_lookahead.py
Exit code 0 only if every strategy passes every hard check on every symbol.

Hard checks (per strategy x symbol)
  meta    NAME/TIMEFRAME/REGIME/ONE_LINER/WHY/DEFAULT_PARAMS/PARAM_GRID/WARMUP_BARS are present and sane
  run     signals(df, DEFAULT_PARAMS, ctx) runs, returns the SPEC columns on df's index, leaves df untouched
  valid   wherever side != 0: stop is a number on the losing side of the entry reference (limit price, or
          the signal bar's close for market entries); limit entries are passive; targets on the winning side
  prefix  signals(df.iloc[:k]) == signals(df).iloc[:k], NaN-aware and EXACT, for
            - fixed cuts spread over the history,
            - cuts placed right after sampled signal rows and exit rows (k = i + 1: the signal on the last
              row of the truncated frame must already be there, i.e. it used rows <= i only),
            - 150 consecutive cuts (every k) so every hour-of-day / day-boundary alignment is hit,
          with ctx truncated to the same last timestamp, and once more with ctx left untruncated
  warmup  signals on a window that starts WARMUP_BARS before a test stretch reproduce the full-history
          result on that stretch (side/exit exact, prices to 1e-9). This is what the live bot relies on.
  grid    every PARAM_GRID combination (full cartesian product, which is how Lab expands it) runs and
          passes `valid`; the combination with the largest value of every key also passes the warm-up
          check and a prefix check on the fixed cuts
  columns REQUIRED_COLUMNS is exact: the output is identical when the frame (and ctx) holds only those
          columns, and removing any one of them breaks or changes the output. The bot uses this list
          to refuse a setup whose inputs are not known live.

Reported, not pass/fail
  signal counts inside the backtest window, runtime, and how often a market entry's stop is already
  beyond the NEXT bar's open (a gap through the stop before the order could fill).

A self-test feeds three deliberately leaky strategies through the same prefix check; the script
refuses to report success unless all three are caught.
"""
import importlib
import inspect
import itertools
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategies.data import load, SYMBOLS, BACKTEST_START, BACKTEST_END  # noqa: E402

OUT = ROOT / "research"
REQUIRED = ["side", "entry", "entry_valid", "stop", "target", "max_hold", "exit"]
REGIMES = {"trending", "ranging", "volatile"}
SKIP = {"__init__", "data"}
N_FIXED, N_SIG, N_EXIT, N_DENSE = 8, 12, 6, 150
FAMILY_LABEL = {
    "baseline": "boring baseline",
    "range_breakout_volume": "range breakout on rising volume",
    "trend_pullback": "trend pullback to support",
    "liquidation_flush_reversal": "reversal after liquidation flush",
    "funding_extremes": "funding extreme",
    "oi_divergence": "open-interest divergence",
    "session_open": "session open",
    "btc_lead_lag": "ETH/SOL lagging BTC",
}

_frames = {}


def frame(sym, tf):
    if (sym, tf) not in _frames:
        _frames[(sym, tf)] = load(sym, tf)
    return _frames[(sym, tf)]


def discover():
    names = sorted(f.stem for f in (ROOT / "strategies").glob("*.py") if f.stem not in SKIP and not f.stem.startswith("_"))
    return [importlib.import_module(f"strategies.{n}") for n in names]


def cut_ctx(ctx, last_ts):
    return {k: v.loc[:last_ts] for k, v in ctx.items()}


def same(a, b, rtol=0.0):
    """NaN-aware equality. rtol=0 means exact. Returns (ok, message)."""
    if list(a.columns) != list(b.columns):
        return False, f"columns differ {list(a.columns)} vs {list(b.columns)}"
    if not a.index.equals(b.index):
        return False, "index differs"
    for col in a.columns:
        x, y = a[col].to_numpy(), b[col].to_numpy()
        if x.dtype.kind == "f" or y.dtype.kind == "f":
            x, y = x.astype(float), y.astype(float)
            eq = (np.isnan(x) & np.isnan(y)) | (x == y)
            if rtol and col in ("entry", "stop", "target"):
                eq |= np.isclose(x, y, rtol=rtol, atol=0.0)
        else:
            eq = x == y
        if not eq.all():
            i = int(np.argmin(eq))
            return False, f"{col} differs at {a.index[i]} ({x[i]!r} vs {y[i]!r}); {int((~eq).sum())} rows"
    return True, ""


def validity(df, out):
    """Structural validity of one output frame. Returns list of problems (empty = ok)."""
    bad = []
    miss = [c for c in REQUIRED if c not in out.columns]
    if miss:
        return [f"missing columns {miss}"]
    if not out.index.equals(df.index):
        return ["output index is not the input index"]
    side = out["side"]
    if not side.isin([-1, 0, 1]).all():
        bad.append("side outside {-1,0,1}")
    if out["exit"].dtype != bool:
        bad.append(f"exit dtype {out['exit'].dtype}, expected bool")
    ev = out["entry_valid"]
    if ev.isna().any() or (ev < 1).any() or (ev != ev.astype(int)).any():
        bad.append("entry_valid must be an int >= 1")
    mh = out["max_hold"].dropna()
    if (mh < 1).any() or (mh != mh.round()).any():
        bad.append("max_hold must be NaN or a whole number >= 1")
    on = side != 0
    if on.any():
        s, stop, entry, target = side[on], out["stop"][on], out["entry"][on], out["target"][on]
        close = df["close"][on]
        if not np.isfinite(stop).all():
            bad.append(f"{int((~np.isfinite(stop)).sum())} signals without a stop")
        ref = entry.fillna(close)
        wrong = ((s == 1) & ~(stop < ref)) | ((s == -1) & ~(stop > ref))
        if wrong.any():
            bad.append(f"{int(wrong.sum())} stops on the wrong side of the entry")
        aggressive = entry.notna() & (((s == 1) & (entry > close)) | ((s == -1) & (entry < close)))
        if aggressive.any():
            bad.append(f"{int(aggressive.sum())} limit entries through the market")
        tw = target.notna() & (((s == 1) & ~(target > ref)) | ((s == -1) & ~(target < ref)))
        if tw.any():
            bad.append(f"{int(tw.sum())} targets on the wrong side of the entry")
        if (stop <= 0).any() or (entry.dropna() <= 0).any() or (target.dropna() <= 0).any():
            bad.append("non-positive price level")
    off = ~on
    if out["stop"][off].notna().any() or out["entry"][off].notna().any() or out["target"][off].notna().any():
        bad.append("price levels set on rows without a signal")
    return bad


def prefix_cuts(n, out, warmup):
    """Cut points k (frame = rows [0, k)). Fixed spread + right after sampled signal / exit rows."""
    fixed = [min(n - 1, max(warmup + 2, int(n * f) + j)) for f, j in
             zip((0.12, 0.27, 0.41, 0.55, 0.68, 0.79, 0.90, 0.97), (1, 5, 11, 2, 7, 3, 13, 9))]
    fixed += [n - 1]
    sig = np.flatnonzero(out["side"].to_numpy() != 0)
    ex = np.flatnonzero(out["exit"].to_numpy())
    pick = lambda a, m: [] if len(a) == 0 else list(a[np.unique(np.linspace(0, len(a) - 1, min(m, len(a))).astype(int))])
    after_sig = [int(i) + 1 for i in pick(sig, N_SIG)]
    after_exit = [int(i) + 1 for i in pick(ex, N_EXIT)]
    return sorted(set(k for k in fixed + after_sig + after_exit if 2 <= k <= n)), len(after_sig), len(after_exit)


def check_prefix(mod, df, ctx, out, params):
    n = len(df)
    cuts, n_sig, n_exit = prefix_cuts(n, out, mod.WARMUP_BARS)
    fails = []
    for k in cuts:
        sub = df.iloc[:k]
        got = mod.signals(sub, dict(params), cut_ctx(ctx, sub.index[-1]))
        ok, msg = same(got, out.iloc[:k])
        if not ok:
            fails.append(f"k={k}: {msg}")
    # ctx NOT truncated: a strategy must not read leader rows beyond its own last row
    k = cuts[len(cuts) // 2]
    got = mod.signals(df.iloc[:k], dict(params), ctx)
    ok, msg = same(got, out.iloc[:k])
    if not ok:
        fails.append(f"k={k} with full ctx: {msg}")
    # consecutive cuts on a recent sub-frame: every hour-of-day / bar-of-day alignment
    end = int(df.index.searchsorted(pd.Timestamp(BACKTEST_END, tz="UTC"), side="right"))
    sub = df.iloc[max(0, end - mod.WARMUP_BARS - 400): end]
    sctx = cut_ctx(ctx, sub.index[-1])
    base = mod.signals(sub, dict(params), sctx)
    dense = range(len(sub) - N_DENSE, len(sub))            # > 6 days of 1h bars / 25 days of 4h bars, every k
    for k in dense:
        got = mod.signals(sub.iloc[:k], dict(params), cut_ctx(sctx, sub.index[k - 1]))
        ok, msg = same(got, base.iloc[:k])
        if not ok:
            fails.append(f"dense k={k}: {msg}")
    return len(cuts) + 1 + len(dense), n_sig, n_exit, fails


def check_warmup(mod, df, ctx, out, params):
    """Tail windows starting WARMUP_BARS before a test stretch must reproduce the full-history output."""
    m = 2000 if mod.TIMEFRAME == "1h" else 1000
    end = int(df.index.searchsorted(pd.Timestamp(BACKTEST_END, tz="UTC"), side="right"))
    fails, rows = [], 0
    for e in (end, end - (9000 if mod.TIMEFRAME == "1h" else 2300), end - (20000 if mod.TIMEFRAME == "1h" else 5000)):
        a = e - m - mod.WARMUP_BARS
        if a < 0:
            continue
        win = df.iloc[a:e]
        got = mod.signals(win, dict(params), {k: v.loc[win.index[0]: win.index[-1]] for k, v in ctx.items()})
        ok, msg = same(got.iloc[-m:], out.iloc[e - m: e], rtol=1e-9)
        rows += m
        if not ok:
            fails.append(f"window ending {df.index[e - 1]}: {msg}")
    return rows, fails


def check_meta(mod, fname):
    bad = []
    for a in ("NAME", "TIMEFRAME", "REGIME", "ONE_LINER", "WHY", "DEFAULT_PARAMS", "PARAM_GRID", "WARMUP_BARS",
              "REQUIRED_COLUMNS", "signals"):
        if not hasattr(mod, a):
            bad.append(f"missing {a}")
    if bad:
        return bad
    if mod.NAME != fname:
        bad.append(f"NAME {mod.NAME!r} != file name {fname!r}")
    if mod.TIMEFRAME not in ("1h", "4h"):
        bad.append("TIMEFRAME must be 1h or 4h")
    if mod.REGIME not in REGIMES:
        bad.append("REGIME must be trending|ranging|volatile")
    for a in ("ONE_LINER", "WHY"):
        if not isinstance(getattr(mod, a), str) or len(getattr(mod, a)) < 20:
            bad.append(f"{a} too short")
    if not isinstance(mod.DEFAULT_PARAMS, dict):
        bad.append("DEFAULT_PARAMS must be a dict")
    if not isinstance(mod.WARMUP_BARS, int) or mod.WARMUP_BARS < 1:
        bad.append("WARMUP_BARS must be a positive int")
    if not isinstance(mod.PARAM_GRID, dict) or not mod.PARAM_GRID:
        bad.append("PARAM_GRID must be a non-empty dict")
    else:
        for k, vals in mod.PARAM_GRID.items():
            if k not in mod.DEFAULT_PARAMS:
                bad.append(f"PARAM_GRID key {k} not in DEFAULT_PARAMS")
            elif not (2 <= len(vals) <= 3):
                bad.append(f"PARAM_GRID[{k}] should hold 2-3 values")
            elif mod.DEFAULT_PARAMS[k] not in vals:
                bad.append(f"PARAM_GRID[{k}] does not contain the default")
    if list(inspect.signature(mod.signals).parameters)[:3] != ["df", "p", "ctx"]:
        bad.append("signals signature must be (df, p, ctx=None)")
    return bad


def fingerprint(df):
    return int(pd.util.hash_pandas_object(df, index=True).sum())


def check_one(mod, sym):
    tf = mod.TIMEFRAME
    df = frame(sym, tf)
    ctx = {"BTCUSDT": frame("BTCUSDT", tf)}
    r = {"symbol": sym, "problems": {}}
    fp = fingerprint(df)
    raw, orig = {}, getattr(mod, "_finish", None)
    if orig is not None:                              # count what the module's own safety net throws away
        def spy(d, side, *a, **k):
            raw["n"] = int((pd.Series(side, index=d.index).fillna(0) != 0).sum())
            return orig(d, side, *a, **k)
        mod._finish = spy
    t0 = time.perf_counter()
    try:
        out = mod.signals(df, dict(mod.DEFAULT_PARAMS), ctx)
    except Exception as e:                                             # noqa: BLE001
        r["problems"]["run"] = [f"{type(e).__name__}: {e}"]
        return r
    finally:
        if orig is not None:
            mod._finish = orig
    r["runtime_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    r["signals_dropped_by_safety_net"] = raw.get("n", int((out["side"] != 0).sum())) - int((out["side"] != 0).sum()) \
        if "side" in out else None
    if fingerprint(df) != fp:
        r["problems"]["run"] = ["signals() modified its input frame"]
    v = validity(df, out)
    if v:
        r["problems"]["valid"] = v
        if any("missing" in x or "index" in x for x in v):
            return r
    n_cuts, n_sig, n_exit, pf = check_prefix(mod, df, ctx, out, mod.DEFAULT_PARAMS)
    r["prefix_cuts"], r["prefix_cuts_after_signal"], r["prefix_cuts_after_exit"] = n_cuts, n_sig, n_exit
    if pf:
        r["problems"]["prefix"] = pf[:5]
    rows, wf = check_warmup(mod, df, ctx, out, mod.DEFAULT_PARAMS)
    r["warmup_rows_compared"] = rows
    if wf:
        r["problems"]["warmup"] = wf
    gf, n_grid = [], 0
    keys = list(mod.PARAM_GRID)
    for vals in itertools.product(*(mod.PARAM_GRID[k] for k in keys)):
        combo = dict(zip(keys, vals))
        if all(mod.DEFAULT_PARAMS.get(k) == v for k, v in combo.items()):
            continue
        n_grid += 1
        try:
            gv = validity(df, mod.signals(df, {**mod.DEFAULT_PARAMS, **combo}, ctx))
            if gv:
                gf.append(f"{combo}: {gv[0]}")
        except Exception as e:                                         # noqa: BLE001
            gf.append(f"{combo}: {type(e).__name__}: {e}")
    big = {**mod.DEFAULT_PARAMS, **{k: max(v) for k, v in mod.PARAM_GRID.items()}}   # longest look-backs
    try:
        big_out = mod.signals(df, dict(big), ctx)
        _, wf_big = check_warmup(mod, df, ctx, big_out, big)
        gf += [f"largest combo, warm-up: {m}" for m in wf_big]
        for k in prefix_cuts(len(df), big_out, mod.WARMUP_BARS)[0][:N_FIXED + 1]:
            sub = df.iloc[:k]
            ok, msg = same(mod.signals(sub, dict(big), cut_ctx(ctx, sub.index[-1])), big_out.iloc[:k])
            if not ok:
                gf.append(f"largest combo, prefix k={k}: {msg}")
    except Exception as e:                                             # noqa: BLE001
        gf.append(f"largest combo: {type(e).__name__}: {e}")
    r["grid_variants"] = n_grid
    if gf:
        r["problems"]["grid"] = gf[:5]
    # ---- declared columns: sufficient and necessary ----
    cf, cols = [], list(mod.REQUIRED_COLUMNS)
    if [c for c in cols if c not in df.columns]:
        cf.append(f"unknown columns declared: {[c for c in cols if c not in df.columns]}")
    else:
        slim_ctx = {k: v[cols] for k, v in ctx.items()}
        try:
            ok, msg = same(mod.signals(df[cols], dict(mod.DEFAULT_PARAMS), slim_ctx), out)
            if not ok:
                cf.append(f"output changes when only REQUIRED_COLUMNS are supplied: {msg}")
        except Exception as e:                                         # noqa: BLE001
            cf.append(f"fails with only REQUIRED_COLUMNS: {type(e).__name__}: {e}")
        for c in cols:
            keep = [x for x in cols if x != c]
            try:
                ok, _ = same(mod.signals(df[keep], dict(mod.DEFAULT_PARAMS), {k: v[keep] for k, v in ctx.items()}), out)
                if ok and (out["side"] != 0).any():
                    cf.append(f"{c} is declared but the output does not depend on it")
            except Exception:                                          # noqa: BLE001
                pass                                                   # breaks without it: genuinely required
    if cf:
        r["problems"]["columns"] = cf[:5]
    # ---- counts only (no prices followed forward, no returns) ----
    w = out.loc[BACKTEST_START:BACKTEST_END]
    r["signals_window"] = int((w["side"] != 0).sum())
    r["signals_long"] = int((w["side"] == 1).sum())
    r["signals_short"] = int((w["side"] == -1).sum())
    r["signals_limit"] = int(w["entry"].notna().sum())
    r["exit_flags_window"] = int(w["exit"].sum())
    r["signals_after_window"] = int((out.loc[BACKTEST_END:]["side"] != 0).sum())
    r["signals_before_window"] = int((out.loc[:BACKTEST_START]["side"] != 0).sum())
    # market entries whose stop is already beyond the next bar's open
    nxt = df["open"].shift(-1)                       # used by the CHECKER only, never by a strategy
    mk = (out["side"] != 0) & out["entry"].isna() & nxt.notna()
    gap = mk & (((out["side"] == 1) & (out["stop"] >= nxt)) | ((out["side"] == -1) & (out["stop"] <= nxt)))
    r["stop_beyond_next_open"] = int(gap.loc[BACKTEST_START:BACKTEST_END].sum())
    dist = ((out["stop"] - out["entry"].fillna(df["close"])).abs() / df["close"])[out["side"] != 0]
    dist = dist.loc[BACKTEST_START:BACKTEST_END]
    r["median_stop_distance_pct"] = round(float(dist.median() * 100), 2) if len(dist) else None
    # SPEC cost of a trade that is stopped out, in units of the risked amount (1R = entry-to-stop distance):
    # entry = taker 5bp + slippage (or maker 2bp for a limit), stop exit = taker 5bp + 2x slippage.
    slip = 2.0 if sym.startswith("SOL") else 1.0
    is_limit = bool(len(w) and w["entry"].notna().sum() == (w["side"] != 0).sum() and (w["side"] != 0).any())
    cost_bp = (2.0 if is_limit else 5.0 + slip) + 5.0 + 2.0 * slip
    r["cost_stopped_trade_bp"] = cost_bp
    r["cost_stopped_trade_pct_of_1R"] = round(cost_bp / 100.0 / (dist.median() * 100) * 100, 1) if len(dist) else None
    # position size = 0.5% equity / stop distance, capped at 3x equity: the cap binds below 0.1667% distance
    r["share_signals_hitting_3x_cap_pct"] = round(float((dist < 0.005 / 3).mean() * 100), 1) if len(dist) else None
    return r


# ---------------------------------------------------------------- self-test of the checker itself
def _leaky(kind):
    def signals(df, p, ctx=None):
        c = df["close"]
        if kind == "shift_minus_1":
            cond = c.shift(-1) > c                                   # peeks one bar ahead
        elif kind == "centered_window":
            cond = c > c.rolling(21, center=True).mean()             # centred window
        else:
            cond = c > c.mean()                                      # full-sample normalisation
        side = cond.fillna(False).astype(int)
        return pd.DataFrame({"side": side, "entry": np.nan, "entry_valid": 1,
                             "stop": (c * 0.97).where(side != 0), "target": np.nan,
                             "max_hold": 10.0, "exit": False}, index=df.index)
    return SimpleNamespace(signals=signals, TIMEFRAME="4h", WARMUP_BARS=50, DEFAULT_PARAMS={})


def self_test():
    df = frame("BTCUSDT", "4h").iloc[-3000:]
    caught = 0
    for kind in ("shift_minus_1", "centered_window", "full_sample_mean"):
        m = _leaky(kind)
        out = m.signals(df, {}, None)
        _, _, _, fails = check_prefix(m, df, {}, out, {})
        caught += bool(fails)
    return caught


# ---------------------------------------------------------------- reporting
def md_table(header, rows):
    s = "| " + " | ".join(header) + " |\n|" + "|".join(["---"] * len(header)) + "|\n"
    return s + "\n".join("| " + " | ".join(str(c) for c in r) + " |" for r in rows) + "\n"


def write_ideas(results):
    L = ["# Setup ideas (untested hypotheses)\n",
         f"{len(results)} setups, one module each in `strategies/`. Generated by `research/check_no_lookahead.py` "
         "from the modules' own metadata, so this table cannot drift from the code.\n",
         "- **Nothing here has been backtested.** No P&L was computed and no parameter was tuned: values are textbook "
         "or round-number defaults. `PARAM_GRID` in each module lists 2-3 neighbouring values per key for Lab.",
         "- Expect most of these to fail after costs. That is the base rate for trading ideas and it is why Lab exists.",
         f"- **Signal count** = rows with `side != 0` inside the backtest window ({BACKTEST_START} to {BACKTEST_END} UTC), "
         "shown as BTC / ETH / SOL. It is a count of signals, not of trades: the engine holds one position per setup and "
         "symbol, so overlapping signals are skipped, and limit orders often do not fill. `tsmom` and "
         "`pullback_limit_sma` re-issue their signal every day / every bar by design, which inflates their count.",
         "- **Regime** = the market state the setup is meant to be switched on in (rule in `research/market_ranking.md`).",
         "- `exit` is side-agnostic everywhere it is used: it fires when the state that justified the entry ends "
         "(trend state changes, or an oscillator crosses its midpoint), so the same flag is correct for longs and shorts.",
         "- The three BTC lead-lag setups need `ctx['BTCUSDT']` and emit nothing for BTC itself (count 0 is correct).",
         "- Funding setups read `funding_paid` only. The funding archive stops at the end of the backtest window, so "
         "they are blind after it until the bot feeds live funding.",
         "- **Survivorship:** BTC, ETH and SOL were not delisted, but they were chosen today knowing they survived. "
         "Counts and any later test results on them say nothing about coins that died.\n"]
    fam_order = list(FAMILY_LABEL)
    res = sorted(results, key=lambda r: (fam_order.index(r["family"]) if r["family"] in fam_order else 99, r["name"]))
    rows = []
    for r in res:
        cnt = " / ".join(str(r["symbols"][s].get("signals_window", "err")) for s in SYMBOLS)
        rows.append([f"`{r['name']}`", FAMILY_LABEL.get(r["family"], r["family"]), r["timeframe"], r["regime"],
                     r["entry_type"], r["one_liner"], r["why"], r["risk"], cnt])
    L.append(md_table(["name", "family", "tf", "regime it needs", "entry", "one-line idea", "why it should work / who loses",
                       "how it most likely fails", "signals BTC / ETH / SOL"], rows))
    fam = {}
    for r in res:
        fam.setdefault(FAMILY_LABEL.get(r["family"], r["family"]), []).append(r["name"])
    L.append("\n## Coverage by family\n")
    L.append(md_table(["family", "setups", "modules"], [[k, len(v), ", ".join(f"`{x}`" for x in v)] for k, v in fam.items()]))

    # ---- notes for Lab (descriptive facts about the test window; no strategy returns) ----
    drift = []
    for sym in SYMBOLS:
        c = frame(sym, "1h")["close"].loc[BACKTEST_START:BACKTEST_END]
        drift.append(f"{sym} {c.iloc[0]:,.2f} -> {c.iloc[-1]:,.2f} ({(c.iloc[-1] / c.iloc[0] - 1) * 100:+.0f}%)")
    zero_oi = ", ".join(f"{sym} {int((frame(sym, '1h')['oi'] <= 0).sum())}" for sym in SYMBOLS)
    thin = [r["name"] for r in res if 0 < max(r["symbols"][s].get("signals_window", 0) for s in SYMBOLS) < 100]
    long_only = [r["name"] for r in res if sum(r["symbols"][s].get("signals_short", 0) for s in SYMBOLS) == 0]
    short_only = [r["name"] for r in res if sum(r["symbols"][s].get("signals_long", 0) for s in SYMBOLS) == 0]
    n_var = sum(int(np.prod([len(v) for v in r["param_grid"].values()])) for r in res)
    L += ["\n## Notes for Lab (read before testing)\n",
          f"1. **The window has a strong upward drift:** {'; '.join(drift)}. Anything long-biased will look good for "
          "that reason alone. Compare every setup with a same-side, same-holding-period random-entry baseline, not with zero. "
          f"Long-only modules: {', '.join(f'`{x}`' for x in long_only) or 'none'}. Short-only: {', '.join(f'`{x}`' for x in short_only) or 'none'}.",
          f"2. **Multiple testing:** {len(res)} setups x 3 symbols, and PARAM_GRID expands to {n_var} parameter combinations per symbol "
          "(cartesian product, as `lab/validate.py` reads it). "
          "Some will pass by luck. A setup should hold up on the neighbouring grid values and on more than one symbol, "
          "and BTC/ETH/SOL are highly correlated, so three symbols are far fewer than three independent samples.",
          f"3. **Thin samples** (under 100 signals on every symbol in four years): {', '.join(f'`{x}`' for x in thin)}. "
          "Whatever these show, the confidence interval will be wide.",
          "4. **Costs:** `research/lookahead_check.md` lists what a stopped trade costs in units of risk for each setup. "
          "The tight-stop 1h setups pay 10-25% of 1R per losing trade before any edge.",
          f"5. **Open-interest data has holes:** the archive prints OI = 0 on some hours ({zero_oi}) and at least one partial "
          "print just before a zero (ETH, bar opening 2026-10-15 11:00 UTC: -18% in one bar with price flat). Modules that use OI drop the "
          "zeros themselves; the multi-bar ones also take a trailing 3-bar median. `strategies/data.py` does not clean this.",
          "6. **Liquidations are a proxy.** Binance publishes no liquidation history; 'flush' means big bar + volume spike + "
          "OI drop on one 1h bar. A cascade that straddles the hour boundary can be missed.",
          "7. **Same-row exit and entry:** `donchian_trend` and `tsmom` can carry `exit=True` and the opposite `side` on one row. "
          "Both re-issue their entry on later bars, so nothing is lost if the engine does not reverse on a single bar. "
          "`ma_trend_atr_stop` puts the new entry on the bar after the cross for the same reason.",
          "8. **Limit entries** are always at or better than the signal close, and the stop is measured from the limit. "
          "If the next bar opens beyond the limit the fill price is the engine's decision.",
          "9. **Regime labels per day** come from `research/ranking.py: regime_series(load(sym, '1h'))`. The label stamped on "
          "day D uses data to the close of D, so apply it from D+1.",
          "10. **Session setups** use fixed UTC hours. US daylight saving moves the cash open between 13:30 and 14:30 UTC; "
          "`ny_open_drive` covers both with a two-bar window, the others ignore DST.\n"]
    (OUT / "ideas.md").write_text("\n".join(L))


def main():
    t_start = time.time()
    caught = self_test()
    mods = discover()
    results, table_rows, all_ok = [], [], caught == 3
    for mod in mods:
        fname = mod.__name__.split(".")[-1]
        meta = check_meta(mod, fname)
        rec = {"name": fname, "family": getattr(mod, "FAMILY", "?"), "timeframe": getattr(mod, "TIMEFRAME", "?"),
               "regime": getattr(mod, "REGIME", "?"), "one_liner": getattr(mod, "ONE_LINER", ""),
               "why": getattr(mod, "WHY", ""), "risk": getattr(mod, "RISK", ""),
               "warmup_bars": getattr(mod, "WARMUP_BARS", None), "default_params": getattr(mod, "DEFAULT_PARAMS", None),
               "param_grid": getattr(mod, "PARAM_GRID", None), "meta_problems": meta, "symbols": {}}
        if not meta:
            for sym in SYMBOLS:
                rec["symbols"][sym] = check_one(mod, sym)
        probs = {k for s in rec["symbols"].values() for k in s["problems"]}
        ok = not meta and not probs
        rec["pass"] = ok
        all_ok &= ok
        sy = rec["symbols"]
        lim = sum(s.get("signals_limit", 0) for s in sy.values())
        tot = sum(s.get("signals_window", 0) for s in sy.values())
        rec["entry_type"] = "limit" if tot and lim == tot else ("market" if lim == 0 else "mixed")
        mark = lambda key: "FAIL" if key in probs else "ok"
        cuts = sum(s.get("prefix_cuts", 0) for s in sy.values())
        ngrid = max((s.get("grid_variants", 0) for s in sy.values()), default=0)
        table_rows.append([f"`{fname}`", rec["timeframe"], "FAIL" if meta else "ok", mark("run"), mark("valid"),
                           f"{mark('prefix')} ({cuts} cuts)", mark("warmup"), f"{mark('grid')} ({ngrid} combos)", mark("columns"),
                           " / ".join(str(sy[s].get("signals_window", "-")) for s in SYMBOLS) if sy else "-",
                           max((s.get("runtime_ms", 0) for s in sy.values()), default=0),
                           "**PASS**" if ok else "**FAIL**"])
        results.append(rec)
        print(f"{'PASS' if ok else 'FAIL'}  {fname}", flush=True)
        if not ok:
            print("      meta:", meta)
            for s, v in sy.items():
                if v["problems"]:
                    print("     ", s, json.dumps(v["problems"], default=str)[:900])

    n_pass = sum(r["pass"] for r in results)
    hdr = ["strategy", "tf", "meta", "runs", "stops valid", "prefix-invariant", "warm-up", "grid", "columns",
           "signals in window BTC / ETH / SOL", "ms per run (max)", "verdict"]
    L = ["# Look-ahead and structure check\n",
         f"- Generated by `research/check_no_lookahead.py` in {time.time() - t_start:.0f}s. "
         f"Data through {frame('BTCUSDT', '1h').index[-1]} (bar open, UTC).",
         f"- **{n_pass} of {len(results)} strategies pass every hard check on all {len(SYMBOLS)} symbols.**",
         f"- Checker self-test: {caught} of 3 deliberately leaky strategies were caught "
         "(shift(-1), centred window, full-sample mean).",
         "- Prefix invariance is exact (NaN-aware), not approximate. 'cuts' = number of truncated re-runs compared, summed over the 3 symbols.",
         "- 'grid' = every PARAM_GRID combination (cartesian product) runs with valid stops; 'combos' is the count per symbol. "
         "'columns' = REQUIRED_COLUMNS is exactly what the module reads.",
         f"- Signal counts are for {BACKTEST_START} to {BACKTEST_END} UTC. Counts only: nothing was followed forward in price.\n",
         md_table(hdr, table_rows)]
    tri = lambda r, key: " / ".join("-" if r["symbols"][s].get(key) is None else str(r["symbols"][s].get(key)) for s in SYMBOLS)
    gap_rows = [[f"`{r['name']}`", r["entry_type"], tri(r, "stop_beyond_next_open"), tri(r, "signals_dropped_by_safety_net"),
                 tri(r, "median_stop_distance_pct"), tri(r, "cost_stopped_trade_pct_of_1R"),
                 tri(r, "share_signals_hitting_3x_cap_pct")] for r in results if r["symbols"]]
    L += ["\n## Stop and cost structure (descriptive, no P&L)\n",
          "All columns are BTC / ETH / SOL, signals inside the backtest window.\n",
          "- **born stopped**: market entries whose stop was already at or beyond the NEXT bar's open. "
          "The next open is read by this checker only, never by a strategy.",
          "- **dropped**: signals the module's own safety net removed over the full history because the stop was "
          "missing or on the wrong side (warm-up rows). A large number here would mean a logic error hidden by the net.",
          "- **stop distance**: median |entry - stop| as % of price.",
          "- **cost of a stopped trade, % of 1R**: SPEC fees and slippage for entry plus a stop exit, divided by the "
          "median stop distance. It is what every losing trade costs on top of the 1R loss, and roughly what a winner "
          "must clear first. Tight-stop setups are the expensive ones.",
          "- **3x cap**: share of signals whose stop is closer than 0.167% of price, where the 3x notional cap would "
          "shrink the position below the intended 0.5% risk.\n",
          md_table(["strategy", "entry", "born stopped", "dropped", "median stop distance %",
                    "cost of a stopped trade, % of 1R", "3x cap binds, % of signals"], gap_rows)]
    fails = [r for r in results if not r["pass"]]
    if fails:
        L.append("\n## Failures\n")
        for r in fails:
            L.append(f"- `{r['name']}`: meta={r['meta_problems']} " +
                     "; ".join(f"{s}: {v['problems']}" for s, v in r["symbols"].items() if v["problems"]))
    (OUT / "lookahead_check.md").write_text("\n".join(L))
    json.dump({"generated_by": "research/check_no_lookahead.py", "backtest_window": [BACKTEST_START, BACKTEST_END],
               "self_test_leaks_caught": caught, "n_strategies": len(results), "n_pass": n_pass, "results": results},
              open(OUT / "lookahead_check.json", "w"), indent=1, default=str)
    write_ideas(results)
    print("\n" + "\n".join(L))
    print(f"\nself-test leaks caught: {caught}/3   strategies passing: {n_pass}/{len(results)}   total time {time.time() - t_start:.0f}s")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
