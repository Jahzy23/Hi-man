"""Engine behaviour end to end on synthetic history: bar-close path, gating, restarts, halts."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from bot import archive
from bot.util import HOUR_MS, iso

from .conftest import T0
from .harness import SYMS, Bot

B = "BTCUSDT"
MIN = 60_000
BPS = 0.0001


def reasons(ds):
    return [d["reason"] for d in ds]


def enter_long(bot, bar=T0):
    """Run the 'always' strategy through one bar close with a quote of 60000.0/60000.1 on BTC."""
    bot.ticks(at=bar + HOUR_MS - 200)
    bot.close_bar(bar)
    p = bot.pos()
    assert p is not None, bot.decisions()
    return p


# ---------------------------------------------------------------- bar-close path
def test_closed_bar_is_persisted_and_decided_once_per_setup_and_symbol(tmp_path):
    bot = Bot(tmp_path).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    for s in SYMS:
        rows = (bot.cfg.state_dir / "bars" / f"{s}_1h.csv").read_text().strip().splitlines()
        assert len(rows) == 2 and rows[1].startswith("2026-11-18T00:00:00.000Z,%d," % T0)
    assert len(bot.decisions("bar")) == 3
    sig = bot.decisions("signal")
    assert [d["symbol"] for d in sig] == SYMS and all(d["bar"] == iso(T0) for d in sig)
    assert len(bot.decisions("fill")) == 3
    # the same close again (duplicate event, reconnect): nothing new
    n = len(bot.decisions())
    bot.close_bar(T0, at=T0 + HOUR_MS + 900)
    assert len(bot.decisions()) == n
    assert len(bot.engine.history.frame(B)) == 601


def test_market_entry_is_sized_from_risk_and_filled_at_the_ask_plus_slippage(tmp_path):
    bot = Bot(tmp_path).start()
    p = enter_long(bot)
    fill = 60000.1 * (1 + BPS)
    assert p.entry_price == pytest.approx(fill) and p.entry_type == "market"
    assert p.stop == 59400.0 and p.target == 61200.0             # 0.99 / 1.02 x close 60000, on the tick grid
    assert p.qty == 0.008                                        # 5 USDT / (60006.1 - 59400) = 0.00825 -> step 0.001
    assert p.entry_ts == T0 + HOUR_MS and p.signal_bar == T0
    order_ids = {d["position_id"] for d in bot.decisions("fill", symbol=B)}
    assert order_ids == {"leader-00001"} or len(order_ids) == 1
    s = bot.decisions("signal", symbol=B)[0]
    assert s["prices"]["stop"] == 59400.0 and "long signal" in s["reason"] and s["qty"] == 0.008


def test_stop_exit_lands_in_the_trade_ledger_and_shield(tmp_path):
    bot = Bot(tmp_path).start()
    enter_long(bot)
    bot.tick(B, 59399.0, at=T0 + HOUR_MS + 5 * MIN)
    assert bot.pos() is None
    t = [x for x in bot.trades() if x["symbol"] == B]
    assert len(t) == 1 and t[0]["exit_reason"] == "stop" and float(t[0]["net"]) < 0
    assert float(t[0]["exit_price"]) == pytest.approx(59399.0 * (1 - 2 * BPS))
    assert t[0]["entry_time"] == iso(T0 + HOUR_MS) and t[0]["signal_bar"] == iso(T0)
    ex = bot.decisions("exit", symbol=B)[0]
    assert "closed" in ex["reason"] and "stop" in ex["reason"] and ex["exit_reason"] == "stop"
    assert bot.engine.shield.books["leader"]["consecutive_losses"] == 1


def test_no_second_entry_while_a_position_is_open(tmp_path):
    bot = Bot(tmp_path).start()
    enter_long(bot)
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)
    assert len(bot.decisions("fill", symbol=B)) == 1
    assert any("signal dropped: a position was open at the bar close" in r
               for r in reasons(bot.decisions("signal", symbol=B)))
    assert bot.decisions("block") == []                           # the engine does not even ask Shield


# ---------------------------------------------------------------- backtester rule R7: which signals are acted on
def script(tmp_path, **params):
    return Bot(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "script", "params": params,
                                  "symbols": [B]}]).start()


def test_exit_flag_and_opposite_signal_on_one_row_is_stop_and_reverse(tmp_path):
    bot = script(tmp_path, long_hours=[0], short_hours=[2], exit_hours=[2])
    enter_long(bot)
    bot.ticks(at=T0 + 2 * HOUR_MS - 200, BTCUSDT=60100.0)
    bot.close_bar(T0 + HOUR_MS, BTCUSDT=60100.0)                  # hour 1: nothing
    assert bot.pos().side == 1
    bot.ticks(at=T0 + 3 * HOUR_MS - 200, BTCUSDT=60300.0)
    bot.close_bar(T0 + 2 * HOUR_MS, BTCUSDT=60300.0)              # hour 2: exit flag AND a short signal
    t = bot.trades()
    assert len(t) == 1 and t[0]["exit_reason"] == "signal" and t[0]["exit_time"] == iso(T0 + 3 * HOUR_MS)
    assert float(t[0]["exit_price"]) == pytest.approx(60300.0 * (1 - BPS))
    p = bot.pos()
    assert p.side == -1 and p.entry_ts == T0 + 3 * HOUR_MS and p.signal_bar == T0 + 2 * HOUR_MS   # same open
    assert p.entry_price == pytest.approx(60300.0 * (1 - BPS)) and p.stop == round(60300.0 * 1.01, 1)
    equity_after_exit = float(t[0]["equity_after"])
    assert p.qty == pytest.approx(int(0.005 * equity_after_exit / (p.stop - 60300.0) * 1000 + 1e-9) / 1000)
    assert any("reversal" in r for r in reasons(bot.decisions("signal")))


def test_max_hold_exit_and_new_signal_on_one_row_reenters_at_the_same_open(tmp_path):
    bot = script(tmp_path, long_hours=[0, 2], max_hold=2)
    p0 = enter_long(bot)
    assert p0.time_exit_ts == T0 + 3 * HOUR_MS                    # filled in bar 01:00 (bar 1), held through bar 02:00
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)
    bot.ticks(at=T0 + 3 * HOUR_MS - 200, BTCUSDT=60200.0)
    bot.close_bar(T0 + 2 * HOUR_MS, BTCUSDT=60200.0)              # row 02:00: max_hold is up and a new long signal
    t = bot.trades()
    assert len(t) == 1 and t[0]["exit_reason"] == "time"
    p = bot.pos()
    assert p.id != p0.id and p.side == 1 and p.entry_ts == T0 + 3 * HOUR_MS


def test_time_exit_taken_by_a_tick_just_before_the_decision_still_allows_the_new_signal(tmp_path):
    bot = script(tmp_path, long_hours=[0, 2], max_hold=2)
    enter_long(bot)
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)
    bot.ticks(at=T0 + 3 * HOUR_MS + 150, BTCUSDT=60200.0)         # first quote of the new bar arrives before the kline
    assert bot.pos() is None and bot.trades()[0]["exit_reason"] == "time"
    bot.close_bar(T0 + 2 * HOUR_MS, BTCUSDT=60200.0, at=T0 + 3 * HOUR_MS + 400)
    assert bot.pos() is not None and bot.pos().entry_ts >= T0 + 3 * HOUR_MS


def test_signal_on_the_row_whose_bar_stopped_the_position_out_is_acted_on(tmp_path):
    bot = script(tmp_path, long_hours=[0, 1])
    enter_long(bot)
    bot.tick(B, 59300.0, at=T0 + HOUR_MS + 10 * MIN)              # stopped inside bar 01:00
    assert bot.pos() is None
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)                                   # flat at the close of row 01:00: its signal counts
    assert bot.pos() is not None and bot.pos().signal_bar == T0 + HOUR_MS


def test_stop_hit_after_the_bar_close_but_before_the_decision_drops_that_rows_signal(tmp_path):
    bot = script(tmp_path, long_hours=[0, 1])
    enter_long(bot)
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.tick(B, 59300.0, at=T0 + 2 * HOUR_MS + 150)               # the new bar opens through the stop
    assert bot.pos() is None and bot.trades()[0]["exit_reason"] == "stop"
    bot.ticks(at=T0 + 2 * HOUR_MS + 250, BTCUSDT=59300.0)
    bot.close_bar(T0 + HOUR_MS, at=T0 + 2 * HOUR_MS + 400, BTCUSDT=59300.0)
    assert bot.pos() is None                                      # the position was open at that close: R7 drops it
    assert any("signal dropped" in r for r in reasons(bot.decisions("signal")))


def test_reversal_waits_when_the_exit_has_no_quote(tmp_path):
    bot = script(tmp_path, long_hours=[0], short_hours=[1], exit_hours=[1])
    enter_long(bot)
    bot.close_bar(T0 + HOUR_MS, at=T0 + 2 * HOUR_MS + 300)        # last quote is an hour old
    assert bot.pos().side == 1 and bot.pos().flatten_reason == "signal"
    assert any("still waiting for a quote" in r for r in reasons(bot.decisions("signal")))
    bot.ticks(at=T0 + 2 * HOUR_MS + 2000)
    assert bot.pos() is None and bot.trades()[0]["exit_reason"] == "signal"


def test_exit_flag_while_flat_does_not_cancel_a_resting_entry(tmp_path):
    bot = script(tmp_path, long_hours=[0], exit_hours=[1], limit_pct=0.002, entry_valid=3)
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    assert bot.order() is not None
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)                                   # exit flag, but nothing is open
    assert bot.order() is not None and bot.decisions("cancel") == []


def test_limit_through_the_market_on_arrival_is_sized_and_filled_at_the_market(tmp_path):
    """R1: 'limit = close' with the market already below it would otherwise be a free maker fill."""
    bot = script(tmp_path, long_hours=[0], limit_pct=0.0005)      # limit 59970.0 for a close of 60000
    bot.ticks(at=T0 + HOUR_MS - 200, BTCUSDT=59900.0)             # but the next bar opens at 59900.0 / 59900.1
    bot.close_bar(T0, BTCUSDT=60000.0)
    p = bot.pos()
    assert p.entry_type == "limit_marketable" and p.entry_price == pytest.approx(59900.1 * (1 + BPS))
    assert p.stop == round(59970.0 * 0.99, 1)
    assert p.qty == int(5.0 / (59900.1 - p.stop) * 1000) / 1000   # sized from the price it really pays
    assert bot.decisions("fill")[0]["liquidity"] == "taker"


def test_limit_entry_rests_then_fills_on_trade_through_and_expires_otherwise(tmp_path):
    setups = [{"id": "leader", "role": "leader", "strategy": "always",
               "params": {"limit_pct": 0.002, "entry_valid": 2, "only_hour": 0}, "symbols": [B]}]
    bot = Bot(tmp_path, setups=setups).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)                                             # hour 0 -> signal, limit 59880.0
    o = bot.order()
    assert o.limit == 59880.0 and o.expires_ts == T0 + 3 * HOUR_MS and bot.pos() is None
    assert o.stop == round(59880.0 * 0.99, 1) and o.qty == 0.008
    bot.tick(B, 59880.0, at=T0 + HOUR_MS + MIN)                   # bid touches the limit: nothing
    assert bot.pos() is None
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)                                   # still valid at the next close
    assert bot.order() is not None
    bot.ticks(at=T0 + 3 * HOUR_MS - 200)
    bot.close_bar(T0 + 2 * HOUR_MS)                               # entry_valid = 2 bars are over
    assert bot.order() is None and bot.pos() is None
    assert any("expired" in r for r in reasons(bot.decisions("cancel")))

    bot2 = Bot(tmp_path / "b", setups=setups).start()
    bot2.ticks(at=T0 + HOUR_MS - 200)
    bot2.close_bar(T0)
    bot2.tick(B, 59879.9, 59880.0, at=T0 + HOUR_MS + MIN)         # the offer reaches our bid: traded through
    p = bot2.pos()
    assert p.entry_price == 59880.0 and p.entry_type == "limit"
    assert bot2.decisions("fill")[0]["liquidity"] == "maker"


def test_signal_exit_closes_at_market_at_the_bar_close(tmp_path):
    setups = [{"id": "leader", "role": "leader", "strategy": "always",
               "params": {"only_hour": 0, "exit_hour": 2}, "symbols": [B]}]
    bot = Bot(tmp_path, setups=setups).start()
    enter_long(bot)
    bot.ticks(at=T0 + 2 * HOUR_MS - 200, BTCUSDT=60100.0)
    bot.close_bar(T0 + HOUR_MS)
    assert bot.pos() is not None
    bot.ticks(at=T0 + 3 * HOUR_MS - 200, BTCUSDT=60300.0)
    bot.close_bar(T0 + 2 * HOUR_MS)                               # hour 2: exit flag
    assert bot.pos() is None
    t = bot.trades()[0]
    assert t["exit_reason"] == "signal" and float(t["exit_price"]) == pytest.approx(60300.0 * (1 - BPS))


def test_time_exit_after_max_hold_bars(tmp_path):
    setups = [{"id": "leader", "role": "leader", "strategy": "always",
               "params": {"only_hour": 0, "max_hold": 2}, "symbols": [B]}]
    bot = Bot(tmp_path, setups=setups).start()
    p = enter_long(bot)
    assert p.time_exit_ts == T0 + 3 * HOUR_MS
    bot.tick(B, 60050.0, at=T0 + 3 * HOUR_MS - 1000)
    assert bot.pos() is not None
    bot.tick(B, 60050.0, at=T0 + 3 * HOUR_MS + 200)
    assert bot.pos() is None and bot.trades()[0]["exit_reason"] == "time"


def test_order_without_stop_is_blocked_by_shield_and_logged(tmp_path):
    setups = [{"id": "leader", "role": "leader", "strategy": "always", "params": {"stop_pct": 0.0}, "symbols": [B]}]
    bot = Bot(tmp_path, setups=setups).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    assert bot.pos() is None and bot.order() is None
    blk = bot.decisions("block")
    assert len(blk) == 1 and "no stop" in blk[0]["reason"] and blk[0]["setup"] == "leader"


def test_late_bar_close_does_not_enter(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    bot.ticks(at=T0 + HOUR_MS + 40_000)
    bot.close_bar(T0, at=T0 + HOUR_MS + 40_500)                   # the close event arrives 40 s late
    assert bot.pos() is None
    assert "late decision" in bot.decisions("block")[0]["reason"]


def test_decision_without_a_fresh_quote_is_blocked(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    bot.close_bar(T0)                                             # no tick was ever received
    assert bot.pos() is None and "no fresh quote" in bot.decisions("block")[0]["reason"]


def test_missing_symbol_close_decides_after_the_grace_period(tmp_path):
    bot = Bot(tmp_path).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0, syms=["BTCUSDT", "ETHUSDT"])
    assert bot.decisions("signal") == []                          # waiting for SOL's bar
    bot.ticks(at=T0 + HOUR_MS + 14_000)
    bot.timer()
    assert bot.decisions("signal") == []
    bot.ticks(at=T0 + HOUR_MS + 15_100)
    bot.timer()
    assert sorted(d["symbol"] for d in bot.decisions("signal")) == ["BTCUSDT", "ETHUSDT"]
    w = bot.decisions("warming", symbol="SOLUSDT")
    assert len(w) == 1 and w[0]["reason"].startswith("WARMING: history gap")


# ---------------------------------------------------------------- warm-up / gap gating
def test_history_gap_keeps_the_strategy_warming_until_the_archive_fills_it(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}],
              now=T0 + 3 * HOUR_MS + 10 * MIN).start()            # bars T0 .. T0+2h were never seen
    h = bot.decisions("history", symbol=B)
    assert h and "3 closed 1h bar(s) missing" in h[0]["reason"]
    bot.ticks(at=T0 + 4 * HOUR_MS - 200)
    bot.close_bar(T0 + 3 * HOUR_MS)
    w = bot.decisions("warming")
    assert len(w) == 1 and w[0]["reason"].startswith("WARMING: history gap") and "3 missing 1h bar(s)" in w[0]["reason"]
    assert bot.pos() is None and bot.decisions("signal") == []
    flags = bot.engine.shield.status()["flags"]
    assert flags["warming:leader:BTCUSDT"]["reason"].startswith("WARMING: history gap")       # Shield reports it
    # next day the daily archive file arrives
    rows = pd.DataFrame([{"time_ms": T0 + i * HOUR_MS, "open": 6e4, "high": 60050.0, "low": 59950.0, "close": 6e4,
                          "volume": 100.0, "quote_volume": 6e6, "trades": 1234, "taker_buy_base": 50.0,
                          "taker_buy_quote": 3e6} for i in range(3)])
    archive._merge_csv(bot.cfg.state_dir / "archive" / f"{B}_1h.csv", rows)
    bot.engine.history.reload(B)
    bot.ticks(at=T0 + 5 * HOUR_MS - 200)
    bot.close_bar(T0 + 4 * HOUR_MS)
    assert bot.pos() is not None
    assert "warming:leader:BTCUSDT" not in bot.engine.shield.status()["flags"]


def test_refresh_worker_is_asked_for_the_missing_days_and_results_are_applied(tmp_path):
    class Worker:
        def __init__(self):
            self.requests, self.results = [], []

        def request(self, sym, days=(), months=()):
            self.requests.append((sym, [d.isoformat() for d in days], list(months)))

        def take_results(self):
            out, self.results = self.results, []
            return out

    bot = Bot(tmp_path, now=T0 + 27 * HOUR_MS + 10 * MIN)        # 2026-11-19 03:10: yesterday's bars are missing
    w = Worker()
    from bot.engine import Engine
    from bot.history import History
    hist = History(bot.cfg.symbols, bot.cfg.state_dir, base_loader=lambda s: bot.frames[s],
                   funding_archive=lambda s: bot.funding_archive)
    bot.engine = Engine(bot.cfg, history=hist, strategies_dir=bot.sdir, refresher=w, clock=lambda: bot.now)
    bot.engine.startup(bot.now)
    assert sorted(w.requests) == [(s, ["2026-11-18"], []) for s in SYMS]       # today's file cannot exist yet
    bot.timer(at=bot.now + 10 * MIN)
    assert len(w.requests) == 3                                               # not before archive_refresh_min
    bot.timer(at=bot.now + 21 * MIN)
    assert len(w.requests) == 6
    rows = pd.DataFrame([{"time_ms": T0 + i * HOUR_MS, "open": 6e4, "high": 60050.0, "low": 59950.0, "close": 6e4,
                          "volume": 100.0, "quote_volume": 6e6, "trades": 1234, "taker_buy_base": 50.0,
                          "taker_buy_quote": 3e6} for i in range(24)])
    archive._merge_csv(bot.cfg.state_dir / "archive" / f"{B}_1h.csv", rows)
    w.results = [{"symbol": B, "added": 24, "missing": [], "error": None},
                 {"symbol": "ETHUSDT", "added": 0, "missing": ["2026-11-18"], "error": None},
                 {"symbol": "SOLUSDT", "added": 0, "missing": [], "error": "ChecksumError: mismatch"}]
    bot.timer(at=bot.now + 1000)
    a = {d["symbol"]: d["reason"] for d in bot.decisions("archive")}
    assert "added 24 row(s)" in a[B] and "3 bar(s) still missing" in a[B]      # 00:00-02:00 of the 19th
    assert "not published yet: ['2026-11-18']" in a["ETHUSDT"] and "failed" in a["SOLUSDT"]
    assert len(bot.engine.history.frame(B)) == 624
    assert "archive_error:*:SOLUSDT" in bot.engine.shield.status()["flags"]


def test_funding_months_are_requested_only_when_complete(tmp_path):
    bot = Bot(tmp_path).start()
    import datetime as dt
    assert bot.engine._missing_funding_months(B, dt.date(2026, 11, 18)) == []
    assert bot.engine._missing_funding_months(B, dt.date(2026, 12, 2)) == [(2026, 11)]
    assert bot.engine._missing_funding_months(B, dt.date(2027, 1, 5)) == [(2026, 11), (2026, 12)]


# ---------------------------------------------------------------- columns that are not available live
def test_strategy_that_reads_oi_is_refused_not_traded_on_nan(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "oi", "strategy": "needs_oi", "symbols": [B]},
                                {"id": "ref", "strategy": "always", "symbols": [B]}]).start()
    assert "oi" in bot.engine.strategies["oi"].needs[B] and bot.engine.strategies["ref"].needs[B] == {}
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    r = bot.decisions("refuse")
    assert len(r) == 1 and r[0]["setup"] == "oi" and r[0]["reason"].startswith("REFUSED: needs_oi reads oi")
    assert r[0]["columns"] == ["oi"]
    assert bot.pos(setup="oi") is None and bot.pos(setup="ref") is not None
    assert "refuse:oi:BTCUSDT" in bot.engine.shield.status()["flags"]
    start = [d for d in bot.decisions("start") if d.get("setup") == "oi"][0]
    assert "['oi']" in start["reason"]


def test_strategy_that_reads_funding_trades_only_once_funding_is_known_for_its_window(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "f", "strategy": "needs_funding", "symbols": [B]}]).start()
    assert set(bot.engine.strategies["f"].needs[B]) == {"funding_rate"}
    nxt = T0 + 8 * HOUR_MS
    # five bars (WARMUP_BARS = 5) with the mark stream watched, but no settlement seen yet: rate unknown
    for i in range(5):
        bar = T0 + i * HOUR_MS
        bot.mark(B, 0.0002, nxt, at=bar + HOUR_MS - 1500)
        bot.ticks(at=bar + HOUR_MS - 200)
        bot.close_bar(bar)
    assert len(bot.decisions("refuse")) == 5 and bot.pos(setup="f") is None
    assert "funding_rate" in bot.decisions("refuse")[-1]["reason"]
    # the settlement at T0+8h is observed; five bars later the whole window is real data
    for i in range(5, 13):
        bar = T0 + i * HOUR_MS
        bot.mark(B, 0.0002, nxt if bar + HOUR_MS <= nxt else nxt + 8 * HOUR_MS, at=bar + HOUR_MS - 1500)
        bot.ticks(at=bar + HOUR_MS - 200)
        bot.close_bar(bar)
        if bot.pos(setup="f"):
            break
    f = bot.engine.history.frame(B)
    assert f["funding_paid"].iloc[-5:].tolist() == [0.0002, 0.0, 0.0, 0.0, 0.0]
    assert f["funding_rate"].iloc[-5:].tolist() == [0.0002] * 5
    p = bot.pos(setup="f")
    assert p is not None and p.side == -1 and p.signal_bar == T0 + 11 * HOUR_MS


def test_declared_required_columns_are_honoured(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "d", "strategy": "declares_oi", "symbols": [B]}],
              engine={"default_warmup_bars": 40}).start()
    assert bot.engine.strategies["d"].needs[B] == {"oi_value": "declared in REQUIRED_COLUMNS"}
    assert bot.engine.strategies["d"].warmup == 40                # module has no WARMUP_BARS: config default
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    assert len(bot.decisions("refuse")) == 1


def test_strategy_using_another_symbol_waits_for_that_symbols_history(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "lag", "strategy": "uses_btc", "symbols": ["ETHUSDT"]},
                                {"id": "solo", "strategy": "always", "symbols": ["ETHUSDT"]}]).start()
    assert bot.engine.strategies["lag"].uses_ctx["ETHUSDT"] and not bot.engine.strategies["solo"].uses_ctx["ETHUSDT"]
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0, syms=["ETHUSDT", "SOLUSDT"])                # BTC's bar never arrives
    bot.ticks(at=T0 + HOUR_MS + 16_000)
    bot.timer()
    w = bot.decisions("warming", setup="lag")
    assert len(w) == 1 and "context symbol BTCUSDT" in w[0]["reason"]
    assert bot.pos("ETHUSDT", "lag") is None and bot.pos("ETHUSDT", "solo") is not None


# ---------------------------------------------------------------- 4h setups
def test_4h_setup_decides_only_at_4h_closes_on_the_4h_bar(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "h4", "strategy": "always4h", "symbols": [B]}]).start()
    for i in range(3):
        bot.ticks(at=T0 + (i + 1) * HOUR_MS - 200)
        bot.close_bar(T0 + i * HOUR_MS)
    assert bot.decisions("signal", "eval", "warming") == []       # 01:00, 02:00, 03:00 closes: not a 4h boundary
    bot.ticks(at=T0 + 4 * HOUR_MS - 200, BTCUSDT=61000.0)
    bot.close_bar(T0 + 3 * HOUR_MS, BTCUSDT=61000.0)
    s = bot.decisions("signal")
    assert len(s) == 1 and s[0]["bar"] == iso(T0) and s[0]["tf"] == "4h"
    p = bot.pos(setup="h4")
    assert p.stop == 60390.0 and p.signal_bar == T0 and p.entry_ts == T0 + 4 * HOUR_MS
    assert p.time_exit_ts == T0 + 12 * HOUR_MS                    # max_hold 2 x 4h from the bar it was filled in


# ---------------------------------------------------------------- funding
def test_funding_is_charged_once_at_the_settlement_and_recorded_on_the_bar(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "params": {"only_hour": 0, "stop_pct": 0.03,
                                                                                  "target_pct": 0.0},
                                 "symbols": [B]}]).start()
    nxt = T0 + 8 * HOUR_MS
    bot.mark(B, 0.0003, nxt, at=T0 + HOUR_MS - 2000)
    p = enter_long(bot)
    cash0 = bot.engine.broker.equity("leader")
    for i in range(1, 7):                                          # bars 01:00 .. 06:00
        bot.mark(B, 0.0003, nxt, at=T0 + (i + 1) * HOUR_MS - 2000)
        bot.ticks(at=T0 + (i + 1) * HOUR_MS - 200)
        bot.close_bar(T0 + i * HOUR_MS)
    bot.mark(B, 0.00031, nxt, mark=60500.0, at=nxt - 900)          # last estimate before the settlement
    bot.tick(B, 60500.0, at=nxt - 200)
    assert bot.decisions("funding", symbol=B) == []
    bot.tick(B, 60500.0, at=nxt + 150)                             # first tick after the funding time
    f = bot.decisions("funding", symbol=B)
    assert len(f) == 1 and f[0]["rate"] == 0.00031 and not f[0]["estimated"]
    amount = -p.qty * 60500.0 * 0.00031
    assert bot.engine.broker.equity("leader") == pytest.approx(cash0 + amount)
    bot.mark(B, 0.0001, nxt + 8 * HOUR_MS, at=nxt + 900)           # the stream moves on to the next period
    bot.close_bar(T0 + 7 * HOUR_MS, at=nxt + 1200)                 # the bar that closed on the settlement
    bot.tick(B, 60500.0, at=nxt + 5000)
    assert len(bot.decisions("funding", symbol=B)) == 1                      # not charged twice
    last = bot.engine.history.frame(B).iloc[-1]
    assert last["funding_paid"] == 0.00031 and last["funding_rate"] == 0.00031
    assert bot.engine.history.frame(B)["funding_paid"].iloc[-2] == 0.0


def test_funding_settlement_without_positions_is_still_logged(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "params": {"only_hour": 5}, "symbols": [B]}]).start()
    bot.mark("ETHUSDT", -0.0001, T0 + HOUR_MS, at=T0 + HOUR_MS - 1000)
    bot.mark("ETHUSDT", -0.0002, T0 + 9 * HOUR_MS, at=T0 + HOUR_MS + 1000)
    f = bot.decisions("funding", symbol="ETHUSDT")
    assert len(f) == 1 and "no open position" in f[0]["reason"] and f[0]["rate"] == -0.0001


def test_stale_mark_data_means_the_bar_funding_is_unknown_not_zero(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "params": {"only_hour": 5}, "symbols": [B]}]).start()
    bot.mark(B, 0.0001, T0 + 8 * HOUR_MS, at=T0 + HOUR_MS - 30_000)   # last mark 30 s before the close
    bot.tick(B, 60000.0, at=T0 + HOUR_MS - 200)
    bot.close_bar(T0, syms=[B])
    last = bot.engine.history.frame(B).iloc[-1]
    assert pd.isna(last["funding_paid"]) and pd.isna(last["funding_rate"])


def test_bar_closing_right_after_the_bot_started_has_unknown_funding(tmp_path):
    """Started seconds after a funding time: the stream already points at the NEXT settlement, but the one
    that may just have happened on this bar's close was never seen. Unknown, not 0.0."""
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "params": {"only_hour": 5}, "symbols": [B]}],
              now=T0 + HOUR_MS + 400).start()
    bot.mark(B, 0.0001, T0 + 9 * HOUR_MS, at=T0 + HOUR_MS + 700)      # first mark arrives after the close instant
    bot.tick(B, 60000.0, at=T0 + HOUR_MS + 800)
    bot.close_bar(T0, syms=[B], at=T0 + HOUR_MS + 1000)
    assert pd.isna(bot.engine.history.frame(B)["funding_paid"].iloc[-1])
    bot.mark(B, 0.0001, T0 + 9 * HOUR_MS, at=T0 + 2 * HOUR_MS - 1000)
    bot.tick(B, 60000.0, at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS, syms=[B])                             # watched the whole hour: now it is a real 0.0
    f = bot.engine.history.frame(B)
    assert f["funding_paid"].iloc[-1] == 0.0 and pd.isna(f["funding_rate"].iloc[-1])


def test_silent_kline_connection_is_a_stale_feed(tmp_path):
    bot = Bot(tmp_path).start()
    t = bot.now
    for s in range(0, 62):                                            # quotes keep coming, markPrice/klines do not
        for sym, px in (("BTCUSDT", 60000.0), ("ETHUSDT", 1800.0), ("SOLUSDT", 75.0)):
            bot.tick(sym, px, at=t + s * 1000)
        bot.timer()
    assert bot.halts() == ["stale_feed"]
    assert "@mark" in bot.engine.shield.halts[0]["reason"]


def test_restart_during_a_stale_halt_does_not_shortcut_the_five_minutes(tmp_path):
    bot = Bot(tmp_path).start()
    bot.ticks()
    bot.timer(at=bot.now + 61_000)                                    # nothing for 61 s
    assert bot.halts() == ["stale_feed"]
    for s in range(1, 200):                                           # healthy for 199 s ...
        bot.ticks(at=bot.now + 1000)
        bot.timer()
    assert bot.halts() == ["stale_feed"]
    bot.kill()
    bot.restart(at=bot.now + 5000)                                    # ... then a quick restart
    for s in range(1, 290):
        bot.ticks(at=bot.now + 1000)
        bot.timer()
    assert bot.halts() == ["stale_feed"]                              # the 5 minutes start again after the restart
    for s in range(1, 15):
        bot.ticks(at=bot.now + 1000)
        bot.timer()
    assert bot.halts() == []


def test_position_on_a_symbol_removed_from_the_config_is_flagged(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": ["SOLUSDT"]}]).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    assert bot.pos("SOLUSDT") is not None
    bot.timer(at=bot.now + 2000)
    bot.kill()
    bot.cfg_over["symbols"] = ["BTCUSDT", "ETHUSDT"]
    bot.restart(at=bot.now + 5000, setups=[{"id": "leader", "strategy": "always", "symbols": ["BTCUSDT"]}])
    fl = bot.decisions("flag")
    assert len(fl) == 1 and "no price feed" in fl[0]["reason"] and fl[0]["symbol"] == "SOLUSDT"
    assert any(k.startswith("orphan_position") for k in bot.engine.shield.status()["flags"])


def test_strategy_that_cannot_be_probed_is_refused(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}])
    bot.funding_archive = bot.funding_archive.iloc[:0]               # no archived funding at all: nothing is fully known
    bot.start()
    assert set(bot.engine.strategies["leader"].needs[B]) == {"funding_paid", "funding_rate", "oi", "oi_value",
                                                              "top_ls_ratio", "acct_ls_ratio"}
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    assert len(bot.decisions("refuse")) == 1 and bot.pos() is None


# ---------------------------------------------------------------- restarts
def test_kill_mid_position_and_resume_no_double_entry_no_lost_stop(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    p0 = enter_long(bot)
    bot.timer(at=T0 + HOUR_MS + 20_000)                           # status.json written: last alive
    bot.kill()
    bot.restart(at=T0 + HOUR_MS + 45_000)                         # back after 25 s
    p = bot.pos()
    assert p is not None and (p.id, p.stop, p.qty, p.entry_price) == (p0.id, p0.stop, p0.qty, p0.entry_price)
    assert bot.halts() == []
    assert any("resumed after" in r for r in reasons(bot.decisions("recover")))
    # the close of the bar we already traded is replayed: no second order, no block noise
    bot.ticks(at=T0 + HOUR_MS + 46_000)
    bot.close_bar(T0, at=T0 + HOUR_MS + 46_500)
    assert len(bot.decisions("fill")) == 1 and bot.decisions("block") == []
    # the stop still works, and gaps are filled at the price we see
    bot.tick(B, 59000.0, at=T0 + HOUR_MS + 50_000)
    assert bot.pos() is None
    t = bot.trades()
    assert len(t) == 1 and t[0]["exit_reason"] == "stop" and float(t[0]["exit_price"]) == pytest.approx(59000.0 * (1 - 2 * BPS))


def test_restart_after_a_long_outage_flattens_at_the_first_tick_and_halts(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    enter_long(bot)
    bot.timer(at=T0 + HOUR_MS + 20_000)
    bot.kill()
    bot.restart(at=T0 + HOUR_MS + 20 * MIN)                       # container was gone for 20 minutes
    assert bot.halts() == ["stale_feed"]
    h = json.loads((bot.cfg.state_dir / "HALT").read_text())
    assert "bot was down for 1180 s" in h["halts"][0]["reason"] and h["blocks_restart"] is False
    assert bot.pos().flatten_reason == "halt:stale_feed"          # no price yet: flatten is pending, on disk
    bot.tick(B, 60200.0, at=T0 + HOUR_MS + 20 * MIN + 2000)
    assert bot.pos() is None
    t = bot.trades()[0]
    assert t["exit_reason"] == "halt:stale_feed" and float(t["exit_price"]) == pytest.approx(60200.0 * (1 - BPS))
    # trading resumes on its own after 5 healthy minutes
    t0 = bot.now
    for s in range(0, 301, 10):
        bot.ticks(at=t0 + s * 1000)
        bot.timer()
    assert bot.halts() == [] and not (bot.cfg.state_dir / "HALT").exists()


def test_restart_with_a_resting_order_after_a_long_outage_cancels_it(tmp_path):
    setups = [{"id": "leader", "strategy": "always", "params": {"limit_pct": 0.002, "entry_valid": 5}, "symbols": [B]}]
    bot = Bot(tmp_path, setups=setups).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)
    assert bot.order() is not None
    bot.timer(at=T0 + HOUR_MS + 5000)
    bot.kill()
    bot.restart(at=T0 + HOUR_MS + 10 * MIN)
    assert bot.order() is None and bot.halts() == ["stale_feed"]
    assert any("halt:stale_feed" in r for r in reasons(bot.decisions("cancel")))


def test_trade_closed_right_before_a_crash_is_repaired_in_the_ledger(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    enter_long(bot)
    bot.tick(B, 59399.0, at=T0 + HOUR_MS + 5 * MIN)
    (bot.cfg.log_dir / "trades.csv").unlink()                     # the ledger write never made it to disk
    (bot.cfg.state_dir / "shield.json").unlink()                  # nor did Shield's count
    bot.kill()
    bot.restart(at=T0 + HOUR_MS + 5 * MIN + 5000)
    assert len(bot.trades()) == 1 and bot.trades()[0]["id"] == "leader-00001"
    assert bot.engine.shield.books["leader"]["consecutive_losses"] == 1
    bot.kill()
    bot.restart(at=T0 + HOUR_MS + 5 * MIN + 9000)                 # and it is idempotent
    assert len(bot.trades()) == 1 and bot.engine.shield.books["leader"]["consecutive_losses"] == 1


def test_funding_settled_during_an_outage_is_charged_as_an_estimate(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "params": {"only_hour": 0, "stop_pct": 0.03},
                                 "symbols": [B]}]).start()
    nxt = T0 + 2 * HOUR_MS
    bot.mark(B, 0.0004, nxt, at=T0 + HOUR_MS - 2000)
    p = enter_long(bot)
    bot.timer(at=T0 + HOUR_MS + 6000)
    bot.kill()
    bot.restart(at=nxt + 30_000)                                  # down across the settlement
    f = bot.decisions("funding", symbol=B)
    assert len(f) == 1 and f[0]["estimated"] and "ESTIMATED" in f[0]["reason"]
    bot.tick(B, 60000.0, at=nxt + 31_000)
    assert "funding_estimated" in bot.trades()[0]["flags"]
    assert float(bot.trades()[0]["funding"]) == pytest.approx(-p.qty * 60000.0 * 0.0004)


def test_config_swap_between_runs(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "always", "symbols": [B]}]).start()
    enter_long(bot)
    bot.timer(at=T0 + HOUR_MS + 5000)
    bot.kill()
    new = [{"id": "leader", "role": "leader", "strategy": "always", "params": {"side": -1}, "symbols": [B]},
           {"id": "runner", "strategy": "always", "symbols": ["ETHUSDT"]}]
    bot.restart(at=T0 + HOUR_MS + 30_000, setups=new)
    books = bot.engine.broker.books
    assert set(books) == {"leader", "runner", f"leader@{[k for k in books if '@' in k][0].split('@')[1]}"}
    assert books["leader"].positions == {} and books["leader"].cash == 1000.0
    bot.ticks(at=T0 + HOUR_MS + 31_000)
    assert bot.trades()[0]["exit_reason"] == "setup_changed" and "@" in bot.trades()[0]["setup"]
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)
    assert bot.pos(B, "leader").side == -1 and bot.pos("ETHUSDT", "runner").side == 1


# ---------------------------------------------------------------- halts through the engine
def test_stale_feed_halts_cancels_and_flattens_then_recovers(tmp_path):
    bot = Bot(tmp_path).start()
    enter_long(bot)
    t = T0 + HOUR_MS
    for s in range(1, 62):                                         # BTC and ETH keep ticking, SOL goes silent
        bot.tick(B, 60010.0, at=t + s * 1000)
        bot.tick("ETHUSDT", 1800.0)
        bot.timer()
    assert bot.halts() == ["stale_feed"]
    assert bot.pos(B) is None and bot.pos("ETHUSDT") is None       # flattened at market: fresh quotes
    assert bot.pos("SOLUSDT").flatten_reason == "halt:stale_feed"  # no quote: waits for its next tick
    assert any("flattened at their next tick" in r for r in reasons(bot.decisions("exit_pending")))
    bot.ticks(at=t + 70_000)
    assert bot.pos("SOLUSDT") is None
    assert sorted(x["exit_reason"] for x in bot.trades()) == ["halt:stale_feed"] * 3
    bot.close_bar(T0 + HOUR_MS, at=T0 + 2 * HOUR_MS + 300)         # a bar closes while halted: bars persist
    assert len(bot.engine.history.frame(B)) == 602
    assert bot.pos(B) is None
    assert all("halted: stale_feed" in d["reason"] for d in bot.decisions("block"))
    assert len(bot.decisions("block")) == 3


def test_daily_loss_halts_only_the_losing_book(tmp_path):
    setups = [{"id": "leader", "role": "leader", "strategy": "always", "params": {"stop_pct": 0.1}, "symbols": [B]},
              {"id": "runner", "strategy": "always", "symbols": ["ETHUSDT"]}]
    bot = Bot(tmp_path, setups=setups, account={"risk_per_trade": 0.05}).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.timer()
    bot.close_bar(T0)
    p = bot.pos(B, "leader")
    assert p.qty == 0.008 and p.stop == 54000.0
    drop = 60000.0 - 31.0 / p.qty                                  # mark-to-market loss just over 3% of 1000
    bot.tick(B, drop, at=T0 + HOUR_MS + 10_000)
    bot.tick("ETHUSDT", 1800.0)
    bot.tick("SOLUSDT", 75.0)
    bot.timer()
    assert [(h["kind"], h["scope"]) for h in bot.engine.shield.halts] == [("daily_loss", "leader")]
    assert bot.pos(B, "leader") is None and bot.pos("ETHUSDT", "runner") is not None
    assert bot.trades()[0]["exit_reason"] == "halt:daily_loss"


def test_unhandled_exception_flattens_and_needs_a_human(tmp_path):
    setups = [{"id": "leader", "role": "leader", "strategy": "always", "symbols": [B]},
              {"id": "bad", "strategy": "boom", "symbols": ["ETHUSDT"]}]
    bot = Bot(tmp_path, setups=setups).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    with pytest.raises(ZeroDivisionError) as ei:
        bot.close_bar(T0)
    bot.engine.fatal(ei.value, bot.now, where="main loop")
    h = json.loads((bot.cfg.state_dir / "HALT").read_text())
    assert h["blocks_restart"] and h["needs_human"] and "ZeroDivisionError: strategy bug" in h["halts"][0]["reason"]
    assert bot.engine.broker.open_positions() == []               # the leader's fresh position was flattened
    assert any(x["exit_reason"] == "halt:exception" for x in bot.trades())
    assert json.loads((bot.cfg.state_dir / "status.json").read_text())["running"] is False


def test_kill_file_flattens_and_blocks_until_removed(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    enter_long(bot)
    (bot.cfg.state_dir / "KILL").write_text("owner said stop")
    bot.ticks(at=T0 + HOUR_MS + 3000)
    bot.timer()
    assert bot.halts() == ["kill"] and bot.pos() is None and bot.trades()[0]["exit_reason"] == "halt:kill"
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)
    assert bot.pos() is None and "halted: kill" in bot.decisions("block")[-1]["reason"]
    (bot.cfg.state_dir / "KILL").unlink()
    (bot.cfg.state_dir / "HALT").unlink()
    bot.ticks(at=T0 + 3 * HOUR_MS - 200)
    bot.timer()
    bot.close_bar(T0 + 2 * HOUR_MS)
    assert bot.pos() is not None


def test_three_losses_halt_the_book_through_the_engine(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    for i in range(3):
        bar = T0 + i * HOUR_MS
        enter_long(bot, bar)
        bot.tick(B, 59000.0, at=bar + HOUR_MS + 60_000)
        assert bot.pos() is None
    assert bot.halts() == ["loss_streak"]
    bot.ticks(at=T0 + 4 * HOUR_MS - 200)
    bot.close_bar(T0 + 3 * HOUR_MS)
    assert bot.pos() is None and "halted: loss_streak" in bot.decisions("block")[-1]["reason"]


def test_clock_anomalies_from_the_feed_halt(tmp_path):
    bot = Bot(tmp_path).start()
    bot.ticks()
    bot.engine.on_book(B, 60000.0, 60000.1, bot.now + 6000, bot.now)          # event 6 s in the future
    assert bot.halts() == ["clock"] and bot.engine.ticks[B].bid == 60000.0

    bot2 = Bot(tmp_path / "2").start()
    bot2.engine.on_kline(B, Bot.kline(T0 + 5 * HOUR_MS, 60000.0, closed=True), bot2.now, bot2.now)   # bar from the future
    assert bot2.halts() == ["clock"] and len(bot2.engine.history.frame(B)) == 600

    bot3 = Bot(tmp_path / "3").start()
    bot3.engine.on_kline(B, Bot.kline(T0 + 1234, 60000.0, closed=True), bot3.now, bot3.now)          # not on the hour
    assert bot3.halts() == ["clock"]

    bot4 = Bot(tmp_path / "4").start()
    bot4.ticks()
    bot4.engine.on_book(B, 59000.0, 59000.1, bot4.now - 9000, bot4.now)       # event time jumped back 9 s
    assert bot4.halts() == ["clock"] and bot4.engine.ticks[B].bid == 60000.0

    bot5 = Bot(tmp_path / "5").start()
    bot5.ticks()
    bot5.engine.on_book(B, 59000.0, 59000.1, bot5.now - 500, bot5.now)        # slightly out of order: just ignored
    bot5.engine.on_book(B, 0.0, 59000.1, bot5.now, bot5.now)
    bot5.engine.on_mark(B, 60000.0, 0.0001, T0 + 8 * HOUR_MS, bot5.now + 9000, bot5.now)
    assert bot5.halts() == ["clock"] and bot5.engine.ticks[B].bid == 60000.0


def test_kline_low_through_the_stop_closes_a_position_the_ticks_missed(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "params": {"only_hour": 0}, "symbols": [B]}]).start()
    enter_long(bot)
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)
    # in the next bar the feed hiccups; the running kline shows a low under the stop although the quote recovered
    bot.tick(B, 60020.0, at=T0 + 2 * HOUR_MS + 30_000)
    bot.engine.on_kline(B, Bot.kline(T0 + 2 * HOUR_MS, 60020.0, low=59350.0), bot.now, bot.now)
    assert bot.pos() is None
    t = bot.trades()[0]
    assert t["exit_reason"] == "stop" and t["flags"] == "stop_seen_on_kline"
    assert float(t["exit_price"]) == pytest.approx(59400.0 * (1 - 2 * BPS))


# ---------------------------------------------------------------- heartbeat / status / misc
def test_heartbeat_on_start_and_every_five_minutes_on_the_clock(tmp_path):
    bot = Bot(tmp_path, now=T0 + 58 * MIN + 20_000).start()
    bot.ticks()
    bot.timer()
    assert len(bot.decisions("heartbeat")) == 1                   # first one as soon as every symbol ticked
    for s in range(1, 100):
        bot.ticks(at=T0 + 58 * MIN + 20_000 + s * 1000)
        bot.timer()
    assert len(bot.decisions("heartbeat")) == 1                   # 59:59 - still the same 5-minute slot
    bot.ticks(at=T0 + 60 * MIN + 500)
    bot.timer()
    hb = bot.decisions("heartbeat")
    assert len(hb) == 2 and hb[1]["ts"].startswith("2026-11-18T01:00:00")
    assert "alive" in hb[1]["reason"] and "shield ok" in hb[1]["reason"] and "WARMING" not in hb[0]["reason"]
    assert set(hb[1]["tick_age_s"]) == set(SYMS) | {f"{x}@mark" for x in SYMS} and hb[1]["equity"] == {"leader": 1000.0}
    bot.ticks(at=T0 + 65 * MIN + 100)
    bot.timer()
    assert len(bot.decisions("heartbeat")) == 3


def test_heartbeat_without_any_tick_still_comes_after_ten_seconds(tmp_path):
    bot = Bot(tmp_path).start()
    bot.timer(at=bot.now + 5000)
    assert bot.decisions("heartbeat") == []
    bot.timer(at=bot.now + 6000)
    assert len(bot.decisions("heartbeat")) == 1


def test_status_file_tells_the_whole_story(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "always", "symbols": [B]}]).start()
    enter_long(bot)
    bot.tick(B, 60100.0, at=T0 + HOUR_MS + 6000)
    bot.timer()
    st = json.loads((bot.cfg.state_dir / "status.json").read_text())
    assert st["running"] and st["mode"] == "paper" and st["ticks"][B]["bid"] == 60100.0
    b = st["books"]["leader"]
    assert b["positions"][0]["stop"] == 59400.0 and b["unrealized"] == pytest.approx(0.008 * (60100.0 - 60006.10001))
    assert b["day_pnl"] == pytest.approx(b["mark_equity"] - 1000.0)
    assert st["history"][B]["missing_bars"] == 0 and st["shield"]["halted"] is False
    assert st["strategies"]["leader"] == {"strategy": "always", "tf": "1h", "warmup": 50, "needs": {}}
    assert st["filters_verified"] is False


def test_start_and_stop_are_journaled(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}]).start()
    enter_long(bot)
    bot.engine.shutdown(bot.now + 1000)
    d = bot.decisions("start", "stop")
    assert "bot started in paper mode" in d[0]["reason"] and "exchange filters verified: False" in d[0]["reason"]
    assert d[-1]["kind"] == "stop" and "1 open position(s)" in d[-1]["reason"] and "NOT watched" in d[-1]["reason"]
    assert json.loads((bot.cfg.state_dir / "status.json").read_text())["running"] is False


def test_feed_connection_events_are_journaled_and_unknown_events_ignored(tmp_path):
    bot = Bot(tmp_path).start()
    bot.engine.on_event(("conn", "public", "down", "ConnectionResetError: reset", bot.now), bot.now)
    bot.engine.on_event(("conn", "public", "up", "connect #2", bot.now), bot.now)
    bot.engine.on_event(("book", "DOGEUSDT", 1.0, 1.1, bot.now, bot.now), bot.now)
    bot.engine.on_event(("mark", "DOGEUSDT", 1.0, 0.0, bot.now + 1, bot.now, bot.now), bot.now)
    bot.engine.on_event(("kline", "DOGEUSDT", Bot.kline(T0, 1.0, closed=True), bot.now, bot.now), bot.now)
    bot.engine.on_event(("kline", B, {**Bot.kline(T0, 1.0, closed=True), "i": "1m"}, bot.now, bot.now), bot.now)
    bot.engine.on_event(("mystery",), bot.now)
    f = bot.decisions("feed")
    assert len(f) == 2 and "is down" in f[0]["reason"] and len(bot.engine.history.frame(B)) == 600


def test_run_loop_processes_events_and_stops(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}])
    hist_now = [T0 + HOUR_MS - 5000]

    class FakeFeed:
        def __init__(self):
            t = T0 + HOUR_MS
            self.events = [("book", B, 60000.0, 60000.1, t - 300, t - 250),
                           ("mark", B, 60000.0, 0.0001, t + 7 * HOUR_MS, t - 200, t - 150),
                           ("kline", B, Bot.kline(T0, 60000.0, closed=True), t + 20, t + 60),
                           ("kline", "ETHUSDT", Bot.kline(T0, 1800.0, closed=True), t + 20, t + 60),
                           ("kline", "SOLUSDT", Bot.kline(T0, 75.0, closed=True), t + 20, t + 60), None, None]

        def get(self, timeout):
            ev = self.events.pop(0) if self.events else None
            hist_now[0] = (ev[-1] if ev else hist_now[0] + 300)
            return ev

        def stats(self):
            return {"public": {"connected": True}}

    from bot.engine import Engine
    from bot.history import History
    hist = History(bot.cfg.symbols, bot.cfg.state_dir, base_loader=lambda s: bot.frames[s],
                   funding_archive=lambda s: bot.funding_archive)
    eng = Engine(bot.cfg, history=hist, strategies_dir=bot.sdir, clock=lambda: hist_now[0])
    feed = FakeFeed()
    eng.run(feed, should_stop=lambda: not feed.events)
    assert eng.broker.book("leader").positions[B].qty == 0.008
    assert eng.feed_stats == {"public": {"connected": True}}
    eng2 = Engine(bot.cfg, history=hist, strategies_dir=bot.sdir, clock=lambda: hist_now[0])
    eng2.run(FakeFeed(), should_stop=lambda: False, max_seconds=0)             # the smoke-run time limit
