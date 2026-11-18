#!/usr/bin/env python
"""Run every strategy module on every symbol with DEFAULT_PARAMS over the SPEC backtest window.

    cd /home/user/Hi-man/quant && python lab/run_all.py

Discovers strategies/*.py (skips names starting with "_" and data.py), tolerates modules that
fail to import or raise (the error is recorded in the summary and the run continues), and writes
    lab/results/summary.csv                      one row per (module, symbol)
    lab/results/trades/<module>__<symbol>.csv    trade list
    lab/results/equity/<module>__<symbol>.csv    daily equity (strategy and buy-and-hold)
    lab/results/yearly.csv, regime.csv           splits, long format
The summary covers the full SPEC window (as specified) and also reports the DEV / HOLDOUT split
of the same run (dev_net_return, holdout_net_return): selection decisions belong on DEV.
``--window dev`` runs without touching the last 12 months at all.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np          # noqa: E402
import pandas as pd         # noqa: E402

from lab import metrics as M            # noqa: E402
from lab import validate as V           # noqa: E402
from lab.engine import (ExecConfig, module_params, module_signals, no_bytecode, run_backtest,  # noqa: E402
                        spec_data)

STRATEGY_DIR = ROOT / "strategies"
RESULTS_DIR = ROOT / "lab" / "results"
REQUIRED_ATTRS = ("TIMEFRAME", "DEFAULT_PARAMS", "signals")
LEAD_COLS = ["module", "name", "symbol", "timeframe", "regime_tag", "status", "error", "net_return", "cagr",
             "max_dd", "sharpe", "profit_factor", "win_rate", "avg_R", "trades", "longest_losing_streak",
             "worst_month", "exposure", "fees_paid", "funding_paid", "slippage_paid", "bh_net_return",
             "bh_max_dd", "excess_vs_bh", "dev_net_return", "holdout_net_return"]


def discover(strat_dir: Path = STRATEGY_DIR) -> list[str]:
    return sorted(p.stem for p in Path(strat_dir).glob("*.py") if not p.stem.startswith("_") and p.stem != "data")


def load_module(name: str, strat_dir: Path = STRATEGY_DIR):
    strat_dir = Path(strat_dir).resolve()
    with no_bytecode():                                 # never write .pyc files into strategies/
        if strat_dir == STRATEGY_DIR.resolve():
            importlib.invalidate_caches()
            return importlib.import_module(f"strategies.{name}")
        spec = importlib.util.spec_from_file_location(f"_lab_strat_{name}", strat_dir / f"{name}.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def _err(e: BaseException) -> str:
    tb = traceback.extract_tb(e.__traceback__)
    where = f" [{Path(tb[-1].filename).name}:{tb[-1].lineno}]" if tb else ""
    return f"{type(e).__name__}: {e}{where}"[:500]


def _jsonable(d: dict) -> str:
    return json.dumps({k: (v.item() if isinstance(v, np.generic) else v) for k, v in d.items()}, default=str, sort_keys=True)


def run_one(module, modname: str, symbol: str, start, end, cfg: ExecConfig, out_dir: Path | None):
    t0 = time.perf_counter()
    df, sig = module_signals(module, symbol, None, upto=end)
    t1 = time.perf_counter()
    res = run_backtest(df, sig, symbol, start, end, cfg, name=getattr(module, "NAME", modname),
                       params=module_params(module))
    t2 = time.perf_counter()
    m = M.compute_metrics(res, df)
    d0, d1 = V.dev_window()
    h0, h1 = V.holdout_window()
    row = dict(m)
    covers_dev = len(res.equity) and res.equity.index[0] <= d1 and res.equity.index[-1] >= d0
    covers_holdout = len(res.equity) and res.equity.index[-1] >= h0
    row.update(dev_net_return=M.segment_return(res.equity, d0, d1, cfg.start_equity) if covers_dev else np.nan,
               holdout_net_return=M.segment_return(res.equity, h0, h1, cfg.start_equity) if covers_holdout else np.nan,
               window_start=str(res.start), window_end=str(res.end), signals_s=t1 - t0, backtest_s=t2 - t1,
               params=_jsonable(module_params(module)))
    row.update({f"diag_{k}": v for k, v in res.diagnostics.items()})
    yearly = M.yearly_split(res, df)
    regime = M.regime_split(res, df)
    if out_dir is not None:
        tag = f"{modname}__{symbol}"
        (out_dir / "trades").mkdir(parents=True, exist_ok=True)
        (out_dir / "equity").mkdir(parents=True, exist_ok=True)
        res.trades.to_csv(out_dir / "trades" / f"{tag}.csv", index=False)
        bh = M.buy_and_hold(df, symbol, res.start, res.end, cfg).equity
        pd.DataFrame({"equity": M.daily_equity(res.equity), "bh_equity": M.daily_equity(bh)}).to_csv(
            out_dir / "equity" / f"{tag}.csv")
    return row, yearly, regime


def run_all(strat_dir: Path = STRATEGY_DIR, out_dir: Path | None = RESULTS_DIR, symbols=None, start=None,
            end=None, cfg: ExecConfig | None = None, verbose: bool = True) -> pd.DataFrame:
    d = spec_data()
    symbols = list(symbols or d.SYMBOLS)
    start = d.BACKTEST_START if start is None else start
    end = d.BACKTEST_END if end is None else end
    cfg = cfg or ExecConfig()
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
    rows, yearly_parts, regime_parts = [], [], []
    for modname in discover(strat_dir):
        try:
            module = load_module(modname, strat_dir)
            missing = [a for a in REQUIRED_ATTRS if not hasattr(module, a)]
            if missing:
                raise AttributeError(f"module is missing {missing}")
            import_error = None
        except BaseException as e:                      # noqa: BLE001 - a broken module must not stop the run
            if isinstance(e, KeyboardInterrupt):
                raise
            module, import_error = None, _err(e)
        for sym in symbols:
            base = dict(module=modname, name=getattr(module, "NAME", modname), symbol=sym,
                        timeframe=getattr(module, "TIMEFRAME", ""), regime_tag=getattr(module, "REGIME", ""))
            if import_error is not None:
                rows.append({**base, "status": "error", "error": "import: " + import_error})
                continue
            try:
                row, yearly, regime = run_one(module, modname, sym, start, end, cfg, out_dir)
                rows.append({**base, "status": "ok", "error": "", **row})
                for part, store in ((yearly, yearly_parts), (regime, regime_parts)):
                    if len(part):
                        store.append(part.reset_index().assign(module=modname, symbol=sym))
            except BaseException as e:                  # noqa: BLE001
                if isinstance(e, KeyboardInterrupt):
                    raise
                rows.append({**base, "status": "error", "error": _err(e)})
            if verbose:
                r = rows[-1]
                msg = (f"net {r['net_return']:+8.2%}  maxDD {r['max_dd']:6.2%}  Sharpe {r['sharpe']:5.2f}  "
                       f"trades {r['trades']:5d}  B&H {r['bh_net_return']:+8.2%}" if r["status"] == "ok" else r["error"])
                print(f"{modname:28s} {sym:8s} {r['status']:5s} {msg}", flush=True)
    summary = pd.DataFrame(rows)
    cols = [c for c in LEAD_COLS if c in summary.columns] + [c for c in summary.columns if c not in LEAD_COLS]
    summary = summary.reindex(columns=cols if len(summary) else LEAD_COLS)
    if out_dir is not None:
        summary.to_csv(out_dir / "summary.csv", index=False)
        for parts, fname in ((yearly_parts, "yearly.csv"), (regime_parts, "regime.csv")):
            if parts:
                pd.concat(parts, ignore_index=True).to_csv(out_dir / fname, index=False)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--strategies", default=str(STRATEGY_DIR))
    ap.add_argument("--out", default=str(RESULTS_DIR))
    ap.add_argument("--symbols", nargs="*")
    ap.add_argument("--window", choices=["full", "dev", "holdout"], default="full",
                    help="full = SPEC backtest window (default); dev = everything before the last 12 months; "
                         "holdout = the last 12 months only")
    ap.add_argument("--start", help="overrides --window")
    ap.add_argument("--end", help="overrides --window")
    ap.add_argument("--exchange-filters", action="store_true",
                    help="apply the bot's lot step / minimum order size table (UNVERIFIED; phase-2 primary setting)")
    ap.add_argument("--fee-mult", type=float, default=1.0)
    ap.add_argument("--slip-mult", type=float, default=1.0)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    w0, w1 = {"full": (None, None), "dev": V.dev_window(), "holdout": V.holdout_window()}[a.window]
    filters = None
    if a.exchange_filters:
        from lab.engine import exchange_filters
        filters = exchange_filters()
        if not filters:
            raise SystemExit("--exchange-filters: bot/instruments.py is not importable")
    summary = run_all(Path(a.strategies), Path(a.out), a.symbols, a.start or w0, a.end or w1,
                      ExecConfig(fee_mult=a.fee_mult, slip_mult=a.slip_mult, filters=filters))
    n_ok = int((summary["status"] == "ok").sum()) if len(summary) else 0
    print(f"\n{len(summary)} runs ({n_ok} ok, {len(summary) - n_ok} failed) in {time.perf_counter() - t0:.1f}s "
          f"-> {Path(a.out) / 'summary.csv'}")
    if len(summary) == 0:
        print("no strategy modules found (strategies/*.py other than data.py and _*.py)")
    else:
        yrs = float(summary["years"].dropna().median()) if n_ok else 4.0
        print(V.multiple_testing(len(summary), yrs)["note"])
        print(M.SURVIVORSHIP_NOTE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
