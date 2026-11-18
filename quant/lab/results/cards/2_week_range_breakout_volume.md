# 2. `week_range_breakout_volume` - breakout of a quiet week on rising volume, ETHUSDT and SOLUSDT (4h bars)

**Status: candidate for paper trading. Unproven. Fails the buy-and-hold gate (passes on ETH, fails on SOL). Not for live.**

## Idea in one line

When price closes outside a tight seven-day range while volume is picking up, go with the break for up to seven days.

## Why the edge should exist, and who loses

Radar's reasoning: A week-long balance area holds a lot of inventory; when price leaves it on rising participation the side caught inside has to exit over several days. The losers are swing traders positioned for the range to hold.

What the runs add: about half the breaks fail and end at the stop (average -1.04R on ETH, -1.05R on SOL). The trades that last to the seven-day exit average +2.27R on ETH and +1.97R on SOL. Longs and shorts both made money on both symbols, which is what you would expect if the effect is about ranges breaking and not about the market drifting up.

## Rules (exact: `strategies/week_range_breakout_volume.py`, DEFAULT_PARAMS)

- **Bars:** 4h, UTC. It decides at every 4h close.
- **Tight range:** over the previous 42 bars (7 days, not counting the current bar), highest high minus lowest low is at most 1 x sqrt(42) x ATR(14), about 6.5 ATR. ATR is the plain average of the last 14 four-hour true ranges, taken at the previous bar.
- **Rising volume:** average volume of the last 6 bars (one day, including the current bar) is at least 1.3 x the average of the 42 bars before them.
- **Entry:** with both conditions true, a close above the range high is a long and a close below the range low is a short. Market order at the next open. Taker fee and slippage.
- **Stop:** 2 x ATR(14) from the signal close. It does not trail.
- **Exit:** at the open after 42 bars (7 days) in the trade, if the stop has not been hit.
- **No profit target.** One position per symbol; signals that arrive while a trade is open are ignored.
- **Parameters:** `{"range_n": 42, "width_mult": 1.0, "vol_fast": 6, "vol_slow": 42, "vol_ratio": 1.3, "atr_n": 14, "stop_atr": 2.0, "max_hold": 42}` (DEFAULT_PARAMS).

What a typical trade looks like (full window):

| | ETHUSDT | SOLUSDT |
|---|---|---|
| Trades in four years | 76 | 64 |
| Long share | 50% | 55% |
| Closed by the stop / by the 7-day time exit | 40 / 35 | 29 / 34 |
| Time in a trade: median / longest | 6.2 / 7 days | 7.0 / 7 days |
| Average winner / average loser | +2.53R / -0.98R | +2.36R / -0.91R |
| Win rate | 42% | 45% |
| Net from longs / from shorts | +$120 / +$79 | +$113 / +$84 |
| Best trade as a share of the four-year profit | 25% | 19% |
| Profit without the three best trades | +$76 | +$95 |

## Size per trade

- **Risk:** 0.5% of the book's equity between entry and stop. Quantity = 0.005 x equity / |entry - stop|, capped at 3x equity notional and rounded down to the lot step.
- **In practice:** the stop sits about 4.2% away on ETH and 5.1% on SOL (median), so a position is about 12% of equity on ETH ($135 on $1,000) and 10% on SOL ($108).
- **Smallest orders:** $46 on ETH and $44 on SOL, well above the assumed minimums of $20 and $5 (unverified). No order was refused for size.

## Results after fees, slippage and funding, against buy-and-hold

Each symbol on its own $1,000 at 0.5% risk, exchange minimums ON:

| Symbol | Window | Setup net | Buy-and-hold net | Max drawdown: setup / B&H | Trades | Win rate | Profit factor | Fees + slippage | Funding paid |
|---|---|---|---|---|---|---|---|---|---|
| ETHUSDT | first 36 months | +15.55% | +99.0% | 3.8% / 79.0% | 54 | 43% | 1.99 | $9 | $5 |
| ETHUSDT | last 12 months (unseen) | +2.13% | -43.3% | 2.4% / 61.3% | 22 | 36% | 1.31 | $4 | $1 |
| ETHUSDT | full window | +19.89% | -6.9% | 3.8% / 80.9% | 76 | 42% | 1.85 | $14 | $6 |
| SOLUSDT | first 36 months | +9.42% | +416.3% | 4.2% / 70.4% | 45 | 47% | 1.76 | $7 | $13 |
| SOLUSDT | last 12 months (unseen) | +7.91% | -48.9% | 1.9% / 66.7% | 19 | 37% | 2.73 | $4 | $1 |
| SOLUSDT | full window | +19.67% | +135.5% | 4.2% / 83.8% | 64 | 45% | 2.13 | $11 | $15 |

| Year | ETHUSDT: setup / B&H (trades) | SOLUSDT: setup / B&H (trades) |
|---|---|---|
| 2022 (Nov-Dec) | -0.9% / -24.2% (4) | +1.3% / -52.2% (3) |
| 2023 | -0.7% / +78.1% (13) | +10.0% / +573.8% (14) |
| 2024 | +9.4% / +29.7% (22) | -1.1% / +62.8% (16) |
| 2025 | +8.0% / -19.1% (17) | +0.5% / -38.4% (15) |
| 2026 (to 31 Oct) | +3.2% / -34.3% (20) | +8.0% / -27.2% (16) |

Both symbols together:

| ETH + SOL | First 36 months | Last 12 months (unseen) | Full window |
|---|---|---|---|
| Sum of the per-symbol runs | +25.0% | +10.0% | +39.6% |
| One $1,000 book trading both (as the bot does), no Shield | +26.4% | +10.0% | +43.1% |
| The same book with the Shield (next-day resume is an ASSUMPTION) | +27.1% | +10.4% | +44.4% |
| Max drawdown (sum of runs) | 4.3% | 3.8% | 4.3% |
| Equal-weight buy-and-hold of the same symbols | +257.6% | -46.1% | +64.3% |

**Buy-and-hold gate (full window, per symbol):**

- ETHUSDT: +19.9% against -6.9%. **Passes.**
- SOLUSDT: +19.7% against +135.5%. **Fails.**
- The setup fails the gate because it must pass on every symbol it trades. The ETH pass comes from ETH buy-and-hold losing money after funding.
- Risk-adjusted (not the gate): Sharpe 1.01 against 0.38 on ETH and 1.12 against 0.70 on SOL; max drawdown 3.8% against 80.9% and 4.2% against 83.8%.
- Bear-labelled bars (full window): ETH +4.0% against -23.8%, SOL +4.3% against -36.1%. Sideways-labelled bars: ETH +15.7% against -18.1%, SOL +10.4% against +67.9%.

## Walk-forward (first 36 months only)

Twelve months choose the parameters from the grid (range 30 / 42 / 60 bars, width 0.8 / 1.0 / 1.25, volume ratio 1.15 / 1.3 / 1.5), the next three months are traded, and the window rolls forward. Only the traded quarters are shown.

| Out-of-sample quarter | ETHUSDT: net (trades) | SOLUSDT: net (trades) |
|---|---|---|
| 2023-11-01 to 2024-02-01 | -1.00% (4) | +4.36% (5) |
| 2024-02-01 to 2024-05-01 | -0.28% (2) | -0.93% (7) |
| 2024-05-01 to 2024-08-01 | +4.13% (5) | +0.50% (6) |
| 2024-08-01 to 2024-11-01 | +0.78% (7) | +0.05% (3) |
| 2024-11-01 to 2025-02-01 | +1.81% (5) | -0.89% (8) |
| 2025-02-01 to 2025-05-01 | +3.32% (6) | -0.11% (1) |
| 2025-05-01 to 2025-08-01 | +6.85% (6) | +0.19% (5) |
| 2025-08-01 to 2025-11-01 | -1.74% (4) | -1.24% (4) |
| **Stitched, 24 months** | **+14.4%** (39), 5 of 8 quarters up | **+1.8%** (39), 4 of 8 quarters up |

SOL's walk-forward is barely above zero. The most frequent pick (`{"range_n": 42, "vol_ratio": 1.15, "width_mult": 1.0}`) was chosen 12% of the time, far below the 50% needed to replace the defaults.

## Worst month and longest losing streak

| | ETHUSDT | SOLUSDT | ETH + SOL together |
|---|---|---|---|
| Worst calendar month, full window | -1.1% (2023-12) | -1.4% (2024-03) | -1.8% (2025-08) |
| Longest losing streak, full window | 5 trades | 4 trades | 7 trades |
| Longest losing streak, last 12 months | 4 | 4 | 5 |

- Calendar years for the two symbols together: 2022 +0.4%, 2023 +9.4%, 2024 +7.3%, 2025 +7.8%, 2026 +9.9% (2022 is November and December only; 2026 runs to 31 October).
- 3 of 17 calendar quarters lost money, and the longest stretch without a new equity high was 237 days.
- No 12-month window ended in the red (worst +1.2%); the worst six months were -2.1%.
- The two-symbol total hides uneven legs: ETH lost a little in 2022 and 2023, SOL in 2024, and SOL made almost nothing in 2025.

## The condition that kills it

**Breaks of a quiet week that do not follow through.** Each failed break costs a full stop, about 1R, and 53% of ETH trades and 45% of SOL trades already end that way. The setup lives on the other half being worth more than twice as much. If ranges start breaking and snapping back (thin markets, stop-hunting around obvious weekly levels) the winners shrink and the arithmetic turns negative quickly. A change in how exchange volume behaves would also break the volume filter.

Stop trading it if either happens:

- the two-symbol book falls more than **9%** from its high (95th percentile when the four-year trade list is resampled: 8.7%; worst seen 4.3%);
- any **12 months** end below where they started (never happened in the backtest; the worst was +1.2%).

## The first sign it is weakening

- **A six-month loss worse than -2.1%**, the worst six months in the backtest.
- **The last 20 trades average below -0.20R.** Random draws of 20 trades from its own history do that only 1 time in 20.
- **A losing streak longer than 12 trades** (95th percentile from resampling; the longest seen was 7).
- **Clearly more than half the trades ending at the stop** over 20 or more trades.

With about 35 trades a year across the two symbols, 20 trades take about 7 months. Any verdict on this setup is slow.

## Final check

| Question | ETHUSDT | SOLUSDT |
|---|---|---|
| Still wins on unseen data (last 12 months)? | **YES** +2.13% over 22 trades | **YES** +7.91% over 19 trades |
| After fees and funding? | **YES** +19.9% full window (fees + slippage $14, funding $6) | **YES** +19.7% full window (fees + slippage $11, funding $15) |
| Over 50+ trades? | **YES** 54 in the first 36 months, 76 in the full window | **NO** 45 in the first 36 months, 64 in the full window |
| With double fees? | **YES** +14.8% (fees and slippage both doubled: +14.5%; base +15.6%) | **YES** +9.0% (fees and slippage both doubled: +8.7%; base +9.4%) |
| Across nearby settings, not one lucky value? | **YES** 27 of 27 grid points profitable (worst +0.8%, median +8.4%, this setting +15.6%) | **YES** 27 of 27 grid points profitable (worst +0.4%, median +7.5%, this setting +9.4%) |
| Any leak from the future? | **NO** signals identical at 36 cut points (0 rows differ); one bar late: +11.0% vs +15.6% on time | **NO** signals identical at 36 cut points (0 rows differ); one bar late: +6.6% vs +9.4% on time |
| Better than same-side random entries? | p = 0.02 first 36 months (random entries averaged +1.9%); p = 0.02 full window | p = 0.04 first 36 months (random entries averaged +1.6%); p = 0.01 full window |
| Clears the multiple-testing haircut? | **NO** Sharpe 1.04, p 0.035 before correction, 1.00 after (87 tests) | **NO** Sharpe 0.76, p 0.093 before correction, 1.00 after (87 tests) |

Also checked: the same signals executed on 1h bars instead of 4h bars give +19.9% on ETH and +19.7% on SOL over the full window. Without exchange minimums: +20.1% and +19.7%.

## What the bot needs

- **Module / timeframe / symbols:** `week_range_breakout_volume`, 4h, ETHUSDT, SOLUSDT. BTCUSDT was killed in the first 36 months (Monte Carlo chance of a loss above 20%, 44 trades).
- **Columns read:** `high`, `low`, `close`, `volume`. No open interest, no funding. Volume must be the same exchange volume the archive carries.
- **Warm-up:** 130 four-hour bars (22 days).
- **Shield:** the three-losses rule would have halted this book 9 times in four years (about 2 a year). Simulated with next-day resume it made no real difference (+43.1% without, +44.4% with).

## WHY THIS COULD FAIL

- **It may be luck.** After correcting for the 87 pairs tested, nothing here is significant (corrected p = 1.00 on both symbols; Sharpe 1.04 and 0.76 over the first 36 months).
- **There are few trades.** 76 on ETH and 64 on SOL in four years, about 19 and 16 a year. SOL had 45 in the first 36 months, under the owner's 50 by itself; the rule is met only by pooling the two symbols (99).
- **The SOL leg is weak where it was tested hardest.** Its walk-forward made +1.8% in 24 months with 4 of 8 quarters up, and most of its first-36-month profit came in 2023.
- **It is sensitive to entry timing on SOL.** Entering two bars later would have made +29.8% instead of +9.4% in the first 36 months. With this few trades a small shift changes which trades exist, so the measured edge has wide error bars.
- **Winners are still concentrated.** The best trade is 25% of the ETH profit and 19% of SOL's; 10 and 8 trades of +3R or better carry the result.
- **It is the same bet as rank 1.** Both follow moves on ETH and SOL. They will tend to win and lose together.
- **It trails buy-and-hold on SOL by more than 100 points.** On ETH it wins only because funding turned a +43% price gain into a -6.9% result for the holder. The absolute return is about 4.6% a year on ETH and 4.6% on SOL.
- **The unseen year is thin evidence.** 22 and 19 trades.
- **The volume filter depends on the venue.** A change in fee tiers, market-maker programmes or wash trading moves 'volume 30% above last week' without any change in real participation.
- **Fills may be worse than modelled.** A breakout bar is exactly when the next open is crowded; entry slippage is assumed to be 1 to 2 basis points.
