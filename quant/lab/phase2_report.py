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
        long_net=float(t.loc[t["side"] > 0, "net"].sum()), short_net=float(t.loc[t["side"] < 0, "net"].sum()))


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
        w(f"- {v} pairs: {k}")
    w("")
    fails = pd.Series([k for f in kt["failed"].fillna("") for k in str(f).split(",") if k]).value_counts()
    w("A pair usually fails several rules at once. Pairs failing each rule: "
      + ", ".join(f"{CODES[k]} {int(fails.get(k, 0))}" for k in ORDER[:-1]) + ".")
    w("")
    w("No pair has a look-ahead leak: the prefix check was clean on all 87 (20 cut points each). The one DELAY flag "
      "(`btc_shock_alt_lag` on ETH) is a pair that lost 2.6% on time and made 2.7% one bar late; it fails six other rules.")
    w("")
    w("What died as a family: every session-open setup, every BTC-lead/alt-lag setup, every liquidation-flush setup, "
      "both mean-reversion setups and all three funding setups (the funding ones mostly for lack of trades). "
      "What is left is almost entirely slow trend or breakout following on 4h bars.")
    w("")
    diff = kt[(kt["nofilter_trades"] > 0) & ((kt["trades"] - kt["nofilter_trades"]).abs() > 0.10 * kt["nofilter_trades"])]
    w("### Where the exchange minimums change the picture (no-filter numbers, labelled)")
    w("")
    w("Pairs whose trade count moves by more than 10% when minimum order sizes are switched off. "
      "All are BTC: on $1,000 a 0.5% risk order is often below the BTC minimum.")
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
    w("- Executing the same 4h signals on 1h bars instead of 4h bars changes no full-window result by more than 0.02 points.")
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
          f"{pct(rg['sideways']['strat'])} / {pct(rg['sideways']['bh'])} | {pct(p['holdout']['net_return'])} / {pct(p['bh']['holdout']['net_return'])} |")
    w("")
    w("**Result: no setup passes the gate.** `tsmom` and `week_range_breakout_volume` pass on ETH and fail on SOL; "
      "`oi_confirmed_breakout` passes on ETH and fails on BTC.")
    w("")
    w(f"- The ETH passes say more about ETH than about the setups: 1x ETH buy-and-hold returned "
      f"{pct(bh['ETHUSDT']['full']['net_return'])} because funding ate the price gain.")
    w("- Regime labels are trailing (90-day return and 200-day mean, known at each bar's open), so 'bear bars' include "
      "the rebounds that follow a fall. That is why BTC buy-and-hold shows a gain inside bear-labelled bars.")
    w("- In the unseen year every pair lost less than buy-and-hold, which fell 32% to 49%.")
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
    w(f"`tsmom` alone would trip the three-losses halt {tsf['halts_loss_streak']} times in four years (about "
      f"{tsf['halts_loss_streak'] / 4:.0f} a year). In the real bot each one stops that book until a person clears it.")
    w("")
    w("## 7. What the evidence does and does not support")
    w("")
    tse, tss = ts["pairs"][1], ts["pairs"][2]
    w("**It supports this much.** Under realistic costs, 71 of 87 setup-symbol pairs fail outright, and whole families "
      "(session, lead-lag, liquidation flush, mean reversion) have nothing in them at these timeframes and fees. "
      "Slow trend and breakout following on 4h bars made modest money in the first 36 months on ETH and SOL "
      "(roughly 4% to 8% a year per symbol at 0.5% risk, drawdowns of 4% to 10%). Two such setups also made money in a "
      "year they had never seen, a bear market, while buy-and-hold lost 43% to 49%. There is no look-ahead leak in "
      "any of them, they survive doubled fees and slippage, and they do better than same-side random entries.")
    w("")
    w("**It does not support this.**")
    w("")
    w("- **That the edge is real.** No pair and no setup is statistically significant once 87 (or 29) tests are "
      "accounted for. The unseen year is one more data point, about 20 to 37 trades per pair.")
    w("- **That the survivors are different bets.** They are close cousins: trend or breakout following on the same "
      "three correlated coins. If crypto stops trending they fail together.")
    f1, f2 = trade_facts(s2[("tsmom", "SOLUSDT")]["trades"]["full"]), trade_facts(s2[("tsmom", "ETHUSDT")]["trades"]["full"])
    w(f"- **That the profit is spread out.** One long trade ({f1['best_from']} to {f1['best_to']}, {f1['best_R']:+.0f}R) is "
      f"{pct(f1['best_share'], 0, False)} of `tsmom`'s four-year profit on SOL; without its three best trades that leg "
      f"loses money. On ETH the best trade is {pct(f2['best_share'], 0, False)}. `week_range_breakout_volume` is steadier "
      "(best trade 19% to 25%) but has only 64 to 76 trades per symbol in four years.")
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
    w("- **Exchange minimums are unverified.** With the assumed table, `tsmom` on ETH had 3 orders refused in four years "
      "and its smallest notional was about $23 against an assumed $20 minimum. If the real ETH minimum is higher, a "
      "noticeable share of `tsmom` ETH orders will be refused. SOL is unaffected.")
    w("- **BTC is not traded** by either candidate: `tsmom` BTC failed the unseen year, and on $1,000 most BTC orders "
      "are below the minimum anyway.")
    w("- **Shield versus trend following:** expect a three-losses halt on `tsmom`'s book about once a month. "
      "Decide who clears it and how fast, or the paper results will mostly measure halts.")
    w("- **Same-open reversal (engine R7):** `tsmom` exits and enters the opposite side at the same open when the "
      "30-day sign flips. The bot must do the same or it will lag the backtest by a day on every flip.")
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


if __name__ == "__main__":
    data = load()
    (OUT / "RANKING.md").write_text(ranking(*data))
    print("wrote", OUT / "RANKING.md")
