"""Parity: the bot's bar-close path makes the same decisions as signals() on the full archive frame.

Archived 1h bars of one symbol are replayed one at a time through Engine.on_kline, exactly as live
closed klines arrive. Each decision the engine takes (last row of signals() on the history it has
at that moment) must equal the row signals() gives for that bar when it sees the WHOLE archive,
future bars included. A mismatch would mean look-ahead, a misaligned bar, or a frame that differs
from strategies.data.load. Checked for a 1h setup and for the same strategy on 4h bars.
"""
from __future__ import annotations

import pandas as pd

from bot.engine import Engine
from bot.history import FUNDING_COLS, KLINE_COLS, History, index_ms
from bot.strategy_loader import extract, load_strategy, run_signals
from bot.util import HOUR_MS

from .conftest import make_cfg

SYM = "ETHUSDT"
N = 240          # ten days of hourly closes


def test_replayed_archive_bars_give_the_same_decisions_as_the_full_frame(tmp_path):
    from strategies.data import BACKTEST_END, load

    full1 = load(SYM, "1h").loc[:BACKTEST_END]
    full4 = load(SYM, "4h").loc[:BACKTEST_END]
    assert len(full1) > 30_000
    sdir = tmp_path / "strats"
    sdir.mkdir()
    ref = (load_strategy("_reference_breakout").module.__file__)
    (sdir / "ref1h.py").write_text(open(ref).read())
    (sdir / "ref4h.py").write_text(open(ref).read().replace('TIMEFRAME = "1h"', 'TIMEFRAME = "4h"'))
    cfg = make_cfg(tmp_path, symbols=[SYM], setups=[
        {"id": "h1", "role": "leader", "strategy": "ref1h", "params": {"lookback": 24}},
        {"id": "h4", "strategy": "ref4h", "params": {"lookback": 12, "entry": "limit"}}])
    head = full1.iloc[:-N]
    clock = {"now": int(index_ms(head.index)[-1]) + HOUR_MS + 1000}
    hist = History([SYM], cfg.state_dir, base_loader=lambda s: head)       # real funding archive behind it
    eng = Engine(cfg, history=hist, strategies_dir=sdir, clock=lambda: clock["now"])
    seen = {"h1": [], "h4": []}
    eng.on_decision = lambda sid, sym, d: seen[sid].append(d)
    eng.startup(clock["now"])
    assert eng.strategies["h1"].needs[SYM] == {} and eng.strategies["h4"].tf == "4h"

    tail = full1.iloc[-N:]
    for t, row in zip(index_ms(tail.index), tail.itertuples()):
        t = int(t)
        close_ms = t + HOUR_MS
        clock["now"] = close_ms - 150
        eng.on_book(SYM, row.close, round(row.close + 0.01, 2), close_ms - 200, clock["now"])   # a quote to trade on
        eng.on_mark(SYM, row.close, 0.0001, close_ms - close_ms % (8 * HOUR_MS) + 8 * HOUR_MS, close_ms - 400,
                    clock["now"])                                                               # the 1 s markPrice
        clock["now"] = close_ms + 300
        k = {"t": t, "T": close_ms - 1, "i": "1h", "o": repr(row.open), "h": repr(row.high), "l": repr(row.low),
             "c": repr(row.close), "v": repr(row.volume), "q": repr(row.quote_volume), "n": int(row.trades),
             "V": repr(row.taker_buy_base), "Q": repr(row.taker_buy_quote), "x": True}
        eng.on_kline(SYM, k, close_ms + 20, clock["now"])
        eng.on_timer(clock["now"])

    # the frames the bot built are the loader's frames (prices and funding; OI is not available live)
    cols = KLINE_COLS + FUNDING_COLS
    pd.testing.assert_frame_equal(eng.history.frame(SYM, "1h")[cols], full1[cols], check_freq=False)
    pd.testing.assert_frame_equal(eng.history.frame(SYM, "4h")[cols], full4[cols], check_freq=False)

    # 1h: one decision per replayed bar, equal to the full-frame row
    s1 = eng.strategies["h1"]
    want1 = run_signals(s1, full1, {SYM: full1})
    assert len(seen["h1"]) == N
    for i, got in enumerate(seen["h1"]):
        assert got == extract(s1, want1, len(full1) - N + i), f"1h bar {tail.index[i]}"
    assert sum(d.side != 0 for d in seen["h1"]) >= 5 and any(d.exit for d in seen["h1"])     # not a vacuous match

    # 4h: decisions only at 00/04/08/12/16/20 UTC closes, on the 4h bar that just closed
    s4 = eng.strategies["h4"]
    want4 = run_signals(s4, full4, {SYM: full4})
    assert len(seen["h4"]) == N // 4
    pos = {int(ms): i for i, ms in enumerate(index_ms(full4.index))}
    for got in seen["h4"]:
        assert got.bar_ms % (4 * HOUR_MS) == 0
        assert got == extract(s4, want4, pos[got.bar_ms]), f"4h bar {got.bar_ms}"
    assert sum(d.side != 0 for d in seen["h4"]) >= 2
    assert [d.bar_ms for d in seen["h4"]] == [int(x) for x in index_ms(full4.index[-N // 4:])]

    # and the replay really went through the whole chain: orders, Shield, ledger
    kinds = {d["kind"] for d in __import__("bot.journal", fromlist=["x"]).read_decisions(cfg.log_dir / "decisions.jsonl")}
    assert {"signal", "eval", "bar"} <= kinds and ("fill" in kinds or "order" in kinds)
    assert eng.broker.last_decision("h1", SYM) == int(index_ms(tail.index)[-1])
