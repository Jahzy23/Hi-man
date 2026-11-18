"""Exit rules: stops, gaps, targets, same-bar ordering, fill-bar handling, time and signal exits."""
import pytest

from .helpers import CFG, FLAT, MAKER, SLIP, STOP_SLIP, TAKER, bars, one, run, sigs

approx = pytest.approx
ENTRY_BAR = (100, 101, 99, 100)      # bar 1: market entry at 100, nothing else happens


def test_stop_exit_pays_taker_fee_and_double_slippage():
    df = bars([FLAT, ENTRY_BAR, (100, 101, 94, 96), (96, 97, 95, 96)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95)})))
    qty = 5 / 5
    entry_fill, exit_fill = 100 * (1 + SLIP), 95 * (1 - STOP_SLIP)
    fees = qty * (entry_fill + exit_fill) * TAKER
    slippage = qty * (100 * SLIP + 95 * STOP_SLIP)
    assert t.exit_reason == "stop" and t.exit_time == df.index[2] and t.bars_held == 2
    assert t.exit_ref == 95.0 and t.exit_price == approx(exit_fill)
    assert t.gross == approx(-5.0) and t.slippage == approx(slippage) and t.fees == approx(fees)
    assert t.net == approx(-5.0 - slippage - fees)
    assert t.R == approx((-5.0 - slippage - fees) / 5.0) and t.R < -1


def test_stop_triggers_on_touch():
    df = bars([FLAT, ENTRY_BAR, (100, 101, 95.0, 96), (96, 97, 95.5, 96)])
    assert one(run(df, sigs(df, {0: dict(side=1, stop=95)}))).exit_reason == "stop"


def test_gap_through_stop_fills_at_the_open():
    df = bars([FLAT, ENTRY_BAR, (93, 94, 92, 93), (93, 93, 93, 93)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95)})))
    assert t.exit_reason == "stop_gap" and t.exit_ref == 93.0
    assert t.exit_price == approx(93 * (1 - STOP_SLIP)) and t.gross == approx(-7.0)
    assert t.R < -1.4                                    # worse than the planned -1R


def test_short_stop_and_gap():
    df = bars([FLAT, ENTRY_BAR, (100, 105, 99, 104), (104, 104, 104, 104)])
    t = one(run(df, sigs(df, {0: dict(side=-1, stop=105)})))
    assert t.exit_reason == "stop" and t.exit_price == approx(105 * (1 + STOP_SLIP)) and t.gross == approx(-5.0)
    df = bars([FLAT, ENTRY_BAR, (107, 108, 106, 107), (107, 107, 107, 107)])
    t = one(run(df, sigs(df, {0: dict(side=-1, stop=105)})))
    assert t.exit_reason == "stop_gap" and t.exit_price == approx(107 * (1 + STOP_SLIP))


def test_stop_and_target_in_same_bar_means_stop():
    df = bars([FLAT, ENTRY_BAR, (100, 106, 94, 100), (100, 100, 100, 100)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, target=104)})))
    assert t.exit_reason == "stop" and t.exit_ref == 95.0
    # ...even when the bar OPENS beyond the target (rule R4: always the stop)
    df = bars([FLAT, ENTRY_BAR, (104.5, 106, 94, 100), (100, 100, 100, 100)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, target=104)})))
    assert t.exit_reason == "stop"


def test_target_is_a_resting_maker_limit_that_must_trade_through():
    df = bars([FLAT, ENTRY_BAR, (100, 104.0, 99, 103), (103, 104.5, 102, 104), (104, 104, 104, 104)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, target=104)})))
    assert t.exit_time == df.index[3]                    # bar 2 only touched 104.0
    assert t.exit_reason == "target" and t.exit_price == 104.0 and t.exit_ref == 104.0
    qty, entry_fill = 1.0, 100 * (1 + SLIP)
    fees = qty * entry_fill * TAKER + qty * 104.0 * MAKER          # maker on the target
    assert t.fees == approx(fees) and t.slippage == approx(qty * 100 * SLIP)   # no exit slippage
    assert t.net == approx(4.0 - 100 * SLIP - fees)


def test_short_target_must_trade_through():
    df = bars([FLAT, ENTRY_BAR, (100, 100.5, 96.0, 96.5), (96.5, 97, 95.5, 96.5), (96.5, 96.5, 96.5, 96.5)])
    t = one(run(df, sigs(df, {0: dict(side=-1, stop=105, target=96)})))
    assert t.exit_time == df.index[3] and t.exit_reason == "target" and t.exit_price == 96.0
    assert t.gross == approx(4.0)


def test_bar_opening_beyond_target_fills_at_target_price_not_at_open():
    df = bars([FLAT, ENTRY_BAR, (106, 107, 105, 106), (106, 106, 106, 106)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, target=104)})))
    assert t.exit_reason == "target" and t.exit_price == 104.0


def test_market_entry_can_hit_target_or_stop_inside_the_fill_bar():
    df = bars([FLAT, (100, 102, 99.5, 100.5), (100.5, 100.5, 100.5, 100.5)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, target=101)})))
    assert t.exit_reason == "target" and t.exit_time == df.index[1] and t.bars_held == 1
    df = bars([FLAT, (100, 102, 94, 100.5), (100.5, 100.5, 100.5, 100.5)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, target=101)})))
    assert t.exit_reason == "stop" and t.exit_time == df.index[1]


def test_limit_filled_and_stopped_in_the_same_bar():                # rule R3
    df = bars([FLAT, (100, 100.5, 96.5, 98), (98, 99, 97.5, 98)])
    res = run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=97.0)}))
    t = one(res)
    qty = 5 / 2
    exit_fill = 97 * (1 - STOP_SLIP)
    fees = qty * 99.0 * MAKER + qty * exit_fill * TAKER
    net = qty * (exit_fill - 99.0) - fees
    assert t.entry_time == df.index[1] and t.exit_time == df.index[1] and t.bars_held == 1
    assert t.exit_reason == "stop" and t.gross == approx(-5.0) and t.net == approx(net)
    assert res.equity.tolist() == approx([1000, 1000 + net, 1000 + net])


def test_limit_fill_bar_target_only_counts_if_the_bar_closes_through_it():   # rule R3
    sg = {0: dict(side=1, entry=99.0, stop=95, target=101.0)}
    # high 102 > target but close 100.5: the high may have come BEFORE the fill -> no exit yet
    df = bars([FLAT, (100, 102, 98, 100.5), (100.5, 100.8, 100, 100.6)])
    t = one(run(df, sigs(df, sg)))
    assert t.exit_reason == "end_of_data"
    # close 101.5 > target: it traded through after the fill for certain
    df = bars([FLAT, (100, 102, 98, 101.5), (101.5, 101.8, 101.2, 101.6)])
    t = one(run(df, sigs(df, sg)))
    assert t.exit_reason == "target" and t.exit_time == df.index[1] and t.exit_price == 101.0
    # from the next bar on the normal trade-through rule applies
    df = bars([FLAT, (100, 102, 98, 100.5), (100.5, 101.2, 100, 100.6), (100.6, 100.6, 100.6, 100.6)])
    t = one(run(df, sigs(df, sg)))
    assert t.exit_reason == "target" and t.exit_time == df.index[2]


def test_gap_filled_limit_sees_the_whole_bar_and_a_gap_through_the_stop():   # rules R2 + R3
    # bar 2 opens below the limit: filled at the open instant, so its later high counts for the target
    df = bars([FLAT, (100, 101, 99.5, 100), (98, 99.5, 97.5, 98.2), (98.2, 98.2, 98.2, 98.2)])
    t = one(run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=95, target=99.3, entry_valid=2)})))
    assert t.exit_reason == "target" and t.exit_time == df.index[2]
    # bar 2 opens below limit AND stop: bought at 99, stopped at the open 98
    df = bars([FLAT, (100, 101, 99.5, 100), (98, 98.2, 97.5, 98.1), (98.1, 98.1, 98.1, 98.1)])
    t = one(run(df, sigs(df, {0: dict(side=1, entry=99.0, stop=98.5, entry_valid=2)})))
    assert t.exit_reason == "stop" and t.entry_price == 99.0 and t.exit_ref == 98.0
    assert t.exit_price == approx(98 * (1 - STOP_SLIP))


@pytest.mark.parametrize("max_hold,exit_bar", [(1, 2), (2, 3), (3, 4)])
def test_max_hold_exits_at_next_open_after_n_bars(max_hold, exit_bar):   # rule R6
    df = bars([FLAT, ENTRY_BAR, (101, 102, 100, 101), (102, 103, 101, 102), (103, 104, 102, 103), (104, 104, 104, 104)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=90, max_hold=max_hold)})))
    assert t.exit_reason == "time" and t.exit_time == df.index[exit_bar] and t.bars_held == max_hold
    assert t.exit_ref == df["open"].iloc[exit_bar]
    assert t.exit_price == approx(df["open"].iloc[exit_bar] * (1 - SLIP))


def test_exit_signal_exits_at_next_open():
    df = bars([FLAT, ENTRY_BAR, (101, 102, 100, 101), (102, 103, 101, 102), (103, 103, 103, 103)])
    res = run(df, sigs(df, {0: dict(side=1, stop=90), 2: dict(exit=True)}))
    t = one(res)
    qty = 5 / 10
    assert t.exit_reason == "exit_signal" and t.exit_time == df.index[3] and t.exit_ref == 102.0
    assert t.exit_price == approx(102 * (1 - SLIP))
    assert t.fees == approx(qty * (100 * (1 + SLIP) + 102 * (1 - SLIP)) * TAKER)


def test_exit_flag_while_flat_is_ignored_and_exit_flag_on_fill_bar_counts():
    df = bars([FLAT, ENTRY_BAR, (101, 102, 100, 101), (102, 102, 102, 102)])
    res = run(df, sigs(df, {0: dict(side=1, stop=90, exit=True), 1: dict(exit=True)}))
    t = one(res)                                         # row-0 flag: flat, ignored. row-1 flag: in position
    assert t.entry_time == df.index[1] and t.exit_time == df.index[2] and t.exit_reason == "exit_signal"


def test_scheduled_exit_that_opens_beyond_the_stop_is_booked_as_a_stop():   # rule R5
    df = bars([FLAT, ENTRY_BAR, (94, 95, 93, 94), (94, 94, 94, 94)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, max_hold=1)})))
    assert t.exit_reason == "stop_gap" and t.exit_price == approx(94 * (1 - STOP_SLIP))


def test_scheduled_exit_that_opens_beyond_the_target_fills_the_resting_target():   # rule R5
    df = bars([FLAT, (100, 103, 99, 102), (105, 106, 104, 105), (105, 105, 105, 105)])
    t = one(run(df, sigs(df, {0: dict(side=1, stop=95, target=104, max_hold=1)})))
    assert t.exit_reason == "target" and t.exit_price == 104.0


def test_stop_and_reverse_on_the_same_open():                       # rule R7
    df = bars([FLAT, ENTRY_BAR, (100, 102, 99, 101), (102, 103, 101, 102), (102, 103, 101, 102)])
    res = run(df, sigs(df, {0: dict(side=1, stop=90), 2: dict(side=-1, stop=112, exit=True)}))
    assert len(res.trades) == 2
    a, b = res.trades.iloc[0], res.trades.iloc[1]
    assert a.exit_reason == "exit_signal" and a.exit_time == df.index[3]
    assert b.side == -1 and b.entry_time == df.index[3] and b.entry_ref == 102.0
    assert b.equity_at_entry == approx(1000 + a.net)     # sized on equity after the first trade
    assert b.qty == approx(0.005 * (1000 + a.net) / 10)
    # with the switch off the exit still happens but the signal on that row is dropped
    from dataclasses import replace
    res = run(df, sigs(df, {0: dict(side=1, stop=90), 2: dict(side=-1, stop=112, exit=True)}),
              cfg=replace(CFG, reverse_on_exit_row=False))
    assert len(res.trades) == 1 and res.trades.iloc[0].exit_reason == "exit_signal"
    assert res.diagnostics["dropped_in_position"] == 1


def test_position_open_at_the_end_is_closed_at_last_close_with_costs():   # rule R11
    df = bars([FLAT, ENTRY_BAR, (100, 103, 99, 102)])
    res = run(df, sigs(df, {0: dict(side=1, stop=90)}))
    t = one(res)
    qty = 0.5
    assert t.exit_reason == "end_of_data" and t.exit_time == df.index[2] and t.bars_held == 2
    assert t.exit_ref == 102.0 and t.exit_price == approx(102 * (1 - SLIP))
    net = qty * (102 * (1 - SLIP) - 100 * (1 + SLIP)) - qty * (100 * (1 + SLIP) + 102 * (1 - SLIP)) * TAKER
    assert t.net == approx(net) and res.equity.iloc[-1] == approx(1000 + net)


# ---------------------------------------------------------------- slow-timeframe signals on 1h execution bars
def _four_hour_case(n_hours=24):
    import numpy as np
    import pandas as pd
    from lab.engine import to_exec_frame
    rows = [(100.0, 101.0, 99.0, 100.0)] * n_hours
    df1 = bars(rows)                                                   # starts 2024-01-01 00:00, 1h
    idx4 = pd.date_range("2024-01-01", periods=n_hours // 4, freq="4h", tz="UTC")
    sig4 = pd.DataFrame({"side": 0, "entry": np.nan, "entry_valid": 1, "stop": np.nan, "target": np.nan,
                         "max_hold": np.nan, "exit": False}, index=idx4)
    return df1, sig4, to_exec_frame


def test_four_hour_signal_is_placed_on_the_1h_bar_that_closes_with_it():
    df1, sig4, to_exec_frame = _four_hour_case()
    sig4.iloc[0, sig4.columns.get_indexer(["side", "stop", "max_hold"])] = [1, 90.0, 2]
    sig4.iloc[3, sig4.columns.get_loc("exit")] = True
    sig1, mult = to_exec_frame(sig4, df1)
    assert mult == 4 and sig1.index.equals(df1.index)
    assert sig1["side"].to_numpy().nonzero()[0].tolist() == [3]        # 4h bar 00:00-04:00 -> 1h bar 03:00
    assert sig1["exit"].to_numpy().nonzero()[0].tolist() == [15]       # 4h bar 12:00-16:00 -> 1h bar 15:00
    t = one(run(df1, sig1, hold_mult=mult))
    assert t.entry_time == df1.index[4]                                # first open after the 4h close
    assert t.exit_reason == "time" and t.exit_time == df1.index[12]    # 2 four-hour bars later
    assert t.bars_held == 8


def test_four_hour_limit_filled_mid_bar_still_exits_on_the_four_hour_boundary_and_validity_scales():
    df1, sig4, to_exec_frame = _four_hour_case()
    rows = [(100.0, 101.0, 99.5, 100.0)] * 24
    rows[6] = (100.0, 100.5, 98.5, 100.0)                              # only bar 06:00 trades through 99
    df1 = bars(rows)
    sig4.iloc[0, sig4.columns.get_indexer(["side", "entry", "stop", "max_hold", "entry_valid"])] = [1, 99.0, 90.0, 2, 1]
    sig1, mult = to_exec_frame(sig4, df1)
    assert sig1["entry_valid"].iloc[3] == 4                            # one 4h bar = four 1h bars
    t = one(run(df1, sig1, hold_mult=mult))
    assert t.entry_time == df1.index[6] and t.entry_price == 99.0
    # filled in the 4h bar 04:00-08:00 (bar 1); bar 2 is 08:00-12:00; exit at the 12:00 open
    assert t.exit_reason == "time" and t.exit_time == df1.index[12] and t.bars_held == 6
    rows[6], rows[8] = rows[0], (100.0, 100.5, 98.5, 100.0)            # trade-through only after the validity window
    df1 = bars(rows)
    assert len(run(df1, to_exec_frame(sig4, df1)[0], hold_mult=4).trades) == 0
