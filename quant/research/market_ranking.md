# Market ranking - BTCUSDT / ETHUSDT / SOLUSDT (Binance USDT-M perps)

- **Data timestamp:** last 1h bar opens 2026-11-20 23:00:00+00:00 and closes **2026-11-21 00:00:00+00:00**. Daily figures use complete UTC days up to 2026-11-20.
- **Source:** https://data.binance.vision/data/futures/um (official Binance public archive), fetched 2026-11-21T12:19:59.676623+00:00, loaded only through `strategies.data.load`. Produced by `research/ranking.py`.
- **Funding is stale:** the funding archive ends **2026-10-31 16:00:00+00:00**, 20.3 days before the last candle. Every funding figure below is as of that date, NOT as of the last bar. Do not read it as current.
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
| BTCUSDT | **volatile** | 95 | 44.4 | down (+DI 5.0 / -DI 42.0) | 6 | trending 38% / ranging 43% / volatile 18% |
| ETHUSDT | **volatile** | 93 | 41.9 | down (+DI 7.0 / -DI 38.8) | 7 | trending 43% / ranging 38% / volatile 20% |
| SOLUSDT | **volatile** | 90 | 49.8 | down (+DI 7.9 / -DI 35.7) | 6 | trending 38% / ranging 46% / volatile 16% |

The label is a coarse switch, not a signal. Vol takes precedence: a market that is falling hard is labelled volatile even when ADX also says trending, because stop distances and gap risk are what change first.

## Rank by dimension

| dimension | rank 1 > 2 > 3 (value) | rule |
|---|---|---|
| trend | SOLUSDT (49.76) > BTCUSDT (44.42) > ETHUSDT (41.91) | trend strength = ADX(14, daily), highest first; direction shown separately |
| momentum | SOLUSDT (-2.35) > ETHUSDT (-2.40) > BTCUSDT (-2.69) | 30d log return / (RV30 x sqrt(30/365)), highest first |
| volume | BTCUSDT (2.07) > ETHUSDT (1.88) > SOLUSDT (1.58) | last 24h USDT volume / prior 30d daily average, highest first |
| funding | ETHUSDT (27.40) > BTCUSDT (14.18) > SOLUSDT (9.25) | |30d funding percentile over 4y - 50|, most extreme first |
| open_interest | ETHUSDT (4.37) > SOLUSDT (-1.84) > BTCUSDT (-2.34) | 30d change in open interest (contracts), largest build first |
| volatility | SOLUSDT (65.40) > ETHUSDT (61.24) > BTCUSDT (46.10) | 30d realised vol (annualised), highest first |

Rank 1 means 'most of that property', not 'best to trade'. With all three in the same macro move the differences between them are small next to the move itself.

## Trend

| market | last close | 7d % | 30d % | 90d % | 200d % | vs 50d MA % | vs 200d MA % | 50d vs 200d MA % | 200d MA 30d slope % | ADX14d | ER30 | from 365d high % | above 365d low % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 58,915.00 | -13.7 | -29.9 | -23.6 | -26.2 | -24.2 | -18.8 | +7.1 | -0.3 | 44.4 | 0.62 | -39.8 (high 97,932.10 on 2026-01-14) | +2.1 |
| ETHUSDT | 1,622.03 | -15.9 | -34.4 | -33.0 | -30.9 | -29.1 | -23.8 | +7.4 | -1.1 | 41.9 | 0.57 | -52.9 (high 3,446.12 on 2025-12-10) | +7.9 |
| SOLUSDT | 68.82 | -12.0 | -35.6 | -26.7 | -18.1 | -29.2 | -22.3 | +9.8 | +0.6 | 49.8 | 0.60 | -53.8 (high 148.80 on 2026-01-13) | +14.6 |

Returns use daily closes (00:00 UTC). ER30 = Kaufman efficiency ratio over 30 daily closes (net move / path length; about 0.18 is what a driftless random walk gives).

## Momentum and volatility

| market | 30d mom / vol | RV 7d ann % | RV 7d pctl (1y) | RV 30d ann % | RV 30d pctl (1y) | RV7 / RV30 | ATR14d % of price |
|---|---|---|---|---|---|---|---|
| BTCUSDT | -2.69 | 66.1 | 95 | 46.1 | 70 | 1.43 | 4.80 |
| ETHUSDT | -2.40 | 84.2 | 93 | 61.2 | 68 | 1.37 | 6.80 |
| SOLUSDT | -2.35 | 89.6 | 90 | 65.4 | 62 | 1.37 | 6.91 |

Realised vol is the standard deviation of 1h log returns, annualised with sqrt(8760). Percentile = share of the last 365 daily readings at or below today's.

## Volume (USDT quote volume)

| market | last 24h | 7d avg/day | 30d avg/day | 24h vs 30d avg | 7d vs 30d avg | 30d vs 365d avg | taker-buy share 24h % | taker-buy share 7d % |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | $24.79bn | $16.30bn | $11.96bn | 2.07x | 1.36x | 1.01x | 48.6 | 49.1 |
| ETHUSDT | $15.08bn | $10.32bn | $8.00bn | 1.88x | 1.29x | 0.84x | 47.8 | 49.0 |
| SOLUSDT | $2.39bn | $1.90bn | $1.51bn | 1.58x | 1.26x | 0.73x | 47.8 | 49.4 |

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
| BTCUSDT | $5.66bn | -2.1 | -13.7 | -2.3 | -29.9 | +27.6 | 26 | 0.47 |
| ETHUSDT | $3.82bn | +1.6 | -15.9 | +4.4 | -34.4 | +38.8 | 63 | 0.48 |
| SOLUSDT | $0.58bn | -9.7 | -12.0 | -1.8 | -35.6 | +33.8 | 41 | 0.38 |

OI / price state:

- **BTCUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI down (deleveraging, positions closed).
- **ETHUSDT** 7d: price down / OI up (new positions opened into the decline); 30d: price down / OI up (new positions opened into the decline).
- **SOLUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI down (deleveraging, positions closed).

OI in contracts is used so a falling price does not mechanically shrink it. A one-sided reading is not possible from OI alone: every contract has a long and a short.

## Positioning ratios (Binance accounts, context only)

| market | all-accounts long/short | pctl (1y) | top-trader long/short (by position) | pctl (1y) |
|---|---|---|---|---|
| BTCUSDT | 3.48 | 100 | 1.67 | 58 |
| ETHUSDT | 2.85 | 84 | 1.62 | 63 |
| SOLUSDT | 3.89 | 91 | 1.63 | 35 |

These count Binance accounts only and say nothing about size on other venues. Not used in any setup.

## Reading (risk view, no forecast)

Order of figures: BTC / ETH / SOL.

- **Regime:** all three carry the same label, **volatile**. 7-day realised vol sits at the 95 / 93 / 90 percentile of the past year and ADX is 44.4 / 41.9 / 49.8 with direction down / down / down. Three markets with one label are one bet, not three.
- **Trend:** price is below both the 50-day and the 200-day average in all three (-24.2 / -29.1 / -29.2% and -18.8 / -23.8 / -22.3%). The 50-day average is still above the 200-day (+7.1 / +7.4 / +9.8%): the averages have not caught up with the fall, so slow trend systems are late by construction here.
- **Size of the move:** 7-day return -13.7 / -15.9 / -12.0%, 30-day -29.9 / -34.4 / -35.6%; -39.8 / -52.9 / -53.8% from the 365-day high. The 7-day lows were BTC 57,700.10 (bar opening 2026-11-20 19:00 UTC), ETH 1,579.68 (bar opening 2026-11-20 19:00 UTC), SOL 67.08 (bar opening 2026-11-20 21:00 UTC); the last close is +2.1 / +2.7 / +2.6% above them.
- **Volatility:** 7-day realised vol 66 / 84 / 90% annualised, 1.43 / 1.37 / 1.37 times the 30-day figure. Daily ATR is 4.8 / 6.8 / 6.9% of price: a stop sized for last month is too tight for this week.
- **Volume:** the last 24h traded 2.07 / 1.88 / 1.58 times the prior 30-day daily average. Extreme.
- **Open interest:** 7-day change in contracts -2.1 / +1.6 / -9.7% against price -13.7 / -15.9 / -12.0%. BTC, SOL: positions were closed into the fall (deleveraging). ETH: open interest ROSE while price fell, i.e. new positions were opened into the decline. That is leverage that has not been flushed yet; it can unwind in either direction.
- **Funding:** not available for the sell-off. The archive stops 2026-10-31 16:00 UTC, 20 days before the last candle. At that cut-off the 30-day average was +4.7 / +3.2 / +4.4% annualised (36 / 23 / 59 percentile of 4 years): unremarkable. Whether funding has since turned negative is unknown from this data.
- **Positioning (Binance accounts only):** long/short account ratio 3.48 / 2.85 / 3.89, at the 100 / 84 / 91 percentile of the past year (BTC, SOL in the top decile: an unusually large share of accounts is net long into the decline). The ratio counts accounts, not size. Context, not a signal.
- **What this does NOT say:** it does not say the low is in, and it does not say the fall continues. It says volatility is high, the three markets are moving as one, and the trend filters most setups rely on are pointing down or lagging.
- **For the setup list:** by the rule above only the setups tagged `volatile` are in their intended regime today. That is a switch for Lab to test, not a recommendation; nothing here has been backtested.
