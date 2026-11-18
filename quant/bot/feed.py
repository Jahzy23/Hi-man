"""Live market data over websocket (read-only, no REST, no orders).

Binance USDT-M futures serve market data on two path families of the same host; the legacy
combined path `/stream?streams=` now only delivers the book streams (observed 2026-11-18:
a kline/markPrice subscription there stays silent), so the feed keeps two connections:

    wss://fstream.binance.com/public/stream?streams=<sym>@bookTicker/...          best bid/ask
    wss://fstream.binance.com/market/stream?streams=<sym>@kline_1h/<sym>@markPrice@1s/...

Each connection has its own reader thread that parses messages and puts small tuples on one queue;
the engine consumes that queue in a single thread. Reconnects are handled here (expected, not an
"unhandled exception"); a feed that stays silent is caught by Shield's stale-feed rule.

Events put on the queue:
    ("book",  symbol, bid, ask, event_ms, recv_ms)
    ("kline", symbol, k_dict, event_ms, recv_ms)          k_dict = Binance kline payload "k"
    ("mark",  symbol, mark_price, funding_rate, next_funding_ms, event_ms, recv_ms)
    ("conn",  conn_name, "up" | "down", detail, recv_ms)
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from urllib.parse import urlparse

HOST = "wss://fstream.binance.com"
DEFAULT_CA = "/root/.ccr/ca-bundle.crt"


def stream_urls(symbols) -> dict:
    low = [s.lower() for s in symbols]
    public = "/".join(f"{s}@bookTicker" for s in low)
    market = "/".join(f"{s}@kline_1h/{s}@markPrice@1s" for s in low)
    return {"public": f"{HOST}/public/stream?streams={public}",
            "market": f"{HOST}/market/stream?streams={market}"}


def proxy_kwargs(env=None) -> dict:
    """websocket-client connection kwargs for the HTTPS proxy and CA bundle of this machine."""
    env = os.environ if env is None else env
    kw: dict = {}
    proxy = env.get("HTTPS_PROXY") or env.get("https_proxy")
    if proxy:
        u = urlparse(proxy)
        kw.update(http_proxy_host=u.hostname, http_proxy_port=u.port, proxy_type="http")
        if u.username:
            kw["http_proxy_auth"] = (u.username, u.password)
    ca = env.get("BOT_CA_BUNDLE") or (DEFAULT_CA if os.path.exists(DEFAULT_CA) else None)
    if ca:
        kw["sslopt"] = {"ca_certs": ca}
    return kw


def parse_message(raw: str, recv_ms: int):
    """Raw combined-stream text -> event tuple, or None for anything we do not use."""
    m = json.loads(raw)
    d = m.get("data")
    if not isinstance(d, dict):
        return None
    e = d.get("e")
    if e == "bookTicker":
        return ("book", d["s"], float(d["b"]), float(d["a"]), int(d["E"]), recv_ms)
    if e == "kline":
        return ("kline", d["s"], d["k"], int(d["E"]), recv_ms)
    if e == "markPriceUpdate":
        return ("mark", d["s"], float(d["p"]), float(d["r"]), int(d["T"]), int(d["E"]), recv_ms)
    return None


class Conflator:
    """Drop book ticks that change neither price (size-only updates are ~80% of the stream).

    A tick is forwarded when bid or ask moved, or when `keepalive_ms` passed since the last one
    forwarded for that symbol (so the engine's staleness clock keeps seeing fresh event times).
    """

    def __init__(self, keepalive_ms: int = 250):
        self.keepalive_ms = keepalive_ms
        self._last: dict = {}

    def keep(self, ev) -> bool:
        _, sym, bid, ask, e_ms, _ = ev
        last = self._last.get(sym)
        if last is None or bid != last[0] or ask != last[1] or e_ms - last[2] >= self.keepalive_ms:
            self._last[sym] = (bid, ask, e_ms)
            return True
        return False


class StreamConn(threading.Thread):
    """One websocket connection with its own reader thread and reconnect loop."""

    def __init__(self, name: str, url: str, out: "queue.Queue", connect=None, conflate: bool = False,
                 recv_timeout_s: float = 10.0, max_age_s: float = 12 * 3600.0, clock=time.time):
        super().__init__(name=f"feed-{name}", daemon=True)
        self.conn_name = name
        self.url = url
        self.out = out
        self.conflator = Conflator() if conflate else None
        self.recv_timeout_s = recv_timeout_s
        self.max_age_s = max_age_s
        self.clock = clock
        self._connect = connect or self._default_connect
        self._stop_evt = threading.Event()
        self._ws = None
        self.connected = False
        self.connects = 0
        self.errors = 0
        self.last_error = ""
        self.messages = 0
        self.bad_messages = 0

    def _default_connect(self):
        import websocket  # imported lazily so tests never need the network stack
        ws = websocket.create_connection(self.url, timeout=10, **proxy_kwargs())
        ws.settimeout(self.recv_timeout_s)
        return ws

    def stop(self):
        self._stop_evt.set()
        ws = self._ws
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass

    def _should_recycle(self, connected_at: float) -> bool:
        """Binance drops a socket after 24 h. Recycle ourselves earlier, mid-hour, so the forced
        disconnect can never straddle an hourly bar close (a missed close = a history gap)."""
        now = self.clock()
        return now - connected_at > self.max_age_s and 1200 <= now % 3600 <= 2400

    def run(self):
        backoff = 0.0                   # the first retry is immediate: every second without klines counts
        while not self._stop_evt.is_set():
            try:
                self._ws = self._connect()
                connected_at = self.clock()
                self.connected = True
                self.connects += 1
                self.out.put(("conn", self.conn_name, "up", f"connect #{self.connects}", int(connected_at * 1000)))
                backoff = 0.0
                while not self._stop_evt.is_set():
                    raw = self._ws.recv()
                    recv_ms = int(self.clock() * 1000)
                    if not raw:
                        continue
                    try:
                        ev = parse_message(raw, recv_ms)
                    except (KeyError, ValueError, TypeError):
                        self.bad_messages += 1          # one odd payload must not cost the connection
                        continue
                    if ev is None:
                        continue
                    self.messages += 1
                    if self.conflator is not None and ev[0] == "book" and not self.conflator.keep(ev):
                        continue
                    self.out.put(ev)
                    if self.messages % 512 == 0 and self._should_recycle(connected_at):
                        raise ConnectionError("planned mid-hour reconnect (connection age)")
            except Exception as e:  # network errors are expected here; reconnect with backoff
                if self._stop_evt.is_set():
                    break
                self.errors += 1
                self.last_error = f"{type(e).__name__}: {e}"[:200]
                self.connected = False
                self.out.put(("conn", self.conn_name, "down", self.last_error, int(self.clock() * 1000)))
                try:
                    if self._ws is not None:
                        self._ws.close()
                except Exception:
                    pass
                self._stop_evt.wait(backoff)
                backoff = min(max(1.0, backoff * 2.0), 30.0)
        self.connected = False


class BinanceFeed:
    """Owns the connections and the event queue.

    The kline/markPrice stream is subscribed TWICE on independent sockets. Sockets through this
    machine's proxy get reset now and then (seen after ~20 min on 2026-11-18); a reset that straddles
    the top of the hour would lose the closed bar, and a lost bar is a history gap that only the next
    day's archive file can fill. The engine ignores the duplicates (bars and settlements are keyed
    by time). The second socket recycles at a different age so they never reconnect together.
    """

    def __init__(self, symbols, connect=None):
        self.queue: "queue.Queue" = queue.Queue()
        urls = stream_urls(symbols)
        self.conns = [StreamConn("public", urls["public"], self.queue, connect=connect, conflate=True),
                      StreamConn("market", urls["market"], self.queue, connect=connect),
                      StreamConn("market-b", urls["market"], self.queue, connect=connect, max_age_s=17 * 3600.0)]

    def start(self):
        for c in self.conns:
            c.start()

    def stop(self):
        for c in self.conns:
            c.stop()
        for c in self.conns:
            c.join(timeout=5)

    def get(self, timeout: float = 1.0):
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stats(self) -> dict:
        return {c.conn_name: {"connected": c.connected, "connects": c.connects, "errors": c.errors,
                              "last_error": c.last_error, "messages": c.messages,
                              "bad_messages": c.bad_messages} for c in self.conns} | {
            "queue": self.queue.qsize()}
