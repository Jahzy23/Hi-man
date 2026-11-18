"""Load strategy modules by name and call them through the SPEC interface, on closed bars only.

The decision is always the LAST row of signals() run on the full history frame (same call the
backtester makes), so the bot and the backtest cannot drift apart.

Column dependency probe: some columns cannot be known live (see history.py). SPEC gives a strategy no
way to declare what it reads, so the loader finds out: it re-runs signals() on fully known archive
rows with one column removed, blanked or scrambled, and if the output moves (or the call fails) the
strategy depends on that column. The engine then refuses to trade it while that column has unknown
cells in the warm-up window. An optional module attribute REQUIRED_COLUMNS is honoured as well.
"""
from __future__ import annotations

import importlib
import importlib.util
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .history import MAYBE_UNKNOWN, index_ms
from .util import ROOT, TF_MS

SIGNAL_COLS = ["side", "entry", "entry_valid", "stop", "target", "max_hold", "exit"]
NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROBE_ROWS = 3000


class StrategyError(RuntimeError):
    pass


@dataclass
class LoadedStrategy:
    name: str
    module: object
    tf: str
    params: dict
    warmup: int
    declared_columns: tuple = ()
    needs: dict = field(default_factory=dict)       # symbol -> {column: why}
    uses_ctx: dict = field(default_factory=dict)    # symbol -> bool


@dataclass
class Decision:
    bar_ms: int
    side: int
    entry: float | None
    entry_valid: int
    stop: float | None
    target: float | None
    max_hold: int | None
    exit: bool


def load_strategy(name: str, params: dict | None = None, default_warmup: int = 300,
                  search_dir: Path | None = None) -> LoadedStrategy:
    if not NAME_RE.match(name or ""):
        raise StrategyError(f"strategy name {name!r} is not a module name")
    if search_dir is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        try:
            mod = importlib.import_module(f"strategies.{name}")
        except ModuleNotFoundError as e:
            raise StrategyError(f"strategy module strategies/{name}.py not found ({e})") from None
    else:
        path = Path(search_dir) / f"{name}.py"
        if not path.exists():
            raise StrategyError(f"strategy module {path} not found")
        spec = importlib.util.spec_from_file_location(f"_bot_strategy_{name}_{abs(hash(str(path)))}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    tf = getattr(mod, "TIMEFRAME", None)
    if tf not in TF_MS:
        raise StrategyError(f"{name}: TIMEFRAME must be '1h' or '4h', got {tf!r}")
    if not callable(getattr(mod, "signals", None)):
        raise StrategyError(f"{name}: no signals(df, p, ctx) function")
    defaults = getattr(mod, "DEFAULT_PARAMS", None)
    if not isinstance(defaults, dict):
        raise StrategyError(f"{name}: DEFAULT_PARAMS must be a dict")
    unknown = set(params or {}) - set(defaults)
    if unknown:
        raise StrategyError(f"{name}: params {sorted(unknown)} are not in DEFAULT_PARAMS {sorted(defaults)}")
    warmup = getattr(mod, "WARMUP_BARS", default_warmup)
    if not isinstance(warmup, int) or warmup < 1:
        raise StrategyError(f"{name}: WARMUP_BARS must be a positive int, got {warmup!r}")
    declared = tuple(getattr(mod, "REQUIRED_COLUMNS", ()) or ())
    return LoadedStrategy(name=name, module=mod, tf=tf, params={**defaults, **(params or {})}, warmup=warmup,
                          declared_columns=declared)


def run_signals(strat: LoadedStrategy, df: pd.DataFrame, ctx: dict | None) -> pd.DataFrame:
    """Call the module on copies (a strategy that mutates its input must not corrupt our history)."""
    out = strat.module.signals(df.copy(), dict(strat.params), None if ctx is None else {k: v.copy() for k, v in ctx.items()})
    if not isinstance(out, pd.DataFrame):
        raise StrategyError(f"{strat.name}: signals() returned {type(out).__name__}, not a DataFrame")
    if len(out) != len(df) or not out.index.equals(df.index):
        raise StrategyError(f"{strat.name}: signals() must return a frame on the same index as its input")
    if "side" not in out.columns:
        raise StrategyError(f"{strat.name}: signals() output has no 'side' column")
    return out


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def extract(strat: LoadedStrategy, out: pd.DataFrame, i: int = -1) -> Decision:
    """Row i of a signals() frame -> Decision. Missing optional columns take the SPEC defaults."""
    row = out.iloc[i]
    side_raw = _f(row["side"])
    side = 0 if side_raw is None else int(side_raw)
    if side not in (-1, 0, 1) or (side_raw is not None and side_raw != side):
        raise StrategyError(f"{strat.name}: side must be -1, 0 or +1, got {row['side']!r}")
    get = (lambda c: row[c] if c in out.columns else None)
    ev = _f(get("entry_valid"))
    mh = _f(get("max_hold"))
    ex = get("exit")
    return Decision(bar_ms=int(index_ms(out.index[[i]])[0]), side=side, entry=_f(get("entry")),
                    entry_valid=max(1, int(ev)) if ev is not None else 1, stop=_f(get("stop")),
                    target=_f(get("target")), max_hold=int(mh) if mh is not None and mh >= 1 else None,
                    exit=bool(ex) if ex is not None and ex == ex else False)


def evaluate(strat: LoadedStrategy, df: pd.DataFrame, ctx: dict | None) -> Decision:
    return extract(strat, run_signals(strat, df, ctx), -1)


# ---------------------------------------------------------------------- dependency probe
def _same(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    for c in SIGNAL_COLS:
        if (c in a.columns) != (c in b.columns):
            return False
        if c not in a.columns:
            continue
        x = pd.to_numeric(a[c], errors="coerce").to_numpy(dtype="float64")
        y = pd.to_numeric(b[c], errors="coerce").to_numpy(dtype="float64")
        if not np.array_equal(x, y, equal_nan=True):
            return False
    return True


def _variants(df: pd.DataFrame, col: str):
    """Three ways a column can be 'not there': removed, blank at the recent end, and wrong."""
    yield "removed", df.drop(columns=[col])
    blank = df.copy()
    blank.iloc[-(len(df) // 3):, blank.columns.get_loc(col)] = np.nan
    yield "blank on recent bars", blank
    rng = np.random.default_rng(12345)
    wrong = df.copy()
    wrong[col] = wrong[col].to_numpy(dtype="float64") * rng.uniform(0.25, 4.0, len(df)) + rng.normal(0, 1e-3, len(df))
    yield "scrambled", wrong


def probe(strat: LoadedStrategy, sym: str, frames: dict, columns=MAYBE_UNKNOWN) -> tuple[dict, bool]:
    """frames: symbol -> fully known frame on strat.tf (same index). Returns (needs, uses_ctx) for sym."""
    frames = {s: f.iloc[-PROBE_ROWS:] for s, f in frames.items()}
    df = frames[sym]
    try:
        base = run_signals(strat, df, frames)
    except StrategyError:
        raise
    except Exception as e:
        raise StrategyError(f"{strat.name}: signals() failed on archive data for {sym}: "
                            f"{type(e).__name__}: {e}") from e
    needs: dict = {c: "declared in REQUIRED_COLUMNS" for c in strat.declared_columns if c in columns}
    for col in columns:
        if col in needs or col not in df.columns:
            continue
        gens = {s: _variants(f, col) for s, f in frames.items()}
        for _ in range(3):
            label, var = None, {}
            for s, g in gens.items():
                label, var[s] = next(g)
            try:
                out = run_signals(strat, var[sym], var)
            except Exception as e:
                needs[col] = f"signals() fails when {col} is {label} ({type(e).__name__})"
                break
            if not _same(out, base):
                needs[col] = f"signals change when {col} is {label}"
                break
    try:
        uses_ctx = not _same(run_signals(strat, df, None), base)
    except Exception:
        uses_ctx = True
    if not uses_ctx and len(frames) > 1:
        # a strategy may read ctx only for other symbols: scramble the others and see
        rng = np.random.default_rng(999)
        other = {s: (f if s == sym else f * rng.uniform(0.5, 2.0, (len(f), 1))) for s, f in frames.items()}
        try:
            uses_ctx = not _same(run_signals(strat, df, other), base)
        except Exception:
            uses_ctx = True
    return needs, uses_ctx
