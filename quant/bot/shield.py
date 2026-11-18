"""Shield: the risk gate. It has the final say on every order and is the only thing that halts.

Order review   every entry gets an Approval (approve / block + reason); the exchange layer refuses
               anything without one. Blocks are journaled. Exits are always approved: Shield never
               stands between a position and its way out.
Halts          cancel resting orders, flatten positions at market (through the `executor` callback),
               write state/HALT with the reason.
                 scope "account" (everything):  stale_feed, exception, clock, kill, manual
                 scope "<setup id>" (one book): daily_loss, loss_streak
               P&L rules are per book on purpose: runners-up are separate paper accounts that exist
               to be compared with the leader, so one book's bad day must not stop the others.
Clearing       daily_loss -> next UTC day.   stale_feed -> feed healthy for stale_clear_s.
               loss_streak, exception, clock, kill, manual -> a human deletes state/HALT
               (and state/KILL first, for kill). Deleting HALT clears every halt listed in it.
State          state/shield.json (atomic). HALT mirrors the active halts for humans and run.sh.
"""
from __future__ import annotations

import math
import traceback
from pathlib import Path

from .config import Config
from .instruments import INSTRUMENTS, on_step, on_tick
from .journal import Journal
from .models import Approval, Intent, Trade
from .util import atomic_write_json, iso, now_ms, read_json, utc_day

ACCOUNT = "account"
NEEDS_HUMAN = {"loss_streak": True, "exception": True, "clock": True, "kill": True, "manual": True,
               "daily_loss": False, "stale_feed": False}
BLOCKS_RESTART = {"exception"}          # run.sh does not restart the process past these
EPS = 1e-9


class Shield:
    def __init__(self, cfg: Config, journal: Journal, executor=None):
        self.cfg = cfg
        self.lim = cfg.shield
        self.journal = journal
        self.executor = executor or (lambda setups, reason, now_ms: None)
        self.state_dir = Path(cfg.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "shield.json"
        self.halt_path = self.state_dir / "HALT"
        self.kill_path = self.state_dir / "KILL"
        self.halts: list[dict] = []
        self.books: dict[str, dict] = {}
        self.flags: dict[str, dict] = {}
        self.last_now: int | None = None
        self.healthy_since: int | None = None
        self._load()

    # ------------------------------------------------------------------ persistence
    def _book(self, setup: str) -> dict:
        return self.books.setdefault(setup, {"day": None, "sod_equity": None, "consecutive_losses": 0,
                                             "seen_trades": [], "blocks_today": 0, "blocks_total": 0,
                                             "last_block": None})

    def _load(self) -> None:
        raw = read_json(self.path) or {}
        self.halts = list(raw.get("halts", []))
        self.books = dict(raw.get("books", {}))
        self.flags = dict(raw.get("flags", {}))
        self.last_now = raw.get("last_now")
        self.healthy_since = None       # "healthy for 5 minutes" is always proven afresh by this process
        t = self.last_now or now_ms()
        if self.halts and not self.halt_path.exists():
            # HALT was deleted while the bot was down: that is the human's "resume"
            self._human_clear(t, "HALT file was deleted while the bot was not running")
        elif not self.halts and self.halt_path.exists():
            self._adopt_halt_file(t)
        self.save()

    def save(self) -> None:
        atomic_write_json(self.path, {"halts": self.halts, "books": self.books, "flags": self.flags,
                                      "last_now": self.last_now, "healthy_since": self.healthy_since})
        self._write_halt_file()

    def _write_halt_file(self) -> None:
        if not self.halts:
            self.halt_path.unlink(missing_ok=True)
            return
        atomic_write_json(self.halt_path, {
            "halted": True,
            "written_at": iso(now_ms()),
            "needs_human": any(h["needs_human"] for h in self.halts),
            "blocks_restart": any(h["kind"] in BLOCKS_RESTART for h in self.halts),
            "how_to_clear": "Delete this file to clear every halt listed here (remove state/KILL first if it "
                            "exists). daily_loss and stale_feed clear on their own.",
            "halts": self.halts,
        })

    def _adopt_halt_file(self, t: int) -> None:
        """A HALT file we did not write (a human's, or one left by a crash before shield.json was saved)."""
        try:
            text = self.halt_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        import json
        try:
            data = json.loads(text)
            halts = [h for h in data.get("halts", []) if isinstance(h, dict) and h.get("kind")]
        except (ValueError, AttributeError):
            halts = []
        if halts:
            for h in halts:
                h.setdefault("scope", ACCOUNT)
                h.setdefault("reason", "adopted from HALT file")
                h.setdefault("since_ms", t)
                h.setdefault("since", iso(h["since_ms"]))
                h["needs_human"] = bool(h.get("needs_human", True))
            self.halts = halts
            return
        self.halt(ACCOUNT, "manual", f"HALT file created by hand: {' '.join(text.split())[:200] or '(empty)'}", t)

    # ------------------------------------------------------------------ halts
    def halted(self, setup: str | None = None) -> list[dict]:
        """Active halts that stop `setup` (account-wide ones plus its own). None = account-wide only."""
        return [h for h in self.halts if h["scope"] == ACCOUNT or (setup is not None and h["scope"] == setup)]

    def any_halt(self) -> bool:
        return bool(self.halts)

    def _find(self, scope: str, kind: str) -> dict | None:
        for h in self.halts:
            if h["scope"] == scope and h["kind"] == kind:
                return h
        return None

    def halt(self, scope: str, kind: str, reason: str, t: int) -> bool:
        """Raise a halt. Returns False if the same (scope, kind) is already active."""
        if self._find(scope, kind) is not None:
            return False
        h = {"scope": scope, "kind": kind, "reason": reason, "since": iso(t), "since_ms": t,
             "needs_human": NEEDS_HUMAN.get(kind, True)}
        if kind == "stale_feed":
            self.healthy_since = None
        self.halts.append(h)
        self.save()                      # the halt is on disk before anything else happens
        self.journal.decision("halt", f"HALT {kind} ({scope}): {reason}", setup=None if scope == ACCOUNT else scope,
                              ts=t, scope=scope, halt_kind=kind, needs_human=h["needs_human"])
        try:
            self.executor(None if scope == ACCOUNT else [scope], f"halt:{kind}", t)
        except Exception as e:           # the halt stands even if flattening failed; positions stay flagged
            self.journal.decision("halt", f"flatten after HALT {kind} failed: {type(e).__name__}: {e}", ts=t,
                                  scope=scope, halt_kind=kind, flatten_failed=True)
        return True

    def clear(self, scope: str, kind: str, t: int, why: str) -> bool:
        h = self._find(scope, kind)
        if h is None:
            return False
        self.halts.remove(h)
        self.save()
        self.journal.decision("halt_clear", f"halt {kind} ({scope}) cleared: {why}",
                              setup=None if scope == ACCOUNT else scope, ts=t, scope=scope, halt_kind=kind)
        return True

    def _human_clear(self, t: int, why: str) -> None:
        kill = self.kill_path.exists()
        for h in list(self.halts):
            if h["kind"] == "kill" and kill:
                continue                 # KILL is still there: that halt stays
            self.halts.remove(h)
            if h["kind"] == "loss_streak":
                self._book(h["scope"])["consecutive_losses"] = 0
            if h["kind"] == "daily_loss":
                self._book(h["scope"])["sod_equity"] = None      # fresh -3% budget from the current equity
            self.journal.decision("halt_clear", f"halt {h['kind']} ({h['scope']}) cleared by a human: {why}",
                                  setup=None if h["scope"] == ACCOUNT else h["scope"], ts=t, scope=h["scope"],
                                  halt_kind=h["kind"])
        self.save()

    def poll_files(self, t: int) -> None:
        """Called every second: KILL created, HALT deleted, or HALT created by a human."""
        if self.kill_path.exists() and self._find(ACCOUNT, "kill") is None:
            try:
                note = " ".join(self.kill_path.read_text(encoding="utf-8", errors="replace").split())[:200]
            except OSError:
                note = ""
            self.halt(ACCOUNT, "kill", f"manual kill file state/KILL present{': ' + note if note else ''}", t)
        if self.halts and not self.halt_path.exists():
            self._human_clear(t, "HALT file deleted")
        elif not self.halts and self.halt_path.exists():
            self._adopt_halt_file(t)

    # ------------------------------------------------------------------ monitors
    def check_equity(self, setup: str, mark_equity: float, t: int) -> None:
        """Day roll + daily loss limit for one book. mark_equity includes open P&L."""
        b = self._book(setup)
        day = utc_day(t)
        if b["day"] != day:
            b["day"], b["sod_equity"], b["blocks_today"] = day, mark_equity, 0
            self.clear(setup, "daily_loss", t, f"new UTC day {day}")
            self.save()
        if b["sod_equity"] is None:
            b["sod_equity"] = mark_equity
            self.save()
        sod = b["sod_equity"]
        pnl = mark_equity - sod
        if sod > 0 and pnl <= -self.lim.daily_loss_pct * sod + EPS:
            self.halt(setup, "daily_loss",
                      f"day P&L {pnl:+.2f} USDT ({pnl / sod:+.2%}) hit the -{self.lim.daily_loss_pct:.0%} limit "
                      f"of start-of-day equity {sod:.2f} (UTC day {day})", t)

    def day_pnl(self, setup: str, mark_equity: float) -> float | None:
        sod = self._book(setup).get("sod_equity")
        return None if sod is None else mark_equity - sod

    def on_trade_closed(self, trade: Trade, t: int) -> None:
        if trade.setup not in {s.id for s in self.cfg.setups}:
            return                       # a retired book winding down
        b = self._book(trade.setup)
        if trade.id in b["seen_trades"]:
            return                       # replayed after a restart: count once
        b["seen_trades"] = (b["seen_trades"] + [trade.id])[-100:]
        b["consecutive_losses"] = b["consecutive_losses"] + 1 if trade.net < 0 else 0
        self.save()
        if b["consecutive_losses"] >= self.lim.max_consecutive_losses:
            self.halt(trade.setup, "loss_streak",
                      f"{b['consecutive_losses']} consecutive losing trades (last: {trade.id} {trade.symbol} "
                      f"net {trade.net:+.2f} USDT)", t)

    def check_feed(self, ages_s: dict, t: int) -> None:
        """ages_s: seconds since each symbol's last book tick (by exchange event time)."""
        worst_sym, worst = None, -1.0
        for sym, age in ages_s.items():
            if age is not None and age > worst:
                worst_sym, worst = sym, age
        if worst > self.lim.stale_feed_s:
            self.healthy_since = None
            self.halt(ACCOUNT, "stale_feed", f"feed stale: {worst_sym} last tick {worst:.0f} s ago "
                                             f"(limit {self.lim.stale_feed_s:.0f} s)", t)
            return
        if self._find(ACCOUNT, "stale_feed") is None:
            return
        if self.healthy_since is None:
            self.healthy_since = t
        elif t - self.healthy_since >= self.lim.stale_clear_s * 1000:
            self.healthy_since = None
            self.clear(ACCOUNT, "stale_feed", t, f"feed healthy for {self.lim.stale_clear_s:.0f} s")

    def check_clock(self, t: int, event_ms: int | None = None, prev_event_ms: int | None = None) -> None:
        skew = self.lim.max_clock_skew_s * 1000
        if self.last_now is not None and t < self.last_now - skew:
            self.halt(ACCOUNT, "clock", f"local clock moved backwards by {(self.last_now - t) / 1000:.1f} s", t)
        if self.last_now is None or t > self.last_now:
            self.last_now = t
        if event_ms is not None and event_ms - t > skew:
            self.halt(ACCOUNT, "clock", f"exchange event time is {(event_ms - t) / 1000:.1f} s ahead of the local "
                                        f"clock (limit {self.lim.max_clock_skew_s:.0f} s)", t)
        if event_ms is not None and prev_event_ms is not None and event_ms < prev_event_ms - skew:
            self.halt(ACCOUNT, "clock", f"event time went backwards by {(prev_event_ms - event_ms) / 1000:.1f} s", t)

    def clock_anomaly(self, reason: str, t: int) -> None:
        self.halt(ACCOUNT, "clock", reason, t)

    def on_exception(self, exc: BaseException, t: int, where: str = "") -> None:
        tb = traceback.extract_tb(exc.__traceback__)
        at = f"{Path(tb[-1].filename).name}:{tb[-1].lineno} in {tb[-1].name}" if tb else "unknown location"
        self.halt(ACCOUNT, "exception", f"unhandled {type(exc).__name__}: {exc} at {at}"
                                        + (f" ({where})" if where else ""), t)

    # ------------------------------------------------------------------ flags (reported, not halting)
    def flag(self, kind: str, reason: str, setup: str | None = None, symbol: str | None = None,
             now_ms: int | None = None) -> None:
        key = f"{kind}:{setup or '*'}:{symbol or '*'}"
        cur = self.flags.get(key)
        t = iso(now_ms) if now_ms is not None else None
        if cur is not None and cur["reason"] == reason:
            cur["last"] = t
            return
        self.flags[key] = {"kind": kind, "setup": setup, "symbol": symbol, "reason": reason,
                           "since": cur["since"] if cur else t, "last": t}
        self.save()

    def clear_flag(self, kind: str, setup: str | None = None, symbol: str | None = None) -> None:
        if self.flags.pop(f"{kind}:{setup or '*'}:{symbol or '*'}", None) is not None:
            self.save()

    # ------------------------------------------------------------------ order review
    def approve_exit(self, setup: str, symbol: str, reason: str) -> Approval:
        """Exits reduce risk. They are never blocked, halted or not."""
        return Approval(f"exit:{setup}:{symbol}", True, f"exit allowed ({reason})", issuer=self)

    def review(self, intent: Intent, *, book_equity: float, gross_notional: float, exposure: str | None,
               quote: tuple | None, now_ms: int) -> Approval:
        reason = self._first_objection(intent, book_equity, gross_notional, exposure, quote, now_ms)
        if reason is None:
            return Approval(intent.id, True, "within limits", issuer=self)
        if intent.setup in {s.id for s in self.cfg.setups}:
            b = self._book(intent.setup)
            b["blocks_today"] += 1
            b["blocks_total"] += 1
            b["last_block"] = {"ts": iso(now_ms), "symbol": intent.symbol, "reason": reason}
            self.save()
        self.journal.decision("block", f"BLOCKED {intent.id}: {reason}", setup=intent.setup, symbol=intent.symbol,
                              ts=now_ms, side=intent.side, qty=intent.qty, entry=intent.entry, stop=intent.stop,
                              target=intent.target, bid=quote[0] if quote else None,
                              ask=quote[1] if quote else None, order_id=intent.id)
        return Approval(intent.id, False, reason, issuer=self)

    def _first_objection(self, i: Intent, equity: float, gross: float, exposure: str | None,
                         quote: tuple | None, t: int) -> str | None:
        cfg, lim = self.cfg, self.lim
        if cfg.mode != "paper":
            return f"mode is {cfg.mode!r}: this build only trades paper"
        setups = {s.id: s for s in cfg.setups}
        if i.setup not in setups:
            return f"unknown setup {i.setup!r}"
        halts = self.halted(i.setup)
        if halts:
            h = halts[0]
            return f"halted: {h['kind']} ({h['scope']}) since {h['since']}: {h['reason']}"
        if i.symbol not in cfg.symbols or i.symbol not in setups[i.setup].symbols or i.symbol not in INSTRUMENTS:
            return f"{i.symbol} is not a configured symbol for {i.setup}"
        if i.side not in (1, -1):
            return f"side must be +1 or -1, got {i.side!r}"
        if not _finite_pos(i.stop):
            return "no stop: every order needs a protective stop price"
        if quote is None or t - quote[2] > lim.max_tick_age_s * 1000 or not (quote[0] > 0 and quote[1] >= quote[0]):
            age = "none" if quote is None else f"{(t - quote[2]) / 1000:.1f} s old"
            return f"no fresh quote for {i.symbol} ({age}, limit {lim.max_tick_age_s:.0f} s)"
        bid, ask = quote[0], quote[1]
        if i.entry is not None and not _finite_pos(i.entry):
            return f"entry price {i.entry!r} is not a valid limit"
        if i.entry is not None and not on_tick(i.symbol, i.entry):
            return f"entry {i.entry} is not on the exchange tick ({INSTRUMENTS[i.symbol].tick})"
        if not on_tick(i.symbol, i.stop):
            return f"stop {i.stop} is not on the exchange tick ({INSTRUMENTS[i.symbol].tick})"
        if i.side > 0:
            ref = ask if i.entry is None else min(i.entry, ask)       # where a buy would really fill
            immediate = i.entry is None or i.entry >= ask
            if i.stop >= ref or (immediate and i.stop >= bid):
                return f"stop on the wrong side: long entry ~{ref} needs a stop below it (bid {bid}), got {i.stop}"
        else:
            ref = bid if i.entry is None else max(i.entry, bid)
            immediate = i.entry is None or i.entry <= bid
            if i.stop <= ref or (immediate and i.stop <= ask):
                return f"stop on the wrong side: short entry ~{ref} needs a stop above it (ask {ask}), got {i.stop}"
        if i.target is not None:
            if not _finite_pos(i.target) or not on_tick(i.symbol, i.target):
                return f"target {i.target} is not on the exchange tick ({INSTRUMENTS[i.symbol].tick})"
            if (i.side > 0 and i.target <= ref) or (i.side < 0 and i.target >= ref):
                return f"target on the wrong side: {'long' if i.side > 0 else 'short'} entry ~{ref}, target {i.target}"
        ins = INSTRUMENTS[i.symbol]
        if not (isinstance(i.qty, (int, float)) and math.isfinite(i.qty) and i.qty > 0):
            return "size is zero (risk budget / stop distance is below one exchange step)"
        if not on_step(i.symbol, i.qty):
            return f"size {i.qty} is not on the exchange step ({ins.step})"
        if not equity > 0:
            return f"book equity is {equity:.2f}"
        notional = i.qty * ref
        if notional > cfg.max_leverage * equity * (1 + 1e-9):
            return (f"size above the leverage cap: notional {notional:.2f} > {cfg.max_leverage:g}x equity "
                    f"{equity:.2f}")
        risk = i.qty * abs(ref - i.stop)
        if risk > cfg.risk_per_trade * equity * (1 + 1e-6):
            return (f"size above the risk budget: {risk:.2f} USDT at risk > {cfg.risk_per_trade:.2%} of equity "
                    f"{equity:.2f}")
        if i.qty < float(ins.min_qty) or notional < float(ins.min_notional):
            return (f"size below the exchange minimum: {i.qty} {i.symbol} = {notional:.2f} USDT "
                    f"(min qty {ins.min_qty}, min notional {ins.min_notional} - assumed, see instruments.py)")
        if gross + notional > cfg.max_leverage * equity * (1 + 1e-9):
            return (f"gross exposure above the leverage cap: open {gross:.2f} + new {notional:.2f} > "
                    f"{cfg.max_leverage:g}x equity {equity:.2f}")
        if exposure == "position":
            return f"{i.setup} already has a position on {i.symbol} (one per setup and symbol)"
        if exposure == "order":
            return f"{i.setup} already has a resting order on {i.symbol} (one per setup and symbol)"
        if t - i.placed_ts > lim.max_decision_delay_s * 1000:
            return (f"late decision: the bar closed {(t - i.placed_ts) / 1000:.0f} s ago "
                    f"(limit {lim.max_decision_delay_s:.0f} s); the backtest assumes the order goes in at the open")
        return None

    # ------------------------------------------------------------------ reporting
    def status(self) -> dict:
        return {"halted": bool(self.halts), "halts": list(self.halts),
                "needs_human": any(h["needs_human"] for h in self.halts),
                "books": {k: {kk: vv for kk, vv in v.items() if kk != "seen_trades"} for k, v in self.books.items()},
                "flags": dict(self.flags), "healthy_since": iso(self.healthy_since)}


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x) and x > 0
