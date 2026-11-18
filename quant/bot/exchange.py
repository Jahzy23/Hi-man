"""The seam between the engine and wherever orders go (paper book today, Binance later)."""
from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Approval, Intent, Order, Position


class ShieldRejected(RuntimeError):
    """Raised when something tries to send an order without Shield's approval for exactly that order."""


def require_approval(intent_id: str, approval: Approval | None) -> None:
    if approval is None or not isinstance(approval, Approval):
        raise ShieldRejected(f"{intent_id}: no Shield approval")
    if approval.issuer is None:
        raise ShieldRejected(f"{intent_id}: approval was not issued by Shield")
    if approval.intent_id != intent_id:
        raise ShieldRejected(f"{intent_id}: approval is for a different order ({approval.intent_id})")
    if not approval.approved:
        raise ShieldRejected(f"{intent_id}: blocked by Shield: {approval.reason}")


class Exchange(ABC):
    """Every implementation must refuse an order that does not carry a matching, approved Approval."""

    name = "abstract"

    @abstractmethod
    def submit_entry(self, intent: Intent, approval: Approval, ts: int) -> list[dict]:
        """Place the entry (resting limit, or marketable when intent.entry is None). Returns events."""

    @abstractmethod
    def cancel_entry(self, setup: str, symbol: str, reason: str, ts: int) -> list[dict]:
        """Cancel the resting entry of (setup, symbol), if any."""

    @abstractmethod
    def close_position(self, setup: str, symbol: str, reason: str, approval: Approval, ts: int) -> list[dict]:
        """Exit (setup, symbol) at market."""

    @abstractmethod
    def open_positions(self) -> list[Position]:
        ...

    @abstractmethod
    def open_orders(self) -> list[Order]:
        ...

    @abstractmethod
    def equity(self, setup: str) -> float:
        """Realised equity of the setup's book (start + closed P&L - fees + funding)."""
