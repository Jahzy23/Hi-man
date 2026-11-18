# Phase 2 pre-registration

Written 2026-11-18 09:04 UTC, before stage 1 was run.

## Kill rules (pair level, fixed in phase 1)

- `min_trades_dev` = 30
- `min_dev_net_return` = 0.0
- `min_profit_factor` = 1.1
- `min_stress_net_return` = 0.0
- `min_wf_oos_net_return` = 0.0
- `min_share_profitable` = 0.6
- `max_mc_prob_loss` = 0.2
- `max_mc_dd95` = 0.25
- `min_haircut_sharpe` = 0.0
- `min_holdout_net_return` = 0.0

Order in which a failed pair is attributed to a rule:

1. look-ahead audit failed
2. fewer than 30 trades in the first 36 months
3. loses money after costs
4. profit factor below 1.10
5. loses with fees x2 and slippage x2
6. walk-forward out-of-sample not profitable
7. fewer than 60% of nearby settings profitable
8. Monte Carlo chance of a loss above 20%
9. Monte Carlo 95th-percentile drawdown above 25%
10. loses money in the last 12 months (finalists only)

## Score bands (setup level, 1 = worst, 5 = best)

**oos_profit** - walk-forward out-of-sample net profit of the book, % per year (sum of the per-symbol walk-forward runs, 2023-11 to 2025-10)

- 1: <= 0%
- 2: 0-2%
- 3: 2-5%
- 4: 5-10%
- 5: > 10%

**max_dd** - max drawdown of the book over the first 36 months, DEFAULT_PARAMS

- 5: < 5%
- 4: 5-10%
- 3: 10-15%
- 2: 15-25%
- 1: >= 25%

**trades** - trades over the first 36 months, pooled across the symbols it survives on

- 1: < 30
- 2: 30-49 (thin)
- 3: 50-99
- 4: 100-199
- 5: >= 200

**lose_streak** - longest run of losing trades in the book (trades ordered by exit time)

- 5: <= 4
- 4: 5-6
- 3: 7-9
- 2: 10-14
- 1: >= 15

**bear_sideways** - book P&L earned in bear-labelled bars (b) and in sideways-labelled bars (s), % of $1,000, first 36 months; each symbol uses its own no-look-ahead regime label

- 5: b > 0 and s > 0
- 4: b + s > 0 and the worse one >= -3%
- 3: b + s > 0 and the worse one < -3%
- 2: -5% <= b + s <= 0
- 1: b + s < -5%

## Protocol

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

## Disclosure

What had already been seen when these rules were written: the phase-1 smoke run of run_all on the first
36 months (old loader, no exchange minimums), which printed net return, max drawdown, Sharpe and trade
count per pair for DEFAULT_PARAMS. No walk-forward, stress, grid, Monte Carlo, regime or holdout result
of any Radar setup had been produced. The kill thresholds were fixed in phase 1 before that smoke run.
The score bands are round numbers tied to the 0.5% risk budget (10R = 5% of equity), not to that output.

## Exchange minimum sizes used by the primary runs (UNVERIFIED, from bot/instruments.py)

| symbol | lot step | min quantity | min notional |
|---|---|---|---|
| BTCUSDT | 0.001 | 0.001 | $100 |
| ETHUSDT | 0.001 | 0.001 | $20 |
| SOLUSDT | 0.01 | 0.01 | $5 |
