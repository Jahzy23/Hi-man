"""Shared fixtures. Every test runs against tmp dirs: nothing here touches bot/state or the network."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import config as config_mod          # noqa: E402
from bot.models import Approval, Intent       # noqa: E402
from bot.util import HOUR_MS                  # noqa: E402

T0 = 1_794_960_000_000        # 2026-11-18 00:00:00 UTC, a 4h boundary
assert T0 % (4 * HOUR_MS) == 0

RAW = {
    "mode": "paper",
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "account": {"equity": 1000.0, "risk_per_trade": 0.005, "max_leverage": 3.0},
    "fees": {"maker": 0.0002, "taker": 0.0005},
    "slippage": {"stop_multiplier": 2.0, "bps": {"BTCUSDT": 1.0, "ETHUSDT": 1.0, "SOLUSDT": 2.0}},
    "shield": {},
    "engine": {},
    "setup": [{"id": "leader", "role": "leader", "strategy": "_reference_breakout", "params": {}}],
}


def make_cfg(tmp_path, setups=None, **over):
    import copy
    raw = copy.deepcopy(RAW)
    if setups is not None:
        raw["setup"] = setups
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(raw.get(k), dict):
            raw[k].update(v)
        else:
            raw[k] = v
    return config_mod.parse(raw, state_dir=tmp_path / "state", log_dir=tmp_path / "logs", source="test")


@pytest.fixture
def cfg(tmp_path):
    return make_cfg(tmp_path)


class _Issuer:
    """Stands in for Shield where a test is about the broker only."""


ISSUER = _Issuer()


def approve(intent_or_id) -> Approval:
    iid = intent_or_id if isinstance(intent_or_id, str) else intent_or_id.id
    return Approval(iid, True, "test approval", issuer=ISSUER)


def make_intent(setup="leader", symbol="BTCUSDT", side=1, qty=0.008, entry=60000.0, stop=59400.0,
                target=61200.0, max_hold=None, entry_valid=1, tf="1h", signal_bar=T0 - HOUR_MS,
                placed_ts=T0, ref_price=None) -> Intent:
    return Intent(id=f"{setup}:{symbol}:{signal_bar}", setup=setup, symbol=symbol, side=side, qty=qty,
                  entry=entry, ref_price=ref_price if ref_price is not None else (entry or 60000.0),
                  stop=stop, target=target, max_hold=max_hold, entry_valid=entry_valid, tf=tf,
                  signal_bar=signal_bar, placed_ts=placed_ts)


def synth_frame(n=600, start=T0 - 600 * HOUR_MS, seed=1, price=60000.0, vol=0.004) -> pd.DataFrame:
    """A contiguous 1h frame with exactly the columns strategies.data.load returns."""
    rng = np.random.default_rng(seed)
    idx = pd.to_datetime(np.arange(n, dtype="int64") * HOUR_MS + start, unit="ms", utc=True).as_unit("us")
    idx.name = "time"
    r = rng.normal(0, vol, n)
    close = price * np.exp(np.cumsum(r))
    open_ = np.concatenate([[price], close[:-1]])
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2, n)))
    volume = rng.uniform(1000, 5000, n)
    df = pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
        "quote_volume": volume * close, "trades": rng.integers(1000, 9000, n),
        "taker_buy_base": volume * 0.5, "taker_buy_quote": volume * close * 0.5,
        "funding_paid": 0.0, "funding_rate": 0.0001,
        "oi": rng.uniform(8e4, 9e4, n), "oi_value": rng.uniform(5e9, 6e9, n),
        "top_ls_ratio": rng.uniform(1.5, 2.0, n), "acct_ls_ratio": rng.uniform(2.5, 3.5, n),
    }, index=idx)
    df["trades"] = df["trades"].astype("int64")
    hours = (df.index.hour % 8 == 7)            # bars that close on a funding time
    df.loc[hours, "funding_paid"] = 0.0001
    return df
