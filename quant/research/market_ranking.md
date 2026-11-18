# Market ranking - BTCUSDT / ETHUSDT / SOLUSDT (Binance USDT-M perps)

- **Data timestamp:** last 1h bar opens 2026-11-17 23:00:00+00:00 and closes **2026-11-18 00:00:00+00:00**. Daily figures use complete UTC days up to 2026-11-17.
- **Source:** https://data.binance.vision/data/futures/um (official Binance public archive), fetched 2026-11-18T07:56:12.952067+00:00, loaded only through `strategies.data.load`. Produced by `research/ranking.py`.
- **Funding is stale:** the funding archive ends **2026-10-31 16:00:00+00:00**, 17.3 days before the last candle. Every funding figure below is as of that date, NOT as of the last bar. Do not read it as current.
- **Open interest** is cleaned before use: hours where the archive prints 0 are dropped and a trailing 3h median removes one-bar partial prints (zero-OI hours in the archive: BTCUSDT 33, ETHUSDT 13, SOLUSDT 12).
- Descriptive statistics only. Nothing here is a forecast and no P&L was computed.
- **Survivorship:** none of the three symbols was delisted, but they were picked today knowing they survived and stayed liquid. A universe chosen in 2022 could have held names that later died, so any history on these three flatters what a 2022 trader would have seen.

## Regime label

Rule (fixed by convention, trailing data only, evaluated on daily bars):

1. **volatile** if 7-day realised vol is at or above the 80th percentile of its own trailing 365 days;
2. else **trending** if ADX(14, daily) >= 25 (direction from +DI vs -DI);
3. else **ranging**.

| market | regime now | RV7 pctl (1y) | ADX14d | direction | days in this label | share of days since 2022-11 (trend/range/vol) |
|---|---|---|---|---|---|---|
| BTCUSDT | **volatile** | 89 | 36.8 | down (+DI 6.7 / -DI 38.0) | 3 | trending 38% / ranging 43% / volatile 18% |
| ETHUSDT | **volatile** | 86 | 36.5 | down (+DI 9.1 / -DI 35.7) | 4 | trending 43% / ranging 38% / volatile 19% |
| SOLUSDT | **volatile** | 86 | 47.5 | down (+DI 9.7 / -DI 34.7) | 3 | trending 38% / ranging 46% / volatile 15% |

The label is a coarse switch, not a signal. Vol takes precedence: a market that is falling hard is labelled volatile even when ADX also says trending, because stop distances and gap risk are what change first.

## Rank by dimension

| dimension | rank 1 > 2 > 3 (value) | rule |
|---|---|---|
| trend | SOLUSDT (47.46) > BTCUSDT (36.85) > ETHUSDT (36.46) | trend strength = ADX(14, daily), highest first; direction shown separately |
| momentum | ETHUSDT (-1.68) > SOLUSDT (-1.83) > BTCUSDT (-1.93) | 30d log return / (RV30 x sqrt(30/365)), highest first |
| volume | BTCUSDT (1.20) > ETHUSDT (1.20) > SOLUSDT (1.14) | last 24h USDT volume / prior 30d daily average, highest first |
| funding | ETHUSDT (27.40) > BTCUSDT (14.18) > SOLUSDT (9.25) | |30d funding percentile over 4y - 50|, most extreme first |
| open_interest | ETHUSDT (3.47) > BTCUSDT (-0.94) > SOLUSDT (-5.77) | 30d change in open interest (contracts), largest build first |
| volatility | SOLUSDT (61.90) > ETHUSDT (57.64) > BTCUSDT (42.41) | 30d realised vol (annualised), highest first |

Rank 1 means 'most of that property', not 'best to trade'. With all three in the same macro move the differences between them are small next to the move itself.

## Trend

| market | last close | 7d % | 30d % | 90d % | 200d % | vs 50d MA % | vs 200d MA % | 50d vs 200d MA % | 200d MA 30d slope % | ADX14d | ER30 | from 365d high % | above 365d low % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 64,921.00 | -12.0 | -20.9 | -6.3 | -17.0 | -17.9 | -10.8 | +8.5 | +0.4 | 36.8 | 0.46 | -33.7 (high 97,932.10 on 2026-01-14) | +12.4 |
| ETHUSDT | 1,824.32 | -11.1 | -24.3 | -19.0 | -20.5 | -22.2 | -14.7 | +9.6 | -0.4 | 36.5 | 0.43 | -47.1 (high 3,446.12 on 2025-12-10) | +21.3 |
| SOLUSDT | 75.15 | -9.8 | -27.8 | -12.0 | -10.2 | -24.9 | -15.3 | +12.7 | +1.3 | 47.5 | 0.48 | -49.5 (high 148.80 on 2026-01-13) | +25.2 |

Returns use daily closes (00:00 UTC). ER30 = Kaufman efficiency ratio over 30 daily closes (net move / path length; about 0.18 is what a driftless random walk gives).

## Momentum and volatility

| market | 30d mom / vol | RV 7d ann % | RV 7d pctl (1y) | RV 30d ann % | RV 30d pctl (1y) | RV7 / RV30 | ATR14d % of price |
|---|---|---|---|---|---|---|---|
| BTCUSDT | -1.93 | 58.1 | 89 | 42.4 | 57 | 1.37 | 4.07 |
| ETHUSDT | -1.68 | 74.6 | 86 | 57.6 | 58 | 1.29 | 5.86 |
| SOLUSDT | -1.83 | 81.8 | 86 | 61.9 | 55 | 1.32 | 6.39 |

Realised vol is the standard deviation of 1h log returns, annualised with sqrt(8760). Percentile = share of the last 365 daily readings at or below today's.

## Volume (USDT quote volume)

| market | last 24h | 7d avg/day | 30d avg/day | 24h vs 30d avg | 7d vs 30d avg | 30d vs 365d avg | taker-buy share 24h % | taker-buy share 7d % |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | $13.88bn | $14.39bn | $11.54bn | 1.20x | 1.25x | 0.97x | 50.2 | 48.7 |
| ETHUSDT | $9.43bn | $9.36bn | $7.87bn | 1.20x | 1.19x | 0.82x | 48.8 | 49.2 |
| SOLUSDT | $1.71bn | $1.80bn | $1.50bn | 1.14x | 1.20x | 0.72x | 50.1 | 49.2 |

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
| BTCUSDT | $6.00bn | -7.2 | -12.0 | -0.9 | -20.9 | +20.0 | 31 | 0.52 |
| ETHUSDT | $4.28bn | +3.1 | -11.1 | +3.5 | -24.3 | +27.7 | 58 | 0.54 |
| SOLUSDT | $0.63bn | -13.1 | -9.8 | -5.8 | -27.8 | +22.0 | 26 | 0.42 |

OI / price state:

- **BTCUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI down (deleveraging, positions closed).
- **ETHUSDT** 7d: price down / OI up (new positions opened into the decline); 30d: price down / OI up (new positions opened into the decline).
- **SOLUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI down (deleveraging, positions closed).

OI in contracts is used so a falling price does not mechanically shrink it. A one-sided reading is not possible from OI alone: every contract has a long and a short.

## Positioning ratios (Binance accounts, context only)

| market | all-accounts long/short | pctl (1y) | top-trader long/short (by position) | pctl (1y) |
|---|---|---|---|---|
| BTCUSDT | 2.78 | 95 | 1.80 | 62 |
| ETHUSDT | 2.57 | 68 | 1.68 | 68 |
| SOLUSDT | 3.37 | 80 | 1.65 | 36 |

These count Binance accounts only and say nothing about size on other venues. Not used in any setup.

## Reading (risk view, no forecast)

Order of figures: BTC / ETH / SOL.

- **Regime:** all three carry the same label, **volatile**. 7-day realised vol sits at the 89 / 86 / 86 percentile of the past year and ADX is 36.8 / 36.5 / 47.5 with direction down / down / down. Three markets with one label are one bet, not three.
- **Trend:** price is below both the 50-day and the 200-day average in all three (-17.9 / -22.2 / -24.9% and -10.8 / -14.7 / -15.3%). The 50-day average is still above the 200-day (+8.5 / +9.6 / +12.7%): the averages have not caught up with the fall, so slow trend systems are late by construction here.
- **Size of the move:** 7-day return -12.0 / -11.1 / -9.8%, 30-day -20.9 / -24.3 / -27.8%; -33.7 / -47.1 / -49.5% from the 365-day high. The 7-day lows were BTC 61,956.30 (bar opening 2026-11-15 01:00 UTC), ETH 1,710.01 (bar opening 2026-11-15 01:00 UTC), SOL 67.35 (bar opening 2026-11-15 01:00 UTC); the last close is +4.8 / +6.7 / +11.6% above them.
- **Volatility:** 7-day realised vol 58 / 75 / 82% annualised, 1.37 / 1.29 / 1.32 times the 30-day figure. Daily ATR is 4.1 / 5.9 / 6.4% of price: a stop sized for last month is too tight for this week.
- **Volume:** the last 24h traded 1.20 / 1.20 / 1.14 times the prior 30-day daily average. Elevated, not extreme.
- **Open interest:** 7-day change in contracts -7.2 / +3.1 / -13.1% against price -12.0 / -11.1 / -9.8%. BTC, SOL: positions were closed into the fall (deleveraging). ETH: open interest ROSE while price fell, i.e. new positions were opened into the decline. That is leverage that has not been flushed yet; it can unwind in either direction.
- **Funding:** not available for the sell-off. The archive stops 2026-10-31 16:00 UTC, 17 days before the last candle. At that cut-off the 30-day average was +4.7 / +3.2 / +4.4% annualised (36 / 23 / 59 percentile of 4 years): unremarkable. Whether funding has since turned negative is unknown from this data.
- **Positioning (Binance accounts only):** long/short account ratio 2.78 / 2.57 / 3.37, at the 95 / 68 / 80 percentile of the past year (BTC in the top decile: an unusually large share of accounts is net long into the decline). The ratio counts accounts, not size. Context, not a signal.
- **What this does NOT say:** it does not say the low is in, and it does not say the fall continues. It says volatility is high, the three markets are moving as one, and the trend filters most setups rely on are pointing down or lagging.
- **For the setup list:** by the rule above only the setups tagged `volatile` are in their intended regime today. That is a switch for Lab to test, not a recommendation; nothing here has been backtested.
