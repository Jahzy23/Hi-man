"""Append-only decision log (JSONL) and trades ledger (CSV)."""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from .models import Trade, to_dict
from .util import clean_float, iso, now_ms, strict

TRADE_FIELDS = ["id", "setup", "symbol", "side", "qty", "entry_time", "entry_price", "entry_type",
                "exit_time", "exit_price", "exit_reason", "gross", "fees", "funding", "net",
                "equity_after", "r_multiple", "signal_bar", "flags"]
PRICE_KEYS = ("entry", "stop", "target", "bid", "ask", "fill", "limit", "mark")


class Journal:
    def __init__(self, log_dir: Path, clock=now_ms):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.decisions_path = self.log_dir / "decisions.jsonl"
        self.trades_path = self.log_dir / "trades.csv"
        self.clock = clock
        self._fh = None
        self.lines = 0                  # decisions written by this process (the engine refreshes status on change)

    def _file(self):
        if self._fh is None or self._fh.closed:
            self._fh = open(self.decisions_path, "a", encoding="utf-8")
        return self._fh

    def decision(self, kind: str, reason: str, setup: str | None = None, symbol: str | None = None,
                 ts: int | None = None, **extra) -> dict:
        """One line per decision. `reason` is always a sentence a human can read on its own."""
        rec = {"ts": iso(self.clock() if ts is None else ts), "kind": kind, "setup": setup, "symbol": symbol}
        prices = {k: clean_float(extra.pop(k)) for k in PRICE_KEYS if k in extra}
        if prices:
            rec["prices"] = prices
        for k, v in extra.items():
            rec[k] = clean_float(v) if isinstance(v, float) else v
        rec["reason"] = reason
        f = self._file()
        f.write(json.dumps(strict(rec), default=str, allow_nan=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
        self.lines += 1
        return rec

    def trade(self, t: Trade | dict) -> None:
        d = to_dict(t) if isinstance(t, Trade) else dict(t)
        row = {"id": d["id"], "setup": d["setup"], "symbol": d["symbol"], "side": d["side"], "qty": d["qty"],
               "entry_time": iso(d["entry_ts"]), "entry_price": d["entry_price"], "entry_type": d["entry_type"],
               "exit_time": iso(d["exit_ts"]), "exit_price": d["exit_price"], "exit_reason": d["exit_reason"],
               "gross": round(d["gross"], 8), "fees": round(d["fees"], 8), "funding": round(d["funding"], 8),
               "net": round(d["net"], 8), "equity_after": round(d["equity_after"], 8),
               "r_multiple": "" if d["r_multiple"] is None else round(d["r_multiple"], 4),
               "signal_bar": iso(d["signal_bar"]), "flags": d.get("flags", "")}
        new = not self.trades_path.exists() or self.trades_path.stat().st_size == 0
        with open(self.trades_path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=TRADE_FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
            f.flush()
            os.fsync(f.fileno())

    def trade_ids(self) -> set:
        if not self.trades_path.exists():
            return set()
        with open(self.trades_path, newline="", encoding="utf-8") as f:
            return {r["id"] for r in csv.DictReader(f) if r.get("id")}

    def close(self):
        if self._fh is not None and not self._fh.closed:
            self._fh.close()


def read_decisions(path: Path, since_ms: int | None = None) -> list[dict]:
    """Tolerant reader: a torn last line (crash mid-write) is skipped, not fatal."""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                out.append(rec)
    except FileNotFoundError:
        return []
    if since_ms is not None:
        cut = iso(since_ms)
        out = [r for r in out if r.get("ts", "") >= cut]
    return out


def read_trades(path: Path) -> list[dict]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return [r for r in csv.DictReader(f) if r.get("id")]
    except FileNotFoundError:
        return []
