#!/usr/bin/env python
"""Phase 2: test, kill and rank Radar's setups.

    cd /home/user/Hi-man/quant
    python lab/phase2.py prereg      # write the rules below to lab/results/PREREG.md (before any result)
    python lab/phase2.py stage1      # all setup x symbol pairs on the first 36 months only
    python lab/phase2.py rank        # kill table, scores, finalists (still first 36 months only)
    python lab/phase2.py stage2      # open the last 12 months ONCE, for the finalists only
    python lab/phase2.py final       # gate vs buy-and-hold, portfolio pass, numbers for the write-up

Primary configuration (owner decision): exchange minimum order sizes ON, because the account
really is $1,000. The table comes from bot/instruments.py and is UNVERIFIED (exchangeInfo is
geo-blocked here). Every table also carries the no-filter number, labelled.
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np          # noqa: E402
import pandas as pd         # noqa: E402

from lab import metrics as M            # noqa: E402
from lab import portfolio as P          # noqa: E402
from lab import run_all as RA           # noqa: E402
from lab import validate as V           # noqa: E402
from lab.engine import (ExecConfig, exchange_filters, get_frame, module_params, module_signals_1h,  # noqa: E402
                        run_backtest, spec_data)

OUT = ROOT / "lab" / "results"
WORK = OUT / "phase2"
N_PAIR_TESTS = 87            # 29 setups x 3 symbols
N_SETUP_TESTS = 29

# ======================================================================================
# PRE-REGISTERED RULES (fixed before any phase-2 result was produced)
# ======================================================================================
KILL_ORDER = [
    ("audit", "look-ahead audit failed"),
    ("trades", "fewer than 30 trades in the first 36 months"),
    ("net", "loses money after costs"),
    ("pf", "profit factor below 1.10"),
    ("stress", "loses with fees x2 and slippage x2"),
    ("wf", "walk-forward out-of-sample not profitable"),
    ("grid", "fewer than 60% of nearby settings profitable"),
    ("mc_loss", "Monte Carlo chance of a loss above 20%"),
    ("mc_dd", "Monte Carlo 95th-percentile drawdown above 25%"),
    ("holdout", "loses money in the last 12 months (finalists only)"),
]

# Scores are given to a SETUP = the book that trades it on every symbol where it survives the
# kill rules ("book" = sum of the per-symbol runs, each sized at 0.5% of its own $1,000).
SCORE_BANDS = {
    "oos_profit": dict(
        what="walk-forward out-of-sample net profit of the book, % per year (sum of the per-symbol "
             "walk-forward runs, 2023-11 to 2025-10)",
        bands=[(1, "<= 0%"), (2, "0-2%"), (3, "2-5%"), (4, "5-10%"), (5, "> 10%")]),
    "max_dd": dict(
        what="max drawdown of the book over the first 36 months, DEFAULT_PARAMS",
        bands=[(5, "< 5%"), (4, "5-10%"), (3, "10-15%"), (2, "15-25%"), (1, ">= 25%")]),
    "trades": dict(
        what="trades over the first 36 months, pooled across the symbols it survives on",
        bands=[(1, "< 30"), (2, "30-49 (thin)"), (3, "50-99"), (4, "100-199"), (5, ">= 200")]),
    "lose_streak": dict(
        what="longest run of losing trades in the book (trades ordered by exit time)",
        bands=[(5, "<= 4"), (4, "5-6"), (3, "7-9"), (2, "10-14"), (1, ">= 15")]),
    "bear_sideways": dict(
        what="book P&L earned in bear-labelled bars (b) and in sideways-labelled bars (s), % of $1,000, "
             "first 36 months; each symbol uses its own no-look-ahead regime label",
        bands=[(5, "b > 0 and s > 0"), (4, "b + s > 0 and the worse one >= -3%"),
               (3, "b + s > 0 and the worse one < -3%"), (2, "-5% <= b + s <= 0"), (1, "b + s < -5%")]),
}
PROTOCOL = """
1. Unit of the kill table: (setup, symbol), DEFAULT_PARAMS, first 36 months (2022-11-01 to 2025-10-31),
   exchange minimum sizes ON. A pair is killed if any pre-registered rule fires (lab/validate.py KILL_RULES).
2. Unit of the ranking: a setup together with the symbols on which it survives every rule in point 1.
3. Eligible for the top three: at least one surviving symbol AND at least 50 trades pooled over the
   surviving symbols in the first 36 months. 30-49 pooled trades = "thin": listed, never ranked top three.
4. Rank eligible setups by the total of the five 1-5 scores (max 25). Ties: (a) the one whose pooled Sharpe
   survives the multiple-testing haircut, (b) more surviving symbols, (c) FAMILY == "baseline" (boring
   first), (d) higher out-of-sample profit / max drawdown.
5. Finalists = the top three eligible setups. If fewer than three are eligible the open slots are filled by
   (a) thin survivors by score, then (b) killed setups by their best pair (fewest rules failed, then higher
   walk-forward out-of-sample return), traded on that one symbol. Every fill-in is labelled
   "paper only, not a candidate for live".
6. Parameters for a finalist: DEFAULT_PARAMS, unless one grid point was chosen by the walk-forward in at
   least half of all fold x surviving-symbol cases, differs from DEFAULT_PARAMS, AND at least 80% of the
   grid is profitable on every surviving symbol. This is decided before the holdout is opened.
7. The last 12 months are opened ONCE, for the finalists' (setup, symbol) pairs only, with the parameters
   from point 6. A pair that loses money there is killed. No replacement finalist is drawn afterwards.
8. Buy-and-hold gate (owner): a finalist passes only if its net return beats the same symbol's 1x
   buy-and-hold net return over the full window on every symbol it trades. Risk-adjusted comparisons are
   reported separately and never substitute for the gate.
9. Multiple testing: Bonferroni haircut with n = 87 for a pair and n = 29 for a setup's pooled book.
   A setup whose pooled Sharpe does not survive is labelled "unproven" whatever its rank.
10. Random-entry baseline (Radar note 1) is reported for finalists: same signals moved to random days
   (same hour of day, same sides, same stop/target distance in ATR). Informational, not a kill rule.
"""
DISCLOSURE = """
What had already been seen when these rules were written: the phase-1 smoke run of run_all on the first
36 months (old loader, no exchange minimums), which printed net return, max drawdown, Sharpe and trade
count per pair for DEFAULT_PARAMS. No walk-forward, stress, grid, Monte Carlo, regime or holdout result
of any Radar setup had been produced. The kill thresholds were fixed in phase 1 before that smoke run.
The score bands are round numbers tied to the 0.5% risk budget (10R = 5% of equity), not to that output.
"""


def score_oos(x):
    return 1 if not x > 0 else 2 if x < 0.02 else 3 if x < 0.05 else 4 if x <= 0.10 else 5


def score_dd(x):
    return 5 if x < 0.05 else 4 if x < 0.10 else 3 if x < 0.15 else 2 if x < 0.25 else 1


def score_trades(n):
    return 1 if n < 30 else 2 if n < 50 else 3 if n < 100 else 4 if n < 200 else 5


def score_streak(n):
    return 5 if n <= 4 else 4 if n <= 6 else 3 if n <= 9 else 2 if n <= 14 else 1


def score_regime(b, s):
    if b > 0 and s > 0:
        return 5
    if b + s > 0:
        return 4 if min(b, s) >= -0.03 else 3
    return 2 if b + s >= -0.05 else 1


def primary_cfg() -> ExecConfig:
    f = exchange_filters()
    if not f:
        raise RuntimeError("bot/instruments.py not importable: cannot build the primary configuration")
    return ExecConfig(filters=f)


def prereg() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    if (WORK / "stage1.pkl").exists() and (OUT / "PREREG.md").exists():
        raise SystemExit("PREREG.md was written before stage 1 and is kept as it is; delete phase2/ to start over")
    lines = ["# Phase 2 pre-registration", "",
             f"Written {pd.Timestamp.now(tz='UTC'):%Y-%m-%d %H:%M} UTC, before stage 1 was run.", "",
             "## Kill rules (pair level, fixed in phase 1)", ""]
    lines += [f"- `{k}` = {v}" for k, v in V.KILL_RULES.items()]
    lines += ["", "Order in which a failed pair is attributed to a rule:", ""]
    lines += [f"{i}. {txt}" for i, (_, txt) in enumerate(KILL_ORDER, 1)]
    lines += ["", "## Score bands (setup level, 1 = worst, 5 = best)", ""]
    for key, d in SCORE_BANDS.items():
        lines += [f"**{key}** - {d['what']}", ""] + [f"- {s}: {txt}" for s, txt in d["bands"]] + [""]
    lines += ["## Protocol", PROTOCOL, "## Disclosure", DISCLOSURE,
              "## Exchange minimum sizes used by the primary runs (UNVERIFIED, from bot/instruments.py)", "",
              "| symbol | lot step | min quantity | min notional |", "|---|---|---|---|"]
    lines += [f"| {s} | {a:g} | {b:g} | ${c:g} |" for s, (a, b, c) in primary_cfg().filters.items()]
    (OUT / "PREREG.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


# ======================================================================================
# stage 1: every pair, first 36 months only
# ======================================================================================
def failed_keys(rep: dict) -> list[str]:
    """Pre-registered rules that fire, as keys of KILL_ORDER (holdout handled in stage 2)."""
    k, d, out = V.KILL_RULES, rep["dev"], []
    if not rep["audit"]["passed"]:
        out.append("audit")
    if d["trades"] < k["min_trades_dev"]:
        out.append("trades")
    if not d["net_return"] > k["min_dev_net_return"]:
        out.append("net")
    pf = d["profit_factor"]
    if not (pf == pf and pf >= k["min_profit_factor"]):
        out.append("pf")
    if not rep["stress"].loc["fees_x2_slippage_x2", "net_return"] > k["min_stress_net_return"]:
        out.append("stress")
    if not rep["walk_forward"]["net_return"] > k["min_wf_oos_net_return"]:
        out.append("wf")
    if rep["neighbourhood"]["share_profitable"] < k["min_share_profitable"]:
        out.append("grid")
    mc = rep["monte_carlo"]
    if not mc["prob_loss"] <= k["max_mc_prob_loss"]:
        out.append("mc_loss")
    if not mc["max_dd_p95"] <= k["max_mc_dd95"]:
        out.append("mc_dd")
    return out


def stage1(only: list[str] | None = None) -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    cfg, plain = primary_cfg(), ExecConfig()
    d0, d1 = V.dev_window()
    symbols = spec_data().SYMBOLS
    out, t00 = {}, time.perf_counter()
    for name in RA.discover():
        if only and name not in only:
            continue
        module = RA.load_module(name)
        for sym in symbols:
            t0 = time.perf_counter()
            rep = V.validate_setup(module, sym, n_tests=N_PAIR_TESTS, cfg=cfg, with_holdout=False)
            bank = V.SignalBank(module, sym, upto=d1)
            nof = bank.run(None, d0, d1, plain)
            base, wfr = rep["dev_result"], rep["walk_forward_result"]
            rec = dict(
                module=name, symbol=sym, timeframe=module.TIMEFRAME, family=getattr(module, "FAMILY", ""),
                dev=rep["dev"], failed=failed_keys(rep), warnings=rep["verdict"]["warnings"],
                audit=rep["audit"], stress=rep["stress"], grid=rep["neighbourhood"],
                grid_table=rep["neighbourhood_table"], wf=rep["walk_forward"], wf_folds=rep["walk_forward_folds"],
                wf_equity=wfr.equity, wf_trades=wfr.trades, mc=rep["monte_carlo"],
                mc_shuffle=rep["monte_carlo_shuffle"], haircut=rep["haircut"], dsr=rep["deflated_sharpe"],
                yearly=rep["yearly"], regime=rep["regime"], equity=base.equity, in_position=base.in_position,
                trades=base.trades, diag=base.diagnostics,
                nofilter=dict(net_return=nof.net_return, trades=len(nof.trades),
                              max_dd=M.max_drawdown(nof.equity, 1000.0), sharpe=M.sharpe(nof.equity, 1000.0),
                              profit_factor=M.profit_factor(nof.trades["net"]) if len(nof.trades) else float("nan")),
                seconds=time.perf_counter() - t0)
            out[(name, sym)] = rec
            d = rec["dev"]
            print(f"{name:28s} {sym:8s} trades {d['trades']:4d} net {d['net_return']:+7.2%} wfOOS "
                  f"{rec['wf']['net_return']:+7.2%} failed {','.join(rec['failed']) or '-':30s} "
                  f"{rec['seconds']:.1f}s", flush=True)
    with open(WORK / "stage1.pkl", "wb") as f:
        pickle.dump(out, f)
    print(f"stage 1: {len(out)} pairs in {time.perf_counter() - t00:.0f}s -> {WORK / 'stage1.pkl'}")


def load_stage1() -> dict:
    with open(WORK / "stage1.pkl", "rb") as f:
        return pickle.load(f)


# ======================================================================================
# rank: kill table, books, scores, finalists (first 36 months only)
# ======================================================================================
def first_kill(failed: list[str]) -> str:
    for key, txt in KILL_ORDER:
        if key in failed:
            return txt
    return ""


def on_1h_grid(series: pd.Series, grid: pd.DatetimeIndex, fill) -> pd.Series:
    """A bar-close series on its own timeframe -> the 1h grid (value appears at the bar's close)."""
    bar = pd.Timedelta(np.median(np.diff(series.index.values[:50])))
    s = series.copy()
    s.index = s.index + (bar - pd.Timedelta(hours=1))
    return s.reindex(grid).ffill().fillna(fill)


def regime_pnl(equity: pd.Series, labels: pd.Series, e0: float = 1000.0) -> dict:
    """Dollar P&L of a bar-close curve split by the regime label of each bar."""
    pnl = equity.diff()
    pnl.iloc[0] = equity.iloc[0] - e0
    lab = labels.reindex(equity.index)
    return {g: float(pnl[lab == g].sum()) for g in ("bull", "bear", "sideways", "unknown")}


def book(recs: list[dict], equity_key="equity", trades_key="trades", e0: float = 1000.0) -> dict:
    """The book that trades one setup on several symbols = sum of the per-symbol runs."""
    sym0 = recs[0]["symbol"]
    grid = get_frame(sym0, "1h").index
    lo = min(r[equity_key].index[0] for r in recs)
    hi = max(r[equity_key].index[-1] + pd.Timedelta(np.median(np.diff(r[equity_key].index.values[:50])))
             - pd.Timedelta(hours=1) for r in recs)
    grid = grid[(grid >= lo) & (grid <= hi)]
    curve = sum(on_1h_grid(r[equity_key], grid, e0) - e0 for r in recs) + e0
    trades = pd.concat([r[trades_key].assign(symbol=r["symbol"]) for r in recs if len(r[trades_key])],
                       ignore_index=True) if any(len(r[trades_key]) for r in recs) else recs[0][trades_key]
    if len(trades):
        trades = trades.sort_values(["exit_time", "entry_time"], kind="stable").reset_index(drop=True)
    st = M.equity_stats(curve, e0, pd.Timedelta(hours=1))
    ts = M.trade_stats(trades)
    return dict(curve=curve, trade_list=trades, **st, **ts)


def rank() -> None:
    s1 = load_stage1()
    d0, d1 = V.dev_window()
    labels = {}
    rows = []
    for (name, sym), r in s1.items():
        d = r["dev"]
        rows.append(dict(
            module=name, symbol=sym, tf=r["timeframe"], family=r["family"], trades=d["trades"],
            net=d["net_return"], pf=d["profit_factor"], max_dd=d["max_dd"], sharpe=d["sharpe"],
            stress=float(r["stress"].loc["fees_x2_slippage_x2", "net_return"]), wf_oos=r["wf"]["net_return"],
            grid_ok=r["grid"]["share_profitable"], mc_loss=r["mc"]["prob_loss"], mc_dd95=r["mc"]["max_dd_p95"],
            audit=r["audit"]["passed"], blocked_min_size=r["diag"].get("rejected_min_size", 0),
            nofilter_net=r["nofilter"]["net_return"], nofilter_trades=r["nofilter"]["trades"],
            haircut_sharpe=r["haircut"]["haircut_sharpe"], n_failed=len(r["failed"]),
            failed=",".join(r["failed"]), killed_by=first_kill(r["failed"]),
            leak_found=not r["audit"]["prefix"]["ok"], delay_anomaly=bool(r["audit"]["delay"]["improves_when_delayed"]),
            prefix_cuts=r["audit"]["prefix"]["cuts_checked"],
            status="killed" if r["failed"] else ("survivor" if r["haircut"]["haircut_sharpe"] > 0 else "unproven")))
    kt = pd.DataFrame(rows)
    # the audit rule has two parts; say which one fired (no pair has a future leak if leak_found is False)
    m = (kt["killed_by"] == KILL_ORDER[0][1]) & ~kt["leak_found"]
    kt.loc[m, "killed_by"] = "audit flag: better when executed one bar late (no future leak found)"
    kt.to_csv(WORK / "kill_table.csv", index=False)

    setups = []
    for name, g in kt.groupby("module", sort=False):
        alive = g[g["n_failed"] == 0]["symbol"].tolist()
        if not alive:
            continue
        recs = [s1[(name, sym)] for sym in alive]
        b = book(recs)
        wf_sum = float(sum(r["wf"]["net_return"] for r in recs))
        wf_years = float(np.mean([r["wf"]["years"] for r in recs]))
        oos_py = (1 + wf_sum) ** (1 / wf_years) - 1 if wf_sum > -1 else -1.0
        bear = side = 0.0
        for r in recs:
            key = (r["symbol"], r["timeframe"])
            if key not in labels:
                labels[key] = M.regime_labels(get_frame(*key))
            rp = regime_pnl(r["equity"], labels[key])
            bear += rp["bear"] / 1000.0
            side += rp["sideways"] / 1000.0
        hc = V.haircut_sharpe(b["sharpe"], b["years"], N_SETUP_TESTS)
        sc = dict(oos_profit=score_oos(oos_py), max_dd=score_dd(b["max_dd"]), trades=score_trades(b["trades"]),
                  lose_streak=score_streak(b["longest_losing_streak"]), bear_sideways=score_regime(bear, side))
        setups.append(dict(
            module=name, family=recs[0]["family"], tf=recs[0]["timeframe"], symbols=alive, n_symbols=len(alive),
            pooled_trades=b["trades"], book_net=b["net_return"], book_max_dd=b["max_dd"], book_sharpe=b["sharpe"],
            book_pf=b["profit_factor"], book_win_rate=b["win_rate"], book_avg_R=b["avg_R"],
            lose_streak=b["longest_losing_streak"], worst_month=b["worst_month"],
            worst_month_label=b["worst_month_label"], wf_oos_sum=wf_sum, wf_oos_per_year=oos_py,
            bear=bear, sideways=side, haircut_sharpe=hc["haircut_sharpe"], p_bonferroni=hc["p_bonferroni"],
            proven=bool(hc["haircut_sharpe"] > 0), significant_5pct=bool(hc["significant_5pct"]), **{f"s_{k}": v for k, v in sc.items()},
            score=int(sum(sc.values())),
            oos_over_dd=wf_sum / b["max_dd"] if b["max_dd"] > 0 else float("nan"),
            eligibility="eligible" if b["trades"] >= 50 else ("thin" if b["trades"] >= 30 else "too few trades")))
    st = pd.DataFrame(setups)
    if len(st):
        st["baseline"] = st["family"].eq("baseline")
        st = st.sort_values(["score", "proven", "n_symbols", "baseline", "oos_over_dd"],
                            ascending=[False, False, False, False, False], kind="stable").reset_index(drop=True)
    st.to_csv(WORK / "setups.csv", index=False)

    # ---- finalists (protocol points 3-6) ----
    finalists = []
    if len(st):
        for _, r in st[st["eligibility"] == "eligible"].head(3).iterrows():
            finalists.append(dict(module=r["module"], symbols=list(r["symbols"]), label="candidate"))
        for _, r in st[st["eligibility"] == "thin"].iterrows():
            if len(finalists) < 3:
                finalists.append(dict(module=r["module"], symbols=list(r["symbols"]),
                                      label="paper only, not a candidate for live (thin: under 50 trades)"))
    if len(finalists) < 3:
        taken = {f["module"] for f in finalists}
        dead = kt[~kt["module"].isin(taken) & (kt["trades"] > 0)].sort_values(["n_failed", "wf_oos"], ascending=[True, False])
        for _, r in dead.iterrows():
            if len(finalists) >= 3:
                break
            if r["module"] in taken:
                continue
            taken.add(r["module"])
            finalists.append(dict(module=r["module"], symbols=[r["symbol"]],
                                  label="paper only, not a candidate for live (killed: " + r["killed_by"] + ")"))
    for rank_no, f in enumerate(finalists, 1):
        module = RA.load_module(f["module"])
        default = module_params(module)
        grid_keys = list(getattr(module, "PARAM_GRID", {}) or {})
        picks, stable = [], True
        for sym in f["symbols"]:
            r = s1[(f["module"], sym)]
            picks += [json.dumps({k: c[k] for k in grid_keys}, sort_keys=True) for c in r["wf_folds"]["chosen"]]
            stable &= r["grid"]["share_profitable"] >= 0.80
        top, cnt = pd.Series(picks).value_counts().index[0], int(pd.Series(picks).value_counts().iloc[0])
        top_d = json.loads(top)
        differs = any(default.get(k) != v for k, v in top_d.items())
        switch = bool(differs and cnt / len(picks) >= 0.5 and stable)
        f.update(rank=rank_no, timeframe=module.TIMEFRAME, params={**default, **top_d} if switch else default,
                 params_source=("walk-forward most-often-chosen grid point" if switch else "DEFAULT_PARAMS"),
                 wf_most_chosen=top_d, wf_most_chosen_share=cnt / len(picks), wf_most_chosen_differs=bool(differs),
                 grid_stable_80=bool(stable))
    (WORK / "finalists.json").write_text(json.dumps(finalists, indent=1, default=str))

    pd.set_option("display.width", 250, "display.max_columns", 40, "display.max_rows", 200)
    print("== kill table: rule that killed each pair (first in the pre-registered order) ==")
    print(kt.groupby("killed_by").size().sort_values(ascending=False).to_string())
    print("\n== how many pairs each rule fails (a pair can fail several) ==")
    print(pd.Series([k for f in kt["failed"] for k in f.split(",") if k]).value_counts().to_string())
    print("\n== pairs with no rule fired ==")
    print(kt[kt.n_failed == 0][["module", "symbol", "tf", "trades", "net", "pf", "max_dd", "sharpe", "stress", "wf_oos",
                                "grid_ok", "mc_loss", "mc_dd95", "nofilter_net", "nofilter_trades", "status"]].round(3).to_string())
    print("\n== setups (books) ==")
    if len(st):
        print(st[["module", "family", "tf", "symbols", "pooled_trades", "book_net", "book_max_dd", "book_sharpe",
                  "lose_streak", "wf_oos_per_year", "bear", "sideways", "s_oos_profit", "s_max_dd", "s_trades",
                  "s_lose_streak", "s_bear_sideways", "score", "proven", "eligibility"]].round(3).to_string())
    print("\n== finalists ==")
    print(json.dumps(finalists, indent=1, default=str))


# ======================================================================================
# stage 2: the last 12 months, once, finalists only
# ======================================================================================
def _brief(res, df) -> dict:
    m = M.compute_metrics(res, df)
    keep = ("net_return", "cagr", "max_dd", "sharpe", "profit_factor", "win_rate", "avg_R", "trades",
            "longest_losing_streak", "worst_month", "worst_month_label", "exposure", "fees_paid", "funding_paid",
            "slippage_paid", "gross_pnl", "net_pnl", "bh_net_return", "bh_max_dd", "bh_sharpe", "bh_cagr",
            "excess_vs_bh", "return_over_maxdd", "years", "stop_exits", "target_exits", "time_exits", "signal_exits",
            "avg_bars_held", "long_share", "trades_per_year", "final_equity")
    return {k: m[k] for k in keep if k in m}


def stage2(dry: bool = False) -> None:
    flag = WORK / "holdout_opened.json"
    if flag.exists() and not dry:
        raise SystemExit(f"the holdout was already opened ({flag}); it is opened once")
    finalists = json.loads((WORK / "finalists.json").read_text())
    cfg, plain = primary_cfg(), ExecConfig()
    d0, d1 = V.dev_window()
    h0, h1 = V.holdout_window()
    s0, s1 = V.spec_window()
    if dry:                                         # rehearsal on data that is already open: no holdout bar is read
        h0, h1 = d1 + pd.Timedelta(seconds=1) - pd.DateOffset(months=12), d1
        s1 = d1
    out, opened = {}, []
    for f in finalists:
        module = RA.load_module(f["module"])
        params = f["params"]
        for sym in f["symbols"]:
            bank = V.SignalBank(module, sym, upto=s1)
            df = bank.df
            runs = {w: bank.run(params, a, b, cfg) for w, (a, b) in
                    (("dev", (d0, d1)), ("holdout", (h0, h1)), ("full", (s0, s1)))}
            rec = dict(module=f["module"], symbol=sym, params=params,
                       **{w: _brief(r, df) for w, r in runs.items()},
                       nofilter={w: _brief(bank.run(params, a, b, plain), df) for w, (a, b) in
                                 (("dev", (d0, d1)), ("holdout", (h0, h1)), ("full", (s0, s1)))},
                       regime_full=M.regime_split(runs["full"], df), regime_holdout=M.regime_split(runs["holdout"], df),
                       regime_dev=M.regime_split(runs["dev"], df),
                       yearly_full=M.yearly_split(runs["full"], df),
                       equity={w: r.equity for w, r in runs.items()}, trades={w: r.trades for w, r in runs.items()},
                       diag={w: r.diagnostics for w, r in runs.items()},
                       stress_full=V.stress(module, sym, params, s0, s1, cfg, bank=bank),
                       stress_dev=V.stress(module, sym, params, d0, d1, cfg, bank=bank),
                       stress_holdout=V.stress(module, sym, params, h0, h1, cfg, bank=bank),
                       prefix_full=V.prefix_invariance(module, sym, params, upto=s1, n_random=12),
                       delay_dev=V.delay_test(module, sym, params, d0, d1, cfg, bank=bank),
                       null_dev=V.random_entry_null(runs["dev"], df, d0, d1, cfg, n=300),
                       null_full=V.random_entry_null(runs["full"], df, s0, s1, cfg, n=300),
                       bh={w: M.buy_and_hold(df, sym, a, b, cfg).stats for w, (a, b) in
                           (("dev", (d0, d1)), ("holdout", (h0, h1)), ("full", (s0, s1)))},
                       bh_equity_full=M.buy_and_hold(df, sym, s0, s1, cfg).equity)
            df1, sig1, mult = module_signals_1h(module, sym, params, upto=s1)       # same setup, 1h execution bars
            rec["exec_1h"] = {w: _brief(run_backtest(df1, sig1, sym, a, b, cfg, hold_mult=mult), df1) for w, (a, b) in
                              (("dev", (d0, d1)), ("holdout", (h0, h1)), ("full", (s0, s1)))}
            out[(f["module"], sym)] = rec
            opened.append([f["module"], sym])
            print(f"{f['module']:28s} {sym:8s} dev {rec['dev']['net_return']:+7.2%} ({rec['dev']['trades']} tr)  "
                  f"holdout {rec['holdout']['net_return']:+7.2%} ({rec['holdout']['trades']} tr)  "
                  f"full {rec['full']['net_return']:+7.2%}  B&H full {rec['bh']['full']['net_return']:+8.2%}", flush=True)
    with open(WORK / ("stage2_dry.pkl" if dry else "stage2.pkl"), "wb") as fh:
        pickle.dump(out, fh)
    if not dry:
        flag.write_text(json.dumps(dict(opened_utc=str(pd.Timestamp.now(tz="UTC")), pairs=opened,
                                        n_pairs=len(opened), window=[str(h0), str(h1)]), indent=1))
    print(f"{'REHEARSAL (no holdout bar read)' if dry else 'HOLDOUT OPENED'} for {len(opened)} setup-symbol pairs")


def load_stage2(dry: bool = False) -> dict:
    with open(WORK / ("stage2_dry.pkl" if dry else "stage2.pkl"), "rb") as f:
        return pickle.load(f)


# ======================================================================================
# final: gate, final checks, portfolio pass
# ======================================================================================
def _windows(dry: bool):
    d0, d1 = V.dev_window()
    h0, h1 = V.holdout_window()
    s0, s1 = V.spec_window()
    if dry:
        h0, h1 = d1 + pd.Timedelta(seconds=1) - pd.DateOffset(months=12), d1
        s1 = d1
    return dict(dev=(d0, d1), holdout=(h0, h1), full=(s0, s1))


def _f(x):
    """JSON-safe number."""
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    return x


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, pd.Timestamp):
        return str(o)
    return _f(o)


def _streak_p95(rets: np.ndarray, n_paths: int = 2000, seed: int = 0) -> int:
    """95th percentile of the longest losing streak when the trade list is resampled."""
    rng = np.random.default_rng(seed)
    n, out = len(rets), []
    for _ in range(n_paths):
        r = rets[rng.integers(0, n, n)] < 0
        best = cur = 0
        for x in r:
            cur = cur + 1 if x else 0
            best = max(best, cur)
        out.append(best)
    return int(np.percentile(out, 95))


def _low_avgR(R: np.ndarray, k: int = 20, n_paths: int = 5000, seed: int = 0) -> float:
    """5th percentile of the average R of k trades drawn from the list: a run below it is unusual."""
    rng = np.random.default_rng(seed)
    return float(np.percentile(R[rng.integers(0, len(R), (n_paths, k))].mean(axis=1), 5))


def final(dry: bool = False) -> None:
    s1, s2 = load_stage1(), load_stage2(dry)
    finalists = json.loads((WORK / "finalists.json").read_text())
    kt, st = pd.read_csv(WORK / "kill_table.csv"), pd.read_csv(WORK / "setups.csv")
    cfg, plain = primary_cfg(), ExecConfig()
    W = _windows(dry)
    res = dict(dry=dry, windows={k: [str(a), str(b)] for k, (a, b) in W.items()}, finalists=[])
    items_all, items_alive = [], []
    for f in finalists:
        module = RA.load_module(f["module"])
        fin = dict(rank=f["rank"], module=f["module"], label_dev=f["label"], timeframe=f["timeframe"],
                   params=f["params"], params_source=f["params_source"], wf_most_chosen=f["wf_most_chosen"],
                   wf_most_chosen_share=f["wf_most_chosen_share"], grid_stable_80=f["grid_stable_80"],
                   family=getattr(module, "FAMILY", ""), one_liner=getattr(module, "ONE_LINER", ""),
                   why=getattr(module, "WHY", ""), risk=getattr(module, "RISK", ""),
                   regime_tag=getattr(module, "REGIME", ""), warmup_bars=int(getattr(module, "WARMUP_BARS", 0)),
                   required_columns=list(getattr(module, "REQUIRED_COLUMNS", ())), pairs=[])
        for sym in f["symbols"]:
            a, b = s1[(f["module"], sym)], s2[(f["module"], sym)]
            full, hold, dev, bh = b["full"], b["holdout"], b["dev"], b["bh"]
            rg = b["regime_full"]
            est_equal_dd = full["net_return"] * bh["full"]["max_dd"] / full["max_dd"] if full["max_dd"] > 0 else None
            folds = a["wf_folds"]
            pair = dict(
                symbol=sym, dev=dev, holdout=hold, full=full, bh=bh, nofilter=b["nofilter"], exec_1h=b["exec_1h"],
                holdout_pass=bool(hold["net_return"] > 0),
                gate_abs_full=bool(full["net_return"] > bh["full"]["net_return"]),
                gate_abs_holdout=bool(hold["net_return"] > bh["holdout"]["net_return"]),
                regime_full={g: dict(strat=float(rg.loc[g, "strat_return"]), bh=float(rg.loc[g, "bh_return"]),
                                     share=float(rg.loc[g, "share_of_bars"]), trades=int(rg.loc[g, "trades"]))
                             for g in ("bull", "bear", "sideways") if g in rg.index},
                regime_holdout={g: dict(strat=float(b["regime_holdout"].loc[g, "strat_return"]),
                                        bh=float(b["regime_holdout"].loc[g, "bh_return"]),
                                        share=float(b["regime_holdout"].loc[g, "share_of_bars"]))
                                for g in ("bull", "bear", "sideways") if g in b["regime_holdout"].index},
                risk_adjusted=dict(sharpe=full["sharpe"], bh_sharpe=bh["full"]["sharpe"],
                                   ret_over_dd=full["return_over_maxdd"],
                                   bh_ret_over_dd=bh["full"]["net_return"] / bh["full"]["max_dd"],
                                   est_return_at_bh_drawdown=est_equal_dd, bh_return=bh["full"]["net_return"]),
                wf=dict(oos_net=a["wf"]["net_return"], oos_trades=a["wf"]["trades"], oos_max_dd=a["wf"]["max_dd"],
                        oos_sharpe=a["wf"]["sharpe"], share_positive_folds=a["wf"]["wf_share_positive_folds"],
                        folds=[dict(oos_start=str(r["oos_start"])[:10], oos_end=str(r["oos_end"])[:10],
                                    oos_net=float(r["oos_net_return"]), oos_trades=int(r["oos_trades"]),
                                    chosen={k: r["chosen"][k] for k in (getattr(module, "PARAM_GRID", {}) or {})})
                               for _, r in folds.iterrows()]),
                stress_dev={k: float(v) for k, v in a["stress"]["net_return"].items()},
                stress_full={k: float(v) for k, v in b["stress_full"]["net_return"].items()},
                stress_holdout={k: float(v) for k, v in b["stress_holdout"]["net_return"].items()},
                grid=dict(share_profitable=a["grid"]["share_profitable"], n=a["grid"]["n_combos"],
                          lo=a["grid"]["net_return_min"], median=a["grid"]["net_return_median"],
                          hi=a["grid"]["net_return_max"], default=a["grid"]["default_net_return"]),
                mc=dict(prob_loss=a["mc"]["prob_loss"], p05=a["mc"]["final_return_p05"], dd95=a["mc"]["max_dd_p95"]),
                audit=dict(leak_found=not (a["audit"]["prefix"]["ok"] and b["prefix_full"]["ok"]),
                           cuts_dev=a["audit"]["prefix"]["cuts_checked"], cuts_full=b["prefix_full"]["cuts_checked"],
                           rows_differ=a["audit"]["prefix"]["rows_any"] + b["prefix_full"]["rows_any"],
                           delay0=a["audit"]["delay"]["net_return_delay0"], delay1=a["audit"]["delay"]["net_return_delay1"],
                           delay2=a["audit"]["delay"]["net_return_delay2"],
                           improves_when_delayed=a["audit"]["delay"]["improves_when_delayed"],
                           collapses_when_delayed=a["audit"]["delay"]["collapses_when_delayed"]),
                null_dev=b["null_dev"], null_full=b["null_full"],
                haircut_pair=dict(sharpe=a["haircut"]["sharpe"], p=a["haircut"]["p_value"],
                                  p_bonf=a["haircut"]["p_bonferroni"], haircut_sharpe=a["haircut"]["haircut_sharpe"]),
                blocked_min_size=dict(dev=b["diag"]["dev"].get("rejected_min_size", 0),
                                      full=b["diag"]["full"].get("rejected_min_size", 0)),
                yearly_full=[dict(year=int(y), net=float(r["net_return"]), bh=float(r["bh_return"]),
                                  trades=int(r["trades"]), max_dd=float(r["max_dd"]))
                             for y, r in b["yearly_full"].iterrows()],
                streak_p95_dev=_streak_p95(b["trades"]["dev"]["ret"].to_numpy(float)) if len(b["trades"]["dev"]) > 5 else None,
                low_avgR20_dev=_low_avgR(b["trades"]["dev"]["R"].to_numpy(float)) if len(b["trades"]["dev"]) > 5 else None)
            fin["pairs"].append(pair)
            items_all.append((module, sym, f["params"]))
            if pair["holdout_pass"]:
                items_alive.append((module, sym, f["params"]))
        alive = [p["symbol"] for p in fin["pairs"] if p["holdout_pass"]]
        fin["symbols_dev"] = f["symbols"]
        fin["symbols_alive"] = alive
        fin["gate_abs"] = bool(alive) and all(p["gate_abs_full"] for p in fin["pairs"] if p["holdout_pass"])
        # books (sum of the per-symbol runs) per window, all dev-surviving symbols and holdout-surviving ones
        fin["book"] = {}
        for tag, syms in (("dev_symbols", f["symbols"]), ("alive_symbols", alive)):
            fin["book"][tag] = {}
            for w in ("dev", "holdout", "full"):
                if not syms:
                    continue
                recs = [dict(symbol=sy, equity=s2[(f["module"], sy)]["equity"][w],
                             trades=s2[(f["module"], sy)]["trades"][w]) for sy in syms]
                e0 = 1000.0
                bk = book(recs)
                bhc = sum(on_1h_grid(M.buy_and_hold(get_frame(sy, f["timeframe"]), sy, *W[w], cfg).equity,
                                     bk["curve"].index, e0) for sy in syms) / len(syms)
                rets = bk["trade_list"]["net"].to_numpy(float) / e0 if len(bk["trade_list"]) else np.array([])
                mc = V.monte_carlo(rets, n_paths=5000) if len(rets) > 1 else {}
                fin["book"][tag][w] = dict(
                    net=bk["net_return"], max_dd=bk["max_dd"], sharpe=bk["sharpe"], trades=bk["trades"],
                    win_rate=bk["win_rate"], pf=bk["profit_factor"], avg_R=bk["avg_R"],
                    lose_streak=bk["longest_losing_streak"], worst_month=bk["worst_month"],
                    worst_month_label=bk["worst_month_label"], fees=bk["fees_paid"], funding=bk["funding_paid"],
                    slippage=bk["slippage_paid"], bh_equal_weight_net=float(bhc.iloc[-1] / e0 - 1),
                    bh_equal_weight_max_dd=M.max_drawdown(bhc, e0), mc_prob_loss=mc.get("prob_loss"),
                    mc_dd95=mc.get("max_dd_p95"), mc_p05=mc.get("final_return_p05"),
                    streak_p95=_streak_p95(rets) if len(rets) > 5 else None,
                    low_avgR20=_low_avgR(bk["trade_list"]["R"].to_numpy(float)) if len(rets) > 5 else None)
        row = st[st["module"] == f["module"]]
        fin["scores"] = {k: int(row.iloc[0][f"s_{k}"]) for k in SCORE_BANDS} if len(row) else {}
        fin["score"] = int(row.iloc[0]["score"]) if len(row) else None
        fin["setup_stats"] = {k: _f(row.iloc[0][k]) for k in
                              ("pooled_trades", "book_net", "book_max_dd", "book_sharpe", "lose_streak",
                               "wf_oos_sum", "wf_oos_per_year", "bear", "sideways", "haircut_sharpe",
                               "p_bonferroni", "proven", "significant_5pct", "eligibility")} if len(row) else {}
        # own $1,000 book per setup, as the bot runs it (Shield per book), on 1h execution bars
        for key, syms in (("own_book", f["symbols"]), ("own_book_alive", alive)):
            fin[key] = {}
            if not syms:
                continue
            specs, frames = P.build_specs([(module, sy, f["params"]) for sy in syms], upto=W["full"][1])
            for w, (a0, b0) in W.items():
                for tag, sh in (("no_shield", None), ("shield", P.ShieldRules())):
                    fin[key][f"{w}_{tag}"] = P.portfolio_metrics(P.run_portfolio(specs, frames, a0, b0, cfg, sh))
        res["finalists"].append(fin)

    # ---- shared $1,000 account ----
    port = {}
    for tag, items in (("all_finalist_pairs", items_all), ("holdout_survivors_only", items_alive)):
        if not items:
            continue
        specs, frames = P.build_specs(items, upto=W["full"][1])
        port[tag] = dict(streams=[f"{sp.name}/{sp.symbol}" for sp in specs])
        for w, (a0, b0) in W.items():
            for stag, sh in (("no_shield", None), ("shield", P.ShieldRules())):
                r = P.run_portfolio(specs, frames, a0, b0, cfg, sh)
                m = P.portfolio_metrics(r)
                m["by_stream"] = {k: dict(trades=int(len(g)), net=float(g["net"].sum()))
                                  for k, g in r.trades.groupby(r.trades["setup"] + "/" + r.trades["symbol"])} if len(r.trades) else {}
                m["monthly_worst"] = m["worst_month"]
                port[tag][f"{w}_{stag}"] = m
                if w == "full":
                    r.equity.resample("1D").last().to_csv(WORK / f"portfolio_{tag}_{stag}{'_dry' if dry else ''}.csv")
            r = P.run_portfolio(specs, frames, *W["full"], plain, P.ShieldRules())        # no exchange minimums, labelled
            port[tag]["full_shield_nofilter"] = P.portfolio_metrics(r)
            r = P.run_portfolio(specs, frames, *W["full"], plain, None)
            port[tag]["full_no_shield_nofilter"] = P.portfolio_metrics(r)
    res["portfolio"] = port
    res["bh"] = {}
    for sym in spec_data().SYMBOLS:
        df = get_frame(sym, "1h")
        res["bh"][sym] = {w: {k: M.buy_and_hold(df, sym, a0, b0, cfg).stats[k] for k in
                              ("net_return", "max_dd", "sharpe", "cagr", "funding_paid", "price_return")}
                          for w, (a0, b0) in W.items()}
    eqw = {}
    for w, (a0, b0) in W.items():
        curves = [M.buy_and_hold(get_frame(sy, "1h"), sy, a0, b0, cfg).equity for sy in spec_data().SYMBOLS]
        c = sum(curves) / len(curves)
        eqw[w] = dict(net_return=float(c.iloc[-1] / 1000 - 1), max_dd=M.max_drawdown(c, 1000.0), sharpe=M.sharpe(c, 1000.0))
    res["bh"]["equal_weight"] = eqw
    res["kill_counts"] = kt.groupby("killed_by").size().to_dict()
    res["n_pairs"] = int(len(kt))
    res["n_pairs_alive_dev"] = int((kt["n_failed"] == 0).sum())
    res["multiple_testing"] = {"pairs": V.multiple_testing(N_PAIR_TESTS, 3.0), "setups": V.multiple_testing(N_SETUP_TESTS, 3.0)}
    (WORK / ("final_dry.json" if dry else "final.json")).write_text(json.dumps(_clean(res), indent=1))
    print(f"wrote {WORK / ('final_dry.json' if dry else 'final.json')}")
    for fin in res["finalists"]:
        print(f"#{fin['rank']} {fin['module']} dev symbols {fin['symbols_dev']} alive after holdout {fin['symbols_alive']} "
              f"absolute gate {fin['gate_abs']}")
        for p in fin["pairs"]:
            print(f"   {p['symbol']}: dev {p['dev']['net_return']:+.2%} holdout {p['holdout']['net_return']:+.2%} "
                  f"({p['holdout']['trades']} tr) full {p['full']['net_return']:+.2%} vs B&H {p['bh']['full']['net_return']:+.2%}"
                  f" | 1h-exec full {p['exec_1h']['full']['net_return']:+.2%} | null p(dev) {p['null_dev']['p_value']:.3f}")
    for tag, d in port.items():
        for k, m in d.items():
            if isinstance(m, dict) and "net_return" in m:
                print(f"   portfolio {tag:24s} {k:26s} net {m['net_return']:+8.2%} maxDD {m['max_dd']:6.2%} "
                      f"Sharpe {m['sharpe']:5.2f} trades {m['trades']:4d} halts {m['halts']:3d}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "prereg":
        prereg()
    elif cmd == "stage1":
        stage1(sys.argv[2:] or None)
    elif cmd == "rank":
        rank()
    elif cmd == "stage2":
        stage2(dry="--dry" in sys.argv)
    elif cmd == "final":
        final(dry="--dry" in sys.argv)
    else:
        print(__doc__)
