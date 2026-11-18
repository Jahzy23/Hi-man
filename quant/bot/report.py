#!/usr/bin/env python3
"""Morning brief as markdown.   python3 bot/report.py --hours 24

Trades, P&L per setup, Shield halts/blocks/flags, data gaps and uptime for the window, from
logs/trades.csv, logs/decisions.jsonl and state/status.json. Read-only.
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))

from bot.journal import read_decisions, read_trades  # noqa: E402
from bot.status import _alive, _load  # noqa: E402
from bot.util import iso  # noqa: E402


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def render(state_dir: Path, log_dir: Path, hours: float = 24.0, now_ms: int | None = None) -> str:
    now_ms = int(time.time() * 1000) if now_ms is None else now_ms
    since = now_ms - int(hours * 3_600_000)
    since_iso = iso(since)
    st = _load(state_dir / "status.json") or {}
    dec = read_decisions(log_dir / "decisions.jsonl", since_ms=since)
    trades = [t for t in read_trades(log_dir / "trades.csv") if t.get("exit_time", "") >= since_iso]
    running = bool(st.get("running")) and _alive(st.get("pid")) and (now_ms - st.get("ts_ms", 0)) < 30_000
    L = [f"# Paper bot report - last {hours:g} h", "",
         f"Window {since_iso[:16]}Z to {iso(now_ms)[:16]}Z (UTC). Mode `{st.get('mode', '?')}`. "
         f"Bot is {'running' if running else '**not running**'}"
         + (f" (last snapshot {st.get('ts')})." if st else " (never started)."), ""]

    # ---- P&L per setup
    L += ["## P&L per setup", "",
          "| setup | role | strategy | trades | wins | losses | net P&L (window) | fees | funding | equity now | "
          "today | total P&L |", "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    per = defaultdict(list)
    for t in trades:
        per[t["setup"]].append(t)
    books = st.get("books", {})
    for sid in sorted(set(books) | set(per), key=lambda s: (0 if books.get(s, {}).get("role") == "leader" else 1, s)):
        b, ts = books.get(sid, {}), per.get(sid, [])
        net = sum(_f(t["net"]) for t in ts)
        wins = sum(1 for t in ts if _f(t["net"]) >= 0)
        day = b.get("day_pnl")
        L.append(f"| {sid} | {b.get('role', '?') if b.get('active', True) else 'retired'} | {b.get('strategy', '?')} | "
                 f"{len(ts)} | {wins} | {len(ts) - wins} | {net:+.2f} | {sum(_f(t['fees']) for t in ts):.2f} | "
                 f"{sum(_f(t['funding']) for t in ts):+.2f} | "
                 f"{'%.2f' % b['mark_equity'] if 'mark_equity' in b else '?'} | "
                 f"{'%+.2f' % day if day is not None else '-'} | "
                 f"{'%+.2f' % b['total_pnl'] if 'total_pnl' in b else '?'} |")
    if not books and not per:
        L.append("| (no books yet) | | | | | | | | | | | |")
    L.append("")
    open_pos = [(sid, p) for sid, b in books.items() for p in b.get("positions", [])]
    open_ord = [(sid, o) for sid, b in books.items() for o in b.get("orders", [])]
    L.append(f"Open now: {len(open_pos)} position(s), {len(open_ord)} resting order(s)."
             + ("".join(f"\n- {sid} {p['symbol']} {'long' if p['side'] > 0 else 'short'} {p['qty']} @ "
                        f"{p['entry_price']:.4f}, stop {p['stop']}, target {p['target']}" for sid, p in open_pos)))
    L.append("")

    # ---- trades
    L += ["## Trades closed in the window", ""]
    if trades:
        L += ["| id | symbol | side | qty | entry (UTC) | entry px | exit (UTC) | exit px | exit reason | net | R | flags |",
              "|---|---|---|---:|---|---:|---|---:|---|---:|---:|---|"]
        for t in trades:
            L.append(f"| {t['id']} | {t['symbol']} | {'long' if _f(t['side']) > 0 else 'short'} | {t['qty']} | "
                     f"{t['entry_time'][:16]} | {_f(t['entry_price']):.4f} | {t['exit_time'][:16]} | "
                     f"{_f(t['exit_price']):.4f} | {t['exit_reason']} | {_f(t['net']):+.2f} | {t.get('r_multiple', '')} | "
                     f"{t.get('flags', '')} |")
    else:
        L.append("None.")
    L.append("")

    # ---- shield
    halts = [d for d in dec if d["kind"] == "halt"]
    clears = [d for d in dec if d["kind"] == "halt_clear"]
    blocks = [d for d in dec if d["kind"] == "block"]
    active = (st.get("shield") or {}).get("halts", [])
    L += ["## Shield", ""]
    L.append(f"- Active halts now: {len(active)}" + "".join(
        f"\n  - **{h['kind']}** [{h['scope']}] since {h['since']}{' - NEEDS A HUMAN' if h.get('needs_human') else ''}: "
        f"{h['reason']}" for h in active))
    L.append(f"- Halts raised in the window: {len(halts)}; cleared: {len(clears)}")
    for d in halts + clears:
        L.append(f"  - {d['ts'][:19]}Z {d['reason']}")
    L.append(f"- Orders blocked in the window: {len(blocks)}")
    by = Counter(_block_class(d["reason"]) for d in blocks)
    for why, n in by.most_common():
        L.append(f"  - {n} x {why}")
    for d in blocks[-10:]:
        L.append(f"  - {d['ts'][:19]}Z {d.get('setup')}/{d.get('symbol')}: {d['reason'][:220]}")
    flags = (st.get("shield") or {}).get("flags", {})
    L.append(f"- Flags now: {len(flags)}")
    for fl in flags.values():
        L.append(f"  - {fl.get('kind')} {fl.get('setup') or ''}/{fl.get('symbol') or ''} since {fl.get('since')}: "
                 f"{str(fl.get('reason'))[:220]}")
    flagged = [t for t in trades if t.get("flags")]
    if flagged:
        L.append(f"- Trades with caveats: " + ", ".join(f"{t['id']} ({t['flags']})" for t in flagged))
    L.append("")

    # ---- decisions / data
    kinds = Counter(d["kind"] for d in dec)
    hb_expected = max(1, int(hours * 12))
    L += ["## Decisions and data", "",
          f"- Decision lines: {len(dec)} " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())),
          f"- Heartbeats: {kinds.get('heartbeat', 0)} of about {hb_expected} expected "
          f"(~{min(100.0, 100.0 * kinds.get('heartbeat', 0) / hb_expected):.0f}% of the window covered)",
          f"- Starts: {kinds.get('start', 0) and sum(1 for d in dec if d['kind'] == 'start' and not d.get('setup'))}, "
          f"stops: {kinds.get('stop', 0)}, feed connection events: {kinds.get('feed', 0)}"]
    for kind, label in (("warming", "WARMING (history gap, no decision made)"),
                        ("refuse", "REFUSED (strategy reads data that is not available live)")):
        c = Counter((d.get("setup"), d.get("symbol")) for d in dec if d["kind"] == kind)
        if c:
            L.append(f"- {label}: " + ", ".join(f"{s}/{y} x{n}" for (s, y), n in sorted(c.items())))
            last = [d for d in dec if d["kind"] == kind][-1]
            L.append(f"  - latest: {last['reason'][:260]}")
    for sym, h in (st.get("history") or {}).items():
        L.append(f"- History {sym}: last bar {h['last_bar']}, own bars {h['own_bars']}, missing {h['missing_bars']}"
                 + (f" ({h['first_missing']} .. {h['last_missing']})" if h["missing_bars"] else "")
                 + (f", **{h['archive_mismatches']} live bar(s) differ from the archive**" if h.get("archive_mismatches") else ""))
    sig = [d for d in dec if d["kind"] == "signal"]
    L.append(f"- Signals seen: {len(sig)}; fills: {kinds.get('fill', 0)}; exits: {kinds.get('exit', 0)}; "
             f"cancels: {kinds.get('cancel', 0)}; funding events: {kinds.get('funding', 0)}")
    L.append("")
    L += ["## Standing caveats", "",
          "- Paper only. Fills are simulated against best bid/ask (see bot/BUILD_LOG.md for the exact rules).",
          f"- Exchange tick/step/min-notional verified against the exchange: "
          f"{'yes' if st.get('filters_verified') else 'NO (hard-coded assumptions in bot/instruments.py)'}.",
          "- Symbols in scope (BTCUSDT, ETHUSDT, SOLUSDT) were never delisted; this is survivorship-free only "
          "because the universe was fixed in advance, not because it was tested.", ""]
    return "\n".join(L)


def _block_class(reason: str) -> str:
    body = reason.split(": ", 1)[1] if ": " in reason else reason
    return body.split(":")[0].split(" (")[0][:60]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24.0)
    ap.add_argument("--state-dir", default=str(HERE / "state"))
    ap.add_argument("--log-dir", default=str(HERE / "logs"))
    a = ap.parse_args(argv)
    print(render(Path(a.state_dir), Path(a.log_dir), a.hours))
    return 0


if __name__ == "__main__":
    sys.exit(main())
