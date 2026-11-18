#!/usr/bin/env python3
"""Durable snapshot of the paper bot, for when the container (and with it bot/state, bot/logs) is lost.

    python3 bot/snapshot.py              copy the minimal durable state into paper_state/
    python3 bot/snapshot.py --restore    put it back into bot/state and bot/logs - only if those are empty
    python3 bot/snapshot.py --show       what the snapshot holds

bot/state and bot/logs are git-ignored; paper_state/ is meant to be committed. What is copied
(an allowlist - nothing else ever leaves bot/state):

    state/books.json          paper books: equity, positions with their stops, resting orders
    state/shield.json         Shield: halts, day baselines, loss streaks
    state/HALT, state/KILL    if present. Without HALT a restored Shield would read its absence as
                              "a human cleared the halt" and resume trading.
    state/market.json         funding observation state (to charge a settlement missed in the outage)
    state/status.json         last snapshot of the engine: tells the restart how long the bot was away
    state/bars/<SYM>_1h.csv   closed bars the bot recorded itself (the archive cannot replace today's)
    logs/trades.csv           trades ledger
    logs/decisions.jsonl      the last 2,000 lines only

Not copied: API keys (they are never on disk), GO_LIVE_CONFIRMED (a human's decision is not
resurrected by a script), lock/pid files, bot.log, the re-downloadable archive refresh.

Consistency: every source file is written atomically by the bot, and they are copied ledger first,
books.json last, so the books are never older than the ledger; the engine's start-up reconciliation
(books.json is the truth) repairs the rest. The snapshot directory is replaced in one rename, and
MANIFEST.json carries a SHA-256 per file that --restore verifies before it writes anything.

After a restore the bot treats the time since the snapshot as an outage: with open positions or
orders and more than 60 s gone, Shield halts (stale feed), cancels the orders and flattens at the
first fresh quote.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from bot.util import iso  # noqa: E402

DEFAULT_DEST = HERE.parent / "paper_state"
DECISION_LINES = 2000
STATE_FILES = ["books.json", "shield.json", "HALT", "KILL", "market.json", "status.json"]
SECRET_ENV = ("BINANCE_API_KEY", "BINANCE_API_SECRET")


class SnapshotError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _tail_lines(src: Path, dst: Path, n: int, chunk: int = 1 << 20) -> int:
    """Last n complete lines of src -> dst, reading only the end of the file (the decision log is
    never rotated). A torn last line (no newline yet) is dropped."""
    with open(src, "rb") as f:
        f.seek(0, 2)
        pos = f.tell()
        data = b""
        while pos > 0 and data.count(b"\n") <= n:        # one more newline than lines: the cut is on a line start
            step = min(chunk, pos)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.split(b"\n")
    lines = lines[:-1]                       # after the last newline: empty, or a line still being written
    if pos > 0:
        lines = lines[1:]                    # the first piece may start in the middle of a line
    keep = lines[-n:]
    with open(dst, "wb") as f:
        for line in keep:
            f.write(line + b"\n")
    return len(keep)


def _plan(state_dir: Path, log_dir: Path) -> list[tuple[Path, str]]:
    """(source, name inside the snapshot) in copy order: ledger and log first, books.json last."""
    items: list[tuple[Path, str]] = []
    for name in ("trades.csv", "decisions.jsonl"):
        if (log_dir / name).is_file():
            items.append((log_dir / name, f"logs/{name}"))
    bars = state_dir / "bars"
    if bars.is_dir():
        for p in sorted(bars.glob("*_1h.csv")):
            items.append((p, f"state/bars/{p.name}"))
    for name in ("status.json", "market.json", "KILL", "HALT", "shield.json", "books.json"):
        if (state_dir / name).is_file():
            items.append((state_dir / name, f"state/{name}"))
    return items


def _no_secrets(root: Path, env=None) -> None:
    env = os.environ if env is None else env
    secrets = [env[k] for k in SECRET_ENV if env.get(k)]
    if not secrets:
        return
    for p in root.rglob("*"):
        if p.is_file():
            text = p.read_text(encoding="utf-8", errors="replace")
            if any(s in text for s in secrets):
                raise SnapshotError(f"{p.name} contains an API credential; snapshot refused")


def take(state_dir: Path, log_dir: Path, dest: Path = DEFAULT_DEST, now_ms: int | None = None, env=None) -> dict:
    """Copy the durable state into `dest`, replacing the previous snapshot in one rename."""
    state_dir, log_dir, dest = Path(state_dir), Path(log_dir), Path(dest)
    items = _plan(state_dir, log_dir)
    if not any(name == "state/books.json" for _, name in items):
        raise SnapshotError(f"nothing to snapshot: {state_dir / 'books.json'} does not exist")
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    tmp = dest.with_name(dest.name + f".tmp{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    files = {}
    try:
        for src, name in items:
            out = tmp / name
            out.parent.mkdir(parents=True, exist_ok=True)
            if name == "logs/decisions.jsonl":
                kept = _tail_lines(src, out, DECISION_LINES)
                files[name] = {"lines": kept}
            else:
                try:
                    shutil.copyfile(src, out)
                except FileNotFoundError:
                    continue                 # e.g. HALT cleared between listing and copying
                files[name] = {}
            files[name].update(bytes=out.stat().st_size, sha256=_sha(out))
        _no_secrets(tmp, env)
        manifest = {"format": 1, "taken_at": iso(now_ms), "taken_at_ms": now_ms,
                    "what": "minimal durable state of the paper bot (see bot/snapshot.py)",
                    "restore": "python3 bot/snapshot.py --restore   (only into an empty bot/state and bot/logs)",
                    "files": files}
        (tmp / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
        old = dest.with_name(dest.name + f".old{os.getpid()}")
        shutil.rmtree(old, ignore_errors=True)
        if dest.exists():
            os.replace(dest, old)
        os.replace(tmp, dest)
        shutil.rmtree(old, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return manifest


def _files_in(d: Path) -> list[Path]:
    return [p for p in d.rglob("*") if p.is_file()] if d.exists() else []


def restore(state_dir: Path, log_dir: Path, src: Path = DEFAULT_DEST) -> dict:
    """Write the snapshot back. Refuses unless bot/state and bot/logs hold no file at all, and unless
    every snapshot file matches its checksum. Nothing is written when it refuses."""
    state_dir, log_dir, src = Path(state_dir), Path(log_dir), Path(src)
    man_path = src / "MANIFEST.json"
    if not man_path.is_file():
        raise SnapshotError(f"no snapshot at {src} (MANIFEST.json missing)")
    try:
        manifest = json.loads(man_path.read_text())
        files = manifest["files"]
    except (ValueError, KeyError) as e:
        raise SnapshotError(f"{man_path} is not a snapshot manifest: {e}") from None
    for d, label in ((state_dir, "state"), (log_dir, "logs")):
        present = _files_in(d)
        if present:
            raise SnapshotError(f"refusing to restore: {d} is not empty ({len(present)} file(s), e.g. "
                                f"{present[0].name}). Restore only replaces lost {label}, it never overwrites.")
    allowed = {f"state/{n}" for n in STATE_FILES} | {"logs/trades.csv", "logs/decisions.jsonl"}
    for name, meta in files.items():
        if not (name in allowed or (name.startswith("state/bars/") and name.endswith("_1h.csv")
                                    and "/" not in name[len("state/bars/"):])):
            raise SnapshotError(f"snapshot lists a file that does not belong in it: {name!r}")
        p = src / name
        if not p.is_file():
            raise SnapshotError(f"snapshot is incomplete: {name} is listed but missing")
        if _sha(p) != meta.get("sha256"):
            raise SnapshotError(f"snapshot is damaged: {name} does not match its checksum")
    if "state/books.json" not in files:
        raise SnapshotError("snapshot has no books.json")
    # books.json goes in last: if a restore is interrupted there are no books yet, run.sh keeps asking
    # and a second --restore refuses the half-filled directories instead of mixing two states
    for name in sorted(files, key=lambda x: x == "state/books.json"):
        root, rel = name.split("/", 1)
        out = (state_dir if root == "state" else log_dir) / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src / name, out)
    return manifest


def describe(src: Path = DEFAULT_DEST) -> str:
    man_path = Path(src) / "MANIFEST.json"
    if not man_path.is_file():
        return f"no snapshot at {src}"
    m = json.loads(man_path.read_text())
    lines = [f"snapshot at {src}, taken {m.get('taken_at')}"]
    for name, meta in sorted(m.get("files", {}).items()):
        lines.append(f"  {name:<28} {meta.get('bytes', '?'):>9} bytes" + (f"  {meta['lines']} lines" if "lines" in meta else ""))
    try:
        books = json.loads((Path(src) / "state" / "books.json").read_text())["books"]
        for bid, b in books.items():
            lines.append(f"  book {bid}: equity {b['cash']:.2f}, {len(b['positions'])} position(s), "
                         f"{len(b['orders'])} resting order(s), {b['n_trades']} trade(s)")
    except (OSError, ValueError, KeyError):
        pass
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--restore", action="store_true", help="copy the snapshot back into empty bot/state and bot/logs")
    ap.add_argument("--show", action="store_true", help="describe the snapshot and exit")
    ap.add_argument("--state-dir", default=str(HERE / "state"))
    ap.add_argument("--log-dir", default=str(HERE / "logs"))
    ap.add_argument("--dir", default=str(DEFAULT_DEST), help="snapshot directory (default: paper_state/)")
    a = ap.parse_args(argv)
    try:
        if a.show:
            print(describe(Path(a.dir)))
        elif a.restore:
            m = restore(Path(a.state_dir), Path(a.log_dir), Path(a.dir))
            print(f"restored {len(m['files'])} file(s) from the snapshot taken {m['taken_at']}")
            print("note: the bot will treat the time since then as an outage (positions are flattened at the "
                  "first quote if it was longer than the stale-feed limit)")
        else:
            m = take(Path(a.state_dir), Path(a.log_dir), Path(a.dir))
            print(f"snapshot written to {a.dir}: {len(m['files'])} file(s), taken {m['taken_at']}")
    except SnapshotError as e:
        print(f"snapshot: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
