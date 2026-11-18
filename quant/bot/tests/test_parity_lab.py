"""Trade-list parity with the backtester on Radar's real strategies.

Archived bars are replayed one by one through the bot (Engine -> Shield -> PaperBroker, driven by
bot/replay.py) and the resulting trades are compared with lab.engine.run_backtest over the same
window, exchange filters on in both. Covered: market entries, stops, exit flags and the
stop-and-reverse rule (donchian_trend), resting limit entries with targets and max_hold
(pullback_limit_sma, breakout_retest), three symbols with different tick/step sizes, 1h and 4h.

Shield's loss-streak halt is switched off here (the backtester has no such rule) and no timer runs,
so no daily-loss or stale-feed halt interferes. The bot rounds limit/stop/target to the exchange
tick, the backtester does not: prices agree to a tick, the trade list itself must be identical.
"""
from __future__ import annotations

import importlib

import pandas as pd
import pytest

from bot.engine import Engine
from bot.history import History, index_ms
from bot.instruments import INSTRUMENTS
from bot.journal import read_decisions, read_trades
from bot.replay import BarReplay
from bot.util import HOUR_MS, TF_MS, parse_iso

from .conftest import make_cfg

REASON = {"stop": "stop", "stop_gap": "stop", "target": "target", "exit_signal": "signal", "time": "time"}

CASES = [
    # strategy, symbol, first bar, last bar, least number of trades that makes the comparison meaningful
    ("donchian_trend", "ETHUSDT", "2026-05-01 00:00", "2026-10-31 12:00", 10),
    ("pullback_limit_sma", "BTCUSDT", "2026-08-01 00:00", "2026-10-31 12:00", 5),
    ("breakout_retest", "SOLUSDT", "2026-10-01 00:00", "2026-10-31 15:00", 5),
]


def replay(tmp_path, name, sym, start, end):
    le = pytest.importorskip("lab.engine")
    try:
        mod = importlib.import_module(f"strategies.{name}")
    except ImportError:
        pytest.skip(f"strategies/{name}.py is not there")
    from strategies.data import load

    full1 = load(sym, "1h")
    start, end = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    res = le.run_module(mod, sym, start=start, end=end, cfg=le.ExecConfig(filters=le.exchange_filters()))
    lab = res.trades[res.trades["exit_reason"] != "end_of_data"].reset_index(drop=True)

    cfg = make_cfg(tmp_path, symbols=[sym], shield={"max_consecutive_losses": 10 ** 6},
                   setups=[{"id": "s", "role": "leader", "strategy": name}])
    head = full1.loc[:start - pd.Timedelta(hours=1)]
    clock = {"now": int(index_ms(head.index)[-1]) + HOUR_MS - 5000}
    eng = Engine(cfg, history=History([sym], cfg.state_dir, base_loader=lambda s: head), clock=lambda: clock["now"])
    eng.startup(clock["now"])
    assert eng.strategies["s"].needs[sym] == {}, "this strategy reads data that is not available live"
    BarReplay(eng, "s", sym, full1, clock).run(int(start.timestamp() * 1000), int(end.timestamp() * 1000))
    return eng, cfg, lab, res


@pytest.mark.parametrize("name, sym, start, end, min_trades", CASES, ids=[c[0] for c in CASES])
def test_bot_trades_match_the_backtester(tmp_path, name, sym, start, end, min_trades):
    eng, cfg, lab, res = replay(tmp_path, name, sym, start, end)
    assert len(lab) >= min_trades, f"window too quiet to prove anything ({len(lab)} trades)"
    bot = read_trades(cfg.log_dir / "trades.csv")
    dec = read_decisions(cfg.log_dir / "decisions.jsonl")
    assert eng.shield.halts == [] and not [d for d in dec if d["kind"] in ("warming", "refuse")]
    blocks = [d for d in dec if d["kind"] == "block"]
    # every order Shield blocked must be one the backtester rejected too (minimum size, stop side)
    lab_rejected = sum(res.diagnostics[k] for k in ("rejected_min_size", "rejected_stop_side", "rejected_target_side",
                                                    "rejected_no_stop"))
    assert len(blocks) == lab_rejected, (len(blocks), res.diagnostics, [b["reason"] for b in blocks][:3])

    assert len(bot) == len(lab), f"bot {len(bot)} trades, backtester {len(lab)}"
    tf = TF_MS[eng.strategies["s"].tf]
    step, tick = float(INSTRUMENTS[sym].step), float(INSTRUMENTS[sym].tick)
    worst = {"qty_steps": 0.0, "entry": 0.0, "exit": 0.0, "net": 0.0, "funding": 0.0}
    for b, (_, t) in zip(bot, lab.iterrows()):
        where = f"{b['id']} vs backtester trade entered {t.entry_time}"
        assert int(float(b["side"])) == int(t.side), where
        assert parse_iso(b["signal_bar"]) == int(t.signal_time.timestamp() * 1000), where
        assert parse_iso(b["entry_time"]) // tf * tf == int(t.entry_time.timestamp() * 1000), where
        assert parse_iso(b["exit_time"]) // tf * tf == int(t.exit_time.timestamp() * 1000), where
        assert b["exit_reason"] == REASON[t.exit_reason], where
        assert b["entry_type"] == t.entry_type, where
        # the bot snaps limit and stop to the tick, so its stop distance differs by up to one tick and
        # the size (risk / distance) moves with it; beyond that only the floor to the lot step may differ
        allowed = step + t.qty * tick / abs(t.entry_ref - t.stop)
        assert abs(float(b["qty"]) - t.qty) <= allowed + 1e-9, (where, b["qty"], t.qty, allowed)
        worst["qty_steps"] = max(worst["qty_steps"], abs(float(b["qty"]) - t.qty) / step)
        worst["entry"] = max(worst["entry"], abs(float(b["entry_price"]) / t.entry_price - 1))
        worst["exit"] = max(worst["exit"], abs(float(b["exit_price"]) / t.exit_price - 1))
        worst["net"] = max(worst["net"], abs(float(b["net"]) - t.net))
        worst["funding"] = max(worst["funding"], abs(float(b["funding"]) + t.funding))   # opposite sign convention
    assert worst["entry"] < 2e-4 and worst["exit"] < 2e-4, worst  # within a tick
    assert worst["net"] < 0.15 and worst["funding"] < 0.01, worst
    realised_bot = eng.broker.equity("s") + sum(p.entry_fee - p.funding for p in eng.broker.open_positions())
    realised_lab = 1000.0 + float(lab["net"].sum())
    assert realised_bot == pytest.approx(realised_lab, abs=0.75)
    print(f"\nparity {name} {sym}: {len(bot)} trades, {len(blocks)} blocked/rejected, worst deviations "
          f"{ {k: round(v, 7) for k, v in worst.items()} }, realised equity bot {realised_bot:.4f} vs "
          f"backtester {realised_lab:.4f}")
    if name == "donchian_trend":                                  # the stop-and-reverse rule (R7) was really exercised
        rev = [(a, b) for a, b in zip(bot, bot[1:]) if a["exit_time"] == b["entry_time"] and a["side"] != b["side"]
               and a["exit_reason"] == "signal"]
        assert rev, "no stop-and-reverse in the window"
        assert any("reversal" in d["reason"] for d in dec if d["kind"] == "signal")
