"""Performance metrics, buy-and-hold benchmark, yearly and regime splits.

Conventions
  * equity series = account equity at each bar CLOSE, indexed by bar open time (engine output);
  * daily equity = last bar-close equity of each UTC day; Sharpe = mean/std of daily returns
    x sqrt(365), risk-free 0, flat days count as 0% days;
  * max drawdown is measured on the bar-level curve with the starting equity as first peak and is
    reported as a positive fraction;
  * CAGR uses the calendar length of the window (first bar open -> last bar close), 365.25 d/yr.

Regime rule (no look-ahead: the label of bar i uses closes up to bar i-1 only, i.e. it is known
at the open of bar i):
    ret90 = close[i-1] / close[i-1 - 90 days] - 1
    ma200 = mean(close[i-200 days .. i-1])   (while fewer than 200 days of history exist the
            mean of what exists is used, minimum 90 days; before that the label is "unknown")
    bull     : ret90 > +15%  and close[i-1] > ma200
    bear     : ret90 < -15%  and close[i-1] < ma200
    sideways : everything else
The +/-15% threshold is a fixed round number, not fitted. Trades are attributed to the regime
of their ENTRY bar; bar returns of the equity curve to the regime of that bar.

Buy-and-hold benchmark: 1x long perp opened with a marketable order at the first open of the
window (taker fee + SPEC slippage on entry), qty = start_equity / open, never rebalanced, pays
real funding (qty * close * funding_paid) on every bar, marked at the close, no exit cost.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import BacktestResult, ExecConfig, bar_length, window_bounds

DAYS_PER_YEAR = 365.25
SHARPE_DAYS = 365
REGIMES = ("bull", "bear", "sideways")
SURVIVORSHIP_NOTE = (
    "Survivorship: BTCUSDT, ETHUSDT and SOLUSDT were chosen today, knowing all three are still listed and "
    "liquid; none was delisted in the window. Results on names picked with hindsight flatter any long-biased "
    "setup (SOL traded below $8 in late 2022 and survived; many peers did not) and say nothing about symbols "
    "that died.")


# --------------------------------------------------------------------------------------
# equity-curve statistics
# --------------------------------------------------------------------------------------
def max_drawdown(equity, start_equity: float | None = None) -> float:
    v = np.asarray(equity, dtype=float)
    if v.size == 0:
        return 0.0
    if start_equity is not None:
        v = np.concatenate(([start_equity], v))
    peak = np.maximum.accumulate(v)
    return float(np.max(1.0 - v / peak))


def daily_equity(equity: pd.Series) -> pd.Series:
    return equity.resample("1D").last().dropna()


def daily_returns(equity: pd.Series, start_equity: float) -> pd.Series:
    d = daily_equity(equity)
    prev = d.shift(1)
    if len(prev):
        prev.iloc[0] = start_equity
    return d / prev - 1.0


def sharpe(equity: pd.Series, start_equity: float) -> float:
    r = daily_returns(equity, start_equity)
    if len(r) < 2:
        return 0.0
    sd = float(r.std(ddof=1))
    return float(r.mean() / sd * np.sqrt(SHARPE_DAYS)) if sd > 0 else 0.0


def monthly_returns(equity: pd.Series, start_equity: float) -> pd.Series:
    m = equity.resample("ME").last().dropna()
    prev = m.shift(1)
    if len(prev):
        prev.iloc[0] = start_equity
    return m / prev - 1.0


def window_years(equity: pd.Series, bar: pd.Timedelta) -> float:
    if len(equity) == 0:
        return 0.0
    return float((equity.index[-1] + bar - equity.index[0]) / pd.Timedelta(days=DAYS_PER_YEAR))


def cagr(final_over_start: float, years: float) -> float:
    if years <= 0:
        return 0.0
    if final_over_start <= 0:
        return -1.0
    return float(final_over_start ** (1.0 / years) - 1.0)


def equity_stats(equity: pd.Series, start_equity: float, bar: pd.Timedelta) -> dict:
    if len(equity) == 0:
        return dict(final_equity=start_equity, net_return=0.0, cagr=0.0, max_dd=0.0, sharpe=0.0,
                    worst_month=0.0, worst_month_label="", years=0.0)
    final = float(equity.iloc[-1])
    yrs = window_years(equity, bar)
    mr = monthly_returns(equity, start_equity)
    return dict(
        final_equity=final,
        net_return=final / start_equity - 1.0,
        cagr=cagr(final / start_equity, yrs),
        max_dd=max_drawdown(equity, start_equity),
        sharpe=sharpe(equity, start_equity),
        worst_month=float(mr.min()) if len(mr) else 0.0,
        worst_month_label=mr.idxmin().strftime("%Y-%m") if len(mr) else "",
        years=yrs,
    )


# --------------------------------------------------------------------------------------
# trade statistics
# --------------------------------------------------------------------------------------
def longest_losing_streak(net) -> int:
    best = cur = 0
    for x in np.asarray(net, dtype=float):
        cur = cur + 1 if x < 0 else 0
        best = max(best, cur)
    return int(best)


def profit_factor(net) -> float:
    net = np.asarray(net, dtype=float)
    wins, losses = net[net > 0].sum(), -net[net < 0].sum()
    if losses == 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def trade_stats(trades: pd.DataFrame) -> dict:
    n = len(trades)
    if n == 0:
        return dict(trades=0, win_rate=np.nan, profit_factor=np.nan, avg_R=np.nan, median_R=np.nan,
                    avg_net=np.nan, longest_losing_streak=0, avg_bars_held=np.nan, long_share=np.nan,
                    gross_pnl=0.0, fees_paid=0.0, slippage_paid=0.0, funding_paid=0.0, net_pnl=0.0,
                    stop_exits=0, target_exits=0, time_exits=0, signal_exits=0)
    net = trades["net"].to_numpy(float)
    reason = trades["exit_reason"]
    return dict(
        trades=n,
        win_rate=float((net > 0).mean()),
        profit_factor=profit_factor(net),
        avg_R=float(trades["R"].mean()),
        median_R=float(trades["R"].median()),
        avg_net=float(net.mean()),
        longest_losing_streak=longest_losing_streak(net),
        avg_bars_held=float(trades["bars_held"].mean()),
        long_share=float((trades["side"] > 0).mean()),
        gross_pnl=float(trades["gross"].sum()),
        fees_paid=float(trades["fees"].sum()),
        slippage_paid=float(trades["slippage"].sum()),
        funding_paid=float(trades["funding"].sum()),      # positive = paid, negative = received
        net_pnl=float(net.sum()),
        stop_exits=int(reason.isin(["stop", "stop_gap"]).sum()),
        target_exits=int((reason == "target").sum()),
        time_exits=int((reason == "time").sum()),
        signal_exits=int((reason == "exit_signal").sum()),
    )


# --------------------------------------------------------------------------------------
# buy-and-hold benchmark
# --------------------------------------------------------------------------------------
@dataclass
class Benchmark:
    equity: pd.Series
    stats: dict


def buy_and_hold(df: pd.DataFrame, symbol: str, start=None, end=None,
                 cfg: ExecConfig | None = None) -> Benchmark:
    cfg = cfg or ExecConfig()
    i0, i1 = window_bounds(df.index, start, end)
    w = df.iloc[i0:i1 + 1]
    bar = bar_length(df.index)
    e0 = cfg.start_equity
    ref = float(w["open"].iloc[0])
    fill = ref * (1.0 + cfg.slip_for(symbol))
    qty = e0 / ref
    fee = qty * fill * cfg.taker_fee * cfg.fee_mult
    close = w["close"].to_numpy(float)
    fund = qty * close * w["funding_paid"].fillna(0.0).to_numpy(float) if "funding_paid" in w else np.zeros(len(w))
    eq = pd.Series(e0 - fee + qty * (close - fill) - np.cumsum(fund), index=w.index, name="bh_equity")
    st = equity_stats(eq, e0, bar)
    st.update(entry_ref=ref, entry_price=fill, qty=qty, fee_paid=fee, slippage_paid=qty * (fill - ref),
              funding_paid=float(fund.sum()), price_return=float(close[-1] / ref - 1.0))
    return Benchmark(eq, st)


# --------------------------------------------------------------------------------------
# regimes
# --------------------------------------------------------------------------------------
def regime_labels(df: pd.DataFrame, thr: float = 0.15, ret_days: int = 90, ma_days: int = 200,
                  min_ma_days: int = 90) -> pd.Series:
    """bull / bear / sideways / unknown per bar; see module docstring for the rule."""
    bpd = int(round(pd.Timedelta(days=1) / bar_length(df.index)))
    prev = df["close"].shift(1)
    ret = prev / df["close"].shift(1 + ret_days * bpd) - 1.0
    ma = prev.rolling(ma_days * bpd, min_periods=min_ma_days * bpd).mean()
    lab = np.full(len(df), "sideways", dtype=object)
    lab[((ret > thr) & (prev > ma)).to_numpy()] = "bull"
    lab[((ret < -thr) & (prev < ma)).to_numpy()] = "bear"
    lab[(ret.isna() | ma.isna()).to_numpy()] = "unknown"
    return pd.Series(lab, index=df.index, name="regime")


# --------------------------------------------------------------------------------------
# headline metrics + splits
# --------------------------------------------------------------------------------------
def compute_metrics(res: BacktestResult, df: pd.DataFrame | None = None) -> dict:
    """Flat dict of everything in the brief. Pass the price frame to add the benchmark (bh_*)."""
    e0 = res.cfg.start_equity
    m = equity_stats(res.equity, e0, res.bar)
    m.update(trade_stats(res.trades))
    m["exposure"] = float(res.in_position.mean()) if len(res.in_position) else 0.0
    m["avg_leverage"] = float(res.trades["leverage"].mean()) if len(res.trades) else np.nan
    m["trades_per_year"] = m["trades"] / m["years"] if m["years"] > 0 else np.nan
    m["cost_pct_of_start"] = (m["fees_paid"] + m["slippage_paid"] + m["funding_paid"]) / e0
    m["return_over_maxdd"] = m["net_return"] / m["max_dd"] if m["max_dd"] > 0 else np.nan
    if df is not None and len(res.equity):
        bh = buy_and_hold(df, res.symbol, res.start, res.end, res.cfg).stats
        m.update(bh_net_return=bh["net_return"], bh_cagr=bh["cagr"], bh_max_dd=bh["max_dd"],
                 bh_sharpe=bh["sharpe"], bh_funding_paid=bh["funding_paid"], bh_fee_paid=bh["fee_paid"],
                 excess_vs_bh=m["net_return"] - bh["net_return"])
    return m


def _period_return(eq: pd.Series, mask: np.ndarray, e0: float) -> float:
    v = eq.to_numpy(float)
    prev = np.concatenate(([e0], v[:-1]))
    r = v[mask] / prev[mask]
    return float(np.prod(r) - 1.0) if r.size else 0.0


def yearly_split(res: BacktestResult, df: pd.DataFrame | None = None) -> pd.DataFrame:
    e0 = res.cfg.start_equity
    eq, tr = res.equity, res.trades
    bh = buy_and_hold(df, res.symbol, res.start, res.end, res.cfg).equity if df is not None and len(eq) else None
    rows = []
    years = eq.index.year
    prev_end, bh_prev = e0, e0
    for y in sorted(set(years)):
        seg = eq[years == y]
        t = tr[pd.DatetimeIndex(tr["entry_time"]).year == y] if len(tr) else tr
        ts = trade_stats(t)
        row = dict(year=int(y), bars=len(seg), net_return=float(seg.iloc[-1] / prev_end - 1.0),
                   max_dd=max_drawdown(seg, prev_end), trades=ts["trades"], win_rate=ts["win_rate"],
                   avg_R=ts["avg_R"], profit_factor=ts["profit_factor"], net_pnl=ts["net_pnl"],
                   fees_paid=ts["fees_paid"], funding_paid=ts["funding_paid"],
                   exposure=float(res.in_position[years == y].mean()))
        if bh is not None:
            bseg = bh[years == y]
            row["bh_return"] = float(bseg.iloc[-1] / bh_prev - 1.0)
            row["bh_max_dd"] = max_drawdown(bseg, bh_prev)
            bh_prev = float(bseg.iloc[-1])
        prev_end = float(seg.iloc[-1])
        rows.append(row)
    return pd.DataFrame(rows).set_index("year") if rows else pd.DataFrame()


def regime_split(res: BacktestResult, df: pd.DataFrame, labels: pd.Series | None = None) -> pd.DataFrame:
    """Per-regime results. ``labels`` defaults to regime_labels(df) (the symbol's own price)."""
    e0 = res.cfg.start_equity
    eq, tr = res.equity, res.trades
    if len(eq) == 0:
        return pd.DataFrame()
    labels = regime_labels(df) if labels is None else labels
    lab = labels.reindex(eq.index).to_numpy()
    bh = buy_and_hold(df, res.symbol, res.start, res.end, res.cfg).equity
    tlab = labels.reindex(pd.DatetimeIndex(tr["entry_time"])).to_numpy() if len(tr) else np.array([], dtype=object)
    rows = []
    for g in list(REGIMES) + ["unknown"]:
        mask = lab == g
        if g == "unknown" and not mask.any():
            continue
        ts = trade_stats(tr[tlab == g] if len(tr) else tr)
        rows.append(dict(regime=g, bars=int(mask.sum()), share_of_bars=float(mask.mean()),
                         strat_return=_period_return(eq, mask, e0), bh_return=_period_return(bh, mask, e0),
                         trades=ts["trades"], win_rate=ts["win_rate"], avg_R=ts["avg_R"],
                         profit_factor=ts["profit_factor"], net_pnl=ts["net_pnl"],
                         exposure=float(res.in_position.to_numpy()[mask].mean()) if mask.any() else 0.0))
    return pd.DataFrame(rows).set_index("regime")


def segment_return(equity: pd.Series, start=None, end=None, start_equity: float | None = None) -> float:
    """Compounded return of the curve between two timestamps (bar open times, inclusive)."""
    from .engine import to_ts
    if len(equity) == 0:
        return 0.0
    i0, i1 = window_bounds(equity.index, start, end)
    if i1 < i0:
        return 0.0
    base = float(equity.iloc[i0 - 1]) if i0 > 0 else (start_equity if start_equity is not None else float(equity.iloc[0]))
    return float(equity.iloc[i1] / base - 1.0)


def format_metrics(m: dict) -> str:
    def pct(x):
        return "n/a" if x is None or (isinstance(x, float) and not np.isfinite(x)) else f"{100 * x:+.2f}%"
    lines = [
        f"net {pct(m['net_return'])}  CAGR {pct(m['cagr'])}  maxDD {pct(-m['max_dd'])}  Sharpe {m['sharpe']:.2f}",
        f"trades {m['trades']}  win {pct(m['win_rate'])}  PF {m['profit_factor']:.2f}  avgR {m['avg_R']:.3f}  "
        f"lose-streak {m['longest_losing_streak']}  worst month {pct(m['worst_month'])} ({m['worst_month_label']})",
        f"exposure {pct(m['exposure'])}  fees ${m['fees_paid']:.2f}  slippage ${m['slippage_paid']:.2f}  "
        f"funding ${m['funding_paid']:.2f}",
    ]
    if "bh_net_return" in m:
        lines.append(f"buy&hold net {pct(m['bh_net_return'])}  maxDD {pct(-m['bh_max_dd'])}  "
                     f"Sharpe {m['bh_sharpe']:.2f}  excess {pct(m['excess_vs_bh'])}")
    return "\n".join(lines)
