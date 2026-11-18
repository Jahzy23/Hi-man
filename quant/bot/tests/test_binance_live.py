"""Live adapter: construction gates, request building, signing. Nothing here touches the network -
the adapter has never been run against Binance (fapi is geo-blocked from this machine)."""
from __future__ import annotations

from urllib.parse import parse_qsl

import pytest

from bot.binance_live import BinanceFuturesLive, LiveTradingRefused, Request, client_id, sign
from bot.exchange import ShieldRejected
from bot.models import Approval

from .conftest import T0, approve, make_cfg, make_intent

ENV = {"BINANCE_API_KEY": "test-key-AAAA", "BINANCE_API_SECRET": "test-secret-BBBB"}
DOC_SECRET = "2b5eb11e18796d12d88f13dc27dbbd02c2cc51ff7059765ed9821957d82bb4d9"
DOC_QUERY = ("symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=9000&timeInForce=GTC"
             "&recvWindow=5000&timestamp=1591702613943")
DOC_SIGNATURE = "3c661234138461fcc7a7d8746c6558c9842d4e10870d2ecbedf7777cad694af9"


class FakeSession:
    def __init__(self, fail_on=None):
        self.sent, self.fail_on = [], fail_on

    def request(self, method, url, headers=None, data=None, timeout=None):
        self.sent.append({"method": method, "url": url, "headers": headers, "data": data})
        body = data or url.split("?", 1)[1]
        kind = dict(parse_qsl(body)).get("type")

        class R:
            status_code = 400 if (self.fail_on and kind == self.fail_on) else 200
            text = '{"code":-4120,"msg":"Order type not supported for this endpoint."}'

            @staticmethod
            def json():
                return {"orderId": 111 + len(self.sent)}
        return R()


def live(tmp_path, env=ENV, confirm=True, mode="live", **kw):
    cfg = make_cfg(tmp_path, mode=mode)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    if confirm:
        (cfg.state_dir / "GO_LIVE_CONFIRMED").write_text("owner said go live\n")
    return BinanceFuturesLive(cfg, env=env, clock=lambda: 1591702613943, **kw)


# ---------------------------------------------------------------- the gates
def test_refuses_in_paper_mode_even_with_the_confirmation_file(tmp_path):
    with pytest.raises(LiveTradingRefused, match="mode"):
        live(tmp_path, mode="paper")


def test_refuses_without_the_go_live_file(tmp_path):
    with pytest.raises(LiveTradingRefused, match="GO_LIVE_CONFIRMED"):
        live(tmp_path, confirm=False)


@pytest.mark.parametrize("env", [{}, {"BINANCE_API_KEY": "k"}, {"BINANCE_API_SECRET": "s"},
                                 {"BINANCE_API_KEY": "", "BINANCE_API_SECRET": "s"}])
def test_refuses_without_both_keys_in_the_environment(tmp_path, env):
    with pytest.raises(LiveTradingRefused, match="BINANCE_API_KEY"):
        live(tmp_path, env=env)


def test_keys_never_appear_in_repr_or_errors(tmp_path):
    ex = live(tmp_path, session=FakeSession(fail_on="LIMIT"))
    assert "test-key" not in repr(ex) and "test-secret" not in repr(ex) and "test-secret" not in str(vars(ex).keys())
    i = make_intent()
    with pytest.raises(RuntimeError) as ei:
        ex.submit_entry(i, approve(i), T0)
    assert "test-key" not in str(ei.value) and "test-secret" not in str(ei.value) and "signature" not in str(ei.value)


# ---------------------------------------------------------------- signing
def test_signature_matches_the_example_in_binances_documentation():
    assert sign(DOC_SECRET, DOC_QUERY) == DOC_SIGNATURE


def test_signed_query_appends_recvwindow_timestamp_and_signature(tmp_path):
    ex = live(tmp_path, env={"BINANCE_API_KEY": "k", "BINANCE_API_SECRET": DOC_SECRET})
    req = Request("POST", "/fapi/v1/order", (("symbol", "BTCUSDT"), ("side", "BUY"), ("type", "LIMIT"),
                                             ("quantity", "1"), ("price", "9000"), ("timeInForce", "GTC")))
    assert ex.signed_query(req) == f"{DOC_QUERY}&signature={DOC_SIGNATURE}"
    assert ex.headers() == {"X-MBX-APIKEY": "k"}


# ---------------------------------------------------------------- request building
def test_limit_entry_request(tmp_path):
    ex = live(tmp_path)
    r = ex.build_entry(make_intent(qty=0.008, entry=60000.0))
    assert (r.method, r.path) == ("POST", "/fapi/v1/order")
    assert r.as_dict() == {"symbol": "BTCUSDT", "side": "BUY", "type": "LIMIT", "timeInForce": "GTC",
                           "quantity": "0.008", "price": "60000.0",
                           "newClientOrderId": f"leader-BTC-{(T0 - 3_600_000) // 1000}-e"}


def test_market_entry_request_and_short_side(tmp_path):
    ex = live(tmp_path)
    r = ex.build_entry(make_intent(symbol="SOLUSDT", side=-1, qty=6.5, entry=None, stop=76.0, target=None))
    assert r.as_dict()["type"] == "MARKET" and r.as_dict()["side"] == "SELL" and r.as_dict()["quantity"] == "6.50"
    assert "price" not in r.as_dict()


def test_stop_request_algo_and_legacy_shapes(tmp_path):
    i = make_intent(stop=59400.0)
    algo = live(tmp_path).build_stop(i)
    assert algo.path == "/fapi/v1/algoOrder"
    assert algo.as_dict() == {"algoType": "CONDITIONAL", "symbol": "BTCUSDT", "side": "SELL", "type": "STOP_MARKET",
                              "triggerPrice": "59400.0", "closePosition": "true", "workingType": "MARK_PRICE",
                              "clientAlgoId": f"leader-BTC-{(T0 - 3_600_000) // 1000}-s"}
    legacy = live(tmp_path, stop_endpoint="legacy").build_stop(i)
    assert legacy.path == "/fapi/v1/order" and legacy.as_dict()["stopPrice"] == "59400.0"
    assert legacy.as_dict()["closePosition"] == "true" and "quantity" not in legacy.as_dict()
    short = live(tmp_path).build_stop(make_intent(side=-1, entry=60200.0, stop=60800.0))
    assert short.as_dict()["side"] == "BUY"
    for ex in (live(tmp_path), live(tmp_path, stop_endpoint="legacy")):
        ro = ex.build_reduce_only_stop(i).as_dict()
        assert ro["reduceOnly"] == "true" and ro["quantity"] == "0.008" and "closePosition" not in ro
    with pytest.raises(ValueError):
        live(tmp_path, stop_endpoint="guess")


def test_prices_and_quantities_are_sent_on_the_exchange_grid(tmp_path):
    ex = live(tmp_path)
    r = ex.build_entry(make_intent(symbol="ETHUSDT", qty=0.30000000000000004, entry=1800.1, stop=1780.0)).as_dict()
    assert r["quantity"] == "0.300" and r["price"] == "1800.10"
    assert ex.build_market_close("BTCUSDT", 1, 0.008).as_dict() == {
        "symbol": "BTCUSDT", "side": "SELL", "type": "MARKET", "quantity": "0.008", "reduceOnly": "true"}
    assert ex.build_cancel("BTCUSDT", "abc").as_dict() == {"symbol": "BTCUSDT", "origClientOrderId": "abc"}


def test_client_ids_are_deterministic_short_and_valid():
    a = client_id("leader", "BTCUSDT", T0, "e")
    assert a == client_id("leader", "BTCUSDT", T0, "e") and a != client_id("leader", "BTCUSDT", T0, "s")
    assert len(client_id("a_very_long_setup_name@with:odd/chars", "SOLUSDT", T0, "e")) <= 36


# ---------------------------------------------------------------- submit (fake transport)
def test_submit_sends_entry_then_stop_signed_with_the_key_header(tmp_path):
    s = FakeSession()
    ex = live(tmp_path, session=s)
    i = make_intent()
    ev = ex.submit_entry(i, approve(i), T0)
    assert ev[0]["kind"] == "order" and len(s.sent) == 2
    entry, stop = (dict(parse_qsl(x["data"])) for x in s.sent)
    assert entry["type"] == "LIMIT" and stop["type"] == "STOP_MARKET"
    for sent, q in zip(s.sent, (entry, stop)):
        assert sent["url"].startswith("https://fapi.binance.com/fapi/v1/") and sent["headers"] == {"X-MBX-APIKEY": "test-key-AAAA"}
        body = sent["data"].rsplit("&signature=", 1)[0]
        assert q["signature"] == sign("test-secret-BBBB", body) and q["timestamp"] == "1591702613943"


def test_entry_is_cancelled_when_its_stop_cannot_be_placed(tmp_path):
    s = FakeSession(fail_on="STOP_MARKET")
    ex = live(tmp_path, session=s)
    i = make_intent()
    with pytest.raises(RuntimeError, match="HTTP 400"):
        ex.submit_entry(i, approve(i), T0)
    assert [x["method"] for x in s.sent] == ["POST", "POST", "DELETE"]
    assert "origClientOrderId=leader-BTC-" in s.sent[2]["url"]


def test_live_adapter_also_needs_shield_and_a_stop(tmp_path):
    s = FakeSession()
    ex = live(tmp_path, session=s)
    i = make_intent()
    with pytest.raises(ShieldRejected):
        ex.submit_entry(i, None, T0)
    with pytest.raises(ShieldRejected):
        ex.submit_entry(i, Approval(i.id, False, "no", issuer=object()), T0)
    j = make_intent(stop=None)
    with pytest.raises(RuntimeError, match="without a stop"):
        ex.submit_entry(j, approve(j), T0)
    with pytest.raises(ShieldRejected):
        ex.close_position("leader", "BTCUSDT", "x", None, T0)
    assert s.sent == []
    for call in (lambda: ex.cancel_entry("leader", "BTCUSDT", "x", T0), ex.open_positions, ex.open_orders,
                 lambda: ex.equity("leader"),
                 lambda: ex.close_position("leader", "BTCUSDT", "x", approve("exit:leader:BTCUSDT"), T0)):
        with pytest.raises(NotImplementedError):
            call()
