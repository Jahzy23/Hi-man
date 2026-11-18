"""Incremental refresh from the public archive (data.binance.vision) into bot/state/archive/.

The research data under data/ is read-only for the bot; newer daily kline files and monthly funding
files are stored separately here. Every zip is verified against its published SHA256 .CHECKSUM (same
rule as datafetch/fetch.py). A daily file for day D appears on D+1, so a bar the bot did not see
live stays missing until then - History.ready() keeps the strategy in WARMING meanwhile.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import os
import threading
import zipfile
from pathlib import Path

import pandas as pd

from .history import KLINE_COLS
from .util import HOUR_MS

BASE = "https://data.binance.vision/data/futures/um"
KCOLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades",
         "taker_buy_base", "taker_buy_quote", "ignore"]


class ChecksumError(RuntimeError):
    pass


def fetch_verified(session, url: str, timeout: float = 30.0) -> bytes | None:
    """First file inside the zip at `url`, or None when it is not published yet (404)."""
    r = session.get(url, timeout=timeout)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    blob = r.content
    c = session.get(url + ".CHECKSUM", timeout=timeout)
    if c.status_code != 200:
        raise ChecksumError(f"no checksum for {url} (HTTP {c.status_code})")
    want = c.content.decode("ascii", "replace").split()[0].lower()
    got = hashlib.sha256(blob).hexdigest()
    if want != got:
        raise ChecksumError(f"checksum mismatch for {url}: published {want[:16]}.., downloaded {got[:16]}..")
    zf = zipfile.ZipFile(io.BytesIO(blob))
    return zf.read(zf.namelist()[0])


def parse_klines(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None, names=KCOLS)
    if str(df.iloc[0, 0]).startswith("open"):
        df = df.iloc[1:]
    df = df.apply(pd.to_numeric)
    out = df[KLINE_COLS].copy()
    out.insert(0, "time_ms", df["open_time"].astype("int64"))
    out = out[out["time_ms"] % HOUR_MS == 0]
    out["trades"] = out["trades"].astype("int64")
    return out.reset_index(drop=True)


def parse_funding(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    calc = pd.to_numeric(df["calc_time"]).astype("int64")
    # calc_time carries a few ms of jitter; settlements are on the hour (fetch.py rounds the same way)
    return pd.DataFrame({"time_ms": (calc + HOUR_MS // 2) // HOUR_MS * HOUR_MS,
                         "funding_rate": pd.to_numeric(df["last_funding_rate"])})


def _merge_csv(path: Path, new: pd.DataFrame) -> int:
    """Merge rows into a CSV keyed by time_ms (atomic rewrite). Returns how many rows were new."""
    path.parent.mkdir(parents=True, exist_ok=True)
    old = pd.read_csv(path) if path.exists() else new.iloc[:0]
    both = pd.concat([old, new]).drop_duplicates("time_ms", keep="last").sort_values("time_ms")
    added = len(both) - len(old)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    both.to_csv(tmp, index=False)
    os.replace(tmp, path)
    return added


class ArchiveRefresher:
    def __init__(self, state_dir: Path, session=None):
        self.dir = Path(state_dir) / "archive"
        self._session = session

    @property
    def session(self):
        if self._session is None:
            import requests
            self._session = requests.Session()      # honours HTTPS_PROXY / REQUESTS_CA_BUNDLE from the env
        return self._session

    def refresh_klines(self, sym: str, days) -> dict:
        """Download daily 1h kline files for `days` (dates). Returns {"added": n, "missing": [days not published]}."""
        added, missing = 0, []
        for d in sorted(set(days)):
            raw = fetch_verified(self.session, f"{BASE}/daily/klines/{sym}/1h/{sym}-1h-{d.isoformat()}.zip")
            if raw is None:
                missing.append(d.isoformat())
                continue
            added += _merge_csv(self.dir / f"{sym}_1h.csv", parse_klines(raw))
        return {"added": added, "missing": missing}

    def refresh_funding(self, sym: str, months) -> dict:
        added, missing = 0, []
        for y, m in sorted(set(months)):
            raw = fetch_verified(self.session, f"{BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{y}-{m:02d}.zip")
            if raw is None:
                missing.append(f"{y}-{m:02d}")
                continue
            added += _merge_csv(self.dir / f"{sym}_funding.csv", parse_funding(raw))
        return {"added": added, "missing": missing}


def days_of(bar_ms_list, upto_day: dt.date) -> list[dt.date]:
    """UTC days (<= upto_day) that contain at least one of the given bar open times."""
    days = {dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).date() for ms in bar_ms_list}
    return sorted(d for d in days if d <= upto_day)


class RefreshWorker(threading.Thread):
    """Runs archive refreshes off the trading thread. The engine asks with request(); results are
    picked up with take_results(). Failures are reported, never raised into the trading loop:
    a failed refresh only means the bot keeps WARMING."""

    def __init__(self, refresher: ArchiveRefresher):
        super().__init__(name="archive-refresh", daemon=True)
        self.refresher = refresher
        self._lock = threading.Lock()
        self._jobs: list = []
        self._results: list = []
        self._wake = threading.Event()
        self._stop_evt = threading.Event()
        self.busy = False

    def request(self, sym: str, days=(), months=()) -> None:
        with self._lock:
            self._jobs.append((sym, list(days), list(months)))
        self._wake.set()

    def take_results(self) -> list:
        with self._lock:
            out, self._results = self._results, []
        return out

    def stop(self):
        self._stop_evt.set()
        self._wake.set()

    def run_once(self) -> None:
        with self._lock:
            jobs, self._jobs = self._jobs, []
        for sym, days, months in jobs:
            res = {"symbol": sym, "added": 0, "missing": [], "error": None}
            try:
                if days:
                    r = self.refresher.refresh_klines(sym, days)
                    res["added"] += r["added"]
                    res["missing"] += r["missing"]
                if months:
                    r = self.refresher.refresh_funding(sym, months)
                    res["added"] += r["added"]
                    res["missing"] += r["missing"]
            except Exception as e:
                res["error"] = f"{type(e).__name__}: {e}"[:300]
            with self._lock:
                self._results.append(res)

    def run(self):
        while not self._stop_evt.is_set():
            self._wake.wait()
            self._wake.clear()
            if self._stop_evt.is_set():
                break
            self.busy = True
            try:
                self.run_once()
            finally:
                self.busy = False
