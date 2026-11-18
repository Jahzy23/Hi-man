# 1. `tsmom` - 30-day time-series momentum on ETHUSDT and SOLUSDT (4h bars)

**Status: candidate for paper trading. Unproven. Fails the buy-and-hold gate (passes on ETH, fails on SOL). Not for live.**

## Idea in one line

Once a day, be long if price is above where it was 30 days ago and short if it is below, with a wide stop for disasters.

## Why the edge should exist, and who loses

Radar's reasoning: Time-series momentum is the best documented futures anomaly: under-reaction to news and trend-chasing flows make last month's sign persist. The losers are contrarians and late hedgers trading against the drift.

What the runs add: the money does not come from being right often. The win rate is 26% on ETH and 39% on SOL. It comes from a few trends that run for weeks: 11 trades of +3R or better on ETH and 8 on SOL in four years pay for everything else. The people on the other side of those trades are those who fade a move that keeps going.

## Rules (exact: `strategies/tsmom.py`, DEFAULT_PARAMS)

- **Bars:** 4h, UTC.
- **When it decides:** once a day, at the close of the 4h bar that opens at 20:00 and ends at 00:00 UTC.
- **Signal:** the sign of close / close 180 bars earlier - 1 (180 x 4h = 30 days). Positive means long, negative means short.
- **Entry:** market order at the next open (00:00 UTC). Taker fee and slippage.
- **Stop:** 4 x ATR(14) away from the signal close. ATR is the plain average of the last 14 four-hour true ranges. The stop does not trail.
- **Exit:** at the first daily decision where the sign has flipped, close at the next open and open the opposite side at that same open.
- **After a stop-out:** it re-enters at the next daily decision if the sign still holds.
- **No profit target and no time limit.** One position per symbol.
- **Parameters:** `{"lookback": 180, "eval_hour": 20, "atr_n": 14, "stop_atr": 4.0}` (DEFAULT_PARAMS).

What a typical trade looks like (full window):

| | ETHUSDT | SOLUSDT |
|---|---|---|
| Trades in four years | 136 | 118 |
| Long share | 48% | 48% |
| Closed by the sign flip / by the stop | 112 / 23 | 88 / 29 |
| Time in a trade: median / average / longest | 3 / 11 / 100 days | 3 / 12 / 112 days |
| Average winner / average loser | +2.64R / -0.48R | +2.20R / -0.64R |
| Net from longs / from shorts | +$212 / +$40 | +$273 / +$10 |

## Size per trade

- **Risk:** 0.5% of the book's equity between entry and stop. Quantity = 0.005 x equity / |entry - stop|, capped at 3x equity notional and rounded down to the lot step.
- **In practice:** the stop sits about 6.2% away on ETH and 9.2% on SOL (median), so a position is small: about 8% of equity on ETH ($84 on $1,000) and 5% on SOL ($65). The leverage cap never comes into play.
- **Smallest orders:** $23 on ETH and $18 on SOL, against assumed minimums of $20 and $5 (unverified).

## Results after fees, slippage and funding, against buy-and-hold

Each symbol on its own $1,000 at 0.5% risk, exchange minimums ON:

| Symbol | Window | Setup net | Buy-and-hold net | Max drawdown: setup / B&H | Trades | Win rate | Profit factor | Fees + slippage | Funding paid |
|---|---|---|---|---|---|---|---|---|---|
| ETHUSDT | first 36 months | +14.30% | +99.0% | 5.3% / 79.0% | 103 | 25% | 1.75 | $11 | $15 |
| ETHUSDT | last 12 months (unseen) | +9.39% | -43.3% | 4.4% / 61.3% | 34 | 32% | 2.88 | $4 | $2 |
| ETHUSDT | full window | +25.22% | -6.9% | 5.3% / 80.9% | 136 | 26% | 2.02 | $15 | $18 |
| SOLUSDT | first 36 months | +23.71% | +416.3% | 9.0% / 70.4% | 82 | 43% | 2.32 | $7 | $31 |
| SOLUSDT | last 12 months (unseen) | +2.59% | -48.9% | 5.0% / 66.7% | 37 | 32% | 1.35 | $4 | $2 |
| SOLUSDT | full window | +28.37% | +135.5% | 9.4% / 83.8% | 118 | 39% | 2.04 | $13 | $34 |

| Year | ETHUSDT: setup / B&H (trades) | SOLUSDT: setup / B&H (trades) |
|---|---|---|
| 2022 (Nov-Dec) | -1.8% / -24.2% (9) | +0.7% / -52.2% (11) |
| 2023 | +3.7% / +78.1% (39) | +25.3% / +573.8% (21) |
| 2024 | -0.9% / +29.7% (37) | -0.8% / +62.8% (22) |
| 2025 | +14.6% / -19.1% (20) | +1.5% / -38.4% (28) |
| 2026 (to 31 Oct) | +8.3% / -34.3% (31) | +1.0% / -27.2% (36) |

Both symbols together:

| ETH + SOL | First 36 months | Last 12 months (unseen) | Full window |
|---|---|---|---|
| Sum of the two runs above | +38.0% | +12.0% | +53.6% |
| One $1,000 book trading both (as the bot does), no Shield | +41.3% | +11.9% | +60.1% |
| The same book with the Shield (next-day resume is an ASSUMPTION) | +32.1% | +12.7% | +50.8% |
| Max drawdown (sum of runs) | 10.3% | 7.2% | 10.3% |
| Equal-weight buy-and-hold of ETH and SOL | +257.6% | -46.1% | +64.3% |

The shared book earns more than the sum of the separate runs because every trade is sized on the combined equity.

**Buy-and-hold gate (full window, per symbol):**

- ETHUSDT: +25.2% against -6.9%. **Passes.**
- SOLUSDT: +28.4% against +135.5%. **Fails.**
- The setup fails the gate because it must pass on every symbol it trades. The ETH pass comes from ETH buy-and-hold losing money after funding, not from a large setup return.
- Risk-adjusted (not the gate): Sharpe 0.94 against 0.38 on ETH and 0.84 against 0.70 on SOL; max drawdown 5.3% against 80.9% and 9.4% against 83.8%.
- Bear-labelled bars (full window): ETH +0.1% against -23.8%, SOL -0.3% against -36.1%. Sideways-labelled bars: ETH +17.5% against -18.1%, SOL +6.3% against +67.9%.

## Walk-forward (first 36 months only)

Twelve months choose the parameters from the grid (lookback 120 / 180 / 360 bars, stop 3 / 4 / 5 ATR), the next three months are traded, and the window rolls forward. Only the traded quarters are shown.

| Out-of-sample quarter | ETHUSDT: net (trades) | SOLUSDT: net (trades) |
|---|---|---|
| 2023-11-01 to 2024-02-01 | +1.75% (1) | +5.24% (3) |
| 2024-02-01 to 2024-05-01 | +1.75% (6) | +4.90% (5) |
| 2024-05-01 to 2024-08-01 | +0.68% (7) | +1.29% (9) |
| 2024-08-01 to 2024-11-01 | -0.85% (13) | -2.00% (13) |
| 2024-11-01 to 2025-02-01 | +2.35% (8) | +3.10% (7) |
| 2025-02-01 to 2025-05-01 | +3.16% (1) | -0.19% (9) |
| 2025-05-01 to 2025-08-01 | +3.72% (2) | +2.52% (5) |
| 2025-08-01 to 2025-11-01 | +0.06% (4) | -3.79% (18) |
| **Stitched, 24 months** | **+13.2%** (42), 7 of 8 quarters up | **+11.2%** (69), 5 of 8 quarters up |

The walk-forward picked the default setting in only some quarters; its most frequent pick (`{"lookback": 120, "stop_atr": 3.0}`) was chosen 25% of the time across all three symbols, below the 50% needed to replace the defaults.

## Worst month and longest losing streak

| | ETHUSDT | SOLUSDT | ETH + SOL together |
|---|---|---|---|
| Worst calendar month, full window | -2.6% (2024-01) | -2.8% (2024-01) | -4.8% (2024-01) |
| Longest losing streak, full window | 11 trades | 7 trades | 15 trades |
| Longest losing streak, last 12 months | 10 | 7 | 15 |

- Calendar years for the two symbols together: 2022 -1.1%, 2023 +29.5%, 2024 -1.5%, 2025 +13.2%, 2026 +7.6% (2022 is November and December only; 2026 runs to 31 October).
- 9 of 17 calendar quarters lost money, and the longest stretch without a new equity high was 501 days.
- A 12-month window ended in the red 4.5% of the time (worst -6.2%); no 24-month window did (worst +4.4%).

## The condition that kills it

**A market that keeps crossing its own 30-day-ago price.** Every crossing flips the sign, closes the trade and opens the other side. There were about 28 flips a year on ETH and 22 on SOL even in this trending sample, most of them small losses. In 2024 the setup lost a little on both symbols while they rose; a few such years in a row with no multi-week trend is how it dies. Sharp V-shaped reversals are the second way: the stop is hit and the sign flips only after most of the turn is over.

Stop trading it if either happens:

- the two-symbol book falls more than **13%** from its high (95th percentile when the four-year trade list is resampled: 12.9%; worst seen 10.3%);
- any **24 months** end below where they started (never happened in the backtest; the worst was +4.4%).

## The first sign it is weakening

- **The last 20 trades average below -0.36R.** Random draws of 20 trades from its own history do that only 1 time in 20.
- **A losing streak longer than 18 trades** (95th percentile from resampling; the longest seen was 15). Streaks of 8 to 15 are normal for it.
- **A 12-month loss worse than -6.2%.** A losing 12 months by itself is a warning, not a verdict.
- **No trade of +3R or better for more than about 11 months on a symbol.** The longest waits in the backtest were 245 days on ETH and 341 on SOL.

Detection is slow by nature. With about one winner in three and 64 trades a year across the two symbols, an edge that has gone cannot be told from an ordinary bad patch in less than several months.

## Final check

| Question | ETHUSDT | SOLUSDT |
|---|---|---|
| Still wins on unseen data (last 12 months)? | **YES** +9.39% over 34 trades | **YES** +2.59% over 37 trades |
| After fees and funding? | **YES** +25.2% full window (fees + slippage $15, funding $18) | **YES** +28.4% full window (fees + slippage $13, funding $34) |
| Over 50+ trades? | **YES** 103 in the first 36 months, 136 in the full window | **YES** 82 in the first 36 months, 118 in the full window |
| With double fees? | **YES** +13.3% (fees and slippage both doubled: +13.1%; base +14.3%) | **YES** +23.0% (fees and slippage both doubled: +22.8%; base +23.7%) |
| Across nearby settings, not one lucky value? | **NO** 6 of 9 grid points profitable (worst -4.1%, median +10.7%, this setting +14.3%) | **YES** 9 of 9 grid points profitable (worst +4.2%, median +18.5%, this setting +23.7%) |
| Any leak from the future? | **NO** signals identical at 36 cut points (0 rows differ); one bar late: +12.3% vs +14.3% on time | **NO** signals identical at 36 cut points (0 rows differ); one bar late: +26.1% vs +23.7% on time |
| Better than same-side random entries? | p = 0.05 first 36 months (random entries averaged +1.9%); p = 0.02 full window | p = 0.05 first 36 months (random entries averaged +3.5%); p = 0.03 full window |
| Clears the multiple-testing haircut? | **NO** Sharpe 0.80, p 0.084 before correction, 1.00 after (87 tests) | **NO** Sharpe 0.91, p 0.058 before correction, 1.00 after (87 tests) |

Also checked: the same signals executed on 1h bars instead of 4h bars give +25.2% on ETH and +28.4% on SOL over the full window, the same as above. Without exchange minimums: +25.3% and +28.3%.

## What the bot needs

- **Module / timeframe / symbols:** `tsmom`, 4h, ETHUSDT, SOLUSDT. BTCUSDT is excluded: it lost 1.80% in the unseen year, and on $1,000 the exchange minimum refused 430 of its orders in four years.
- **Columns read:** `high`, `low`, `close`. No open interest, no funding.
- **Warm-up:** 400 four-hour bars (67 days) before the first decision.
- **Same-open reversal:** on a sign flip the bot must close and open the other side at the same open (`lab/engine.py` rule R7). Without it the bot re-enters a day late on every flip.
- **Shield:** the three-losses rule would have halted this book 43 times in four years (about 11 a year). Each halt needs a person to clear it. Simulated with next-day resume, the Shield cut the full-window return from +60.1% to +50.8% and the drawdown from 10.4% to 7.3%.

## WHY THIS COULD FAIL

- **It may be luck.** After correcting for the 87 pairs tested, nothing here is significant (corrected p = 1.00 on both symbols; Sharpe 0.80 and 0.91 over the first 36 months).
- **One trade carries the SOL result.** The long from 2023-10-01 to 2024-01-21 made +43R, 77% of SOL's four-year profit. Without its three best trades SOL loses $41. On ETH the best trade is 33% of the profit and the result without the top three is +$55.
- **It needs trends, and the sample was full of them.** The window holds one of the largest crypto bull runs and a deep bear leg. In a flat market the setup bleeds small losses and fees, as it did in 2024.
- **It is fragile to the lookback on ETH.** With a 20-day lookback it loses at every stop width (6 of 9 grid points profitable). A rule that works at 30 and 60 days but not at 20 is not firmly established.
- **It trails buy-and-hold on SOL by more than 100 points.** On ETH it wins only because funding turned a +43% price gain into a -6.9% result for the holder. At 0.5% risk the absolute return is small: about 5.8% a year on ETH and 6.4% on SOL.
- **The unseen year is thin evidence.** 34 and 37 trades, and SOL's +2.59% is close to nothing. The BTC leg of the same setup lost money in that year.
- **The Shield and this setup pull against each other.** A setup that loses two trades in three will hit three losses in a row about once a month. If halts are slow to clear, the trades that pay for everything may be missed.
- **Fills may be worse than modelled.** Stops are assumed to fill at the stop price plus 2 to 4 basis points. A stop 4 ATR away is usually reached in a fast market.
- **Order sizes are near the floor on ETH.** The smallest ETH order was $23 against an unverified $20 minimum. A higher real minimum removes trades.
- **ETH and SOL move together.** Two symbols are not two independent tests; a bad stretch will hit both.
