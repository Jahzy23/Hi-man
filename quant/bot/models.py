"""Plain records passed between engine, Shield and the exchange. Times are epoch ms (UTC)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Intent:
    """An entry order the engine wants to place. Nothing reaches an exchange without a Shield verdict."""
    id: str                    # "<setup>:<symbol>:<signal bar iso>" - deterministic, so a replay cannot double-enter
    setup: str
    symbol: str
    side: int                  # +1 long, -1 short
    qty: float                 # already floored to the exchange step
    entry: float | None        # limit price on the tick grid; None = marketable (taker) at the next open
    ref_price: float           # the price the size was computed from (limit, or expected market fill)
    stop: float | None
    target: float | None
    max_hold: int | None       # bars
    entry_valid: int           # bars a resting limit stays live
    tf: str
    signal_bar: int            # open time of the bar whose close produced the signal
    placed_ts: int             # open time of the next bar = the instant the order becomes live
    reason: str = ""


@dataclass
class Approval:
    """Shield's verdict. `issuer` is the Shield instance, so a hand-made approval is not accepted."""
    intent_id: str
    approved: bool
    reason: str
    issuer: object = field(default=None, repr=False, compare=False)


@dataclass
class Order:
    """A resting limit entry."""
    id: str
    setup: str
    symbol: str
    side: int
    qty: float
    limit: float
    stop: float
    target: float | None
    max_hold: int | None
    tf: str
    signal_bar: int
    placed_ts: int
    expires_ts: int


@dataclass
class Position:
    id: str
    setup: str
    symbol: str
    side: int
    qty: float
    entry_price: float
    entry_ts: int
    entry_fee: float
    entry_type: str            # "limit" (maker) | "market" (taker)
    stop: float
    target: float | None
    time_exit_ts: int | None
    tf: str
    signal_bar: int
    risk_usd: float            # qty * |entry - stop| at the fill, for R multiples
    funding: float = 0.0       # cumulative funding cash flow (negative = paid)
    last_funding_ts: int = 0   # last settlement applied (a settlement is never applied twice)
    flatten_reason: str | None = None   # set when Shield ordered a flatten but no fresh quote existed yet
    flags: list = field(default_factory=list)


@dataclass
class Trade:
    id: str
    setup: str
    symbol: str
    side: int
    qty: float
    entry_ts: int
    entry_price: float
    entry_type: str
    exit_ts: int
    exit_price: float
    exit_reason: str
    gross: float
    fees: float
    funding: float
    net: float
    equity_after: float
    r_multiple: float | None
    signal_bar: int
    flags: str = ""


def to_dict(obj) -> dict:
    d = asdict(obj)
    d.pop("issuer", None)
    return d
