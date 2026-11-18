"""Entry rules: next-bar execution, marketable fills, limit trade-through, validity, rejections."""
import numpy as np
import pytest

from lab.engine import ExecConfig
from .helpers import CFG, FLAT, MAKER, SLIP, TAKER, bars, one, run, sigs

approx = pytest.approx


def test_market_entry_fills_at_next_open_with_taker_fee_and_slippage():
    df = bars([FLAT, (102, 103, 101, 102), (102, 104, 101, 103), (105, 106, 104, 105), (105, 105, 105, 105)])
    res = run(df, sigs(df, {0: dict(side=1, stop=95, max_hold=2)}))
    t = one(res)
    qty = 0.005 * 1000 / (102 - 95)                      # 5 / 7
    entry_fill, exit_fill = 102 * (1 + SLIP), 105 * (1 - SLIP)
    fees = qty * entry_fill * TAKER + qty * exit_fill * TAKER
    gross = qty * (105 - 102)
    slippage = qty * (102 * SLIP + 105 * SLIP)
    net = gross - slippage - fees
    assert t.entry_time == df.index[1] and t.exit_time == df.index[3]
    assert t.signal_time == df.index[0]
    assert t.entry_type == "market" and t.exit_reason == "time" and t.bars_held == 2
    assert t.qty == approx(qty)
    assert t.entry_ref == approx(102) and t.entry_price == approx(entry_fill)
    assert t.exit_ref == approx(105) and t.exit_price == approx(exit_fill)
    assert t.gross == approx(gross) and t.fees == approx(fees) and t.slippage == approx(slippage)
    assert t.funding == 0 and t.net == approx(net)
    assert t.risk_usd == approx(5.0) and t.R == approx(net / 5.0)
    assert t.ret == approx(net / 1000)
    fee_in = qty * entry_fill * TAKER
    expected_equity = [1000, 1000 - fee_in + qty * (102 - entry_fill), 1000 - fee_in + qty * (103 - entry_fill),
                       1000 + net, 1000 + net]
    assert res.equity.tolist() == approx(expected_equity)
    assert res.in_position.tolist() == [False, True, True, False, False]


def test_short_market_entry_mirror():
    df = bars([FLAT, (102, 103, 101, 102), (102, 104, 101, 103), (105, 106, 104, 105), (105, 105, 105, 105)])
    t = one(run(df, sigs(df, {0: dict(side=-1, stop=109, max_hold=2)})))
    qty = 5 / 7
    entry_fill, exit_fill = 102 * (1 - SLIP), 105 * (1 + SLIP)      # sell lower, buy back higher
    fees = qty * (entry_fill + exit_fill) * TAKER
    net = qty * (entry_fill - exit_fill) - fees
    assert t.side == -1 and t.entry_price == approx(entry_fill) and t.exit_price == approx(exit_fill)
    assert t.gross == approx(-3 * qty) and t.net == approx(net) and t.net < 0


def test_signal_on_last_row_cannot_trade():
    df = bars([FLAT, FLAT, (100, 120, 100, 120)])
    res = run(df, sigs(df, {2: dict(side=1, stop=90)}))
    assert len(res.trades) == 0 and res.equity.tolist() == [1000, 1000, 1000]


def test_signal_never_fills_on_its_own_bar():
    # the signal bar itself rallies 20%; the entry must be the NEXT open (120), not this bar's open
    df = bars([(100, 120, 100, 120), (120, 121, 119, 120), (120, 120, 120, 120)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=100)})))
    assert t.entry_time == df.index[1] and t.entry_ref == 120.0


def test_limit_buy_needs_trade_through_not_touch():
    rows = [FLAT, (100, 100.5, 99.0, 100.2), (100.2, 100.4, 100, 100.3)]
    df = bars(rows)
    res = run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=95)}))
    assert len(res.trades) == 0                                   # low == limit: touched, not filled
    assert res.diagnostics["limit_expired"] == 1

    rows[1] = (100, 100.5, 98.99, 100.2)                          # one tick through
    df = bars(rows)
    res = run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=95)}))
    t = one(res)
    qty = 5 / (99 - 95)
    assert t.entry_type == "limit" and t.entry_time == df.index[1]
    assert t.entry_price == 99.0 and t.entry_ref == 99.0          # fills at the limit, no slippage
    assert t.qty == approx(qty)
    exit_fill = 100.3 * (1 - SLIP)                                # still open at the end -> closed at last close
    fees = qty * 99.0 * MAKER + qty * exit_fill * TAKER           # MAKER on the limit entry
    assert t.exit_reason == "end_of_data" and t.fees == approx(fees)
    assert t.slippage == approx(qty * 100.3 * SLIP)               # only the exit slipped


def test_limit_sell_needs_trade_through_not_touch():
    rows = [FLAT, (100, 101.0, 99.5, 100.2), (100.2, 100.4, 100, 100.3)]
    df = bars(rows)
    assert len(run(df, sigs(df, {0: dict(side=-1, entry=101.0, stop=105)})).trades) == 0
    rows[1] = (100, 101.01, 99.5, 100.2)
    df = bars(rows)
    t = one(run(df, sigs(df, {0: dict(side=-1, entry=101.0, stop=105)})))
    assert t.side == -1 and t.entry_price == 101.0 and t.qty == approx(5 / 4)


@pytest.mark.parametrize("valid,through_bar,expect_fill", [
    (1, 1, True), (1, 2, False), (2, 2, True), (2, 3, False), (3, 3, True), (3, 4, False)])
def test_entry_valid_expiry(valid, through_bar, expect_fill):
    rows = [FLAT] + [(100, 100.5, 99.5, 100)] * 5
    rows[through_bar] = (100, 100.5, 98.5, 100)
    df = bars(rows)
    res = run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=95, entry_valid=valid)}))
    assert (len(res.trades) == 1) is expect_fill
    if expect_fill:
        assert res.trades.iloc[0].entry_time == df.index[through_bar]
    else:
        assert res.diagnostics["limit_expired"] == 1


def test_limit_through_the_market_when_placed_is_a_taker_fill_at_the_open():   # rule R1
    df = bars([FLAT, (100, 101, 99, 100), (100, 100, 100, 100)])
    t = one(run(df, sigs(df, {0: dict(side=1, entry=105.0, stop=95)})))
    assert t.entry_type == "limit_marketable"
    assert t.entry_ref == 100.0 and t.entry_price == approx(100 * (1 + SLIP))
    assert t.qty == approx(5 / (100 - 95))                        # sized off the open it fills at
    exit_fill = 100 * (1 - SLIP)
    assert t.fees == approx(t.qty * (t.entry_price + exit_fill) * TAKER)   # taker, not maker


def test_limit_equal_to_open_rests_and_fills_as_maker_only_if_traded_through():
    df = bars([FLAT, (100, 101, 99, 100), (100, 100, 100, 100)])
    t = one(run(df, sigs(df, {0: dict(side=1, entry=100.0, stop=95)})))
    assert t.entry_type == "limit" and t.entry_price == 100.0
    df = bars([FLAT, (100, 101, 100, 100.5), (100.5, 100.5, 100.5, 100.5)])   # low == limit: no fill
    assert len(run(df, sigs(df, {0: dict(side=1, entry=100.0, stop=95)})).trades) == 0


def test_resting_limit_gapped_through_on_a_later_bar_fills_at_limit_not_at_open():   # rule R2
    df = bars([FLAT, (100, 101, 99.5, 100), (98, 98.5, 97.5, 98.2), (98.2, 98.4, 98, 98.3)])
    t = one(run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=95, entry_valid=2)})))
    assert t.entry_time == df.index[2] and t.entry_type == "limit"
    assert t.entry_price == 99.0                                  # no price improvement to 98


def test_pending_limit_is_replaced_by_a_newer_signal_or_kept_under_ignore_policy():
    df = bars([FLAT, (100, 101, 99.5, 100), (100, 100.5, 98, 99), (99, 99.5, 98.5, 99)])
    sg = sigs(df, {0: dict(side=1, entry=90.0, stop=85, entry_valid=5),
                   1: dict(side=1, entry=99.0, stop=95, entry_valid=1)})
    from dataclasses import replace
    res = run(df, sg)                                             # default: the resting order stands (as in the bot)
    assert len(res.trades) == 0 and res.diagnostics["limit_ignored"] == 1
    res = run(df, sg, cfg=replace(CFG, pending_policy="replace"))
    t = one(res)
    assert t.entry_price == 99.0 and t.entry_time == df.index[2] and res.diagnostics["limit_replaced"] == 1
    with pytest.raises(ValueError):
        run(df, sg, cfg=replace(CFG, pending_policy="newest"))


def test_market_signal_while_a_limit_rests_is_also_ignored_by_default():
    from dataclasses import replace
    df = bars([FLAT, (100, 101, 99.5, 100), (100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100)])
    sg = sigs(df, {0: dict(side=1, entry=90.0, stop=85, entry_valid=5), 1: dict(side=-1, stop=110)})
    res = run(df, sg)
    assert len(res.trades) == 0 and res.diagnostics["limit_ignored"] == 1
    res = run(df, sg, cfg=replace(CFG, pending_policy="replace"))
    t = one(res)
    assert t.side == -1 and t.entry_type == "market" and t.entry_time == df.index[2]


def test_new_signal_is_taken_once_the_resting_order_has_expired():
    df = bars([FLAT, (100, 101, 99.5, 100), (100, 100.5, 98, 99), (99, 99.5, 98.5, 99)])
    sg = sigs(df, {0: dict(side=1, entry=90.0, stop=85, entry_valid=1),      # live in bar 1 only
                   1: dict(side=1, entry=99.0, stop=95, entry_valid=1)})     # decided at the close of bar 1
    res = run(df, sg)
    t = one(res)
    assert t.entry_price == 99.0 and t.entry_time == df.index[2]
    assert res.diagnostics["limit_expired"] == 1 and res.diagnostics["limit_ignored"] == 0


def test_signals_without_a_valid_stop_or_target_are_rejected():
    df = bars([FLAT, (100, 101, 99, 100), (100, 101, 99, 100)])
    res = run(df, sigs(df, {0: dict(side=1)}))                    # stop is REQUIRED
    assert len(res.trades) == 0 and res.diagnostics["rejected_no_stop"] == 1
    res = run(df, sigs(df, {0: dict(side=1, stop=100.0)}))        # long stop at/above the entry price
    assert len(res.trades) == 0 and res.diagnostics["rejected_stop_side"] == 1
    res = run(df, sigs(df, {0: dict(side=-1, stop=99.0)}))        # short stop below the entry price
    assert len(res.trades) == 0 and res.diagnostics["rejected_stop_side"] == 1
    res = run(df, sigs(df, {0: dict(side=1, stop=95, target=100.0)}))      # target not beyond entry
    assert len(res.trades) == 0 and res.diagnostics["rejected_target_side"] == 1
    res = run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=99.5)}))      # limit order, stop above limit
    assert len(res.trades) == 0 and res.diagnostics["rejected_stop_side"] == 1


def test_one_position_per_setup_and_symbol():
    df = bars([FLAT, (100, 101, 99, 100), (100, 101, 99, 100), (100, 101, 99, 100), (100, 100, 100, 100)])
    res = run(df, sigs(df, {0: dict(side=1, stop=90), 1: dict(side=1, stop=90), 2: dict(side=-1, stop=110)}))
    t = one(res)
    assert t.entry_time == df.index[1] and t.exit_reason == "end_of_data"
    assert res.diagnostics["dropped_in_position"] == 2


def test_signal_dropped_while_in_position_is_not_resurrected_after_the_stop():      # rule R7
    rows = [FLAT, (100, 101, 99, 100), (100, 100.5, 94, 96), (96, 97, 95.5, 96), (96, 96, 96, 96)]
    df = bars(rows)
    res = run(df, sigs(df, {0: dict(side=1, stop=95), 1: dict(side=-1, stop=110)}))
    assert len(res.trades) == 1 and res.trades.iloc[0].exit_reason == "stop"
    # a signal on the close of the stop-out bar is seen flat and IS taken at the next open
    res = run(df, sigs(df, {0: dict(side=1, stop=95), 2: dict(side=-1, stop=110)}))
    assert len(res.trades) == 2
    assert res.trades.iloc[1].side == -1 and res.trades.iloc[1].entry_time == df.index[3]


def test_window_only_trades_inside_start_end_and_uses_warmup_row_as_signal():
    df = bars([FLAT] * 3 + [(100, 101, 99, 100)] * 7)
    sg = sigs(df, {1: dict(side=1, stop=90, max_hold=1), 4: dict(side=1, stop=90, max_hold=1),
                   7: dict(side=1, stop=90, max_hold=1)})
    res = run(df, sg, start=df.index[5], end=df.index[7])
    assert res.equity.index[0] == df.index[5] and res.equity.index[-1] == df.index[7] and len(res.equity) == 3
    t = one(res)                                   # row 1 is before the window, row 7 would fill outside it
    assert t.signal_time == df.index[4] and t.entry_time == df.index[5] and t.exit_time == df.index[6]


def test_extra_delay_moves_execution_one_more_bar():
    df = bars([FLAT, (101, 102, 100, 101), (103, 104, 102, 103), (104, 104, 104, 104)])
    sg = sigs(df, {0: dict(side=1, stop=90)})
    assert one(run(df, sg)).entry_ref == 101.0
    from dataclasses import replace
    assert one(run(df, sg, cfg=replace(CFG, extra_delay=1))).entry_ref == 103.0


def test_default_cost_table_matches_spec():
    cfg = ExecConfig()
    assert (cfg.maker_fee, cfg.taker_fee, cfg.risk_frac, cfg.max_leverage, cfg.start_equity) == \
        (0.0002, 0.0005, 0.005, 3.0, 1000.0)
    assert cfg.slip_for("BTCUSDT") == 0.0001 and cfg.slip_for("ETHUSDT") == 0.0001
    assert cfg.slip_for("SOLUSDT") == 0.0002
    assert cfg.stressed().slip_for("SOLUSDT") == 0.0004 and cfg.stressed().fee_mult == 2.0
    df = bars([FLAT, (100, 101, 99, 100), (100, 101, 94, 96), (96, 96, 96, 96)])
    sg = sigs(df, {0: dict(side=1, stop=95)})
    for sym, s in (("BTCUSDT", 0.0001), ("ETHUSDT", 0.0001), ("SOLUSDT", 0.0002)):
        t = one(run(df, sg, cfg=cfg, symbol=sym))
        assert t.entry_price == approx(100 * (1 + s))
        assert t.exit_price == approx(95 * (1 - 2 * s))            # stop exits pay 2x slippage


def test_missing_optional_columns_get_spec_defaults_and_bad_frames_raise():
    df = bars([FLAT, (100, 101, 99, 100), (100, 100, 100, 100)])
    sg = sigs(df, {0: dict(side=1, stop=95)})[["side", "stop"]]
    assert one(run(df, sg)).entry_type == "market"
    with pytest.raises(ValueError):
        run(df, sg[["side"]])
    with pytest.raises(ValueError):
        run(df, sg.iloc[:-1])


def test_signal_on_the_bar_before_an_unscheduled_gap_stop_stays_dropped():          # rule R7
    rows = [FLAT, (100, 101, 99, 100), (93, 94, 92, 93), (93, 94, 92, 93), (93, 93, 93, 93)]
    df = bars(rows)
    # row 1 closes with the long still open and no exit planned -> its short signal is dropped,
    # even though the long is then stopped at the very next open
    res = run(df, sigs(df, {0: dict(side=1, stop=95), 1: dict(side=-1, stop=110)}))
    t = one(res)
    assert t.exit_reason == "stop_gap" and res.diagnostics["dropped_in_position"] == 1
    # with an exit already scheduled for that open (max_hold=1) the same signal is taken there
    res = run(df, sigs(df, {0: dict(side=1, stop=95, max_hold=1), 1: dict(side=-1, stop=110)}))
    assert len(res.trades) == 2 and res.trades.iloc[0].exit_reason == "stop_gap"
    assert res.trades.iloc[1].side == -1 and res.trades.iloc[1].entry_time == df.index[2]
    assert res.trades.iloc[1].entry_ref == 93.0


def test_exchange_filters_floor_the_size_and_skip_trades_below_the_minimum():      # rule R12 (off by default)
    from dataclasses import replace
    df = bars([FLAT, (100, 101, 99, 100), (100, 101, 99, 100), (100, 100, 100, 100)])
    filt = {"BTCUSDT": (0.3, 0.3, 50.0)}                         # lot step 0.3, min qty 0.3, min notional $50
    cfg = replace(CFG, filters=filt)
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95)}), cfg=cfg))           # raw 1.0 unit -> 0.9
    assert t.qty == approx(0.9) and t.risk_usd == approx(4.5)                  # never rounded up
    res = run(df, sigs(df, {0: dict(side=1, stop=80)}), cfg=cfg)              # raw 0.25 unit -> below one step
    assert len(res.trades) == 0 and res.diagnostics["rejected_min_size"] == 1
    res = run(df, sigs(df, {0: dict(side=1, stop=88)}), cfg=cfg)              # 0.4167 -> 0.3 units = $30 < $50
    assert len(res.trades) == 0 and res.diagnostics["rejected_min_size"] == 1
    res = run(df, sigs(df, {0: dict(side=1, entry=99.5, stop=80)}), cfg=cfg)  # same for a limit order
    assert len(res.trades) == 0 and res.diagnostics["rejected_min_size"] == 1
    assert one(run(df, sigs(df, {0: dict(side=1, stop=80)}))).qty == approx(0.25)        # filters off: SPEC sizing
    assert one(run(df, sigs(df, {0: dict(side=1, stop=95)}), cfg=cfg, symbol="ETHUSDT")).qty == approx(1.0)
