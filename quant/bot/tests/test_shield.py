"""Every Shield rule: order blocks, halts, auto-clears, human clears. Written before bot/shield.py."""
from __future__ import annotations

import json

import pytest

from bot.journal import Journal, read_decisions
from bot.models import Trade
from bot.shield import Shield
from bot.util import HOUR_MS, iso

from .conftest import T0, make_cfg, make_intent

DAY_MS = 24 * HOUR_MS
NOW = T0 + 1000


class Exec:
    def __init__(self):
        self.calls = []

    def __call__(self, setups, reason, now_ms):
        self.calls.append((None if setups is None else sorted(setups), reason))


@pytest.fixture
def env(tmp_path):
    cfg = make_cfg(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "a"},
                                     {"id": "runner", "strategy": "b"}])
    ex = Exec()
    sh = Shield(cfg, Journal(cfg.log_dir), executor=ex)
    return cfg, sh, ex


def review(sh, intent=None, *, equity=1000.0, gross=0.0, exposure=None, quote=(60100.0, 60100.1, NOW),
           now=NOW, **kw):
    intent = intent or make_intent(**kw)
    return sh.review(intent, book_equity=equity, gross_notional=gross, exposure=exposure, quote=quote, now_ms=now)


def trade(i, net, setup="leader", symbol="BTCUSDT"):
    return Trade(id=f"{setup}-{i:05d}", setup=setup, symbol=symbol, side=1, qty=0.008, entry_ts=T0, entry_price=60000.0,
                 entry_type="limit", exit_ts=T0 + 1000, exit_price=59400.0, exit_reason="stop", gross=net, fees=0.0,
                 funding=0.0, net=net, equity_after=1000.0 + net, r_multiple=None, signal_bar=T0 - HOUR_MS)


def blocks(cfg):
    return [d for d in read_decisions(cfg.log_dir / "decisions.jsonl") if d["kind"] == "block"]


def halt_file(cfg):
    p = cfg.state_dir / "HALT"
    return json.loads(p.read_text()) if p.exists() else None


# ---------------------------------------------------------------- order review
def test_a_sane_order_is_approved(env):
    cfg, sh, _ = env
    a = review(sh)
    assert a.approved and a.issuer is sh and a.intent_id == make_intent().id
    assert blocks(cfg) == []


@pytest.mark.parametrize("kw, needle", [
    (dict(stop=None), "no stop"),
    (dict(stop=float("nan")), "no stop"),
    (dict(stop=60500.0), "stop on the wrong side"),                         # long with stop above entry
    (dict(side=-1, entry=60200.0, stop=59900.0, target=59000.0), "stop on the wrong side"),
    (dict(entry=None, stop=60100.0), "stop on the wrong side"),              # market long, stop at/above the bid
    (dict(target=59900.0), "target on the wrong side"),
    (dict(qty=0.06), "above the leverage cap"),                              # 0.06 * 60000 = 3600 > 3 * 1000
    (dict(qty=0.02), "above the risk budget"),                               # 0.02 * 600 = 12 > 5
    (dict(qty=0.0), "size is zero"),
    (dict(qty=0.0085), "not on the exchange step"),
    (dict(qty=0.001, stop=59990.0), "below the exchange minimum"),           # 60 USDT notional < 100
    (dict(entry=60000.05), "not on the exchange tick"),
    (dict(symbol="DOGEUSDT"), "not a configured symbol"),
    (dict(side=0), "side"),
    (dict(setup="nobody"), "unknown setup"),
])
def test_bad_orders_are_blocked_with_a_reason_and_logged(env, kw, needle):
    cfg, sh, _ = env
    a = review(sh, **kw)
    assert not a.approved and needle in a.reason, a.reason
    b = blocks(cfg)
    assert len(b) == 1 and needle in b[0]["reason"] and b[0]["symbol"] == kw.get("symbol", "BTCUSDT")


def test_duplicate_position_or_resting_order_is_blocked(env):
    cfg, sh, _ = env
    assert "already has a position" in review(sh, exposure="position").reason
    assert "already has a resting order" in review(sh, exposure="order").reason
    assert len(blocks(cfg)) == 2


def test_gross_leverage_cap_across_the_book(env):
    _, sh, _ = env
    a = review(sh, gross=2700.0)                                             # 2700 + 480 > 3000
    assert not a.approved and "gross" in a.reason


def test_stale_or_missing_quote_blocks(env):
    _, sh, _ = env
    assert "no fresh quote" in review(sh, quote=None).reason
    assert "no fresh quote" in review(sh, quote=(60100.0, 60100.1, NOW - 11_000)).reason
    assert review(sh, quote=(60100.0, 60100.1, NOW - 9_000)).approved


def test_late_decision_blocks(env):
    _, sh, _ = env
    a = review(sh, now=T0 + 31_000, quote=(60100.0, 60100.1, T0 + 31_000))
    assert not a.approved and "late" in a.reason
    assert review(sh, now=T0 + 29_000, quote=(60100.0, 60100.1, T0 + 29_000)).approved


def test_live_mode_is_blocked_in_this_build(tmp_path):
    cfg = make_cfg(tmp_path, mode="live")
    sh = Shield(cfg, Journal(cfg.log_dir), executor=Exec())
    assert "paper" in review(sh).reason


def test_exits_are_never_blocked_even_when_halted(env):
    _, sh, _ = env
    sh.on_exception(RuntimeError("boom"), NOW)
    a = sh.approve_exit("leader", "BTCUSDT", "stop")
    assert a.approved and a.intent_id == "exit:leader:BTCUSDT" and a.issuer is sh


# ---------------------------------------------------------------- halts
def test_daily_loss_halts_the_book_and_clears_on_the_next_utc_day(env):
    cfg, sh, ex = env
    sh.check_equity("leader", 1000.0, NOW)
    sh.check_equity("runner", 1000.0, NOW)
    sh.check_equity("leader", 970.01, NOW + 1000)
    assert sh.halted("leader") == []
    sh.check_equity("leader", 970.0, NOW + 2000)                             # exactly -3%
    h = sh.halted("leader")
    assert [x["kind"] for x in h] == ["daily_loss"] and not h[0]["needs_human"]
    assert ex.calls == [(["leader"], "halt:daily_loss")]
    assert sh.halted("runner") == []                                         # the other book keeps trading
    assert review(sh, setup="runner", equity=1000.0).approved
    a = review(sh)
    assert not a.approved and "halted" in a.reason and "daily_loss" in a.reason
    hf = halt_file(cfg)
    assert hf["halts"][0]["kind"] == "daily_loss" and "-3.00%" in hf["halts"][0]["reason"]
    assert hf["blocks_restart"] is False
    # same day, later: still halted, executor not called again
    sh.check_equity("leader", 969.0, NOW + 3 * HOUR_MS)
    assert len(ex.calls) == 1
    # next UTC day: auto-clear, new baseline
    sh.check_equity("leader", 969.0, T0 + DAY_MS + 5)
    assert sh.halted("leader") == [] and halt_file(cfg) is None
    sh.check_equity("leader", 969.0 * 0.9701, T0 + DAY_MS + 10)
    assert sh.halted("leader") == []
    sh.check_equity("leader", 969.0 * 0.97 - 0.01, T0 + DAY_MS + 20)
    assert [x["kind"] for x in sh.halted("leader")] == ["daily_loss"]


def test_three_straight_losses_halt_until_a_human_deletes_halt(env):
    cfg, sh, ex = env
    sh.on_trade_closed(trade(1, -5.0), NOW)
    sh.on_trade_closed(trade(2, -5.0), NOW)
    assert sh.halted("leader") == []
    sh.on_trade_closed(trade(2, -5.0), NOW)                                  # same trade replayed: not counted twice
    assert sh.halted("leader") == []
    sh.on_trade_closed(trade(3, -0.01), NOW)
    h = sh.halted("leader")
    assert [x["kind"] for x in h] == ["loss_streak"] and h[0]["needs_human"]
    assert ex.calls == [(["leader"], "halt:loss_streak")]
    assert sh.halted("runner") == []
    # it survives the UTC day roll and a restart
    sh.check_equity("leader", 985.0, T0 + DAY_MS + 5)
    assert [x["kind"] for x in sh.halted("leader")] == ["loss_streak"]
    sh2 = Shield(cfg, Journal(cfg.log_dir), executor=Exec())
    assert [x["kind"] for x in sh2.halted("leader")] == ["loss_streak"]
    assert not review(sh2).approved
    # the human deletes HALT
    (cfg.state_dir / "HALT").unlink()
    sh2.poll_files(T0 + DAY_MS + 1000)
    assert sh2.halted("leader") == []
    assert review(sh2, now=T0 + DAY_MS + 1000, quote=(60100.0, 60100.1, T0 + DAY_MS + 1000),
                  placed_ts=T0 + DAY_MS, signal_bar=T0 + DAY_MS - HOUR_MS).approved
    sh2.on_trade_closed(trade(4, -5.0), NOW)                                 # the streak restarted from zero
    assert sh2.halted("leader") == []


def test_a_winning_trade_resets_the_streak(env):
    _, sh, _ = env
    for i, net in enumerate([-5, -5, +7, -5, -5], 1):
        sh.on_trade_closed(trade(i, float(net)), NOW)
    assert sh.halted("leader") == []
    sh.on_trade_closed(trade(6, -5.0), NOW)
    assert [x["kind"] for x in sh.halted("leader")] == ["loss_streak"]


def test_loss_streaks_are_counted_per_book(env):
    _, sh, _ = env
    sh.on_trade_closed(trade(1, -5.0), NOW)
    sh.on_trade_closed(trade(1, -5.0, setup="runner"), NOW)
    sh.on_trade_closed(trade(2, -5.0), NOW)
    sh.on_trade_closed(trade(2, -5.0, setup="runner"), NOW)
    assert sh.halted("leader") == [] and sh.halted("runner") == []
    sh.on_trade_closed(trade(3, -5.0, setup="runner"), NOW)
    assert sh.halted("leader") == [] and [x["kind"] for x in sh.halted("runner")] == ["loss_streak"]


def test_stale_feed_halts_everything_and_clears_after_5_healthy_minutes(env):
    cfg, sh, ex = env
    ok = {"BTCUSDT": 0.2, "ETHUSDT": 0.3, "SOLUSDT": 1.0}
    sh.check_feed(ok, NOW)
    sh.check_feed(ok | {"SOLUSDT": 60.0}, NOW + 1000)
    assert sh.halted() == []
    sh.check_feed(ok | {"SOLUSDT": 60.5}, NOW + 2000)
    h = sh.halted("leader")
    assert [x["kind"] for x in h] == ["stale_feed"] and h[0]["scope"] == "account" and not h[0]["needs_human"]
    assert "SOLUSDT" in h[0]["reason"]
    assert ex.calls == [(None, "halt:stale_feed")]
    assert sh.halted("runner") == h
    assert not review(sh).approved
    # healthy again, but not yet for 5 minutes
    t = NOW + 10_000
    sh.check_feed(ok, t)
    sh.check_feed(ok, t + 299_000)
    assert [x["kind"] for x in sh.halted()] == ["stale_feed"]
    # a relapse restarts the 5 minute clock
    sh.check_feed(ok | {"BTCUSDT": 61.0}, t + 299_500)
    sh.check_feed(ok, t + 300_000)
    sh.check_feed(ok, t + 599_000)
    assert [x["kind"] for x in sh.halted()] == ["stale_feed"]
    sh.check_feed(ok, t + 600_000)
    assert sh.halted() == [] and halt_file(cfg) is None
    assert len(ex.calls) == 1


def test_unhandled_exception_halts_until_a_human_clears_and_blocks_restart(env):
    cfg, sh, ex = env
    try:
        raise ZeroDivisionError("division by zero")
    except ZeroDivisionError as e:
        sh.on_exception(e, NOW, where="engine.on_book")
    h = sh.halted("runner")
    assert h[0]["kind"] == "exception" and h[0]["needs_human"] and "ZeroDivisionError" in h[0]["reason"]
    assert ex.calls == [(None, "halt:exception")]
    hf = halt_file(cfg)
    assert hf["needs_human"] is True and hf["blocks_restart"] is True
    sh.check_equity("leader", 1000.0, T0 + DAY_MS + 5)                       # a new day does not clear it
    sh.check_feed({"BTCUSDT": 0.1}, T0 + DAY_MS + 5)
    assert sh.halted()[0]["kind"] == "exception"


def test_clock_anomalies_halt(env):
    _, sh, ex = env
    sh.check_clock(NOW, event_ms=NOW - 300)
    sh.check_clock(NOW + 1000, event_ms=NOW + 1000 + 4_900)                  # 4.9 s ahead: tolerated
    assert sh.halted() == []
    sh.check_clock(NOW + 2000, event_ms=NOW + 2000 + 5_100)
    h = sh.halted()
    assert h[0]["kind"] == "clock" and h[0]["needs_human"] and "ahead of the local clock" in h[0]["reason"]
    assert ex.calls == [(None, "halt:clock")]


def test_local_clock_running_backwards_halts(env):
    cfg, sh, _ = env
    sh.check_clock(NOW)
    sh.check_clock(NOW - 4_000)
    assert sh.halted() == []
    sh.check_clock(NOW - 6_000)
    assert "backwards" in sh.halted()[0]["reason"]


def test_local_clock_backwards_across_a_restart_halts(env):
    cfg, sh, _ = env
    sh.check_clock(NOW)
    sh.save()
    sh2 = Shield(cfg, Journal(cfg.log_dir), executor=Exec())
    sh2.check_clock(NOW - 60_000)
    assert sh2.halted()[0]["kind"] == "clock"


def test_event_time_running_backwards_halts(env):
    _, sh, _ = env
    sh.check_clock(NOW, event_ms=NOW - 100, prev_event_ms=NOW - 200)
    sh.check_clock(NOW, event_ms=NOW - 3000, prev_event_ms=NOW - 200)        # small reordering: tolerated
    assert sh.halted() == []
    sh.check_clock(NOW, event_ms=NOW - 9000, prev_event_ms=NOW - 200)
    assert "event time went backwards" in sh.halted()[0]["reason"]


def test_kill_file_halts_and_cannot_be_cleared_while_it_exists(env):
    cfg, sh, ex = env
    sh.poll_files(NOW)
    assert sh.halted() == []
    (cfg.state_dir / "KILL").write_text("stop it\n")
    sh.poll_files(NOW + 1000)
    h = sh.halted()
    assert h[0]["kind"] == "kill" and h[0]["needs_human"] and "stop it" in h[0]["reason"]
    assert ex.calls == [(None, "halt:kill")]
    (cfg.state_dir / "HALT").unlink()                                        # human removes HALT but not KILL
    sh.poll_files(NOW + 2000)
    assert sh.halted()[0]["kind"] == "kill" and halt_file(cfg) is not None
    (cfg.state_dir / "KILL").unlink()
    (cfg.state_dir / "HALT").unlink()
    sh.poll_files(NOW + 3000)
    assert sh.halted() == []


def test_a_hand_made_halt_file_is_honoured(env):
    cfg, sh, ex = env
    (cfg.state_dir / "HALT").write_text("lunch break, hands off\n")
    sh.poll_files(NOW)
    h = sh.halted()
    assert h[0]["kind"] == "manual" and h[0]["needs_human"] and "lunch break" in h[0]["reason"]
    assert ex.calls == [(None, "halt:manual")]
    assert not review(sh).approved


def test_halt_file_deleted_while_the_bot_was_down_clears_on_start(env):
    cfg, sh, _ = env
    sh.on_exception(RuntimeError("x"), NOW)
    (cfg.state_dir / "HALT").unlink()
    sh2 = Shield(cfg, Journal(cfg.log_dir), executor=Exec())
    assert sh2.halted() == []
    kinds = [d["kind"] for d in read_decisions(cfg.log_dir / "decisions.jsonl")]
    assert "halt" in kinds and "halt_clear" in kinds


def test_halt_file_without_shield_state_is_adopted_on_start(tmp_path):
    cfg = make_cfg(tmp_path)
    cfg.state_dir.mkdir(parents=True)
    (cfg.state_dir / "HALT").write_text(json.dumps({"halts": [
        {"scope": "account", "kind": "exception", "reason": "crash before shield state was saved",
         "since": iso(NOW), "since_ms": NOW, "needs_human": True}]}))
    sh = Shield(cfg, Journal(cfg.log_dir), executor=Exec())
    assert sh.halted()[0]["kind"] == "exception"


def test_human_clear_of_a_daily_loss_halt_resets_the_baseline(env):
    cfg, sh, _ = env
    sh.check_equity("leader", 1000.0, NOW)
    sh.check_equity("leader", 960.0, NOW + 1000)
    assert sh.halted("leader")
    (cfg.state_dir / "HALT").unlink()
    sh.poll_files(NOW + 2000)
    sh.check_equity("leader", 960.0, NOW + 3000)                             # no instant re-halt
    assert sh.halted("leader") == []
    sh.check_equity("leader", 931.0, NOW + 4000)                             # -3% from the new baseline
    assert sh.halted("leader")


def test_every_halt_and_clear_is_journaled_and_status_reports_them(env):
    cfg, sh, _ = env
    sh.check_feed({"BTCUSDT": 99.0}, NOW)
    sh.flag("warming", "WARMING: history gap 8 bars", setup="leader", symbol="BTCUSDT", now_ms=NOW)
    review(sh, stop=None)
    st = sh.status()
    assert st["halted"] and st["halts"][0]["kind"] == "stale_feed"
    assert st["books"]["leader"]["blocks_today"] == 1
    assert st["flags"]["warming:leader:BTCUSDT"]["reason"].startswith("WARMING")
    d = read_decisions(cfg.log_dir / "decisions.jsonl")
    assert [x["kind"] for x in d] == ["halt", "block"]
    assert json.loads((cfg.state_dir / "shield.json").read_text())["halts"]
    sh.clear_flag("warming", setup="leader", symbol="BTCUSDT")
    assert sh.status()["flags"] == {}
