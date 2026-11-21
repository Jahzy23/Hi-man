# Market ranking - BTCUSDT / ETHUSDT / SOLUSDT (Binance USDT-M perps)

- **Data timestamp:** last 1h bar opens 2026-11-19 23:00:00+00:00 and closes **2026-11-20 00:00:00+00:00**. Daily figures use complete UTC days up to 2026-11-19.
- **Source:** https://data.binance.vision/data/futures/um (official Binance public archive), fetched 2026-11-21T01:53:51.000089+00:00, loaded only through `strategies.data.load`. Produced by `research/ranking.py`.
- **Funding is stale:** the funding archive ends **2026-10-31 16:00:00+00:00**, 19.3 days before the last candle. Every funding figure below is as of that date, NOT as of the last bar. Do not read it as current.
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
| BTCUSDT | **volatile** | 90 | 41.8 | down (+DI 5.8 / -DI 37.4) | 5 | trending 38% / ranging 43% / volatile 18% |
| ETHUSDT | **volatile** | 89 | 39.8 | down (+DI 8.0 / -DI 32.7) | 6 | trending 43% / ranging 38% / volatile 19% |
| SOLUSDT | **volatile** | 89 | 48.7 | down (+DI 8.7 / -DI 31.3) | 5 | trending 38% / ranging 46% / volatile 16% |

The label is a coarse switch, not a signal. Vol takes precedence: a market that is falling hard is labelled volatile even when ADX also says trending, because stop distances and gap risk are what change first.

## Rank by dimension

| dimension | rank 1 > 2 > 3 (value) | rule |
|---|---|---|
| trend | SOLUSDT (48.67) > BTCUSDT (41.80) > ETHUSDT (39.80) | trend strength = ADX(14, daily), highest first; direction shown separately |
| momentum | ETHUSDT (-2.00) > SOLUSDT (-2.10) > BTCUSDT (-2.35) | 30d log return / (RV30 x sqrt(30/365)), highest first |
| volume | BTCUSDT (1.32) > ETHUSDT (1.09) > SOLUSDT (0.94) | last 24h USDT volume / prior 30d daily average, highest first |
| funding | ETHUSDT (27.40) > BTCUSDT (14.18) > SOLUSDT (9.25) | |30d funding percentile over 4y - 50|, most extreme first |
| open_interest | ETHUSDT (7.35) > BTCUSDT (2.38) > SOLUSDT (-3.12) | 30d change in open interest (contracts), largest build first |
| volatility | SOLUSDT (62.57) > ETHUSDT (57.89) > BTCUSDT (43.35) | 30d realised vol (annualised), highest first |

Rank 1 means 'most of that property', not 'best to trade'. With all three in the same macro move the differences between them are small next to the move itself.

## Trend

| market | last close | 7d % | 30d % | 90d % | 200d % | vs 50d MA % | vs 200d MA % | 50d vs 200d MA % | 200d MA 30d slope % | ADX14d | ER30 | from 365d high % | above 365d low % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 61,959.30 | -13.3 | -25.3 | -20.9 | -21.1 | -20.8 | -14.7 | +7.6 | +0.0 | 41.8 | 0.54 | -36.7 (high 97,932.10 on 2026-01-14) | +7.3 |
| ETHUSDT | 1,756.73 | -13.0 | -28.2 | -30.2 | -24.3 | -23.9 | -17.6 | +8.2 | -0.9 | 39.8 | 0.50 | -49.0 (high 3,446.12 on 2025-12-10) | +16.8 |
| SOLUSDT | 73.11 | -10.5 | -31.3 | -22.0 | -12.8 | -25.5 | -17.5 | +10.8 | +0.9 | 48.7 | 0.56 | -50.9 (high 148.80 on 2026-01-13) | +21.8 |

Returns use daily closes (00:00 UTC). ER30 = Kaufman efficiency ratio over 30 daily closes (net move / path length; about 0.18 is what a driftless random walk gives).

## Momentum and volatility

| market | 30d mom / vol | RV 7d ann % | RV 7d pctl (1y) | RV 30d ann % | RV 30d pctl (1y) | RV7 / RV30 | ATR14d % of price |
|---|---|---|---|---|---|---|---|
| BTCUSDT | -2.35 | 60.9 | 90 | 43.4 | 61 | 1.41 | 4.30 |
| ETHUSDT | -2.00 | 77.7 | 89 | 57.9 | 59 | 1.34 | 5.93 |
| SOLUSDT | -2.10 | 85.4 | 89 | 62.6 | 56 | 1.37 | 6.33 |

Realised vol is the standard deviation of 1h log returns, annualised with sqrt(8760). Percentile = share of the last 365 daily readings at or below today's.

## Volume (USDT quote volume)

| market | last 24h | 7d avg/day | 30d avg/day | 24h vs 30d avg | 7d vs 30d avg | 30d vs 365d avg | taker-buy share 24h % | taker-buy share 7d % |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | $15.57bn | $15.54bn | $11.82bn | 1.32x | 1.31x | 1.00x | 49.6 | 48.9 |
| ETHUSDT | $8.74bn | $9.79bn | $7.99bn | 1.09x | 1.23x | 0.83x | 50.2 | 49.3 |
| SOLUSDT | $1.43bn | $1.84bn | $1.52bn | 0.94x | 1.22x | 0.74x | 50.2 | 49.5 |

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
| BTCUSDT | $6.01bn | -4.3 | -13.3 | +2.4 | -25.3 | +27.7 | 49 | 0.51 |
| ETHUSDT | $4.31bn | +5.0 | -13.0 | +7.3 | -28.2 | +35.6 | 71 | 0.54 |
| SOLUSDT | $0.61bn | -13.2 | -10.5 | -3.1 | -31.3 | +28.2 | 36 | 0.40 |

OI / price state:

- **BTCUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI up (new positions opened into the decline).
- **ETHUSDT** 7d: price down / OI up (new positions opened into the decline); 30d: price down / OI up (new positions opened into the decline).
- **SOLUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI down (deleveraging, positions closed).

OI in contracts is used so a falling price does not mechanically shrink it. A one-sided reading is not possible from OI alone: every contract has a long and a short.

## Positioning ratios (Binance accounts, context only)

| market | all-accounts long/short | pctl (1y) | top-trader long/short (by position) | pctl (1y) |
|---|---|---|---|---|
| BTCUSDT | 3.43 | 100 | 1.95 | 69 |
| ETHUSDT | 3.06 | 91 | 1.87 | 77 |
| SOLUSDT | 3.62 | 86 | 1.61 | 33 |

These count Binance accounts only and say nothing about size on other venues. Not used in any setup.

## Reading (risk view, no forecast)

Order of figures: BTC / ETH / SOL.

- **Regime:** all three carry the same label, **volatile**. 7-day realised vol sits at the 90 / 89 / 89 percentile of the past year and ADX is 41.8 / 39.8 / 48.7 with direction down / down / down. Three markets with one label are one bet, not three.
- **Trend:** price is below both the 50-day and the 200-day average in all three (-20.8 / -23.9 / -25.5% and -14.7 / -17.6 / -17.5%). The 50-day average is still above the 200-day (+7.6 / +8.2 / +10.8%): the averages have not caught up with the fall, so slow trend systems are late by construction here.
- **Size of the move:** 7-day return -13.3 / -13.0 / -10.5%, 30-day -25.3 / -28.2 / -31.3%; -36.7 / -49.0 / -50.9% from the 365-day high. The 7-day lows were BTC 61,375.20 (bar opening 2026-11-19 21:00 UTC), ETH 1,710.01 (bar opening 2026-11-15 01:00 UTC), SOL 67.35 (bar opening 2026-11-15 01:00 UTC); the last close is +1.0 / +2.7 / +8.6% above them.
- **Volatility:** 7-day realised vol 61 / 78 / 85% annualised, 1.41 / 1.34 / 1.37 times the 30-day figure. Daily ATR is 4.3 / 5.9 / 6.3% of price: a stop sized for last month is too tight for this week.
- **Volume:** the last 24h traded 1.32 / 1.09 / 0.94 times the prior 30-day daily average. Elevated, not extreme.
- **Open interest:** 7-day change in contracts -4.3 / +5.0 / -13.2% against price -13.3 / -13.0 / -10.5%. BTC, SOL: positions were closed into the fall (deleveraging). ETH: open interest ROSE while price fell, i.e. new positions were opened into the decline. That is leverage that has not been flushed yet; it can unwind in either direction.
- **Funding:** not available for the sell-off. The archive stops 2026-10-31 16:00 UTC, 19 days before the last candle. At that cut-off the 30-day average was +4.7 / +3.2 / +4.4% annualised (36 / 23 / 59 percentile of 4 years): unremarkable. Whether funding has since turned negative is unknown from this data.
- **Positioning (Binance accounts only):** long/short account ratio 3.43 / 3.06 / 3.62, at the 100 / 91 / 86 percentile of the past year (BTC, ETH in the top decile: an unusually large share of accounts is net long into the decline). The ratio counts accounts, not size. Context, not a signal.
- **What this does NOT say:** it does not say the low is in, and it does not say the fall continues. It says volatility is high, the three markets are moving as one, and the trend filters most setups rely on are pointing down or lagging.
- **For the setup list:** by the rule above only the setups tagged `volatile` are in their intended regime today. That is a switch for Lab to test, not a recommendation; nothing here has been backtested.
