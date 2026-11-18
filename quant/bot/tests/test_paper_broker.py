"""Paper broker = SPEC execution model against book ticks. Written before bot/paper.py existed."""
from __future__ import annotations

import pytest

from bot.exchange import ShieldRejected
from bot.models import Approval
from bot.paper import PaperBroker
from bot.util import HOUR_MS

from .conftest import T0, approve, make_cfg, make_intent

MAKER, TAKER = 0.0002, 0.0005
BPS = 0.0001


def kinds(events):
    return [e["kind"] for e in events]


def trade_of(events):
    ex = [e for e in events if e["kind"] == "exit"]
    assert len(ex) == 1, events
    return ex[0]["trade"]


@pytest.fixture
def broker(cfg):
    b = PaperBroker(cfg)
    b.on_book("BTCUSDT", 60100.0, 60100.1, T0)
    return b


def open_long(b, ts=T0 + 2000, **kw):
    """Resting buy at 60000 filled by the ask coming down to the limit."""
    i = make_intent(**kw)
    b.submit_entry(i, approve(i), T0)
    ev = b.on_book(i.symbol, i.entry - 0.1, i.entry, ts)
    assert kinds(ev) == ["fill"], ev
    return i


# ---------------------------------------------------------------- limit entries: trade-through
def test_limit_buy_rests_and_does_not_fill_on_touch(broker):
    i = make_intent()
    ev = broker.submit_entry(i, approve(i), T0)
    assert kinds(ev) == ["order"]
    # the bid sits AT our limit (we would only be joining the queue), ask one tick above: no fill
    assert broker.on_book("BTCUSDT", 60000.0, 60000.1, T0 + 1000) == []
    # the bid has dropped below the limit but nobody offers at it yet: still not proven filled
    assert broker.on_book("BTCUSDT", 59999.8, 60000.1, T0 + 1500) == []
    assert broker.book("leader").positions == {}


def test_limit_buy_fills_at_limit_with_maker_fee_when_traded_through(broker):
    i = make_intent()
    broker.submit_entry(i, approve(i), T0)
    ev = broker.on_book("BTCUSDT", 59999.9, 60000.0, T0 + 2000)     # offers now AT our limit: queue is gone
    assert kinds(ev) == ["fill"]
    p = broker.book("leader").positions["BTCUSDT"]
    assert p.entry_price == 60000.0 and p.entry_type == "limit"
    assert p.entry_fee == pytest.approx(0.008 * 60000.0 * MAKER)
    assert p.stop == 59400.0 and p.target == 61200.0             # stop is attached from the instant of fill
    assert broker.book("leader").orders == {}
    assert broker.equity("leader") == pytest.approx(1000.0 - p.entry_fee)


def test_limit_sell_needs_bid_at_or_above_limit(broker):
    i = make_intent(side=-1, entry=60200.0, stop=60800.0, target=59000.0)
    broker.submit_entry(i, approve(i), T0)
    assert broker.on_book("BTCUSDT", 60199.9, 60200.0, T0 + 1000) == []      # ask touches: no fill
    ev = broker.on_book("BTCUSDT", 60200.0, 60200.1, T0 + 2000)
    assert kinds(ev) == ["fill"]
    assert broker.book("leader").positions["BTCUSDT"].side == -1


def test_stop_is_live_on_the_very_tick_of_the_fill(broker):
    """Gap through both the limit and the stop: filled at the limit, stopped out on the same tick."""
    i = make_intent()
    broker.submit_entry(i, approve(i), T0)
    ev = broker.on_book("BTCUSDT", 59300.0, 59300.1, T0 + 2000)
    assert kinds(ev) == ["fill", "exit"]
    t = trade_of(ev)
    assert t.exit_reason == "stop" and t.entry_price == 60000.0
    assert t.exit_price == pytest.approx(59300.0 * (1 - 2 * BPS))


def test_limit_marketable_at_placement_is_a_taker_fill_not_above_limit(broker):
    i = make_intent(entry=60150.0, stop=59500.0)         # ask is 60100.1, limit is above it
    ev = broker.submit_entry(i, approve(i), T0)
    assert kinds(ev) == ["fill"]
    p = broker.book("leader").positions["BTCUSDT"]
    assert p.entry_type == "market"
    assert p.entry_price == pytest.approx(min(60150.0, 60100.1 * (1 + BPS)))
    assert p.entry_fee == pytest.approx(p.qty * p.entry_price * TAKER)


def test_resting_limit_expires_after_entry_valid_bars(broker):
    i = make_intent(entry_valid=2)
    broker.submit_entry(i, approve(i), T0)
    assert broker.on_book("BTCUSDT", 60050.0, 60050.1, T0 + 2 * HOUR_MS - 1) == []
    ev = broker.on_book("BTCUSDT", 59999.9, 60000.0, T0 + 2 * HOUR_MS)        # would fill, but it expired
    assert kinds(ev) == ["cancel"] and "expired" in ev[0]["reason"]
    assert broker.book("leader").orders == {} and broker.book("leader").positions == {}


def test_expire_orders_by_clock_without_a_tick(broker):
    i = make_intent(entry_valid=1)
    broker.submit_entry(i, approve(i), T0)
    assert broker.expire_orders(T0 + HOUR_MS - 1) == []
    assert kinds(broker.expire_orders(T0 + HOUR_MS)) == ["cancel"]


# ---------------------------------------------------------------- marketable entries
def test_market_entry_pays_taker_fee_and_slippage(broker):
    i = make_intent(entry=None, ref_price=60100.1)
    ev = broker.submit_entry(i, approve(i), T0)
    assert kinds(ev) == ["fill"]
    p = broker.book("leader").positions["BTCUSDT"]
    assert p.entry_price == pytest.approx(60100.1 * (1 + 1 * BPS))
    assert p.entry_fee == pytest.approx(0.008 * p.entry_price * TAKER)
    assert p.entry_type == "market"


def test_market_short_entry_sells_the_bid_minus_slippage_sol_2bp(cfg):
    b = PaperBroker(cfg)
    b.on_book("SOLUSDT", 75.00, 75.01, T0)
    i = make_intent(symbol="SOLUSDT", side=-1, qty=6.0, entry=None, stop=76.0, target=73.0, ref_price=75.0)
    b.submit_entry(i, approve(i), T0)
    p = b.book("leader").positions["SOLUSDT"]
    assert p.entry_price == pytest.approx(75.00 * (1 - 2 * BPS))


def test_market_entry_without_a_quote_is_refused(cfg):
    b = PaperBroker(cfg)
    i = make_intent(entry=None)
    with pytest.raises(RuntimeError):
        b.submit_entry(i, approve(i), T0)


# ---------------------------------------------------------------- exits
def test_stop_exit_fills_at_stop_with_double_slippage_and_taker_fee(broker):
    open_long(broker)
    assert broker.on_book("BTCUSDT", 59400.1, 59400.2, T0 + 3000) == []       # one tick above the stop
    ev = broker.on_book("BTCUSDT", 59400.0, 59400.1, T0 + 4000)
    t = trade_of(ev)
    assert t.exit_reason == "stop"
    px = 59400.0 * (1 - 2 * BPS)
    assert t.exit_price == pytest.approx(px)
    gross = 0.008 * (px - 60000.0)
    fees = 0.008 * 60000.0 * MAKER + 0.008 * px * TAKER
    assert t.gross == pytest.approx(gross) and t.fees == pytest.approx(fees)
    assert t.net == pytest.approx(gross - fees)
    assert broker.equity("leader") == pytest.approx(1000.0 + gross - fees)
    assert broker.book("leader").positions == {}


def test_gap_through_stop_fills_at_the_gap_price_not_the_stop(broker):
    open_long(broker)
    ev = broker.on_book("BTCUSDT", 58900.0, 58900.1, T0 + 4000)
    t = trade_of(ev)
    assert t.exit_reason == "stop"
    assert t.exit_price == pytest.approx(58900.0 * (1 - 2 * BPS))
    assert t.exit_price < 59400.0 * (1 - 2 * BPS)


def test_short_stop_and_gap(broker):
    i = make_intent(side=-1, entry=60200.0, stop=60800.0, target=59000.0)
    broker.submit_entry(i, approve(i), T0)
    broker.on_book("BTCUSDT", 60200.0, 60200.1, T0 + 1000)
    assert broker.on_book("BTCUSDT", 60799.8, 60799.9, T0 + 2000) == []
    t = trade_of(broker.on_book("BTCUSDT", 60900.0, 60900.1, T0 + 3000))
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(60900.1 * (1 + 2 * BPS))
    assert t.net < 0


def test_target_is_a_resting_limit_maker_fee_fills_at_target(broker):
    open_long(broker)
    assert broker.on_book("BTCUSDT", 61199.9, 61200.0, T0 + 3000) == []       # ask touches the target only
    t = trade_of(broker.on_book("BTCUSDT", 61200.0, 61200.1, T0 + 4000))
    assert t.exit_reason == "target" and t.exit_price == 61200.0
    gross = 0.008 * 1200.0
    fees = 0.008 * 60000.0 * MAKER + 0.008 * 61200.0 * MAKER
    assert t.net == pytest.approx(gross - fees)
    assert t.r_multiple == pytest.approx((gross - fees) / (0.008 * 600.0))


def test_stop_before_target_when_one_bar_touched_both(broker):
    """SPEC: stop and target touched in the same bar -> the stop is assumed to have been hit first."""
    open_long(broker, ts=T0 + 2000)
    bar_open = T0 + HOUR_MS                      # a full bar after the entry
    broker.on_book("BTCUSDT", 60000.0, 60000.1, bar_open + 1000)
    ev = broker.on_bar_extreme("BTCUSDT", bar_open, high=61500.0, low=59300.0, ts=bar_open + 5000)
    t = trade_of(ev)
    assert t.exit_reason == "stop"
    assert t.exit_price == pytest.approx(59400.0 * (1 - 2 * BPS))     # price is back above: filled at the stop
    assert "stop_seen_on_kline" in t.flags


def test_bar_extreme_is_ignored_for_the_bar_the_position_was_opened_in(broker):
    open_long(broker, ts=T0 + 2000)
    # this bar's low may have printed before our fill, so it proves nothing about our stop
    assert broker.on_bar_extreme("BTCUSDT", T0, high=60200.0, low=59000.0, ts=T0 + 5000) == []


def test_bar_extreme_never_fills_a_target(broker):
    open_long(broker, ts=T0 + 2000)
    assert broker.on_bar_extreme("BTCUSDT", T0 + HOUR_MS, high=62000.0, low=59900.0, ts=T0 + HOUR_MS + 5) == []


def test_time_exit_at_open_after_max_hold_bars(broker):
    open_long(broker, ts=T0 + 30 * 60_000, max_hold=3)
    p = broker.book("leader").positions["BTCUSDT"]
    assert p.time_exit_ts == T0 + 3 * HOUR_MS
    assert broker.on_book("BTCUSDT", 60300.0, 60300.1, T0 + 3 * HOUR_MS - 1) == []
    t = trade_of(broker.on_book("BTCUSDT", 60300.0, 60300.1, T0 + 3 * HOUR_MS))
    assert t.exit_reason == "time"
    assert t.exit_price == pytest.approx(60300.0 * (1 - BPS))
    assert t.fees == pytest.approx(0.008 * 60000.0 * MAKER + 0.008 * t.exit_price * TAKER)


def test_time_exit_counts_bars_of_the_strategy_timeframe(broker):
    open_long(broker, ts=T0 + 5 * HOUR_MS, max_hold=2, tf="4h", entry_valid=2)
    assert broker.book("leader").positions["BTCUSDT"].time_exit_ts == T0 + 12 * HOUR_MS


def test_market_fill_stamped_before_the_bar_open_still_counts_from_the_new_bar(broker):
    """The last quote can be a few ms older than the bar open; max_hold must not lose a bar for it."""
    broker.on_book("BTCUSDT", 60100.0, 60100.1, T0 - 40)
    i = make_intent(entry=None, max_hold=2)
    broker.submit_entry(i, approve(i), T0)
    assert broker.book("leader").positions["BTCUSDT"].time_exit_ts == T0 + 2 * HOUR_MS


def test_signal_exit_is_a_taker_market_exit(broker):
    open_long(broker)
    broker.on_book("BTCUSDT", 60500.0, 60500.1, T0 + HOUR_MS)
    t = trade_of(broker.close_position("leader", "BTCUSDT", "signal", approve("exit:leader:BTCUSDT"), T0 + HOUR_MS))
    assert t.exit_reason == "signal" and t.exit_price == pytest.approx(60500.0 * (1 - BPS))


def test_close_position_requires_shield_approval(broker):
    open_long(broker)
    with pytest.raises(ShieldRejected):
        broker.close_position("leader", "BTCUSDT", "signal", None, T0 + HOUR_MS)


# ---------------------------------------------------------------- funding
def test_long_pays_positive_funding_short_receives(cfg):
    b = PaperBroker(cfg)
    b.on_book("BTCUSDT", 60100.0, 60100.1, T0)
    i = make_intent(entry=None)
    b.submit_entry(i, approve(i), T0)
    eq0 = b.equity("leader")
    ev = b.on_funding("BTCUSDT", rate=0.0001, mark=60000.0, funding_ts=T0 + 8 * HOUR_MS)
    assert kinds(ev) == ["funding"]
    assert b.equity("leader") == pytest.approx(eq0 - 0.008 * 60000.0 * 0.0001)
    assert b.book("leader").positions["BTCUSDT"].funding == pytest.approx(-0.048)

    b.on_book("ETHUSDT", 1800.0, 1800.01, T0)
    s = make_intent(symbol="ETHUSDT", side=-1, qty=0.2, entry=None, stop=1830.0, target=1700.0)
    b.submit_entry(s, approve(s), T0)
    eq1 = b.equity("leader")
    b.on_funding("ETHUSDT", rate=0.0001, mark=1800.0, funding_ts=T0 + 8 * HOUR_MS)
    assert b.equity("leader") == pytest.approx(eq1 + 0.2 * 1800.0 * 0.0001)


def test_no_funding_for_a_position_opened_at_or_after_the_settlement(broker):
    i = make_intent(entry=None)
    broker.submit_entry(i, approve(i), T0)
    assert broker.on_funding("BTCUSDT", 0.0001, 60000.0, funding_ts=T0) == []


def test_funding_is_in_the_trade_net(broker):
    open_long(broker)
    broker.on_funding("BTCUSDT", 0.0002, 60000.0, T0 + 8 * HOUR_MS)
    t = trade_of(broker.on_book("BTCUSDT", 61200.0, 61200.1, T0 + 9 * HOUR_MS))
    assert t.funding == pytest.approx(-0.008 * 60000.0 * 0.0002)
    assert t.net == pytest.approx(t.gross - t.fees + t.funding)
    assert broker.equity("leader") == pytest.approx(1000.0 + t.net)


# ---------------------------------------------------------------- guards
def test_no_order_without_an_approved_matching_shield_approval(broker):
    i = make_intent()
    with pytest.raises(ShieldRejected):
        broker.submit_entry(i, None, T0)
    with pytest.raises(ShieldRejected):
        broker.submit_entry(i, Approval(i.id, False, "blocked", issuer=object()), T0)
    with pytest.raises(ShieldRejected):
        broker.submit_entry(i, approve("some:other:order"), T0)
    with pytest.raises(ShieldRejected):
        broker.submit_entry(i, Approval(i.id, True, "forged"), T0)          # no issuer
    assert broker.book("leader").orders == {}


def test_one_position_per_setup_and_symbol(broker):
    open_long(broker)
    j = make_intent(signal_bar=T0)
    with pytest.raises(RuntimeError):
        broker.submit_entry(j, approve(j), T0 + HOUR_MS)
    k = make_intent(symbol="ETHUSDT", qty=0.1, entry=1800.0, stop=1780.0, target=1850.0)
    assert kinds(broker.submit_entry(k, approve(k), T0)) == ["order"]         # other symbol is fine
    with pytest.raises(RuntimeError):
        broker.submit_entry(k, approve(k), T0)                               # resting order counts too


def test_an_order_without_a_stop_never_reaches_the_book(broker):
    i = make_intent(stop=None)
    with pytest.raises(RuntimeError):
        broker.submit_entry(i, approve(i), T0)


def test_a_position_closes_exactly_once(broker):
    open_long(broker)
    assert kinds(broker.on_book("BTCUSDT", 59400.0, 59400.1, T0 + 4000)) == ["exit"]
    assert broker.on_book("BTCUSDT", 59300.0, 59300.1, T0 + 5000) == []
    assert broker.on_book("BTCUSDT", 59400.0, 59400.1, T0 + 6000) == []
    assert broker.book("leader").n_trades == 1


def test_crossed_or_non_positive_quotes_are_ignored(broker):
    open_long(broker)
    assert broker.on_book("BTCUSDT", 0.0, 59000.0, T0 + 3000) == []
    assert broker.on_book("BTCUSDT", 59000.0, 58000.0, T0 + 3000) == []
    assert "BTCUSDT" in broker.book("leader").positions


# ---------------------------------------------------------------- books, flatten, persistence
def test_parallel_books_have_independent_equity(tmp_path):
    cfg = make_cfg(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "a"},
                                     {"id": "runner", "strategy": "b"}])
    b = PaperBroker(cfg)
    b.on_book("BTCUSDT", 60100.0, 60100.1, T0)
    i = make_intent(setup="leader")
    b.submit_entry(i, approve(i), T0)
    b.on_book("BTCUSDT", 59999.9, 60000.0, T0 + 1000)
    b.on_book("BTCUSDT", 59400.0, 59400.1, T0 + 2000)
    assert b.equity("leader") < 1000.0 and b.equity("runner") == 1000.0
    assert b.book("runner").n_trades == 0 and b.book("leader").n_trades == 1


def test_flatten_cancels_orders_and_closes_at_market(broker):
    open_long(broker)
    k = make_intent(symbol="ETHUSDT", qty=0.1, entry=1800.0, stop=1780.0, target=1850.0)
    broker.submit_entry(k, approve(k), T0)
    broker.on_book("BTCUSDT", 60050.0, 60050.1, T0 + 9000)
    ev = broker.flatten(None, "halt:test", T0 + 10_000, max_quote_age_ms=10_000)
    assert sorted(kinds(ev)) == ["cancel", "exit"]
    t = trade_of(ev)
    assert t.exit_reason == "halt:test" and t.exit_price == pytest.approx(60050.0 * (1 - BPS))
    assert broker.open_positions() == [] and broker.open_orders() == []


def test_flatten_without_a_fresh_quote_waits_for_the_next_tick(broker):
    open_long(broker, ts=T0 + 2000)
    ev = broker.flatten(None, "halt:stale_feed", T0 + 120_000, max_quote_age_ms=10_000)
    assert ev == []                                             # last quote is 118 s old: no price to trade at
    assert broker.book("leader").positions["BTCUSDT"].flatten_reason == "halt:stale_feed"
    t = trade_of(broker.on_book("BTCUSDT", 60010.0, 60010.1, T0 + 130_000))
    assert t.exit_reason == "halt:stale_feed"


def test_pending_flatten_still_respects_a_gapped_stop(broker):
    open_long(broker, ts=T0 + 2000)
    broker.flatten(None, "halt:stale_feed", T0 + 120_000, max_quote_age_ms=10_000)
    t = trade_of(broker.on_book("BTCUSDT", 59000.0, 59000.1, T0 + 130_000))
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(59000.0 * (1 - 2 * BPS))


def test_state_survives_a_restart_and_the_stop_is_not_lost(cfg):
    b = PaperBroker(cfg)
    b.on_book("BTCUSDT", 60100.0, 60100.1, T0)
    open_long(b)
    k = make_intent(symbol="ETHUSDT", qty=0.1, entry=1800.0, stop=1780.0, target=1850.0, entry_valid=3)
    b.submit_entry(k, approve(k), T0)
    b.set_last_decision("leader", "BTCUSDT", T0 - HOUR_MS)
    eq = b.equity("leader")
    del b                                                        # "kill -9": nothing but the state file remains

    b2 = PaperBroker(cfg)
    assert b2.equity("leader") == pytest.approx(eq)
    p = b2.book("leader").positions["BTCUSDT"]
    assert (p.stop, p.target, p.qty, p.entry_price) == (59400.0, 61200.0, 0.008, 60000.0)
    assert b2.book("leader").orders["ETHUSDT"].limit == 1800.0
    assert b2.last_decision("leader", "BTCUSDT") == T0 - HOUR_MS
    with pytest.raises(RuntimeError):                            # no double entry after the restart
        j = make_intent()
        b2.submit_entry(j, approve(j), T0 + 5000)
    t = trade_of(b2.on_book("BTCUSDT", 59350.0, 59350.1, T0 + 60_000))   # first tick after restart is past the stop
    assert t.exit_reason == "stop" and t.exit_price == pytest.approx(59350.0 * (1 - 2 * BPS))
    assert kinds(b2.on_book("ETHUSDT", 1799.99, 1800.0, T0 + 61_000)) == ["fill"]


def test_config_swap_retires_the_old_book_and_starts_a_fresh_one(tmp_path):
    cfg1 = make_cfg(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "alpha", "params": {"n": 20}}])
    b = PaperBroker(cfg1)
    b.on_book("BTCUSDT", 60100.0, 60100.1, T0)
    open_long(b)
    k = make_intent(symbol="ETHUSDT", qty=0.1, entry=1800.0, stop=1780.0, target=1850.0)
    b.submit_entry(k, approve(k), T0)
    old_fp = cfg1.setups[0].fingerprint

    cfg2 = make_cfg(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "beta", "params": {"n": 5}},
                                      {"id": "runner", "strategy": "alpha", "params": {"n": 20}}])
    b2 = PaperBroker(cfg2)
    ev = b2.startup_events
    assert any(e["kind"] == "cancel" and e["symbol"] == "ETHUSDT" for e in ev)
    assert b2.book("leader").strategy == "beta" and b2.equity("leader") == 1000.0
    assert b2.book("leader").positions == {} and b2.equity("runner") == 1000.0
    old = b2.books[f"leader@{old_fp}"]
    assert not old.active and old.positions["BTCUSDT"].flatten_reason == "setup_changed"
    t = trade_of(b2.on_book("BTCUSDT", 60300.0, 60300.1, T0 + 9000))          # old position leaves at first tick
    assert t.setup == f"leader@{old_fp}" and t.exit_reason == "setup_changed"
    # an unchanged config keeps the book as it is
    b3 = PaperBroker(cfg2)
    assert b3.startup_events == [] and b3.book("leader").strategy == "beta"


def test_unrealized_marks_at_the_side_we_would_exit_on(broker):
    open_long(broker)
    broker.on_book("BTCUSDT", 60100.0, 60110.0, T0 + 3000)
    assert broker.unrealized("leader") == pytest.approx(0.008 * (60100.0 - 60000.0))
    assert broker.mark_equity("leader") == pytest.approx(broker.equity("leader") + 0.8)
    assert broker.gross_notional("leader") == pytest.approx(0.008 * 60000.0)
