"""Several (setup, symbol) streams trading ONE account, with the Shield's loss rules.

Everything runs on the 1h grid: a 4h setup still decides on its own closed 4h bars, the orders are
simulated on 1h bars (``engine.to_exec_frame``). Fill, stop, target, fee, slippage and funding
rules are those of ``engine.run_backtest`` (R1-R12); ``tests/test_portfolio.py`` checks that one
stream alone reproduces the engine trade for trade.

What is shared
  equity     every order is sized on the account's realised equity (cash) at the moment it is sent
             (resting limits keep the size they were given when placed, as in the bot);
  gross cap  an order is BLOCKED (not shrunk) if open positions + resting orders + the new order
             would exceed max_leverage x equity - Forge's Shield rule;
  order of events inside one bar: exits at the open (all streams), then entries at the open
             (streams in the order given), then everything that happens inside the bar. Money made
             or lost inside a bar reaches the shared equity at that bar's close.

Shield (optional, account scope)
  daily_loss   mark-to-market equity at a bar close <= (1 - 3%) x equity at the start of that UTC
               day -> halt;
  loss_streak  3 losing trades in a row (net < 0, account-wide, in closing order) -> halt;
  halt         resting entry orders are cancelled at once, open positions are closed at the next
               1h open (taker fee + slippage, reason "shield"), no entries for the rest of that
               UTC day. Trading resumes at 00:00 UTC of the next day with the loss counter at 0.
               For loss_streak the real Shield waits for a human; next-day resume is an ASSUMPTION.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import metrics as M
from .engine import TRADE_COLS, ExecConfig, bar_length, check_signal_frame, window_bounds, _col


@dataclass(frozen=True)
class ShieldRules:
    daily_loss_pct: float = 0.03
    max_consecutive_losses: int = 3


@dataclass
class StreamSpec:
    name: str                 # setup name
    symbol: str
    sig: pd.DataFrame         # signals on the 1h execution index
    hold_mult: int = 1        # 4 for a 4h setup


@dataclass
class PortfolioResult:
    trades: pd.DataFrame
    equity: pd.Series                  # mark-to-market account equity at each 1h close
    in_position: pd.Series             # any position open during the bar
    halts: pd.DataFrame                # time, kind
    diagnostics: dict                  # per stream
    cfg: ExecConfig
    start: pd.Timestamp
    end: pd.Timestamp
    bar: pd.Timedelta

    @property
    def final_equity(self) -> float:
        return float(self.equity.iloc[-1])

    @property
    def net_return(self) -> float:
        return self.final_equity / self.cfg.start_equity - 1.0


class _Account:
    __slots__ = ("cash", "pending", "closed")

    def __init__(self, cash: float):
        self.cash = cash          # realised equity
        self.pending = 0.0        # intrabar flows, added to cash at the bar close
        self.closed = []          # (net, stream, reason) of trades closed since last looked at


class _Stream:
    """One (setup, symbol): the engine's bar logic split into at-open and intrabar steps."""

    def __init__(self, spec: StreamSpec, df: pd.DataFrame, cfg: ExecConfig, acct: _Account):
        check_signal_frame(df, spec.sig)
        self.name, self.symbol, self.acct, self.cfg = spec.name, spec.symbol, acct, cfg
        self.index = df.index
        n = len(df)
        sig = spec.sig
        self.O, self.H = df["open"].to_numpy(float).tolist(), df["high"].to_numpy(float).tolist()
        self.L, self.C = df["low"].to_numpy(float).tolist(), df["close"].to_numpy(float).tolist()
        self.FP = df["funding_paid"].fillna(0.0).to_numpy(float).tolist()
        self.SIDE = np.sign(np.nan_to_num(_col(sig, "side", 0.0, n), nan=0.0)).astype(np.int64).tolist()
        self.ENTRY = _col(sig, "entry", np.nan, n).tolist()
        self.VALID = np.maximum(np.nan_to_num(_col(sig, "entry_valid", 1.0, n), nan=1.0), 1.0).astype(np.int64).tolist()
        self.STOP = _col(sig, "stop", np.nan, n).tolist()
        self.TARGET = _col(sig, "target", np.nan, n).tolist()
        h = _col(sig, "max_hold", np.nan, n)
        self.HOLD = np.where(np.isfinite(h), np.maximum(h, 1.0), 0.0).astype(np.int64).tolist()
        self.EXIT = (sig["exit"].astype("boolean").fillna(False).to_numpy(dtype=bool).tolist()
                     if "exit" in sig.columns else [False] * n)
        self.mult = int(spec.hold_mult)
        bar = bar_length(df.index)
        self.OFF = (((df.index - pd.Timestamp(0, tz=df.index.tz)) // bar) % max(self.mult, 1)).tolist()
        self.maker, self.taker = cfg.maker_fee * cfg.fee_mult, cfg.taker_fee * cfg.fee_mult
        self.slip = cfg.slip_for(spec.symbol)
        self.stop_slip = self.slip * cfg.stop_slip_mult
        self.replace_pending = cfg.pending_policy == "replace"
        self.reverse_ok = bool(cfg.reverse_on_exit_row)
        filt = (cfg.filters or {}).get(spec.symbol)
        self.filt = tuple(float(x) for x in filt) if filt else None
        self.pos = 0
        self.qty = self.entry_ref = self.entry_fill = self.stp = self.tgt = 0.0
        self.has_tgt = False
        self.mh = self.bars_in = self.entry_bar = self.sig_bar = 0
        self.entry_fee = self.t_funding = self.eq_entry = 0.0
        self.entry_type = ""
        self.exit_due, self.due_reason = False, ""
        self.pend = None          # (side, limit, stop, target, max_hold, expiry, placed, signal_row, qty, equity)
        self.can_act = True
        self.carry = False        # position carried into this bar's intrabar step
        self.entered_at_open = False
        self.unreal = 0.0
        self.held = False         # a position was open at some time during the current bar
        self.trades: list[tuple] = []
        self.diag = dict(acted_on=0, market_orders=0, limit_orders=0, limit_marketable=0, limit_filled=0,
                         limit_expired=0, limit_replaced=0, limit_ignored=0, limit_cancelled_by_halt=0,
                         rejected_no_stop=0, rejected_stop_side=0, rejected_target_side=0, rejected_no_equity=0,
                         rejected_min_size=0, blocked_gross_cap=0, blocked_halt=0, flattened_by_halt=0)

    # ---- sizing / exposure ---------------------------------------------------------
    def size(self, ref: float, stop: float, equity: float) -> float:
        q = self.cfg.risk_frac * equity / abs(ref - stop)
        if q * ref > self.cfg.max_leverage * equity:
            q = self.cfg.max_leverage * equity / ref
        if self.filt:
            step, min_qty, min_notional = self.filt
            q = math.floor(q / step + 1e-9) * step
            if q <= 0.0 or q < min_qty * (1 - 1e-9) or q * ref < min_notional:
                return 0.0
        return q

    def exposure(self) -> float:
        """Notional of the open position plus the resting entry order (Shield's gross measure)."""
        e = self.qty * self.entry_fill if self.pos else 0.0
        if self.pend is not None:
            e += self.pend[8] * self.pend[1]
        return e

    # ---- fills ----------------------------------------------------------------------
    def _flow(self, amount: float, intrabar: bool) -> None:
        if intrabar:
            self.acct.pending += amount
        else:
            self.acct.cash += amount

    def _open(self, j, sd, ref, is_taker, s_stop, s_tgt, s_mh, s_row, etype, q, equity, intrabar):
        fill = ref * (1.0 + sd * self.slip) if is_taker else ref
        fee = q * fill * (self.taker if is_taker else self.maker)
        self._flow(-fee, intrabar)
        self.eq_entry = equity
        self.pos, self.qty, self.entry_ref, self.entry_fill = sd, q, ref, fill
        self.stp, self.tgt, self.has_tgt, self.mh = s_stop, s_tgt, math.isfinite(s_tgt), s_mh
        if self.mult > 1 and s_mh:
            self.mh = s_mh * self.mult - self.OFF[j]
        self.bars_in, self.entry_bar, self.sig_bar = 0, j, s_row
        self.entry_fee, self.t_funding, self.entry_type, self.exit_due = fee, 0.0, etype, False
        self.held = True

    def _close(self, j, ref, reason, kind, when):
        pos, qty = self.pos, self.qty
        if kind == 0:
            fill, rate = ref, self.maker
        elif kind == 1:
            fill, rate = ref * (1.0 - pos * self.slip), self.taker
        else:
            fill, rate = ref * (1.0 - pos * self.stop_slip), self.taker
        intrabar = when != 0
        if when == 1:
            f = pos * qty * ref * self.FP[j]
            mode = self.cfg.funding_partial_bar
            if f != 0.0 and (mode == "full" or (mode == "cost_only" and f > 0.0)):
                self._flow(-f, True)
                self.t_funding += f
        exit_fee = qty * fill * rate
        self._flow(pos * qty * (fill - self.entry_fill) - exit_fee, intrabar)
        gross = pos * qty * (ref - self.entry_ref)
        slp = qty * (abs(self.entry_fill - self.entry_ref) + abs(fill - ref))
        fees = self.entry_fee + exit_fee
        net = gross - slp - fees - self.t_funding
        risk = qty * abs(self.entry_ref - self.stp)
        held = j - self.entry_bar + (1 if when else 0)
        idx = self.index
        self.trades.append((idx[self.sig_bar], idx[self.entry_bar], idx[j], pos, self.entry_type, qty, self.entry_ref,
                            self.entry_fill, ref, fill, self.stp, self.tgt if self.has_tgt else np.nan,
                            qty * self.entry_ref, qty * self.entry_ref / self.eq_entry, self.eq_entry, risk, gross,
                            fees, slp, self.t_funding, net, net / self.eq_entry, net / risk if risk > 0 else np.nan,
                            reason, held, self.entry_bar, j, self.name, self.symbol))
        self.acct.closed.append((net, self, reason))
        self.pos, self.exit_due, self.unreal = 0, False, 0.0

    def _hold(self, j, cl, first):
        pos = self.pos
        f = pos * self.qty * cl * self.FP[j]
        self.acct.pending -= f
        self.t_funding += f
        self.bars_in = 1 if first else self.bars_in + 1
        if self.EXIT[j]:
            self.exit_due, self.due_reason = True, "exit_signal"
        elif self.mh and self.bars_in >= self.mh:
            self.exit_due, self.due_reason = True, "time"
        self.unreal = pos * self.qty * (cl - self.entry_fill)

    # ---- step 1: exits at the open ---------------------------------------------------
    def open_exits(self, j, flatten):
        self.can_act, self.carry, self.entered_at_open, self.held = True, False, False, False
        if self.pos:
            pos, opn, stp = self.pos, self.O[j], self.stp
            self.can_act = self.exit_due and self.reverse_ok and not flatten
            if (pos > 0 and opn <= stp) or (pos < 0 and opn >= stp):
                self._close(j, opn, "stop_gap", 2, 0)
            elif flatten:
                self.diag["flattened_by_halt"] += 1
                self._close(j, opn, "shield", 1, 0)
            elif self.exit_due:
                if self.has_tgt and ((pos > 0 and opn > self.tgt) or (pos < 0 and opn < self.tgt)):
                    self._close(j, self.tgt, "target", 0, 0)
                else:
                    self._close(j, opn, self.due_reason, 1, 0)
            else:
                self.carry = self.held = True
        if flatten:
            self.cancel_order()

    def cancel_order(self):
        if self.pend is not None:
            self.pend = None
            self.diag["limit_cancelled_by_halt"] += 1

    # ---- step 2: entries at the open -------------------------------------------------
    def open_entries(self, j, halted, gross_fn):
        if self.pos or not self.can_act:
            return
        acct, cfg, d = self.acct, self.cfg, self.diag
        opn, s = self.O[j], j - 1
        isfinite = math.isfinite
        if s >= 0 and self.SIDE[s]:
            if halted:
                d["blocked_halt"] += 1
            else:
                d["acted_on"] += 1
                sd, s_stop, lim, s_tgt = self.SIDE[s], self.STOP[s], self.ENTRY[s], self.TARGET[s]
                is_mkt = not isfinite(lim)
                ref = opn if is_mkt else lim
                cash = acct.cash
                if self.pend is not None and not self.replace_pending:
                    d["limit_ignored"] += 1
                elif not isfinite(s_stop):
                    d["rejected_no_stop"] += 1
                elif (sd > 0 and s_stop >= ref) or (sd < 0 and s_stop <= ref) or s_stop <= 0:
                    d["rejected_stop_side"] += 1
                elif isfinite(s_tgt) and ((sd > 0 and s_tgt <= ref) or (sd < 0 and s_tgt >= ref)):
                    d["rejected_target_side"] += 1
                elif cash <= 0:
                    d["rejected_no_equity"] += 1
                else:
                    if is_mkt and self.pend is not None:
                        d["limit_replaced"] += 1
                        self.pend = None
                    q = self.size(ref, s_stop, cash)
                    own = 0.0 if is_mkt or self.pend is None else self.pend[8] * self.pend[1]   # order being replaced
                    if q <= 0.0:
                        d["rejected_min_size"] += 1
                    elif gross_fn() - own + q * ref > cfg.max_leverage * cash * (1 + 1e-9):
                        d["blocked_gross_cap"] += 1
                    elif is_mkt:
                        self._open(j, sd, opn, True, s_stop, s_tgt, self.HOLD[s], s, "market", q, cash, False)
                        d["market_orders"] += 1
                        self.entered_at_open = True
                    else:
                        if self.pend is not None:
                            d["limit_replaced"] += 1
                        d["limit_orders"] += 1
                        self.pend = (sd, lim, s_stop, s_tgt, self.HOLD[s], j + self.VALID[s] - 1, j, s, q, cash)
        if self.pos == 0 and self.pend is not None and self.pend[6] == j:              # R1: marketable on arrival
            sd, lim, s_stop, s_tgt, s_mh, expiry, placed, s_row, q0, eq0 = self.pend
            if (sd > 0 and opn < lim) or (sd < 0 and opn > lim):
                self.pend = None
                if (sd > 0 and s_stop >= opn) or (sd < 0 and s_stop <= opn):
                    d["rejected_stop_side"] += 1
                elif isfinite(s_tgt) and ((sd > 0 and s_tgt <= opn) or (sd < 0 and s_tgt >= opn)):
                    d["rejected_target_side"] += 1
                else:
                    q = self.size(opn, s_stop, acct.cash)
                    if q <= 0.0:
                        d["rejected_min_size"] += 1
                    elif gross_fn() + q * opn > cfg.max_leverage * acct.cash * (1 + 1e-9):
                        d["blocked_gross_cap"] += 1
                    else:
                        self._open(j, sd, opn, True, s_stop, s_tgt, s_mh, s_row, "limit_marketable", q, acct.cash, False)
                        d["limit_marketable"] += 1
                        self.entered_at_open = True

    # ---- step 3: inside the bar ------------------------------------------------------
    def intrabar(self, j):
        opn, hi, lo, cl = self.O[j], self.H[j], self.L[j], self.C[j]
        if self.carry:
            pos, stp = self.pos, self.stp
            if (pos > 0 and lo <= stp) or (pos < 0 and hi >= stp):
                self._close(j, stp, "stop", 2, 1)
            elif self.has_tgt and ((pos > 0 and hi > self.tgt) or (pos < 0 and lo < self.tgt)):
                self._close(j, self.tgt, "target", 0, 1)
            else:
                self._hold(j, cl, False)
            return
        at_open_fill = self.entered_at_open
        if self.pos == 0 and self.pend is not None:
            sd, lim, s_stop, s_tgt, s_mh, expiry, placed, s_row, q, eq0 = self.pend
            if (sd > 0 and lo < lim) or (sd < 0 and hi > lim):
                self.pend = None
                self._open(j, sd, lim, False, s_stop, s_tgt, s_mh, s_row, "limit", q, eq0, True)
                self.diag["limit_filled"] += 1
                at_open_fill = (sd > 0 and opn < lim) or (sd < 0 and opn > lim)
            elif j >= expiry:
                self.pend = None
                self.diag["limit_expired"] += 1
        if self.pos:
            pos, stp = self.pos, self.stp
            if (pos > 0 and lo <= stp) or (pos < 0 and hi >= stp):
                px = stp
                if at_open_fill and ((pos > 0 and opn <= stp) or (pos < 0 and opn >= stp)):
                    px = opn
                self._close(j, px, "stop", 2, 1)
            elif self.has_tgt and (
                    ((pos > 0 and hi > self.tgt) or (pos < 0 and lo < self.tgt)) if at_open_fill
                    else ((pos > 0 and cl > self.tgt) or (pos < 0 and cl < self.tgt))):
                self._close(j, self.tgt, "target", 0, 1)
            else:
                self._hold(j, cl, True)


def run_portfolio(specs: list[StreamSpec], frames: dict[str, pd.DataFrame], start=None, end=None,
                  cfg: ExecConfig | None = None, shield: ShieldRules | None = None) -> PortfolioResult:
    """Simulate all streams on one account. ``frames``: symbol -> 1h frame (identical indices).
    ``shield=None`` switches the loss rules off (the gross cap always applies)."""
    cfg = cfg or ExecConfig()
    if cfg.extra_delay:
        raise ValueError("extra_delay is an audit option of the single-stream engine only")
    index = next(iter(frames.values())).index
    for f in frames.values():
        if not f.index.equals(index):
            raise ValueError("all execution frames must share one 1h index")
    i0, i1 = window_bounds(index, start, end)
    bar = bar_length(index)
    acct = _Account(float(cfg.start_equity))
    streams = [_Stream(sp, frames[sp.symbol], cfg, acct) for sp in specs]
    n = len(index)
    eq = np.full(n, np.nan)
    inpos = np.zeros(n, dtype=bool)
    DAY = ((index - pd.Timestamp(0, tz=index.tz)) // pd.Timedelta(days=1)).tolist()
    gross_fn = lambda: sum(st.exposure() for st in streams)        # noqa: E731
    halts: list[tuple] = []
    halted_day = None
    flatten_next = False
    streak, cur_day = 0, None
    sod = mark = acct.cash
    max_losses = shield.max_consecutive_losses if shield else 0

    def count_closed(day, t_idx):
        """Feed newly closed trades to the loss-streak rule. Returns True if it halts now."""
        nonlocal streak, halted_day
        fired = False
        for net, _st, reason in acct.closed:
            if shield is None or halted_day == day or reason == "shield":
                continue                                    # closed while halted: not counted
            streak = streak + 1 if net < 0 else 0
            if streak >= max_losses:
                halted_day, fired = day, True
                halts.append((index[t_idx], "loss_streak"))
        acct.closed.clear()
        return fired

    for j in range(i0, i1 + 1):
        day = DAY[j]
        if day != cur_day:
            cur_day, sod = day, mark                        # equity at the start of the UTC day
            if halted_day is not None and day > halted_day:
                halted_day, streak = None, 0                # resume with a clean counter
        for st in streams:
            st.open_exits(j, flatten_next)
        flatten_next = False
        if count_closed(day, j):                            # third loss booked at this open: flatten the rest now
            for st in streams:
                if st.pos:
                    st.carry = False
                    st.diag["flattened_by_halt"] += 1
                    st.can_act = False
                    st._close(j, st.O[j], "shield", 1, 0)
                st.cancel_order()
            acct.closed.clear()
        halted = shield is not None and halted_day == day
        for st in streams:
            st.open_entries(j, halted, gross_fn)
        for st in streams:
            st.intrabar(j)
        acct.cash += acct.pending
        acct.pending = 0.0
        mark = acct.cash + sum(st.unreal for st in streams if st.pos)
        eq[j] = mark
        inpos[j] = any(st.held for st in streams)
        new_halt = count_closed(day, j)
        if shield is not None and halted_day != day and sod > 0 and mark - sod <= -shield.daily_loss_pct * sod + 1e-9:
            halted_day, new_halt = day, True
            halts.append((index[j], "daily_loss"))
        if new_halt:
            flatten_next = True                             # positions go at the next open
            for st in streams:
                st.cancel_order()                           # resting entries are cancelled at once

    for st in streams:                                      # R11
        if st.pos:
            st._close(i1, st.C[i1], "end_of_data", 1, 2)
    acct.closed.clear()
    acct.cash += acct.pending
    eq[i1] = acct.cash
    cols = TRADE_COLS + ["setup", "symbol"]
    rows = [t for st in streams for t in st.trades]
    trades = pd.DataFrame(rows, columns=cols)
    if len(trades):
        trades = trades.sort_values(["exit_time", "entry_time"], kind="stable").reset_index(drop=True)
    equity = pd.Series(eq[i0:i1 + 1], index=index[i0:i1 + 1], name="equity")
    return PortfolioResult(trades, equity, pd.Series(inpos[i0:i1 + 1], index=index[i0:i1 + 1], name="in_position"),
                           pd.DataFrame(halts, columns=["time", "kind"]),
                           {f"{st.name}/{st.symbol}": dict(st.diag) for st in streams}, cfg, index[i0], index[i1], bar)


def portfolio_metrics(res: PortfolioResult) -> dict:
    e0 = res.cfg.start_equity
    m = M.equity_stats(res.equity, e0, res.bar)
    m.update(M.trade_stats(res.trades))
    m["exposure"] = float(res.in_position.mean())
    m["halts"] = int(len(res.halts))
    m["halts_daily_loss"] = int((res.halts["kind"] == "daily_loss").sum()) if len(res.halts) else 0
    m["halts_loss_streak"] = int((res.halts["kind"] == "loss_streak").sum()) if len(res.halts) else 0
    d = res.diagnostics.values()
    for k in ("blocked_gross_cap", "blocked_halt", "flattened_by_halt", "rejected_min_size"):
        m[k] = int(sum(x[k] for x in d))
    m["return_over_maxdd"] = m["net_return"] / m["max_dd"] if m["max_dd"] > 0 else float("nan")
    return m


def build_specs(items, upto=None, frames=None):
    """items: iterable of (module, symbol, params or None). Returns (specs, frames by symbol)."""
    from .engine import get_frame, module_signals_1h, to_ts
    specs, frames = [], dict(frames or {})
    for module, symbol, params in items:
        df1, sig1, mult = module_signals_1h(module, symbol, params, upto)
        frames.setdefault(symbol, df1)
        specs.append(StreamSpec(getattr(module, "NAME", module.__name__), symbol, sig1, mult))
    return specs, frames
