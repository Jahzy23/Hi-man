# 3. `oi_confirmed_breakout` - breakout confirmed by rising open interest, BTCUSDT and ETHUSDT (4h bars)

**Status: PAPER ONLY, NOT A CANDIDATE FOR LIVE. Killed at the final gate: it did not make money on either symbol in the last 12 months. It also cannot run on the live feed today, which carries no open interest.**

Only two setups survived every test. This one fills the third slot as the least bad of the rest: it scored highest on the first 36 months, then failed the one test it had not seen. It is here so the owner can see what a near miss looks like, not as a proposal.

## Idea in one line

Take a new five-day high or low only if open interest has grown at least 5% over the same five days.

## Why the edge should exist, and who loses

Radar's reasoning: Rising open interest on a breakout means new positions are being opened, and whoever opened against the move is trapped. The losers are faders who added against the breakout.

Radar's own warning: More open interest also means more leverage to liquidate if the break fails: bigger and faster reversals.

## Rules (exact: `strategies/oi_confirmed_breakout.py`, DEFAULT_PARAMS)

- **Bars:** 4h, UTC. It decides at every 4h close.
- **Open interest building:** cleaned open interest (zero prints dropped, trailing 3-bar median) is at least 5% higher than 30 bars (5 days) earlier.
- **Entry:** the first close above the highest high of the previous 30 bars while open interest is building is a long; the first close below the lowest low is a short. Market order at the next open.
- **Stop:** 2 x ATR(14) from the signal close. It does not trail.
- **Exit:** at the open after 42 bars (7 days), if the stop has not been hit. No profit target.
- **Parameters:** `{"n": 30, "oi_rise": 0.05, "atr_n": 14, "stop_atr": 2.0, "max_hold": 42}` (DEFAULT_PARAMS).

| | BTCUSDT | ETHUSDT |
|---|---|---|
| Trades in four years | 63 | 82 |
| Long share | 75% | 62% |
| Closed by the stop / by the 7-day time exit | 40 / 23 | 46 / 36 |
| Time in a trade: median / longest | 3.0 / 7 days | 5.3 / 7 days |
| Average winner / average loser | +3.01R / -1.04R | +2.74R / -1.03R |
| Win rate | 33% | 41% |
| Net from longs / from shorts | +$150 / -$52 | +$146 / +$89 |
| Best trade as a share of the four-year profit | 64% | 26% |
| Profit without the three best trades | -$42 | +$86 |

## Size per trade

- **Risk:** 0.5% of the book's equity between entry and stop, capped at 3x equity notional, rounded down to the lot step.
- **In practice:** stops about 2.4% away on BTC and 3.5% on ETH, so positions of about 19% and 14% of equity ($195 and $160).
- **BTC and the minimum order:** with the assumed $100 minimum and 0.001 BTC lot, 13 BTC orders were refused as too small in four years (unverified table).

## Results after fees, slippage and funding, against buy-and-hold

| Symbol | Window | Setup net | Buy-and-hold net | Max drawdown: setup / B&H | Trades | Win rate | Profit factor | Fees + slippage | Funding paid |
|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | first 36 months | +10.15% | +360.4% | 4.2% / 36.4% | 56 | 32% | 1.55 | $14 | $9 |
| BTCUSDT | last 12 months (unseen) | -0.74% | -31.8% | 1.9% / 48.7% | 7 | 43% | 0.59 | $2 | $0 |
| BTCUSDT | full window | +9.79% | +190.7% | 4.2% / 62.1% | 63 | 33% | 1.48 | $16 | $9 |
| ETHUSDT | first 36 months | +23.54% | +99.0% | 4.2% / 79.0% | 67 | 42% | 2.06 | $15 | $11 |
| ETHUSDT | last 12 months (unseen) | -0.01% | -43.3% | 3.3% / 61.3% | 15 | 40% | 1.00 | $2 | $0 |
| ETHUSDT | full window | +23.56% | -6.9% | 4.4% / 80.9% | 82 | 41% | 1.85 | $18 | $11 |

| Year | BTCUSDT: setup / B&H (trades) | ETHUSDT: setup / B&H (trades) |
|---|---|---|
| 2022 (Nov-Dec) | +0.0% / -19.6% (1) | -0.6% / -24.2% (2) |
| 2023 | +6.5% / +141.4% (23) | +7.8% / +78.1% (23) |
| 2024 | +3.9% / +108.3% (19) | +8.6% / +29.7% (26) |
| 2025 | -0.9% / -13.6% (14) | +4.9% / -19.1% (20) |
| 2026 (to 31 Oct) | +0.1% / -16.8% (6) | +1.2% / -34.3% (11) |

| BTC + ETH | First 36 months | Last 12 months (unseen) | Full window |
|---|---|---|---|
| Sum of the per-symbol runs | +33.7% | -0.8% | +33.3% |
| One $1,000 book trading both (as the bot does), no Shield | +35.1% | -0.6% | +38.9% |
| The same book with the Shield (next-day resume is an ASSUMPTION) | +35.6% | +0.2% | +40.3% |
| Max drawdown (sum of runs) | 6.5% | 4.7% | 6.5% |
| Equal-weight buy-and-hold of the same symbols | +229.7% | -37.6% | +91.9% |

**Buy-and-hold gate (full window, per symbol):**

- BTCUSDT: +9.8% against +190.7%. **Fails.**
- ETHUSDT: +23.6% against -6.9%. **Passes.**
- Fails the gate. It is also dead at the earlier holdout gate, so the question is academic.

## Walk-forward (first 36 months only)

| Out-of-sample quarter | BTCUSDT: net (trades) | ETHUSDT: net (trades) |
|---|---|---|
| 2023-11-01 to 2024-02-01 | +3.50% (1) | -2.42% (6) |
| 2024-02-01 to 2024-05-01 | +3.88% (4) | +3.85% (10) |
| 2024-05-01 to 2024-08-01 | +1.04% (6) | +2.60% (10) |
| 2024-08-01 to 2024-11-01 | -1.53% (8) | +8.20% (8) |
| 2024-11-01 to 2025-02-01 | -0.48% (1) | -0.04% (9) |
| 2025-02-01 to 2025-05-01 | -0.20% (3) | +1.09% (5) |
| 2025-05-01 to 2025-08-01 | -0.93% (6) | +8.13% (10) |
| 2025-08-01 to 2025-11-01 | +0.85% (5) | +0.27% (4) |
| **Stitched, 24 months** | **+6.2%** (34), 4 of 8 quarters up | **+23.3%** (62), 6 of 8 quarters up |

## Worst month and longest losing streak

| | BTCUSDT | ETHUSDT | BTC + ETH together |
|---|---|---|---|
| Worst calendar month, full window | -2.2% (2025-03) | -1.5% (2025-10) | -2.3% (2023-05) |
| Longest losing streak, full window | 5 trades | 6 trades | 9 trades |

- Calendar years for the two symbols together: 2022 -0.6%, 2023 +14.3%, 2024 +11.8%, 2025 +3.7%, 2026 +1.2% (2022 is November and December only; 2026 runs to 31 October). The profit shrank every year after 2023.

## The condition that kills it

**It has already met it.** The rule fixed in advance was that a finalist must make money in the last 12 months, which it had never seen:

- BTCUSDT: -0.74% over 7 trades.
- ETHUSDT: -0.01% over 15 trades.
- Without the exchange minimums the figures are +3.89% and +0.04%: a scrape, on a size table that is itself unverified. The verdict uses the primary configuration, and a result this close to zero would not be a pass worth having either way.

What would bring it back: open interest on the live feed, and a further unseen period in which it makes money. Until both, it is not a candidate. If open interest is ever fed live, running it on paper as an observation is harmless.

## The first sign it is weakening

It showed before the holdout was opened, in the walk-forward and the yearly figures:

- BTC's walk-forward had 4 of 8 quarters up, and 4 of the last 5 lost money.
- The two-symbol profit fell from +14.3% in 2023 to +11.8% in 2024 and +3.7% in 2025.
- On BTC the result is no better than same-side random entries (p = 0.12).
- On ETH one bar of delay halves the result (+11.4% against +23.5%): the entry has to be immediate, which is the first thing to go wrong live.

## Final check

| Question | BTCUSDT | ETHUSDT |
|---|---|---|
| Still wins on unseen data (last 12 months)? | **NO** -0.74% over 7 trades | **NO** -0.01% over 15 trades |
| After fees and funding? | **YES** +9.8% full window (fees + slippage $16, funding $9) | **YES** +23.6% full window (fees + slippage $18, funding $11) |
| Over 50+ trades? | **YES** 56 in the first 36 months, 63 in the full window | **YES** 67 in the first 36 months, 82 in the full window |
| With double fees? | **YES** +9.3% (fees and slippage both doubled: +9.0%; base +10.2%) | **YES** +22.3% (fees and slippage both doubled: +22.0%; base +23.5%) |
| Across nearby settings, not one lucky value? | **YES** 26 of 27 grid points profitable (worst -1.3%, median +6.3%, this setting +10.2%) | **YES** 27 of 27 grid points profitable (worst +5.2%, median +13.2%, this setting +23.5%) |
| Any leak from the future? | **NO** signals identical at 36 cut points (0 rows differ); one bar late: +5.4% vs +10.2% on time | **NO** signals identical at 36 cut points (0 rows differ); one bar late: +11.4% vs +23.5% on time |
| Better than same-side random entries? | p = 0.12 first 36 months (random entries averaged +3.2%); p = 0.13 full window | p = 0.02 first 36 months (random entries averaged +2.0%); p = 0.03 full window |
| Clears the multiple-testing haircut? | **NO** Sharpe 0.76, p 0.093 before correction, 1.00 after (87 tests) | **NO** Sharpe 1.27, p 0.014 before correction, 1.00 after (87 tests) |

## What the bot needs

- **Columns read:** `high`, `low`, `close`, `oi`. **`oi` is not available live**: the bot marks live open interest as unknown and refuses to decide for a setup that reads an unknown column. The archive supplies it only in daily files, after the fact.
- **Warm-up:** 80 four-hour bars (13 days).
- **Recommendation:** do not load it as the leader. If it is loaded at all, mark it as an observation book.

## WHY THIS COULD FAIL

- **It already has.** Flat to down on both symbols in the only year it had not seen.
- **It ranked first on the selection window and that was probably selection.** Best of 29 setups on 36 months is exactly where luck concentrates. Its pooled Sharpe of 1.31 has a corrected p-value of 0.33 (29 setups): it cleared the weak pre-registered bar and is nowhere near the usual 5%.
- **The BTC result is two or three trades.** The best trade is 64% of BTC's four-year profit; without the three best BTC loses $42. BTC shorts lost $52.
- **The open-interest series is dirty.** The archive has zero prints and at least one partial print; the setup cleans them, but a 5% threshold on a five-day change is sensitive to exactly that kind of error.
- **Open interest is not on the live feed.** A setup that cannot be run live cannot be paper-traded honestly.
- **On $1,000 BTC orders are often below the exchange minimum**, so the BTC leg trades a filtered subset of its signals.
- **It is one more trend-following bet** on the same coins as ranks 1 and 2.
