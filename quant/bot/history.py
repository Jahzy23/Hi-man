"""History for the strategies: archive + archive refresh + the bot's own live bars, one frame per symbol.

`strategies.data.load(sym, "1h")` is the contract. This module returns frames with the same index
convention (bar OPEN time, UTC) and the same 15 columns, built from three layers:

    base     strategies.data.load(sym, "1h")                      immutable research archive
    refresh  state/archive/<sym>_1h.csv, <sym>_funding.csv        newer daily/monthly archive files
    own      state/bars/<sym>_1h.csv                              closed bars this bot saw live

What is known live, and what is not (cells that are not known are NaN and flagged in `unknown`):
    open..taker_buy_quote   kline stream: complete.
    funding_rate / _paid    markPrice stream (r, T), only for settlements the bot watched happen.
                            The funding ARCHIVE is monthly and appears after month end, so between
                            the last archived month and the first settlement the bot observes the
                            real values do not exist anywhere we can reach: NaN/unknown, not the
                            stale forward-fill load() would show.
    oi, oi_value, top_ls_ratio, acct_ls_ratio
                            no websocket stream carries them (checked 2026-11-18); the archive is
                            D+1. Always NaN/unknown on bars newer than the base archive.
The engine refuses a strategy that reads a column with unknown cells in its warm-up window.
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

import numpy as np
import pandas as pd

from .util import HOUR_MS, TF_MS, iso

KLINE_COLS = ["open", "high", "low", "close", "volume", "quote_volume", "trades", "taker_buy_base",
              "taker_buy_quote"]
FUNDING_COLS = ["funding_paid", "funding_rate"]
OI_COLS = ["oi", "oi_value", "top_ls_ratio", "acct_ls_ratio"]
COLUMNS = KLINE_COLS + FUNDING_COLS + OI_COLS
MAYBE_UNKNOWN = FUNDING_COLS + OI_COLS
AGG_4H = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum",
          "quote_volume": "sum", "trades": "sum", "taker_buy_base": "sum", "taker_buy_quote": "sum",
          "funding_paid": "sum", "funding_rate": "last", "oi": "last", "oi_value": "last",
          "top_ls_ratio": "last", "acct_ls_ratio": "last"}          # identical to strategies/data.py
BAR_FIELDS = ["time", "time_ms"] + KLINE_COLS + FUNDING_COLS + ["recv_ms"]


def to_index(ms) -> pd.DatetimeIndex:
    idx = pd.to_datetime(np.asarray(ms, dtype="int64"), unit="ms", utc=True).as_unit("us")
    idx.name = "time"
    return idx


def index_ms(idx: pd.DatetimeIndex) -> np.ndarray:
    return idx.as_unit("ms").asi8


def default_base_loader(sym: str) -> pd.DataFrame:
    from strategies.data import load
    return load(sym, "1h")


def default_funding_archive(sym: str) -> pd.Series:
    """The archived funding settlements behind load(): time -> rate. Read-only."""
    from strategies.data import DATA
    f = pd.read_csv(DATA / f"{sym}_funding.csv.gz", parse_dates=["time"]).set_index("time")
    return f["funding_rate"]


def month_after(ts: pd.Timestamp) -> pd.Timestamp:
    """Monthly funding files are complete months: coverage runs to the first instant of the next month."""
    y, m = (ts.year + 1, 1) if ts.month == 12 else (ts.year, ts.month + 1)
    return pd.Timestamp(year=y, month=m, day=1, tz="UTC")


class BarStore:
    """Append-only CSV of closed 1h bars per symbol. One fsync'd line per bar; a torn last line
    (killed mid-write) is dropped on read."""

    def __init__(self, state_dir: Path):
        self.dir = Path(state_dir) / "bars"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, sym: str) -> Path:
        return self.dir / f"{sym}_1h.csv"

    def read(self, sym: str) -> list[dict]:
        p = self.path(sym)
        if not p.exists():
            return []
        rows, torn = [], False
        with open(p, newline="", encoding="utf-8") as f:
            for i, rec in enumerate(csv.reader(f)):
                if i == 0 and rec and rec[0] == "time":
                    continue
                if len(rec) != len(BAR_FIELDS):
                    torn = True
                    continue
                try:
                    row = {"time_ms": int(rec[1])}
                    for k, v in zip(BAR_FIELDS[2:], rec[2:]):
                        row[k] = float(v) if v != "" else float("nan")
                except ValueError:
                    torn = True
                    continue
                rows.append(row)
        if torn:
            self._rewrite(sym, rows)
        out, last = [], -1
        for r in sorted(rows, key=lambda r: r["time_ms"]):
            if r["time_ms"] > last and r["time_ms"] % HOUR_MS == 0:
                out.append(r)
                last = r["time_ms"]
        return out

    def _line(self, row: dict) -> list:
        vals = [iso(row["time_ms"]), int(row["time_ms"])]
        for k in BAR_FIELDS[2:]:
            v = row.get(k)
            vals.append("" if v is None or (isinstance(v, float) and v != v) else repr(float(v)))
        return vals

    def _rewrite(self, sym: str, rows: list[dict]) -> None:
        tmp = self.path(sym).with_suffix(".tmp")
        with open(tmp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(BAR_FIELDS)
            for r in rows:
                w.writerow(self._line(r))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path(sym))

    def append(self, sym: str, row: dict) -> None:
        p = self.path(sym)
        new = not p.exists() or p.stat().st_size == 0
        with open(p, "a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(BAR_FIELDS)
            w.writerow(self._line(row))
            f.flush()
            os.fsync(f.fileno())


def read_archive_klines(state_dir: Path, sym: str) -> pd.DataFrame:
    p = Path(state_dir) / "archive" / f"{sym}_1h.csv"
    if not p.exists():
        return pd.DataFrame(columns=KLINE_COLS, index=to_index([]))
    d = pd.read_csv(p)
    out = d[KLINE_COLS].copy()
    out.index = to_index(d["time_ms"].to_numpy())
    return out[~out.index.duplicated(keep="last")].sort_index()


def read_archive_funding(state_dir: Path, sym: str) -> pd.Series:
    p = Path(state_dir) / "archive" / f"{sym}_funding.csv"
    if not p.exists():
        return pd.Series(dtype="float64", index=to_index([]), name="funding_rate")
    d = pd.read_csv(p)
    s = pd.Series(d["funding_rate"].to_numpy(dtype="float64"), index=to_index(d["time_ms"].to_numpy()),
                  name="funding_rate")
    return s[~s.index.duplicated(keep="last")].sort_index()


class History:
    def __init__(self, symbols, state_dir: Path, base_loader=None, funding_archive=None):
        self.symbols = list(symbols)
        self.state_dir = Path(state_dir)
        self.store = BarStore(self.state_dir)
        base_loader = base_loader or default_base_loader
        funding_archive = funding_archive or default_funding_archive
        self.base: dict[str, pd.DataFrame] = {}
        self.base_funding: dict[str, pd.Series] = {}
        self._static: dict = {}
        self._fund: dict = {}
        self._own_rows: dict[str, list] = {}
        self._cache: dict = {}
        self.mismatches: dict[str, list] = {s: [] for s in self.symbols}
        for s in self.symbols:
            b = base_loader(s)
            if list(b.columns) != COLUMNS:
                raise RuntimeError(f"{s}: loader columns changed: {list(b.columns)}")
            b = b.copy()
            b.index = to_index(index_ms(b.index))
            if len(b) and (np.diff(index_ms(b.index)) <= 0).any():
                raise RuntimeError(f"{s}: base archive index is not strictly increasing")
            self.base[s] = b
            f = funding_archive(s).astype("float64")
            f.index = to_index(index_ms(pd.DatetimeIndex(f.index)))
            self.base_funding[s] = f.sort_index()
            self.reload(s)

    # ------------------------------------------------------------------ building
    def reload(self, sym: str) -> None:
        """Re-read the archive refresh files and the own-bar CSV from disk and rebuild the frames."""
        base = self.base[sym]
        ext = read_archive_klines(self.state_dir, sym)
        ext = ext[~ext.index.isin(base.index)]
        k = pd.concat([base[KLINE_COLS], ext]) if len(ext) else base[KLINE_COLS].copy()
        k = k.sort_index()
        k["trades"] = k["trades"].astype("int64")
        close_t = k.index + pd.Timedelta(hours=1)
        fund = self.base_funding[sym]
        extf = read_archive_funding(self.state_dir, sym)
        if len(extf):
            fund = pd.concat([fund, extf[~extf.index.isin(fund.index)]]).sort_index()
        cov_end = month_after(fund.index[-1]) if len(fund) else pd.Timestamp("1970-01-01", tz="UTC")
        known_f = np.asarray(close_t < cov_end)
        paid = fund.reindex(close_t).fillna(0.0).to_numpy()
        rate = fund.reindex(fund.index.union(close_t)).ffill().reindex(close_t).to_numpy()
        static = k.copy()
        static["funding_paid"] = np.where(known_f, paid, np.nan)
        static["funding_rate"] = np.where(known_f, rate, np.nan)
        in_base = np.asarray(static.index.isin(base.index))
        for c in OI_COLS:
            col = np.full(len(static), np.nan)
            col[in_base] = base[c].to_numpy()
            static[c] = col
        unknown = pd.DataFrame(False, index=static.index, columns=MAYBE_UNKNOWN)
        for c in FUNDING_COLS:
            unknown[c] = ~known_f
        for c in OI_COLS:
            unknown[c] = ~in_base
        self._static[sym] = (static[COLUMNS], unknown, cov_end)
        self._fund[sym] = fund
        rows = self.store.read(sym)
        # integrity: a bar we recorded live must equal the archive's version of it once that exists
        st_ms = set(index_ms(static.index).tolist())
        self.mismatches[sym] = []
        for r in rows:
            if r["time_ms"] in st_ms:
                a = static.loc[to_index([r["time_ms"]])[0]]
                bad = [c for c in ("open", "high", "low", "close") if abs(a[c] - r[c]) > 1e-9 * max(1.0, abs(a[c]))]
                if bad:
                    self.mismatches[sym].append({"time": iso(r["time_ms"]), "columns": bad})
        self._own_rows[sym] = rows
        self._cache.pop(sym, None)

    def _build(self, sym: str):
        if sym in self._cache:
            return self._cache[sym]
        static, unknown, cov_end = self._static[sym]
        st_ms = index_ms(static.index)
        have = set(st_ms.tolist())
        own_by_ms = {r["time_ms"]: r for r in self._own_rows[sym]}
        # own observations of funding fill unknown cells of archive bars (same bar, live value)
        static_f, unknown_f = static, unknown
        fill = [ms for ms in own_by_ms if ms in have]
        if fill:
            static_f, unknown_f = static.copy(), unknown.copy()
            for ms in fill:
                t = to_index([ms])[0]
                for c in FUNDING_COLS:
                    v = own_by_ms[ms].get(c)
                    if unknown_f.at[t, c] and v is not None and v == v:
                        static_f.at[t, c] = v
                        unknown_f.at[t, c] = False
        new = [r for ms, r in sorted(own_by_ms.items()) if ms not in have]
        if new:
            idx = to_index([r["time_ms"] for r in new])
            own = pd.DataFrame({c: [r.get(c, np.nan) for r in new] for c in KLINE_COLS + FUNDING_COLS}, index=idx)
            own["trades"] = own["trades"].astype("int64")
            # where the funding ARCHIVE covers the bar it wins over what we saw live (same rule as load())
            fund = self._fund[sym]
            close_t = idx + pd.Timedelta(hours=1)
            covered = np.asarray(close_t < cov_end)
            if covered.any():
                paid = fund.reindex(close_t).fillna(0.0).to_numpy()
                rate = fund.reindex(fund.index.union(close_t)).ffill().reindex(close_t).to_numpy()
                own["funding_paid"] = np.where(covered, paid, own["funding_paid"].to_numpy())
                own["funding_rate"] = np.where(covered, rate, own["funding_rate"].to_numpy())
            for c in OI_COLS:
                own[c] = np.nan
            own = own[COLUMNS]
            own_unknown = pd.DataFrame(True, index=idx, columns=MAYBE_UNKNOWN)
            for c in FUNDING_COLS:
                own_unknown[c] = own[c].isna().to_numpy()
            df = pd.concat([static_f, own])
            unk = pd.concat([unknown_f, own_unknown])
        else:
            df, unk = static_f, unknown_f
        self._cache[sym] = {"1h": (df, unk)}
        return self._cache[sym]

    def frame(self, sym: str, tf: str = "1h") -> pd.DataFrame:
        return self._get(sym, tf)[0]

    def unknown(self, sym: str, tf: str = "1h") -> pd.DataFrame:
        return self._get(sym, tf)[1]

    def _get(self, sym: str, tf: str):
        c = self._build(sym)
        if tf not in c:
            if tf != "4h":
                raise ValueError(f"unsupported timeframe {tf!r}")
            c[tf] = resample_4h(*c["1h"])
        return c[tf]

    # ------------------------------------------------------------------ live bars
    def last_bar_ms(self, sym: str) -> int | None:
        df = self.frame(sym, "1h")
        return int(index_ms(df.index[-1:])[0]) if len(df) else None

    def add_live_bar(self, sym: str, bar: dict) -> bool:
        """Persist one closed 1h bar seen live. False if we already have a bar at or after it."""
        ms = int(bar["time_ms"])
        if ms % HOUR_MS != 0:
            raise ValueError(f"{sym}: bar time {ms} is not on the hour")
        own = self._own_rows[sym]
        if own and ms <= own[-1]["time_ms"]:
            return False
        row = {"time_ms": ms, "recv_ms": bar.get("recv_ms")}
        for c in KLINE_COLS + FUNDING_COLS:
            v = bar.get(c)
            row[c] = float("nan") if v is None else float(v)
        self.store.append(sym, row)
        own.append(row)
        self._cache.pop(sym, None)
        return True

    # ------------------------------------------------------------------ gaps and readiness
    def missing_bars(self, sym: str, upto_ms: int) -> list[int]:
        """Open times of 1h bars missing between the first bar after the base archive and upto_ms."""
        df = self.frame(sym, "1h")
        have = index_ms(df.index)
        base = self.base[sym]
        start = int(index_ms(base.index[-1:])[0]) + HOUR_MS if len(base) else (int(have[0]) if len(have) else upto_ms)
        if upto_ms < start:
            return []
        want = np.arange(start, upto_ms + 1, HOUR_MS, dtype="int64")
        return np.setdiff1d(want, have).tolist()

    def ready(self, sym: str, tf: str, warmup: int, expected_last_ms: int) -> tuple[bool, str]:
        """True when the last `warmup` bars of `tf` are contiguous and end at expected_last_ms."""
        df = self.frame(sym, tf)
        step = TF_MS[tf]
        if len(df) == 0:
            return False, "history gap: no bars at all"
        ms = index_ms(df.index)
        if int(ms[-1]) != expected_last_ms:
            missing = self.missing_bars(sym, expected_last_ms + step - HOUR_MS)
            return False, (f"history gap: last {tf} bar is {iso(int(ms[-1]))}, expected {iso(expected_last_ms)}"
                           + _describe_missing(missing))
        tail = ms[-warmup:]
        if len(tail) < warmup:
            return False, f"history gap: only {len(tail)} {tf} bars, strategy needs {warmup}"
        breaks = np.flatnonzero(np.diff(tail) != step)
        if len(breaks):
            missing = self.missing_bars(sym, expected_last_ms + step - HOUR_MS)
            return False, (f"history gap: the last {warmup} {tf} bars are not contiguous"
                           + _describe_missing(missing))
        return True, "ok"

    def unknown_columns(self, sym: str, tf: str, warmup: int) -> dict:
        """Column -> number of unknown cells within the last `warmup` bars."""
        unk = self.unknown(sym, tf).iloc[-warmup:]
        counts = unk.sum()
        return {c: int(n) for c, n in counts.items() if n > 0}

    def summary(self, sym: str, now_ms: int) -> dict:
        last_closed = now_ms - now_ms % HOUR_MS - HOUR_MS
        missing = self.missing_bars(sym, last_closed)
        df = self.frame(sym, "1h")
        return {"bars": int(len(df)), "last_bar": iso(self.last_bar_ms(sym)),
                "base_last_bar": iso(int(index_ms(self.base[sym].index[-1:])[0])) if len(self.base[sym]) else None,
                "own_bars": len(self._own_rows[sym]), "missing_bars": len(missing),
                "first_missing": iso(missing[0]) if missing else None,
                "last_missing": iso(missing[-1]) if missing else None,
                "archive_mismatches": len(self.mismatches[sym])}


def _describe_missing(missing: list[int]) -> str:
    if not missing:
        return ""
    return (f" - {len(missing)} missing 1h bar(s) {iso(missing[0])} .. {iso(missing[-1])}; they can only come "
            f"from the daily archive file, published the day after")


def resample_4h(df: pd.DataFrame, unknown: pd.DataFrame):
    """4h bars from 1h bars, aligned to 00:00 UTC, incomplete buckets dropped - same as load(sym, '4h').
    A 4h cell is unknown (NaN) when any of its four 1h cells is unknown."""
    if len(df) == 0:
        return df.copy(), unknown.copy()
    r = df.resample("4h", label="left", closed="left").agg(AGG_4H)
    n = df["close"].resample("4h", label="left", closed="left").count()
    u = unknown.resample("4h", label="left", closed="left").max().astype(bool)
    keep = n == 4
    r, u = r[keep], u[keep]
    r = r.copy()
    for c in MAYBE_UNKNOWN:
        r.loc[u[c].to_numpy(), c] = np.nan
    return r, u
