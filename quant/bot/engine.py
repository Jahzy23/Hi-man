"""The engine: turns feed events into decisions. Single thread, explicit time, everything on disk.

    book tick   -> funding settlement if due -> paper broker (fills, stops, targets, time exits)
    kline       -> stop safety net; on a CLOSED bar: persist it, then decide once per (setup, symbol)
    markPrice   -> funding rate / next funding time
    timer       -> Shield monitors (clock, KILL/HALT files, stale feed, daily loss), order expiry,
                   archive refresh, heartbeat, status file

A decision for bar t is made at most once (the book records the last signal bar it processed in the
same atomic write as the order), from the last row of signals() on the full history, and only when
that history is contiguous and complete for the strategy.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from . import instruments
from .archive import ArchiveRefresher, RefreshWorker, days_of
from .config import Config
from .history import HOUR_MS, MAYBE_UNKNOWN, History, index_ms
from .instruments import round_price
from .journal import Journal
from .models import Intent
from .paper import PaperBroker
from .shield import ACCOUNT, Shield
from .sizing import position_size
from .strategy_loader import LoadedStrategy, evaluate, load_strategy, probe
from .util import ROOT, TF_MS, atomic_write_json, iso, now_ms, read_json

FUNDING_FRESH_MS = 10_000      # the last pre-settlement rate must be this recent to count as observed
STATUS_EVERY_MS = 5_000
TIMER_EVERY_MS = 250


@dataclass
class Tick:
    bid: float = 0.0
    ask: float = 0.0
    e_ms: int = 0
    recv_ms: int = 0


@dataclass
class FundingState:
    last_mark: float | None = None
    last_r: float | None = None            # latest estimate for the NEXT settlement
    last_r_e_ms: int | None = None
    next_ms: int | None = None             # next funding time
    settled_ms: int = 0                    # last settlement we processed
    settled_rate: float | None = None      # its rate, None when it was not observed live
    settled_observed: bool = False
    watch_from_ms: int | None = None       # start of the current gap-free run of markPrice messages


class Engine:
    def __init__(self, cfg: Config, *, history: History | None = None, strategies: dict | None = None,
                 strategies_dir: Path | None = None, refresher=None, clock=now_ms):
        self.cfg = cfg
        self.clock = clock
        self.state_dir = Path(cfg.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.journal = Journal(cfg.log_dir, clock=clock)
        self.shield = Shield(cfg, self.journal, executor=self._execute_halt)
        self.broker = PaperBroker(cfg, now_ms=clock())
        self.history = history or History(cfg.symbols, self.state_dir)
        self.strategies: dict[str, LoadedStrategy] = strategies or {
            s.id: load_strategy(s.strategy, s.params, cfg.engine.default_warmup_bars, strategies_dir)
            for s in cfg.setups}
        self.worker = refresher            # RefreshWorker or None (tests)
        self.ticks = {s: Tick() for s in cfg.symbols}
        self.funding = {s: FundingState() for s in cfg.symbols}
        self._mark_seen = {s: False for s in cfg.symbols}     # in THIS run (persisted times do not count)
        self.pending: dict[int, set] = {}
        self._decided: set = set()
        self.feed_stats: dict = {}
        self.conn_events = 0
        self.started_ms = clock()
        self.last_alive_ms: int | None = None
        self._last_timer = 0
        self._last_status = 0
        self._status_lines = 0
        self._last_hb_slot: int | None = None
        self._first_hb_done = False
        self._last_refresh_req = 0
        self._started = False
        self.counters = {"ticks": 0, "klines": 0, "marks": 0, "bars": 0, "decisions": 0}
        self.on_decision = None            # optional observer(setup_id, symbol, Decision), used by the parity test
        snap = cfg.engine.snapshot_dir
        self.snapshot_dir = None if not snap else (Path(snap) if Path(snap).is_absolute() else ROOT / snap)
        self._snap_lines = 0
        self._last_snap = 0
        self._snap_now = False
        self._snap_failed = False

    # ================================================================== lifecycle
    def startup(self, now: int) -> None:
        cfg = self.cfg
        self.started_ms = now
        try:
            prev = read_json(self.state_dir / "status.json") or {}
        except ValueError:
            prev = {}                    # only a snapshot: unreadable means "last alive unknown"
        self.last_alive_ms = prev.get("ts_ms")
        self._load_funding_state()
        self.shield.check_clock(now)
        self.journal.decision(
            "start", f"bot started in {cfg.mode} mode: {len(cfg.setups)} setup(s) "
                     f"[{', '.join(f'{s.id}={s.strategy}' for s in cfg.setups)}] on {', '.join(cfg.symbols)}; "
                     f"equity {cfg.equity:g}/book, risk {cfg.risk_per_trade:.2%}, max leverage {cfg.max_leverage:g}x; "
                     f"exchange filters verified: {instruments.FILTERS_VERIFIED}",
            ts=now, pid=os.getpid(), config=cfg.source, last_alive=iso(self.last_alive_ms))
        self._handle_events(self.broker.startup_events, now)
        self.broker.startup_events = []
        self._reconcile_ledger(now)
        self._recover_after_outage(now)
        for p in self.broker.open_positions():
            if p.symbol not in cfg.symbols:
                why = (f"position {p.id} on {p.symbol} has no price feed: {p.symbol} is no longer in the config's "
                       f"symbols. Add it back so the position can be closed.")
                self.journal.decision("flag", why, setup=p.setup, symbol=p.symbol, ts=now)
                self.shield.flag("orphan_position", why, setup=p.setup, symbol=p.symbol, now_ms=now)
        self._probe_strategies(now)
        for sym in cfg.symbols:
            s = self.history.summary(sym, now)
            if s["missing_bars"] or s["archive_mismatches"]:
                self.journal.decision("history", f"{sym}: {s['missing_bars']} closed 1h bar(s) missing "
                                                 f"({s['first_missing']} .. {s['last_missing']}), "
                                                 f"{s['archive_mismatches']} own-bar/archive mismatch(es)",
                                      symbol=sym, ts=now, **s)
            if s["archive_mismatches"]:
                self.shield.flag("bar_mismatch", f"{s['archive_mismatches']} live bar(s) differ from the archive: "
                                                 f"{self.history.mismatches[sym][:3]}", symbol=sym, now_ms=now)
        for book in self.broker.active_books():           # day baseline exists before the first trade
            self.shield.check_equity(book.id, self.broker.mark_equity(book.id), now)
        self._request_refresh(now, force=True)
        self._started = True
        self._write_status(now)

    def shutdown(self, now: int, reason: str = "stop requested") -> None:
        self._save_funding_state()
        self._write_status(now, running=False)
        n_pos, n_ord = len(self.broker.open_positions()), len(self.broker.open_orders())
        self.journal.decision("stop", f"bot stopped ({reason}); {n_pos} open position(s) and {n_ord} resting order(s) "
                                      f"stay in state/books.json" + (
            "; stops are NOT watched while the bot is down - after an outage longer than "
            f"{self.cfg.shield.stale_feed_s:.0f} s they are flattened at the first tick" if n_pos or n_ord else ""),
                              ts=now)
        self.shield.save()
        self._maybe_snapshot(now, force=True)
        self.journal.close()

    def fatal(self, exc: BaseException, now: int, where: str = "") -> None:
        """Unhandled exception: Shield halts (flatten, HALT file, needs a human)."""
        self.shield.on_exception(exc, now, where)
        try:
            self._write_status(now, running=False)
        except Exception:
            pass

    def _reconcile_ledger(self, now: int) -> None:
        """books.json is the source of truth. A trade that closed right before a crash may be missing
        from trades.csv or unknown to Shield; both are idempotent to repair."""
        from .models import Trade
        have = self.journal.trade_ids()
        for book in self.broker.books.values():
            for td in book.recent_trades:
                if td["id"] not in have:
                    self.journal.trade(td)
                    self.journal.decision("recover", f"trade {td['id']} was closed before the last shutdown but "
                                                     f"missing from trades.csv; ledger repaired", setup=td["setup"],
                                          symbol=td["symbol"], ts=now)
                self.shield.on_trade_closed(Trade(**td), now)

    def _recover_after_outage(self, now: int) -> None:
        positions, orders = self.broker.open_positions(), self.broker.open_orders()
        # funding that settled while we were not watching: charge the last estimate we had, flagged
        for sym, f in self.funding.items():
            if f.next_ms is not None and f.next_ms <= now:
                if f.last_r is not None and f.last_mark is not None:
                    ev = self.broker.on_funding(sym, f.last_r, f.last_mark, f.next_ms, estimated=True)
                    self._handle_events(ev, now)
                f.settled_ms, f.next_ms = f.next_ms, None
            if f.next_ms is None:        # cannot prove that nothing settled while we were away
                f.settled_rate, f.settled_observed = None, False
            f.watch_from_ms = None
        if not positions and not orders:
            return
        limit = self.cfg.shield.stale_feed_s * 1000
        down = None if self.last_alive_ms is None else now - self.last_alive_ms
        if down is None or down > limit:
            how = "for an unknown time" if down is None else f"for {down / 1000:.0f} s"
            self.shield.halt(ACCOUNT, "stale_feed",
                             f"bot was down {how} with {len(positions)} open position(s) and {len(orders)} resting "
                             f"order(s) (last alive {iso(self.last_alive_ms)}); stops were not watched", now)
        else:
            self.journal.decision("recover", f"resumed after {down / 1000:.1f} s with {len(positions)} open "
                                             f"position(s) and {len(orders)} resting order(s); stops re-armed",
                                  ts=now)

    def _probe_strategies(self, now: int) -> None:
        """Find out which live-unavailable columns each strategy reads (see strategy_loader.probe)."""
        for setup in self.cfg.setups:
            strat = self.strategies[setup.id]
            frames = {}
            for sym in self.cfg.symbols:
                df, unk = self.history.frame(sym, strat.tf), self.history.unknown(sym, strat.tf)
                known = ~unk.any(axis=1).to_numpy()
                # the longest fully known prefix (the research archive up to the funding archive end)
                n = int(np.argmin(known)) if not known.all() else len(known)
                frames[sym] = df.iloc[:n]
            n = min(len(f) for f in frames.values())
            end = min(f.index[-1] for f in frames.values()) if n else None
            frames = {s: f.loc[:end] for s, f in frames.items()} if n else frames
            for sym in setup.symbols:
                if not n:                # no fully known rows to probe on: assume it reads everything
                    strat.needs[sym] = {c: "could not be probed (no fully known history)" for c in MAYBE_UNKNOWN}
                    strat.uses_ctx[sym] = True
                    continue
                strat.needs[sym], strat.uses_ctx[sym] = probe(strat, sym, frames)
            needs = sorted({c for d in strat.needs.values() for c in d})
            self.journal.decision(
                "start", f"setup {setup.id}: strategy {strat.name} on {strat.tf}, warm-up {strat.warmup} bars, "
                         f"reads live-unavailable columns: {needs or 'none'}, uses other symbols: "
                         f"{any(strat.uses_ctx.values())}", setup=setup.id, ts=now, params=strat.params,
                needs={s: d for s, d in strat.needs.items() if d})

    # ================================================================== feed events
    def on_event(self, ev: tuple, now: int) -> None:
        kind = ev[0]
        if kind == "book":
            self.on_book(ev[1], ev[2], ev[3], ev[4], now)
        elif kind == "kline":
            self.on_kline(ev[1], ev[2], ev[3], now)
        elif kind == "mark":
            self.on_mark(ev[1], ev[2], ev[3], ev[4], ev[5], now)
        elif kind == "conn":
            self.on_conn(ev[1], ev[2], ev[3], now)

    def on_conn(self, name: str, state: str, detail: str, now: int) -> None:
        self.conn_events += 1
        self.journal.decision("feed", f"feed connection '{name}' is {state}: {detail}", ts=now, conn=name, state=state)

    def on_book(self, sym: str, bid: float, ask: float, e_ms: int, now: int) -> None:
        tk = self.ticks.get(sym)
        if tk is None:
            return
        skew = self.cfg.shield.max_clock_skew_s * 1000
        if e_ms - now > skew or (tk.e_ms and e_ms < tk.e_ms - skew):
            self.shield.check_clock(now, e_ms, tk.e_ms or None)
            return
        if e_ms < tk.e_ms or not (bid > 0 and ask >= bid):
            return                       # out of order or broken quote: never trade on it
        tk.bid, tk.ask, tk.e_ms, tk.recv_ms = bid, ask, e_ms, now
        self.counters["ticks"] += 1
        self._settle_if_due(sym, e_ms, now)
        events = self.broker.on_book(sym, bid, ask, e_ms)
        if events:
            self._handle_events(events, now)

    def on_mark(self, sym: str, mark: float, rate: float, next_ms: int, e_ms: int, now: int) -> None:
        f = self.funding.get(sym)
        if f is None:
            return
        self.counters["marks"] += 1
        self._mark_seen[sym] = True
        if e_ms - now > self.cfg.shield.max_clock_skew_s * 1000:
            self.shield.check_clock(now, e_ms)
            return
        self._settle_if_due(sym, e_ms, now)          # uses the rate seen BEFORE this message
        if f.last_r_e_ms is None or e_ms - f.last_r_e_ms > FUNDING_FRESH_MS:
            f.watch_from_ms = e_ms                   # a gap: what happened in it was not observed
        f.last_mark, f.last_r, f.last_r_e_ms = mark, rate, e_ms
        if next_ms > f.settled_ms and next_ms > e_ms:
            f.next_ms = next_ms
        self.broker.set_funding_outlook(sym, rate, f.next_ms)

    def _settle_if_due(self, sym: str, ts: int, now: int) -> None:
        f = self.funding[sym]
        if f.next_ms is None or ts < f.next_ms or f.next_ms <= f.settled_ms:
            return
        t = f.next_ms
        observed = (f.last_r is not None and f.last_r_e_ms is not None and f.last_mark is not None
                    and t - f.last_r_e_ms <= FUNDING_FRESH_MS)
        f.settled_ms, f.next_ms = t, None
        f.settled_rate, f.settled_observed = (f.last_r if observed else None), observed
        events = []
        if f.last_r is not None and f.last_mark is not None:
            events = self.broker.on_funding(sym, f.last_r, f.last_mark, t, estimated=not observed)
        self._handle_events(events, now)
        if not events:
            self.journal.decision("funding", f"{sym} funding settled at {iso(t)}: rate "
                                             f"{'unknown' if f.last_r is None else format(f.last_r, '+.6%')}"
                                             f"{'' if observed else ' (not observed live)'}; no open position",
                                  symbol=sym, ts=now, rate=f.last_r, funding_time=iso(t), observed=observed)
        self._save_funding_state()

    def on_kline(self, sym: str, k: dict, e_ms: int, now: int) -> None:
        if sym not in self.ticks or k.get("i") != "1h":
            return
        self.counters["klines"] += 1
        t = int(k["t"])
        skew = self.cfg.shield.max_clock_skew_s * 1000
        if t % HOUR_MS != 0 or t > now + skew:
            self.shield.clock_anomaly(f"{sym} kline open time {iso(t)} is not a past hour boundary "
                                      f"(local clock {iso(now)})", now)
            return
        events = self.broker.on_bar_extreme(sym, t, float(k["h"]), float(k["l"]), max(e_ms, t))
        if events:
            self._handle_events(events, now)
        if not k.get("x"):
            return
        close_ms = t + HOUR_MS
        if close_ms > now + skew:
            self.shield.clock_anomaly(f"{sym} bar {iso(t)} reported closed {(close_ms - now) / 1000:.1f} s before "
                                      f"its close time by the local clock", now)
            return
        last = self.history.last_bar_ms(sym)
        if last is not None and t < last:
            return                       # an old bar replayed: ignore
        self._settle_if_due(sym, close_ms, now)
        f = self.funding[sym]
        fresh = f.last_r_e_ms is not None and close_ms - f.last_r_e_ms <= FUNDING_FRESH_MS
        watched = fresh and f.watch_from_ms is not None and f.watch_from_ms < close_ms
        if f.settled_ms == close_ms:
            paid = f.settled_rate if f.settled_observed else None
        elif watched and f.next_ms is not None and f.next_ms > close_ms:
            paid = 0.0                   # we were watching from before the close and nothing settled on it
        else:
            paid = None                  # e.g. started just after a funding time: it may have settled unseen
        rate = f.settled_rate if f.settled_observed else None
        bar = {"time_ms": t, "open": float(k["o"]), "high": float(k["h"]), "low": float(k["l"]),
               "close": float(k["c"]), "volume": float(k["v"]), "quote_volume": float(k["q"]),
               "trades": int(k["n"]), "taker_buy_base": float(k["V"]), "taker_buy_quote": float(k["Q"]),
               "funding_paid": paid, "funding_rate": rate, "recv_ms": now}
        if self.history.add_live_bar(sym, bar):
            self.counters["bars"] += 1
            self.journal.decision("bar", f"{sym} 1h bar {iso(t)} closed and persisted: O {bar['open']} H {bar['high']} "
                                         f"L {bar['low']} C {bar['close']} (received {(now - close_ms) / 1000:.2f} s "
                                         f"after the close)", symbol=sym, ts=now, bar=iso(t),
                                  funding_paid=paid, funding_rate=rate)
        if t in self._decided:
            return                       # duplicate close from the redundant socket, already decided
        self.pending.setdefault(t, set()).add(sym)
        if self.pending[t] >= set(self.cfg.symbols):
            self._decide(t, now)

    # ================================================================== decisions
    def _decide(self, bar_ms: int, now: int) -> None:
        """All decisions for the 1h bar opened at bar_ms, which has just closed."""
        self.pending.pop(bar_ms, None)
        for old in [b for b in self.pending if b < bar_ms]:
            self.pending.pop(old, None)
        self._decided.add(bar_ms)
        self._decided = {b for b in self._decided if b > bar_ms - 48 * HOUR_MS}
        self._snap_now = True            # a new bar is on disk: refresh the durable snapshot
        close_ms = bar_ms + HOUR_MS
        self._handle_events(self.broker.expire_orders(close_ms), now)
        for setup in self.cfg.setups:
            strat = self.strategies[setup.id]
            tf_ms = TF_MS[strat.tf]
            if close_ms % tf_ms != 0:
                continue                 # a 4h setup only decides when a 4h bar (00:00 UTC aligned) closes
            sig_bar = close_ms - tf_ms
            for sym in setup.symbols:
                self._decide_one(setup, strat, sym, sig_bar, close_ms, now)

    def _decide_one(self, setup, strat: LoadedStrategy, sym: str, sig_bar: int, close_ms: int, now: int) -> None:
        sid = setup.id
        done = self.broker.last_decision(sid, sym)
        if done is not None and done >= sig_bar:
            return                       # this bar was already decided (restart, duplicate event)
        self.counters["decisions"] += 1
        common = dict(setup=sid, symbol=sym, ts=now, bar=iso(sig_bar), tf=strat.tf)
        # 1. history must be contiguous for the warm-up window, for every frame the strategy reads
        ok, why = self.history.ready(sym, strat.tf, strat.warmup, sig_bar)
        if ok and strat.uses_ctx.get(sym, True):
            for other in self.cfg.symbols:
                if other != sym:
                    ok, why = self.history.ready(other, strat.tf, strat.warmup, sig_bar)
                    if not ok:
                        why = f"context symbol {other}: {why}"
                        break
        if not ok:
            self.journal.decision("warming", f"WARMING: {why}", **common)
            self.shield.flag("warming", f"WARMING: {why}", setup=sid, symbol=sym, now_ms=now)
            self.broker.set_last_decision(sid, sym, sig_bar)
            return
        self.shield.clear_flag("warming", setup=sid, symbol=sym)
        # 2. the strategy must not read a column that is unknown live
        needs = strat.needs.get(sym, {})
        if needs:
            syms = self.cfg.symbols if strat.uses_ctx.get(sym, True) else [sym]
            missing = {}
            for s in syms:
                for c, n in self.history.unknown_columns(s, strat.tf, strat.warmup).items():
                    if c in needs:
                        missing[c] = max(missing.get(c, 0), n)
            if missing:
                cols = ", ".join(f"{c} (unknown on {n} of the last {strat.warmup} bars)" for c, n in missing.items())
                why = (f"REFUSED: {strat.name} reads {cols}; that data is not available live, so no decision is "
                       f"made rather than trading on NaN")
                self.journal.decision("refuse", why, **common, columns=sorted(missing))
                self.shield.flag("refuse", why, setup=sid, symbol=sym, now_ms=now)
                self.broker.set_last_decision(sid, sym, sig_bar)
                return
        self.shield.clear_flag("refuse", setup=sid, symbol=sym)
        # 3. the decision is the last closed row of signals() on the full frame
        df = self.history.frame(sym, strat.tf)
        last = df.index[-1]              # == sig_bar (ready() checked it); nothing later may reach the strategy
        ctx = {s: self.history.frame(s, strat.tf).loc[:last] for s in self.cfg.symbols}
        d = evaluate(strat, df, ctx)
        if self.on_decision is not None:
            self.on_decision(sid, sym, d)
        if d.bar_ms != sig_bar:
            raise RuntimeError(f"{sid}/{sym}: decision row {iso(d.bar_ms)} is not the closed bar {iso(sig_bar)}")
        # 4. backtester rule R7. A signal is acted on when the book was flat at the bar close, or when
        #    this very row schedules the exit of the open position (exit flag / max_hold): the position
        #    leaves at the open and the new signal goes in at the same open (stop-and-reverse).
        #    Any other signal that arrives while a position is open is dropped; so is a signal while an
        #    entry limit is still resting.
        book = self.broker.book(sid)
        sig = dict(side=d.side, entry=d.entry, stop=d.stop, target=d.target, exit_flag=d.exit)
        pos = book.positions.get(sym)
        last_exit = book.last_exit.get(sym)
        closed_after_close = pos is None and last_exit is not None and last_exit["ts"] >= close_ms
        open_at_close = pos is not None or closed_after_close
        if pos is not None:
            time_due = pos.time_exit_ts is not None and pos.time_exit_ts <= close_ms
            exit_due = d.exit or time_due
        else:
            exit_due = closed_after_close and (d.exit or bool(last_exit.get("scheduled")))
        if pos is not None and exit_due:
            reason = "signal" if d.exit else "time"
            self.journal.decision("signal", f"{'exit signal' if d.exit else 'max_hold reached'} at the bar close: "
                                            f"exiting at the open", **common, **sig)
            ev = self.broker.close_position(sid, sym, reason, self.shield.approve_exit(sid, sym, reason),
                                            max(close_ms, self.ticks[sym].e_ms),
                                            max_quote_age_ms=int(self.cfg.shield.max_tick_age_s * 1000))
            if not ev:
                self.journal.decision("exit_pending", "no fresh quote for the exit: it executes at the next tick",
                                      **common)
            self._handle_events(ev, now)
        if d.side == 0:
            if not (pos is not None and exit_due):
                self.journal.decision("eval", "in a position; no exit signal at bar close" if pos is not None
                                      else "no signal at bar close", **common)
        elif open_at_close and not exit_due:
            self.journal.decision("signal", f"{_side(d.side)} signal dropped: a position was open at the bar close "
                                            f"and this row does not schedule its exit (one position per setup and "
                                            f"symbol)", **common, **sig)
        elif self.broker.has_exposure(sid, sym) == "position":
            self.journal.decision("signal", f"{_side(d.side)} signal not entered: the exit of the old position is "
                                            f"still waiting for a quote", **common, **sig)
        elif self.broker.has_exposure(sid, sym) == "order":
            self.journal.decision("signal", f"{_side(d.side)} signal ignored: an entry order is already resting",
                                  **common, **sig)
        else:
            self._enter(setup, strat, sym, d, sig_bar, close_ms, now, common,
                        reversal=open_at_close and exit_due)
        self.broker.set_last_decision(sid, sym, sig_bar)

    def _enter(self, setup, strat, sym, d, sig_bar, close_ms, now, common, reversal: bool = False) -> None:
        sid, cfg = setup.id, self.cfg
        tk = self.ticks[sym]
        quote = (tk.bid, tk.ask, tk.e_ms) if tk.e_ms else None
        stop = round_price(sym, d.stop) if d.stop is not None and d.stop > 0 else None
        target = round_price(sym, d.target) if d.target is not None and d.target > 0 else None
        market_px = None if quote is None else (tk.ask if d.side > 0 else tk.bid)   # where a taker fills now
        if d.entry is None:
            entry, ref = None, market_px                 # R9: sized from the price seen when the order is sent
        else:
            # a passive limit is never rounded towards the market
            entry = round_price(sym, d.entry, "down" if d.side > 0 else "up") if d.entry > 0 else d.entry
            through = market_px is not None and (market_px <= entry if d.side > 0 else market_px >= entry)
            ref = market_px if through else entry        # R1: through the market on arrival = taker at the market
        equity = self.broker.equity(sid)
        qty = position_size(sym, equity, ref, stop, cfg.risk_per_trade, cfg.max_leverage) if ref and stop else 0.0
        intent = Intent(id=f"{sid}:{sym}:{iso(sig_bar)}", setup=sid, symbol=sym, side=d.side, qty=qty, entry=entry,
                        ref_price=ref or 0.0, stop=stop, target=target, max_hold=d.max_hold,
                        entry_valid=d.entry_valid, tf=strat.tf, signal_bar=sig_bar, placed_ts=close_ms)
        self.journal.decision(
            "signal", f"{_side(d.side)} signal at the close of {iso(sig_bar)}"
                      f"{' (reversal: the old position left at this open)' if reversal else ''}: "
                      f"{'marketable' if entry is None else f'limit {entry}'}, stop {stop}, target {target}, "
                      f"size {qty} (risk {cfg.risk_per_trade:.2%} of {equity:.2f})",
            **common, side=d.side, qty=qty, entry=entry, stop=stop, target=target,
            bid=tk.bid or None, ask=tk.ask or None, raw_entry=d.entry, raw_stop=d.stop, raw_target=d.target)
        approval = self.shield.review(intent, book_equity=equity, gross_notional=self.broker.gross_notional(sid),
                                      exposure=self.broker.has_exposure(sid, sym), quote=quote, now_ms=now)
        if not approval.approved:
            return                       # Shield journaled the block
        ev = self.broker.submit_entry(intent, approval, max(close_ms, tk.e_ms))
        self._handle_events(ev, now)

    # ================================================================== event plumbing
    def _handle_events(self, events: list, now: int) -> None:
        for e in events:
            e = dict(e)
            kind = e.pop("kind")
            reason = e.pop("reason")
            trade = e.pop("trade", None)
            ev_ts = e.pop("ts", None)
            self.journal.decision(kind, reason, setup=e.pop("setup", None), symbol=e.pop("symbol", None), ts=now,
                                  event_time=iso(ev_ts), **e)
            self._snap_now = True        # orders, fills, exits: the snapshot follows within seconds
            if trade is not None:
                self.journal.trade(trade)
                self.shield.on_trade_closed(trade, now)

    def _execute_halt(self, setups, reason: str, now: int) -> None:
        """Shield's executor: cancel resting orders, flatten at market (or at the next tick)."""
        events = self.broker.flatten(setups, reason, now, int(self.cfg.shield.max_tick_age_s * 1000))
        self._handle_events(events, now)
        waiting = [p for p in self.broker.open_positions() if p.flatten_reason]
        if waiting:
            self.journal.decision("exit_pending", f"{len(waiting)} position(s) have no fresh quote and will be "
                                                  f"flattened at their next tick: "
                                                  f"{[f'{p.setup}/{p.symbol}' for p in waiting]}", ts=now)

    # ================================================================== timer
    def on_timer(self, now: int) -> None:
        self._last_timer = now
        sh = self.shield
        sh.check_clock(now)
        sh.poll_files(now)
        ages = {s: (now - max(t.e_ms, self.started_ms)) / 1000.0 for s, t in self.ticks.items()}
        for s, f in self.funding.items():        # markPrice arrives every second on the kline connection
            seen = f.last_r_e_ms if self._mark_seen[s] else 0
            ages[f"{s}@mark"] = (now - max(seen, self.started_ms)) / 1000.0
        sh.check_feed(ages, now)
        for book in self.broker.active_books():
            sh.check_equity(book.id, self.broker.mark_equity(book.id), now)
        self._handle_events(self.broker.expire_orders(now), now)
        grace = self.cfg.engine.bar_close_grace_s * 1000
        for bar_ms in sorted(self.pending):
            if now >= bar_ms + HOUR_MS + grace:
                missing = sorted(set(self.cfg.symbols) - self.pending[bar_ms])
                self.journal.decision("history", f"bar {iso(bar_ms)}: no close received for {missing} within "
                                                 f"{grace / 1000:.0f} s; deciding without them", ts=now)
                self._decide(bar_ms, now)
        self._collect_refresh(now)
        self._request_refresh(now)
        self._heartbeat(now, ages)
        news = self.journal.lines != self._status_lines and now - self._last_status >= 1000
        if news or now - self._last_status >= STATUS_EVERY_MS:
            self._write_status(now)
        self._maybe_snapshot(now)

    def _maybe_snapshot(self, now: int, force: bool = False) -> None:
        """Copy the durable state to the git-tracked snapshot directory (bot/snapshot.py). Runs after
        the status file is fresh; a failure is reported and never stops trading."""
        if self.snapshot_dir is None:
            return
        due = self.journal.lines != self._snap_lines and now - self._last_snap >= self.cfg.engine.snapshot_min * 60_000
        if not (force or due or (self._snap_now and now - self._last_snap >= 5_000)):
            return
        self._last_snap, self._snap_now = now, False
        try:
            from .snapshot import take
            take(self.state_dir, self.cfg.log_dir, self.snapshot_dir, now_ms=now)
            self._snap_lines = self.journal.lines
            self.shield.clear_flag("snapshot_error")
        except Exception as e:           # disk, permissions, a credential found in a file: report, keep trading
            why = f"snapshot to {self.snapshot_dir} failed: {type(e).__name__}: {e}"
            self.shield.flag("snapshot_error", why, now_ms=now)
            if not self._snap_failed:
                self.journal.decision("flag", why, ts=now)
            self._snap_failed = True
            self._snap_lines = self.journal.lines
        else:
            self._snap_failed = False

    def _heartbeat(self, now: int, ages: dict) -> None:
        hb_ms = self.cfg.engine.heartbeat_s * 1000
        slot = now // hb_ms
        all_ticking = all(t.e_ms for t in self.ticks.values())
        first = not self._first_hb_done and (all_ticking or now - self.started_ms > 10_000)
        if self._last_hb_slot is None:
            self._last_hb_slot = slot
        if not first and slot == self._last_hb_slot:
            return
        self._first_hb_done, self._last_hb_slot = True, slot
        worst = max(ages.values()) if ages else 0.0
        n_pos, n_ord = len(self.broker.open_positions()), len(self.broker.open_orders())
        last_closed = now - now % HOUR_MS - HOUR_MS
        missing = {s: len(self.history.missing_bars(s, last_closed)) for s in self.cfg.symbols}
        halts = [f"{h['kind']}({h['scope']})" for h in self.shield.halts]
        gap = "contiguous" if not any(missing.values()) else f"gap {missing} -> WARMING"
        self.journal.decision(
            "heartbeat", f"alive: oldest tick {worst:.1f} s, {n_pos} position(s), {n_ord} resting order(s), "
                         f"shield {'HALT ' + ','.join(halts) if halts else 'ok'}, history {gap}",
            ts=now, tick_age_s={s: round(a, 2) for s, a in ages.items()},
            quotes={s: [t.bid, t.ask] for s, t in self.ticks.items()},
            equity={b.id: round(self.broker.mark_equity(b.id), 4) for b in self.broker.active_books()},
            missing_bars=missing, feed=self.feed_stats, counters=dict(self.counters))

    # ================================================================== archive refresh
    def _request_refresh(self, now: int, force: bool = False) -> None:
        if self.worker is None:
            return
        every = self.cfg.engine.archive_refresh_min * 60_000
        if not force and now - self._last_refresh_req < every:
            return
        self._last_refresh_req = now
        today = dt.datetime.fromtimestamp(now / 1000, tz=dt.timezone.utc).date()
        yesterday = today - dt.timedelta(days=1)
        last_closed = now - now % HOUR_MS - HOUR_MS
        for sym in self.cfg.symbols:
            days = days_of(self.history.missing_bars(sym, last_closed), yesterday)
            months = self._missing_funding_months(sym, today)
            if days or months:
                self.worker.request(sym, days, months)

    def _missing_funding_months(self, sym: str, today: dt.date) -> list:
        cov_end = self.history.funding_coverage_end(sym)  # first instant not covered by archived funding
        out, y, m = [], cov_end.year, cov_end.month
        while (y, m) < (today.year, today.month):         # only complete months can be published
            out.append((y, m))
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        return out

    def _collect_refresh(self, now: int) -> None:
        if self.worker is None:
            return
        for res in self.worker.take_results():
            sym = res["symbol"]
            if res["error"]:
                self.journal.decision("archive", f"{sym}: archive refresh failed: {res['error']}", symbol=sym, ts=now)
                self.shield.flag("archive_error", res["error"], symbol=sym, now_ms=now)
                continue
            self.shield.clear_flag("archive_error", symbol=sym)
            if res["added"]:
                self.history.reload(sym)
            s = self.history.summary(sym, now)
            self.journal.decision("archive", f"{sym}: archive refresh added {res['added']} row(s); not published yet: "
                                             f"{res['missing'] or 'nothing'}; {s['missing_bars']} bar(s) still missing",
                                  symbol=sym, ts=now, added=res["added"], not_published=res["missing"])
            if s["archive_mismatches"]:
                self.shield.flag("bar_mismatch", f"{s['archive_mismatches']} live bar(s) differ from the archive: "
                                                 f"{self.history.mismatches[sym][:3]}", symbol=sym, now_ms=now)

    # ================================================================== persistence of small things
    def _load_funding_state(self) -> None:
        try:
            raw = read_json(self.state_dir / "market.json") or {}
        except ValueError:
            raw = {}
        for sym, d in raw.get("funding", {}).items():
            if sym in self.funding:
                self.funding[sym] = FundingState(**{k: v for k, v in d.items()
                                                    if k in FundingState.__dataclass_fields__})

    def _save_funding_state(self) -> None:
        atomic_write_json(self.state_dir / "market.json",
                          {"funding": {s: asdict(f) for s, f in self.funding.items()}})

    def _write_status(self, now: int, running: bool = True) -> None:
        self._last_status = now
        self._status_lines = self.journal.lines
        self._save_funding_state()
        books = {}
        for b in self.broker.books.values():
            mark = self.broker.mark_equity(b.id)
            books[b.id] = {
                "strategy": b.strategy, "role": b.role, "active": b.active, "start_equity": b.start_equity,
                "equity": b.cash, "mark_equity": mark, "unrealized": mark - b.cash,
                "day_pnl": self.shield.day_pnl(b.id, mark) if b.active else None,
                "total_pnl": mark - b.start_equity, "trades": b.n_trades, "wins": b.n_wins, "losses": b.n_losses,
                "fees": b.sum_fees, "funding": b.sum_funding,
                "positions": [asdict(p) for p in b.positions.values()],
                "orders": [asdict(o) for o in b.orders.values()]}
        strategies = {sid: {"strategy": s.name, "tf": s.tf, "warmup": s.warmup,
                            "needs": {k: sorted(v) for k, v in s.needs.items() if v}}
                      for sid, s in self.strategies.items()}
        atomic_write_json(self.state_dir / "status.json", {
            "ts_ms": now, "ts": iso(now), "running": running, "pid": os.getpid(), "started": iso(self.started_ms),
            "mode": self.cfg.mode, "config": self.cfg.source,
            "ticks": {s: {"bid": t.bid, "ask": t.ask, "event_ms": t.e_ms, "age_s": round((now - t.e_ms) / 1000, 2)
                          if t.e_ms else None} for s, t in self.ticks.items()},
            "funding": {s: asdict(f) for s, f in self.funding.items()},
            "feed": self.feed_stats, "counters": self.counters, "books": books, "strategies": strategies,
            "shield": self.shield.status(),
            "history": {s: self.history.summary(s, now) for s in self.cfg.symbols},
            "filters_verified": instruments.FILTERS_VERIFIED})

    # ================================================================== main loop
    def run(self, feed, should_stop, max_seconds: float | None = None) -> None:
        self.startup(self.clock())
        deadline = None if max_seconds is None else self.clock() + int(max_seconds * 1000)
        while not should_stop():
            ev = feed.get(timeout=TIMER_EVERY_MS / 1000.0)
            now = self.clock()
            if ev is not None:
                self.on_event(ev, now)
            if now - self._last_timer >= TIMER_EVERY_MS:
                self.feed_stats = feed.stats()
                self.on_timer(now)
            if deadline is not None and now >= deadline:
                break


def _side(side: int) -> str:
    return "long" if side > 0 else "short"
