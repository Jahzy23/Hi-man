# Market ranking - BTCUSDT / ETHUSDT / SOLUSDT (Binance USDT-M perps)

- **Data timestamp:** last 1h bar opens 2026-11-23 23:00:00+00:00 and closes **2026-11-24 00:00:00+00:00**. Daily figures use complete UTC days up to 2026-11-23.
- **Source:** https://data.binance.vision/data/futures/um (official Binance public archive), fetched 2026-11-25T01:53:34.661715+00:00, loaded only through `strategies.data.load`. Produced by `research/ranking.py`.
- **Funding is stale:** the funding archive ends **2026-10-31 16:00:00+00:00**, 23.3 days before the last candle. Every funding figure below is as of that date, NOT as of the last bar. Do not read it as current.
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
| BTCUSDT | **volatile** | 98 | 49.1 | down (+DI 11.8 / -DI 35.6) | 9 | trending 38% / ranging 43% / volatile 19% |
| ETHUSDT | **volatile** | 96 | 45.3 | down (+DI 13.3 / -DI 31.9) | 10 | trending 43% / ranging 38% / volatile 20% |
| SOLUSDT | **volatile** | 93 | 50.3 | down (+DI 16.6 / -DI 30.0) | 9 | trending 38% / ranging 46% / volatile 16% |

The label is a coarse switch, not a signal. Vol takes precedence: a market that is falling hard is labelled volatile even when ADX also says trending, because stop distances and gap risk are what change first.

## Rank by dimension

| dimension | rank 1 > 2 > 3 (value) | rule |
|---|---|---|
| trend | SOLUSDT (50.29) > BTCUSDT (49.14) > ETHUSDT (45.33) | trend strength = ADX(14, daily), highest first; direction shown separately |
| momentum | ETHUSDT (-1.65) > SOLUSDT (-1.70) > BTCUSDT (-1.79) | 30d log return / (RV30 x sqrt(30/365)), highest first |
| volume | BTCUSDT (1.56) > SOLUSDT (1.45) > ETHUSDT (1.32) | last 24h USDT volume / prior 30d daily average, highest first |
| funding | ETHUSDT (27.40) > BTCUSDT (14.18) > SOLUSDT (9.25) | |30d funding percentile over 4y - 50|, most extreme first |
| open_interest | ETHUSDT (0.78) > BTCUSDT (-2.43) > SOLUSDT (-10.11) | 30d change in open interest (contracts), largest build first |
| volatility | SOLUSDT (72.39) > ETHUSDT (67.92) > BTCUSDT (54.12) | 30d realised vol (annualised), highest first |

Rank 1 means 'most of that property', not 'best to trade'. With all three in the same macro move the differences between them are small next to the move itself.

## Trend

| market | last close | 7d % | 30d % | 90d % | 200d % | vs 50d MA % | vs 200d MA % | 50d vs 200d MA % | 200d MA 30d slope % | ADX14d | ER30 | from 365d high % | above 365d low % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 61,239.90 | -5.6 | -24.2 | -22.0 | -23.4 | -19.7 | -15.2 | +5.5 | -0.9 | 49.1 | 0.49 | -37.5 (high 97,932.10 on 2026-01-14) | +11.1 |
| ETHUSDT | 1,709.85 | -6.0 | -27.5 | -30.0 | -25.3 | -23.2 | -19.3 | +5.1 | -1.7 | 45.3 | 0.43 | -50.4 (high 3,446.12 on 2025-12-10) | +13.7 |
| SOLUSDT | 71.98 | +0.6 | -29.7 | -25.5 | -18.6 | -23.6 | -18.5 | +6.8 | -0.0 | 50.3 | 0.49 | -51.6 (high 148.80 on 2026-01-13) | +19.9 |

Returns use daily closes (00:00 UTC). ER30 = Kaufman efficiency ratio over 30 daily closes (net move / path length; about 0.18 is what a driftless random walk gives).

## Momentum and volatility

| market | 30d mom / vol | RV 7d ann % | RV 7d pctl (1y) | RV 30d ann % | RV 30d pctl (1y) | RV7 / RV30 | ATR14d % of price |
|---|---|---|---|---|---|---|---|
| BTCUSDT | -1.79 | 77.2 | 98 | 54.1 | 89 | 1.43 | 5.09 |
| ETHUSDT | -1.65 | 88.5 | 96 | 67.9 | 78 | 1.30 | 6.77 |
| SOLUSDT | -1.70 | 92.5 | 93 | 72.4 | 75 | 1.28 | 6.94 |

Realised vol is the standard deviation of 1h log returns, annualised with sqrt(8760). Percentile = share of the last 365 daily readings at or below today's.

## Volume (USDT quote volume)

| market | last 24h | 7d avg/day | 30d avg/day | 24h vs 30d avg | 7d vs 30d avg | 30d vs 365d avg | taker-buy share 24h % | taker-buy share 7d % |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | $19.81bn | $17.19bn | $12.69bn | 1.56x | 1.35x | 1.08x | 51.0 | 49.8 |
| ETHUSDT | $10.97bn | $9.74bn | $8.30bn | 1.32x | 1.17x | 0.88x | 51.1 | 49.6 |
| SOLUSDT | $2.25bn | $1.73bn | $1.55bn | 1.45x | 1.12x | 0.77x | 50.5 | 49.7 |

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
| BTCUSDT | $5.72bn | +0.5 | -5.6 | -2.4 | -24.2 | +21.8 | 26 | 0.45 |
| ETHUSDT | $3.93bn | +0.8 | -6.0 | +0.8 | -27.5 | +28.3 | 40 | 0.47 |
| SOLUSDT | $0.56bn | -11.2 | +0.6 | -10.1 | -29.7 | +19.5 | 13 | 0.36 |

OI / price state:

- **BTCUSDT** 7d: price down / OI up (new positions opened into the decline); 30d: price down / OI down (deleveraging, positions closed).
- **ETHUSDT** 7d: price down / OI up (new positions opened into the decline); 30d: price down / OI up (new positions opened into the decline).
- **SOLUSDT** 7d: price up / OI down (short covering, no new money); 30d: price down / OI down (deleveraging, positions closed).

OI in contracts is used so a falling price does not mechanically shrink it. A one-sided reading is not possible from OI alone: every contract has a long and a short.

## Positioning ratios (Binance accounts, context only)

| market | all-accounts long/short | pctl (1y) | top-trader long/short (by position) | pctl (1y) |
|---|---|---|---|---|
| BTCUSDT | 2.75 | 95 | 1.57 | 51 |
| ETHUSDT | 2.34 | 53 | 1.70 | 70 |
| SOLUSDT | 3.95 | 93 | 1.97 | 60 |

These count Binance accounts only and say nothing about size on other venues. Not used in any setup.

## Reading (risk view, no forecast)

Order of figures: BTC / ETH / SOL.

- **Regime:** all three carry the same label, **volatile**. 7-day realised vol sits at the 98 / 96 / 93 percentile of the past year and ADX is 49.1 / 45.3 / 50.3 with direction down / down / down. Three markets with one label are one bet, not three.
- **Trend:** price is below both the 50-day and the 200-day average in all three (-19.7 / -23.2 / -23.6% and -15.2 / -19.3 / -18.5%). The 50-day average is still above the 200-day (+5.5 / +5.1 / +6.8%): the averages have not caught up with the fall, so slow trend systems are late by construction here.
- **Size of the move:** 7-day return -5.6 / -6.0 / +0.6%, 30-day -24.2 / -27.5 / -29.7%; -37.5 / -50.4 / -51.6% from the 365-day high. The 7-day lows were BTC 55,126.70 (bar opening 2026-11-21 06:00 UTC), ETH 1,536.10 (bar opening 2026-11-21 04:00 UTC), SOL 64.83 (bar opening 2026-11-21 06:00 UTC); the last close is +11.1 / +11.3 / +11.0% above them.
- **Volatility:** 7-day realised vol 77 / 88 / 93% annualised, 1.43 / 1.30 / 1.28 times the 30-day figure. Daily ATR is 5.1 / 6.8 / 6.9% of price: a stop sized for last month is too tight for this week.
- **Volume:** the last 24h traded 1.56 / 1.32 / 1.45 times the prior 30-day daily average. Elevated, not extreme.
- **Open interest:** 7-day change in contracts +0.5 / +0.8 / -11.2% against price -5.6 / -6.0 / +0.6%. SOL: positions were closed into the fall (deleveraging). BTC, ETH: open interest ROSE while price fell, i.e. new positions were opened into the decline. That is leverage that has not been flushed yet; it can unwind in either direction.
- **Funding:** not available for the sell-off. The archive stops 2026-10-31 16:00 UTC, 23 days before the last candle. At that cut-off the 30-day average was +4.7 / +3.2 / +4.4% annualised (36 / 23 / 59 percentile of 4 years): unremarkable. Whether funding has since turned negative is unknown from this data.
- **Positioning (Binance accounts only):** long/short account ratio 2.75 / 2.34 / 3.95, at the 95 / 53 / 93 percentile of the past year (BTC, SOL in the top decile: an unusually large share of accounts is net long into the decline). The ratio counts accounts, not size. Context, not a signal.
- **What this does NOT say:** it does not say the low is in, and it does not say the fall continues. It says volatility is high, the three markets are moving as one, and the trend filters most setups rely on are pointing down or lagging.
- **For the setup list:** by the rule above only the setups tagged `volatile` are in their intended regime today. That is a switch for Lab to test, not a recommendation; nothing here has been backtested.
