"""Small shared helpers: time conversion, atomic file writes, paths."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]          # .../quant
BOT_DIR = Path(__file__).resolve().parent
HOUR_MS = 3_600_000
TF_MS = {"1h": HOUR_MS, "4h": 4 * HOUR_MS}


def now_ms() -> int:
    return int(time.time() * 1000)


def iso(ms: int | float | None) -> str | None:
    """Epoch milliseconds -> 'YYYY-MM-DDTHH:MM:SS.mmmZ' (UTC)."""
    if ms is None:
        return None
    t = dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc)
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def parse_iso(s: str) -> int:
    """Inverse of iso(); also accepts '+00:00' offsets and second resolution."""
    t = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.timezone.utc)
    return int(round(t.timestamp() * 1000))


def utc_day(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000.0, tz=dt.timezone.utc).strftime("%Y-%m-%d")


def floor_ms(ms: int, step_ms: int) -> int:
    """Floor to a multiple of step_ms counted from the epoch (00:00 UTC aligned for 1h/4h)."""
    return ms - ms % step_ms


def atomic_write_text(path: Path, text: str) -> None:
    """Write-then-rename so a crash never leaves a half-written state file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def strict(obj):
    """Copy of obj that is valid strict JSON: NaN/inf -> null, numpy scalars -> Python numbers."""
    if isinstance(obj, dict):
        return {str(k): strict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [strict(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        obj = obj.item()
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    return obj


def atomic_write_json(path: Path, obj) -> None:
    atomic_write_text(path, json.dumps(strict(obj), indent=1, sort_keys=True, allow_nan=False))


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def clean_float(x):
    """float or None (NaN/None/inf -> None). Used before anything is persisted or compared."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v
