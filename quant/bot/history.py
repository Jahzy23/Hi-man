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
    """Frames per symbol. The archive part ("static": base + refresh files) is rebuilt only by reload();
    live bars are appended incrementally, so the hourly cost does not grow with the bot's uptime."""

    def __init__(self, symbols, state_dir: Path, base_loader=None, funding_archive=None):
        self.symbols = list(symbols)
        self.state_dir = Path(state_dir)
        self.store = BarStore(self.state_dir)
        base_loader = base_loader or default_base_loader
        funding_archive = funding_archive or default_funding_archive
        self.base: dict[str, pd.DataFrame] = {}
        self.base_funding: dict[str, pd.Series] = {}
        self._static: dict = {}        # sym -> (frame, unknown mask, funding coverage end)
        self._static_ms: dict = {}     # sym -> (sorted ms array, set)
        self._static4h: dict = {}      # sym -> (4h frame, 4h unknown, tail rows of static not in it)
        self._fund: dict = {}
        self._own_rows: dict[str, list] = {}
        self._own: dict = {}           # sym -> (frame, unknown) of live bars the archive does not have yet
        self._ms1h: dict = {}          # sym -> sorted ms of every 1h bar we have
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
        static = static[COLUMNS]
        unknown = pd.DataFrame(False, index=static.index, columns=MAYBE_UNKNOWN)
        for c in FUNDING_COLS:
            unknown[c] = ~known_f
        for c in OI_COLS:
            unknown[c] = ~in_base
        self._fund[sym] = fund
        rows = self.store.read(sym)
        st_ms = index_ms(static.index)
        st_set = set(st_ms.tolist())
        # bars we recorded live that the archive now has too: the archive's prices win (and must agree),
        # our funding observation fills the archive bar's unknown funding cells
        self.mismatches[sym] = []
        for r in rows:
            if r["time_ms"] not in st_set:
                continue
            t = to_index([r["time_ms"]])[0]
            a = static.loc[t]
            bad = [c for c in ("open", "high", "low", "close") if abs(a[c] - r[c]) > 1e-9 * max(1.0, abs(a[c]))]
            if bad:
                self.mismatches[sym].append({"time": iso(r["time_ms"]), "columns": bad})
            for c in FUNDING_COLS:
                v = r.get(c)
                if unknown.at[t, c] and v is not None and v == v:
                    static.at[t, c] = v
                    unknown.at[t, c] = False
        self._static[sym] = (static, unknown, cov_end)
        self._static_ms[sym] = (st_ms, st_set)
        self._static4h.pop(sym, None)
        self._own_rows[sym] = rows
        self._own[sym] = self._own_frames(sym, [r for r in rows if r["time_ms"] not in st_set])
        self._ms1h[sym] = np.union1d(st_ms, index_ms(self._own[sym][0].index))
        self._cache.pop(sym, None)

    def _own_frames(self, sym: str, rows: list):
        """(frame, unknown) for live bars that are not in the archive part."""
        cov_end, fund = self._static[sym][2], self._fund[sym]
        n = len(rows)
        ms = np.array([r["time_ms"] for r in rows], dtype="int64")
        idx = to_index(ms)
        data = {c: np.array([r.get(c, np.nan) for r in rows], dtype="float64") for c in KLINE_COLS + FUNDING_COLS}
        if n:
            # where the funding ARCHIVE covers the bar it wins over what we saw live (same rule as load())
            covered = ms + HOUR_MS < int(cov_end.timestamp() * 1000)
            if covered.any():
                close_t = idx + pd.Timedelta(hours=1)
                paid = fund.reindex(close_t).fillna(0.0).to_numpy()
                rate = fund.reindex(fund.index.union(close_t)).ffill().reindex(close_t).to_numpy()
                data["funding_paid"] = np.where(covered, paid, data["funding_paid"])
                data["funding_rate"] = np.where(covered, rate, data["funding_rate"])
        data["trades"] = data["trades"].astype("int64")
        for c in OI_COLS:
            data[c] = np.full(n, np.nan)
        own = pd.DataFrame(data, index=idx, columns=COLUMNS)
        unk = pd.DataFrame({c: (np.isnan(data[c]) if c in FUNDING_COLS else np.ones(n, dtype=bool))
                            for c in MAYBE_UNKNOWN}, index=idx, columns=MAYBE_UNKNOWN)
        return own, unk

    def _get(self, sym: str, tf: str):
        c = self._cache.setdefault(sym, {})
        if tf in c:
            return c[tf]
        static, s_unk, _ = self._static[sym]
        own, o_unk = self._own[sym]
        ordered = not len(own) or not len(static) or own.index[0] > static.index[-1]
        if tf == "1h":
            if not len(own):
                c[tf] = (static, s_unk)
            else:
                df, unk = pd.concat([static, own]), pd.concat([s_unk, o_unk])
                if not ordered:              # a live bar older than the newest archive bar (archive has a hole)
                    df, unk = df.sort_index(), unk.sort_index()
                c[tf] = (df, unk)
        elif tf == "4h":
            if not ordered:
                c[tf] = resample_4h(*self._get(sym, "1h"))
            else:
                if sym not in self._static4h:    # the archive part is resampled once, not every hour
                    ms = self._static_ms[sym][0]
                    cut = int(ms[-1]) - int(ms[-1]) % TF_MS["4h"] if len(ms) else 0
                    n_head = int(np.searchsorted(ms, cut))
                    head = resample_4h(static.iloc[:n_head], s_unk.iloc[:n_head])
                    self._static4h[sym] = (head[0], head[1], static.iloc[n_head:], s_unk.iloc[n_head:])
                h_df, h_unk, t_df, t_unk = self._static4h[sym]
                tail = resample_4h(pd.concat([t_df, own]), pd.concat([t_unk, o_unk]))
                c[tf] = (pd.concat([h_df, tail[0]]), pd.concat([h_unk, tail[1]])) if len(tail[0]) else (h_df, h_unk)
        else:
            raise ValueError(f"unsupported timeframe {tf!r}")
        return c[tf]

    def frame(self, sym: str, tf: str = "1h") -> pd.DataFrame:
        return self._get(sym, tf)[0]

    def unknown(self, sym: str, tf: str = "1h") -> pd.DataFrame:
        return self._get(sym, tf)[1]

    # ------------------------------------------------------------------ live bars
    def last_bar_ms(self, sym: str) -> int | None:
        ms = self._ms1h[sym]
        return int(ms[-1]) if len(ms) else None

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
        if ms in self._static_ms[sym][1]:
            self.reload(sym)                 # the archive already has this bar: only its funding can be news
            return True
        df1, unk1 = self._own_frames(sym, [row])
        df0, unk0 = self._own[sym]
        self._own[sym] = (pd.concat([df0, df1]), pd.concat([unk0, unk1])) if len(df0) else (df1, unk1)
        cur = self._ms1h[sym]
        self._ms1h[sym] = np.append(cur, ms) if not len(cur) or ms > cur[-1] else np.union1d(cur, [ms])
        self._cache.pop(sym, None)
        return True

    # ------------------------------------------------------------------ gaps and readiness
    def missing_bars(self, sym: str, upto_ms: int) -> list[int]:
        """Open times of 1h bars missing between the first bar after the base archive and upto_ms."""
        have = self._ms1h[sym]
        base = self.base[sym]
        start = int(index_ms(base.index[-1:])[0]) + HOUR_MS if len(base) else (int(have[0]) if len(have) else upto_ms)
        if upto_ms < start:
            return []
        want = np.arange(start, upto_ms + 1, HOUR_MS, dtype="int64")
        return np.setdiff1d(want, have).tolist()

    def ready(self, sym: str, tf: str, warmup: int, expected_last_ms: int) -> tuple[bool, str]:
        """True when the last `warmup` bars of `tf` are contiguous and end at expected_last_ms."""
        step = TF_MS[tf]
        ms = self._ms1h[sym] if tf == "1h" else index_ms(self.frame(sym, tf).index)
        if len(ms) == 0:
            return False, "history gap: no bars at all"
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

    def funding_coverage_end(self, sym: str) -> pd.Timestamp:
        return self._static[sym][2]

    def summary(self, sym: str, now_ms: int) -> dict:
        last_closed = now_ms - now_ms % HOUR_MS - HOUR_MS
        missing = self.missing_bars(sym, last_closed)
        return {"bars": int(len(self._ms1h[sym])), "last_bar": iso(self.last_bar_ms(sym)),
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
