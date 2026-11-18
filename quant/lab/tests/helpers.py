"""Tiny hand-built price paths for the engine tests."""
import numpy as np
import pandas as pd

from lab.engine import ExecConfig, run_backtest

# Round numbers for hand arithmetic: slippage 10 bp (stops 20 bp), taker 5 bp, maker 2 bp,
# $1,000 start, 0.5% risk, 3x cap.
CFG = ExecConfig(slippage=0.001)
SLIP, STOP_SLIP, TAKER, MAKER = 0.001, 0.002, 0.0005, 0.0002
FLAT = (100.0, 100.0, 100.0, 100.0)


def bars(rows, funding=None, start="2024-01-01", freq="1h"):
    """rows = [(open, high, low, close), ...] -> frame shaped like strategies.data.load()."""
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC", name="time")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["funding_paid"] = 0.0 if funding is None else np.asarray(funding, dtype=float)
    return df


def sigs(df, rows=None):
    """Blank signal frame; rows = {row_number: {column: value}}."""
    s = pd.DataFrame({"side": 0, "entry": np.nan, "entry_valid": 1, "stop": np.nan, "target": np.nan,
                      "max_hold": np.nan, "exit": False}, index=df.index)
    for i, kv in (rows or {}).items():
        for k, v in kv.items():
            s.iloc[i, s.columns.get_loc(k)] = v
    return s


def run(df, sg, cfg=CFG, symbol="BTCUSDT", **kw):
    return run_backtest(df, sg, symbol, cfg=cfg, **kw)


def one(res):
    assert len(res.trades) == 1, f"expected exactly one trade, got {len(res.trades)}"
    return res.trades.iloc[0]
