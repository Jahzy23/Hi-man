"""A whole bot on synthetic history with explicit time. No network, no real state."""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
import pandas as pd

from bot.engine import Engine
from bot.history import History
from bot.journal import read_decisions
from bot.util import HOUR_MS

from .conftest import T0, make_cfg, synth_frame

SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

STRATEGIES = {
    # always signals; everything about the order is a parameter
    "always": '''
import numpy as np, pandas as pd
NAME = "always"; TIMEFRAME = "1h"; REGIME = "trending"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {"side": 1, "limit_pct": 0.0, "stop_pct": 0.01, "target_pct": 0.02, "max_hold": 0,
                  "entry_valid": 1, "exit_hour": -1, "only_hour": -1}
WARMUP_BARS = 50
def signals(df, p, ctx=None):
    s = int(p["side"]); c = df["close"]
    out = pd.DataFrame(index=df.index)
    out["side"] = s
    if p["only_hour"] >= 0:
        out["side"] = np.where(df.index.hour == p["only_hour"], s, 0)
    out["entry"] = c * (1 - s * p["limit_pct"]) if p["limit_pct"] else np.nan
    ref = c * (1 - s * p["limit_pct"])
    out["entry_valid"] = int(p["entry_valid"])
    out["stop"] = ref * (1 - s * p["stop_pct"]) if p["stop_pct"] else np.nan
    out["target"] = ref * (1 + s * p["target_pct"]) if p["target_pct"] else np.nan
    out["max_hold"] = float(p["max_hold"]) if p["max_hold"] else np.nan
    out["exit"] = df.index.hour == p["exit_hour"]
    return out
''',
    # signals by hour of day: lets a test put an exit flag and a new signal on chosen rows
    "script": '''
import numpy as np, pandas as pd
NAME = "script"; TIMEFRAME = "1h"; REGIME = "trending"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {"long_hours": [], "short_hours": [], "exit_hours": [], "max_hold": 0, "stop_pct": 0.01,
                  "limit_pct": 0.0, "entry_valid": 1, "target_pct": 0.0}
WARMUP_BARS = 50
def signals(df, p, ctx=None):
    h = df.index.hour; c = df["close"]
    s = np.where(np.isin(h, p["long_hours"]), 1, np.where(np.isin(h, p["short_hours"]), -1, 0))
    out = pd.DataFrame(index=df.index)
    out["side"] = s
    ref = c * (1 - s * p["limit_pct"])
    out["entry"] = np.where(s != 0, ref, np.nan) if p["limit_pct"] else np.nan
    out["entry_valid"] = int(p["entry_valid"])
    out["stop"] = np.where(s != 0, ref * (1 - s * p["stop_pct"]), np.nan)
    out["target"] = np.where(s != 0, ref * (1 + s * p["target_pct"]), np.nan) if p["target_pct"] else np.nan
    out["max_hold"] = float(p["max_hold"]) if p["max_hold"] else np.nan
    out["exit"] = np.isin(h, p["exit_hours"])
    return out
''',
    "always4h": '''
import numpy as np, pandas as pd
NAME = "always4h"; TIMEFRAME = "4h"; REGIME = "trending"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {}
WARMUP_BARS = 20
def signals(df, p, ctx=None):
    out = pd.DataFrame(index=df.index)
    out["side"] = 1; out["entry"] = np.nan; out["entry_valid"] = 1
    out["stop"] = df["close"] * 0.99; out["target"] = np.nan; out["max_hold"] = 2.0; out["exit"] = False
    return out
''',
    "needs_oi": '''
import numpy as np, pandas as pd
NAME = "needs_oi"; TIMEFRAME = "1h"; REGIME = "volatile"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {}
WARMUP_BARS = 50
def signals(df, p, ctx=None):
    out = pd.DataFrame(index=df.index)
    out["side"] = np.where(df["oi"].pct_change(3) > 0, 1, 0)
    out["entry"] = np.nan; out["entry_valid"] = 1; out["stop"] = df["close"] * 0.99
    out["target"] = np.nan; out["max_hold"] = np.nan; out["exit"] = False
    return out
''',
    "needs_funding": '''
import numpy as np, pandas as pd
NAME = "needs_funding"; TIMEFRAME = "1h"; REGIME = "ranging"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {}
WARMUP_BARS = 5
def signals(df, p, ctx=None):
    out = pd.DataFrame(index=df.index)
    out["side"] = np.where(df["funding_rate"] > 0.0, -1, 0)
    out["entry"] = np.nan; out["entry_valid"] = 1; out["stop"] = df["close"] * 1.01
    out["target"] = np.nan; out["max_hold"] = np.nan; out["exit"] = False
    return out
''',
    "declares_oi": '''
import numpy as np, pandas as pd
NAME = "declares_oi"; TIMEFRAME = "1h"; REGIME = "volatile"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {}
REQUIRED_COLUMNS = ["oi_value"]
def signals(df, p, ctx=None):
    out = pd.DataFrame(index=df.index)
    out["side"] = 0; out["stop"] = np.nan
    return out
''',
    "uses_btc": '''
import numpy as np, pandas as pd
NAME = "uses_btc"; TIMEFRAME = "1h"; REGIME = "trending"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {}
WARMUP_BARS = 50
def signals(df, p, ctx=None):
    btc = ctx["BTCUSDT"]["close"].reindex(df.index)
    out = pd.DataFrame(index=df.index)
    out["side"] = np.where(btc.pct_change() > 0, 1, -1)
    out["entry"] = np.nan; out["entry_valid"] = 1
    out["stop"] = df["close"] * np.where(btc.pct_change() > 0, 0.99, 1.01)
    out["target"] = np.nan; out["max_hold"] = np.nan; out["exit"] = False
    return out
''',
    "boom": '''
import numpy as np, pandas as pd
NAME = "boom"; TIMEFRAME = "1h"; REGIME = "trending"; ONE_LINER = "test fixture"
DEFAULT_PARAMS = {}
WARMUP_BARS = 50
def signals(df, p, ctx=None):
    if len(df) > 600:
        raise ZeroDivisionError("strategy bug")
    out = pd.DataFrame(index=df.index)
    out["side"] = 0; out["stop"] = np.nan
    return out
''',
}


class Bot:
    def __init__(self, tmp_path, setups=None, now=T0 + 30 * 60_000, n=600, **cfg_over):
        self.tmp = Path(tmp_path)
        self.setups = setups or [{"id": "leader", "role": "leader", "strategy": "always"}]
        self.cfg_over = cfg_over
        self.cfg = make_cfg(self.tmp, setups=self.setups, **cfg_over)
        self.sdir = self.tmp / "strats"
        self.sdir.mkdir(parents=True, exist_ok=True)
        for name, code in STRATEGIES.items():
            (self.sdir / f"{name}.py").write_text(code)
        self.frames = {s: synth_frame(n=n, start=T0 - n * HOUR_MS, seed=i + 1) for i, s in enumerate(SYMS)}
        idx = pd.date_range(end=pd.Timestamp("2026-10-31 16:00", tz="UTC"), periods=300, freq="8h")
        self.funding_archive = pd.Series(0.0001, index=idx, name="funding_rate")
        self.now = now
        self.engine: Engine | None = None

    # -------------------------------------------------- lifecycle
    def start(self, at=None, setups=None):
        if at is not None:
            self.now = at
        if setups is not None:
            self.setups = setups
            self.cfg = make_cfg(self.tmp, setups=setups, **self.cfg_over)
        hist = History(self.cfg.symbols, self.cfg.state_dir, base_loader=lambda s: self.frames[s],
                       funding_archive=lambda s: self.funding_archive)
        self.engine = Engine(self.cfg, history=hist, strategies_dir=self.sdir, clock=lambda: self.now)
        self.engine.startup(self.now)
        return self

    def kill(self):
        """Simulate kill -9: the object is dropped, nothing is flushed or closed properly."""
        self.engine = None

    restart = start

    # -------------------------------------------------- feed
    def tick(self, sym, bid, ask=None, at=None):
        if at is not None:
            self.now = at
        tick = {"BTCUSDT": 0.1, "ETHUSDT": 0.01, "SOLUSDT": 0.01}[sym]
        self.engine.on_book(sym, float(bid), float(ask if ask is not None else round(bid + tick, 2)), self.now - 80, self.now)

    def ticks(self, at=None, **prices):
        """Fresh quotes for every symbol (default 60000 / 1800 / 75), plus the once-a-second markPrice
        message of the kline connection, repeating whatever funding rate / time the engine last saw."""
        if at is not None:
            self.now = at
        for sym, px in {"BTCUSDT": 60000.0, "ETHUSDT": 1800.0, "SOLUSDT": 75.0, **prices}.items():
            self.tick(sym, px)
        for sym in self.cfg.symbols:
            self.keepalive_mark(sym)

    def keepalive_mark(self, sym):
        f = self.engine.funding[sym]
        e_ms = self.now - 100
        if f.last_r_e_ms is not None and f.last_r_e_ms >= e_ms:
            return
        nxt = f.next_ms if f.next_ms is not None and f.next_ms > e_ms else e_ms - e_ms % (8 * HOUR_MS) + 8 * HOUR_MS
        self.engine.on_mark(sym, f.last_mark or 1.0, f.last_r if f.last_r is not None else 0.0001, nxt, e_ms, self.now)

    def close_bar(self, bar_ms, syms=None, at=None, lag=300, **closes):
        """Closed 1h klines for `syms`. Bar closes at bar_ms + 1h; the events arrive `lag` ms later."""
        self.now = at if at is not None else bar_ms + HOUR_MS + lag
        for sym in (syms or self.cfg.symbols):
            c = closes.get(sym, {"BTCUSDT": 60000.0, "ETHUSDT": 1800.0, "SOLUSDT": 75.0}[sym])
            self.engine.on_kline(sym, self.kline(bar_ms, c, closed=True), bar_ms + HOUR_MS + 20, self.now)

    @staticmethod
    def kline(bar_ms, close, high=None, low=None, closed=False):
        return {"t": bar_ms, "T": bar_ms + HOUR_MS - 1, "i": "1h", "o": str(close), "c": str(close),
                "h": str(high if high is not None else close * 1.001), "l": str(low if low is not None else close * 0.999),
                "v": "100.0", "n": 1234, "x": closed, "q": str(100.0 * close), "V": "50.0", "Q": str(50.0 * close)}

    def mark(self, sym, rate, next_ms, mark=None, at=None):
        if at is not None:
            self.now = at
        px = mark or {"BTCUSDT": 60000.0, "ETHUSDT": 1800.0, "SOLUSDT": 75.0}[sym]
        self.engine.on_mark(sym, px, rate, next_ms, self.now - 100, self.now)

    def timer(self, at=None):
        if at is not None:
            self.now = at
        self.engine.on_timer(self.now)

    # -------------------------------------------------- inspection
    def decisions(self, *kinds, setup=None, symbol=None):
        d = read_decisions(self.cfg.log_dir / "decisions.jsonl")
        return [x for x in d if (not kinds or x["kind"] in kinds) and (setup is None or x.get("setup") == setup)
                and (symbol is None or x.get("symbol") == symbol)]

    def trades(self):
        p = self.cfg.log_dir / "trades.csv"
        if not p.exists():
            return []
        with open(p, newline="") as f:
            return list(csv.DictReader(f))

    def pos(self, sym="BTCUSDT", setup="leader"):
        return self.engine.broker.book(setup).positions.get(sym)

    def order(self, sym="BTCUSDT", setup="leader"):
        return self.engine.broker.book(setup).orders.get(sym)

    def halts(self):
        return [h["kind"] for h in self.engine.shield.halts]
