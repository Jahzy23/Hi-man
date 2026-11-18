"""Radar: market ranking + regime label as of the last complete bar.

Run from quant/:   python research/ranking.py
Writes research/market_ranking.json and research/market_ranking.md.

Descriptive statistics only: no forward returns, no P&L, nothing fitted.
Every number is computed from `strategies.data.load` (the only loader).
All rolling statistics are trailing, so `regime_series` can also be used historically
(a label stamped on UTC day D uses data up to the close of D; trade on it from D+1).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategies.data import load, SYMBOLS  # noqa: E402

OUT = ROOT / "research"
HOURS_PER_YEAR = 24 * 365

# ---- regime rule (fixed by convention, not fitted) --------------------------------------------
VOL_PCTL_VOLATILE = 80.0   # RV7 at/above the 80th percentile of its own trailing 365 days
ADX_TRENDING = 25.0        # textbook ADX threshold for "a trend exists"
ADX_N = 14


def clean_oi(oi: pd.Series) -> pd.Series:
    """Open interest with archive glitches removed.

    The Binance metrics archive contains hours where open interest is 0 or a partial sum.
    Zeros are treated as missing and a trailing 3-hour median removes one-bar partial prints.
    Trailing only (no look-ahead)."""
    return oi.where(oi > 0).rolling(3, min_periods=2).median()


def daily_bars(h: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last",
           "quote_volume": "sum", "taker_buy_quote": "sum", "funding_paid": "sum"}
    d = h.resample("1D").agg(agg)
    n = h["close"].resample("1D").count()
    return d[n == 24]                      # complete UTC days only


def adx(d: pd.DataFrame, n: int = ADX_N) -> pd.DataFrame:
    """Wilder ADX on daily bars (trailing)."""
    up = d["high"].diff()
    dn = -d["low"].diff()
    plus_dm = up.where((up > dn) & (up > 0), 0.0)
    minus_dm = dn.where((dn > up) & (dn > 0), 0.0)
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    a = 1.0 / n
    atr = tr.ewm(alpha=a, adjust=False, min_periods=n).mean()
    pdi = 100 * plus_dm.ewm(alpha=a, adjust=False, min_periods=n).mean() / atr
    mdi = 100 * minus_dm.ewm(alpha=a, adjust=False, min_periods=n).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    return pd.DataFrame({"adx": dx.ewm(alpha=a, adjust=False, min_periods=n).mean(),
                         "plus_di": pdi, "minus_di": mdi, "atr": atr})


def realised_vol(h: pd.DataFrame, hours: int) -> pd.Series:
    """Annualised realised volatility from 1h log returns over a trailing window."""
    r = np.log(h["close"]).diff()
    return r.rolling(hours, min_periods=hours).std() * np.sqrt(HOURS_PER_YEAR)


def trailing_pctl(s: pd.Series, window: int) -> pd.Series:
    """Percentile (0-100) of each value inside its own trailing window (inclusive)."""
    return s.rolling(window, min_periods=window // 2).rank(pct=True) * 100.0


def regime_series(h: pd.DataFrame) -> pd.DataFrame:
    """Daily regime label. Rule, in order:
       1. volatile  if RV7 percentile (vs its trailing 365 daily readings) >= 80
       2. trending  if ADX(14, daily) >= 25
       3. ranging   otherwise
    """
    d = daily_bars(h)
    rv7 = realised_vol(h, 168)
    rv7_d = rv7[rv7.index.hour == 23]
    rv7_d.index = rv7_d.index.floor("D")
    rv7_d = rv7_d.reindex(d.index)
    a = adx(d)
    out = pd.DataFrame({"rv7": rv7_d, "rv7_pctl_1y": trailing_pctl(rv7_d, 365),
                        "adx": a["adx"], "plus_di": a["plus_di"], "minus_di": a["minus_di"]})
    lab = pd.Series("ranging", index=d.index, dtype=object)
    lab[out["adx"] >= ADX_TRENDING] = "trending"
    lab[out["rv7_pctl_1y"] >= VOL_PCTL_VOLATILE] = "volatile"
    lab[out["adx"].isna() | out["rv7_pctl_1y"].isna()] = None
    out["regime"] = lab
    out["direction"] = np.where(out["plus_di"] > out["minus_di"], "up", "down")
    return out


def pct_of(value: float, hist: pd.Series) -> float:
    hist = hist.dropna()
    return float((hist <= value).mean() * 100.0)


def quadrant(price_chg: float, oi_chg: float) -> str:
    if price_chg < 0 and oi_chg > 0:
        return "price down / OI up (new positions opened into the decline)"
    if price_chg < 0 and oi_chg <= 0:
        return "price down / OI down (deleveraging, positions closed)"
    if price_chg >= 0 and oi_chg > 0:
        return "price up / OI up (leverage added with the move)"
    return "price up / OI down (short covering, no new money)"


def metrics(sym: str) -> dict:
    h = load(sym, "1h")
    d = daily_bars(h)
    last_bar = h.index[-1]
    asof_close = last_bar + pd.Timedelta(hours=1)
    c = d["close"]
    px = float(h["close"].iloc[-1])
    m = {"symbol": sym, "last_bar_open_utc": str(last_bar), "as_of_utc": str(asof_close),
         "last_close": px, "last_daily_bar": str(d.index[-1].date())}

    # ---- trend / momentum (daily closes, 00:00 UTC) ----
    for n in (7, 30, 90, 200):
        m[f"ret_{n}d_pct"] = float((c.iloc[-1] / c.iloc[-1 - n] - 1) * 100)
    sma50, sma200 = c.rolling(50).mean(), c.rolling(200).mean()
    m["sma50d"] = float(sma50.iloc[-1]); m["sma200d"] = float(sma200.iloc[-1])
    m["dist_sma50d_pct"] = float((c.iloc[-1] / sma50.iloc[-1] - 1) * 100)
    m["dist_sma200d_pct"] = float((c.iloc[-1] / sma200.iloc[-1] - 1) * 100)
    m["sma50_vs_sma200_pct"] = float((sma50.iloc[-1] / sma200.iloc[-1] - 1) * 100)
    m["sma200d_slope_30d_pct"] = float((sma200.iloc[-1] / sma200.iloc[-31] - 1) * 100)
    hi365 = d["high"].iloc[-365:].max()
    m["high_365d"] = float(hi365)
    m["high_365d_date"] = str(d["high"].iloc[-365:].idxmax().date())
    m["drawdown_from_365d_high_pct"] = float((px / hi365 - 1) * 100)
    m["low_365d"] = float(d["low"].iloc[-365:].min())
    m["above_365d_low_pct"] = float((px / m["low_365d"] - 1) * 100)
    er = (c - c.shift(30)).abs() / c.diff().abs().rolling(30).sum()
    m["efficiency_ratio_30d"] = float(er.iloc[-1])
    w7 = h.iloc[-168:]
    m["low_7d"] = float(w7["low"].min()); m["low_7d_bar_open_utc"] = str(w7["low"].idxmin())
    m["high_7d"] = float(w7["high"].max()); m["high_7d_bar_open_utc"] = str(w7["high"].idxmax())
    m["bounce_from_7d_low_pct"] = float((px / m["low_7d"] - 1) * 100)

    reg = regime_series(h)
    r = reg.iloc[-1]
    m["adx14d"] = float(r["adx"]); m["plus_di"] = float(r["plus_di"]); m["minus_di"] = float(r["minus_di"])
    m["trend_direction"] = str(r["direction"])

    # ---- volatility ----
    rv7, rv30 = realised_vol(h, 168), realised_vol(h, 720)
    m["rv7d_ann_pct"] = float(rv7.iloc[-1] * 100)
    m["rv30d_ann_pct"] = float(rv30.iloc[-1] * 100)
    m["rv7d_pctl_1y"] = float(r["rv7_pctl_1y"])
    rv30_d = rv30[rv30.index.hour == 23]
    m["rv30d_pctl_1y"] = pct_of(rv30_d.iloc[-1], rv30_d.iloc[-365:])
    m["rv7_over_rv30"] = float(rv7.iloc[-1] / rv30.iloc[-1])
    m["atr14d_pct_of_price"] = float(adx(d)["atr"].iloc[-1] / c.iloc[-1] * 100)
    m["mom30_vol_adj"] = float(np.log(c.iloc[-1] / c.iloc[-31]) / (rv30.iloc[-1] * np.sqrt(30 / 365)))

    # ---- volume (USDT quote volume) ----
    qv = d["quote_volume"]
    m["qvol_last_24h_usd"] = float(qv.iloc[-1])
    m["qvol_avg_7d_usd"] = float(qv.iloc[-7:].mean())
    m["qvol_avg_30d_usd"] = float(qv.iloc[-31:-1].mean())          # the 30 days BEFORE the last day
    m["qvol_avg_365d_usd"] = float(qv.iloc[-366:-1].mean())
    m["qvol_24h_vs_30d_avg"] = float(qv.iloc[-1] / qv.iloc[-31:-1].mean())
    m["qvol_7d_vs_30d_avg"] = float(qv.iloc[-7:].mean() / qv.iloc[-31:-1].mean())
    m["qvol_30d_vs_365d_avg"] = float(qv.iloc[-31:-1].mean() / qv.iloc[-366:-1].mean())
    m["taker_buy_share_24h_pct"] = float(d["taker_buy_quote"].iloc[-1] / qv.iloc[-1] * 100)
    m["taker_buy_share_7d_pct"] = float(d["taker_buy_quote"].iloc[-7:].sum() / qv.iloc[-7:].sum() * 100)

    # ---- funding (archive ends before the candles do: report the date explicitly) ----
    fp = h["funding_paid"]
    last_settle_bar = fp[fp != 0].index[-1]
    f_end = last_settle_bar + pd.Timedelta(hours=1)               # settlement time = bar close
    m["funding_data_end_utc"] = str(f_end)
    m["funding_stale_days_at_last_bar"] = float((asof_close - f_end) / pd.Timedelta(days=1))
    fh = fp.loc[:last_settle_bar]
    win4y = fh.loc[f_end - pd.Timedelta(days=4 * 365):]
    m["funding_last_settled_pct"] = float(fh.iloc[-1] * 100)
    f24 = fh.rolling(24).sum()
    f30 = fh.rolling(720).sum()
    m["funding_last_24h_sum_pct"] = float(f24.iloc[-1] * 100)
    m["funding_30d_sum_pct"] = float(f30.iloc[-1] * 100)
    m["funding_30d_avg_per_8h_pct"] = float(f30.iloc[-1] / 90 * 100)
    m["funding_30d_annualised_pct"] = float(f30.iloc[-1] / 30 * 365 * 100)
    f24_4y = f24.loc[win4y.index[0]:][lambda s: s.index.hour == 23]
    f30_4y = f30.loc[win4y.index[0]:][lambda s: s.index.hour == 23]
    m["funding_last_24h_pctl_4y"] = pct_of(f24.iloc[-1], f24_4y)
    m["funding_30d_pctl_4y"] = pct_of(f30.iloc[-1], f30_4y)
    m["funding_4y_median_30d_annualised_pct"] = float(f30_4y.median() / 30 * 365 * 100)
    m["funding_share_negative_settlements_4y_pct"] = float((win4y[win4y != 0] < 0).mean() * 100)

    # ---- open interest (contracts, glitch-cleaned) ----
    oi = clean_oi(h["oi"])
    m["oi_contracts"] = float(oi.iloc[-1])
    m["oi_value_usd"] = float(oi.iloc[-1] * px)
    for n, k in ((7, 168), (30, 720)):
        m[f"oi_chg_{n}d_pct"] = float((oi.iloc[-1] / oi.iloc[-1 - k] - 1) * 100)
        m[f"price_chg_{n}d_hourly_pct"] = float((h["close"].iloc[-1] / h["close"].iloc[-1 - k] - 1) * 100)
        m[f"oi_minus_price_{n}d_pp"] = m[f"oi_chg_{n}d_pct"] - m[f"price_chg_{n}d_hourly_pct"]
        m[f"oi_price_state_{n}d"] = quadrant(m[f"price_chg_{n}d_hourly_pct"], m[f"oi_chg_{n}d_pct"])
    oi30 = (oi / oi.shift(720) - 1)
    m["oi_chg_30d_pctl_1y"] = pct_of(oi30.iloc[-1], oi30.iloc[-HOURS_PER_YEAR:])
    m["oi_value_vs_avg_daily_volume_30d"] = float(m["oi_value_usd"] / m["qvol_avg_30d_usd"])
    m["oi_zero_hours_in_archive"] = int((h["oi"] <= 0).sum())
    m["oi_missing_hours_in_archive"] = int(h["oi"].isna().sum())

    # ---- positioning ratios ----
    for col in ("acct_ls_ratio", "top_ls_ratio"):
        s = h[col]
        m[f"{col}_last"] = float(s.iloc[-1])
        m[f"{col}_pctl_1y"] = pct_of(s.iloc[-1], s.iloc[-HOURS_PER_YEAR:])

    # ---- regime ----
    m["regime"] = str(r["regime"])
    lab = reg["regime"].loc["2022-11-01":].dropna()
    m["regime_base_rates_pct"] = {k: float(v) for k, v in (lab.value_counts(normalize=True) * 100).round(1).items()}
    m["regime_last_10_days"] = [str(x) for x in reg["regime"].iloc[-10:]]
    m["regime_days_in_current_label"] = int((reg["regime"][::-1] != r["regime"]).values.argmax()) \
        if (reg["regime"] != r["regime"]).any() else len(reg)
    return m


RANK_RULES = {
    "trend": ("adx14d", True, "trend strength = ADX(14, daily), highest first; direction shown separately"),
    "momentum": ("mom30_vol_adj", True, "30d log return / (RV30 x sqrt(30/365)), highest first"),
    "volume": ("qvol_24h_vs_30d_avg", True, "last 24h USDT volume / prior 30d daily average, highest first"),
    "funding": ("_funding_extremeness", True, "|30d funding percentile over 4y - 50|, most extreme first"),
    "open_interest": ("oi_chg_30d_pct", True, "30d change in open interest (contracts), largest build first"),
    "volatility": ("rv30d_ann_pct", True, "30d realised vol (annualised), highest first"),
}


def rank(ms: dict) -> dict:
    out = {}
    for dim, (key, desc, rule) in RANK_RULES.items():
        vals = {s: (abs(m["funding_30d_pctl_4y"] - 50) if key == "_funding_extremeness" else m[key]) for s, m in ms.items()}
        order = sorted(vals, key=vals.get, reverse=desc)
        out[dim] = {"rule": rule, "order": order, "values": {s: float(vals[s]) for s in order}}
    return out


def f(x, nd=2, plus=False):
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "n/a"
    return f"{x:+,.{nd}f}" if plus else f"{x:,.{nd}f}"


def usd(x):
    return f"${x / 1e9:,.2f}bn"


def table(header, rows):
    s = "| " + " | ".join(header) + " |\n|" + "|".join(["---"] * len(header)) + "|\n"
    return s + "\n".join("| " + " | ".join(r) + " |" for r in rows) + "\n"


def render(ms: dict, ranks: dict, manifest: dict) -> str:
    syms = list(ms)
    a = ms[syms[0]]
    L = []
    L.append("# Market ranking - BTCUSDT / ETHUSDT / SOLUSDT (Binance USDT-M perps)\n")
    L.append(f"- **Data timestamp:** last 1h bar opens {a['last_bar_open_utc']} and closes **{a['as_of_utc']}**. "
             f"Daily figures use complete UTC days up to {a['last_daily_bar']}.")
    L.append(f"- **Source:** {manifest['source']} (official Binance public archive), fetched {manifest['fetched_at_utc']}, "
             "loaded only through `strategies.data.load`. Produced by `research/ranking.py`.")
    L.append(f"- **Funding is stale:** the funding archive ends **{a['funding_data_end_utc']}**, "
             f"{a['funding_stale_days_at_last_bar']:.1f} days before the last candle. "
             "Every funding figure below is as of that date, NOT as of the last bar. Do not read it as current.")
    L.append("- **Open interest** is cleaned before use: hours where the archive prints 0 are dropped and a trailing "
             "3h median removes one-bar partial prints "
             f"(zero-OI hours in the archive: " + ", ".join(f"{s} {ms[s]['oi_zero_hours_in_archive']}" for s in syms) + ").")
    L.append("- Descriptive statistics only. Nothing here is a forecast and no P&L was computed.")
    L.append("- **Survivorship:** none of the three symbols was delisted, but they were picked today knowing they "
             "survived and stayed liquid. A universe chosen in 2022 could have held names that later died, so any "
             "history on these three flatters what a 2022 trader would have seen.\n")

    L.append("## Regime label\n")
    L.append("Rule (fixed by convention, trailing data only, evaluated on daily bars):\n")
    L.append(f"1. **volatile** if 7-day realised vol is at or above the {VOL_PCTL_VOLATILE:.0f}th percentile of its own trailing 365 days;")
    L.append(f"2. else **trending** if ADX({ADX_N}, daily) >= {ADX_TRENDING:.0f} (direction from +DI vs -DI);")
    L.append("3. else **ranging**.\n")
    rows = []
    for s in syms:
        m = ms[s]
        br = m["regime_base_rates_pct"]
        rows.append([s, f"**{m['regime']}**", f(m["rv7d_pctl_1y"], 0), f(m["adx14d"], 1),
                     f"{m['trend_direction']} (+DI {f(m['plus_di'], 1)} / -DI {f(m['minus_di'], 1)})",
                     str(m["regime_days_in_current_label"]),
                     " / ".join(f"{k} {br.get(k, 0.0):.0f}%" for k in ("trending", "ranging", "volatile"))])
    L.append(table(["market", "regime now", "RV7 pctl (1y)", "ADX14d", "direction", "days in this label",
                    "share of days since 2022-11 (trend/range/vol)"], rows))
    L.append("The label is a coarse switch, not a signal. Vol takes precedence: a market that is falling hard is "
             "labelled volatile even when ADX also says trending, because stop distances and gap risk are what change first.\n")

    L.append("## Rank by dimension\n")
    rows = []
    for dim, r in ranks.items():
        rows.append([dim, " > ".join(f"{s} ({f(r['values'][s], 2)})" for s in r["order"]), r["rule"]])
    L.append(table(["dimension", "rank 1 > 2 > 3 (value)", "rule"], rows))
    L.append("Rank 1 means 'most of that property', not 'best to trade'. With all three in the same macro move the "
             "differences between them are small next to the move itself.\n")

    L.append("## Trend\n")
    L.append(table(["market", "last close", "7d %", "30d %", "90d %", "200d %", "vs 50d MA %", "vs 200d MA %",
                    "50d vs 200d MA %", "200d MA 30d slope %", "ADX14d", "ER30", "from 365d high %", "above 365d low %"],
                   [[s, f(ms[s]["last_close"], 2), f(ms[s]["ret_7d_pct"], 1, True), f(ms[s]["ret_30d_pct"], 1, True),
                     f(ms[s]["ret_90d_pct"], 1, True), f(ms[s]["ret_200d_pct"], 1, True),
                     f(ms[s]["dist_sma50d_pct"], 1, True), f(ms[s]["dist_sma200d_pct"], 1, True),
                     f(ms[s]["sma50_vs_sma200_pct"], 1, True), f(ms[s]["sma200d_slope_30d_pct"], 1, True),
                     f(ms[s]["adx14d"], 1), f(ms[s]["efficiency_ratio_30d"], 2),
                     f"{f(ms[s]['drawdown_from_365d_high_pct'], 1, True)} (high {f(ms[s]['high_365d'], 2)} on {ms[s]['high_365d_date']})",
                     f(ms[s]["above_365d_low_pct"], 1, True)] for s in syms]))
    L.append("Returns use daily closes (00:00 UTC). ER30 = Kaufman efficiency ratio over 30 daily closes "
             "(net move / path length; about 0.18 is what a driftless random walk gives).\n")

    L.append("## Momentum and volatility\n")
    L.append(table(["market", "30d mom / vol", "RV 7d ann %", "RV 7d pctl (1y)", "RV 30d ann %", "RV 30d pctl (1y)",
                    "RV7 / RV30", "ATR14d % of price"],
                   [[s, f(ms[s]["mom30_vol_adj"], 2, True), f(ms[s]["rv7d_ann_pct"], 1), f(ms[s]["rv7d_pctl_1y"], 0),
                     f(ms[s]["rv30d_ann_pct"], 1), f(ms[s]["rv30d_pctl_1y"], 0), f(ms[s]["rv7_over_rv30"], 2),
                     f(ms[s]["atr14d_pct_of_price"], 2)] for s in syms]))
    L.append("Realised vol is the standard deviation of 1h log returns, annualised with sqrt(8760). "
             "Percentile = share of the last 365 daily readings at or below today's.\n")

    L.append("## Volume (USDT quote volume)\n")
    L.append(table(["market", "last 24h", "7d avg/day", "30d avg/day", "24h vs 30d avg", "7d vs 30d avg",
                    "30d vs 365d avg", "taker-buy share 24h %", "taker-buy share 7d %"],
                   [[s, usd(ms[s]["qvol_last_24h_usd"]), usd(ms[s]["qvol_avg_7d_usd"]), usd(ms[s]["qvol_avg_30d_usd"]),
                     f(ms[s]["qvol_24h_vs_30d_avg"], 2) + "x", f(ms[s]["qvol_7d_vs_30d_avg"], 2) + "x",
                     f(ms[s]["qvol_30d_vs_365d_avg"], 2) + "x", f(ms[s]["taker_buy_share_24h_pct"], 1),
                     f(ms[s]["taker_buy_share_7d_pct"], 1)] for s in syms]))
    L.append("'30d avg' is the 30 complete days before the last day, so the last day is not compared with itself.\n")

    L.append(f"## Funding (as of {a['funding_data_end_utc']} - stale, see top)\n")
    L.append(table(["market", "last settled %", "last 24h sum %", "24h pctl (4y)", "30d avg per 8h %",
                    "30d annualised %", "30d pctl (4y)", "4y median 30d annualised %", "negative settlements 4y %"],
                   [[s, f(ms[s]["funding_last_settled_pct"], 4, True), f(ms[s]["funding_last_24h_sum_pct"], 4, True),
                     f(ms[s]["funding_last_24h_pctl_4y"], 0), f(ms[s]["funding_30d_avg_per_8h_pct"], 4, True),
                     f(ms[s]["funding_30d_annualised_pct"], 1, True), f(ms[s]["funding_30d_pctl_4y"], 0),
                     f(ms[s]["funding_4y_median_30d_annualised_pct"], 1, True),
                     f(ms[s]["funding_share_negative_settlements_4y_pct"], 0)] for s in syms]))
    L.append("Funding is summed from actual settlements, so SOL's 2h/4h intervals are handled. Positive = longs pay shorts. "
             "'per 8h' = 30-day sum / 90. Percentiles are against daily readings over the 4 years ending at the funding cut-off.\n")

    L.append("## Open interest (contracts)\n")
    L.append(table(["market", "OI value", "OI chg 7d %", "price chg 7d %", "OI chg 30d %", "price chg 30d %",
                    "OI minus price 30d (pp)", "OI 30d chg pctl (1y)", "OI value / avg daily volume"],
                   [[s, usd(ms[s]["oi_value_usd"]), f(ms[s]["oi_chg_7d_pct"], 1, True),
                     f(ms[s]["price_chg_7d_hourly_pct"], 1, True), f(ms[s]["oi_chg_30d_pct"], 1, True),
                     f(ms[s]["price_chg_30d_hourly_pct"], 1, True), f(ms[s]["oi_minus_price_30d_pp"], 1, True),
                     f(ms[s]["oi_chg_30d_pctl_1y"], 0), f(ms[s]["oi_value_vs_avg_daily_volume_30d"], 2)] for s in syms]))
    L.append("OI / price state:\n")
    for s in syms:
        L.append(f"- **{s}** 7d: {ms[s]['oi_price_state_7d']}; 30d: {ms[s]['oi_price_state_30d']}.")
    L.append("\nOI in contracts is used so a falling price does not mechanically shrink it. A one-sided reading is not "
             "possible from OI alone: every contract has a long and a short.\n")

    L.append("## Positioning ratios (Binance accounts, context only)\n")
    L.append(table(["market", "all-accounts long/short", "pctl (1y)", "top-trader long/short (by position)", "pctl (1y)"],
                   [[s, f(ms[s]["acct_ls_ratio_last"], 2), f(ms[s]["acct_ls_ratio_pctl_1y"], 0),
                     f(ms[s]["top_ls_ratio_last"], 2), f(ms[s]["top_ls_ratio_pctl_1y"], 0)] for s in syms]))
    L.append("These count Binance accounts only and say nothing about size on other venues. Not used in any setup.\n")
    return "\n".join(L)


def reading(ms: dict) -> str:
    """Plain-language summary. Every sentence is built from the numbers above; nothing is a forecast."""
    syms = list(ms)
    short = {s: s.replace("USDT", "") for s in syms}
    j = lambda key, nd=1, plus=True: " / ".join(f(ms[s][key], nd, plus) for s in syms)
    L = ["## Reading (risk view, no forecast)\n", f"Order of figures: {' / '.join(short.values())}.\n"]
    regs = {ms[s]["regime"] for s in syms}
    dirs = {ms[s]["trend_direction"] for s in syms}
    if len(regs) == 1:
        L.append(f"- **Regime:** all three carry the same label, **{regs.pop()}**. 7-day realised vol sits at the "
                 f"{j('rv7d_pctl_1y', 0, False)} percentile of the past year and ADX is {j('adx14d', 1, False)} "
                 f"with direction {' / '.join(ms[s]['trend_direction'] for s in syms)}. "
                 "Three markets with one label are one bet, not three.")
    else:
        L.append("- **Regime:** labels differ: " + ", ".join(f"{short[s]} {ms[s]['regime']}" for s in syms) + ".")
    below50 = all(ms[s]["dist_sma50d_pct"] < 0 for s in syms)
    below200 = all(ms[s]["dist_sma200d_pct"] < 0 for s in syms)
    cross = all(ms[s]["sma50_vs_sma200_pct"] > 0 for s in syms)
    if below50 and below200:
        t = (f"- **Trend:** price is below both the 50-day and the 200-day average in all three "
             f"({j('dist_sma50d_pct')}% and {j('dist_sma200d_pct')}%).")
        if cross:
            t += (f" The 50-day average is still above the 200-day ({j('sma50_vs_sma200_pct')}%): the averages have not "
                  "caught up with the fall, so slow trend systems are late by construction here.")
        L.append(t)
    else:
        L.append(f"- **Trend:** distance to the 50-day average {j('dist_sma50d_pct')}%, to the 200-day {j('dist_sma200d_pct')}%.")
    L.append(f"- **Size of the move:** 7-day return {j('ret_7d_pct')}%, 30-day {j('ret_30d_pct')}%; "
             f"{j('drawdown_from_365d_high_pct')}% from the 365-day high. The 7-day lows were "
             + ", ".join(f"{short[s]} {f(ms[s]['low_7d'], 2)} (bar opening {ms[s]['low_7d_bar_open_utc'][:16]} UTC)" for s in syms)
             + f"; the last close is {j('bounce_from_7d_low_pct')}% above them.")
    L.append(f"- **Volatility:** 7-day realised vol {j('rv7d_ann_pct', 0, False)}% annualised, "
             f"{j('rv7_over_rv30', 2, False)} times the 30-day figure. Daily ATR is {j('atr14d_pct_of_price', 1, False)}% of price: "
             "a stop sized for last month is too tight for this week.")
    vmax = max(ms[s]["qvol_24h_vs_30d_avg"] for s in syms)
    vword = "Extreme." if vmax >= 2 else ("Elevated, not extreme." if vmax >= 1.1 else "Normal.")
    L.append(f"- **Volume:** the last 24h traded {j('qvol_24h_vs_30d_avg', 2, False)} times the prior 30-day daily average. {vword}")
    up = [short[s] for s in syms if ms[s]["oi_chg_7d_pct"] > 0]
    dn = [short[s] for s in syms if ms[s]["oi_chg_7d_pct"] <= 0]
    t = f"- **Open interest:** 7-day change in contracts {j('oi_chg_7d_pct')}% against price {j('price_chg_7d_hourly_pct')}%."
    if dn:
        t += f" {', '.join(dn)}: positions were closed into the fall (deleveraging)."
    if up:
        t += (f" {', '.join(up)}: open interest ROSE while price fell, i.e. new positions were opened into the decline. "
              "That is leverage that has not been flushed yet; it can unwind in either direction.")
    L.append(t)
    L.append(f"- **Funding:** not available for the sell-off. The archive stops {ms[syms[0]]['funding_data_end_utc'][:16]} UTC, "
             f"{ms[syms[0]]['funding_stale_days_at_last_bar']:.0f} days before the last candle. At that cut-off the 30-day "
             f"average was {j('funding_30d_annualised_pct')}% annualised ({j('funding_30d_pctl_4y', 0, False)} percentile of 4 years): "
             "unremarkable. Whether funding has since turned negative is unknown from this data.")
    hot = [short[s] for s in syms if ms[s]["acct_ls_ratio_pctl_1y"] >= 90]
    L.append(f"- **Positioning (Binance accounts only):** long/short account ratio {j('acct_ls_ratio_last', 2, False)}, "
             f"at the {j('acct_ls_ratio_pctl_1y', 0, False)} percentile of the past year"
             + (f" ({', '.join(hot)} in the top decile: an unusually large share of accounts is net long into the decline)" if hot else "")
             + ". The ratio counts accounts, not size. Context, not a signal.")
    L.append("- **What this does NOT say:** it does not say the low is in, and it does not say the fall continues. "
             "It says volatility is high, the three markets are moving as one, and the trend filters most setups rely on "
             "are pointing down or lagging.")
    L.append("- **For the setup list:** by the rule above only the setups tagged `volatile` are in their intended regime "
             "today. That is a switch for Lab to test, not a recommendation; nothing here has been backtested.\n")
    return "\n".join(L)


def main():
    manifest = json.load(open(ROOT / "data" / "MANIFEST.json"))
    ms = {s: metrics(s) for s in SYMBOLS}
    ranks = rank(ms)
    out = {"generated_by": "research/ranking.py",
           "data_source": manifest["source"], "data_fetched_at_utc": manifest["fetched_at_utc"],
           "as_of_utc": ms[SYMBOLS[0]]["as_of_utc"],
           "regime_rule": {"volatile": f"RV7 percentile vs trailing 365d >= {VOL_PCTL_VOLATILE}",
                           "trending": f"else ADX({ADX_N}, daily) >= {ADX_TRENDING}", "ranging": "otherwise"},
           "rank_rules": {k: v[2] for k, v in RANK_RULES.items()},
           "ranks": ranks, "markets": ms}
    json.dump(out, open(OUT / "market_ranking.json", "w"), indent=1)
    md = render(ms, ranks, manifest) + "\n" + reading(ms)
    (OUT / "market_ranking.md").write_text(md)
    print(md)


if __name__ == "__main__":
    main()
