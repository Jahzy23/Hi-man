# Forward log: finalists on data after the backtest window (never used for selection)

Each run starts flat at the window start with a fresh $1,000 book, 0.5% risk, exchange minimums ON (unverified
table). Open positions at the window end are marked out at the last close with taker costs.

## Run 2026-11-21 03:50 UTC: window 2026-10-31 16:00 to 2026-11-19 23:00 UTC (about 19 days)

Funding caveat: the funding archive ends 2026-10-31 16:00, so funding in this window is counted as **zero**.
All open positions here are shorts; real funding could add or subtract a little. Treat these as ESTIMATES.

| setup | symbol | trades | net | open at end | price change, same window |
|---|---|---|---|---|---|
| tsmom (rank 1) | ETHUSDT | 2 (1 stopped short, 1 open short) | +1.42% | yes | -21.9% |
| tsmom (rank 1) | SOLUSDT | 1 (open short) | +1.38% | yes | -23.8% |
| week_range_breakout_volume (rank 2) | ETHUSDT | 0 | 0.00% | no | -21.9% |
| week_range_breakout_volume (rank 2) | SOLUSDT | 0 | 0.00% | no | -23.8% |
| oi_confirmed_breakout (rank 3) | BTCUSDT | 1 (open short) | +1.38% | yes | -20.1% |
| oi_confirmed_breakout (rank 3) | ETHUSDT | 1 (open short) | +0.04% | yes | -21.9% |

Reading: 5 trades in total, far too few to judge any edge. The momentum shorts caught part of the fall but at
0.5% risk the gains are small. The breakout setup found no qualifying week. Nothing here changes the ranking.

## Run 2026-11-22 03:50 UTC: window 2026-10-31 16:00 to 2026-11-20 23:00 UTC (about 20 days)

Same rules and the same funding caveat: funding after 2026-10-31 16:00 is counted as zero. ESTIMATES.

| setup | symbol | trades | net | open at end | price change, same window |
|---|---|---|---|---|---|
| tsmom (rank 1) | ETHUSDT | 2 | +1.81% | yes | -27.9% |
| tsmom (rank 1) | SOLUSDT | 1 | +1.64% | yes | -28.2% |
| week_range_breakout_volume (rank 2) | ETHUSDT | 0 | 0.00% | no | -27.9% |
| week_range_breakout_volume (rank 2) | SOLUSDT | 0 | 0.00% | no | -28.2% |
| oi_confirmed_breakout (rank 3) | BTCUSDT | 1 | +1.35% | no (closed) | -24.0% |
| oi_confirmed_breakout (rank 3) | ETHUSDT | 1 | +0.65% | yes | -27.9% |

Reading: still 5 trades in total; all profitable shorts so far, as expected for trend setups in a falling market.
Too few to judge. No change to the ranking.

## Run 2026-11-23 03:50 UTC: window 2026-10-31 16:00 to the 4h bar opening 2026-11-21 20:00 UTC (about 21 days)

Same rules; now scripted as `python3 -m lab.forward`. Funding after 2026-10-31 16:00 counted as zero (ESTIMATES).

| setup | symbol | trades | net | price change, same window |
|---|---|---|---|---|
| tsmom (rank 1) | ETHUSDT | 2 | +1.63% | -25.2% |
| tsmom (rank 1) | SOLUSDT | 1 | +1.55% | -26.6% |
| week_range_breakout_volume (rank 2) | ETHUSDT | 0 | 0.00% | -25.2% |
| week_range_breakout_volume (rank 2) | SOLUSDT | 1 | -0.51% | -26.6% |
| oi_confirmed_breakout (rank 3) | BTCUSDT | 1 | +1.35% | -22.4% |
| oi_confirmed_breakout (rank 3) | ETHUSDT | 1 | +0.37% | -25.2% |

Reading: 6 trades in total. The 21 Nov rebound (about +9% off the lows) took back some of the open short gains
(tsmom ETH +1.81% -> +1.63%). Rank 2 took its first forward trade, a small loss on SOL. Still far too few to judge.

## Run 2026-11-24 03:50 UTC: window 2026-10-31 16:00 to the 4h bar opening 2026-11-22 20:00 UTC (about 22 days)

`python3 -m lab.forward`. Funding after 2026-10-31 16:00 counted as zero (ESTIMATES).

| setup | symbol | trades | net | price change, same window |
|---|---|---|---|---|
| tsmom (rank 1) | ETHUSDT | 2 | +1.52% | -23.5% |
| tsmom (rank 1) | SOLUSDT | 1 | +1.53% | -26.2% |
| week_range_breakout_volume (rank 2) | ETHUSDT | 0 | 0.00% | -23.5% |
| week_range_breakout_volume (rank 2) | SOLUSDT | 1 | -0.51% | -26.2% |
| oi_confirmed_breakout (rank 3) | BTCUSDT | 1 | +1.35% | -21.0% |
| oi_confirmed_breakout (rank 3) | ETHUSDT | 1 | +0.37% | -23.5% |

Reading: no new trades. The rebound took a little more from the open tsmom shorts. Still 6 trades in total, too few to judge.

## Run 2026-11-27 22:20 UTC (catch-up; the 25-27 Nov runs were missed while the session was down): window 2026-10-31 16:00 to the 4h bar opening 2026-11-26 20:00 UTC (about 26 days)

`python3 -m lab.forward`. Funding after 2026-10-31 16:00 counted as zero (ESTIMATES).

| setup | symbol | trades | net | price change, same window |
|---|---|---|---|---|
| tsmom (rank 1) | ETHUSDT | 2 | +1.62% | -25.0% |
| tsmom (rank 1) | SOLUSDT | 1 | +1.51% | -25.9% |
| week_range_breakout_volume (rank 2) | ETHUSDT | 0 | 0.00% | -25.0% |
| week_range_breakout_volume (rank 2) | SOLUSDT | 1 | -0.51% | -25.9% |
| oi_confirmed_breakout (rank 3) | BTCUSDT | 1 | +1.35% | -21.9% |
| oi_confirmed_breakout (rank 3) | ETHUSDT | 1 | +0.37% | -25.0% |

Reading: no new trades in four days. Still 6 trades in total, too few to judge.
