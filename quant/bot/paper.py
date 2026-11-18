"""Paper broker: the SPEC execution model, driven by live best bid/ask ticks.

One Book per setup (leader and runners-up are separate $ accounts). All state lives in
state/books.json, rewritten atomically after every change, so a kill at any instant resumes with the
same orders, positions and stops.

How SPEC's bar rules map onto book ticks (documented in BUILD_LOG.md, section Architecture):
  resting BUY limit L   fills at L (maker) when best ask <= L  - an offer can only reach L after every
                        bid resting at L (ours included) has been consumed: the market traded through.
                        A bid merely touching or dropping below L proves nothing and does not fill.
  resting SELL limit L  fills at L (maker) when best bid >= L.
  marketable entry      buys the ask * (1 + slip), sells the bid * (1 - slip), taker fee.
  stop (long)           triggers when best bid <= stop; fills at min(stop, bid) * (1 - 2*slip), taker.
                        min() is SPEC's "gap through stop -> fill at the open": the first price we see
                        after a gap, never the (better) stop price.
  target (long)         resting sell limit: fills at target (maker) when best bid >= target.
  time / signal exits   sell the bid * (1 - slip), taker.
  same instant          the stop is checked before the target and before the time exit.
Shorts are the mirror image. Slippage-adjusted fill prices are model prices and are not snapped to
the tick grid; order prices (limit, stop, target) and quantities are.

The backtester (lab/engine.py) lists the choices it made where SPEC is silent as rules R1-R12; this
broker follows them: R1 a limit that is through the market on arrival is a taker fill at the market;
R2 a resting limit fills at its own price; R4/R5 stop before target, a scheduled exit beyond the stop
is a stop and beyond the target fills the target; R6 max_hold counts the fill bar as bar 1; R10 the
cost-only funding charge for a stop/target exit inside a bar that ends on a settlement; R12 lot step
and minimum size (enforced by sizing + Shield). R7 (which signals are acted on) lives in engine.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import Config
from .exchange import Exchange, require_approval
from .models import Approval, Intent, Order, Position, Trade
from .util import TF_MS, atomic_write_json, floor_ms, read_json

STATE_VERSION = 1
KEEP_RECENT_TRADES = 200


@dataclass
class Book:
    id: str
    strategy: str
    fingerprint: str
    role: str
    start_equity: float
    cash: float                                   # realised equity: start + closed P&L - fees + funding
    active: bool = True
    created_ts: int = 0
    orders: dict = field(default_factory=dict)        # symbol -> Order
    positions: dict = field(default_factory=dict)     # symbol -> Position
    last_decision: dict = field(default_factory=dict)  # symbol -> bar open ms of the last processed signal bar
    last_exit: dict = field(default_factory=dict)      # symbol -> {"ts", "reason", "scheduled"} of the last close
    trade_seq: int = 0
    n_trades: int = 0
    n_wins: int = 0
    n_losses: int = 0
    sum_gross: float = 0.0
    sum_fees: float = 0.0
    sum_funding: float = 0.0
    recent_trades: list = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["orders"] = {s: asdict(o) for s, o in self.orders.items()}
        d["positions"] = {s: asdict(p) for s, p in self.positions.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Book":
        d = dict(d)
        orders = {s: _build(Order, o) for s, o in d.pop("orders", {}).items()}
        positions = {s: _build(Position, p) for s, p in d.pop("positions", {}).items()}
        b = _build(cls, d)
        b.orders, b.positions = orders, positions
        return b


def _build(cls, d: dict):
    names = cls.__dataclass_fields__
    return cls(**{k: v for k, v in d.items() if k in names})


class PaperBroker(Exchange):
    name = "paper"

    def __init__(self, cfg: Config, now_ms: int = 0):
        self.cfg = cfg
        self.path = Path(cfg.state_dir) / "books.json"
        self.books: dict[str, Book] = {}
        self.quotes: dict[str, tuple] = {}            # symbol -> (bid, ask, ts)
        self.funding_outlook: dict[str, tuple] = {}   # symbol -> (estimated rate, next funding ms), from markPrice
        self.startup_events: list[dict] = []
        self._busy: set = set()
        self._load()
        self._sync_books(now_ms)
        self._reindex()

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        raw = read_json(self.path)
        if not raw:
            return
        if raw.get("version") != STATE_VERSION:
            raise RuntimeError(f"{self.path}: unknown state version {raw.get('version')!r}")
        self.books = {k: Book.from_dict(v) for k, v in raw["books"].items()}

    def _save(self) -> None:
        atomic_write_json(self.path, {"version": STATE_VERSION,
                                      "books": {k: b.to_dict() for k, b in self.books.items()}})
        self._reindex()

    def _reindex(self) -> None:
        self._busy = {s for b in self.books.values() for s in (*b.orders, *b.positions)}

    def _sync_books(self, now_ms: int) -> None:
        """Make the books match the config. A setup whose strategy/params/symbols changed, or that
        was removed, is retired under '<id>@<fingerprint>': its orders are cancelled now, its
        positions leave at the next tick, and its P&L stays on file. The id starts a fresh book."""
        wanted = {s.id: s for s in self.cfg.setups}
        changed = False
        for key in list(self.books):
            b = self.books[key]
            if not b.active:
                continue
            s = wanted.get(key)
            if s is not None and s.fingerprint == b.fingerprint:
                if b.role != s.role:
                    b.role, changed = s.role, True
                continue
            why = "setup_changed" if s is not None else "setup_removed"
            new_key, n = f"{key}@{b.fingerprint}", 1
            while new_key in self.books:
                n += 1
                new_key = f"{key}@{b.fingerprint}.{n}"
            del self.books[key]
            b.id, b.active = new_key, False
            for sym, o in list(b.orders.items()):
                o.setup = new_key
                self.startup_events.append(self._cancel(b, sym, f"{why}: book retired", now_ms))
            for p in b.positions.values():
                p.setup, p.flatten_reason = new_key, why
            self.books[new_key] = b
            changed = True
        for s in self.cfg.setups:
            if s.id not in self.books:
                self.books[s.id] = Book(id=s.id, strategy=s.strategy, fingerprint=s.fingerprint, role=s.role,
                                        start_equity=self.cfg.equity, cash=self.cfg.equity, created_ts=now_ms)
                changed = True
        if changed:
            self._save()

    # ------------------------------------------------------------------ queries
    def book(self, setup: str) -> Book:
        return self.books[setup]

    def active_books(self) -> list[Book]:
        return [b for b in self.books.values() if b.active]

    def open_positions(self) -> list[Position]:
        return [p for b in self.books.values() for p in b.positions.values()]

    def open_orders(self) -> list[Order]:
        return [o for b in self.books.values() for o in b.orders.values()]

    def equity(self, setup: str) -> float:
        return self.books[setup].cash

    def unrealized(self, setup: str) -> float:
        """Mark-to-market at the side of the book we would exit on (bid for longs, ask for shorts)."""
        total = 0.0
        for sym, p in self.books[setup].positions.items():
            q = self.quotes.get(sym)
            if q is None:
                continue
            px = q[0] if p.side > 0 else q[1]
            total += p.side * p.qty * (px - p.entry_price)
        return total

    def mark_equity(self, setup: str) -> float:
        return self.books[setup].cash + self.unrealized(setup)

    def gross_notional(self, setup: str) -> float:
        b = self.books[setup]
        return (sum(p.qty * p.entry_price for p in b.positions.values())
                + sum(o.qty * o.limit for o in b.orders.values()))

    def has_exposure(self, setup: str, symbol: str) -> str | None:
        b = self.books[setup]
        if symbol in b.positions:
            return "position"
        if symbol in b.orders:
            return "order"
        return None

    def last_decision(self, setup: str, symbol: str) -> int | None:
        return self.books[setup].last_decision.get(symbol)

    def set_last_decision(self, setup: str, symbol: str, bar_ms: int) -> None:
        self.books[setup].last_decision[symbol] = bar_ms
        self._save()

    # ------------------------------------------------------------------ Exchange API
    def submit_entry(self, intent: Intent, approval: Approval, ts: int) -> list[dict]:
        require_approval(intent.id, approval)
        book = self.books.get(intent.setup)
        if book is None or not book.active:
            raise RuntimeError(f"{intent.id}: no active book for setup {intent.setup!r}")
        sym = intent.symbol
        if intent.stop is None:
            raise RuntimeError(f"{intent.id}: refusing an order without a stop")
        if intent.side not in (1, -1) or not intent.qty > 0:
            raise RuntimeError(f"{intent.id}: bad side/qty {intent.side}/{intent.qty}")
        if sym in book.positions or sym in book.orders:
            raise RuntimeError(f"{intent.id}: {intent.setup} already has a {self.has_exposure(intent.setup, sym)} "
                               f"on {sym}")
        ts = max(ts, intent.placed_ts)
        slip = self.cfg.slip(sym)
        q = self.quotes.get(sym)
        events: list[dict] = []
        if intent.entry is None:
            if q is None:
                raise RuntimeError(f"{intent.id}: marketable order but no quote for {sym}")
            price = q[1] * (1 + slip) if intent.side > 0 else q[0] * (1 - slip)
            events.append(self._open(book, intent, price, ts, "market"))
        else:
            limit = intent.entry
            crosses = q is not None and (q[1] <= limit if intent.side > 0 else q[0] >= limit)
            if crosses:      # R1: a limit that is through the market on arrival is a taker fill at the market
                price = q[1] * (1 + slip) if intent.side > 0 else q[0] * (1 - slip)
                events.append(self._open(book, intent, price, ts, "limit_marketable"))
            else:
                o = Order(id=intent.id, setup=book.id, symbol=sym, side=intent.side, qty=intent.qty, limit=limit,
                          stop=intent.stop, target=intent.target, max_hold=intent.max_hold, tf=intent.tf,
                          signal_bar=intent.signal_bar, placed_ts=intent.placed_ts,
                          expires_ts=intent.placed_ts + max(1, int(intent.entry_valid)) * TF_MS[intent.tf])
                book.orders[sym] = o
                events.append({"kind": "order", "setup": book.id, "symbol": sym, "ts": ts, "side": o.side,
                               "qty": o.qty, "limit": o.limit, "stop": o.stop, "target": o.target,
                               "expires_ts": o.expires_ts, "order_id": o.id,
                               "reason": f"resting {'buy' if o.side > 0 else 'sell'} limit {o.qty} @ {o.limit}, "
                                         f"stop {o.stop}, live for {intent.entry_valid} bar(s)"})
        book.last_decision[sym] = intent.signal_bar     # same atomic write as the order: no double entry
        self._save()
        return events

    def cancel_entry(self, setup: str, symbol: str, reason: str, ts: int) -> list[dict]:
        book = self.books[setup]
        if symbol not in book.orders:
            return []
        ev = [self._cancel(book, symbol, reason, ts)]
        self._save()
        return ev

    def close_position(self, setup: str, symbol: str, reason: str, approval: Approval, ts: int,
                       max_quote_age_ms: int | None = None) -> list[dict]:
        require_approval(f"exit:{setup}:{symbol}", approval)
        book = self.books[setup]
        p = book.positions.get(symbol)
        if p is None:
            return []
        events = self._market_exit(book, p, reason, ts, max_quote_age_ms)
        self._save()
        return events

    # ------------------------------------------------------------------ simulation inputs
    def on_book(self, symbol: str, bid: float, ask: float, ts: int) -> list[dict]:
        """Feed one best bid/ask. Returns the fills/exits/cancels it caused (usually none)."""
        if not (bid > 0 and ask >= bid):          # also False for NaN: a broken quote never trades
            return []
        self.quotes[symbol] = (bid, ask, ts)
        if symbol not in self._busy:
            return []
        events: list[dict] = []
        for book in list(self.books.values()):
            o = book.orders.get(symbol)
            if o is not None:
                if ts >= o.expires_ts:
                    events.append(self._cancel(book, symbol, "expired: entry_valid bars elapsed", ts))
                elif (ask <= o.limit) if o.side > 0 else (bid >= o.limit):
                    del book.orders[symbol]
                    events.append(self._open(book, o, o.limit, max(ts, o.placed_ts), "limit"))
            p = book.positions.get(symbol)
            if p is not None:
                ev = self._check_exit(book, p, bid, ask, ts)
                if ev is not None:
                    events.append(ev)
        if events:
            self._save()
        return events

    def on_bar_extreme(self, symbol: str, bar_open_ts: int, high: float, low: float, ts: int) -> list[dict]:
        """Safety net for ticks we never saw (reconnect, restart inside a bar): if the exchange's own
        bar shows a print through the stop, the stop was hit. Only for positions opened BEFORE this
        bar (its extremes may predate a later fill), only for stops (never assumes a profit)."""
        if symbol not in self._busy:
            return []
        events = []
        mult = self.cfg.stop_slippage_mult * self.cfg.slip(symbol)
        q = self.quotes.get(symbol)
        for book in list(self.books.values()):
            p = book.positions.get(symbol)
            if p is None or p.entry_ts >= bar_open_ts:
                continue
            if p.side > 0 and low <= p.stop:
                px = min(p.stop, q[0]) if q else p.stop
                p.flags.append("stop_seen_on_kline")
                events.append(self._close(book, p, px * (1 - mult), ts, "stop", "taker", ref=px, intrabar=True))
            elif p.side < 0 and high >= p.stop:
                px = max(p.stop, q[1]) if q else p.stop
                p.flags.append("stop_seen_on_kline")
                events.append(self._close(book, p, px * (1 + mult), ts, "stop", "taker", ref=px, intrabar=True))
        if events:
            self._save()
        return events

    def on_funding(self, symbol: str, rate: float, mark: float, funding_ts: int, estimated: bool = False) -> list[dict]:
        """Funding settlement: every position held at funding_ts pays side * qty * mark * rate."""
        events = []
        for book in self.books.values():
            p = book.positions.get(symbol)
            if p is None or p.entry_ts >= funding_ts or funding_ts <= p.last_funding_ts:
                continue
            amount = -p.side * p.qty * mark * rate
            p.funding += amount
            p.last_funding_ts = funding_ts
            if estimated and "funding_estimated" not in p.flags:
                p.flags.append("funding_estimated")
            book.cash += amount
            book.sum_funding += amount
            events.append({"kind": "funding", "setup": book.id, "symbol": symbol, "ts": funding_ts, "rate": rate,
                           "mark": mark, "amount": amount, "estimated": estimated,
                           "reason": f"funding {rate:+.6%} on {p.qty} {symbol} @ mark {mark:.2f}: {amount:+.4f} USDT"
                                     + (" (ESTIMATED: settlement was not observed live)" if estimated else "")})
        if events:
            self._save()
        return events

    def expire_orders(self, ts: int) -> list[dict]:
        events = [self._cancel(b, s, "expired: entry_valid bars elapsed", ts)
                  for b in self.books.values() for s, o in list(b.orders.items()) if ts >= o.expires_ts]
        if events:
            self._save()
        return events

    def flatten(self, setups, reason: str, ts: int, max_quote_age_ms: int) -> list[dict]:
        """Shield's halt: cancel resting orders and close positions at market for `setups`
        (None = every book). A position whose symbol has no fresh quote is closed at its next tick."""
        events = []
        for book in list(self.books.values()):
            if setups is not None and book.id not in setups:
                continue
            for sym in list(book.orders):
                events.append(self._cancel(book, sym, reason, ts))
            for p in list(book.positions.values()):
                events.extend(self._market_exit(book, p, reason, ts, max_quote_age_ms))
        self._save()
        return events

    def set_funding_outlook(self, symbol: str, rate: float, next_ms: int | None) -> None:
        self.funding_outlook[symbol] = (rate, next_ms)

    def _partial_bar_funding(self, book: Book, p: Position, ref: float, ts: int) -> None:
        """Backtester rule R10: a position stopped out or taken out at its target inside a bar that ends
        with a funding settlement is charged that funding if it is a COST, never credited. Live, the
        settlement has not happened yet when the position closes, so the current estimate from the
        markPrice stream is used and the trade is flagged."""
        out = self.funding_outlook.get(p.symbol)
        if not out or out[1] is None or out[0] is None:
            return
        rate, next_ms = out
        tf_ms = TF_MS[p.tf]
        if not (ts < next_ms <= floor_ms(ts, tf_ms) + tf_ms):
            return
        amount = -p.side * p.qty * ref * rate
        if amount >= 0:
            return
        p.funding += amount
        p.last_funding_ts = next_ms
        p.flags.append("funding_partial_bar_est")
        book.cash += amount
        book.sum_funding += amount

    # ------------------------------------------------------------------ internals
    def _fee(self, notional: float, liquidity: str) -> float:
        return abs(notional) * (self.cfg.maker_fee if liquidity == "maker" else self.cfg.taker_fee)

    def _cancel(self, book: Book, symbol: str, reason: str, ts: int) -> dict:
        o = book.orders.pop(symbol)
        return {"kind": "cancel", "setup": book.id, "symbol": symbol, "ts": ts, "order_id": o.id,
                "limit": o.limit, "side": o.side, "qty": o.qty, "reason": f"resting order cancelled ({reason})"}

    def _open(self, book: Book, src, price: float, ts: int, entry_type: str) -> dict:
        """src is an Intent (immediate fill) or an Order (resting limit filled)."""
        sym = src.symbol
        liquidity = "maker" if entry_type == "limit" else "taker"
        fee = self._fee(src.qty * price, liquidity)
        tf_ms = TF_MS[src.tf]
        time_exit = None
        if src.max_hold is not None and src.max_hold > 0:
            time_exit = floor_ms(max(ts, src.placed_ts), tf_ms) + int(src.max_hold) * tf_ms
        book.trade_seq += 1
        p = Position(id=f"{book.id}-{book.trade_seq:05d}", setup=book.id, symbol=sym, side=src.side, qty=src.qty,
                     entry_price=price, entry_ts=ts, entry_fee=fee, entry_type=entry_type, stop=src.stop,
                     target=src.target, time_exit_ts=time_exit, tf=src.tf, signal_bar=src.signal_bar,
                     risk_usd=src.qty * abs(price - src.stop))
        book.positions[sym] = p
        book.cash -= fee
        book.sum_fees += fee
        return {"kind": "fill", "setup": book.id, "symbol": sym, "ts": ts, "side": p.side, "qty": p.qty,
                "fill": price, "stop": p.stop, "target": p.target, "fee": fee, "liquidity": liquidity,
                "position_id": p.id, "time_exit_ts": time_exit,
                "reason": f"{'long' if p.side > 0 else 'short'} {p.qty} {sym} filled @ {price:.4f} "
                          f"({entry_type}, {liquidity} fee {fee:.4f}); stop {p.stop} attached"}

    def _check_exit(self, book: Book, p: Position, bid: float, ask: float, ts: int) -> dict | None:
        """Tick-driven exits. Order: stop, resting target, then anything at market (flatten, time)."""
        slip = self.cfg.slip(p.symbol)
        mult = self.cfg.stop_slippage_mult
        tf_ms = TF_MS[p.tf]
        # an exit that was already scheduled for this instant (R5): a target reached now is not "intrabar"
        due = bool(p.flatten_reason) or (p.time_exit_ts is not None and ts >= p.time_exit_ts)
        if p.side > 0:
            if bid <= p.stop:
                ref = min(p.stop, bid)
                return self._close(book, p, ref * (1 - mult * slip), ts, "stop", "taker", ref=ref,
                                   intrabar=ts % tf_ms != 0)
            if p.target is not None and bid >= p.target:
                return self._close(book, p, p.target, ts, "target", "maker", ref=p.target, intrabar=not due,
                                   scheduled=due)
            if p.flatten_reason:
                return self._close(book, p, bid * (1 - slip), ts, p.flatten_reason, "taker", ref=bid, scheduled=True)
            if p.time_exit_ts is not None and ts >= p.time_exit_ts:
                return self._close(book, p, bid * (1 - slip), ts, "time", "taker", ref=bid)
        else:
            if ask >= p.stop:
                ref = max(p.stop, ask)
                return self._close(book, p, ref * (1 + mult * slip), ts, "stop", "taker", ref=ref,
                                   intrabar=ts % tf_ms != 0)
            if p.target is not None and ask <= p.target:
                return self._close(book, p, p.target, ts, "target", "maker", ref=p.target, intrabar=not due,
                                   scheduled=due)
            if p.flatten_reason:
                return self._close(book, p, ask * (1 + slip), ts, p.flatten_reason, "taker", ref=ask, scheduled=True)
            if p.time_exit_ts is not None and ts >= p.time_exit_ts:
                return self._close(book, p, ask * (1 + slip), ts, "time", "taker", ref=ask)
        return None

    def _market_exit(self, book: Book, p: Position, reason: str, ts: int, max_quote_age_ms: int | None) -> list[dict]:
        """A scheduled exit (signal, time, halt) at the current quote, or left on the position when no
        usable quote exists. R5: beyond the stop it is booked as a stop (2x slippage); beyond the
        target the resting target fills at its price (maker)."""
        q = self.quotes.get(p.symbol)
        if q is None or (max_quote_age_ms is not None and ts - q[2] > max_quote_age_ms):
            p.flatten_reason = reason
            return []
        bid, ask, _ = q
        slip = self.cfg.slip(p.symbol)
        mult = self.cfg.stop_slippage_mult
        if p.side > 0:
            if bid <= p.stop:
                ref = min(p.stop, bid)
                return [self._close(book, p, ref * (1 - mult * slip), ts, "stop", "taker", ref=ref, scheduled=True)]
            if p.target is not None and bid >= p.target:
                return [self._close(book, p, p.target, ts, "target", "maker", ref=p.target, scheduled=True)]
            return [self._close(book, p, bid * (1 - slip), ts, reason, "taker", ref=bid, scheduled=True)]
        if ask >= p.stop:
            ref = max(p.stop, ask)
            return [self._close(book, p, ref * (1 + mult * slip), ts, "stop", "taker", ref=ref, scheduled=True)]
        if p.target is not None and ask <= p.target:
            return [self._close(book, p, p.target, ts, "target", "maker", ref=p.target, scheduled=True)]
        return [self._close(book, p, ask * (1 + slip), ts, reason, "taker", ref=ask, scheduled=True)]

    def _close(self, book: Book, p: Position, price: float, ts: int, reason: str, liquidity: str,
               ref: float | None = None, intrabar: bool = False, scheduled: bool = False) -> dict:
        """ref = exit price before slippage. intrabar = a stop/target hit inside a bar (not at its open
        instant, not a scheduled exit): rule R10 then applies. scheduled = exit flag / max_hold / halt."""
        del book.positions[p.symbol]
        if intrabar:
            self._partial_bar_funding(book, p, ref if ref is not None else price, ts)
        book.last_exit[p.symbol] = {"ts": ts, "reason": reason, "scheduled": bool(
            scheduled or reason == "time" or (p.time_exit_ts is not None and ts >= p.time_exit_ts))}
        gross = p.side * p.qty * (price - p.entry_price)
        exit_fee = self._fee(p.qty * price, liquidity)
        fees = p.entry_fee + exit_fee
        net = gross - fees + p.funding
        book.cash += gross - exit_fee            # entry fee and funding already went through cash
        book.sum_gross += gross
        book.sum_fees += exit_fee
        book.n_trades += 1
        if net < 0:
            book.n_losses += 1
        else:
            book.n_wins += 1
        t = Trade(id=p.id, setup=book.id, symbol=p.symbol, side=p.side, qty=p.qty, entry_ts=p.entry_ts,
                  entry_price=p.entry_price, entry_type=p.entry_type, exit_ts=ts, exit_price=price,
                  exit_reason=reason, gross=gross, fees=fees, funding=p.funding, net=net,
                  equity_after=book.cash, r_multiple=(net / p.risk_usd) if p.risk_usd > 0 else None,
                  signal_bar=p.signal_bar, flags="|".join(p.flags))
        book.recent_trades.append(asdict(t))
        del book.recent_trades[:-KEEP_RECENT_TRADES]
        return {"kind": "exit", "setup": book.id, "symbol": p.symbol, "ts": ts, "side": p.side, "qty": p.qty,
                "fill": price, "entry": p.entry_price, "exit_reason": reason, "net": net, "gross": gross,
                "fees": fees, "funding": p.funding, "liquidity": liquidity, "trade": t,
                "reason": f"{'long' if p.side > 0 else 'short'} {p.qty} {p.symbol} closed @ {price:.4f} by {reason}: "
                          f"net {net:+.4f} USDT (gross {gross:+.4f}, fees {fees:.4f}, funding {p.funding:+.4f})"}
