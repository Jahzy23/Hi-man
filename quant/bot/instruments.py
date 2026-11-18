"""Exchange filters (tick size, step size, min notional) and exact rounding.

ASSUMPTIONS - VERIFY BEFORE GO-LIVE. /fapi/v1/exchangeInfo is geo-blocked from this machine, so
these values are hard-coded from memory of Binance USDT-M filters and have NOT been checked against
the exchange. A wrong tick/step makes the live adapter's orders fail with -1111/-4014 style errors;
a wrong min notional makes small orders fail with -4164.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_EVEN, Decimal


@dataclass(frozen=True)
class Instrument:
    symbol: str
    tick: Decimal          # PRICE_FILTER.tickSize
    step: Decimal          # LOT_SIZE.stepSize
    min_qty: Decimal       # LOT_SIZE.minQty
    min_notional: Decimal  # MIN_NOTIONAL.notional (assumed)


INSTRUMENTS = {
    "BTCUSDT": Instrument("BTCUSDT", Decimal("0.1"), Decimal("0.001"), Decimal("0.001"), Decimal("100")),
    "ETHUSDT": Instrument("ETHUSDT", Decimal("0.01"), Decimal("0.001"), Decimal("0.001"), Decimal("20")),
    "SOLUSDT": Instrument("SOLUSDT", Decimal("0.0100"), Decimal("0.01"), Decimal("0.01"), Decimal("5")),
}
FILTERS_VERIFIED = False   # flip only after comparing with exchangeInfo from a machine that can reach it


def _d(x) -> Decimal:
    # repr() round-trips a float exactly as the shortest decimal string, so 64703.4 -> Decimal('64703.4')
    return x if isinstance(x, Decimal) else Decimal(repr(float(x)))


def round_price(symbol: str, price: float, mode: str = "nearest") -> float:
    """Snap a price to the tick grid. mode: 'nearest' | 'down' | 'up'."""
    ins = INSTRUMENTS[symbol]
    rounding = {"nearest": ROUND_HALF_EVEN, "down": ROUND_FLOOR, "up": ROUND_CEILING}[mode]
    n = (_d(price) / ins.tick).to_integral_value(rounding=rounding)
    return float(n * ins.tick)


def floor_qty(symbol: str, qty: float) -> float:
    """Round a quantity DOWN to the step grid (never up: rounding up would exceed the risk budget).
    Same arithmetic as lab.engine (rule R12): floor(q / step + 1e-9) steps, so a quantity that is a
    whole number of steps up to float noise is not pushed one step down."""
    ins = INSTRUMENTS[symbol]
    if not qty > 0 or not math.isfinite(qty):
        return 0.0
    n = math.floor(qty / float(ins.step) + 1e-9)
    return float(Decimal(n) * ins.step)


def on_tick(symbol: str, price: float) -> bool:
    ins = INSTRUMENTS[symbol]
    return (_d(price) % ins.tick) == 0


def on_step(symbol: str, qty: float) -> bool:
    ins = INSTRUMENTS[symbol]
    return (_d(qty) % ins.step) == 0


def fmt_price(symbol: str, price: float) -> str:
    """String for the REST API: exactly on the tick grid, no exponent, no float noise."""
    ins = INSTRUMENTS[symbol]
    snapped = (_d(price) / ins.tick).to_integral_value(rounding=ROUND_HALF_EVEN) * ins.tick
    return format(snapped.quantize(ins.tick), "f")


def fmt_qty(symbol: str, qty: float) -> str:
    ins = INSTRUMENTS[symbol]
    snapped = (_d(qty) / ins.step).to_integral_value(rounding=ROUND_FLOOR) * ins.step
    return format(snapped.quantize(ins.step), "f")
