"""Feed plumbing without a network: URL building, parsing, conflation, reconnect loop."""
from __future__ import annotations

import json
import queue
import time

import pytest

from bot import feed
from bot.feed import BinanceFeed, Conflator, StreamConn, parse_message, proxy_kwargs, stream_urls

BOOK = ('{"stream":"btcusdt@bookTicker","data":{"e":"bookTicker","u":1,"s":"BTCUSDT","ps":"BTCUSDT","b":"64525.60",'
        '"B":"3.568","a":"64525.70","A":"8.466","T":1794989605375,"E":1794989605375,"st":1}}')
KLINE = ('{"stream":"btcusdt@kline_1h","data":{"e":"kline","E":1794989705764,"s":"BTCUSDT","k":{"t":1794988800000,'
         '"T":1794992399999,"s":"BTCUSDT","i":"1h","o":"64851.00","c":"64588.80","h":"64851.10","l":"64500.00",'
         '"v":"1855.354","n":39201,"x":false,"q":"119966752.36210","V":"765.774","Q":"49496531.72740","B":"0"}}}')
MARK = ('{"stream":"btcusdt@markPrice@1s","data":{"e":"markPriceUpdate","E":1794989706005,"s":"BTCUSDT",'
        '"p":"64589.66981884","ap":"64589.66981884","P":"64767.47082162","i":"64646.89217391","r":"0.00001700",'
        '"T":1795017600000,"st":1}}')


def test_stream_urls_use_the_two_path_families():
    u = stream_urls(["BTCUSDT", "SOLUSDT"])
    assert u["public"] == "wss://fstream.binance.com/public/stream?streams=btcusdt@bookTicker/solusdt@bookTicker"
    assert u["market"] == ("wss://fstream.binance.com/market/stream?streams="
                           "btcusdt@kline_1h/btcusdt@markPrice@1s/solusdt@kline_1h/solusdt@markPrice@1s")


def test_proxy_kwargs_from_the_environment(tmp_path):
    ca = tmp_path / "ca.crt"
    ca.write_text("x")
    kw = proxy_kwargs({"HTTPS_PROXY": "http://user:pw@proxy.local:8080", "BOT_CA_BUNDLE": str(ca)})
    assert kw == {"http_proxy_host": "proxy.local", "http_proxy_port": 8080, "proxy_type": "http",
                  "http_proxy_auth": ("user", "pw"), "sslopt": {"ca_certs": str(ca)}}
    kw = proxy_kwargs({"https_proxy": "http://proxy.local:3128", "BOT_CA_BUNDLE": str(ca)})
    assert "http_proxy_auth" not in kw and kw["http_proxy_port"] == 3128
    assert "http_proxy_host" not in proxy_kwargs({})


def test_parse_messages_as_captured_from_the_live_stream():
    assert parse_message(BOOK, 7) == ("book", "BTCUSDT", 64525.6, 64525.7, 1794989605375, 7)
    k = parse_message(KLINE, 8)
    assert k[:2] == ("kline", "BTCUSDT") and k[2]["x"] is False and k[2]["t"] == 1794988800000 and k[3:] == (1794989705764, 8)
    assert parse_message(MARK, 9) == ("mark", "BTCUSDT", 64589.66981884, 0.000017, 1795017600000, 1794989706005, 9)
    assert parse_message('{"result":null,"id":1}', 1) is None
    assert parse_message('{"stream":"x","data":{"e":"aggTrade"}}', 1) is None
    with pytest.raises(KeyError):
        parse_message('{"stream":"x","data":{"e":"bookTicker","s":"BTCUSDT"}}', 1)


def test_conflator_keeps_price_changes_and_a_keepalive():
    c = Conflator(keepalive_ms=250)
    t = 1_000_000
    assert c.keep(("book", "BTCUSDT", 100.0, 100.1, t, t))
    assert not c.keep(("book", "BTCUSDT", 100.0, 100.1, t + 10, t + 10))        # size-only update
    assert c.keep(("book", "ETHUSDT", 100.0, 100.1, t + 11, t + 11))            # other symbol
    assert c.keep(("book", "BTCUSDT", 100.0, 100.2, t + 20, t + 20))            # ask moved
    assert c.keep(("book", "BTCUSDT", 99.9, 100.2, t + 30, t + 30))             # bid moved
    assert not c.keep(("book", "BTCUSDT", 99.9, 100.2, t + 279, t + 279))
    assert c.keep(("book", "BTCUSDT", 99.9, 100.2, t + 280, t + 280))           # 250 ms since the last one kept


class FakeWS:
    def __init__(self, script):
        self.script, self.closed = list(script), False

    def recv(self):
        if not self.script:
            time.sleep(0.01)
            raise TimeoutError("no data")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True


def drain(q, n, timeout=3.0):
    out, end = [], time.time() + timeout
    while len(out) < n and time.time() < end:
        try:
            out.append(q.get(timeout=0.05))
        except queue.Empty:
            pass
    return out


def test_stream_conn_reconnects_after_an_error_and_skips_garbage():
    q = queue.Queue()
    sockets = [FakeWS([BOOK, "", "not json", '{"stream":"x","data":{"e":"bookTicker"}}', BOOK,
                       ConnectionResetError("reset by peer")]),
               FakeWS([MARK, KLINE])]
    made = []

    def connect():
        ws = sockets[len(made)] if len(made) < len(sockets) else FakeWS([])
        made.append(ws)
        return ws

    c = StreamConn("public", "wss://x", q, connect=connect, conflate=True)
    c.start()
    ev = drain(q, 6)
    c.stop()
    c.join(timeout=3)
    assert not c.is_alive()
    kinds = [e[0] if e[0] != "conn" else f"conn-{e[2]}" for e in ev]
    assert kinds[:6] == ["conn-up", "book", "conn-down", "conn-up", "mark", "kline"]   # 2nd identical book conflated
    assert "ConnectionResetError" in ev[2][3] and made[0].closed
    assert c.connects >= 2 and c.errors >= 1 and c.bad_messages == 2 and c.messages == 4


def test_connection_is_recycled_mid_hour_when_old():
    c = StreamConn("m", "wss://x", queue.Queue(), connect=lambda: None, max_age_s=100, clock=lambda: 3600 * 5 + 1500)
    assert c._should_recycle(connected_at=3600 * 5 + 1500 - 101)
    assert not c._should_recycle(connected_at=3600 * 5 + 1500 - 50)             # too young
    c.clock = lambda: 3600 * 5 + 3590                                           # 10 s before the hourly close
    assert not c._should_recycle(connected_at=0)
    c.clock = lambda: 3600 * 5 + 30
    assert not c._should_recycle(connected_at=0)


def test_binance_feed_has_a_redundant_kline_socket_and_reports_stats():
    urls = []

    def connect():
        urls.append(1)
        return FakeWS([BOOK])

    f = BinanceFeed(["BTCUSDT"], connect=connect)
    assert [c.conn_name for c in f.conns] == ["public", "market", "market-b"]
    assert f.conns[1].url == f.conns[2].url and "kline_1h" in f.conns[1].url and "bookTicker" in f.conns[0].url
    assert f.conns[1].max_age_s != f.conns[2].max_age_s and f.conns[0].conflator is not None and f.conns[1].conflator is None
    assert f.get(timeout=0.01) is None
    f.start()
    got = drain(f.queue, 6)
    f.stop()
    assert sum(1 for e in got if e[0] == "conn" and e[2] == "up") == 3 and sum(1 for e in got if e[0] == "book") == 3
    st = f.stats()
    assert set(st) == {"public", "market", "market-b", "queue"} and st["public"]["connects"] >= 1
    assert all(not c.is_alive() for c in f.conns)


def test_default_connect_passes_proxy_settings_to_websocket_client(monkeypatch):
    import websocket
    seen = {}

    class WS:
        def settimeout(self, t):
            seen["timeout"] = t

    def fake_create(url, timeout=None, **kw):
        seen.update(url=url, kw=kw)
        return WS()

    monkeypatch.setattr(websocket, "create_connection", fake_create)
    monkeypatch.setenv("HTTPS_PROXY", "http://u:p@h:1")
    c = StreamConn("public", "wss://example/stream", queue.Queue())
    c._default_connect()
    assert seen["url"] == "wss://example/stream" and seen["kw"]["http_proxy_host"] == "h" and seen["timeout"] == 10.0
    assert json.dumps(feed.HOST)
