"""Lab backtest engine: bar-accurate implementation of the SPEC execution model.

One call = one (setup, symbol) pair on one timeframe. The engine is timeframe-agnostic: it only
needs the frame returned by ``strategies.data.load`` and the frame returned by ``signals()``.

Timeline for a signal on row i (decided at the CLOSE of bar i):
  * the order is placed at the OPEN of bar i+1 - never earlier;
  * ``entry`` NaN  -> marketable order: fills at open[i+1] +/- slippage, taker fee;
  * ``entry`` set  -> resting limit, live for bars i+1 .. i+entry_valid, fills at the limit price
    (maker fee, no slippage) only if the bar trades THROUGH it (low < limit for buys,
    high > limit for sells; a touch is not a fill).

Rules the SPEC leaves open, and the (conservative) choice made here. Forge's paper broker must
match these or the two will disagree:

 R1  Limit already through the market when placed (open[i+1] < limit for a buy): the order is
     marketable on arrival, so it fills at the open as a TAKER (fee + slippage), not at the limit
     as a maker. Otherwise "limit = close + 1 tick" would buy a guaranteed maker fill.
 R2  On a later bar of the validity window that OPENS through the limit, the resting order fills
     at the limit price (maker) at the open instant - no price improvement.
 R3  Fill bar of a limit entry: the stop is live for the rest of the bar. If the bar's range
     reaches the stop the trade is stopped in that same bar. The target counts in the fill bar
     only if the bar CLOSES through it (the only proof that it traded through after the fill),
     unless the fill happened at the open instant (R2 / market entry), where the whole bar counts.
 R4  Stops trigger on a touch (low <= stop for longs). Targets need a trade-through (high > target).
     Stop and target both reached in one bar -> stop, always, even if the bar opened beyond the
     target. Bar opens beyond the stop -> filled at the open (2x slippage).
 R5  A scheduled exit (exit flag / max_hold) happens at the next open. If that open is beyond the
     stop the exit is booked as a stop (2x slippage); if it is beyond the target the resting
     target fills at the target price (maker).
 R6  max_hold = n: the fill bar is bar 1; exit at the open of the bar after bar n.
 R7  Signals arriving while a position is open are dropped, except on the row whose close
     schedules an exit for the next open (exit flag or max_hold): the position is closed at that
     open and the new signal is then acted on at the same open, so stop-and-reverse setups work
     (``reverse_on_exit_row=False`` drops those signals too).
     A new signal while an entry limit is still resting is ignored until that order fills or
     expires (``pending_policy="ignore"``, what Forge's bot does; ``"replace"`` is available).
     An exit flag while flat does nothing: it never cancels a resting entry order.
 R8  Signals with no stop, a stop on the wrong side of the entry price, or a target on the wrong
     side are rejected and counted in ``diagnostics`` (for marketable entries the check is made
     against the open they would fill at).
 R9  Sizing uses the price the bot sees when it sends the order: the limit price, or the next
     open for marketable entries. qty = risk_frac * equity / |entry - stop|, capped so that
     qty * entry <= max_leverage * equity. Equity = realised account equity (flat at that time).
 R10 Funding: side * qty * close[k] * funding_paid[k] for every bar k the position is open at the
     bar close (both signs). On a bar where the position is closed intrabar (stop/target) the
     funding of that bar is charged only if it is a COST to the position, never credited.
 R11 A position still open at the last bar of the window is closed at that bar's close, paying
     taker fee + slippage (reason ``end_of_data``).
 R12 Lot size / minimum notional are not in SPEC and are OFF by default. ``ExecConfig(filters=
     exchange_filters())`` turns them on with the bot's instrument table: quantity is floored to
     the lot step and the trade is skipped when it is below the minimum quantity or notional.
     On a $1,000 account this matters for BTC (see ``diagnostics["rejected_min_size"]``).

Equity curve: one value per bar = account equity marked at the bar CLOSE (open position marked
at close, exit costs not yet deducted).
"""
from __future__ import annotations

import inspect
import math
import sys
from bisect import bisect_left
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from functools import lru_cache

import numpy as np
import pandas as pd

MAKER_FEE = 0.0002
TAKER_FEE = 0.0005
SLIPPAGE = {"BTCUSDT": 0.0001, "ETHUSDT": 0.0001, "SOLUSDT": 0.0002}
DEFAULT_SLIPPAGE = 0.0002          # unknown symbol -> the worse of the two SPEC values
START_EQUITY = 1000.0

REQUIRED_COLS = ("side", "stop")
TRADE_COLS = [
    "signal_time", "entry_time", "exit_time", "side", "entry_type", "qty", "entry_ref",
    "entry_price", "exit_ref", "exit_price", "stop", "target", "notional", "leverage",
    "equity_at_entry", "risk_usd", "gross", "fees", "slippage", "funding", "net", "ret", "R",
    "exit_reason", "bars_held", "entry_bar", "exit_bar",
]


@dataclass(frozen=True)
class ExecConfig:
    start_equity: float = START_EQUITY
    risk_frac: float = 0.005            # 0.5% of equity risked per trade
    max_leverage: float = 3.0           # notional cap, x equity
    maker_fee: float = MAKER_FEE
    taker_fee: float = TAKER_FEE
    slippage: float | None = None       # None -> SPEC per-symbol table
    stop_slip_mult: float = 2.0         # stop exits pay 2x slippage
    fee_mult: float = 1.0               # stress multipliers
    slip_mult: float = 1.0
    extra_delay: int = 0                # audit only: act this many bars later than the contract
    pending_policy: str = "ignore"      # "ignore" | "replace" (new signal while a limit rests, R7)
    reverse_on_exit_row: bool = True    # act on a signal whose row also schedules the exit (R7)
    funding_partial_bar: str = "cost_only"   # "cost_only" | "none" | "full"  (rule R10)
    filters: dict | None = None         # symbol -> (lot step, min qty, min notional); None = off (R12)

    def stressed(self, fee_mult: float = 2.0, slip_mult: float = 2.0) -> "ExecConfig":
        return replace(self, fee_mult=fee_mult, slip_mult=slip_mult)

    def slip_for(self, symbol: str) -> float:
        base = self.slippage if self.slippage is not None else SLIPPAGE.get(symbol, DEFAULT_SLIPPAGE)
        return base * self.slip_mult


@dataclass
class BacktestResult:
    symbol: str
    trades: pd.DataFrame
    equity: pd.Series                 # equity at each bar close, indexed by bar open time
    in_position: pd.Series            # True if a position was open at any time during the bar
    diagnostics: dict
    cfg: ExecConfig
    start: pd.Timestamp
    end: pd.Timestamp                 # open time of the last bar in the window
    bar: pd.Timedelta
    name: str = ""
    params: dict = field(default_factory=dict)

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1]) if len(self.equity) else self.cfg.start_equity

    @property
    def net_return(self) -> float:
        return self.final_equity / self.cfg.start_equity - 1.0


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------
def to_ts(x, index: pd.DatetimeIndex) -> pd.Timestamp:
    """Timestamp comparable with ``index`` (naive strings are read as UTC)."""
    t = pd.Timestamp(x)
    if index.tz is not None:
        t = t.tz_localize(index.tz) if t.tzinfo is None else t.tz_convert(index.tz)
    elif t.tzinfo is not None:
        t = t.tz_convert("UTC").tz_localize(None)
    return t


def window_bounds(index: pd.DatetimeIndex, start=None, end=None) -> tuple[int, int]:
    """First and last row (inclusive) whose bar OPEN time lies in [start, end]."""
    i0 = 0 if start is None else int(index.searchsorted(to_ts(start, index), side="left"))
    i1 = len(index) - 1 if end is None else int(index.searchsorted(to_ts(end, index), side="right")) - 1
    return i0, i1


def bar_length(index: pd.DatetimeIndex) -> pd.Timedelta:
    if len(index) < 2:
        return pd.Timedelta(hours=1)
    return pd.Timedelta(pd.Series(index[: min(len(index), 500)]).diff().median())


def _col(sig: pd.DataFrame, name: str, default: float, n: int) -> np.ndarray:
    if name not in sig.columns:
        return np.full(n, default, dtype=float)
    return pd.to_numeric(sig[name], errors="coerce").to_numpy(dtype=float, na_value=np.nan)


def check_signal_frame(df: pd.DataFrame, sig: pd.DataFrame) -> None:
    if not isinstance(sig, pd.DataFrame):
        raise ValueError(f"signals() must return a DataFrame, got {type(sig).__name__}")
    missing = [c for c in REQUIRED_COLS if c not in sig.columns]
    if missing:
        raise ValueError(f"signals() frame is missing required column(s): {missing}")
    if len(sig) != len(df) or not sig.index.equals(df.index):
        raise ValueError("signals() frame must be on exactly the same index as the price frame")


# --------------------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------------------
def run_backtest(df: pd.DataFrame, sig: pd.DataFrame, symbol: str, start=None, end=None,
                 cfg: ExecConfig | None = None, name: str = "", params: dict | None = None,
                 hold_mult: int = 1) -> BacktestResult:
    """Run one (setup, symbol) backtest. ``df``/``sig`` share an index; warm-up rows before
    ``start`` are only ever read as signal rows (the last one may trigger an order at the first
    bar of the window).

    ``hold_mult`` > 1: the signals were decided on a timeframe ``hold_mult`` times slower than
    ``df`` and re-indexed with ``to_exec_frame`` (e.g. a 4h setup executed on 1h bars). max_hold
    is then counted in the slower bars: the exit falls on the open of the slow bar after bar n."""
    cfg = cfg or ExecConfig()
    check_signal_frame(df, sig)
    index = df.index
    n = len(df)
    i0, i1 = window_bounds(index, start, end)
    bar = bar_length(index)
    if n == 0 or i1 < i0:
        empty = pd.Series(dtype=float)
        return BacktestResult(symbol, pd.DataFrame(columns=TRADE_COLS), empty, empty.astype(bool), {},
                              cfg, pd.NaT, pd.NaT, bar, name, params or {})

    O = df["open"].to_numpy(float).tolist()
    H = df["high"].to_numpy(float).tolist()
    L = df["low"].to_numpy(float).tolist()
    C = df["close"].to_numpy(float).tolist()
    FP = (df["funding_paid"].fillna(0.0).to_numpy(float).tolist()
          if "funding_paid" in df.columns else [0.0] * n)

    side_a = np.sign(np.nan_to_num(_col(sig, "side", 0.0, n), nan=0.0)).astype(np.int64)
    entry_a = _col(sig, "entry", np.nan, n)
    valid_a = np.nan_to_num(_col(sig, "entry_valid", 1.0, n), nan=1.0)
    valid_a = np.maximum(valid_a, 1.0).astype(np.int64)
    stop_a = _col(sig, "stop", np.nan, n)
    target_a = _col(sig, "target", np.nan, n)
    hold_a = _col(sig, "max_hold", np.nan, n)
    hold_a = np.where(np.isfinite(hold_a), np.maximum(hold_a, 1.0), 0.0).astype(np.int64)  # 0 = none
    if "exit" in sig.columns:
        exit_a = sig["exit"].astype("boolean").fillna(False).to_numpy(dtype=bool)
    else:
        exit_a = np.zeros(n, dtype=bool)

    d = int(cfg.extra_delay)
    if d > 0:                                   # audit: everything the strategy says arrives d bars late
        def _shift(a, fill):
            out = np.empty_like(a)
            out[:d] = fill
            out[d:] = a[:-d]
            return out
        side_a, entry_a, valid_a = _shift(side_a, 0), _shift(entry_a, np.nan), _shift(valid_a, 1)
        stop_a, target_a = _shift(stop_a, np.nan), _shift(target_a, np.nan)
        hold_a, exit_a = _shift(hold_a, 0), _shift(exit_a, False)

    hold_mult = int(hold_mult)
    if hold_mult > 1:                           # position of each bar inside its slow-timeframe block
        OFF = (((index - pd.Timestamp(0, tz=index.tz)) // bar) % hold_mult).tolist()
    SIDE, ENTRY, VALID = side_a.tolist(), entry_a.tolist(), valid_a.tolist()
    STOP, TARGET, HOLD, EXIT = stop_a.tolist(), target_a.tolist(), hold_a.tolist(), exit_a.tolist()
    sig_rows = np.flatnonzero(side_a != 0).tolist()

    maker = cfg.maker_fee * cfg.fee_mult
    taker = cfg.taker_fee * cfg.fee_mult
    slip = cfg.slip_for(symbol)
    stop_slip = slip * cfg.stop_slip_mult
    risk_frac, max_lev = cfg.risk_frac, cfg.max_leverage
    if cfg.pending_policy not in ("ignore", "replace"):
        raise ValueError("pending_policy must be 'ignore' or 'replace'")
    replace_pending = cfg.pending_policy == "replace"
    reverse_ok = bool(cfg.reverse_on_exit_row)
    filt = (cfg.filters or {}).get(symbol)
    f_step, f_min_qty, f_min_notional = (float(x) for x in filt) if filt else (0.0, 0.0, 0.0)
    fund_mode = cfg.funding_partial_bar
    isfinite = math.isfinite

    eq = np.full(n, np.nan)
    inpos = np.zeros(n, dtype=bool)
    trades: list[tuple] = []
    diag = dict(signals=0, signals_in_window=0, acted_on=0, dropped_in_position=0, market_orders=0, limit_orders=0,
                limit_marketable=0, limit_filled=0, limit_expired=0, limit_replaced=0,
                limit_ignored=0, rejected_no_stop=0, rejected_stop_side=0, rejected_target_side=0,
                rejected_no_equity=0, rejected_min_size=0)

    cash = float(cfg.start_equity)
    pos = 0
    qty = entry_ref = entry_fill = stp = tgt = 0.0
    has_tgt = False
    mh = bars_in = entry_bar = sig_bar = 0
    entry_fee = t_funding = eq_entry = 0.0
    entry_type = ""
    exit_due = False
    due_reason = ""
    pend = None        # (side, limit, stop, target, max_hold, expiry_bar, placed_bar, signal_row)

    def _size(ref, s_stop):
        """R9 sizing; 0.0 when exchange filters are on and the order would be below the minimum (R12)."""
        q = risk_frac * cash / abs(ref - s_stop)
        if q * ref > max_lev * cash:
            q = max_lev * cash / ref
        if filt:
            q = math.floor(q / f_step + 1e-9) * f_step
            if q <= 0.0 or q < f_min_qty * (1 - 1e-9) or q * ref < f_min_notional:
                return 0.0
        return q

    def _open(j, sd, ref, is_taker, s_stop, s_tgt, s_mh, s_row, etype):
        nonlocal pos, qty, entry_ref, entry_fill, stp, tgt, has_tgt, mh, bars_in, entry_bar, sig_bar
        nonlocal entry_fee, t_funding, eq_entry, entry_type, exit_due, cash
        q = _size(ref, s_stop)
        if q <= 0.0:
            diag["rejected_min_size"] += 1
            return False
        fill = ref * (1.0 + sd * slip) if is_taker else ref
        fee = q * fill * (taker if is_taker else maker)
        eq_entry = cash
        cash -= fee
        pos, qty, entry_ref, entry_fill = sd, q, ref, fill
        stp, tgt, has_tgt, mh = s_stop, s_tgt, isfinite(s_tgt), s_mh
        if hold_mult > 1 and s_mh:
            mh = s_mh * hold_mult - OFF[j]          # R6 in slow bars: the fill's slow bar is bar 1
        bars_in, entry_bar, sig_bar = 0, j, s_row
        entry_fee, t_funding, entry_type, exit_due = fee, 0.0, etype, False
        return True

    def _close(j, ref, reason, kind, when):
        """kind 0 = resting target (maker, no slippage), 1 = taker, 2 = stop (taker, 2x slippage).
        when 0 = at the bar open, 1 = intrabar, 2 = at the close of the last bar (bar already accrued)."""
        nonlocal pos, cash, t_funding, exit_due
        if kind == 0:
            fill, rate = ref, maker
        elif kind == 1:
            fill, rate = ref * (1.0 - pos * slip), taker
        else:
            fill, rate = ref * (1.0 - pos * stop_slip), taker
        if when == 1:
            inpos[j] = True
            f = pos * qty * ref * FP[j]
            if f != 0.0 and (fund_mode == "full" or (fund_mode == "cost_only" and f > 0.0)):
                cash -= f
                t_funding += f
        exit_fee = qty * fill * rate
        cash += pos * qty * (fill - entry_fill) - exit_fee
        gross = pos * qty * (ref - entry_ref)
        slp = qty * (abs(entry_fill - entry_ref) + abs(fill - ref))
        fees = entry_fee + exit_fee
        net = gross - slp - fees - t_funding
        risk = qty * abs(entry_ref - stp)
        held = j - entry_bar + (1 if when else 0)
        trades.append((index[sig_bar], index[entry_bar], index[j], pos, entry_type, qty, entry_ref,
                       entry_fill, ref, fill, stp, tgt if has_tgt else np.nan, qty * entry_ref,
                       qty * entry_ref / eq_entry, eq_entry, risk, gross, fees, slp, t_funding, net,
                       net / eq_entry, net / risk if risk > 0 else np.nan, reason, held, entry_bar, j))
        pos = 0
        exit_due = False

    j = i0
    if sig_rows:                                   # jump straight to the first actionable bar
        k = bisect_left(sig_rows, i0 - 1)
        j = sig_rows[k] + 1 if k < len(sig_rows) else i1 + 1
    else:
        j = i1 + 1

    while j <= i1:
        opn, hi, lo, cl = O[j], H[j], L[j], C[j]
        can_act = True
        flat_at_open = True
        if pos:
            can_act = exit_due and reverse_ok
            if (pos > 0 and opn <= stp) or (pos < 0 and opn >= stp):
                _close(j, opn, "stop_gap", 2, 0)                                    # R4
            elif exit_due:
                if has_tgt and ((pos > 0 and opn > tgt) or (pos < 0 and opn < tgt)):
                    _close(j, tgt, "target", 0, 0)                                  # R5
                else:
                    _close(j, opn, due_reason, 1, 0)
            elif (pos > 0 and lo <= stp) or (pos < 0 and hi >= stp):
                _close(j, stp, "stop", 2, 1)
                flat_at_open = False
            elif has_tgt and ((pos > 0 and hi > tgt) or (pos < 0 and lo < tgt)):
                _close(j, tgt, "target", 0, 1)
                flat_at_open = False
            else:                                   # held through the whole bar
                f = pos * qty * cl * FP[j]
                cash -= f
                t_funding += f
                bars_in += 1
                inpos[j] = True
                if EXIT[j]:
                    exit_due, due_reason = True, "exit_signal"
                elif mh and bars_in >= mh:
                    exit_due, due_reason = True, "time"
                eq[j] = cash + pos * qty * (cl - entry_fill)
                j += 1
                continue

        if flat_at_open:
            s = j - 1
            if s >= 0 and SIDE[s] and can_act:
                diag["acted_on"] += 1
                sd, s_stop, lim, s_tgt = SIDE[s], STOP[s], ENTRY[s], TARGET[s]
                is_mkt = not isfinite(lim)
                ref = opn if is_mkt else lim
                if pend is not None and not replace_pending:
                    diag["limit_ignored"] += 1          # an entry order is already resting (R7)
                elif not isfinite(s_stop):
                    diag["rejected_no_stop"] += 1
                elif (sd > 0 and s_stop >= ref) or (sd < 0 and s_stop <= ref) or s_stop <= 0:
                    diag["rejected_stop_side"] += 1
                elif isfinite(s_tgt) and ((sd > 0 and s_tgt <= ref) or (sd < 0 and s_tgt >= ref)):
                    diag["rejected_target_side"] += 1
                elif cash <= 0:
                    diag["rejected_no_equity"] += 1
                elif is_mkt:
                    if pend is not None:
                        diag["limit_replaced"] += 1
                        pend = None
                    if _open(j, sd, opn, True, s_stop, s_tgt, HOLD[s], s, "market"):
                        diag["market_orders"] += 1
                elif _size(lim, s_stop) <= 0.0:         # too small to be accepted: never rests
                    diag["rejected_min_size"] += 1
                else:
                    if pend is not None:
                        diag["limit_replaced"] += 1
                    diag["limit_orders"] += 1
                    pend = (sd, lim, s_stop, s_tgt, HOLD[s], j + VALID[s] - 1, j, s)

            at_open_fill = pos != 0                 # market fill above happened at the open instant
            if pos == 0 and pend is not None:
                sd, lim, s_stop, s_tgt, s_mh, expiry, placed, s_row = pend
                through_at_open = (sd > 0 and opn < lim) or (sd < 0 and opn > lim)
                if through_at_open and j == placed:                                     # R1
                    pend = None
                    if (sd > 0 and s_stop >= opn) or (sd < 0 and s_stop <= opn):
                        diag["rejected_stop_side"] += 1
                    elif isfinite(s_tgt) and ((sd > 0 and s_tgt <= opn) or (sd < 0 and s_tgt >= opn)):
                        diag["rejected_target_side"] += 1
                    elif _open(j, sd, opn, True, s_stop, s_tgt, s_mh, s_row, "limit_marketable"):
                        diag["limit_marketable"] += 1
                        at_open_fill = True
                elif (sd > 0 and lo < lim) or (sd < 0 and hi > lim):
                    pend = None
                    if _open(j, sd, lim, False, s_stop, s_tgt, s_mh, s_row, "limit"):
                        diag["limit_filled"] += 1
                    at_open_fill = through_at_open                                      # R2
                elif j >= expiry:
                    pend = None
                    diag["limit_expired"] += 1

            if pos:                                 # filled in this bar: stop/target for the rest of it (R3)
                if (pos > 0 and lo <= stp) or (pos < 0 and hi >= stp):
                    px = stp
                    if at_open_fill and ((pos > 0 and opn <= stp) or (pos < 0 and opn >= stp)):
                        px = opn                    # gapped through limit AND stop
                    _close(j, px, "stop", 2, 1)
                elif has_tgt and (
                        ((pos > 0 and hi > tgt) or (pos < 0 and lo < tgt)) if at_open_fill
                        else ((pos > 0 and cl > tgt) or (pos < 0 and cl < tgt))):
                    _close(j, tgt, "target", 0, 1)
                else:
                    f = pos * qty * cl * FP[j]
                    cash -= f
                    t_funding += f
                    bars_in = 1
                    inpos[j] = True
                    if EXIT[j]:
                        exit_due, due_reason = True, "exit_signal"
                    elif mh and bars_in >= mh:
                        exit_due, due_reason = True, "time"
                    eq[j] = cash + pos * qty * (cl - entry_fill)
                    j += 1
                    continue

        eq[j] = cash
        if pend is None:                            # flat, nothing resting: skip to the next signal
            k = bisect_left(sig_rows, j)
            if k == len(sig_rows):
                break
            j = sig_rows[k] + 1
        else:
            j += 1

    if pos:                                         # R11
        _close(i1, C[i1], "end_of_data", 1, 2)
        eq[i1] = cash

    diag["signals"] = int(len(sig_rows))
    diag["signals_in_window"] = int(np.count_nonzero(side_a[max(i0 - 1, 0):i1] != 0))
    diag["dropped_in_position"] = diag["signals_in_window"] - diag["acted_on"]
    equity = pd.Series(eq[i0:i1 + 1], index=index[i0:i1 + 1], name="equity").ffill().fillna(cfg.start_equity)
    tdf = pd.DataFrame(trades, columns=TRADE_COLS)
    return BacktestResult(symbol, tdf, equity, pd.Series(inpos[i0:i1 + 1], index=index[i0:i1 + 1], name="in_position"),
                          diag, cfg, index[i0], index[i1], bar, name, dict(params or {}))


# --------------------------------------------------------------------------------------
# slower-timeframe signals on a faster execution frame
# --------------------------------------------------------------------------------------
def to_exec_frame(sig: pd.DataFrame, df_exec: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Re-index signals decided on a slower timeframe onto the execution frame ``df_exec``.

    The row of the slow bar opening at t is placed on the execution bar that CLOSES at the same
    instant, so the order still goes out at the first open after the slow bar's close.
    entry_valid is converted to execution bars; max_hold stays in slow bars (pass the returned
    multiple to ``run_backtest(hold_mult=...)``). Returns (signals on df_exec.index, multiple)."""
    exec_bar = bar_length(df_exec.index)
    mult = int(round(bar_length(sig.index) / exec_bar))
    if mult <= 1:
        if not sig.index.equals(df_exec.index):
            raise ValueError("signals are not on the execution index")
        return sig, 1
    pos = df_exec.index.get_indexer(sig.index + (mult - 1) * exec_bar)
    ok = pos >= 0
    n = len(df_exec)
    out = {}
    for col, default in (("side", 0.0), ("entry", np.nan), ("entry_valid", 1.0), ("stop", np.nan),
                         ("target", np.nan), ("max_hold", np.nan)):
        a = np.full(n, default, dtype=float)
        if col in sig.columns:
            a[pos[ok]] = pd.to_numeric(sig[col], errors="coerce").to_numpy(dtype=float, na_value=np.nan)[ok]
        out[col] = a
    out["entry_valid"] = np.nan_to_num(out["entry_valid"], nan=1.0) * mult
    ex = np.zeros(n, dtype=bool)
    if "exit" in sig.columns:
        ex[pos[ok]] = sig["exit"].astype("boolean").fillna(False).to_numpy(dtype=bool)[ok]
    out["exit"] = ex
    return pd.DataFrame(out, index=df_exec.index), mult


def module_signals_1h(module, symbol: str, params: dict | None = None, upto=None):
    """(1h frame, signals on the 1h index, hold multiple) for any strategy module: the setup still
    decides on its own timeframe, execution is simulated on 1h bars."""
    df_tf, sig = module_signals(module, symbol, params, upto)
    if getattr(module, "TIMEFRAME", "1h") == "1h":
        return df_tf, sig, 1
    df1 = get_frame(symbol, "1h")
    if upto is not None:
        df1 = df1.loc[:to_ts(upto, df1.index)]
    sig1, mult = to_exec_frame(sig, df1)
    return df1, sig1, mult


# --------------------------------------------------------------------------------------
# data + strategy-module plumbing (shared by metrics / validate / run_all)
# --------------------------------------------------------------------------------------
@contextmanager
def no_bytecode():
    """Import other teams' modules without leaving .pyc files in their directories."""
    old, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        yield
    finally:
        sys.dont_write_bytecode = old


def exchange_filters() -> dict | None:
    """symbol -> (lot step, min qty, min notional) read from Forge's instrument table
    (bot/instruments.py; its values are marked unverified there). None if the bot is not importable."""
    try:
        with no_bytecode():
            from bot.instruments import INSTRUMENTS
        return {k: (float(v.step), float(v.min_qty), float(v.min_notional)) for k, v in INSTRUMENTS.items()}
    except Exception:                               # noqa: BLE001 - optional dependency on another team's code
        return None


def spec_data():
    """The ``strategies.data`` module (the contract: load, SYMBOLS, BACKTEST_START, BACKTEST_END)."""
    with no_bytecode():
        import strategies.data as d
    return d


@lru_cache(maxsize=16)
def get_frame(symbol: str, tf: str) -> pd.DataFrame:
    """Cached ``strategies.data.load`` (the only loader). Treat the result as read-only."""
    return spec_data().load(symbol, tf)


def get_ctx(tf: str, upto=None) -> dict[str, pd.DataFrame]:
    out = {s: get_frame(s, tf) for s in spec_data().SYMBOLS}
    if upto is not None:
        out = {s: f.loc[:upto] for s, f in out.items()}
    return out


def call_signals(module, df: pd.DataFrame, params: dict, ctx: dict | None) -> pd.DataFrame:
    """Call ``module.signals`` with or without ctx depending on its signature."""
    fn = module.signals
    try:
        npar = len(inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        npar = 3
    sig = fn(df, params, ctx) if npar >= 3 else fn(df, params)
    check_signal_frame(df, sig)
    return sig


def module_params(module, params: dict | None = None) -> dict:
    p = dict(getattr(module, "DEFAULT_PARAMS", {}) or {})
    if params:
        p.update(params)
    return p


def module_signals(module, symbol: str, params: dict | None = None, upto=None):
    """(df, sig) for a strategy module on one symbol; frames are cut at ``upto`` (inclusive) so
    nothing after the test window can reach the strategy."""
    tf = getattr(module, "TIMEFRAME", "1h")
    df = get_frame(symbol, tf)
    if upto is not None:
        df = df.loc[:to_ts(upto, df.index)]
    ctx = get_ctx(tf, df.index[-1] if len(df) else None)
    return df, call_signals(module, df, module_params(module, params), ctx)


def run_module(module, symbol: str, params: dict | None = None, start=None, end=None,
               cfg: ExecConfig | None = None, presignals=None) -> BacktestResult:
    """Backtest a strategy module on one symbol over [start, end] (defaults: SPEC window)."""
    d = spec_data()
    start = d.BACKTEST_START if start is None else start
    end = d.BACKTEST_END if end is None else end
    df, sig = presignals if presignals is not None else module_signals(module, symbol, params, upto=end)
    return run_backtest(df, sig, symbol, start, end, cfg, name=getattr(module, "NAME", getattr(module, "__name__", "")),
                        params=module_params(module, params))
