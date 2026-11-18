"""Entry point:  python3 -m bot.main [--config config/setup.toml] [--max-seconds N]

Exit codes (bot/run.sh acts on them):
    0  clean stop (SIGTERM/SIGINT or --max-seconds reached)
    2  refused: config mode is not "paper" (this build never places a real order)
    3  halted: unhandled exception or a HALT that needs a human is in place - do not restart
    4  another instance holds the lock
"""
from __future__ import annotations

import argparse
import fcntl
import logging
import logging.handlers
import os
import signal
import sys
from pathlib import Path

from .config import DEFAULT_CONFIG, load_config
from .util import BOT_DIR, atomic_write_json, iso, now_ms, read_json

log = logging.getLogger("bot")


def emergency_halt(state_dir: Path, reason: str) -> None:
    """Write a HALT that needs a human when there is no Shield instance to do it (startup failures)."""
    state_dir = Path(state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "HALT"
    t = now_ms()
    try:
        cur = read_json(path) or {}
        halts = [h for h in cur.get("halts", []) if isinstance(h, dict)]
    except ValueError:
        halts = []
    halts.append({"scope": "account", "kind": "exception", "reason": reason[:500], "since": iso(t), "since_ms": t,
                  "needs_human": True})
    atomic_write_json(path, {"halted": True, "written_at": iso(t), "needs_human": True, "blocks_restart": True,
                             "how_to_clear": "Fix the cause, then delete this file.", "halts": halts})


def blocking_halt(state_dir: Path) -> str | None:
    """Reason text if state/HALT holds a halt the process must not start past."""
    path = Path(state_dir) / "HALT"
    try:
        data = read_json(path)
    except ValueError:
        return None                      # hand-written HALT: Shield adopts it and runs halted
    if not data or not data.get("blocks_restart"):
        return None
    return "; ".join(f"{h.get('kind')}: {h.get('reason')}" for h in data.get("halts", [])) or "HALT"


def acquire_lock(state_dir: Path):
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(state_dir / "bot.lock", "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    (state_dir / "bot.pid").write_text(f"{os.getpid()}\n")
    return fh


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S")
    fmt.converter = __import__("time").gmtime
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = logging.handlers.RotatingFileHandler(log_dir / "bot.log", maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)


def main(argv=None, feed_factory=None, engine_factory=None) -> int:
    ap = argparse.ArgumentParser(description="Paper-trading bot")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--max-seconds", type=float, default=None, help="stop cleanly after N seconds (smoke runs)")
    ap.add_argument("--no-refresh", action="store_true", help="do not contact data.binance.vision")
    args = ap.parse_args(argv)
    fallback_state = Path(args.state_dir) if args.state_dir else BOT_DIR / "state"
    try:
        cfg = load_config(args.config, state_dir=args.state_dir, log_dir=args.log_dir)
    except Exception as e:
        print(f"config error: {type(e).__name__}: {e}", file=sys.stderr)
        emergency_halt(fallback_state, f"config error in {args.config}: {type(e).__name__}: {e}")
        return 3
    setup_logging(cfg.log_dir)
    if cfg.mode != "paper":
        log.error("mode is %r: this build only runs paper. Nothing started, no order placed.", cfg.mode)
        return 2
    reason = blocking_halt(cfg.state_dir)
    if reason:
        log.error("HALT needs a human, not starting: %s (delete %s to resume)", reason, cfg.state_dir / "HALT")
        return 3
    lock = acquire_lock(cfg.state_dir)
    if lock is None:
        log.error("another bot instance holds %s", cfg.state_dir / "bot.lock")
        return 4
    stop = {"flag": False, "why": "stop requested"}

    def on_signal(signum, _frame):
        stop["flag"], stop["why"] = True, f"signal {signal.Signals(signum).name}"

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    engine = feed = worker = None
    try:
        from .archive import ArchiveRefresher, RefreshWorker
        from .engine import Engine
        from .feed import BinanceFeed
        if not args.no_refresh:
            worker = RefreshWorker(ArchiveRefresher(cfg.state_dir))
            worker.start()
        engine = (engine_factory or Engine)(cfg, refresher=worker)
        feed = (feed_factory or BinanceFeed)(cfg.symbols)
        feed.start()
        log.info("bot running: pid %d, config %s, state %s", os.getpid(), cfg.source, cfg.state_dir)
        engine.run(feed, should_stop=lambda: stop["flag"], max_seconds=args.max_seconds)
        why = stop["why"] if stop["flag"] else f"--max-seconds {args.max_seconds:g} reached"
        engine.shutdown(now_ms(), why)
        log.info("bot stopped: %s", why)
        return 0
    except Exception as e:
        log.exception("unhandled exception - halting")
        try:
            if engine is not None:
                engine.fatal(e, now_ms(), "main loop")
            else:
                emergency_halt(cfg.state_dir, f"startup failed: {type(e).__name__}: {e}")
        except Exception as e2:          # even the halt failed (disk?): leave the plainest possible HALT
            log.exception("halt procedure failed")
            emergency_halt(cfg.state_dir, f"{type(e).__name__}: {e}; then halt failed: {type(e2).__name__}: {e2}")
        return 3
    finally:
        for thing in (feed, worker):
            try:
                if thing is not None:
                    thing.stop()
            except Exception:
                pass
        try:
            (cfg.state_dir / "bot.pid").unlink(missing_ok=True)
            lock.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
