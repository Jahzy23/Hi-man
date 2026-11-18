"""Binance USDT-M futures adapter (signed REST). NOT USED BY THIS BUILD and NEVER TESTED AGAINST BINANCE.

fapi.binance.com answers HTTP 451 from this machine, so only request building and signing are
unit-tested (bot/tests/test_binance_live.py, including the signature example from Binance's API
documentation). Nothing here has ever been sent to the exchange. Before go-live someone must, from a
machine that can reach the API (ideally against the futures testnet first):
  * verify tick/step/min-notional in instruments.py against /fapi/v1/exchangeInfo;
  * verify the stop endpoint: Binance announced that conditional orders (STOP_MARKET ...) moved to the
    Algo Order endpoints (POST /fapi/v1/algoOrder, `triggerPrice`) - this is from the author's memory
    and is unverified; both request shapes are built here, `stop_endpoint` selects one;
  * verify that a closePosition stop may rest before the entry has filled, or else place it on the
    fill event - this adapter has NO fill tracking (no user-data stream, no partial-fill handling,
    no reconciliation of exchange state with bot state). That lifecycle is not written yet.
  * confirm the account is in one-way mode (positionSide BOTH) and the leverage/margin mode per symbol.

Safety: the constructor refuses unless config mode == "live" AND state/GO_LIVE_CONFIRMED exists AND
both keys are in the environment. main.py additionally refuses any mode other than "paper".
Keys are read from BINANCE_API_KEY / BINANCE_API_SECRET only, never stored on disk, never logged.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode

from .config import Config
from .exchange import Exchange, require_approval
from .instruments import fmt_price, fmt_qty
from .models import Approval, Intent, Order, Position
from .util import now_ms

BASE_URL = "https://fapi.binance.com"
CLIENT_ID_RE = re.compile(r"^[\.A-Z\:/a-z0-9_-]{1,36}$")


class LiveTradingRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    params: tuple          # ordered (key, value) pairs, unsigned

    def as_dict(self) -> dict:
        return dict(self.params)


def sign(secret: str, query: str) -> str:
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def client_id(setup: str, symbol: str, signal_bar_ms: int, suffix: str) -> str:
    """Deterministic per signal, so a retried order is rejected as a duplicate instead of doubling up."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "", setup)[:12]
    cid = f"{safe}-{symbol[:-4]}-{signal_bar_ms // 1000}-{suffix}"
    if not CLIENT_ID_RE.match(cid):
        raise ValueError(f"client order id {cid!r} is not valid for Binance")
    return cid


class BinanceFuturesLive(Exchange):
    name = "binance-futures-live"

    def __init__(self, cfg: Config, session=None, clock=now_ms, env=None, stop_endpoint: str = "algo",
                 recv_window: int = 5000):
        if cfg.mode != "live":
            raise LiveTradingRefused(f"config mode is {cfg.mode!r}; the live adapter needs mode = \"live\"")
        confirm = Path(cfg.state_dir) / "GO_LIVE_CONFIRMED"
        if not confirm.exists():
            raise LiveTradingRefused(f"{confirm} does not exist; the owner has not said go live")
        env = os.environ if env is None else env
        key, secret = env.get("BINANCE_API_KEY"), env.get("BINANCE_API_SECRET")
        if not key or not secret:
            raise LiveTradingRefused("BINANCE_API_KEY / BINANCE_API_SECRET are not set in the environment")
        if stop_endpoint not in ("algo", "legacy"):
            raise ValueError("stop_endpoint must be 'algo' or 'legacy'")
        self.cfg = cfg
        self._key = key
        self._secret = secret
        self._session = session
        self.clock = clock
        self.stop_endpoint = stop_endpoint
        self.recv_window = recv_window

    def __repr__(self) -> str:          # never show credentials
        return f"<BinanceFuturesLive stop_endpoint={self.stop_endpoint} key=***>"

    # ------------------------------------------------------------------ request building (unit-tested)
    def build_entry(self, intent: Intent) -> Request:
        side = "BUY" if intent.side > 0 else "SELL"
        cid = client_id(intent.setup, intent.symbol, intent.signal_bar, "e")
        if intent.entry is None:
            p = (("symbol", intent.symbol), ("side", side), ("type", "MARKET"),
                 ("quantity", fmt_qty(intent.symbol, intent.qty)), ("newClientOrderId", cid))
        else:
            p = (("symbol", intent.symbol), ("side", side), ("type", "LIMIT"), ("timeInForce", "GTC"),
                 ("quantity", fmt_qty(intent.symbol, intent.qty)), ("price", fmt_price(intent.symbol, intent.entry)),
                 ("newClientOrderId", cid))
        return Request("POST", "/fapi/v1/order", p)

    def build_stop(self, intent: Intent) -> Request:
        """Protective stop for the position the entry will open: opposite side, closes the whole position."""
        side = "SELL" if intent.side > 0 else "BUY"
        cid = client_id(intent.setup, intent.symbol, intent.signal_bar, "s")
        px = fmt_price(intent.symbol, intent.stop)
        if self.stop_endpoint == "legacy":
            p = (("symbol", intent.symbol), ("side", side), ("type", "STOP_MARKET"), ("stopPrice", px),
                 ("closePosition", "true"), ("workingType", "MARK_PRICE"), ("newClientOrderId", cid))
            return Request("POST", "/fapi/v1/order", p)
        p = (("algoType", "CONDITIONAL"), ("symbol", intent.symbol), ("side", side), ("type", "STOP_MARKET"),
             ("triggerPrice", px), ("closePosition", "true"), ("workingType", "MARK_PRICE"), ("clientAlgoId", cid))
        return Request("POST", "/fapi/v1/algoOrder", p)

    def build_reduce_only_stop(self, intent: Intent) -> Request:
        """Alternative stop shape: reduce-only with an explicit quantity instead of closePosition."""
        side = "SELL" if intent.side > 0 else "BUY"
        cid = client_id(intent.setup, intent.symbol, intent.signal_bar, "s")
        px, qty = fmt_price(intent.symbol, intent.stop), fmt_qty(intent.symbol, intent.qty)
        if self.stop_endpoint == "legacy":
            p = (("symbol", intent.symbol), ("side", side), ("type", "STOP_MARKET"), ("stopPrice", px),
                 ("quantity", qty), ("reduceOnly", "true"), ("workingType", "MARK_PRICE"), ("newClientOrderId", cid))
            return Request("POST", "/fapi/v1/order", p)
        p = (("algoType", "CONDITIONAL"), ("symbol", intent.symbol), ("side", side), ("type", "STOP_MARKET"),
             ("triggerPrice", px), ("quantity", qty), ("reduceOnly", "true"), ("workingType", "MARK_PRICE"),
             ("clientAlgoId", cid))
        return Request("POST", "/fapi/v1/algoOrder", p)

    def build_cancel(self, symbol: str, orig_client_id: str) -> Request:
        return Request("DELETE", "/fapi/v1/order", (("symbol", symbol), ("origClientOrderId", orig_client_id)))

    def build_market_close(self, symbol: str, side: int, qty: float) -> Request:
        return Request("POST", "/fapi/v1/order", (("symbol", symbol), ("side", "SELL" if side > 0 else "BUY"),
                                                  ("type", "MARKET"), ("quantity", fmt_qty(symbol, qty)),
                                                  ("reduceOnly", "true")))

    def signed_query(self, req: Request) -> str:
        """urlencoded params + recvWindow + timestamp + signature (HMAC-SHA256 of everything before it)."""
        q = urlencode(list(req.params) + [("recvWindow", self.recv_window), ("timestamp", self.clock())])
        return f"{q}&signature={sign(self._secret, q)}"

    def headers(self) -> dict:
        return {"X-MBX-APIKEY": self._key}

    # ------------------------------------------------------------------ transport (never exercised from here)
    def _send(self, req: Request) -> dict:
        if self._session is None:
            import requests
            self._session = requests.Session()
        url = f"{BASE_URL}{req.path}"
        body = self.signed_query(req)
        if req.method == "POST":
            r = self._session.request("POST", url, headers=self.headers(), data=body, timeout=10)
        else:
            r = self._session.request(req.method, f"{url}?{body}", headers=self.headers(), timeout=10)
        if r.status_code != 200:
            # the response body carries Binance's error code; the request (signature, key) is not echoed
            raise RuntimeError(f"Binance {req.method} {req.path} failed: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    # ------------------------------------------------------------------ Exchange API
    def submit_entry(self, intent: Intent, approval: Approval, ts: int) -> list[dict]:
        require_approval(intent.id, approval)
        if intent.stop is None:
            raise RuntimeError(f"{intent.id}: refusing an order without a stop")
        entry = self._send(self.build_entry(intent))
        try:
            stop = self._send(self.build_stop(intent))
        except Exception:
            # an entry without its stop must not be left resting
            self._send(self.build_cancel(intent.symbol, client_id(intent.setup, intent.symbol, intent.signal_bar, "e")))
            raise
        return [{"kind": "order", "setup": intent.setup, "symbol": intent.symbol, "ts": ts,
                 "reason": f"live entry {entry.get('orderId')} and stop {stop.get('orderId', stop.get('algoId'))} placed",
                 "order_id": intent.id}]

    def cancel_entry(self, setup: str, symbol: str, reason: str, ts: int) -> list[dict]:
        raise NotImplementedError("live order lifecycle is not implemented (see module docstring)")

    def close_position(self, setup: str, symbol: str, reason: str, approval: Approval, ts: int) -> list[dict]:
        require_approval(f"exit:{setup}:{symbol}", approval)
        raise NotImplementedError("live order lifecycle is not implemented (see module docstring)")

    def open_positions(self) -> list[Position]:
        raise NotImplementedError("live order lifecycle is not implemented (see module docstring)")

    def open_orders(self) -> list[Order]:
        raise NotImplementedError("live order lifecycle is not implemented (see module docstring)")

    def equity(self, setup: str) -> float:
        raise NotImplementedError("live order lifecycle is not implemented (see module docstring)")
