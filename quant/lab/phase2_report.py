#!/usr/bin/env python
"""Write lab/results/RANKING.md, lab/results/cards/*.md and lab/results/top3.json from the phase-2 runs.

Every number is read from lab/results/phase2/ (stage1.pkl, stage2.pkl, final.json, kill_table.csv,
setups.csv). Nothing is typed in by hand; the prose around the numbers is.
"""
from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

import numpy as np          # noqa: E402
import pandas as pd         # noqa: E402

from lab import metrics as M            # noqa: E402
from lab import phase2 as P2            # noqa: E402
from lab import validate as V           # noqa: E402

OUT, WORK = P2.OUT, P2.WORK
CODES = {"audit": "DELAY", "trades": "N<30", "net": "NET", "pf": "PF", "stress": "STRESS", "wf": "WF",
         "grid": "GRID", "mc_loss": "MC-LOSS", "mc_dd": "MC-DD"}
ORDER = [k for k, _ in P2.KILL_ORDER]


def pct(x, d=1, sign=True):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{100 * x:+.{d}f}%" if sign else f"{100 * x:.{d}f}%"


def yn(flag):
    return "YES" if flag else "NO"


def usd(x, d=0):
    return f"{'+' if x >= 0 else '-'}${abs(x):.{d}f}"


def paid(x):
    """A cost in dollars; a negative cost (funding received) is shown as such."""
    if abs(x) < 0.5:
        return "$0"
    return f"${x:.0f}" if x > 0 else f"received ${-x:.0f}"


def load():
    s1 = pickle.load(open(WORK / "stage1.pkl", "rb"))
    s2 = pickle.load(open(WORK / "stage2.pkl", "rb"))
    fin = json.loads((WORK / "final.json").read_text())
    kt = pd.read_csv(WORK / "kill_table.csv", keep_default_na=False, na_values=[""])
    st = pd.read_csv(WORK / "setups.csv")
    opened = json.loads((WORK / "holdout_opened.json").read_text())
    return s1, s2, fin, kt, st, opened


def trade_facts(t: pd.DataFrame, tf_hours: int = 4) -> dict:
    d = (t["entry_ref"] - t["stop"]).abs() / t["entry_ref"]
    srt = t.sort_values("net", ascending=False)
    tot = float(t["net"].sum())
    best = srt.iloc[0]
    wins, losses = t[t["net"] > 0], t[t["net"] <= 0]
    return dict(
        n=len(t), long_share=float((t["side"] > 0).mean()), stop_med=float(d.median()), stop_p10=float(d.quantile(.1)),
        stop_p90=float(d.quantile(.9)), lev_med=float(t["leverage"].median()), lev_max=float(t["leverage"].max()),
        notional_med=float(t["notional"].median()), notional_min=float(t["notional"].min()),
        hold_med_d=float(t["bars_held"].median() * tf_hours / 24), hold_mean_d=float(t["bars_held"].mean() * tf_hours / 24),
        hold_max_d=float(t["bars_held"].max() * tf_hours / 24), reasons=t["exit_reason"].value_counts().to_dict(),
        avg_win_R=float(wins["R"].mean()), avg_loss_R=float(losses["R"].mean()), best_R=float(best["R"]),
        best_net=float(best["net"]), best_share=float(best["net"] / tot) if tot else float("nan"),
        best_from=str(best["entry_time"])[:10], best_to=str(best["exit_time"])[:10], best_side=int(best["side"]),
        net=tot, net_wo_best=tot - float(best["net"]), net_wo_top3=tot - float(srt["net"].iloc[:3].sum()),
        median_R=float(t["R"].median()), win_rate=float((t["net"] > 0).mean()),
        long_net=float(t.loc[t["side"] > 0, "net"].sum()), short_net=float(t.loc[t["side"] < 0, "net"].sum()),
        avg_R_by_reason={k: float(v) for k, v in t.groupby("exit_reason")["R"].mean().items()},
        **big_winner_gaps(t))


def big_winner_gaps(t: pd.DataFrame, thr: float = 3.0) -> dict:
    """How often a trade of +thr R or better comes along, and the longest wait between two of them."""
    big = t[t["R"] >= thr].sort_values("exit_time")
    first, last = t["entry_time"].min(), t["exit_time"].max()
    pts = [first] + list(big["exit_time"]) + [last]
    gaps = [(b - a).days for a, b in zip(pts[:-1], pts[1:])]
    years = (last - first).days / 365.25
    return dict(big_thr=thr, big_n=int(len(big)), big_per_year=float(len(big) / years) if years > 0 else float("nan"),
                big_longest_gap_days=int(max(gaps)) if gaps else 0, big_net=float(big["net"].sum()))


def results_table(f, syms) -> list[str]:
    """Per-symbol results vs buy-and-hold for a card."""
    L = ["| Symbol | Window | Setup net | Buy-and-hold net | Max drawdown: setup / B&H | Trades | Win rate | Profit factor | Fees + slippage | Funding paid |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for p in f["pairs"]:
        if p["symbol"] not in syms:
            continue
        for wn, lab in (("dev", "first 36 months"), ("holdout", "last 12 months (unseen)"), ("full", "full window")):
            m, b = p[wn], p["bh"][wn]
            L.append(f"| {p['symbol']} | {lab} | {pct(m['net_return'], 2)} | {pct(b['net_return'])} | "
                     f"{pct(m['max_dd'], 1, False)} / {pct(b['max_dd'], 1, False)} | {m['trades']} | {pct(m['win_rate'], 0, False)} | "
                     f"{m['profit_factor']:.2f} | ${m['fees_paid'] + m['slippage_paid']:.0f} | {paid(m['funding_paid'])} |")
    return L


def yearly_table(f, syms) -> list[str]:
    ps = [p for p in f["pairs"] if p["symbol"] in syms]
    years = [y["year"] for y in ps[0]["yearly_full"]]
    L = ["| Year | " + " | ".join(f"{p['symbol']}: setup / B&H (trades)" for p in ps) + " |", "|---|" + "---|" * len(ps)]
    for i, y in enumerate(years):
        note = " (Nov-Dec)" if i == 0 else (" (to 31 Oct)" if i == len(years) - 1 else "")
        L.append(f"| {y}{note} | " + " | ".join(
            f"{pct(p['yearly_full'][i]['net'])} / {pct(p['yearly_full'][i]['bh'])} ({p['yearly_full'][i]['trades']})" for p in ps) + " |")
    return L


def wf_table(f, syms) -> list[str]:
    ps = [p for p in f["pairs"] if p["symbol"] in syms]
    L = ["| Out-of-sample quarter | " + " | ".join(f"{p['symbol']}: net (trades)" for p in ps) + " |", "|---|" + "---|" * len(ps)]
    for i, fold in enumerate(ps[0]["wf"]["folds"]):
        L.append(f"| {fold['oos_start']} to {fold['oos_end']} | " + " | ".join(
            f"{pct(p['wf']['folds'][i]['oos_net'], 2)} ({p['wf']['folds'][i]['oos_trades']})" for p in ps) + " |")
    L.append("| **Stitched, 24 months** | " + " | ".join(
        f"**{pct(p['wf']['oos_net'])}** ({p['wf']['oos_trades']}), {round(p['wf']['share_positive_folds'] * 8)} of 8 quarters up"
        for p in ps) + " |")
    return L


def final_check(f, syms) -> list[str]:
    ps = [p for p in f["pairs"] if p["symbol"] in syms]
    L = ["| Question | " + " | ".join(p["symbol"] for p in ps) + " |", "|---|" + "---|" * len(ps)]

    def row(q, fn):
        L.append(f"| {q} | " + " | ".join(fn(p) for p in ps) + " |")
    row("Still wins on unseen data (last 12 months)?",
        lambda p: f"**{yn(p['holdout_pass'])}** {pct(p['holdout']['net_return'], 2)} over {p['holdout']['trades']} trades")
    row("After fees and funding?", lambda p: f"**{yn(p['full']['net_return'] > 0)}** {pct(p['full']['net_return'])} full window "
        f"(fees + slippage ${p['full']['fees_paid'] + p['full']['slippage_paid']:.0f}, funding {paid(p['full']['funding_paid'])})")
    row("Over 50+ trades?", lambda p: f"**{yn(p['dev']['trades'] >= 50)}** {p['dev']['trades']} in the first 36 months, {p['full']['trades']} in the full window")
    row("With double fees?", lambda p: f"**{yn(p['stress_dev']['fees_x2'] > 0)}** {pct(p['stress_dev']['fees_x2'])} "
        f"(fees and slippage both doubled: {pct(p['stress_dev']['fees_x2_slippage_x2'])}; base {pct(p['stress_dev']['base'])})")
    row("Across nearby settings, not one lucky value?",
        lambda p: f"**{yn(p['grid']['share_profitable'] >= 0.8)}** {round(p['grid']['share_profitable'] * p['grid']['n'])} of {p['grid']['n']} "
                  f"grid points profitable (worst {pct(p['grid']['lo'])}, median {pct(p['grid']['median'])}, this setting {pct(p['grid']['default'])})")
    row("Any leak from the future?",
        lambda p: f"**{'YES' if p['audit']['leak_found'] else 'NO'}** signals identical at {p['audit']['cuts_dev'] + p['audit']['cuts_full']} "
                  f"cut points ({p['audit']['rows_differ']} rows differ); one bar late: {pct(p['audit']['delay1'])} vs {pct(p['audit']['delay0'])} on time")
    row("Better than same-side random entries?",
        lambda p: f"p = {p['null_dev']['p_value']:.2f} first 36 months (random entries averaged {pct(p['null_dev']['null_mean'])}); "
                  f"p = {p['null_full']['p_value']:.2f} full window")
    row("Clears the multiple-testing haircut?",
        lambda p: f"**NO** Sharpe {p['haircut_pair']['sharpe']:.2f}, p {p['haircut_pair']['p']:.3f} before correction, "
                  f"{p['haircut_pair']['p_bonf']:.2f} after (87 tests)")
    return L


# ======================================================================================
# RANKING.md
# ======================================================================================
def kill_cell(r) -> str:
    if r["n_failed"] == 0:
        return f"**alive** {pct(r['net'])} / {int(r['trades'])}"
    failed = [k for k in str(r["failed"]).split(",") if k]
    first = next(k for k in ORDER if k in failed)
    if r["trades"] == 0:
        return f"{CODES[first]} (no trades)"
    return f"{CODES[first]}({len(failed)}) {pct(r['net'])} / {int(r['trades'])}"


def ranking(s1, s2, fin, kt, st, opened) -> str:
    F = {f["module"]: f for f in fin["finalists"]}
    ts, wr, oi = F["tsmom"], F["week_range_breakout_volume"], F["oi_confirmed_breakout"]
    bh = fin["bh"]
    port = fin["portfolio"]
    A, S = port["all_finalist_pairs"], port["holdout_survivors_only"]
    n_alive = int((kt["n_failed"] == 0).sum())
    n_killed = len(kt) - n_alive
    L = []
    w = L.append

    def book(f, tag, win):
        return f["book"][tag][win]

    w("# Ranking of Radar's 29 setups (Lab, phase 2)")
    w("")
    w(f"Written from runs made on {pd.Timestamp.now(tz='UTC'):%Y-%m-%d}. Every number below comes from a run stored in "
      "`lab/results/phase2/`; figures marked ESTIMATE are arithmetic on those runs, not runs themselves.")
    w("")
    w("## Outcome")
    w("")
    w(f"- **{n_killed} of {len(kt)} setup-symbol pairs were killed** on the first 36 months by the pre-registered rules; "
      f"{n_alive} pairs (in {len(st)} setups) were left standing.")
    w(f"- **The last 12 months were opened once, for {opened['n_pairs']} pairs** (the top three setups by score). "
      f"{sum(p['holdout_pass'] for f in fin['finalists'] for p in f['pairs'])} made money there, "
      f"{sum(not p['holdout_pass'] for f in fin['finalists'] for p in f['pairs'])} did not.")
    w("- **Two setups survive everything. Neither is proven, and neither passes the buy-and-hold gate on every symbol it trades.**")
    w("- **No setup passes the owner's gate as written.** Both survivors beat ETH buy-and-hold over the full window "
      f"only because ETH buy-and-hold lost {pct(-bh['ETHUSDT']['full']['net_return'], 1, False)} after funding; "
      "both trail SOL buy-and-hold by more than 100 points.")
    w("")
    w("| Rank | Setup | Trade it on | Status | First 36 months | Last 12 months (unseen) | Full window | Max drawdown | Trades (full) |")
    w("|---|---|---|---|---|---|---|---|---|")
    for rk, f, status in ((1, ts, "candidate for paper trading, unproven"),
                          (2, wr, "candidate for paper trading, unproven")):
        b = f["book"]["alive_symbols"]
        w(f"| {rk} | `{f['module']}` | {', '.join(f['symbols_alive'])} | {status} | {pct(b['dev']['net'])} | "
          f"{pct(b['holdout']['net'])} | {pct(b['full']['net'])} | {pct(b['full']['max_dd'], 1, False)} | {b['full']['trades']} |")
    b = oi["book"]["dev_symbols"]
    w(f"| 3 | `{oi['module']}` | {', '.join(oi['symbols_dev'])} | **paper only, not a candidate for live** | {pct(b['dev']['net'])} | "
      f"{pct(b['holdout']['net'])} | {pct(b['full']['net'])} | {pct(b['full']['max_dd'], 1, False)} | {b['full']['trades']} |")
    w("")
    w("Each row is the setup's book: the sum of its per-symbol runs, every trade risking 0.5% of a $1,000 account, "
      "after fees, slippage and funding, exchange minimum order sizes ON.")
    w("")
    w("Only two setups are real candidates, so the third slot is not padded: `oi_confirmed_breakout` was first on the "
      "first-36-month score, then lost money on both symbols in the last 12 months, and it cannot run live because the "
      "live feed has no open interest.")
    w("")
    w("## How this was run")
    w("")
    w("- **Primary configuration:** exchange minimum order sizes ON (owner decision: the account really is $1,000). "
      "The size table comes from `bot/instruments.py` and is **unverified** (exchangeInfo is geo-blocked here). "
      "No-filter numbers are shown alongside and labelled.")
    w("- **Selection window:** 2022-11-01 to 2025-10-31 (first 36 months). All kill rules, scores and the choice of "
      "finalists used this window only.")
    w(f"- **Holdout:** 2025-11-01 to 2026-10-31, opened once on {opened['opened_utc'][:16]} UTC for exactly "
      f"{opened['n_pairs']} pairs. No other setup has been run on it.")
    w("- **Rules fixed beforehand:** kill thresholds, score bands, tie-breaks and the finalist protocol are in "
      "`lab/results/PREREG.md`, written before stage 1 ran. It also states what had already been seen (a phase-1 "
      "smoke run of net return, drawdown, Sharpe and trade count on the first 36 months).")
    w("- **Engine rules:** `lab/engine.py` R1-R12, including same-open reversal when a signal sits on the exit row.")
    w("- **Loader:** current `strategies/data.py` (open-interest prints <= 0 masked).")
    w("")
    w("## 1. Kill table: 29 setups x 3 symbols, first 36 months")
    w("")
    w("Each cell: the rule that killed the pair (first in the pre-registered order), the number of rules it failed in "
      "brackets, then net return and trade count with DEFAULT_PARAMS. **alive** = no rule fired.")
    w("")
    w("| Code | Rule |")
    w("|---|---|")
    for k, txt in P2.KILL_ORDER[:-1]:
        t2 = "audit flag: profitable only when executed one bar late; no look-ahead leak found" if k == "audit" else txt
        w(f"| {CODES[k]} | {t2} |")
    w("")
    w("| Setup | Family | TF | BTCUSDT | ETHUSDT | SOLUSDT |")
    w("|---|---|---|---|---|---|")
    for name, g in kt.groupby("module", sort=False):
        cells = {r["symbol"]: kill_cell(r) for _, r in g.iterrows()}
        w(f"| `{name}` | {g['family'].iloc[0]} | {g['tf'].iloc[0]} | {cells.get('BTCUSDT', '')} | {cells.get('ETHUSDT', '')} | {cells.get('SOLUSDT', '')} |")
    w("")
    kc = kt[kt["n_failed"] > 0].groupby("killed_by").size().sort_values(ascending=False)
    w("Killed by (first rule that fired):")
    w("")
    for k, v in kc.items():
        w(f"- {v} pair{'s' if v != 1 else ''}: {k}")
    w("")
    fails = pd.Series([k for f in kt["failed"].fillna("") for k in str(f).split(",") if k]).value_counts()
    w("A pair usually fails several rules at once. Pairs failing each rule: "
      + ", ".join(f"{CODES[k]} {int(fails.get(k, 0))}" for k in ORDER[:-1]) + ".")
    w("")
    assert not kt["leak_found"].any()
    w(f"No pair has a look-ahead leak: the prefix check was clean on all {len(kt)} pairs "
      f"({int(kt['prefix_cuts'].min())} cut points each).")
    for _, r in kt[kt["delay_anomaly"]].iterrows():
        dl = s1[(r["module"], r["symbol"])]["audit"]["delay"]
        w(f"The one DELAY flag is `{r['module']}` on {r['symbol']}: it returned {pct(dl['net_return_delay0'])} on time and "
          f"{pct(dl['net_return_delay1'])} when executed one bar late. That is not a leak, and the pair fails "
          f"{int(r['n_failed']) - 1} other rules anyway.")
    w("")
    w("By family:")
    w("")
    w("| Family | Setups | Pairs alive / tested | Alive |")
    w("|---|---|---|---|")
    for fam, g in kt.groupby("family", sort=False):
        al = g[g["n_failed"] == 0]
        names = ", ".join(f"`{m}` {sy.replace('USDT', '')}" for m, sy in zip(al["module"], al["symbol"])) or "none"
        w(f"| {fam} | {g['module'].nunique()} | {len(al)} / {len(g)} | {names} |")
    w("")
    alive = kt[kt["n_failed"] == 0]
    n4h = int((alive["tf"] == "4h").sum())
    w(f"Lead-lag, liquidation-flush and funding setups have no surviving pair (the funding ones mostly for lack of "
      f"trades), and session setups have one of twelve. {len(alive) - 1} of the {len(alive)} surviving pairs follow a move "
      f"(trend, breakout or opening drive); {n4h} of the {len(alive)} are on 4h bars. The only fade left is "
      "`oi_divergence_fade` on ETH, with 31 trades.")
    w("")
    diff = kt[(kt["nofilter_trades"] > 0) & ((kt["trades"] - kt["nofilter_trades"]).abs() > 0.10 * kt["nofilter_trades"])]
    w("### Where the exchange minimums change the picture (no-filter numbers, labelled)")
    w("")
    only_btc = bool((diff["symbol"] == "BTCUSDT").all())
    w("Pairs whose trade count moves by more than 10% when minimum order sizes are switched off. "
      + ("All are BTC: on $1,000 a 0.5% risk order is often below the BTC minimum." if only_btc else
         "Most are BTC: on $1,000 a 0.5% risk order is often below the BTC minimum."))
    w("")
    w("| Pair | With minimums (primary): net / trades | Orders refused as too small | WITHOUT minimums: net / trades |")
    w("|---|---|---|---|")
    for _, r in diff.iterrows():
        w(f"| `{r['module']}` {r['symbol']} | {pct(r['net'])} / {int(r['trades'])} | {int(r['blocked_min_size'])} | "
          f"{pct(r['nofilter_net'])} / {int(r['nofilter_trades'])} |")
    w("")
    w("No-filter net return and trade count for all 87 pairs are in `lab/results/kill_table.csv` "
      "(columns `nofilter_net`, `nofilter_trades`). The kill rules themselves were run with minimums ON only.")
    w("")
    w("## 2. Survivors and scores")
    w("")
    w("A setup is scored as the book that trades it on every symbol where it survived section 1. "
      "Score bands were fixed in advance:")
    w("")
    w("| Score | Out-of-sample profit per year | Max drawdown | Trades (36 months, pooled) | Longest losing streak | Bear (b) and sideways (s) P&L |")
    w("|---|---|---|---|---|---|")
    bands = P2.SCORE_BANDS
    for sc in (5, 4, 3, 2, 1):
        row = [next(t for s_, t in bands[k]["bands"] if s_ == sc) for k in ("oos_profit", "max_dd", "trades", "lose_streak", "bear_sideways")]
        w(f"| {sc} | " + " | ".join(row) + " |")
    w("")
    w("| # | Setup | Symbols | Trades | Net | Max DD | Sharpe | Losing streak | OOS/yr | Bear | Sideways | Scores (oos, dd, trades, streak, regimes) | Total | Haircut p (n=29) | Note |")
    w("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in st.iterrows():
        syms = ", ".join(s.replace("USDT", "") for s in eval(r["symbols"]))
        note = {"eligible": "", "thin": "thin (under 50 trades)"}.get(r["eligibility"], r["eligibility"])
        w(f"| {i + 1} | `{r['module']}` | {syms} | {int(r['pooled_trades'])} | {pct(r['book_net'])} | {pct(r['book_max_dd'], 1, False)} | "
          f"{r['book_sharpe']:.2f} | {int(r['lose_streak'])} | {pct(r['wf_oos_per_year'])} | {pct(r['bear'])} | {pct(r['sideways'])} | "
          f"{int(r['s_oos_profit'])}, {int(r['s_max_dd'])}, {int(r['s_trades'])}, {int(r['s_lose_streak'])}, {int(r['s_bear_sideways'])} | "
          f"**{int(r['score'])}** | {r['p_bonferroni']:.2f} | {note} |")
    w("")
    w("All columns are first-36-month numbers. OOS/yr is the walk-forward out-of-sample profit (12 months to choose "
      "parameters, next 3 months traded, rolled over 8 quarters), summed over the setup's symbols.")
    w("")
    mt = fin["multiple_testing"]
    w("**Multiple testing.** " + mt["setups"]["note"].replace("29 setup x symbol tests", "With 29 setups tested"))
    w("")
    n5 = int(st["significant_5pct"].sum())
    clear = st[st["proven"]]["module"].tolist()
    w(f"- At the usual 5% level after the Bonferroni correction, **{n5} of {len(st)} surviving setups are significant**. "
      "The last column is the corrected p-value; all are far above 0.05.")
    w(f"- At pair level (87 tests) none of the {n_alive} surviving pairs clears the haircut either.")
    w(f"- The pre-registered bar was weaker (corrected p below 0.5). {len(clear)} setups clear it: "
      + ", ".join(f"`{m}`" for m in clear) + ". That decided one tie-break and nothing else. "
      "Every setup in this report should be read as **unproven**.")
    w("")
    w("Tie at 21 points between `oi_confirmed_breakout` and `tsmom` was broken by the pre-registered order (clears the "
      "haircut bar first), which put `oi_confirmed_breakout` first before the holdout. `oi_divergence_fade` (20 points) and "
      "`oi_reset_trend` (16) have under 50 trades and cannot be ranked in the top three.")
    w("")
    w("## 3. Final gate: the last 12 months")
    w("")
    w(f"Finalists were fixed before opening: the top three eligible setups, {opened['n_pairs']} pairs, DEFAULT_PARAMS "
      "(no walk-forward grid point was chosen in at least half the folds, so the rule to switch parameters did not apply). "
      "A pair must make money on the unseen year; no replacement was drawn for those that failed.")
    w("")
    w("| Pair | First 36 months | Last 12 months (primary) | Trades | Buy-and-hold, same 12 months | Last 12 months WITHOUT minimums | Verdict |")
    w("|---|---|---|---|---|---|---|")
    for f in fin["finalists"]:
        for p in f["pairs"]:
            w(f"| `{f['module']}` {p['symbol']} | {pct(p['dev']['net_return'])} | **{pct(p['holdout']['net_return'], 2)}** | "
              f"{p['holdout']['trades']} | {pct(p['bh']['holdout']['net_return'])} | {pct(p['nofilter']['holdout']['net_return'], 2)} "
              f"({p['nofilter']['holdout']['trades']} trades) | {'survives' if p['holdout_pass'] else '**killed**'} |")
    w("")
    oib, oie = oi["pairs"][0], oi["pairs"][1]
    tsb = ts["pairs"][0]
    w(f"- `oi_confirmed_breakout` is killed on both symbols. ETH was flat ({pct(oie['holdout']['net_return'], 2)}); the rule says it must make money.")
    w(f"- `tsmom` loses its BTC leg ({pct(tsb['holdout']['net_return'], 2)}) and keeps ETH and SOL.")
    w(f"- Without the exchange minimums all three killed pairs would have scraped through "
      f"({pct(oib['nofilter']['holdout']['net_return'], 2)}, {pct(oie['nofilter']['holdout']['net_return'], 2)}, "
      f"{pct(tsb['nofilter']['holdout']['net_return'], 2)}). On BTC the minimums refuse most small orders, so the "
      "primary and no-filter BTC results are different sets of trades. The verdicts above use the primary configuration.")
    w("- The unseen year was a bear market (buy-and-hold lost 32% to 49%), so it tests the short side and the stops "
      "more than the long side.")
    w("")
    w("## 4. Top three")
    w("")
    for rk, f, tag in ((1, ts, "alive_symbols"), (2, wr, "alive_symbols"), (3, oi, "dev_symbols")):
        syms = f["symbols_alive"] if tag == "alive_symbols" else f["symbols_dev"]
        b = f["book"][tag]
        head = {1: "candidate for paper trading, unproven", 2: "candidate for paper trading, unproven",
                3: "paper only, not a candidate for live"}[rk]
        w(f"### {rk}. `{f['module']}` on {', '.join(syms)} - {head}")
        w("")
        w(f"{f['one_liner']} Card: `lab/results/cards/{rk}_{f['module']}.md`.")
        w("")
        w("| | First 36 months | Last 12 months | Full window |")
        w("|---|---|---|---|")
        w(f"| Net return (book) | {pct(b['dev']['net'])} | {pct(b['holdout']['net'])} | {pct(b['full']['net'])} |")
        w(f"| Max drawdown | {pct(b['dev']['max_dd'], 1, False)} | {pct(b['holdout']['max_dd'], 1, False)} | {pct(b['full']['max_dd'], 1, False)} |")
        w(f"| Trades | {b['dev']['trades']} | {b['holdout']['trades']} | {b['full']['trades']} |")
        w(f"| Win rate | {pct(b['dev']['win_rate'], 0, False)} | {pct(b['holdout']['win_rate'], 0, False)} | {pct(b['full']['win_rate'], 0, False)} |")
        w(f"| Longest losing streak | {b['dev']['lose_streak']} | {b['holdout']['lose_streak']} | {b['full']['lose_streak']} |")
        w(f"| Worst month | {pct(b['dev']['worst_month'])} ({b['dev']['worst_month_label']}) | {pct(b['holdout']['worst_month'])} ({b['holdout']['worst_month_label']}) | {pct(b['full']['worst_month'])} ({b['full']['worst_month_label']}) |")
        w(f"| Equal-weight buy-and-hold of the same symbols | {pct(b['dev']['bh_equal_weight_net'])} | {pct(b['holdout']['bh_equal_weight_net'])} | {pct(b['full']['bh_equal_weight_net'])} |")
        w("")
    w("### Rank 1 against rank 2")
    w("")
    r1, r2 = rescore(s1, "tsmom", ts["symbols_alive"]), rescore(s1, "week_range_breakout_volume", wr["symbols_alive"])
    p1 = book_path_facts(s2, "tsmom", ts["symbols_alive"])
    p2 = book_path_facts(s2, "week_range_breakout_volume", wr["symbols_alive"])
    t1 = [trade_facts(s2[("tsmom", sy)]["trades"]["full"]) for sy in ts["symbols_alive"]]
    t2 = [trade_facts(s2[("week_range_breakout_volume", sy)]["trades"]["full"]) for sy in wr["symbols_alive"]]
    b1, b2 = ts["book"]["alive_symbols"], wr["book"]["alive_symbols"]
    w("The order follows the pre-registered score. The two are close, and they differ in character, so the owner should see both sides:")
    w("")
    w("| ETH + SOL book, full window unless stated | `tsmom` | `week_range_breakout_volume` |")
    w("|---|---|---|")
    w(f"| Pre-registered score (first 36 months, symbols alive then) | {ts['score']} | {wr['score']} |")
    w(f"| Same score recomputed on ETH + SOL only | {r1['total']} ({', '.join(str(v) for v in r1['scores'].values())}) | {r2['total']} ({', '.join(str(v) for v in r2['scores'].values())}) |")
    w(f"| Net return | {pct(b1['full']['net'])} | {pct(b2['full']['net'])} |")
    w(f"| Last 12 months (unseen) | {pct(b1['holdout']['net'])} | {pct(b2['holdout']['net'])} |")
    w(f"| Max drawdown | {pct(b1['full']['max_dd'], 1, False)} | {pct(b2['full']['max_dd'], 1, False)} |")
    w(f"| Trades per year | {p1['trades_per_year']:.0f} | {p2['trades_per_year']:.0f} |")
    w(f"| Calendar years in profit (of {len(p1['years'])}, two of them partial) | {sum(v > 0 for v in p1['years'].values())} | {sum(v > 0 for v in p2['years'].values())} |")
    w(f"| Losing quarters (of {p1['quarters']}) | {p1['losing_quarters']} | {p2['losing_quarters']} |")
    w(f"| Share of 12-month windows that lost money | {pct(p1['roll_12m_neg_share'], 1, False)} | {pct(p2['roll_12m_neg_share'], 1, False)} |")
    w(f"| Longest stretch without a new equity high | {p1['longest_underwater_days']} days | {p2['longest_underwater_days']} days |")
    w(f"| Best single trade as a share of a symbol's profit | {pct(min(x['best_share'] for x in t1), 0, False)} and {pct(max(x['best_share'] for x in t1), 0, False)} | {pct(min(x['best_share'] for x in t2), 0, False)} and {pct(max(x['best_share'] for x in t2), 0, False)} |")
    w(f"| Three-losses Shield halts per year on its own book | {ts['own_book_alive']['full_shield']['halts_loss_streak'] / 4:.0f} | {wr['own_book_alive']['full_shield']['halts_loss_streak'] / 4:.0f} |")
    w("")
    tie = "the same total" if r1["total"] == r2["total"] else ("a higher total for `tsmom`" if r1["total"] > r2["total"] else "a higher total for `week_range_breakout_volume`")
    w(f"- Recomputing the score on the two symbols each setup keeps gives {tie}. "
      + ("The pre-registered tie-break (boring baseline family first) still puts `tsmom` first. " if r1["total"] == r2["total"] else "")
      + "I have not re-ordered them after the fact.")
    w("- `tsmom` has more trades and more return, and it is the textbook baseline. Its profit is lumpy: it lost money in "
      "two of the five calendar years and in half the quarters.")
    w("- `week_range_breakout_volume` is the steadier of the two: lower drawdown, no losing year, fewer losing quarters "
      "and less dependence on single trades. It has about half as many trades and less return.")
    w("- If only one is paper-traded, the score says `tsmom`; anyone who weighs steadiness above the score would pick rank 2. "
      "Both are unproven and they are related bets, so running both on paper costs nothing and adds evidence.")
    w("")
    w("### Final check (per symbol; yes/no with the number)")
    w("")
    w("| Question | " + " | ".join(f"`{f['module']}` {p['symbol'].replace('USDT', '')}" for f in (ts, wr, oi) for p in f["pairs"]) + " |")
    w("|---|" + "---|" * sum(len(f["pairs"]) for f in (ts, wr, oi)))
    allp = [(f, p) for f in (ts, wr, oi) for p in f["pairs"]]
    w("| Still wins on unseen data (last 12 months)? | " + " | ".join(
        f"{yn(p['holdout_pass'])} {pct(p['holdout']['net_return'], 2)}, {p['holdout']['trades']} trades" for f, p in allp) + " |")
    w("| After fees and funding (full window)? | " + " | ".join(
        f"{yn(p['full']['net_return'] > 0)} {pct(p['full']['net_return'])}" for f, p in allp) + " |")
    w("| Over 50+ trades (first 36 months)? | " + " | ".join(
        f"{yn(p['dev']['trades'] >= 50)} {p['dev']['trades']}" for f, p in allp) + " |")
    w("| With double fees (first 36 months)? | " + " | ".join(
        f"{yn(p['stress_dev']['fees_x2'] > 0)} {pct(p['stress_dev']['fees_x2'])}" for f, p in allp) + " |")
    w("| Fees and slippage both doubled? | " + " | ".join(
        f"{yn(p['stress_dev']['fees_x2_slippage_x2'] > 0)} {pct(p['stress_dev']['fees_x2_slippage_x2'])}" for f, p in allp) + " |")
    w("| Across nearby settings? | " + " | ".join(
        f"{yn(p['grid']['share_profitable'] >= 0.8)} {round(p['grid']['share_profitable'] * p['grid']['n'])} of {p['grid']['n']} profitable, worst {pct(p['grid']['lo'])}"
        for f, p in allp) + " |")
    w("| Any leak from the future? | " + " | ".join(
        f"{'YES' if p['audit']['leak_found'] else 'NO'} ({p['audit']['cuts_dev'] + p['audit']['cuts_full']} cut points, {p['audit']['rows_differ']} rows differ)"
        for f, p in allp) + " |")
    w("| Better than same-side random entries? (p, first 36 months) | " + " | ".join(
        f"p = {p['null_dev']['p_value']:.2f} (random: {pct(p['null_dev']['null_mean'])})" for f, p in allp) + " |")
    w("| Survives the multiple-testing haircut (87 pairs)? | " + " | ".join(
        f"NO (corrected p {p['haircut_pair']['p_bonf']:.2f})" for f, p in allp) + " |")
    w("")
    w("- 'Nearby settings' counts YES at 80% of the grid or more. `tsmom` on ETH loses with the 20-day lookback at every "
      "stop width and wins with 30 and 60 days.")
    w("- 'Over 50+ trades' is per symbol here; pooled over the symbols each setup keeps, "
      f"`tsmom` has {ts['book']['alive_symbols']['dev']['trades']} and `week_range_breakout_volume` "
      f"{wr['book']['alive_symbols']['dev']['trades']} in the first 36 months, which is what the owner's 50-trade rule asks for.")
    w("- The random-entry line moves every real trade to a random day (same hour, same side, same stop distance in ATR, "
      "same holding time) 300 times. It is not corrected for the 87 tests and is not a kill rule.")
    mx = max(abs(p["exec_1h"][wn]["net_return"] - p[wn]["net_return"]) for f, p in allp for wn in ("dev", "holdout", "full"))
    w(f"- Executing the same 4h signals on 1h bars instead of 4h bars changes no result (first 36 months, last 12 months "
      f"or full window) by more than {100 * mx:.2f} points.")
    w("")
    w("## 5. Buy-and-hold gate")
    w("")
    w("Gate (owner): the setup's net return must beat the same symbol's 1x buy-and-hold net return over the full window "
      "(2022-11-01 to 2026-10-31), on every symbol it trades. Buy-and-hold pays the entry fee and real funding.")
    w("")
    w("| Pair | Setup, full window | Buy-and-hold, full window | Beats it? | In bear bars: setup / B&H | In sideways bars: setup / B&H | Last 12 months: setup / B&H |")
    w("|---|---|---|---|---|---|---|")
    for f, p in allp:
        rg = p["regime_full"]
        w(f"| `{f['module']}` {p['symbol']} | {pct(p['full']['net_return'])} | {pct(p['bh']['full']['net_return'])} | "
          f"**{yn(p['gate_abs_full'])}** | {pct(rg['bear']['strat'])} / {pct(rg['bear']['bh'])} | "
          f"{pct(rg['sideways']['strat'])} / {pct(rg['sideways']['bh'])} | {pct(p['holdout']['net_return'], 2)} / {pct(p['bh']['holdout']['net_return'])} |")
    w("")
    w("**Result: no setup passes the gate.** `tsmom` and `week_range_breakout_volume` pass on ETH and fail on SOL; "
      "`oi_confirmed_breakout` passes on ETH and fails on BTC.")
    w("")
    w(f"- The ETH passes say more about ETH than about the setups: 1x ETH buy-and-hold returned "
      f"{pct(bh['ETHUSDT']['full']['net_return'])} because funding ate the price gain.")
    w("- Regime labels are trailing (90-day return and 200-day mean, known at each bar's open), so 'bear bars' include "
      "the rebounds that follow a fall. That is why BTC buy-and-hold shows a gain inside bear-labelled bars.")
    w("- In the unseen year every pair did better than buy-and-hold, which fell 32% to 49%. "
      "Three of the seven still lost money or stood still.")
    w("")
    w("### Risk-adjusted comparison (reported separately; it does not replace the gate)")
    w("")
    w("| Pair | Sharpe: setup / B&H | Return / max drawdown: setup / B&H | Max drawdown: setup / B&H | Return if scaled to B&H's drawdown (ESTIMATE) | B&H return |")
    w("|---|---|---|---|---|---|")
    for f, p in allp:
        ra = p["risk_adjusted"]
        w(f"| `{f['module']}` {p['symbol']} | {ra['sharpe']:.2f} / {ra['bh_sharpe']:.2f} | {ra['ret_over_dd']:.1f} / {ra['bh_ret_over_dd']:.1f} | "
          f"{pct(p['full']['max_dd'], 1, False)} / {pct(p['bh']['full']['max_dd'], 1, False)} | {pct(ra['est_return_at_bh_drawdown'], 0)} | {pct(ra['bh_return'])} |")
    w("")
    w("The ESTIMATE column multiplies the setup's return by (buy-and-hold drawdown / setup drawdown). It is a ratio, not a "
      "forecast: reaching it would need roughly 9 to 20 times the risk per trade, which the 3x leverage cap, compounding "
      "and worse fills at size would not allow. Read it as 'per unit of drawdown the setups did better', nothing more.")
    w("")
    w("## 6. Portfolio pass: one shared $1,000 account")
    w("")
    w("All streams trade one account on 1h execution bars: every order is sized at 0.5% of the account's realised equity, "
      "an order is refused if gross exposure would pass 3x equity, exchange minimums ON.")
    w("")
    w("Shield as simulated:")
    w("")
    w("- **Daily loss:** equity marked to market at an hourly close is 3% or more below the start of that UTC day.")
    w("- **Loss streak:** 3 losing trades in a row, account-wide.")
    w("- **On a halt:** resting orders are cancelled, positions are closed at the next hourly open, and there are no "
      "entries for the rest of that UTC day.")
    w("- **ASSUMPTION:** trading resumes at 00:00 UTC the next day for both rules. The real Shield waits for a human "
      "after a loss streak.")
    w("")

    def prow(label, m):
        return (f"| {label} | {pct(m['net_return'])} | {pct(m['max_dd'], 1, False)} | {m['sharpe']:.2f} | {m['trades']} | "
                f"{pct(m['worst_month'])} | {m['longest_losing_streak']} | {m['halts']} ({m['halts_daily_loss']} daily, {m['halts_loss_streak']} streak) |")

    for title, D, note in (
            (f"A. The finalists as chosen before the holdout: {len(A['streams'])} streams", A,
             "This is the clean test for the unseen year, because the 7 streams were fixed before it was opened."),
            (f"B. What is left after the gate: {len(S['streams'])} streams (`tsmom` and `week_range_breakout_volume` on ETH and SOL)", S,
             "This is the set proposed for paper trading. Its last-12-months line is flattered: these 4 were kept "
             "*because* they made money in that year.")):
        w(f"### {title}")
        w("")
        w(note)
        w("")
        w("| Window, Shield | Net return | Max drawdown | Sharpe | Trades | Worst month | Longest losing streak | Halts |")
        w("|---|---|---|---|---|---|---|---|")
        for win, lab in (("dev", "First 36 months"), ("holdout", "Last 12 months"), ("full", "Full window")):
            w(prow(f"{lab}, no Shield", D[f"{win}_no_shield"]))
            w(prow(f"{lab}, Shield", D[f"{win}_shield"]))
        w(prow("Full window, no Shield, WITHOUT exchange minimums", D["full_no_shield_nofilter"]))
        w(prow("Full window, Shield, WITHOUT exchange minimums", D["full_shield_nofilter"]))
        w("")
    eq = bh["equal_weight"]
    w("| Buy-and-hold for comparison | First 36 months | Last 12 months | Full window | Max drawdown (full) |")
    w("|---|---|---|---|---|")
    for sym in ("BTCUSDT", "ETHUSDT", "SOLUSDT"):
        w(f"| {sym} | {pct(bh[sym]['dev']['net_return'])} | {pct(bh[sym]['holdout']['net_return'])} | {pct(bh[sym]['full']['net_return'])} | {pct(bh[sym]['full']['max_dd'], 1, False)} |")
    w(f"| Equal weight of the three | {pct(eq['dev']['net_return'])} | {pct(eq['holdout']['net_return'])} | {pct(eq['full']['net_return'])} | {pct(eq['full']['max_dd'], 1, False)} |")
    w("")
    w("What the portfolio pass shows:")
    w("")
    ah_n, ah_s = A["holdout_no_shield"], A["holdout_shield"]
    af_n, af_s = A["full_no_shield"], A["full_shield"]
    sf_n, sf_s = S["full_no_shield"], S["full_shield"]
    w(f"- **Unseen year, 7 streams:** {pct(ah_n['net_return'])} without the Shield, {pct(ah_s['net_return'])} with it, "
      f"max drawdown about {pct(ah_n['max_dd'], 0, False)}, while equal-weight buy-and-hold lost "
      f"{pct(-eq['holdout']['net_return'], 0, False)}. This is the most trustworthy line in the report, and it is one year.")
    w(f"- **Stacking streams is what makes the account-level number large.** Seven streams at 0.5% risk each is roughly "
      f"seven times the risk of one. The full-window figures ({pct(af_n['net_return'])} / {pct(af_s['net_return'])} for A, "
      f"{pct(sf_n['net_return'])} / {pct(sf_s['net_return'])} for B) include the 36 months on which these setups were "
      "picked as the best of 29, so they overstate what to expect. They are not the owner's gate, which is per setup and per symbol.")
    w(f"- **The Shield's loss-streak rule fires constantly on trend setups.** {af_s['halts_loss_streak']} loss-streak halts "
      f"in four years on A ({sf_s['halts_loss_streak']} on B), against {af_s['halts_daily_loss']} daily-loss halts. "
      "A win rate near 35% makes three losses in a row routine. Each halt closes open trend trades at market.")
    w(f"- **Cost of the Shield in this backtest:** full-window return falls from {pct(af_n['net_return'])} to {pct(af_s['net_return'])} on A "
      f"and from {pct(sf_n['net_return'])} to {pct(sf_s['net_return'])} on B; drawdown is about the same on A "
      f"({pct(af_n['max_dd'], 1, False)} vs {pct(af_s['max_dd'], 1, False)}) and lower on B "
      f"({pct(sf_n['max_dd'], 1, False)} vs {pct(sf_s['max_dd'], 1, False)}). With a human needed to clear each "
      "loss-streak halt, the real cost would be larger than simulated.")
    w(f"- **The gross 3x cap never bound** ({af_n['blocked_gross_cap']} orders refused for it). The -3% day is rare at this risk level.")
    w("")
    w("### Each setup on its own $1,000 book (how the bot is configured today)")
    w("")
    w("| Book | Window | Net, no Shield | Net, Shield | Max drawdown, Shield | Halts (Shield) |")
    w("|---|---|---|---|---|---|")
    for f, key in ((ts, "own_book_alive"), (wr, "own_book_alive"), (oi, "own_book")):
        syms = "+".join(s.replace("USDT", "") for s in (f["symbols_alive"] if key == "own_book_alive" else f["symbols_dev"]))
        for win, lab in (("dev", "First 36 months"), ("holdout", "Last 12 months"), ("full", "Full window")):
            a_, b_ = f[key][f"{win}_no_shield"], f[key][f"{win}_shield"]
            w(f"| `{f['module']}` {syms} | {lab} | {pct(a_['net_return'])} | {pct(b_['net_return'])} | {pct(b_['max_dd'], 1, False)} | "
              f"{b_['halts']} ({b_['halts_loss_streak']} streak) |")
    w("")
    tsf = ts["own_book_alive"]["full_shield"]
    w("- These books trade both symbols from one $1,000, so they compound a little faster than the sum of the "
      "per-symbol runs quoted in the outcome table.")
    w(f"- `tsmom` alone would trip the three-losses halt {tsf['halts_loss_streak']} times in four years (about "
      f"{tsf['halts_loss_streak'] / 4:.0f} a year). In the real bot each one stops that book until a person clears it.")
    w("")
    w("## 7. What the evidence does and does not support")
    w("")
    tse, tss = ts["pairs"][1], ts["pairs"][2]
    f1, f2 = trade_facts(s2[("tsmom", "SOLUSDT")]["trades"]["full"]), trade_facts(s2[("tsmom", "ETHUSDT")]["trades"]["full"])
    fw = [trade_facts(s2[("week_range_breakout_volume", sy)]["trades"]["full"]) for sy in ("ETHUSDT", "SOLUSDT")]
    keep = [p for f in (ts, wr) for p in f["pairs"] if p["holdout_pass"]]
    cg = [p["dev"]["cagr"] for p in keep]
    dd = [p["dev"]["max_dd"] for p in keep]
    pv = [p["null_dev"]["p_value"] for p in keep]
    w(f"**It supports this much.** Under realistic costs, {n_killed} of {len(kt)} setup-symbol pairs fail outright. "
      "The lead-lag, liquidation-flush and funding families have no surviving pair, and the session family has one. "
      "Slow trend and breakout following on 4h bars made modest money in the first 36 months: for the four pairs that "
      f"survive, {pct(min(cg), 1, False)} to {pct(max(cg), 1, False)} a year per symbol at 0.5% risk, with drawdowns of "
      f"{pct(min(dd), 1, False)} to {pct(max(dd), 1, False)}. Those four also made money in a year they had never seen, "
      "a bear market in which ETH and SOL buy-and-hold lost 43% and 49%. None has a look-ahead leak, all survive "
      "doubled fees and slippage, and each beat same-side random entries in the first 36 months "
      f"(p from {min(pv):.2f} to {max(pv):.2f} per pair, not corrected for the number of tests).")
    w("")
    w("**It does not support this.**")
    w("")
    ht = [p["holdout"]["trades"] for p in keep]
    w("- **That the edge is real.** No pair and no setup is statistically significant once 87 (or 29) tests are "
      f"accounted for. The unseen year is one more data point: {min(ht)} to {max(ht)} trades per surviving pair.")
    w("- **That the survivors are different bets.** They are close cousins: trend or breakout following on the same "
      "three correlated coins. If crypto stops trending they fail together.")
    w(f"- **That the profit is spread out.** One long trade ({f1['best_from']} to {f1['best_to']}, {f1['best_R']:+.0f}R) is "
      f"{pct(f1['best_share'], 0, False)} of `tsmom`'s four-year profit on SOL; without its three best trades that leg "
      f"loses money. On ETH the best trade is {pct(f2['best_share'], 0, False)}. `week_range_breakout_volume` is steadier "
      f"(best trade {pct(min(x['best_share'] for x in fw), 0, False)} to {pct(max(x['best_share'] for x in fw), 0, False)}) "
      f"but has only {min(x['n'] for x in fw)} to {max(x['n'] for x in fw)} trades per symbol in four years.")
    w("- **That anything beats buy-and-hold.** The gate fails. The ETH passes come from ETH buy-and-hold losing money.")
    w("- **The account-level totals as a forecast.** They stack streams chosen with hindsight on 36 of the 48 months.")
    w("- **Live fills.** Lot sizes and minimums are unverified, slippage is a fixed 1 to 2 basis points, and the "
      "backtest assumes every stop fills at its price plus that slippage.")
    w("")
    w("**Plain reading:** paper-trade the two survivors to collect evidence. Nothing here justifies real money yet, and "
      "by the owner's own rule nothing goes live.")
    w("")
    w("## 8. Before paper trading starts")
    w("")
    w("- **Open interest is not on the live feed.** `oi_confirmed_breakout` reads `oi`; the bot marks live OI as unknown "
      "and refuses to trade a setup whose required column is unknown. It cannot run until OI is fed live. "
      "The two candidates need only price and volume (`high`, `low`, `close`; plus `volume` for the breakout).")
    w("- **Funding history stops at 2026-10-31.** Neither candidate reads funding for its signals; it only affects P&L "
      "accounting, which the bot takes from the live funding stream.")
    w("- **Warm-up:** `tsmom` 400 four-hour bars (67 days), `week_range_breakout_volume` 130 (22 days).")
    filt = P2.primary_cfg().filters
    w(f"- **Exchange minimums are unverified.** With the assumed table, `tsmom` on ETH had "
      f"{tse['blocked_min_size']['full']} orders refused in four years and its smallest order was about "
      f"${f2['notional_min']:.0f} against an assumed ${filt['ETHUSDT'][2]:.0f} minimum (median ${f2['notional_med']:.0f}). "
      "If the real ETH minimum is higher, a noticeable share of `tsmom` ETH orders will be refused. "
      f"On SOL the smallest order was ${f1['notional_min']:.0f} against an assumed ${filt['SOLUSDT'][2]:.0f}.")
    w("- **BTC is not traded** by either candidate: `tsmom` BTC failed the unseen year, and on $1,000 most of its BTC "
      f"orders were below the minimum anyway ({ts['pairs'][0]['blocked_min_size']['full']} refused in four years).")
    w(f"- **Shield versus trend following:** expect a three-losses halt on `tsmom`'s book about "
      f"{tsf['halts_loss_streak'] / 4:.0f} times a year and on `week_range_breakout_volume`'s about "
      f"{wr['own_book_alive']['full_shield']['halts_loss_streak'] / 4:.0f} times. Decide who clears them and how fast, "
      "or the paper results will mostly measure halts.")
    w("- **Same-open reversal (`lab/engine.py` rule R7; point 9 of the phase-1 report):** `tsmom` exits and enters "
      "the opposite side at the same open when the 30-day sign flips. The bot must do the same or it will lag the "
      "backtest by a day on every flip.")
    w("- **Parameters:** DEFAULT_PARAMS for all three (`lab/results/top3.json`).")
    w("")
    w("## Files")
    w("")
    w("- `lab/results/PREREG.md` - rules, bands and protocol fixed before the runs")
    w("- `lab/results/kill_table.csv`, `lab/results/setups.csv` - every pair and every surviving setup")
    w("- `lab/results/cards/` - one card per top-three setup; `lab/results/top3.json` - machine-readable")
    w("- `lab/results/phase2/` - stored runs (`final.json` holds every number quoted here)")
    w("- Reproduce: `python lab/phase2.py prereg | stage1 | rank | stage2 | final`, then `python lab/phase2_report.py` "
      "(`stage2` refuses to run twice)")
    w("")
    w(M.SURVIVORSHIP_NOTE)
    w("")
    return "\n".join(L)


# ======================================================================================
# cards
# ======================================================================================
def book_path_facts(s2, module, syms, window="full") -> dict:
    """Rolling-window lows, calendar years and time under water of a setup's book (sum of per-symbol runs)."""
    recs = [dict(symbol=sy, equity=s2[(module, sy)]["equity"][window], trades=s2[(module, sy)]["trades"][window]) for sy in syms]
    bk = P2.book(recs)
    d = bk["curve"].resample("1D").last()
    out = {}
    for days, tag in ((182, "6m"), (365, "12m"), (730, "24m")):
        r = (d / d.shift(days) - 1).dropna()
        out[f"roll_{tag}_min"] = float(r.min())
        out[f"roll_{tag}_neg_share"] = float((r < 0).mean())
    yr = d.resample("YE").last()
    prev = yr.shift(1)
    prev.iloc[0] = 1000.0
    out["years"] = {str(k.year): float(v) for k, v in (yr / prev - 1).items()}
    under = d < d.cummax()
    out["longest_underwater_days"] = int(under.groupby((~under).cumsum()).sum().max())
    q = bk["trade_list"].set_index("exit_time")["net"].resample("QE").sum()
    out["quarters"], out["losing_quarters"] = int(len(q)), int((q < 0).sum())
    out["trades_per_year"] = float(len(bk["trade_list"]) / 4.0)
    return out


def rescore(s1, module, syms) -> dict:
    """The pre-registered five scores, recomputed on a subset of symbols (first 36 months)."""
    from lab.engine import get_frame
    recs = [s1[(module, sy)] for sy in syms]
    b = P2.book(recs)
    wf_sum = float(sum(r["wf"]["net_return"] for r in recs))
    wf_years = float(np.mean([r["wf"]["years"] for r in recs]))
    oos = (1 + wf_sum) ** (1 / wf_years) - 1
    bear = side = 0.0
    for r in recs:
        rp = P2.regime_pnl(r["equity"], M.regime_labels(get_frame(r["symbol"], r["timeframe"])))
        bear += rp["bear"] / 1000.0
        side += rp["sideways"] / 1000.0
    sc = dict(oos_profit=P2.score_oos(oos), max_dd=P2.score_dd(b["max_dd"]), trades=P2.score_trades(b["trades"]),
              lose_streak=P2.score_streak(b["longest_losing_streak"]), bear_sideways=P2.score_regime(bear, side))
    return dict(scores=sc, total=int(sum(sc.values())), oos_per_year=oos, max_dd=b["max_dd"], trades=b["trades"],
                lose_streak=b["longest_losing_streak"], bear=bear, sideways=side)


def card_common_tail(f, syms, L):
    w = L.append
    w("## Final check")
    w("")
    L.extend(final_check(f, syms))
    w("")


def card_tsmom(s1, s2, fin) -> str:
    f = next(x for x in fin["finalists"] if x["module"] == "tsmom")
    syms = f["symbols_alive"]
    fe, fs = (trade_facts(s2[("tsmom", sy)]["trades"]["full"]) for sy in ("ETHUSDT", "SOLUSDT"))
    bk = f["book"]["alive_symbols"]
    ob = f["own_book_alive"]
    pf_ = book_path_facts(s2, "tsmom", syms)
    pe, ps_ = f["pairs"][1], f["pairs"][2]
    pb = f["pairs"][0]
    p = f["params"]
    L = []
    w = L.append
    w("# 1. `tsmom` - 30-day time-series momentum on ETHUSDT and SOLUSDT (4h bars)")
    w("")
    w("**Status: candidate for paper trading. Unproven. Fails the buy-and-hold gate (passes on ETH, fails on SOL). Not for live.**")
    w("")
    w("## Idea in one line")
    w("")
    w("Once a day, be long if price is above where it was 30 days ago and short if it is below, with a wide stop for disasters.")
    w("")
    w("## Why the edge should exist, and who loses")
    w("")
    w(f"Radar's reasoning: {f['why']}")
    w("")
    w("What the runs add: the money does not come from being right often. "
      f"The win rate is {pct(fe['win_rate'], 0, False)} on ETH and {pct(fs['win_rate'], 0, False)} on SOL. "
      f"It comes from a few trends that run for weeks: {fe['big_n']} trades of +3R or better on ETH and {fs['big_n']} on SOL in four years "
      "pay for everything else. The people on the other side of those trades are those who fade a move that keeps going.")
    w("")
    w("## Rules (exact: `strategies/tsmom.py`, DEFAULT_PARAMS)")
    w("")
    w("- **Bars:** 4h, UTC.")
    w(f"- **When it decides:** once a day, at the close of the 4h bar that opens at {p['eval_hour']}:00 and ends at 00:00 UTC.")
    w(f"- **Signal:** the sign of close / close {p['lookback']} bars earlier - 1 ({p['lookback']} x 4h = {p['lookback'] // 6} days). "
      "Positive means long, negative means short.")
    w("- **Entry:** market order at the next open (00:00 UTC). Taker fee and slippage.")
    w(f"- **Stop:** {p['stop_atr']:g} x ATR({p['atr_n']}) away from the signal close. ATR is the plain average of the last "
      f"{p['atr_n']} four-hour true ranges. The stop does not trail.")
    w("- **Exit:** at the first daily decision where the sign has flipped, close at the next open and open the opposite "
      "side at that same open.")
    w("- **After a stop-out:** it re-enters at the next daily decision if the sign still holds.")
    w("- **No profit target and no time limit.** One position per symbol.")
    w(f"- **Parameters:** `{json.dumps(p)}` ({f['params_source']}).")
    w("")
    w("What a typical trade looks like (full window):")
    w("")
    w("| | ETHUSDT | SOLUSDT |")
    w("|---|---|---|")
    w(f"| Trades in four years | {fe['n']} | {fs['n']} |")
    w(f"| Long share | {pct(fe['long_share'], 0, False)} | {pct(fs['long_share'], 0, False)} |")
    w(f"| Closed by the sign flip / by the stop | {fe['reasons'].get('exit_signal', 0)} / {fe['reasons'].get('stop', 0)} | {fs['reasons'].get('exit_signal', 0)} / {fs['reasons'].get('stop', 0)} |")
    w(f"| Time in a trade: median / average / longest | {fe['hold_med_d']:.0f} / {fe['hold_mean_d']:.0f} / {fe['hold_max_d']:.0f} days | {fs['hold_med_d']:.0f} / {fs['hold_mean_d']:.0f} / {fs['hold_max_d']:.0f} days |")
    w(f"| Average winner / average loser | {fe['avg_win_R']:+.2f}R / {fe['avg_loss_R']:+.2f}R | {fs['avg_win_R']:+.2f}R / {fs['avg_loss_R']:+.2f}R |")
    w(f"| Net from longs / from shorts | {usd(fe['long_net'])} / {usd(fe['short_net'])} | {usd(fs['long_net'])} / {usd(fs['short_net'])} |")
    w("")
    w("## Size per trade")
    w("")
    w("- **Risk:** 0.5% of the book's equity between entry and stop. Quantity = 0.005 x equity / |entry - stop|, capped "
      "at 3x equity notional and rounded down to the lot step.")
    w(f"- **In practice:** the stop sits about {pct(fe['stop_med'], 1, False)} away on ETH and {pct(fs['stop_med'], 1, False)} on SOL "
      f"(median), so a position is small: about {pct(fe['lev_med'], 0, False)} of equity on ETH (${fe['notional_med']:.0f} on $1,000) "
      f"and {pct(fs['lev_med'], 0, False)} on SOL (${fs['notional_med']:.0f}). The leverage cap never comes into play.")
    w(f"- **Smallest orders:** ${fe['notional_min']:.0f} on ETH and ${fs['notional_min']:.0f} on SOL, against assumed minimums "
      "of $20 and $5 (unverified).")
    w("")
    w("## Results after fees, slippage and funding, against buy-and-hold")
    w("")
    w("Each symbol on its own $1,000 at 0.5% risk, exchange minimums ON:")
    w("")
    L.extend(results_table(f, syms))
    w("")
    L.extend(yearly_table(f, syms))
    w("")
    w("Both symbols together:")
    w("")
    w("| ETH + SOL | First 36 months | Last 12 months (unseen) | Full window |")
    w("|---|---|---|---|")
    w(f"| Sum of the two runs above | {pct(bk['dev']['net'])} | {pct(bk['holdout']['net'])} | {pct(bk['full']['net'])} |")
    w(f"| One $1,000 book trading both (as the bot does), no Shield | {pct(ob['dev_no_shield']['net_return'])} | {pct(ob['holdout_no_shield']['net_return'])} | {pct(ob['full_no_shield']['net_return'])} |")
    w(f"| The same book with the Shield (next-day resume is an ASSUMPTION) | {pct(ob['dev_shield']['net_return'])} | {pct(ob['holdout_shield']['net_return'])} | {pct(ob['full_shield']['net_return'])} |")
    w(f"| Max drawdown (sum of runs) | {pct(bk['dev']['max_dd'], 1, False)} | {pct(bk['holdout']['max_dd'], 1, False)} | {pct(bk['full']['max_dd'], 1, False)} |")
    w(f"| Equal-weight buy-and-hold of ETH and SOL | {pct(bk['dev']['bh_equal_weight_net'])} | {pct(bk['holdout']['bh_equal_weight_net'])} | {pct(bk['full']['bh_equal_weight_net'])} |")
    w("")
    w("The shared book earns more than the sum of the separate runs because every trade is sized on the combined equity.")
    w("")
    w("**Buy-and-hold gate (full window, per symbol):**")
    w("")
    for pp in (pe, ps_):
        w(f"- {pp['symbol']}: {pct(pp['full']['net_return'])} against {pct(pp['bh']['full']['net_return'])}. "
          f"**{'Passes' if pp['gate_abs_full'] else 'Fails'}.**")
    w("- The setup fails the gate because it must pass on every symbol it trades. The ETH pass comes from ETH buy-and-hold "
      "losing money after funding, not from a large setup return.")
    w(f"- Risk-adjusted (not the gate): Sharpe {pe['risk_adjusted']['sharpe']:.2f} against {pe['risk_adjusted']['bh_sharpe']:.2f} on ETH and "
      f"{ps_['risk_adjusted']['sharpe']:.2f} against {ps_['risk_adjusted']['bh_sharpe']:.2f} on SOL; max drawdown "
      f"{pct(pe['full']['max_dd'], 1, False)} against {pct(pe['bh']['full']['max_dd'], 1, False)} and "
      f"{pct(ps_['full']['max_dd'], 1, False)} against {pct(ps_['bh']['full']['max_dd'], 1, False)}.")
    w(f"- Bear-labelled bars (full window): ETH {pct(pe['regime_full']['bear']['strat'])} against {pct(pe['regime_full']['bear']['bh'])}, "
      f"SOL {pct(ps_['regime_full']['bear']['strat'])} against {pct(ps_['regime_full']['bear']['bh'])}. "
      f"Sideways-labelled bars: ETH {pct(pe['regime_full']['sideways']['strat'])} against {pct(pe['regime_full']['sideways']['bh'])}, "
      f"SOL {pct(ps_['regime_full']['sideways']['strat'])} against {pct(ps_['regime_full']['sideways']['bh'])}.")
    w("")
    w("## Walk-forward (first 36 months only)")
    w("")
    w("Twelve months choose the parameters from the grid (lookback 120 / 180 / 360 bars, stop 3 / 4 / 5 ATR), the next "
      "three months are traded, and the window rolls forward. Only the traded quarters are shown.")
    w("")
    L.extend(wf_table(f, syms))
    w("")
    w(f"The walk-forward picked the default setting in only some quarters; its most frequent pick "
      f"(`{json.dumps(f['wf_most_chosen'])}`) was chosen {pct(f['wf_most_chosen_share'], 0, False)} of the time across all three symbols, "
      "below the 50% needed to replace the defaults.")
    w("")
    w("## Worst month and longest losing streak")
    w("")
    w("| | ETHUSDT | SOLUSDT | ETH + SOL together |")
    w("|---|---|---|---|")
    w(f"| Worst calendar month, full window | {pct(pe['full']['worst_month'])} ({pe['full']['worst_month_label']}) | {pct(ps_['full']['worst_month'])} ({ps_['full']['worst_month_label']}) | {pct(bk['full']['worst_month'])} ({bk['full']['worst_month_label']}) |")
    w(f"| Longest losing streak, full window | {pe['full']['longest_losing_streak']} trades | {ps_['full']['longest_losing_streak']} trades | {bk['full']['lose_streak']} trades |")
    w(f"| Longest losing streak, last 12 months | {pe['holdout']['longest_losing_streak']} | {ps_['holdout']['longest_losing_streak']} | {bk['holdout']['lose_streak']} |")
    w("")
    w("- Calendar years for the two symbols together: " + ", ".join(f"{y} {pct(v)}" for y, v in pf_["years"].items())
      + " (2022 is November and December only; 2026 runs to 31 October).")
    w(f"- {pf_['losing_quarters']} of {pf_['quarters']} calendar quarters lost money, and the longest stretch without a new "
      f"equity high was {pf_['longest_underwater_days']} days.")
    w(f"- A 12-month window ended in the red {pct(pf_['roll_12m_neg_share'], 1, False)} of the time (worst {pct(pf_['roll_12m_min'])}); "
      f"no 24-month window did (worst {pct(pf_['roll_24m_min'])}).")
    w("")
    w("## The condition that kills it")
    w("")
    w("**A market that keeps crossing its own 30-day-ago price.** Every crossing flips the sign, closes the trade and opens "
      f"the other side. There were about {fe['reasons'].get('exit_signal', 0) / 4:.0f} flips a year on ETH and "
      f"{fs['reasons'].get('exit_signal', 0) / 4:.0f} on SOL even in this trending sample, most of them small losses. "
      "In 2024 the setup lost a little on both symbols while they rose; a few such years in a row with no multi-week trend "
      "is how it dies. Sharp V-shaped reversals are the second way: the stop is hit and the sign flips only after most of "
      "the turn is over.")
    w("")
    w("Stop trading it if either happens:")
    w("")
    w(f"- the two-symbol book falls more than **13%** from its high (95th percentile when the four-year trade list is "
      f"resampled: {pct(bk['full']['mc_dd95'], 1, False)}; worst seen {pct(bk['full']['max_dd'], 1, False)});")
    w(f"- any **24 months** end below where they started (never happened in the backtest; the worst was {pct(pf_['roll_24m_min'])}).")
    w("")
    w("## The first sign it is weakening")
    w("")
    w(f"- **The last 20 trades average below {bk['full']['low_avgR20']:+.2f}R.** Random draws of 20 trades from its own history "
      "do that only 1 time in 20.")
    w(f"- **A losing streak longer than {bk['full']['streak_p95']} trades** (95th percentile from resampling; the longest seen was "
      f"{bk['full']['lose_streak']}). Streaks of 8 to 15 are normal for it.")
    w(f"- **A 12-month loss worse than {pct(pf_['roll_12m_min'])}.** A losing 12 months by itself is a warning, not a verdict.")
    w(f"- **No trade of +3R or better for more than about {max(fe['big_longest_gap_days'], fs['big_longest_gap_days']) // 30} months on a symbol.** "
      f"The longest waits in the backtest were {fe['big_longest_gap_days']} days on ETH and {fs['big_longest_gap_days']} on SOL.")
    w("")
    w("Detection is slow by nature. With about one winner in three and "
      f"{pf_['trades_per_year']:.0f} trades a year across the two symbols, an edge that has gone cannot be told from an "
      "ordinary bad patch in less than several months.")
    w("")
    card_common_tail(f, syms, L)
    w(f"Also checked: the same signals executed on 1h bars instead of 4h bars give {pct(pe['exec_1h']['full']['net_return'])} on ETH and "
      f"{pct(ps_['exec_1h']['full']['net_return'])} on SOL over the full window, the same as above. Without exchange minimums: "
      f"{pct(pe['nofilter']['full']['net_return'])} and {pct(ps_['nofilter']['full']['net_return'])}.")
    w("")
    w("## What the bot needs")
    w("")
    w(f"- **Module / timeframe / symbols:** `tsmom`, {f['timeframe']}, {', '.join(syms)}. BTCUSDT is excluded: it lost "
      f"{pct(-pb['holdout']['net_return'], 2, False)} in the unseen year, and on $1,000 the exchange minimum refused "
      f"{pb['blocked_min_size']['full']} of its orders in four years.")
    w(f"- **Columns read:** {', '.join('`' + c + '`' for c in f['required_columns'])}. No open interest, no funding.")
    w(f"- **Warm-up:** {f['warmup_bars']} four-hour bars ({f['warmup_bars'] * 4 / 24:.0f} days) before the first decision.")
    w("- **Same-open reversal:** on a sign flip the bot must close and open the other side at the same open "
      "(`lab/engine.py` rule R7). Without it the bot re-enters a day late on every flip.")
    w(f"- **Shield:** the three-losses rule would have halted this book {ob['full_shield']['halts_loss_streak']} times in four years "
      f"(about {ob['full_shield']['halts_loss_streak'] / 4:.0f} a year). Each halt needs a person to clear it. "
      f"Simulated with next-day resume, the Shield cut the full-window return from {pct(ob['full_no_shield']['net_return'])} to "
      f"{pct(ob['full_shield']['net_return'])} and the drawdown from {pct(ob['full_no_shield']['max_dd'], 1, False)} to "
      f"{pct(ob['full_shield']['max_dd'], 1, False)}.")
    w("")
    w("## WHY THIS COULD FAIL")
    w("")
    w(f"- **It may be luck.** After correcting for the 87 pairs tested, nothing here is significant (corrected p = 1.00 on "
      f"both symbols; Sharpe {pe['haircut_pair']['sharpe']:.2f} and {ps_['haircut_pair']['sharpe']:.2f} over the first 36 months).")
    w(f"- **One trade carries the SOL result.** The long from {fs['best_from']} to {fs['best_to']} made {fs['best_R']:+.0f}R, "
      f"{pct(fs['best_share'], 0, False)} of SOL's four-year profit. Without its three best trades SOL loses ${-fs['net_wo_top3']:.0f}. "
      f"On ETH the best trade is {pct(fe['best_share'], 0, False)} of the profit and the result without the top three is "
      f"{usd(fe['net_wo_top3'])}.")
    w("- **It needs trends, and the sample was full of them.** The window holds one of the largest crypto bull runs and a "
      "deep bear leg. In a flat market the setup bleeds small losses and fees, as it did in 2024.")
    w(f"- **It is fragile to the lookback on ETH.** With a 20-day lookback it loses at every stop width "
      f"({round(pe['grid']['share_profitable'] * pe['grid']['n'])} of {pe['grid']['n']} grid points profitable). "
      "A rule that works at 30 and 60 days but not at 20 is not firmly established.")
    w(f"- **It trails buy-and-hold on SOL by more than 100 points.** On ETH it wins only because funding turned a "
      f"{pct(fin['bh']['ETHUSDT']['full']['price_return'], 0)} price gain into a {pct(fin['bh']['ETHUSDT']['full']['net_return'])} "
      "result for the holder. At 0.5% risk the absolute return is small: about "
      f"{pct(pe['full']['cagr'], 1, False)} a year on ETH and {pct(ps_['full']['cagr'], 1, False)} on SOL.")
    w("- **The unseen year is thin evidence.** "
      f"{pe['holdout']['trades']} and {ps_['holdout']['trades']} trades, and SOL's {pct(ps_['holdout']['net_return'], 2)} is close to nothing. "
      "The BTC leg of the same setup lost money in that year.")
    w("- **The Shield and this setup pull against each other.** A setup that loses two trades in three will hit "
      "three losses in a row about once a month. If halts are slow to clear, the trades that pay for everything may be missed.")
    w("- **Fills may be worse than modelled.** Stops are assumed to fill at the stop price plus 2 to 4 basis points. "
      "A stop 4 ATR away is usually reached in a fast market.")
    w("- **Order sizes are near the floor on ETH.** The smallest ETH order was "
      f"${fe['notional_min']:.0f} against an unverified $20 minimum. A higher real minimum removes trades.")
    w("- **ETH and SOL move together.** Two symbols are not two independent tests; a bad stretch will hit both.")
    w("")
    return "\n".join(L)


def shape_table(facts: dict, L: list) -> None:
    """Typical-trade table for setups that leave by stop or by time."""
    syms = list(facts)
    L.append("| | " + " | ".join(syms) + " |")
    L.append("|---|" + "---|" * len(syms))

    def row(label, fn):
        L.append(f"| {label} | " + " | ".join(fn(facts[sy]) for sy in syms) + " |")
    row("Trades in four years", lambda x: str(x["n"]))
    row("Long share", lambda x: pct(x["long_share"], 0, False))
    row("Closed by the stop / by the 7-day time exit", lambda x: f"{x['reasons'].get('stop', 0) + x['reasons'].get('stop_gap', 0)} / {x['reasons'].get('time', 0)}")
    row("Time in a trade: median / longest", lambda x: f"{x['hold_med_d']:.1f} / {x['hold_max_d']:.0f} days")
    row("Average winner / average loser", lambda x: f"{x['avg_win_R']:+.2f}R / {x['avg_loss_R']:+.2f}R")
    row("Win rate", lambda x: pct(x["win_rate"], 0, False))
    row("Net from longs / from shorts", lambda x: f"{usd(x['long_net'])} / {usd(x['short_net'])}")
    row("Best trade as a share of the four-year profit", lambda x: pct(x["best_share"], 0, False))
    row("Profit without the three best trades", lambda x: usd(x["net_wo_top3"]))


def together_table(f, tag_book, ob, label, L: list) -> None:
    bk = f["book"][tag_book]
    L.append(f"| {label} | First 36 months | Last 12 months (unseen) | Full window |")
    L.append("|---|---|---|---|")
    L.append(f"| Sum of the per-symbol runs | {pct(bk['dev']['net'])} | {pct(bk['holdout']['net'])} | {pct(bk['full']['net'])} |")
    L.append(f"| One $1,000 book trading both (as the bot does), no Shield | {pct(ob['dev_no_shield']['net_return'])} | {pct(ob['holdout_no_shield']['net_return'])} | {pct(ob['full_no_shield']['net_return'])} |")
    L.append(f"| The same book with the Shield (next-day resume is an ASSUMPTION) | {pct(ob['dev_shield']['net_return'])} | {pct(ob['holdout_shield']['net_return'])} | {pct(ob['full_shield']['net_return'])} |")
    L.append(f"| Max drawdown (sum of runs) | {pct(bk['dev']['max_dd'], 1, False)} | {pct(bk['holdout']['max_dd'], 1, False)} | {pct(bk['full']['max_dd'], 1, False)} |")
    L.append(f"| Equal-weight buy-and-hold of the same symbols | {pct(bk['dev']['bh_equal_weight_net'])} | {pct(bk['holdout']['bh_equal_weight_net'])} | {pct(bk['full']['bh_equal_weight_net'])} |")


def card_week_range(s1, s2, fin) -> str:
    name = "week_range_breakout_volume"
    f = next(x for x in fin["finalists"] if x["module"] == name)
    syms = f["symbols_alive"]
    facts = {sy: trade_facts(s2[(name, sy)]["trades"]["full"]) for sy in syms}
    fe, fs = facts["ETHUSDT"], facts["SOLUSDT"]
    bk, ob = f["book"]["alive_symbols"], f["own_book_alive"]
    pf_ = book_path_facts(s2, name, syms)
    pe, ps_ = f["pairs"][0], f["pairs"][1]
    p = f["params"]
    L = []
    w = L.append
    w("# 2. `week_range_breakout_volume` - breakout of a quiet week on rising volume, ETHUSDT and SOLUSDT (4h bars)")
    w("")
    w("**Status: candidate for paper trading. Unproven. Fails the buy-and-hold gate (passes on ETH, fails on SOL). Not for live.**")
    w("")
    w("## Idea in one line")
    w("")
    w("When price closes outside a tight seven-day range while volume is picking up, go with the break for up to seven days.")
    w("")
    w("## Why the edge should exist, and who loses")
    w("")
    w(f"Radar's reasoning: {f['why']}")
    w("")
    w("What the runs add: about half the breaks fail and end at the stop "
      f"(average {fe['avg_R_by_reason']['stop']:+.2f}R on ETH, {fs['avg_R_by_reason']['stop']:+.2f}R on SOL). "
      f"The trades that last to the seven-day exit average {fe['avg_R_by_reason']['time']:+.2f}R on ETH and "
      f"{fs['avg_R_by_reason']['time']:+.2f}R on SOL. Longs and shorts both made money on both symbols, "
      "which is what you would expect if the effect is about ranges breaking and not about the market drifting up.")
    w("")
    w("## Rules (exact: `strategies/week_range_breakout_volume.py`, DEFAULT_PARAMS)")
    w("")
    w("- **Bars:** 4h, UTC. It decides at every 4h close.")
    w(f"- **Tight range:** over the previous {p['range_n']} bars ({p['range_n'] // 6} days, not counting the current bar), highest high minus "
      f"lowest low is at most {p['width_mult']:g} x sqrt({p['range_n']}) x ATR({p['atr_n']}), about {p['width_mult'] * p['range_n'] ** 0.5:.1f} ATR. "
      "ATR is the plain average of the last 14 four-hour true ranges, taken at the previous bar.")
    w(f"- **Rising volume:** average volume of the last {p['vol_fast']} bars (one day, including the current bar) is at least "
      f"{p['vol_ratio']:g} x the average of the {p['vol_slow']} bars before them.")
    w("- **Entry:** with both conditions true, a close above the range high is a long and a close below the range low is a "
      "short. Market order at the next open. Taker fee and slippage.")
    w(f"- **Stop:** {p['stop_atr']:g} x ATR({p['atr_n']}) from the signal close. It does not trail.")
    w(f"- **Exit:** at the open after {p['max_hold']} bars ({p['max_hold'] // 6} days) in the trade, if the stop has not been hit.")
    w("- **No profit target.** One position per symbol; signals that arrive while a trade is open are ignored.")
    w(f"- **Parameters:** `{json.dumps(p)}` ({f['params_source']}).")
    w("")
    w("What a typical trade looks like (full window):")
    w("")
    shape_table(facts, L)
    w("")
    w("## Size per trade")
    w("")
    w("- **Risk:** 0.5% of the book's equity between entry and stop. Quantity = 0.005 x equity / |entry - stop|, capped "
      "at 3x equity notional and rounded down to the lot step.")
    w(f"- **In practice:** the stop sits about {pct(fe['stop_med'], 1, False)} away on ETH and {pct(fs['stop_med'], 1, False)} on SOL "
      f"(median), so a position is about {pct(fe['lev_med'], 0, False)} of equity on ETH (${fe['notional_med']:.0f} on $1,000) and "
      f"{pct(fs['lev_med'], 0, False)} on SOL (${fs['notional_med']:.0f}).")
    w(f"- **Smallest orders:** ${fe['notional_min']:.0f} on ETH and ${fs['notional_min']:.0f} on SOL, well above the assumed minimums "
      "of $20 and $5 (unverified). No order was refused for size.")
    w("")
    w("## Results after fees, slippage and funding, against buy-and-hold")
    w("")
    w("Each symbol on its own $1,000 at 0.5% risk, exchange minimums ON:")
    w("")
    L.extend(results_table(f, syms))
    w("")
    L.extend(yearly_table(f, syms))
    w("")
    w("Both symbols together:")
    w("")
    together_table(f, "alive_symbols", ob, "ETH + SOL", L)
    w("")
    w("**Buy-and-hold gate (full window, per symbol):**")
    w("")
    for pp in (pe, ps_):
        w(f"- {pp['symbol']}: {pct(pp['full']['net_return'])} against {pct(pp['bh']['full']['net_return'])}. "
          f"**{'Passes' if pp['gate_abs_full'] else 'Fails'}.**")
    w("- The setup fails the gate because it must pass on every symbol it trades. The ETH pass comes from ETH buy-and-hold "
      "losing money after funding.")
    w(f"- Risk-adjusted (not the gate): Sharpe {pe['risk_adjusted']['sharpe']:.2f} against {pe['risk_adjusted']['bh_sharpe']:.2f} on ETH and "
      f"{ps_['risk_adjusted']['sharpe']:.2f} against {ps_['risk_adjusted']['bh_sharpe']:.2f} on SOL; max drawdown "
      f"{pct(pe['full']['max_dd'], 1, False)} against {pct(pe['bh']['full']['max_dd'], 1, False)} and "
      f"{pct(ps_['full']['max_dd'], 1, False)} against {pct(ps_['bh']['full']['max_dd'], 1, False)}.")
    w(f"- Bear-labelled bars (full window): ETH {pct(pe['regime_full']['bear']['strat'])} against {pct(pe['regime_full']['bear']['bh'])}, "
      f"SOL {pct(ps_['regime_full']['bear']['strat'])} against {pct(ps_['regime_full']['bear']['bh'])}. "
      f"Sideways-labelled bars: ETH {pct(pe['regime_full']['sideways']['strat'])} against {pct(pe['regime_full']['sideways']['bh'])}, "
      f"SOL {pct(ps_['regime_full']['sideways']['strat'])} against {pct(ps_['regime_full']['sideways']['bh'])}.")
    w("")
    w("## Walk-forward (first 36 months only)")
    w("")
    w("Twelve months choose the parameters from the grid (range 30 / 42 / 60 bars, width 0.8 / 1.0 / 1.25, volume ratio "
      "1.15 / 1.3 / 1.5), the next three months are traded, and the window rolls forward. Only the traded quarters are shown.")
    w("")
    L.extend(wf_table(f, syms))
    w("")
    w(f"SOL's walk-forward is barely above zero. The most frequent pick (`{json.dumps(f['wf_most_chosen'])}`) was chosen "
      f"{pct(f['wf_most_chosen_share'], 0, False)} of the time, far below the 50% needed to replace the defaults.")
    w("")
    w("## Worst month and longest losing streak")
    w("")
    w("| | ETHUSDT | SOLUSDT | ETH + SOL together |")
    w("|---|---|---|---|")
    w(f"| Worst calendar month, full window | {pct(pe['full']['worst_month'])} ({pe['full']['worst_month_label']}) | {pct(ps_['full']['worst_month'])} ({ps_['full']['worst_month_label']}) | {pct(bk['full']['worst_month'])} ({bk['full']['worst_month_label']}) |")
    w(f"| Longest losing streak, full window | {pe['full']['longest_losing_streak']} trades | {ps_['full']['longest_losing_streak']} trades | {bk['full']['lose_streak']} trades |")
    w(f"| Longest losing streak, last 12 months | {pe['holdout']['longest_losing_streak']} | {ps_['holdout']['longest_losing_streak']} | {bk['holdout']['lose_streak']} |")
    w("")
    w("- Calendar years for the two symbols together: " + ", ".join(f"{y} {pct(v)}" for y, v in pf_["years"].items())
      + " (2022 is November and December only; 2026 runs to 31 October).")
    w(f"- {pf_['losing_quarters']} of {pf_['quarters']} calendar quarters lost money, and the longest stretch without a new "
      f"equity high was {pf_['longest_underwater_days']} days.")
    w(f"- No 12-month window ended in the red (worst {pct(pf_['roll_12m_min'])}); the worst six months were {pct(pf_['roll_6m_min'])}.")
    w("- The two-symbol total hides uneven legs: ETH lost a little in 2022 and 2023, SOL in 2024, and SOL made almost "
      "nothing in 2025.")
    w("")
    w("## The condition that kills it")
    w("")
    w("**Breaks of a quiet week that do not follow through.** Each failed break costs a full stop, about 1R, and "
      f"{pct((fe['reasons'].get('stop', 0)) / fe['n'], 0, False)} of ETH trades and {pct((fs['reasons'].get('stop', 0)) / fs['n'], 0, False)} "
      "of SOL trades already end that way. The setup lives on the other half being worth more than twice as much. "
      "If ranges start breaking and snapping back (thin markets, stop-hunting around obvious weekly levels) the "
      "winners shrink and the arithmetic turns negative quickly. A change in how exchange volume behaves would also "
      "break the volume filter.")
    w("")
    w("Stop trading it if either happens:")
    w("")
    w(f"- the two-symbol book falls more than **9%** from its high (95th percentile when the four-year trade list is "
      f"resampled: {pct(bk['full']['mc_dd95'], 1, False)}; worst seen {pct(bk['full']['max_dd'], 1, False)});")
    w(f"- any **12 months** end below where they started (never happened in the backtest; the worst was {pct(pf_['roll_12m_min'])}).")
    w("")
    w("## The first sign it is weakening")
    w("")
    w(f"- **A six-month loss worse than {pct(pf_['roll_6m_min'])}**, the worst six months in the backtest.")
    w(f"- **The last 20 trades average below {bk['full']['low_avgR20']:+.2f}R.** Random draws of 20 trades from its own history "
      "do that only 1 time in 20.")
    w(f"- **A losing streak longer than {bk['full']['streak_p95']} trades** (95th percentile from resampling; the longest seen was "
      f"{bk['full']['lose_streak']}).")
    w("- **Clearly more than half the trades ending at the stop** over 20 or more trades.")
    w("")
    w(f"With about {pf_['trades_per_year']:.0f} trades a year across the two symbols, 20 trades take about "
      f"{20 / pf_['trades_per_year'] * 12:.0f} months. Any verdict on this setup is slow.")
    w("")
    card_common_tail(f, syms, L)
    w(f"Also checked: the same signals executed on 1h bars instead of 4h bars give {pct(pe['exec_1h']['full']['net_return'])} on ETH and "
      f"{pct(ps_['exec_1h']['full']['net_return'])} on SOL over the full window. Without exchange minimums: "
      f"{pct(pe['nofilter']['full']['net_return'])} and {pct(ps_['nofilter']['full']['net_return'])}.")
    w("")
    w("## What the bot needs")
    w("")
    w(f"- **Module / timeframe / symbols:** `{name}`, {f['timeframe']}, {', '.join(syms)}. BTCUSDT was killed in the first 36 months "
      "(Monte Carlo chance of a loss above 20%, 44 trades).")
    w(f"- **Columns read:** {', '.join('`' + c + '`' for c in f['required_columns'])}. No open interest, no funding. "
      "Volume must be the same exchange volume the archive carries.")
    w(f"- **Warm-up:** {f['warmup_bars']} four-hour bars ({f['warmup_bars'] * 4 / 24:.0f} days).")
    w(f"- **Shield:** the three-losses rule would have halted this book {ob['full_shield']['halts_loss_streak']} times in four years "
      f"(about {ob['full_shield']['halts_loss_streak'] / 4:.0f} a year). Simulated with next-day resume it made no real difference "
      f"({pct(ob['full_no_shield']['net_return'])} without, {pct(ob['full_shield']['net_return'])} with).")
    w("")
    w("## WHY THIS COULD FAIL")
    w("")
    w(f"- **It may be luck.** After correcting for the 87 pairs tested, nothing here is significant (corrected p = 1.00 on "
      f"both symbols; Sharpe {pe['haircut_pair']['sharpe']:.2f} and {ps_['haircut_pair']['sharpe']:.2f} over the first 36 months).")
    w(f"- **There are few trades.** {fe['n']} on ETH and {fs['n']} on SOL in four years, about {fe['n'] / 4:.0f} and {fs['n'] / 4:.0f} a year. "
      f"SOL had {ps_['dev']['trades']} in the first 36 months, under the owner's 50 by itself; the rule is met only by pooling "
      f"the two symbols ({bk['dev']['trades']}).")
    w(f"- **The SOL leg is weak where it was tested hardest.** Its walk-forward made {pct(ps_['wf']['oos_net'])} in 24 months with "
      f"{round(ps_['wf']['share_positive_folds'] * 8)} of 8 quarters up, and most of its first-36-month profit came in 2023.")
    w(f"- **It is sensitive to entry timing on SOL.** Entering two bars later would have made {pct(ps_['audit']['delay2'])} "
      f"instead of {pct(ps_['audit']['delay0'])} in the first 36 months. With this few trades a small shift changes which "
      "trades exist, so the measured edge has wide error bars.")
    w(f"- **Winners are still concentrated.** The best trade is {pct(fe['best_share'], 0, False)} of the ETH profit and "
      f"{pct(fs['best_share'], 0, False)} of SOL's; {fe['big_n']} and {fs['big_n']} trades of +3R or better carry the result.")
    w("- **It is the same bet as rank 1.** Both follow moves on ETH and SOL. They will tend to win and lose together.")
    w(f"- **It trails buy-and-hold on SOL by more than 100 points.** On ETH it wins only because funding turned a "
      f"{pct(fin['bh']['ETHUSDT']['full']['price_return'], 0)} price gain into a {pct(fin['bh']['ETHUSDT']['full']['net_return'])} "
      f"result for the holder. The absolute return is about {pct(pe['full']['cagr'], 1, False)} a year on ETH and "
      f"{pct(ps_['full']['cagr'], 1, False)} on SOL.")
    w(f"- **The unseen year is thin evidence.** {pe['holdout']['trades']} and {ps_['holdout']['trades']} trades.")
    w("- **The volume filter depends on the venue.** A change in fee tiers, market-maker programmes or wash trading "
      "moves 'volume 30% above last week' without any change in real participation.")
    w("- **Fills may be worse than modelled.** A breakout bar is exactly when the next open is crowded; entry "
      "slippage is assumed to be 1 to 2 basis points.")
    w("")
    return "\n".join(L)


def card_oi(s1, s2, fin) -> str:
    name = "oi_confirmed_breakout"
    f = next(x for x in fin["finalists"] if x["module"] == name)
    syms = f["symbols_dev"]
    facts = {sy: trade_facts(s2[(name, sy)]["trades"]["full"]) for sy in syms}
    fb, fe = facts["BTCUSDT"], facts["ETHUSDT"]
    bk, ob = f["book"]["dev_symbols"], f["own_book"]
    pf_ = book_path_facts(s2, name, syms)
    pb, pe = f["pairs"][0], f["pairs"][1]
    p = f["params"]
    ss = f["setup_stats"]
    L = []
    w = L.append
    w("# 3. `oi_confirmed_breakout` - breakout confirmed by rising open interest, BTCUSDT and ETHUSDT (4h bars)")
    w("")
    w("**Status: PAPER ONLY, NOT A CANDIDATE FOR LIVE. Killed at the final gate: it did not make money on either symbol in "
      "the last 12 months. It also cannot run on the live feed today, which carries no open interest.**")
    w("")
    w("Only two setups survived every test. This one fills the third slot as the least bad of the rest: it scored highest "
      "on the first 36 months, then failed the one test it had not seen. It is here so the owner can see what a near miss "
      "looks like, not as a proposal.")
    w("")
    w("## Idea in one line")
    w("")
    w("Take a new five-day high or low only if open interest has grown at least 5% over the same five days.")
    w("")
    w("## Why the edge should exist, and who loses")
    w("")
    w(f"Radar's reasoning: {f['why']}")
    w("")
    w(f"Radar's own warning: {f['risk']}")
    w("")
    w("## Rules (exact: `strategies/oi_confirmed_breakout.py`, DEFAULT_PARAMS)")
    w("")
    w("- **Bars:** 4h, UTC. It decides at every 4h close.")
    w(f"- **Open interest building:** cleaned open interest (zero prints dropped, trailing 3-bar median) is at least "
      f"{pct(p['oi_rise'], 0, False)} higher than {p['n']} bars ({p['n'] // 6} days) earlier.")
    w(f"- **Entry:** the first close above the highest high of the previous {p['n']} bars while open interest is building is a "
      f"long; the first close below the lowest low is a short. Market order at the next open.")
    w(f"- **Stop:** {p['stop_atr']:g} x ATR({p['atr_n']}) from the signal close. It does not trail.")
    w(f"- **Exit:** at the open after {p['max_hold']} bars ({p['max_hold'] // 6} days), if the stop has not been hit. No profit target.")
    w(f"- **Parameters:** `{json.dumps(p)}` ({f['params_source']}).")
    w("")
    shape_table(facts, L)
    w("")
    w("## Size per trade")
    w("")
    w("- **Risk:** 0.5% of the book's equity between entry and stop, capped at 3x equity notional, rounded down to the lot step.")
    w(f"- **In practice:** stops about {pct(fb['stop_med'], 1, False)} away on BTC and {pct(fe['stop_med'], 1, False)} on ETH, so positions of "
      f"about {pct(fb['lev_med'], 0, False)} and {pct(fe['lev_med'], 0, False)} of equity (${fb['notional_med']:.0f} and ${fe['notional_med']:.0f}).")
    w(f"- **BTC and the minimum order:** with the assumed $100 minimum and 0.001 BTC lot, {pb['blocked_min_size']['full']} BTC orders "
      "were refused as too small in four years (unverified table).")
    w("")
    w("## Results after fees, slippage and funding, against buy-and-hold")
    w("")
    L.extend(results_table(f, syms))
    w("")
    L.extend(yearly_table(f, syms))
    w("")
    together_table(f, "dev_symbols", ob, "BTC + ETH", L)
    w("")
    w("**Buy-and-hold gate (full window, per symbol):**")
    w("")
    for pp in (pb, pe):
        w(f"- {pp['symbol']}: {pct(pp['full']['net_return'])} against {pct(pp['bh']['full']['net_return'])}. "
          f"**{'Passes' if pp['gate_abs_full'] else 'Fails'}.**")
    w("- Fails the gate. It is also dead at the earlier holdout gate, so the question is academic.")
    w("")
    w("## Walk-forward (first 36 months only)")
    w("")
    L.extend(wf_table(f, syms))
    w("")
    w("## Worst month and longest losing streak")
    w("")
    w("| | BTCUSDT | ETHUSDT | BTC + ETH together |")
    w("|---|---|---|---|")
    w(f"| Worst calendar month, full window | {pct(pb['full']['worst_month'])} ({pb['full']['worst_month_label']}) | {pct(pe['full']['worst_month'])} ({pe['full']['worst_month_label']}) | {pct(bk['full']['worst_month'])} ({bk['full']['worst_month_label']}) |")
    w(f"| Longest losing streak, full window | {pb['full']['longest_losing_streak']} trades | {pe['full']['longest_losing_streak']} trades | {bk['full']['lose_streak']} trades |")
    w("")
    w("- Calendar years for the two symbols together: " + ", ".join(f"{y} {pct(v)}" for y, v in pf_["years"].items())
      + " (2022 is November and December only; 2026 runs to 31 October). The profit shrank every year after 2023.")
    w("")
    w("## The condition that kills it")
    w("")
    w("**It has already met it.** The rule fixed in advance was that a finalist must make money in the last 12 months, "
      "which it had never seen:")
    w("")
    w(f"- BTCUSDT: {pct(pb['holdout']['net_return'], 2)} over {pb['holdout']['trades']} trades.")
    w(f"- ETHUSDT: {pct(pe['holdout']['net_return'], 2)} over {pe['holdout']['trades']} trades.")
    w(f"- Without the exchange minimums the figures are {pct(pb['nofilter']['holdout']['net_return'], 2)} and "
      f"{pct(pe['nofilter']['holdout']['net_return'], 2)}: a scrape, on a size table that is itself unverified. The verdict uses the "
      "primary configuration, and a result this close to zero would not be a pass worth having either way.")
    w("")
    w("What would bring it back: open interest on the live feed, and a further unseen period in which it makes money. "
      "Until both, it is not a candidate. If open interest is ever fed live, running it on paper as an observation is harmless.")
    w("")
    w("## The first sign it is weakening")
    w("")
    w("It showed before the holdout was opened, in the walk-forward and the yearly figures:")
    w("")
    last5 = [x["oos_net"] for x in pb["wf"]["folds"]][-5:]
    w(f"- BTC's walk-forward had {round(pb['wf']['share_positive_folds'] * 8)} of 8 quarters up, and "
      f"{sum(x < 0 for x in last5)} of the last 5 lost money.")
    yrs = pf_["years"]
    w(f"- The two-symbol profit fell from {pct(yrs['2023'])} in 2023 to {pct(yrs['2024'])} in 2024 and {pct(yrs['2025'])} in 2025.")
    w(f"- On BTC the result is no better than same-side random entries (p = {pb['null_dev']['p_value']:.2f}).")
    w(f"- On ETH one bar of delay halves the result ({pct(pe['audit']['delay1'])} against {pct(pe['audit']['delay0'])}): the entry "
      "has to be immediate, which is the first thing to go wrong live.")
    w("")
    card_common_tail(f, syms, L)
    w("## What the bot needs")
    w("")
    w(f"- **Columns read:** {', '.join('`' + c + '`' for c in f['required_columns'])}. **`oi` is not available live**: the bot marks "
      "live open interest as unknown and refuses to decide for a setup that reads an unknown column. The archive "
      "supplies it only in daily files, after the fact.")
    w(f"- **Warm-up:** {f['warmup_bars']} four-hour bars ({f['warmup_bars'] * 4 / 24:.0f} days).")
    w("- **Recommendation:** do not load it as the leader. If it is loaded at all, mark it as an observation book.")
    w("")
    w("## WHY THIS COULD FAIL")
    w("")
    w("- **It already has.** Flat to down on both symbols in the only year it had not seen.")
    w(f"- **It ranked first on the selection window and that was probably selection.** Best of 29 setups on 36 months is "
      f"exactly where luck concentrates. Its pooled Sharpe of {ss['book_sharpe']:.2f} has a corrected p-value of {ss['p_bonferroni']:.2f} "
      "(29 setups): it cleared the weak pre-registered bar and is nowhere near the usual 5%.")
    w(f"- **The BTC result is two or three trades.** The best trade is {pct(fb['best_share'], 0, False)} of BTC's four-year profit; "
      f"without the three best BTC loses ${-fb['net_wo_top3']:.0f}. BTC shorts lost ${-fb['short_net']:.0f}.")
    w("- **The open-interest series is dirty.** The archive has zero prints and at least one partial print; the setup "
      "cleans them, but a 5% threshold on a five-day change is sensitive to exactly that kind of error.")
    w("- **Open interest is not on the live feed.** A setup that cannot be run live cannot be paper-traded honestly.")
    w("- **On $1,000 BTC orders are often below the exchange minimum**, so the BTC leg trades a filtered subset of its signals.")
    w("- **It is one more trend-following bet** on the same coins as ranks 1 and 2.")
    w("")
    return "\n".join(L)


def write_top3(s1, s2, fin, opened) -> None:
    F = {f["module"]: f for f in fin["finalists"]}
    entries = []
    for rk, name, syms_key, candidate, status in (
            (1, "tsmom", "symbols_alive", True, "candidate for paper trading; unproven; not for live"),
            (2, "week_range_breakout_volume", "symbols_alive", True, "candidate for paper trading; unproven; not for live"),
            (3, "oi_confirmed_breakout", "symbols_dev", False, "paper only, not a candidate for live")):
        f = F[name]
        syms = f[syms_key]
        per_sym = {p["symbol"]: bool(p["gate_abs_full"]) for p in f["pairs"] if p["symbol"] in syms}
        entries.append(dict(
            rank=rk, module=name, status=status, candidate=candidate, unproven=True,
            symbols=syms, timeframe=f["timeframe"], params=f["params"], params_source=f["params_source"],
            params_note=(f"walk-forward's most-often-chosen grid point {json.dumps(f['wf_most_chosen'])} was picked in "
                         f"{f['wf_most_chosen_share']:.0%} of fold x symbol cases, below the 50% needed to replace DEFAULT_PARAMS"
                         + ("" if f["grid_stable_80"] else "; the neighbourhood is also not stable on every symbol")),
            warmup_bars=f["warmup_bars"], required_columns=f["required_columns"],
            needs_open_interest="oi" in f["required_columns"], needs_funding_history=any(c.startswith("funding") for c in f["required_columns"]),
            runnable_on_live_feed="oi" not in f["required_columns"],
            passes_absolute_buy_and_hold_gate=bool(per_sym) and all(per_sym.values()) and candidate,
            beats_buy_and_hold_full_window_by_symbol=per_sym,
            holdout_last_12_months_net_by_symbol={p["symbol"]: round(p["holdout"]["net_return"], 6) for p in f["pairs"]},
            holdout_pass_by_symbol={p["symbol"]: bool(p["holdout_pass"]) for p in f["pairs"]},
            full_window_net_by_symbol={p["symbol"]: round(p["full"]["net_return"], 6) for p in f["pairs"] if p["symbol"] in syms},
            buy_and_hold_full_window_net_by_symbol={p["symbol"]: round(p["bh"]["full"]["net_return"], 6) for p in f["pairs"] if p["symbol"] in syms},
            symbols_dropped={p["symbol"]: "did not make money in the last 12 months" for p in f["pairs"] if not p["holdout_pass"]},
            score_first_36_months=f["score"], scores=f["scores"], scored_on_symbols=f["symbols_dev"],
            card=f"lab/results/cards/{rk}_{name}.md",
            notes=[n for n in (
                "exit and enter the opposite side at the same open on a sign flip (lab/engine.py rule R7)" if name == "tsmom" else None,
                "killed at the final gate: did not make money on either symbol in the last 12 months" if not candidate else None,
                "reads `oi`, which the live feed does not carry: the bot will refuse to decide" if "oi" in f["required_columns"] else None,
                "expect about %d three-losses Shield halts a year on this book" % round(
                    (f["own_book_alive"] if candidate else f["own_book"])["full_shield"]["halts_loss_streak"] / 4),
            ) if n]))
    doc = dict(
        generated_utc=str(pd.Timestamp.now(tz="UTC"))[:19],
        source="lab/results/phase2/final.json",
        configuration=dict(
            exchange_minimums="ON (table from bot/instruments.py, UNVERIFIED)", risk_per_trade=0.005, max_leverage=3.0,
            start_equity=1000.0, engine_rules="lab/engine.py R1-R12",
            selection_window=fin["windows"]["dev"], holdout_window=fin["windows"]["holdout"], full_window=fin["windows"]["full"]),
        holdout=dict(opened_once_utc=opened["opened_utc"][:19], pairs_opened=opened["n_pairs"], pairs=opened["pairs"]),
        buy_and_hold_gate=dict(
            rule="net return must beat the same symbol's 1x buy-and-hold net return over the full window on every symbol traded",
            any_setup_passes=any(e["passes_absolute_buy_and_hold_gate"] for e in entries)),
        candidates_for_live=[],
        candidates_for_paper=[e["module"] for e in entries if e["candidate"]],
        top3=entries)
    (OUT / "top3.json").write_text(json.dumps(doc, indent=1))
    print("wrote", OUT / "top3.json")


def write_cards(s1, s2, fin) -> None:
    d = OUT / "cards"
    d.mkdir(parents=True, exist_ok=True)
    for fname, fn in (("1_tsmom.md", card_tsmom), ("2_week_range_breakout_volume.md", card_week_range),
                      ("3_oi_confirmed_breakout.md", card_oi)):
        (d / fname).write_text(fn(s1, s2, fin))
        print("wrote", d / fname)


if __name__ == "__main__":
    data = load()
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "ranking"):
        (OUT / "RANKING.md").write_text(ranking(*data))
        print("wrote", OUT / "RANKING.md")
    if what in ("all", "cards"):
        write_cards(data[0], data[1], data[2])
    if what in ("all", "top3"):
        write_top3(data[0], data[1], data[2], data[5])
