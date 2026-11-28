# Market ranking - BTCUSDT / ETHUSDT / SOLUSDT (Binance USDT-M perps)

- **Data timestamp:** last 1h bar opens 2026-11-27 23:00:00+00:00 and closes **2026-11-28 00:00:00+00:00**. Daily figures use complete UTC days up to 2026-11-27.
- **Source:** https://data.binance.vision/data/futures/um (official Binance public archive), fetched 2026-11-28T12:19:43.288503+00:00, loaded only through `strategies.data.load`. Produced by `research/ranking.py`.
- **Funding is stale:** the funding archive ends **2026-10-31 16:00:00+00:00**, 27.3 days before the last candle. Every funding figure below is as of that date, NOT as of the last bar. Do not read it as current.
- **Open interest** is cleaned before use: hours where the archive prints 0 are dropped and a trailing 3h median removes one-bar partial prints (zero-OI hours in the archive: BTCUSDT 0, ETHUSDT 0, SOLUSDT 0).
- Descriptive statistics only. Nothing here is a forecast and no P&L was computed.
- **Survivorship:** none of the three symbols was delisted, but they were picked today knowing they survived and stayed liquid. A universe chosen in 2022 could have held names that later died, so any history on these three flatters what a 2022 trader would have seen.

## Regime label

Rule (fixed by convention, trailing data only, evaluated on daily bars):

1. **volatile** if 7-day realised vol is at or above the 80th percentile of its own trailing 365 days;
2. else **trending** if ADX(14, daily) >= 25 (direction from +DI vs -DI);
3. else **ranging**.

| market | regime now | RV7 pctl (1y) | ADX14d | direction | days in this label | share of days since 2022-11 (trend/range/vol) |
|---|---|---|---|---|---|---|
| BTCUSDT | **volatile** | 97 | 50.1 | down (+DI 11.4 / -DI 36.1) | 13 | trending 38% / ranging 43% / volatile 19% |
| ETHUSDT | **volatile** | 91 | 45.1 | down (+DI 12.5 / -DI 32.6) | 14 | trending 42% / ranging 38% / volatile 20% |
| SOLUSDT | **volatile** | 88 | 45.5 | down (+DI 17.4 / -DI 29.0) | 13 | trending 38% / ranging 46% / volatile 16% |

The label is a coarse switch, not a signal. Vol takes precedence: a market that is falling hard is labelled volatile even when ADX also says trending, because stop distances and gap risk are what change first.

## Rank by dimension

| dimension | rank 1 > 2 > 3 (value) | rule |
|---|---|---|
| trend | BTCUSDT (50.10) > SOLUSDT (45.55) > ETHUSDT (45.13) | trend strength = ADX(14, daily), highest first; direction shown separately |
| momentum | SOLUSDT (-1.67) > ETHUSDT (-1.74) > BTCUSDT (-1.85) | 30d log return / (RV30 x sqrt(30/365)), highest first |
| volume | BTCUSDT (1.03) > ETHUSDT (0.96) > SOLUSDT (0.87) | last 24h USDT volume / prior 30d daily average, highest first |
| funding | ETHUSDT (27.40) > BTCUSDT (14.18) > SOLUSDT (9.25) | |30d funding percentile over 4y - 50|, most extreme first |
| open_interest | BTCUSDT (2.75) > ETHUSDT (1.07) > SOLUSDT (-17.19) | 30d change in open interest (contracts), largest build first |
| volatility | SOLUSDT (74.76) > ETHUSDT (70.42) > BTCUSDT (57.51) | 30d realised vol (annualised), highest first |

Rank 1 means 'most of that property', not 'best to trade'. With all three in the same macro move the differences between them are small next to the move itself.

## Trend

| market | last close | 7d % | 30d % | 90d % | 200d % | vs 50d MA % | vs 200d MA % | 50d vs 200d MA % | 200d MA 30d slope % | ADX14d | ER30 | from 365d high % | above 365d low % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 59,893.20 | +1.7 | -26.3 | -23.4 | -26.7 | -19.4 | -16.6 | +3.4 | -1.7 | 50.1 | 0.52 | -38.8 (high 97,932.10 on 2026-01-14) | +8.6 |
| ETHUSDT | 1,661.54 | +2.4 | -29.6 | -32.4 | -29.0 | -22.8 | -21.1 | +2.2 | -2.4 | 45.1 | 0.47 | -51.8 (high 3,446.12 on 2025-12-10) | +10.5 |
| SOLUSDT | 71.63 | +4.1 | -30.0 | -32.1 | -26.4 | -20.9 | -18.4 | +3.2 | -1.0 | 45.5 | 0.48 | -51.9 (high 148.80 on 2026-01-13) | +19.3 |

Returns use daily closes (00:00 UTC). ER30 = Kaufman efficiency ratio over 30 daily closes (net move / path length; about 0.18 is what a driftless random walk gives).

## Momentum and volatility

| market | 30d mom / vol | RV 7d ann % | RV 7d pctl (1y) | RV 30d ann % | RV 30d pctl (1y) | RV7 / RV30 | ATR14d % of price |
|---|---|---|---|---|---|---|---|
| BTCUSDT | -1.85 | 76.1 | 97 | 57.5 | 93 | 1.32 | 5.04 |
| ETHUSDT | -1.74 | 81.3 | 91 | 70.4 | 85 | 1.15 | 6.49 |
| SOLUSDT | -1.67 | 84.5 | 88 | 74.8 | 81 | 1.13 | 6.55 |

Realised vol is the standard deviation of 1h log returns, annualised with sqrt(8760). Percentile = share of the last 365 daily readings at or below today's.

## Volume (USDT quote volume)

| market | last 24h | 7d avg/day | 30d avg/day | 24h vs 30d avg | 7d vs 30d avg | 30d vs 365d avg | taker-buy share 24h % | taker-buy share 7d % |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | $14.01bn | $15.22bn | $13.56bn | 1.03x | 1.12x | 1.15x | 50.0 | 50.3 |
| ETHUSDT | $8.22bn | $8.15bn | $8.54bn | 0.96x | 0.95x | 0.91x | 50.5 | 50.5 |
| SOLUSDT | $1.40bn | $1.58bn | $1.62bn | 0.87x | 0.97x | 0.81x | 49.4 | 49.9 |

'30d avg' is the 30 complete days before the last day, so the last day is not compared with itself.

## Funding (as of 2026-10-31 16:00:00+00:00 - stale, see top)

| market | last settled % | last 24h sum % | 24h pctl (4y) | 30d avg per 8h % | 30d annualised % | 30d pctl (4y) | 4y median 30d annualised % | negative settlements 4y % |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | +0.0100 | +0.0300 | 78 | +0.0043 | +4.7 | 36 | +5.9 | 14 |
| ETHUSDT | -0.0036 | -0.0278 | 1 | +0.0029 | +3.2 | 23 | +5.4 | 15 |
| SOLUSDT | +0.0070 | -0.0008 | 30 | +0.0040 | +4.4 | 59 | +3.1 | 33 |

Funding is summed from actual settlements, so SOL's 2h/4h intervals are handled. Positive = longs pay shorts. 'per 8h' = 30-day sum / 90. Percentiles are against daily readings over the 4 years ending at the funding cut-off.

## Open interest (contracts)

| market | OI value | OI chg 7d % | price chg 7d % | OI chg 30d % | price chg 30d % | OI minus price 30d (pp) | OI 30d chg pctl (1y) | OI value / avg daily volume |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | $5.90bn | +2.4 | +1.7 | +2.7 | -26.3 | +29.1 | 53 | 0.43 |
| ETHUSDT | $3.96bn | +1.0 | +2.4 | +1.1 | -29.6 | +30.6 | 43 | 0.46 |
| SOLUSDT | $0.54bn | -10.1 | +4.1 | -17.2 | -30.0 | +12.8 | 4 | 0.33 |

OI / price state:

- **BTCUSDT** 7d: price up / OI up (leverage added with the move); 30d: price down / OI up (new positions opened into the decline).
- **ETHUSDT** 7d: price up / OI up (leverage added with the move); 30d: price down / OI up (new positions opened into the decline).
- **SOLUSDT** 7d: price up / OI down (short covering, no new money); 30d: price down / OI down (deleveraging, positions closed).

OI in contracts is used so a falling price does not mechanically shrink it. A one-sided reading is not possible from OI alone: every contract has a long and a short.

## Positioning ratios (Binance accounts, context only)

| market | all-accounts long/short | pctl (1y) | top-trader long/short (by position) | pctl (1y) |
|---|---|---|---|---|
| BTCUSDT | 3.00 | 98 | 2.06 | 76 |
| ETHUSDT | 2.50 | 65 | 1.85 | 78 |
| SOLUSDT | 3.53 | 84 | 2.07 | 68 |

These count Binance accounts only and say nothing about size on other venues. Not used in any setup.

## Reading (risk view, no forecast)

Order of figures: BTC / ETH / SOL.

- **Regime:** all three carry the same label, **volatile**. 7-day realised vol sits at the 97 / 91 / 88 percentile of the past year and ADX is 50.1 / 45.1 / 45.5 with direction down / down / down. Three markets with one label are one bet, not three.
- **Trend:** price is below both the 50-day and the 200-day average in all three (-19.4 / -22.8 / -20.9% and -16.6 / -21.1 / -18.4%). The 50-day average is still above the 200-day (+3.4 / +2.2 / +3.2%): the averages have not caught up with the fall, so slow trend systems are late by construction here.
- **Size of the move:** 7-day return +1.7 / +2.4 / +4.1%, 30-day -26.3 / -29.6 / -30.0%; -38.8 / -51.8 / -51.9% from the 365-day high. The 7-day lows were BTC 55,126.70 (bar opening 2026-11-21 06:00 UTC), ETH 1,536.10 (bar opening 2026-11-21 04:00 UTC), SOL 64.83 (bar opening 2026-11-21 06:00 UTC); the last close is +8.6 / +8.2 / +10.5% above them.
- **Volatility:** 7-day realised vol 76 / 81 / 84% annualised, 1.32 / 1.15 / 1.13 times the 30-day figure. Daily ATR is 5.0 / 6.5 / 6.5% of price: a stop sized for last month is too tight for this week.
- **Volume:** the last 24h traded 1.03 / 0.96 / 0.87 times the prior 30-day daily average. Normal.
- **Open interest:** 7-day change in contracts +2.4 / +1.0 / -10.1% against price +1.7 / +2.4 / +4.1%. SOL: positions were closed into the fall (deleveraging). BTC, ETH: open interest ROSE while price fell, i.e. new positions were opened into the decline. That is leverage that has not been flushed yet; it can unwind in either direction.
- **Funding:** not available for the sell-off. The archive stops 2026-10-31 16:00 UTC, 27 days before the last candle. At that cut-off the 30-day average was +4.7 / +3.2 / +4.4% annualised (36 / 23 / 59 percentile of 4 years): unremarkable. Whether funding has since turned negative is unknown from this data.
- **Positioning (Binance accounts only):** long/short account ratio 3.00 / 2.50 / 3.53, at the 98 / 65 / 84 percentile of the past year (BTC in the top decile: an unusually large share of accounts is net long into the decline). The ratio counts accounts, not size. Context, not a signal.
- **What this does NOT say:** it does not say the low is in, and it does not say the fall continues. It says volatility is high, the three markets are moving as one, and the trend filters most setups rely on are pointing down or lagging.
- **For the setup list:** by the rule above only the setups tagged `volatile` are in their intended regime today. That is a switch for Lab to test, not a recommendation; nothing here has been backtested.
