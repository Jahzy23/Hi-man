"""Sizing, exchange-grid rounding, journal and small helpers."""
from __future__ import annotations

import json

import pytest

from bot import instruments as ins
from bot.journal import Journal, read_decisions, read_trades
from bot.models import Trade
from bot.sizing import position_size
from bot.util import HOUR_MS, atomic_write_json, clean_float, floor_ms, iso, parse_iso, read_json, utc_day

from .conftest import T0


# ---------------------------------------------------------------- sizing (SPEC: 0.005 x equity / |entry - stop|, cap 3x)
def test_size_is_risk_over_stop_distance_floored_to_the_step():
    assert position_size("BTCUSDT", 1000.0, 60000.0, 59400.0, 0.005, 3.0) == 0.008      # 5 / 600 = 0.00833
    assert position_size("BTCUSDT", 1000.0, 60000.0, 60600.0, 0.005, 3.0) == 0.008      # shorts: same distance
    assert position_size("ETHUSDT", 1000.0, 1800.0, 1782.0, 0.005, 3.0) == 0.277        # 5 / 18 = 0.2777
    assert position_size("SOLUSDT", 1000.0, 75.0, 74.25, 0.005, 3.0) == 6.66            # 5 / 0.75 = 6.666
    assert position_size("BTCUSDT", 2000.0, 60000.0, 59400.0, 0.005, 3.0) == 0.016      # scales with equity


def test_size_is_capped_at_three_times_equity_notional():
    q = position_size("BTCUSDT", 1000.0, 60000.0, 59990.0, 0.005, 3.0)                  # 5 / 10 = 0.5 BTC = 30x
    assert q == 0.05 and q * 60000.0 <= 3000.0
    assert position_size("SOLUSDT", 1000.0, 75.0, 74.99, 0.005, 3.0) == 40.0


def test_size_never_rounds_up_and_is_zero_when_nothing_fits():
    assert position_size("BTCUSDT", 1000.0, 60000.0, 54000.0, 0.005, 3.0) == 0.0        # 5 / 6000 < one step
    assert position_size("BTCUSDT", 1000.0, 60000.0, 60000.0, 0.005, 3.0) == 0.0        # no distance
    assert position_size("BTCUSDT", 0.0, 60000.0, 59400.0, 0.005, 3.0) == 0.0
    assert position_size("BTCUSDT", 1000.0, 60000.0, None, 0.005, 3.0) == 0.0
    assert position_size("BTCUSDT", 1000.0, float("nan"), 59400.0, 0.005, 3.0) == 0.0
    for dist in (613.7, 599.99, 1234.5, 77.7):
        q = position_size("BTCUSDT", 1000.0, 60000.0, 60000.0 - dist, 0.005, 3.0)
        assert q * dist <= 5.0 + 1e-9 and ins.on_step("BTCUSDT", q)


# ---------------------------------------------------------------- tick / step grid
def test_assumed_exchange_filters_are_the_ones_flagged_for_verification():
    assert (str(ins.INSTRUMENTS["BTCUSDT"].tick), str(ins.INSTRUMENTS["BTCUSDT"].step)) == ("0.1", "0.001")
    assert (str(ins.INSTRUMENTS["ETHUSDT"].tick), str(ins.INSTRUMENTS["ETHUSDT"].step)) == ("0.01", "0.001")
    assert (str(ins.INSTRUMENTS["SOLUSDT"].tick), str(ins.INSTRUMENTS["SOLUSDT"].step)) == ("0.0100", "0.01")
    assert ins.FILTERS_VERIFIED is False


def test_price_rounding_is_exact_on_the_tick_grid():
    assert ins.round_price("BTCUSDT", 64703.449) == 64703.4
    assert ins.round_price("BTCUSDT", 64703.45, "up") == 64703.5
    assert ins.round_price("BTCUSDT", 64703.49, "down") == 64703.4
    assert ins.round_price("ETHUSDT", 1824.325000001) == 1824.33
    assert ins.round_price("SOLUSDT", 75.149999) == 75.15
    assert ins.round_price("BTCUSDT", 0.1 + 0.2) == 0.3                       # float noise does not leak through
    for px in (59400.0, 61200.0, 0.1 * 594123):
        r = ins.round_price("BTCUSDT", px)
        assert ins.on_tick("BTCUSDT", r) and abs(r - px) <= 0.05 + 1e-9
    assert not ins.on_tick("BTCUSDT", 60000.05) and ins.on_tick("SOLUSDT", 75.15) and not ins.on_tick("SOLUSDT", 75.155)


def test_quantity_rounds_down_only_and_formats_for_the_api():
    assert ins.floor_qty("BTCUSDT", 0.0089999) == 0.008 and ins.floor_qty("BTCUSDT", 0.009) == 0.009
    assert ins.floor_qty("BTCUSDT", 0.1 + 0.2) == 0.3 and ins.floor_qty("SOLUSDT", 6.999999) == 6.99
    assert ins.floor_qty("BTCUSDT", 0.0) == 0.0 and ins.floor_qty("BTCUSDT", -1.0) == 0.0
    assert ins.floor_qty("BTCUSDT", float("nan")) == 0.0
    assert ins.on_step("BTCUSDT", 0.008) and not ins.on_step("BTCUSDT", 0.0085)
    assert ins.fmt_qty("BTCUSDT", 0.30000000000000004) == "0.300" and ins.fmt_qty("SOLUSDT", 6.5) == "6.50"
    assert ins.fmt_price("BTCUSDT", 65000) == "65000.0" and ins.fmt_price("SOLUSDT", 75.1) == "75.1000"
    assert ins.fmt_price("ETHUSDT", 1e-7 + 1800.1) == "1800.10" and "e" not in ins.fmt_qty("BTCUSDT", 1e-9).lower()


# ---------------------------------------------------------------- journal
def trade(i=1, net=-3.2):
    return Trade(id=f"leader-{i:05d}", setup="leader", symbol="BTCUSDT", side=1, qty=0.008, entry_ts=T0,
                 entry_price=60000.0, entry_type="limit", exit_ts=T0 + 5000, exit_price=59400.0, exit_reason="stop",
                 gross=-4.8, fees=0.33, funding=0.0, net=net, equity_after=996.8, r_multiple=None, signal_bar=T0 - HOUR_MS)


def test_decision_log_is_one_json_line_per_decision_with_utc_time_and_reason(tmp_path):
    j = Journal(tmp_path / "logs", clock=lambda: T0 + 1234)
    j.decision("signal", "long signal at the close", setup="leader", symbol="BTCUSDT", entry=float("nan"), stop=59400.0,
               bid=60000.0, ask=60000.1, qty=0.008, bar="2026-11-17T23:00:00.000Z")
    j.decision("heartbeat", "alive", ts=T0 + 300_000, tick_age_s={"BTCUSDT": 0.2})
    lines = (tmp_path / "logs" / "decisions.jsonl").read_text().splitlines()
    assert len(lines) == 2 and j.lines == 2
    a, b = (json.loads(x) for x in lines)
    assert a == {"ts": "2026-11-18T00:00:01.234Z", "kind": "signal", "setup": "leader", "symbol": "BTCUSDT",
                 "prices": {"entry": None, "stop": 59400.0, "bid": 60000.0, "ask": 60000.1}, "qty": 0.008,
                 "bar": "2026-11-17T23:00:00.000Z", "reason": "long signal at the close"}
    assert b["ts"] == "2026-11-18T00:05:00.000Z" and b["setup"] is None and b["reason"] == "alive"
    j.close()
    j.decision("eval", "reopens after close")                                # append-only across handles
    assert len(read_decisions(tmp_path / "logs" / "decisions.jsonl")) == 3


def test_decision_reader_survives_a_torn_last_line_and_filters_by_time(tmp_path):
    j = Journal(tmp_path, clock=lambda: T0)
    j.decision("a", "first", ts=T0)
    j.decision("b", "second", ts=T0 + HOUR_MS)
    with open(tmp_path / "decisions.jsonl", "a") as f:
        f.write('\n{"ts": "2026-11-18T02:00:00.000Z", "kind": "c", "rea')      # killed mid-write
    assert [d["kind"] for d in read_decisions(tmp_path / "decisions.jsonl")] == ["a", "b"]
    assert [d["kind"] for d in read_decisions(tmp_path / "decisions.jsonl", since_ms=T0 + 1)] == ["b"]
    assert read_decisions(tmp_path / "missing.jsonl") == [] and read_trades(tmp_path / "missing.csv") == []


def test_trades_ledger_has_one_header_and_readable_times(tmp_path):
    j = Journal(tmp_path)
    assert j.trade_ids() == set()
    j.trade(trade(1))
    j.trade(trade(2, net=7.5).__dict__)
    rows = read_trades(tmp_path / "trades.csv")
    assert (tmp_path / "trades.csv").read_text().count("entry_time") == 1 and len(rows) == 2
    assert rows[0]["entry_time"] == "2026-11-18T00:00:00.000Z" and rows[0]["exit_reason"] == "stop"
    assert rows[0]["r_multiple"] == "" and float(rows[1]["net"]) == 7.5 and j.trade_ids() == {"leader-00001", "leader-00002"}


# ---------------------------------------------------------------- util
def test_time_helpers():
    assert iso(T0) == "2026-11-18T00:00:00.000Z" and iso(None) is None and parse_iso(iso(T0 + 7)) == T0 + 7
    assert parse_iso("2026-11-18T00:00:00+00:00") == T0 and parse_iso("2026-11-18T00:00:00") == T0
    assert utc_day(T0 - 1) == "2026-11-17" and utc_day(T0) == "2026-11-18"
    assert floor_ms(T0 + 5 * HOUR_MS + 99, 4 * HOUR_MS) == T0 + 4 * HOUR_MS


def test_atomic_json_and_clean_float(tmp_path):
    import numpy as np
    p = tmp_path / "deep" / "x.json"
    atomic_write_json(p, {"a": np.float64(1.5), "b": np.int64(2), "c": float("nan")})
    assert "NaN" not in p.read_text() and json.loads(p.read_text()) == {"a": 1.5, "b": 2, "c": None}   # strict JSON
    assert read_json(tmp_path / "nope.json", default={"d": 1}) == {"d": 1}
    assert list(p.parent.iterdir()) == [p]                                    # no temp file left behind
    with pytest.raises(TypeError):
        atomic_write_json(p, {"x": object()})
    j = Journal(tmp_path / "j")
    j.decision("heartbeat", "nested not-a-number must not break strict JSON readers", quotes={"BTCUSDT": [float("nan"), 1.0]},
               big=float("inf"), n=np.int64(3))
    line = (tmp_path / "j" / "decisions.jsonl").read_text()
    assert "NaN" not in line and "Infinity" not in line
    assert json.loads(line)["quotes"] == {"BTCUSDT": [None, 1.0]} and json.loads(line)["n"] == 3
    assert clean_float("1.5") == 1.5 and clean_float(None) is None and clean_float(float("nan")) is None
    assert clean_float(float("inf")) is None and clean_float("abc") is None
