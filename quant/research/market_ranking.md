# Market ranking - BTCUSDT / ETHUSDT / SOLUSDT (Binance USDT-M perps)

- **Data timestamp:** last 1h bar opens 2026-11-22 23:00:00+00:00 and closes **2026-11-23 00:00:00+00:00**. Daily figures use complete UTC days up to 2026-11-22.
- **Source:** https://data.binance.vision/data/futures/um (official Binance public archive), fetched 2026-11-24T01:53:32.772363+00:00, loaded only through `strategies.data.load`. Produced by `research/ranking.py`.
- **Funding is stale:** the funding archive ends **2026-10-31 16:00:00+00:00**, 22.3 days before the last candle. Every funding figure below is as of that date, NOT as of the last bar. Do not read it as current.
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
| BTCUSDT | **volatile** | 98 | 49.1 | down (+DI 5.6 / -DI 39.4) | 8 | trending 38% / ranging 43% / volatile 18% |
| ETHUSDT | **volatile** | 91 | 45.6 | down (+DI 7.0 / -DI 35.1) | 9 | trending 43% / ranging 38% / volatile 20% |
| SOLUSDT | **volatile** | 88 | 51.9 | down (+DI 7.3 / -DI 34.3) | 8 | trending 38% / ranging 46% / volatile 16% |

The label is a coarse switch, not a signal. Vol takes precedence: a market that is falling hard is labelled volatile even when ADX also says trending, because stop distances and gap risk are what change first.

## Rank by dimension

| dimension | rank 1 > 2 > 3 (value) | rule |
|---|---|---|
| trend | SOLUSDT (51.94) > BTCUSDT (49.06) > ETHUSDT (45.64) | trend strength = ADX(14, daily), highest first; direction shown separately |
| momentum | ETHUSDT (-1.74) > SOLUSDT (-1.90) > BTCUSDT (-1.90) | 30d log return / (RV30 x sqrt(30/365)), highest first |
| volume | BTCUSDT (0.96) > ETHUSDT (0.74) > SOLUSDT (0.65) | last 24h USDT volume / prior 30d daily average, highest first |
| funding | ETHUSDT (27.40) > BTCUSDT (14.18) > SOLUSDT (9.25) | |30d funding percentile over 4y - 50|, most extreme first |
| open_interest | ETHUSDT (1.21) > BTCUSDT (0.54) > SOLUSDT (-6.48) | 30d change in open interest (contracts), largest build first |
| volatility | SOLUSDT (68.84) > ETHUSDT (65.19) > BTCUSDT (51.61) | 30d realised vol (annualised), highest first |

Rank 1 means 'most of that property', not 'best to trade'. With all three in the same macro move the differences between them are small next to the move itself.

## Trend

| market | last close | 7d % | 30d % | 90d % | 200d % | vs 50d MA % | vs 200d MA % | 50d vs 200d MA % | 200d MA 30d slope % | ADX14d | ER30 | from 365d high % | above 365d low % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| BTCUSDT | 61,236.90 | -6.2 | -24.5 | -22.4 | -24.8 | -20.2 | -15.4 | +6.0 | -0.7 | 49.1 | 0.49 | -37.5 (high 97,932.10 on 2026-01-14) | +11.1 |
| ETHUSDT | 1,723.33 | -6.0 | -27.8 | -30.5 | -26.7 | -23.3 | -18.8 | +5.9 | -1.5 | 45.6 | 0.44 | -50.0 (high 3,446.12 on 2025-12-10) | +14.6 |
| SOLUSDT | 70.69 | -2.8 | -31.2 | -28.6 | -20.7 | -25.8 | -20.0 | +7.8 | +0.2 | 51.9 | 0.52 | -52.5 (high 148.80 on 2026-01-13) | +17.8 |

Returns use daily closes (00:00 UTC). ER30 = Kaufman efficiency ratio over 30 daily closes (net move / path length; about 0.18 is what a driftless random walk gives).

## Momentum and volatility

| market | 30d mom / vol | RV 7d ann % | RV 7d pctl (1y) | RV 30d ann % | RV 30d pctl (1y) | RV7 / RV30 | ATR14d % of price |
|---|---|---|---|---|---|---|---|
| BTCUSDT | -1.90 | 71.6 | 98 | 51.6 | 79 | 1.39 | 4.95 |
| ETHUSDT | -1.74 | 81.5 | 91 | 65.2 | 73 | 1.25 | 6.57 |
| SOLUSDT | -1.90 | 82.9 | 88 | 68.8 | 68 | 1.20 | 6.66 |

Realised vol is the standard deviation of 1h log returns, annualised with sqrt(8760). Percentile = share of the last 365 daily readings at or below today's.

## Volume (USDT quote volume)

| market | last 24h | 7d avg/day | 30d avg/day | 24h vs 30d avg | 7d vs 30d avg | 30d vs 365d avg | taker-buy share 24h % | taker-buy share 7d % |
|---|---|---|---|---|---|---|---|---|
| BTCUSDT | $12.03bn | $16.05bn | $12.59bn | 0.96x | 1.27x | 1.07x | 51.1 | 49.5 |
| ETHUSDT | $6.12bn | $9.27bn | $8.29bn | 0.74x | 1.12x | 0.88x | 50.2 | 49.4 |
| SOLUSDT | $1.01bn | $1.64bn | $1.56bn | 0.65x | 1.05x | 0.77x | 51.1 | 49.6 |

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
| BTCUSDT | $5.70bn | -0.6 | -6.2 | +0.5 | -24.5 | +25.1 | 40 | 0.45 |
| ETHUSDT | $4.04bn | +1.9 | -6.0 | +1.2 | -27.8 | +29.0 | 43 | 0.49 |
| SOLUSDT | $0.56bn | -11.8 | -2.8 | -6.5 | -31.2 | +24.7 | 24 | 0.36 |

OI / price state:

- **BTCUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI up (new positions opened into the decline).
- **ETHUSDT** 7d: price down / OI up (new positions opened into the decline); 30d: price down / OI up (new positions opened into the decline).
- **SOLUSDT** 7d: price down / OI down (deleveraging, positions closed); 30d: price down / OI down (deleveraging, positions closed).

OI in contracts is used so a falling price does not mechanically shrink it. A one-sided reading is not possible from OI alone: every contract has a long and a short.

## Positioning ratios (Binance accounts, context only)

| market | all-accounts long/short | pctl (1y) | top-trader long/short (by position) | pctl (1y) |
|---|---|---|---|---|
| BTCUSDT | 2.10 | 76 | 1.68 | 58 |
| ETHUSDT | 1.86 | 24 | 1.64 | 64 |
| SOLUSDT | 3.46 | 82 | 1.78 | 46 |

These count Binance accounts only and say nothing about size on other venues. Not used in any setup.

## Reading (risk view, no forecast)

Order of figures: BTC / ETH / SOL.

- **Regime:** all three carry the same label, **volatile**. 7-day realised vol sits at the 98 / 91 / 88 percentile of the past year and ADX is 49.1 / 45.6 / 51.9 with direction down / down / down. Three markets with one label are one bet, not three.
- **Trend:** price is below both the 50-day and the 200-day average in all three (-20.2 / -23.3 / -25.8% and -15.4 / -18.8 / -20.0%). The 50-day average is still above the 200-day (+6.0 / +5.9 / +7.8%): the averages have not caught up with the fall, so slow trend systems are late by construction here.
- **Size of the move:** 7-day return -6.2 / -6.0 / -2.8%, 30-day -24.5 / -27.8 / -31.2%; -37.5 / -50.0 / -52.5% from the 365-day high. The 7-day lows were BTC 55,126.70 (bar opening 2026-11-21 06:00 UTC), ETH 1,536.10 (bar opening 2026-11-21 04:00 UTC), SOL 64.83 (bar opening 2026-11-21 06:00 UTC); the last close is +11.1 / +12.2 / +9.0% above them.
- **Volatility:** 7-day realised vol 72 / 82 / 83% annualised, 1.39 / 1.25 / 1.20 times the 30-day figure. Daily ATR is 5.0 / 6.6 / 6.7% of price: a stop sized for last month is too tight for this week.
- **Volume:** the last 24h traded 0.96 / 0.74 / 0.65 times the prior 30-day daily average. Normal.
- **Open interest:** 7-day change in contracts -0.6 / +1.9 / -11.8% against price -6.2 / -6.0 / -2.8%. BTC, SOL: positions were closed into the fall (deleveraging). ETH: open interest ROSE while price fell, i.e. new positions were opened into the decline. That is leverage that has not been flushed yet; it can unwind in either direction.
- **Funding:** not available for the sell-off. The archive stops 2026-10-31 16:00 UTC, 22 days before the last candle. At that cut-off the 30-day average was +4.7 / +3.2 / +4.4% annualised (36 / 23 / 59 percentile of 4 years): unremarkable. Whether funding has since turned negative is unknown from this data.
- **Positioning (Binance accounts only):** long/short account ratio 2.10 / 1.86 / 3.46, at the 76 / 24 / 82 percentile of the past year. The ratio counts accounts, not size. Context, not a signal.
- **What this does NOT say:** it does not say the low is in, and it does not say the fall continues. It says volatility is high, the three markets are moving as one, and the trend filters most setups rely on are pointing down or lagging.
- **For the setup list:** by the rule above only the setups tagged `volatile` are in their intended regime today. That is a switch for Lab to test, not a recommendation; nothing here has been backtested.
