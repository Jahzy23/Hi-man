"""Replay archived bars through the live path (Engine + Shield + PaperBroker). A test/diagnostic tool.

The live broker works on quotes; the backtester (lab/engine.py) works on bars. To compare the two,
each bar of the strategy's timeframe is turned into the few quotes that matter, in the order the
backtester's rules assume:

    open instant     quote (O, O), so a marketable order fills at O +/- slippage and a gap through a
                     stop or a resting limit is seen at the open (R1, R2, R4, R5);
    then the bar     the decision for the bar that just closed (closed 1h kline -> Engine);
    inside the bar   position open: a quote AT the stop if the bar's range reaches it (stop first, R4),
                     otherwise a quote at the target if the bar trades through it;
                     entry limit resting: a quote at the limit if the bar trades through it, then the
                     stop for the rest of the bar, the target only if the bar closes through it (R3);
    bar close        funding settles through the markPrice path (rate = the bar's funding_paid).

Quotes one tick wide are used at the extremes so that "trades through" (strict) and "touches"
behave as in the backtester. Nothing here is used by the running bot.
"""
from __future__ import annotations

import pandas as pd

from .engine import Engine
from .history import index_ms
from .instruments import INSTRUMENTS
from .util import HOUR_MS, TF_MS


class BarReplay:
    def __init__(self, engine: Engine, setup_id: str, symbol: str, frame_1h: pd.DataFrame, clock: dict):
        self.eng = engine
        self.sid = setup_id
        self.sym = symbol
        self.tf = engine.strategies[setup_id].tf
        self.tf_ms = TF_MS[self.tf]
        self.df = frame_1h
        self.ms = index_ms(frame_1h.index)
        self.pos_of = {int(m): i for i, m in enumerate(self.ms)}
        self.tick = float(INSTRUMENTS[symbol].tick)
        self.clock = clock
        self.t = 0

    # ------------------------------------------------------------------ plumbing
    def _now(self, ms: int) -> int:
        self.clock["now"] = max(self.clock["now"], ms)
        return self.clock["now"]

    def _quote(self, bid: float, ask: float, ms: int) -> None:
        self.t = max(self.t + 1, ms)
        now = self._now(self.t)
        self.eng.on_book(self.sym, round(bid, 8), round(ask, 8), self.t, now)

    def _kline(self, i: int, lag: int = 300) -> None:
        r = self.df.iloc[i]
        t = int(self.ms[i])
        k = {"t": t, "T": t + HOUR_MS - 1, "i": "1h", "o": repr(float(r.open)), "h": repr(float(r.high)),
             "l": repr(float(r.low)), "c": repr(float(r.close)), "v": repr(float(r.volume)),
             "q": repr(float(r.quote_volume)), "n": int(r.trades), "V": repr(float(r.taker_buy_base)),
             "Q": repr(float(r.taker_buy_quote)), "x": True}
        now = self._now(t + HOUR_MS + lag)
        self.eng.on_kline(self.sym, k, t + HOUR_MS + 20, now)

    def _mark(self, price: float, rate: float, next_ms: int, ms: int) -> None:
        self.eng.on_mark(self.sym, price, rate, next_ms, ms, self._now(ms))

    def _book(self):
        return self.eng.broker.book(self.sid)

    # ------------------------------------------------------------------ one bar of the strategy timeframe
    def _bar(self, i0: int, decide: bool) -> None:
        n = self.tf_ms // HOUR_MS
        rows = self.df.iloc[i0:i0 + n]
        t0 = int(self.ms[i0])
        o, h, l, c = float(rows.open.iloc[0]), float(rows.high.max()), float(rows.low.min()), float(rows.close.iloc[-1])
        fp = float(rows.funding_paid.fillna(0.0).sum())
        tk = self.tick
        # open instant: settle the previous bar's funding (mark path), then the opening quote
        self._quote(o, o, t0)
        if decide:
            self._kline(i0 - 1)                      # the closed 1h bar that ends at t0 -> decisions at this open
        # what the market "expects" for this bar's close: drives settlement and the R10 exit-bar charge
        close_ms = t0 + self.tf_ms
        self._mark(o, fp, close_ms if fp != 0.0 else close_ms + 1000 * self.tf_ms, t0 + 2)
        book = self._book()
        filled_intrabar = False
        order = book.orders.get(self.sym)
        if order is not None:                        # R3: resting limit, not filled at the open
            if order.side > 0 and l < order.limit:
                self._quote(order.limit - tk, order.limit, t0 + 1000)
                filled_intrabar = True
            elif order.side < 0 and h > order.limit:
                self._quote(order.limit, order.limit + tk, t0 + 1000)
                filled_intrabar = True
        p = book.positions.get(self.sym)
        if p is not None:
            if p.side > 0:
                if l <= p.stop:
                    self._quote(p.stop, p.stop + tk, t0 + 2000)
                elif p.target is not None and ((c > p.target) if filled_intrabar else (h > p.target)):
                    self._quote(p.target, p.target + tk, t0 + 3000)
            else:
                if h >= p.stop:
                    self._quote(p.stop - tk, p.stop, t0 + 2000)
                elif p.target is not None and ((c < p.target) if filled_intrabar else (l < p.target)):
                    self._quote(p.target - tk, p.target, t0 + 3000)
        for k in range(n - 1):                       # the interior 1h closes only feed the history
            self._kline(i0 + k)
        self._mark(c, fp, close_ms if fp != 0.0 else close_ms + 1000 * self.tf_ms, close_ms - 500)

    def run(self, first_ms: int, last_ms: int) -> None:
        """Replay strategy-timeframe bars whose open time lies in [first_ms, last_ms]. The bar before
        first_ms must already be in the engine's history; its close is the first decision."""
        if first_ms % self.tf_ms or last_ms % self.tf_ms:
            raise ValueError("replay bounds must be bar open times of the strategy timeframe")
        self.t = first_ms - 1
        t = first_ms
        while t <= last_ms:
            self._bar(self.pos_of[t], decide=True)
            t += self.tf_ms
