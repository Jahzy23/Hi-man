#!/usr/bin/env python3
"""Compact status of the bot from the files it writes.   python3 bot/status.py [--all] [--state-dir D --log-dir D]

Reads only: state/status.json (snapshot the engine rewrites every 5 s), state/HALT, state/KILL,
logs/decisions.jsonl. Works whether or not the bot is running.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from bot.util import iso, parse_iso  # noqa: E402

ROUTINE = {"heartbeat", "bar", "eval"}


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return None


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def tail_decisions(path: Path, max_bytes: int = 2_000_000) -> list[dict]:
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return []
    lines = data.splitlines()
    if size > max_bytes:
        lines = lines[1:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _dur(seconds: float) -> str:
    seconds = int(max(0, seconds))
    h, r = divmod(seconds, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m" if h else (f"{m}m{s:02d}s" if m else f"{s}s")


def _age(ms, now_ms) -> str:
    return "never" if not ms else f"{(now_ms - ms) / 1000:.1f}s"


def render(state_dir: Path, log_dir: Path, show_all: bool = False, now_ms: int | None = None, n: int = 20) -> str:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    st = _load(state_dir / "status.json")
    out = []
    if st is None:
        out.append("BOT      never started (no state/status.json)")
    else:
        snap_age = (now_ms - st["ts_ms"]) / 1000
        running = bool(st.get("running")) and _alive(st.get("pid")) and snap_age < 30
        up = _dur((now_ms - parse_iso(st["started"])) / 1000) if running else "-"
        out.append(f"BOT      {'RUNNING' if running else 'NOT RUNNING'}  pid {st.get('pid')}  up {up}  mode {st.get('mode')}  "
                   f"snapshot {snap_age:.0f}s old ({st['ts']})")
        ticks = st.get("ticks", {})
        last = max((t.get("event_ms") or 0) for t in ticks.values()) if ticks else 0
        out.append(f"FEED     last tick {_age(last, now_ms)} ago   " + "  ".join(
            f"{s} {t['bid']}/{t['ask']} ({_age(t.get('event_ms'), now_ms)})" for s, t in ticks.items()))
        feed = st.get("feed") or {}
        conns = "  ".join(f"{k}:{'up' if v.get('connected') else 'DOWN'} (connects {v.get('connects')}, errors {v.get('errors')})"
                          for k, v in feed.items() if isinstance(v, dict))
        if conns:
            out.append(f"         {conns}")
    halt = _load(state_dir / "HALT")
    sh = (st or {}).get("shield", {})
    halts = (halt or {}).get("halts") or sh.get("halts") or []
    if (state_dir / "HALT").exists() and not halts:
        halts = [{"kind": "manual", "scope": "account", "since": "?", "needs_human": True,
                  "reason": "state/HALT exists (not written by Shield)"}]
    if halts:
        out.append(f"SHIELD   HALTED ({len(halts)})" + ("  ** needs a human: delete state/HALT **"
                                                        if any(h.get("needs_human") for h in halts) else "  (clears on its own)"))
        for h in halts:
            out.append(f"         - {h.get('kind')} [{h.get('scope')}] since {h.get('since')}: {h.get('reason')}")
    else:
        out.append("SHIELD   ok (no halt)")
    if (state_dir / "KILL").exists():
        out.append("         KILL file present: trading stays halted until state/KILL and state/HALT are removed")
    for key, fl in sorted((sh.get("flags") or {}).items()):
        out.append(f"         flag {fl.get('kind')} {fl.get('setup') or ''}/{fl.get('symbol') or ''}: {fl.get('reason')}"[:260])
    if st is not None:
        for sym, h in (st.get("history") or {}).items():
            gap = "contiguous" if not h["missing_bars"] else (f"{h['missing_bars']} bar(s) MISSING "
                                                              f"{h['first_missing']} .. {h['last_missing']} -> WARMING")
            out.append(f"HISTORY  {sym} last bar {h['last_bar']}  own bars {h['own_bars']}  {gap}")
        out.append("BOOKS    setup              role       strategy              equity    today    total  trades  W/L   open")
        positions = []
        for bid, b in (st.get("books") or {}).items():
            day = "     -" if b.get("day_pnl") is None else f"{b['day_pnl']:+8.2f}"
            blocks = (sh.get("books") or {}).get(bid, {}).get("blocks_today", 0)
            out.append(f"         {bid[:18]:<18} {(b['role'] if b['active'] else 'retired')[:10]:<10} {b['strategy'][:20]:<20} "
                       f"{b['mark_equity']:8.2f} {day} {b['total_pnl']:+8.2f}  {b['trades']:>5}  {b['wins']}/{b['losses']}   "
                       f"{len(b['positions'])} pos, {len(b['orders'])} ord" + (f", {blocks} block(s) today" if blocks else ""))
            for p in b["positions"]:
                q = (st.get("ticks") or {}).get(p["symbol"], {})
                px = q.get("bid") if p["side"] > 0 else q.get("ask")
                upnl = p["side"] * p["qty"] * (px - p["entry_price"]) if px else float("nan")
                positions.append(f"         {bid} {p['symbol']} {'long' if p['side'] > 0 else 'short'} {p['qty']} @ "
                                 f"{p['entry_price']:.4f}  stop {p['stop']}  target {p['target']}  uPnL {upnl:+.2f}"
                                 + (f"  FLATTEN PENDING ({p['flatten_reason']})" if p.get("flatten_reason") else ""))
            for o in b["orders"]:
                positions.append(f"         {bid} {o['symbol']} resting {'buy' if o['side'] > 0 else 'sell'} {o['qty']} @ "
                                 f"{o['limit']}  stop {o['stop']}  expires {iso(o['expires_ts'])}")
        out.append("OPEN     " + ("none" if not positions else ""))
        out.extend(positions)
        if not st.get("filters_verified", False):
            out.append("NOTE     exchange tick/step/min-notional are unverified assumptions (bot/instruments.py)")
    dec = tail_decisions(log_dir / "decisions.jsonl")
    shown = dec if show_all else [d for d in dec if d.get("kind") not in ROUTINE]
    hour_ago = iso(now_ms - 3_600_000)
    routine = {}
    for d in dec:
        if d.get("kind") in ROUTINE and d.get("ts", "") >= hour_ago:
            routine[d["kind"]] = routine.get(d["kind"], 0) + 1
    hb = [d for d in dec if d.get("kind") == "heartbeat"]
    out.append(f"LAST {n} DECISIONS" + ("" if show_all else f"  (routine lines in the last hour: {routine or 'none'}; "
                                                              f"last heartbeat {hb[-1]['ts'] if hb else 'never'}; --all shows them)"))
    for d in shown[-n:]:
        who = "/".join(x for x in (d.get("setup"), d.get("symbol")) if x)
        out.append(f"  {d.get('ts', '')[:19]}Z {d.get('kind', ''):<12} {who:<16} {str(d.get('reason', ''))[:200]}")
    if not shown:
        out.append("  (none)")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include heartbeat/bar/eval lines in the decision list")
    ap.add_argument("--state-dir", default=str(HERE / "state"))
    ap.add_argument("--log-dir", default=str(HERE / "logs"))
    ap.add_argument("-n", type=int, default=20)
    a = ap.parse_args(argv)
    print(render(Path(a.state_dir), Path(a.log_dir), a.all, n=a.n))
    return 0


if __name__ == "__main__":
    sys.exit(main())
