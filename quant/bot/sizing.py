"""SPEC sizing: qty = risk_per_trade * equity / |entry - stop|, capped at max_leverage * equity notional."""
from __future__ import annotations

from .instruments import floor_qty


def position_size(symbol: str, equity: float, entry: float, stop: float, risk_per_trade: float,
                  max_leverage: float) -> float:
    """Quantity floored to the exchange step. 0.0 when no valid size exists."""
    if not (equity > 0 and entry > 0 and stop is not None and stop > 0):
        return 0.0
    dist = abs(entry - stop)
    if not dist > 0:
        return 0.0
    qty = risk_per_trade * equity / dist
    cap = max_leverage * equity / entry
    return floor_qty(symbol, min(qty, cap))
