# Ranking of Radar's 29 setups (Lab, phase 2)

Written from runs made on 2026-11-18. Every number below comes from a run stored in `lab/results/phase2/`; figures marked ESTIMATE are arithmetic on those runs, not runs themselves.

## Outcome

- **71 of 87 setup-symbol pairs were killed** on the first 36 months by the pre-registered rules; 16 pairs (in 11 setups) were left standing.
- **The last 12 months were opened once, for 7 pairs** (the top three setups by score). 4 made money there, 3 did not.
- **Two setups survive everything. Neither is proven, and neither passes the buy-and-hold gate on every symbol it trades.**
- **No setup passes the owner's gate as written.** Both survivors beat ETH buy-and-hold over the full window only because ETH buy-and-hold lost 6.9% after funding; both trail SOL buy-and-hold by more than 100 points.

| Rank | Setup | Trade it on | Status | First 36 months | Last 12 months (unseen) | Full window | Max drawdown | Trades (full) |
|---|---|---|---|---|---|---|---|---|
| 1 | `tsmom` | ETHUSDT, SOLUSDT | candidate for paper trading, unproven | +38.0% | +12.0% | +53.6% | 10.3% | 254 |
| 2 | `week_range_breakout_volume` | ETHUSDT, SOLUSDT | candidate for paper trading, unproven | +25.0% | +10.0% | +39.6% | 4.3% | 140 |
| 3 | `oi_confirmed_breakout` | BTCUSDT, ETHUSDT | **paper only, not a candidate for live** | +33.7% | -0.8% | +33.3% | 6.5% | 145 |

Each row is the setup's book: the sum of its per-symbol runs, every trade risking 0.5% of a $1,000 account, after fees, slippage and funding, exchange minimum order sizes ON.

Only two setups are real candidates, so the third slot is not padded: `oi_confirmed_breakout` was first on the first-36-month score, then lost money on both symbols in the last 12 months, and it cannot run live because the live feed has no open interest.

## How this was run

- **Primary configuration:** exchange minimum order sizes ON (owner decision: the account really is $1,000). The size table comes from `bot/instruments.py` and is **unverified** (exchangeInfo is geo-blocked here). No-filter numbers are shown alongside and labelled.
- **Selection window:** 2022-11-01 to 2025-10-31 (first 36 months). All kill rules, scores and the choice of finalists used this window only.
- **Holdout:** 2025-11-01 to 2026-10-31, opened once on 2026-11-18 09:12 UTC for exactly 7 pairs. No other setup has been run on it.
- **Rules fixed beforehand:** kill thresholds, score bands, tie-breaks and the finalist protocol are in `lab/results/PREREG.md`, written before stage 1 ran. It also states what had already been seen (a phase-1 smoke run of net return, drawdown, Sharpe and trade count on the first 36 months).
- **Engine rules:** `lab/engine.py` R1-R12, including same-open reversal when a signal sits on the exit row.
- **Loader:** current `strategies/data.py` (open-interest prints <= 0 masked).

## 1. Kill table: 29 setups x 3 symbols, first 36 months

Each cell: the rule that killed the pair (first in the pre-registered order), the number of rules it failed in brackets, then net return and trade count with DEFAULT_PARAMS. **alive** = no rule fired.

| Code | Rule |
|---|---|
| DELAY | audit flag: profitable only when executed one bar late; no look-ahead leak found |
| N<30 | fewer than 30 trades in the first 36 months |
| NET | loses money after costs |
| PF | profit factor below 1.10 |
| STRESS | loses with fees x2 and slippage x2 |
| WF | walk-forward out-of-sample not profitable |
| GRID | fewer than 60% of nearby settings profitable |
| MC-LOSS | Monte Carlo chance of a loss above 20% |
| MC-DD | Monte Carlo 95th-percentile drawdown above 25% |

| Setup | Family | TF | BTCUSDT | ETHUSDT | SOLUSDT |
|---|---|---|---|---|---|
| `alt_btc_residual_catchup` | btc_lead_lag | 1h | N<30 (no trades) | NET(4) -1.0% / 119 | NET(6) -10.8% / 113 |
| `asia_range_london_breakout` | session_open | 1h | NET(7) -33.7% / 460 | NET(7) -28.2% / 484 | NET(7) -11.2% / 495 |
| `bollinger_mean_reversion` | baseline | 1h | NET(7) -61.2% / 934 | NET(6) -35.8% / 1009 | NET(7) -40.2% / 995 |
| `breakout_retest` | range_breakout_volume | 1h | NET(7) -16.4% / 332 | NET(7) -24.7% / 320 | NET(6) -0.2% / 360 |
| `btc_breakout_alt_lag` | btc_lead_lag | 1h | N<30 (no trades) | NET(6) -6.6% / 332 | NET(7) -19.0% / 375 |
| `btc_shock_alt_lag` | btc_lead_lag | 1h | N<30 (no trades) | DELAY(7) -2.6% / 98 | NET(6) -12.6% / 214 |
| `donchian_trend` | baseline | 4h | MC-LOSS(1) +18.1% / 96 | **alive** +13.8% / 94 | **alive** +16.7% / 91 |
| `funding_crowd_unwind` | funding_extremes | 1h | N<30(7) -2.6% / 8 | N<30(7) -2.7% / 8 | N<30(7) -5.7% / 18 |
| `funding_extreme_fade` | funding_extremes | 4h | N<30(2) +0.7% / 3 | N<30(4) +0.2% / 17 | N<30(7) -0.1% / 24 |
| `liq_flush_reclaim` | liquidation_flush_reversal | 1h | NET(6) -3.6% / 43 | NET(5) -1.0% / 40 | NET(6) -0.4% / 35 |
| `liq_flush_retest_bid` | liquidation_flush_reversal | 1h | NET(5) -2.7% / 48 | NET(6) -5.0% / 37 | N<30(3) +1.4% / 24 |
| `liq_flush_reversal` | liquidation_flush_reversal | 1h | NET(6) -3.7% / 70 | NET(6) -5.3% / 57 | NET(6) -3.8% / 51 |
| `london_open_fakeout_fade` | session_open | 1h | NET(7) -19.0% / 221 | NET(7) -31.5% / 232 | NET(7) -27.4% / 285 |
| `ma_trend_atr_stop` | baseline | 4h | **alive** +12.5% / 56 | MC-LOSS(1) +5.8% / 90 | WF(2) +6.9% / 78 |
| `negative_funding_uptrend` | funding_extremes | 4h | N<30(2) +1.0% / 6 | N<30(7) -0.8% / 5 | WF(1) +8.4% / 55 |
| `ny_open_drive` | session_open | 1h | NET(6) -13.0% / 193 | **alive** +9.5% / 157 | NET(6) -6.8% / 154 |
| `oi_buildup_range_break` | oi_divergence | 1h | MC-LOSS(1) +4.6% / 35 | WF(2) +3.9% / 41 | NET(5) -0.6% / 71 |
| `oi_confirmed_breakout` | oi_divergence | 4h | **alive** +10.2% / 56 | **alive** +23.5% / 67 | MC-LOSS(1) +4.9% / 71 |
| `oi_divergence_fade` | oi_divergence | 4h | N<30(7) -1.9% / 20 | **alive** +14.9% / 31 | NET(6) -3.9% / 61 |
| `oi_reset_trend` | oi_divergence | 4h | WF(2) +4.8% / 31 | WF(3) +3.0% / 39 | **alive** +45.5% / 46 |
| `pullback_limit_sma` | trend_pullback | 4h | NET(6) -5.6% / 165 | NET(6) -7.7% / 182 | NET(6) -0.1% / 162 |
| `range_breakout_volume` | range_breakout_volume | 1h | PF(4) +1.0% / 319 | MC-DD(1) +17.5% / 290 | **alive** +29.3% / 276 |
| `rsi2_trend_pullback` | trend_pullback | 4h | NET(6) -11.5% / 556 | NET(6) -14.0% / 832 | NET(6) -8.4% / 819 |
| `squeeze_blowoff_fade` | liquidation_flush_reversal | 1h | NET(6) -6.8% / 38 | N<30(7) -2.4% / 25 | N<30(7) -2.4% / 28 |
| `trend_pullback_ma` | trend_pullback | 4h | **alive** +46.6% / 70 | MC-LOSS(1) +10.1% / 72 | MC-LOSS(1) +24.0% / 74 |
| `tsmom` | baseline | 4h | **alive** +19.7% / 61 | **alive** +14.3% / 103 | **alive** +23.7% / 82 |
| `us_close_asia_fade` | session_open | 1h | NET(5) -14.7% / 84 | NET(6) -16.0% / 88 | NET(6) -5.7% / 75 |
| `vol_squeeze_breakout` | baseline | 4h | WF(2) +10.7% / 188 | NET(6) -12.5% / 194 | **alive** +13.1% / 171 |
| `week_range_breakout_volume` | range_breakout_volume | 4h | MC-LOSS(1) +6.1% / 44 | **alive** +15.6% / 54 | **alive** +9.4% / 45 |

Killed by (first rule that fired):

- 40 pairs: loses money after costs
- 15 pairs: fewer than 30 trades in the first 36 months
- 7 pairs: Monte Carlo chance of a loss above 20%
- 6 pairs: walk-forward out-of-sample not profitable
- 1 pairs: Monte Carlo 95th-percentile drawdown above 25%
- 1 pairs: audit flag: better when executed one bar late (no future leak found)
- 1 pairs: profit factor below 1.10

A pair usually fails several rules at once. Pairs failing each rule: DELAY 1, N<30 15, NET 52, PF 54, STRESS 53, WF 52, GRID 52, MC-LOSS 68, MC-DD 20.

No pair has a look-ahead leak: the prefix check was clean on all 87 (20 cut points each). The one DELAY flag (`btc_shock_alt_lag` on ETH) is a pair that lost 2.6% on time and made 2.7% one bar late; it fails six other rules.

What died as a family: every session-open setup, every BTC-lead/alt-lag setup, every liquidation-flush setup, both mean-reversion setups and all three funding setups (the funding ones mostly for lack of trades). What is left is almost entirely slow trend or breakout following on 4h bars.

### Where the exchange minimums change the picture (no-filter numbers, labelled)

Pairs whose trade count moves by more than 10% when minimum order sizes are switched off. All are BTC: on $1,000 a 0.5% risk order is often below the BTC minimum.

| Pair | With minimums (primary): net / trades | Orders refused as too small | WITHOUT minimums: net / trades |
|---|---|---|---|
| `bollinger_mean_reversion` BTCUSDT | -61.2% / 934 | 151 | -63.7% / 1050 |
| `funding_extreme_fade` BTCUSDT | +0.7% / 3 | 12 | +0.1% / 12 |
| `liq_flush_reclaim` BTCUSDT | -3.6% / 43 | 8 | -3.6% / 51 |
| `ma_trend_atr_stop` BTCUSDT | +12.5% / 56 | 27 | +18.7% / 83 |
| `negative_funding_uptrend` BTCUSDT | +1.0% / 6 | 3 | +0.4% / 9 |
| `oi_divergence_fade` BTCUSDT | -1.9% / 20 | 8 | -2.9% / 28 |
| `oi_reset_trend` BTCUSDT | +4.8% / 31 | 12 | +8.1% / 36 |
| `pullback_limit_sma` BTCUSDT | -5.6% / 165 | 545 | -3.2% / 186 |
| `rsi2_trend_pullback` BTCUSDT | -11.5% / 556 | 312 | -16.3% / 840 |
| `tsmom` BTCUSDT | +19.7% / 61 | 313 | +20.3% / 105 |
| `week_range_breakout_volume` BTCUSDT | +6.1% / 44 | 13 | +10.4% / 54 |

No-filter net return and trade count for all 87 pairs are in `lab/results/kill_table.csv` (columns `nofilter_net`, `nofilter_trades`). The kill rules themselves were run with minimums ON only.

## 2. Survivors and scores

A setup is scored as the book that trades it on every symbol where it survived section 1. Score bands were fixed in advance:

| Score | Out-of-sample profit per year | Max drawdown | Trades (36 months, pooled) | Longest losing streak | Bear (b) and sideways (s) P&L |
|---|---|---|---|---|---|
| 5 | > 10% | < 5% | >= 200 | <= 4 | b > 0 and s > 0 |
| 4 | 5-10% | 5-10% | 100-199 | 5-6 | b + s > 0 and the worse one >= -3% |
| 3 | 2-5% | 10-15% | 50-99 | 7-9 | b + s > 0 and the worse one < -3% |
| 2 | 0-2% | 15-25% | 30-49 (thin) | 10-14 | -5% <= b + s <= 0 |
| 1 | <= 0% | >= 25% | < 30 | >= 15 | b + s < -5% |

| # | Setup | Symbols | Trades | Net | Max DD | Sharpe | Losing streak | OOS/yr | Bear | Sideways | Scores (oos, dd, trades, streak, regimes) | Total | Haircut p (n=29) | Note |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `oi_confirmed_breakout` | BTC, ETH | 123 | +33.7% | 6.5% | 1.31 | 9 | +13.8% | +1.4% | +25.2% | 5, 4, 4, 3, 5 | **21** | 0.33 |  |
| 2 | `tsmom` | BTC, ETH, SOL | 246 | +57.7% | 10.0% | 1.17 | 12 | +13.3% | +3.4% | +18.9% | 5, 4, 5, 2, 5 | **21** | 0.62 |  |
| 3 | `oi_divergence_fade` | ETH | 31 | +14.9% | 3.4% | 1.35 | 4 | +4.7% | +6.8% | +3.4% | 3, 5, 2, 5, 5 | **20** | 0.28 | thin (under 50 trades) |
| 4 | `week_range_breakout_volume` | ETH, SOL | 99 | +25.0% | 4.3% | 1.14 | 7 | +7.8% | +6.2% | +14.1% | 4, 5, 3, 3, 5 | **20** | 0.70 |  |
| 5 | `range_breakout_volume` | SOL | 276 | +29.3% | 9.5% | 0.92 | 12 | +4.1% | +2.3% | +16.0% | 3, 4, 5, 2, 5 | **19** | 1.00 |  |
| 6 | `ny_open_drive` | ETH | 157 | +9.5% | 4.5% | 0.61 | 9 | +8.4% | -1.1% | -3.0% | 4, 5, 4, 3, 2 | **18** | 1.00 |  |
| 7 | `vol_squeeze_breakout` | SOL | 171 | +13.1% | 7.9% | 0.63 | 10 | +5.9% | +5.0% | -3.8% | 4, 4, 4, 2, 3 | **17** | 1.00 |  |
| 8 | `ma_trend_atr_stop` | BTC | 56 | +12.5% | 5.4% | 0.71 | 9 | +2.4% | -2.8% | +12.1% | 3, 4, 3, 3, 4 | **17** | 1.00 |  |
| 9 | `trend_pullback_ma` | BTC | 70 | +46.6% | 8.5% | 1.19 | 13 | +13.1% | -3.7% | +24.0% | 5, 4, 3, 2, 3 | **17** | 0.57 |  |
| 10 | `oi_reset_trend` | SOL | 46 | +45.5% | 12.8% | 1.18 | 11 | +5.5% | +11.3% | +5.9% | 4, 3, 2, 2, 5 | **16** | 0.61 | thin (under 50 trades) |
| 11 | `donchian_trend` | ETH, SOL | 185 | +30.5% | 15.6% | 0.84 | 18 | +6.2% | +6.7% | -0.4% | 4, 2, 4, 1, 4 | **15** | 1.00 |  |

All columns are first-36-month numbers. OOS/yr is the walk-forward out-of-sample profit (12 months to choose parameters, next 3 months traded, rolled over 8 quarters), summed over the setup's symbols.

**Multiple testing.** With 29 setups tested at 5%: expect 1.5 to look good by chance (P(at least one) = 77%); the best of 29 zero-edge backtests is expected to show Sharpe ~1.19 over 3.0y. Bar to clear: Sharpe > 1.69 (Bonferroni) instead of 0.95.

- At the usual 5% level after the Bonferroni correction, **0 of 11 surviving setups are significant**. The last column is the corrected p-value; all are far above 0.05.
- At pair level (87 tests) none of the 16 surviving pairs clears the haircut either.
- The pre-registered bar was weaker (corrected p below 0.5). 2 setups clear it: `oi_confirmed_breakout`, `oi_divergence_fade`. That decided one tie-break and nothing else. Every setup in this report should be read as **unproven**.

Tie at 21 points between `oi_confirmed_breakout` and `tsmom` was broken by the pre-registered order (clears the haircut bar first), which put `oi_confirmed_breakout` first before the holdout. `oi_divergence_fade` (20 points) and `oi_reset_trend` (16) have under 50 trades and cannot be ranked in the top three.

## 3. Final gate: the last 12 months

Finalists were fixed before opening: the top three eligible setups, 7 pairs, DEFAULT_PARAMS (no walk-forward grid point was chosen in at least half the folds, so the rule to switch parameters did not apply). A pair must make money on the unseen year; no replacement was drawn for those that failed.

| Pair | First 36 months | Last 12 months (primary) | Trades | Buy-and-hold, same 12 months | Last 12 months WITHOUT minimums | Verdict |
|---|---|---|---|---|---|---|
| `oi_confirmed_breakout` BTCUSDT | +10.2% | **-0.74%** | 7 | -31.8% | +3.89% (10 trades) | **killed** |
| `oi_confirmed_breakout` ETHUSDT | +23.5% | **-0.01%** | 15 | -43.3% | +0.04% (15 trades) | **killed** |
| `tsmom` BTCUSDT | +19.7% | **-1.80%** | 21 | -31.8% | +0.43% (43 trades) | **killed** |
| `tsmom` ETHUSDT | +14.3% | **+9.39%** | 34 | -43.3% | +9.52% (34 trades) | survives |
| `tsmom` SOLUSDT | +23.7% | **+2.59%** | 37 | -48.9% | +2.63% (37 trades) | survives |
| `week_range_breakout_volume` ETHUSDT | +15.6% | **+2.13%** | 22 | -43.3% | +2.18% (22 trades) | survives |
| `week_range_breakout_volume` SOLUSDT | +9.4% | **+7.91%** | 19 | -48.9% | +7.95% (19 trades) | survives |

- `oi_confirmed_breakout` is killed on both symbols. ETH was flat (-0.01%); the rule says it must make money.
- `tsmom` loses its BTC leg (-1.80%) and keeps ETH and SOL.
- Without the exchange minimums all three killed pairs would have scraped through (+3.89%, +0.04%, +0.43%). On BTC the minimums refuse most small orders, so the primary and no-filter BTC results are different sets of trades. The verdicts above use the primary configuration.
- The unseen year was a bear market (buy-and-hold lost 32% to 49%), so it tests the short side and the stops more than the long side.

## 4. Top three

### 1. `tsmom` on ETHUSDT, SOLUSDT - candidate for paper trading, unproven

Once a day, hold the sign of the trailing 30-day return; 4-ATR disaster stop. Card: `lab/results/cards/1_tsmom.md`.

| | First 36 months | Last 12 months | Full window |
|---|---|---|---|
| Net return (book) | +38.0% | +12.0% | +53.6% |
| Max drawdown | 10.3% | 7.2% | 10.3% |
| Trades | 185 | 71 | 254 |
| Win rate | 33% | 32% | 32% |
| Longest losing streak | 8 | 15 | 15 |
| Worst month | -4.8% (2024-01) | -4.2% (2026-10) | -4.8% (2024-01) |
| Equal-weight buy-and-hold of the same symbols | +257.6% | -46.1% | +64.3% |

### 2. `week_range_breakout_volume` on ETHUSDT, SOLUSDT - candidate for paper trading, unproven

Close outside a tight 7-day range while the last day's volume runs 30% above the prior week: go with it. Card: `lab/results/cards/2_week_range_breakout_volume.md`.

| | First 36 months | Last 12 months | Full window |
|---|---|---|---|
| Net return (book) | +25.0% | +10.0% | +39.6% |
| Max drawdown | 4.3% | 3.8% | 4.3% |
| Trades | 99 | 41 | 140 |
| Win rate | 44% | 37% | 44% |
| Longest losing streak | 7 | 5 | 7 |
| Worst month | -1.8% (2025-08) | -1.0% (2026-10) | -1.8% (2025-08) |
| Equal-weight buy-and-hold of the same symbols | +257.6% | -46.1% | +64.3% |

### 3. `oi_confirmed_breakout` on BTCUSDT, ETHUSDT - paper only, not a candidate for live

Go with a new 30-bar extreme only when open interest has risen 5%+ over the same window. Card: `lab/results/cards/3_oi_confirmed_breakout.md`.

| | First 36 months | Last 12 months | Full window |
|---|---|---|---|
| Net return (book) | +33.7% | -0.8% | +33.3% |
| Max drawdown | 6.5% | 4.7% | 6.5% |
| Trades | 123 | 22 | 145 |
| Win rate | 37% | 41% | 38% |
| Longest losing streak | 9 | 4 | 9 |
| Worst month | -2.3% (2023-05) | -1.2% (2025-11) | -2.3% (2023-05) |
| Equal-weight buy-and-hold of the same symbols | +229.7% | -37.6% | +91.9% |

### Final check (per symbol; yes/no with the number)

| Question | `tsmom` BTC | `tsmom` ETH | `tsmom` SOL | `week_range_breakout_volume` ETH | `week_range_breakout_volume` SOL | `oi_confirmed_breakout` BTC | `oi_confirmed_breakout` ETH |
|---|---|---|---|---|---|---|---|
| Still wins on unseen data (last 12 months)? | NO -1.80%, 21 trades | YES +9.39%, 34 trades | YES +2.59%, 37 trades | YES +2.13%, 22 trades | YES +7.91%, 19 trades | NO -0.74%, 7 trades | NO -0.01%, 15 trades |
| After fees and funding (full window)? | YES +17.0% | YES +25.2% | YES +28.4% | YES +19.9% | YES +19.7% | YES +9.8% | YES +23.6% |
| Over 50+ trades (first 36 months)? | YES 61 | YES 103 | YES 82 | YES 54 | NO 45 | YES 56 | YES 67 |
| With double fees (first 36 months)? | YES +19.0% | YES +13.3% | YES +23.0% | YES +14.8% | YES +9.0% | YES +9.3% | YES +22.3% |
| Fees and slippage both doubled? | YES +18.8% | YES +13.1% | YES +22.8% | YES +14.5% | YES +8.7% | YES +9.0% | YES +22.0% |
| Across nearby settings? | YES 8 of 9 profitable, worst -0.6% | NO 6 of 9 profitable, worst -4.1% | YES 9 of 9 profitable, worst +4.2% | YES 27 of 27 profitable, worst +0.8% | YES 27 of 27 profitable, worst +0.4% | YES 26 of 27 profitable, worst -1.3% | YES 27 of 27 profitable, worst +5.2% |
| Any leak from the future? | NO (36 cut points, 0 rows differ) | NO (36 cut points, 0 rows differ) | NO (36 cut points, 0 rows differ) | NO (36 cut points, 0 rows differ) | NO (36 cut points, 0 rows differ) | NO (36 cut points, 0 rows differ) | NO (36 cut points, 0 rows differ) |
| Better than same-side random entries? (p, first 36 months) | p = 0.02 (random: +3.1%) | p = 0.05 (random: +1.9%) | p = 0.05 (random: +3.5%) | p = 0.02 (random: +1.9%) | p = 0.04 (random: +1.6%) | p = 0.12 (random: +3.2%) | p = 0.02 (random: +2.0%) |
| Survives the multiple-testing haircut (87 pairs)? | NO (corrected p 1.00) | NO (corrected p 1.00) | NO (corrected p 1.00) | NO (corrected p 1.00) | NO (corrected p 1.00) | NO (corrected p 1.00) | NO (corrected p 1.00) |

- 'Nearby settings' counts YES at 80% of the grid or more. `tsmom` on ETH loses with the 20-day lookback at every stop width and wins with 30 and 60 days.
- 'Over 50+ trades' is per symbol here; pooled over the symbols each setup keeps, `tsmom` has 185 and `week_range_breakout_volume` 99 in the first 36 months, which is what the owner's 50-trade rule asks for.
- The random-entry line moves every real trade to a random day (same hour, same side, same stop distance in ATR, same holding time) 300 times. It is not corrected for the 87 tests and is not a kill rule.
- Executing the same 4h signals on 1h bars instead of 4h bars changes no full-window result by more than 0.02 points.

## 5. Buy-and-hold gate

Gate (owner): the setup's net return must beat the same symbol's 1x buy-and-hold net return over the full window (2022-11-01 to 2026-10-31), on every symbol it trades. Buy-and-hold pays the entry fee and real funding.

| Pair | Setup, full window | Buy-and-hold, full window | Beats it? | In bear bars: setup / B&H | In sideways bars: setup / B&H | Last 12 months: setup / B&H |
|---|---|---|---|---|---|---|
| `tsmom` BTCUSDT | +17.0% | +190.7% | **NO** | -2.5% / +42.0% | +10.1% / -20.0% | -1.8% / -31.8% |
| `tsmom` ETHUSDT | +25.2% | -6.9% | **YES** | +0.1% / -23.8% | +17.5% / -18.1% | +9.4% / -43.3% |
| `tsmom` SOLUSDT | +28.4% | +135.5% | **NO** | -0.3% / -36.1% | +6.3% / +67.9% | +2.6% / -48.9% |
| `week_range_breakout_volume` ETHUSDT | +19.9% | -6.9% | **YES** | +4.0% / -23.8% | +15.7% / -18.1% | +2.1% / -43.3% |
| `week_range_breakout_volume` SOLUSDT | +19.7% | +135.5% | **NO** | +4.3% / -36.1% | +10.4% / +67.9% | +7.9% / -48.9% |
| `oi_confirmed_breakout` BTCUSDT | +9.8% | +190.7% | **NO** | -2.0% / +42.0% | +9.7% / -20.0% | -0.7% / -31.8% |
| `oi_confirmed_breakout` ETHUSDT | +23.6% | -6.9% | **YES** | +0.7% / -23.8% | +18.0% / -18.1% | -0.0% / -43.3% |

**Result: no setup passes the gate.** `tsmom` and `week_range_breakout_volume` pass on ETH and fail on SOL; `oi_confirmed_breakout` passes on ETH and fails on BTC.

- The ETH passes say more about ETH than about the setups: 1x ETH buy-and-hold returned -6.9% because funding ate the price gain.
- Regime labels are trailing (90-day return and 200-day mean, known at each bar's open), so 'bear bars' include the rebounds that follow a fall. That is why BTC buy-and-hold shows a gain inside bear-labelled bars.
- In the unseen year every pair lost less than buy-and-hold, which fell 32% to 49%.

### Risk-adjusted comparison (reported separately; it does not replace the gate)

| Pair | Sharpe: setup / B&H | Return / max drawdown: setup / B&H | Max drawdown: setup / B&H | Return if scaled to B&H's drawdown (ESTIMATE) | B&H return |
|---|---|---|---|---|---|
| `tsmom` BTCUSDT | 0.70 / 0.77 | 2.7 / 3.1 | 6.2% / 62.1% | +169% | +190.7% |
| `tsmom` ETHUSDT | 0.94 / 0.38 | 4.8 / -0.1 | 5.3% / 80.9% | +385% | -6.9% |
| `tsmom` SOLUSDT | 0.84 / 0.70 | 3.0 / 1.6 | 9.4% / 83.8% | +253% | +135.5% |
| `week_range_breakout_volume` ETHUSDT | 1.01 / 0.38 | 5.3 / -0.1 | 3.8% / 80.9% | +428% | -6.9% |
| `week_range_breakout_volume` SOLUSDT | 1.12 / 0.70 | 4.7 / 1.6 | 4.2% / 83.8% | +397% | +135.5% |
| `oi_confirmed_breakout` BTCUSDT | 0.63 / 0.77 | 2.3 / 3.1 | 4.2% / 62.1% | +143% | +190.7% |
| `oi_confirmed_breakout` ETHUSDT | 1.04 / 0.38 | 5.3 / -0.1 | 4.4% / 80.9% | +430% | -6.9% |

The ESTIMATE column multiplies the setup's return by (buy-and-hold drawdown / setup drawdown). It is a ratio, not a forecast: reaching it would need roughly 9 to 20 times the risk per trade, which the 3x leverage cap, compounding and worse fills at size would not allow. Read it as 'per unit of drawdown the setups did better', nothing more.

## 6. Portfolio pass: one shared $1,000 account

All streams trade one account on 1h execution bars: every order is sized at 0.5% of the account's realised equity, an order is refused if gross exposure would pass 3x equity, exchange minimums ON.

Shield as simulated:

- **Daily loss:** equity marked to market at an hourly close is 3% or more below the start of that UTC day.
- **Loss streak:** 3 losing trades in a row, account-wide.
- **On a halt:** resting orders are cancelled, positions are closed at the next hourly open, and there are no entries for the rest of that UTC day.
- **ASSUMPTION:** trading resumes at 00:00 UTC the next day for both rules. The real Shield waits for a human after a loss streak.

### A. The finalists as chosen before the holdout: 7 streams

This is the clean test for the unseen year, because the 7 streams were fixed before it was opened.

| Window, Shield | Net return | Max drawdown | Sharpe | Trades | Worst month | Longest losing streak | Halts |
|---|---|---|---|---|---|---|---|
| First 36 months, no Shield | +167.1% | 13.8% | 1.55 | 496 | -5.6% | 18 | 0 (0 daily, 0 streak) |
| First 36 months, Shield | +119.1% | 12.5% | 1.44 | 648 | -5.6% | 12 | 84 (6 daily, 78 streak) |
| Last 12 months, no Shield | +21.6% | 11.3% | 1.09 | 154 | -5.0% | 19 | 0 (0 daily, 0 streak) |
| Last 12 months, Shield | +10.1% | 11.2% | 0.74 | 178 | -4.5% | 15 | 20 (1 daily, 19 streak) |
| Full window, no Shield | +249.6% | 13.8% | 1.50 | 673 | -5.6% | 23 | 0 (0 daily, 0 streak) |
| Full window, Shield | +149.3% | 14.1% | 1.32 | 858 | -5.6% | 23 | 111 (6 daily, 105 streak) |
| Full window, no Shield, WITHOUT exchange minimums | +256.6% | 13.4% | 1.50 | 695 | -6.0% | 23 | 0 (0 daily, 0 streak) |
| Full window, Shield, WITHOUT exchange minimums | +150.7% | 14.7% | 1.30 | 900 | -5.3% | 23 | 114 (6 daily, 108 streak) |

### B. What is left after the gate: 4 streams (`tsmom` and `week_range_breakout_volume` on ETH and SOL)

This is the set proposed for paper trading. Its last-12-months line is flattered: these 4 were kept *because* they made money in that year.

| Window, Shield | Net return | Max drawdown | Sharpe | Trades | Worst month | Longest losing streak | Halts |
|---|---|---|---|---|---|---|---|
| First 36 months, no Shield | +76.9% | 11.7% | 1.34 | 284 | -4.8% | 11 | 0 (0 daily, 0 streak) |
| First 36 months, Shield | +57.9% | 9.3% | 1.23 | 329 | -4.8% | 10 | 42 (2 daily, 40 streak) |
| Last 12 months, no Shield | +22.1% | 9.3% | 1.29 | 112 | -4.9% | 17 | 0 (0 daily, 0 streak) |
| Last 12 months, Shield | +23.3% | 8.2% | 1.36 | 123 | -4.5% | 19 | 17 (0 daily, 17 streak) |
| Full window, no Shield | +125.1% | 11.7% | 1.38 | 394 | -4.9% | 17 | 0 (0 daily, 0 streak) |
| Full window, Shield | +102.9% | 9.3% | 1.32 | 450 | -4.8% | 19 | 59 (2 daily, 57 streak) |
| Full window, no Shield, WITHOUT exchange minimums | +125.2% | 11.8% | 1.37 | 396 | -4.9% | 17 | 0 (0 daily, 0 streak) |
| Full window, Shield, WITHOUT exchange minimums | +102.7% | 9.3% | 1.31 | 450 | -4.9% | 19 | 59 (2 daily, 57 streak) |

| Buy-and-hold for comparison | First 36 months | Last 12 months | Full window | Max drawdown (full) |
|---|---|---|---|---|
| BTCUSDT | +360.4% | -31.8% | +190.7% | 62.4% |
| ETHUSDT | +99.0% | -43.3% | -6.9% | 81.9% |
| SOLUSDT | +416.2% | -48.9% | +135.4% | 84.1% |
| Equal weight of the three | +291.9% | -41.3% | +106.4% | 73.1% |

What the portfolio pass shows:

- **Unseen year, 7 streams:** +21.6% without the Shield, +10.1% with it, max drawdown about 11%, while equal-weight buy-and-hold lost 41%. This is the most trustworthy line in the report, and it is one year.
- **Stacking streams is what makes the account-level number large.** Seven streams at 0.5% risk each is roughly seven times the risk of one. The full-window figures (+249.6% / +149.3% for A, +125.1% / +102.9% for B) include the 36 months on which these setups were picked as the best of 29, so they overstate what to expect. They are not the owner's gate, which is per setup and per symbol.
- **The Shield's loss-streak rule fires constantly on trend setups.** 105 loss-streak halts in four years on A (57 on B), against 6 daily-loss halts. A win rate near 35% makes three losses in a row routine. Each halt closes open trend trades at market.
- **Cost of the Shield in this backtest:** full-window return falls from +249.6% to +149.3% on A and from +125.1% to +102.9% on B; drawdown is about the same on A (13.8% vs 14.1%) and lower on B (11.7% vs 9.3%). With a human needed to clear each loss-streak halt, the real cost would be larger than simulated.
- **The gross 3x cap never bound** (0 orders refused for it). The -3% day is rare at this risk level.

### Each setup on its own $1,000 book (how the bot is configured today)

| Book | Window | Net, no Shield | Net, Shield | Max drawdown, Shield | Halts (Shield) |
|---|---|---|---|---|---|
| `tsmom` ETH+SOL | First 36 months | +41.3% | +32.1% | 7.3% | 32 (32 streak) |
| `tsmom` ETH+SOL | Last 12 months | +11.9% | +12.7% | 7.2% | 12 (11 streak) |
| `tsmom` ETH+SOL | Full window | +60.1% | +50.8% | 7.3% | 44 (43 streak) |
| `week_range_breakout_volume` ETH+SOL | First 36 months | +26.4% | +27.1% | 4.9% | 6 (6 streak) |
| `week_range_breakout_volume` ETH+SOL | Last 12 months | +10.0% | +10.4% | 3.6% | 3 (3 streak) |
| `week_range_breakout_volume` ETH+SOL | Full window | +43.1% | +44.4% | 4.9% | 9 (9 streak) |
| `oi_confirmed_breakout` BTC+ETH | First 36 months | +35.1% | +35.6% | 7.0% | 15 (15 streak) |
| `oi_confirmed_breakout` BTC+ETH | Last 12 months | -0.6% | +0.2% | 3.9% | 3 (3 streak) |
| `oi_confirmed_breakout` BTC+ETH | Full window | +38.9% | +40.3% | 7.0% | 17 (17 streak) |

`tsmom` alone would trip the three-losses halt 43 times in four years (about 11 a year). In the real bot each one stops that book until a person clears it.

## 7. What the evidence does and does not support

**It supports this much.** Under realistic costs, 71 of 87 setup-symbol pairs fail outright, and whole families (session, lead-lag, liquidation flush, mean reversion) have nothing in them at these timeframes and fees. Slow trend and breakout following on 4h bars made modest money in the first 36 months on ETH and SOL (roughly 4% to 8% a year per symbol at 0.5% risk, drawdowns of 4% to 10%). Two such setups also made money in a year they had never seen, a bear market, while buy-and-hold lost 43% to 49%. There is no look-ahead leak in any of them, they survive doubled fees and slippage, and they do better than same-side random entries.

**It does not support this.**

- **That the edge is real.** No pair and no setup is statistically significant once 87 (or 29) tests are accounted for. The unseen year is one more data point, about 20 to 37 trades per pair.
- **That the survivors are different bets.** They are close cousins: trend or breakout following on the same three correlated coins. If crypto stops trending they fail together.
- **That the profit is spread out.** One long trade (2023-10-01 to 2024-01-21, +43R) is 77% of `tsmom`'s four-year profit on SOL; without its three best trades that leg loses money. On ETH the best trade is 33%. `week_range_breakout_volume` is steadier (best trade 19% to 25%) but has only 64 to 76 trades per symbol in four years.
- **That anything beats buy-and-hold.** The gate fails. The ETH passes come from ETH buy-and-hold losing money.
- **The account-level totals as a forecast.** They stack streams chosen with hindsight on 36 of the 48 months.
- **Live fills.** Lot sizes and minimums are unverified, slippage is a fixed 1 to 2 basis points, and the backtest assumes every stop fills at its price plus that slippage.

**Plain reading:** paper-trade the two survivors to collect evidence. Nothing here justifies real money yet, and by the owner's own rule nothing goes live.

## 8. Before paper trading starts

- **Open interest is not on the live feed.** `oi_confirmed_breakout` reads `oi`; the bot marks live OI as unknown and refuses to trade a setup whose required column is unknown. It cannot run until OI is fed live. The two candidates need only price and volume (`high`, `low`, `close`; plus `volume` for the breakout).
- **Funding history stops at 2026-10-31.** Neither candidate reads funding for its signals; it only affects P&L accounting, which the bot takes from the live funding stream.
- **Warm-up:** `tsmom` 400 four-hour bars (67 days), `week_range_breakout_volume` 130 (22 days).
- **Exchange minimums are unverified.** With the assumed table, `tsmom` on ETH had 3 orders refused in four years and its smallest notional was about $23 against an assumed $20 minimum. If the real ETH minimum is higher, a noticeable share of `tsmom` ETH orders will be refused. SOL is unaffected.
- **BTC is not traded** by either candidate: `tsmom` BTC failed the unseen year, and on $1,000 most BTC orders are below the minimum anyway.
- **Shield versus trend following:** expect a three-losses halt on `tsmom`'s book about once a month. Decide who clears it and how fast, or the paper results will mostly measure halts.
- **Same-open reversal (engine R7):** `tsmom` exits and enters the opposite side at the same open when the 30-day sign flips. The bot must do the same or it will lag the backtest by a day on every flip.
- **Parameters:** DEFAULT_PARAMS for all three (`lab/results/top3.json`).

## Files

- `lab/results/PREREG.md` - rules, bands and protocol fixed before the runs
- `lab/results/kill_table.csv`, `lab/results/setups.csv` - every pair and every surviving setup
- `lab/results/cards/` - one card per top-three setup; `lab/results/top3.json` - machine-readable
- `lab/results/phase2/` - stored runs (`final.json` holds every number quoted here)
- Reproduce: `python lab/phase2.py prereg | stage1 | rank | stage2 | final`, then `python lab/phase2_report.py` (`stage2` refuses to run twice)

Survivorship: BTCUSDT, ETHUSDT and SOLUSDT were chosen today, knowing all three are still listed and liquid; none was delisted in the window. Results on names picked with hindsight flatter any long-biased setup (SOL traded below $8 in late 2022 and survived; many peers did not) and say nothing about symbols that died.
