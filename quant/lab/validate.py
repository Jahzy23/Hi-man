"""Validation tools. Every function takes a strategy module (SPEC interface) and a symbol.

  walk_forward        rolling / anchored; in-sample pick from PARAM_GRID by a robust criterion,
                      next block out-of-sample; only the stitched out-of-sample curve is reported
  holdout             last 12 months of the SPEC window, never used for any selection
  monte_carlo         bootstrap / reshuffle of per-trade returns
  param_neighbourhood every PARAM_GRID combination, share profitable after costs, spread
  stress              fees x2, slippage x2, both
  lookahead_audit     prefix-invariance of signals + extra-delay test
  multiple_testing    false positives expected by chance, Bonferroni bar, Sharpe haircut,
                      deflated Sharpe ratio
  validate_setup      all of the above for one (module, symbol); verdict() applies the
                      pre-registered kill rules in KILL_RULES and returns a tier:
                      killed / unproven (alive, but not distinguishable from luck after the
                      multiple-testing haircut) / survivor

Window discipline
  DEV      = BACKTEST_START .. HOLDOUT_START (exclusive)  -> all selection happens here
  HOLDOUT  = last 12 months of the SPEC window            -> looked at once, at the end
walk_forward / param_neighbourhood / stress / monte_carlo default to the DEV window.

Signals are computed once per parameter set on the frame cut at the end of the window in use and
re-used across folds. That is only valid for prefix-invariant strategies - which is exactly what
lookahead_audit checks (it also checks the fold boundaries); a strategy that fails the audit is
dead anyway. ``walk_forward(strict=True)`` recomputes signals per fold on truncated frames.
"""
from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, replace
from statistics import NormalDist

import numpy as np
import pandas as pd

from . import metrics as M
from .engine import (BacktestResult, ExecConfig, call_signals, get_ctx, get_frame, module_params,
                     run_backtest, spec_data, to_ts, window_bounds)

_N = NormalDist()
EULER_GAMMA = 0.5772156649015329
HOLDOUT_MONTHS = 12
SIGNAL_COLS = ("side", "entry", "entry_valid", "stop", "target", "max_hold", "exit")

# Pre-registered kill rules (fixed before any of Radar's setups was run).
KILL_RULES = dict(
    min_trades_dev=30,              # fewer trades on DEV -> not enough evidence
    min_dev_net_return=0.0,         # must make money after costs on DEV
    min_profit_factor=1.10,
    min_stress_net_return=0.0,      # must survive fees x2 AND slippage x2
    min_wf_oos_net_return=0.0,      # stitched walk-forward out-of-sample must be > 0
    min_share_profitable=0.60,      # share of PARAM_GRID combinations profitable after costs
    max_mc_prob_loss=0.20,          # bootstrap probability of ending below start
    max_mc_dd95=0.25,               # 95th percentile bootstrap max drawdown
    min_haircut_sharpe=0.0,         # Bonferroni-haircut Sharpe must stay > 0
    min_holdout_net_return=0.0,     # untouched last 12 months must not lose
)


# --------------------------------------------------------------------------------------
# windows
# --------------------------------------------------------------------------------------
def spec_window() -> tuple[pd.Timestamp, pd.Timestamp]:
    d = spec_data()
    return pd.Timestamp(d.BACKTEST_START, tz="UTC"), pd.Timestamp(d.BACKTEST_END, tz="UTC")


def holdout_start() -> pd.Timestamp:
    """First instant of the untouched holdout = 12 months before the end of the SPEC window."""
    _, end = spec_window()
    return (end + pd.Timedelta(minutes=1)).ceil("D") - pd.DateOffset(months=HOLDOUT_MONTHS)


def dev_window() -> tuple[pd.Timestamp, pd.Timestamp]:
    start, _ = spec_window()
    return start, holdout_start() - pd.Timedelta(seconds=1)


def holdout_window() -> tuple[pd.Timestamp, pd.Timestamp]:
    return holdout_start(), spec_window()[1]


def _utc(x) -> pd.Timestamp:
    t = pd.Timestamp(x)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


# --------------------------------------------------------------------------------------
# parameter grid
# --------------------------------------------------------------------------------------
def param_grid(module) -> tuple[list[dict], str, list[tuple] | None]:
    """(combinations, source, grid coordinates or None).

    PARAM_GRID may be a dict name -> list of values (cartesian product, merged over
    DEFAULT_PARAMS) or a list of dicts. Without PARAM_GRID an automatic +/-25% neighbourhood of
    the numeric DEFAULT_PARAMS is used (full product up to 4 parameters, one-at-a-time beyond)."""
    base = module_params(module)
    g = getattr(module, "PARAM_GRID", None)
    if isinstance(g, dict) and g:
        keys = list(g)
        vals = [list(v) if isinstance(v, (list, tuple, np.ndarray)) else [v] for v in g.values()]
        combos, coords = [], []
        for pos in itertools.product(*[range(len(v)) for v in vals]):
            combos.append({**base, **{k: vals[i][pos[i]] for i, k in enumerate(keys)}})
            coords.append(pos)
        return combos, "PARAM_GRID", coords
    if isinstance(g, (list, tuple)) and g:
        return [{**base, **dict(c)} for c in g], "PARAM_GRID(list)", None
    num = [k for k, v in base.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0]

    def around(v):
        out = []
        for f in (0.75, 1.0, 1.25):
            x = v * f
            if isinstance(v, int):
                x = max(1, int(round(x)))
            if x not in out:
                out.append(x)
        return out
    if not num:
        return [base], "auto(no numeric params)", None
    if len(num) <= 4:
        vals = [around(base[k]) for k in num]
        combos, coords = [], []
        for pos in itertools.product(*[range(len(v)) for v in vals]):
            combos.append({**base, **{k: vals[i][pos[i]] for i, k in enumerate(num)}})
            coords.append(pos)
        return combos, "auto(+/-25%, product)", coords
    combos = [base]
    for k in num:
        combos += [{**base, k: x} for x in around(base[k]) if x != base[k]]
    return combos, "auto(+/-25%, one-at-a-time)", None


def _neighbours(coords: list[tuple]) -> list[list[int]]:
    where = {c: i for i, c in enumerate(coords)}
    out = []
    for c in coords:
        nb = []
        for dim in range(len(c)):
            for step in (-1, 1):
                k = c[:dim] + (c[dim] + step,) + c[dim + 1:]
                if k in where:
                    nb.append(where[k])
        out.append(nb)
    return out


class SignalBank:
    """Signals for many parameter sets of one (module, symbol), computed once."""

    def __init__(self, module, symbol: str, upto=None):
        self.module, self.symbol = module, symbol
        tf = getattr(module, "TIMEFRAME", "1h")
        df = get_frame(symbol, tf)
        self.df = df if upto is None else df.loc[:to_ts(upto, df.index)]
        self.ctx = get_ctx(tf, self.df.index[-1] if len(self.df) else None)
        self._cache: dict[str, pd.DataFrame] = {}

    def sig(self, params: dict | None = None) -> pd.DataFrame:
        p = module_params(self.module, params)
        key = repr(sorted(p.items(), key=lambda kv: kv[0]))
        if key not in self._cache:
            self._cache[key] = call_signals(self.module, self.df, p, self.ctx)
        return self._cache[key]

    def run(self, params=None, start=None, end=None, cfg: ExecConfig | None = None) -> BacktestResult:
        return run_backtest(self.df, self.sig(params), self.symbol, start, end, cfg,
                            name=getattr(self.module, "NAME", ""), params=module_params(self.module, params))


# --------------------------------------------------------------------------------------
# (a) walk-forward
# --------------------------------------------------------------------------------------
def wf_folds(start, end, is_months: int = 12, oos_months: int = 3, mode: str = "rolling") -> list[dict]:
    """Fold boundaries; every interval is [from, to) with ``end`` the last usable instant."""
    start, end = _utc(start), _utc(end)
    stop = end + pd.Timedelta(seconds=1)
    folds, t = [], start + pd.DateOffset(months=is_months)
    while t < stop:
        oos_end = min(t + pd.DateOffset(months=oos_months), stop)
        folds.append(dict(is_start=start if mode == "anchored" else t - pd.DateOffset(months=is_months),
                          is_end=t, oos_start=t, oos_end=oos_end))
        t = t + pd.DateOffset(months=oos_months)
    return folds


def robust_score(res: BacktestResult, min_trades: int = 10, dd_floor: float = 0.02) -> float:
    """In-sample selection score = net return / max(max drawdown, 2%). NaN if too few trades."""
    if len(res.trades) < min_trades:
        return float("nan")
    return res.net_return / max(M.max_drawdown(res.equity, res.cfg.start_equity), dd_floor)


def _smooth(scores: np.ndarray, coords) -> np.ndarray:
    """Blend each score with its grid neighbours (own score counts double): prefers plateaus to
    isolated peaks, and still ranks correctly on a grid with only two values per parameter."""
    if coords is None:
        return scores.copy()
    out = np.full(len(scores), np.nan)
    for i, nb in enumerate(_neighbours(coords)):
        if np.isfinite(scores[i]):
            vals = [scores[k] for k in nb if np.isfinite(scores[k])]
            out[i] = float((2.0 * scores[i] + sum(vals)) / (2.0 + len(vals)))
    return out


@dataclass
class WalkForwardResult:
    result: BacktestResult            # stitched out-of-sample only
    metrics: dict
    folds: pd.DataFrame
    grid_source: str
    n_combos: int


def walk_forward(module, symbol: str, start=None, end=None, is_months: int = 12, oos_months: int = 3,
                 mode: str = "rolling", cfg: ExecConfig | None = None, min_trades: int = 10,
                 strict: bool = False, bank: "SignalBank | None" = None) -> WalkForwardResult:
    """Pick parameters on each in-sample block, trade the next block, stitch the OOS blocks.

    Selection: robust_score per combination, blended with its grid neighbours, arg-max. If no
    combination has ``min_trades`` in-sample trades the fold trades DEFAULT_PARAMS. Equity is
    carried from one OOS block to the next; a position open at the end of a block is closed there
    (taker fee + slippage). Default window = DEV (the holdout is never touched)."""
    cfg = cfg or ExecConfig()
    d0, d1 = dev_window()
    start = d0 if start is None else _utc(start)
    end = d1 if end is None else _utc(end)
    combos, source, coords = param_grid(module)
    bank = bank or SignalBank(module, symbol, upto=end)
    default = module_params(module)
    one_sec = pd.Timedelta(seconds=1)
    equity = cfg.start_equity
    eq_parts, pos_parts, trade_parts, rows = [], [], [], []
    for f in wf_folds(start, end, is_months, oos_months, mode):
        is_bank = SignalBank(module, symbol, upto=f["is_end"] - one_sec) if strict else bank
        scores = np.array([robust_score(is_bank.run(c, f["is_start"], f["is_end"] - one_sec, cfg), min_trades)
                           for c in combos])
        sm = _smooth(scores, coords)
        if np.isfinite(sm).any():
            best = int(np.nanargmax(sm))
            chosen, fallback = combos[best], False
        else:
            best, chosen, fallback = -1, default, True
        oos_bank = SignalBank(module, symbol, upto=f["oos_end"] - one_sec) if strict else bank
        r = oos_bank.run(chosen, f["oos_start"], f["oos_end"] - one_sec, replace(cfg, start_equity=equity))
        rows.append(dict(is_start=f["is_start"], is_end=f["is_end"], oos_start=f["oos_start"], oos_end=f["oos_end"],
                         chosen=chosen, fallback_default=fallback,
                         is_score=float(scores[best]) if best >= 0 else np.nan,
                         is_score_smoothed=float(sm[best]) if best >= 0 else np.nan,
                         is_share_positive=float(np.mean(scores[np.isfinite(scores)] > 0)) if np.isfinite(scores).any() else np.nan,
                         oos_net_return=r.net_return, oos_trades=len(r.trades), oos_max_dd=M.max_drawdown(r.equity, equity)))
        eq_parts.append(r.equity)
        pos_parts.append(r.in_position)
        trade_parts.append(r.trades)
        equity = r.final_equity
    if not eq_parts:
        raise ValueError("window too short for a single walk-forward fold")
    eq = pd.concat(eq_parts)
    trades = pd.concat([t for t in trade_parts if len(t)], ignore_index=True) if any(len(t) for t in trade_parts) \
        else trade_parts[0]
    stitched = BacktestResult(symbol, trades, eq, pd.concat(pos_parts), {}, cfg, eq.index[0], eq.index[-1],
                              r.bar, getattr(module, "NAME", "") + ":walk_forward_oos", {})
    m = M.compute_metrics(stitched, bank.df)
    folds = pd.DataFrame(rows)
    m["wf_folds"] = len(folds)
    m["wf_share_positive_folds"] = float((folds["oos_net_return"] > 0).mean())
    m["wf_distinct_param_sets"] = int(folds["chosen"].map(repr).nunique())
    m["wf_fallback_folds"] = int(folds["fallback_default"].sum())
    return WalkForwardResult(stitched, m, folds, source, len(combos))


# --------------------------------------------------------------------------------------
# (b) holdout
# --------------------------------------------------------------------------------------
def holdout(module, symbol: str, params: dict | None = None, cfg: ExecConfig | None = None):
    """One run on the last 12 months with parameters fixed beforehand. Returns (result, metrics)."""
    h0, h1 = holdout_window()
    bank = SignalBank(module, symbol, upto=h1)
    r = bank.run(params, h0, h1, cfg)
    return r, M.compute_metrics(r, bank.df)


# --------------------------------------------------------------------------------------
# (c) Monte Carlo
# --------------------------------------------------------------------------------------
def monte_carlo(trades, n_paths: int = 5000, seed: int = 0, method: str = "bootstrap") -> dict:
    """Resample per-trade returns (net / equity at entry) and compound them.

    bootstrap : draw n trades with replacement -> distribution of final return AND drawdown
    shuffle   : permute the realised trades    -> same final return, distribution of drawdown
    Reports the 5th percentile of final return, the 95th percentile of max drawdown and the
    probability of finishing below the starting equity."""
    rets = np.asarray(trades["ret"] if isinstance(trades, pd.DataFrame) else trades, dtype=float)
    rets = rets[np.isfinite(rets)]
    n = len(rets)
    out = dict(method=method, n_trades=n, n_paths=n_paths)
    if n < 2:
        out.update(final_return_p05=np.nan, final_return_p50=np.nan, final_return_p95=np.nan,
                   final_return_mean=np.nan, max_dd_p50=np.nan, max_dd_p95=np.nan, prob_loss=np.nan,
                   observed_return=float(np.prod(1 + rets) - 1) if n else 0.0, observed_max_dd=np.nan)
        return out
    rng = np.random.default_rng(seed)
    finals, dds = np.empty(n_paths), np.empty(n_paths)
    chunk = max(1, min(n_paths, 2_000_000 // n))
    for a in range(0, n_paths, chunk):
        b = min(n_paths, a + chunk)
        if method == "bootstrap":
            idx = rng.integers(0, n, size=(b - a, n))
        elif method == "shuffle":
            idx = np.argsort(rng.random((b - a, n)), axis=1)
        else:
            raise ValueError("method must be 'bootstrap' or 'shuffle'")
        path = np.cumprod(1.0 + rets[idx], axis=1)
        path = np.maximum(path, 0.0)
        peak = np.maximum(np.maximum.accumulate(path, axis=1), 1.0)
        finals[a:b] = path[:, -1] - 1.0
        dds[a:b] = np.max(1.0 - path / peak, axis=1)
    obs = np.cumprod(1.0 + rets)
    out.update(
        final_return_p05=float(np.percentile(finals, 5)), final_return_p50=float(np.percentile(finals, 50)),
        final_return_p95=float(np.percentile(finals, 95)), final_return_mean=float(finals.mean()),
        max_dd_p50=float(np.percentile(dds, 50)), max_dd_p95=float(np.percentile(dds, 95)),
        prob_loss=float(np.mean(finals < 0.0)),
        observed_return=float(obs[-1] - 1.0),
        observed_max_dd=float(np.max(1.0 - obs / np.maximum(np.maximum.accumulate(obs), 1.0))),
    )
    return out


# --------------------------------------------------------------------------------------
# (d) parameter neighbourhood
# --------------------------------------------------------------------------------------
def param_neighbourhood(module, symbol: str, start=None, end=None, cfg: ExecConfig | None = None,
                        bank: "SignalBank | None" = None):
    """Run every PARAM_GRID combination. Returns (table, summary)."""
    d0, d1 = dev_window()
    start = d0 if start is None else start
    end = d1 if end is None else end
    combos, source, _ = param_grid(module)
    default = module_params(module)
    if default not in combos:
        combos = combos + [default]
    bank = bank or SignalBank(module, symbol, upto=end)
    rows = []
    for c in combos:
        r = bank.run(c, start, end, cfg)
        e0 = r.cfg.start_equity
        rows.append(dict(params=c, is_default=c == default, net_return=r.net_return,
                         max_dd=M.max_drawdown(r.equity, e0), sharpe=M.sharpe(r.equity, e0),
                         profit_factor=M.profit_factor(r.trades["net"]) if len(r.trades) else np.nan,
                         trades=len(r.trades)))
    tab = pd.DataFrame(rows)
    nr = tab["net_return"].to_numpy(float)
    dflt = float(tab.loc[tab["is_default"], "net_return"].iloc[0])
    summary = dict(
        grid_source=source, n_combos=len(tab),
        share_profitable=float(np.mean(nr > 0)),
        share_sharpe_positive=float(np.mean(tab["sharpe"] > 0)),
        net_return_min=float(nr.min()), net_return_p25=float(np.percentile(nr, 25)),
        net_return_median=float(np.median(nr)), net_return_p75=float(np.percentile(nr, 75)),
        net_return_max=float(nr.max()), net_return_spread=float(nr.max() - nr.min()),
        net_return_std=float(nr.std(ddof=0)),
        default_net_return=dflt, default_percentile=float(np.mean(nr <= dflt)),
        min_trades=int(tab["trades"].min()),
    )
    return tab, summary


# --------------------------------------------------------------------------------------
# (e) cost stress
# --------------------------------------------------------------------------------------
def stress(module, symbol: str, params: dict | None = None, start=None, end=None,
           cfg: ExecConfig | None = None, bank: SignalBank | None = None) -> pd.DataFrame:
    """Base vs fees x2 vs slippage x2 vs both. One row per scenario."""
    cfg = cfg or ExecConfig()
    d0, d1 = dev_window()
    start = d0 if start is None else start
    end = d1 if end is None else end
    bank = bank or SignalBank(module, symbol, upto=end)
    rows = []
    for label, fm, sm in (("base", 1, 1), ("fees_x2", 2, 1), ("slippage_x2", 1, 2), ("fees_x2_slippage_x2", 2, 2)):
        r = bank.run(params, start, end, replace(cfg, fee_mult=cfg.fee_mult * fm, slip_mult=cfg.slip_mult * sm))
        e0 = r.cfg.start_equity
        ts = M.trade_stats(r.trades)
        rows.append(dict(scenario=label, net_return=r.net_return, max_dd=M.max_drawdown(r.equity, e0),
                         sharpe=M.sharpe(r.equity, e0), profit_factor=ts["profit_factor"], trades=ts["trades"],
                         fees_paid=ts["fees_paid"], slippage_paid=ts["slippage_paid"]))
    return pd.DataFrame(rows).set_index("scenario")


# --------------------------------------------------------------------------------------
# (f) look-ahead audit
# --------------------------------------------------------------------------------------
def _col_equal(a: pd.Series, b: pd.Series) -> np.ndarray:
    """Element-wise equality, NaN == NaN, floats compared with a tight tolerance."""
    if a.dtype == bool or b.dtype == bool or a.dtype == object or b.dtype == object:
        x, y = a.to_numpy(dtype=object), b.to_numpy(dtype=object)
        return np.array([(xi == yi) or (pd.isna(xi) and pd.isna(yi)) for xi, yi in zip(x, y)], dtype=bool)
    x, y = a.to_numpy(dtype=float, na_value=np.nan), b.to_numpy(dtype=float, na_value=np.nan)
    return np.isclose(x, y, rtol=1e-9, atol=1e-12, equal_nan=True)


def prefix_invariance(module, symbol: str, params: dict | None = None, cuts=None, n_random: int = 8,
                      seed: int = 0, upto=None) -> dict:
    """signals(df.iloc[:k]) must equal signals(df).iloc[:k] row for row (SPEC rule).

    ``cuts``: extra cut points (row counts or timestamps). A mismatch is *material* when it is on
    a row that carries a signal (side != 0 in either version) or changes the exit flag."""
    tf = getattr(module, "TIMEFRAME", "1h")
    df = get_frame(symbol, tf)
    if upto is not None:
        df = df.loc[:to_ts(upto, df.index)]
    p = module_params(module, params)
    full = call_signals(module, df, p, get_ctx(tf, df.index[-1]))
    n = len(df)
    ks = {n - 1, n - 2, n - 25, n // 2}
    rng = np.random.default_rng(seed)
    ks.update(int(k) for k in rng.integers(n // 3, n - 1, size=n_random))
    for c in (cuts or []):
        ks.add(int(c) if isinstance(c, (int, np.integer)) else int(df.index.searchsorted(to_ts(c, df.index))))
    ks = sorted(k for k in ks if 50 < k < n)
    cols = [c for c in SIGNAL_COLS if c in full.columns]
    details, material_total, any_total = [], 0, 0
    for k in ks:
        sub = df.iloc[:k]
        part = call_signals(module, sub, p, get_ctx(tf, sub.index[-1]))
        ref = full.iloc[:k]
        bad = np.zeros(k, dtype=bool)
        badcols = []
        for c in cols:
            if c not in part.columns:
                badcols.append(c + "(missing)")
                bad[:] = True
                continue
            eq = _col_equal(part[c], ref[c])
            if not eq.all():
                badcols.append(c)
                bad |= ~eq
        if bad.any():
            side_p = np.nan_to_num(pd.to_numeric(part["side"], errors="coerce").to_numpy(float))
            side_f = np.nan_to_num(pd.to_numeric(ref["side"], errors="coerce").to_numpy(float))
            mat = bad & ((side_p != 0) | (side_f != 0))
            if "exit" in cols and "exit" in part.columns:
                mat |= ~_col_equal(part["exit"], ref["exit"])
            first = int(np.flatnonzero(bad)[0])
            details.append(dict(k=k, cut_time=str(df.index[k - 1]), rows_differ=int(bad.sum()),
                                rows_material=int(mat.sum()), columns=badcols,
                                first_bad_row=str(df.index[first]), bars_before_cut=k - 1 - first))
            material_total += int(mat.sum())
            any_total += int(bad.sum())
    return dict(ok=material_total == 0, strictly_identical=any_total == 0, cuts_checked=len(ks),
                rows_material=material_total, rows_any=any_total, mismatches=details)


def delay_test(module, symbol: str, params: dict | None = None, start=None, end=None,
               cfg: ExecConfig | None = None, bank: SignalBank | None = None) -> dict:
    """Execute the same signals 1 and 2 bars later than the contract.

    An honest edge decays gently. Three patterns are reported:
      improves_when_delayed : one bar later is PROFITABLE and clearly better than on time (by more
                              than 5 points and 25%). Being slower should not pay: timestamps are
                              probably misaligned or the fill model is being gamed. Fails the audit.
      less_bad_when_delayed : same size of improvement but still not profitable - no leak, the
                              entry timing is simply wrong-footed at lag 0. Warning only.
      collapses_when_delayed: a profitable result is more than halved by one bar of latency
                              (fragile to latency; also what a leak in the inputs looks like).
                              Warning only - prefix_invariance is the hard test for leaks."""
    cfg = cfg or ExecConfig()
    d0, d1 = dev_window()
    start = d0 if start is None else start
    end = d1 if end is None else end
    bank = bank or SignalBank(module, symbol, upto=end)
    out = {}
    for dly in (0, 1, 2):
        r = bank.run(params, start, end, replace(cfg, extra_delay=dly))
        out[f"net_return_delay{dly}"] = r.net_return
        out[f"sharpe_delay{dly}"] = M.sharpe(r.equity, r.cfg.start_equity)
        out[f"trades_delay{dly}"] = len(r.trades)
    base, d1r = out["net_return_delay0"], out["net_return_delay1"]
    better = d1r - base > max(0.05, 0.25 * abs(base))
    out["improves_when_delayed"] = bool(better and d1r > 0)
    out["less_bad_when_delayed"] = bool(better and d1r <= 0)
    out["collapses_when_delayed"] = bool(base > 0.05 and d1r < 0.5 * base)
    return out


def lookahead_audit(module, symbol: str, params: dict | None = None, start=None, end=None,
                    cfg: ExecConfig | None = None, cuts=None) -> dict:
    d0, d1 = dev_window()
    end = d1 if end is None else end
    if cuts is None:                       # fold boundaries of the default walk-forward
        cuts = [f["oos_start"] for f in wf_folds(d0, d1)]
    pi = prefix_invariance(module, symbol, params, cuts=cuts, upto=end)
    dt = delay_test(module, symbol, params, start, end, cfg)
    return dict(prefix=pi, delay=dt, passed=bool(pi["ok"] and not dt["improves_when_delayed"]),
                warnings=[w for w, on in (("signals differ on rows without a signal", pi["ok"] and not pi["strictly_identical"]),
                                          ("edge collapses with one bar of delay", dt["collapses_when_delayed"]),
                                          ("loses less when executed one bar late", dt["less_bad_when_delayed"])) if on])


# --------------------------------------------------------------------------------------
# (g) multiple testing
# --------------------------------------------------------------------------------------
def expected_max_sharpe(n_trials: int, sharpe_std: float) -> float:
    """Expected best Sharpe among n independent trials with true Sharpe 0 (Bailey & Lopez de Prado)."""
    if n_trials <= 1:
        return 0.0
    return sharpe_std * ((1 - EULER_GAMMA) * _N.inv_cdf(1 - 1.0 / n_trials)
                         + EULER_GAMMA * _N.inv_cdf(1 - 1.0 / (n_trials * math.e)))


def multiple_testing(n_tests: int, years: float, alpha: float = 0.05) -> dict:
    """What luck alone produces when n_tests (setup x symbol) backtests are run.

    Under the null the annualised Sharpe estimate is ~ N(0, 1/years), so a one-sided test at
    level alpha passes with Sharpe > z_alpha / sqrt(years). Bonferroni replaces alpha by
    alpha / n_tests. Treats the tests as independent (symbols are correlated, so the true number
    of independent tests is lower and Bonferroni errs on the strict side)."""
    n_tests = max(int(n_tests), 1)
    z1 = _N.inv_cdf(1 - alpha)
    zb = _N.inv_cdf(1 - alpha / n_tests)
    sd = 1.0 / math.sqrt(years)
    out = dict(n_tests=n_tests, years=years, alpha=alpha,
               expected_false_positives=alpha * n_tests,
               prob_at_least_one_false_positive=1 - (1 - alpha) ** n_tests,
               sharpe_bar_single_test=z1 * sd, bonferroni_alpha=alpha / n_tests,
               sharpe_bar_bonferroni=zb * sd, expected_best_sharpe_by_luck=expected_max_sharpe(n_tests, sd))
    out["note"] = (
        f"{n_tests} setup x symbol tests at {alpha:.0%}: expect {out['expected_false_positives']:.1f} to look good by "
        f"chance (P(at least one) = {out['prob_at_least_one_false_positive']:.0%}); the best of {n_tests} zero-edge "
        f"backtests is expected to show Sharpe ~{out['expected_best_sharpe_by_luck']:.2f} over {years:.1f}y. "
        f"Bar to clear: Sharpe > {out['sharpe_bar_bonferroni']:.2f} (Bonferroni) instead of {out['sharpe_bar_single_test']:.2f}.")
    return out


def haircut_sharpe(sharpe_annual: float, years: float, n_tests: int) -> dict:
    """Bonferroni haircut (Harvey & Liu): p-value x n_tests, converted back into a Sharpe."""
    t = sharpe_annual * math.sqrt(years)
    p = 1 - _N.cdf(t)
    p_adj = min(1.0, p * max(int(n_tests), 1))
    if p_adj >= 0.5:                 # no evidence left after the correction
        hs = 0.0
    elif p_adj <= 0.0:               # beyond float precision: nothing to haircut
        hs = sharpe_annual
    else:
        hs = _N.inv_cdf(1 - p_adj) / math.sqrt(years)
    return dict(sharpe=sharpe_annual, t_stat=t, p_value=p, p_bonferroni=p_adj, haircut_sharpe=hs,
                haircut=1 - hs / sharpe_annual if sharpe_annual > 0 else 1.0,
                significant_5pct=bool(p_adj < 0.05))


def deflated_sharpe(daily_ret, n_trials: int, trial_sharpes_annual=None) -> dict:
    """Deflated Sharpe ratio = P(true Sharpe > 0) after correcting for the number of trials and
    for skew / fat tails of the daily returns. ``trial_sharpes_annual``: Sharpes of all trials
    (their dispersion sets the luck benchmark); if omitted the null dispersion 1/sqrt(years) is used."""
    r = np.asarray(daily_ret, dtype=float)
    r = r[np.isfinite(r)]
    T = len(r)
    if T < 30 or r.std(ddof=1) == 0:
        return dict(dsr=np.nan, sharpe_daily=np.nan, benchmark_sharpe_annual=np.nan, T=T)
    sr = r.mean() / r.std(ddof=1)
    z = (r - r.mean()) / r.std(ddof=0)
    skew, kurt = float(np.mean(z ** 3)), float(np.mean(z ** 4))
    ann = math.sqrt(M.SHARPE_DAYS)
    if trial_sharpes_annual is not None and len(trial_sharpes_annual) > 1:
        sd_daily = float(np.std(np.asarray(trial_sharpes_annual, dtype=float), ddof=1)) / ann
    else:
        sd_daily = 1.0 / math.sqrt(T)
    sr0 = expected_max_sharpe(n_trials, sd_daily)
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4.0 * sr ** 2))
    dsr = _N.cdf((sr - sr0) * math.sqrt(T - 1) / denom)
    return dict(dsr=float(dsr), sharpe_daily=float(sr), sharpe_annual=float(sr * ann),
                benchmark_sharpe_annual=float(sr0 * ann), skew=skew, kurtosis=kurt, T=T, n_trials=n_trials)


# --------------------------------------------------------------------------------------
# everything for one setup
# --------------------------------------------------------------------------------------
def validate_setup(module, symbol: str, n_tests: int = 1, cfg: ExecConfig | None = None,
                   mc_paths: int = 5000, with_holdout: bool = True) -> dict:
    """Full battery for one (module, symbol). All selection-type work is on DEV; the holdout is
    run once with DEFAULT_PARAMS."""
    cfg = cfg or ExecConfig()
    d0, d1 = dev_window()
    bank = SignalBank(module, symbol, upto=d1)
    base = bank.run(None, d0, d1, cfg)
    m = M.compute_metrics(base, bank.df)
    rep = dict(name=getattr(module, "NAME", ""), symbol=symbol, timeframe=getattr(module, "TIMEFRAME", "1h"),
               dev=m, dev_result=base)
    rep["yearly"] = M.yearly_split(base, bank.df)
    rep["regime"] = M.regime_split(base, bank.df)
    rep["audit"] = lookahead_audit(module, symbol, cfg=cfg)
    rep["stress"] = stress(module, symbol, cfg=cfg, bank=bank)
    rep["neighbourhood_table"], rep["neighbourhood"] = param_neighbourhood(module, symbol, cfg=cfg, bank=bank)
    wf = walk_forward(module, symbol, cfg=cfg, bank=bank)
    rep["walk_forward"], rep["walk_forward_folds"] = wf.metrics, wf.folds
    rep["monte_carlo"] = monte_carlo(base.trades, n_paths=mc_paths)
    rep["monte_carlo_shuffle"] = monte_carlo(base.trades, n_paths=mc_paths, method="shuffle")
    rep["multiple_testing"] = multiple_testing(n_tests, m["years"])
    rep["haircut"] = haircut_sharpe(m["sharpe"], m["years"], n_tests)
    rep["deflated_sharpe"] = deflated_sharpe(M.daily_returns(base.equity, cfg.start_equity), n_tests)
    if with_holdout:
        hr, hm = holdout(module, symbol, cfg=cfg)
        rep["holdout"], rep["holdout_result"] = hm, hr
    rep["verdict"] = verdict(rep)
    return rep


def verdict(rep: dict, rules: dict | None = None) -> dict:
    """Apply KILL_RULES.

    tier = "killed"   : at least one hard rule fired (failed_rules)
           "unproven" : no hard rule fired, but the Sharpe does not survive the multiple-testing
                        haircut - cannot be told apart from the luckiest of n zero-edge backtests
           "survivor" : passed everything (survives = True only here)"""
    k = {**KILL_RULES, **(rules or {})}
    fails = []

    def chk(cond, msg):
        if cond:
            fails.append(msg)
    d = rep["dev"]
    chk(not rep["audit"]["passed"], "look-ahead audit failed")
    chk(d["trades"] < k["min_trades_dev"], f"only {d['trades']} trades on DEV (< {k['min_trades_dev']})")
    chk(not d["net_return"] > k["min_dev_net_return"], f"DEV net return {d['net_return']:+.1%} after costs")
    pf = d["profit_factor"]
    chk(not (pf == pf and pf >= k["min_profit_factor"]), f"profit factor {pf:.2f} < {k['min_profit_factor']}")
    s = rep["stress"].loc["fees_x2_slippage_x2", "net_return"]
    chk(not s > k["min_stress_net_return"], f"fees x2 + slippage x2 net return {s:+.1%}")
    w = rep["walk_forward"]["net_return"]
    chk(not w > k["min_wf_oos_net_return"], f"walk-forward out-of-sample net return {w:+.1%}")
    sp = rep["neighbourhood"]["share_profitable"]
    chk(sp < k["min_share_profitable"], f"only {sp:.0%} of parameter combinations profitable")
    mc = rep["monte_carlo"]
    chk(not mc["prob_loss"] <= k["max_mc_prob_loss"], f"Monte Carlo P(loss) {mc['prob_loss']:.0%}")
    chk(not mc["max_dd_p95"] <= k["max_mc_dd95"], f"Monte Carlo 95th pct max drawdown {mc['max_dd_p95']:.0%}")
    if "holdout" in rep:
        h = rep["holdout"]["net_return"]
        chk(not h > k["min_holdout_net_return"], f"holdout (last 12 months) net return {h:+.1%}")
    hs = rep["haircut"]["haircut_sharpe"]
    unproven = not hs > k["min_haircut_sharpe"]
    warnings = list(rep["audit"]["warnings"])
    if unproven:
        warnings.append(f"Sharpe {rep['haircut']['sharpe']:.2f} does not survive the multiple-testing haircut "
                        f"(n={rep['multiple_testing']['n_tests']})")
    tier = "killed" if fails else ("unproven" if unproven else "survivor")
    return dict(survives=tier == "survivor", tier=tier, failed_rules=fails, warnings=warnings)
