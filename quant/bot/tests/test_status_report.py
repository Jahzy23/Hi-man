"""status.py and report.py read the same files the bot writes; exercised on a synthetic session."""
from __future__ import annotations

import os
import subprocess
import sys

from bot import report, status
from bot.util import HOUR_MS

from .conftest import ROOT, T0
from .harness import Bot

B = "BTCUSDT"


def session(tmp_path):
    """One winning trade, one stop-out, one blocked order, a stale-feed halt, an open position."""
    setups = [{"id": "leader", "role": "leader", "strategy": "always", "symbols": [B]},
              {"id": "runner", "strategy": "always", "params": {"stop_pct": 0.0}, "symbols": ["ETHUSDT"]},
              {"id": "oi", "strategy": "needs_oi", "symbols": ["SOLUSDT"]}]
    bot = Bot(tmp_path, setups=setups, now=T0 + 3 * HOUR_MS + 600_000).start()       # 3 bars missing -> WARMING
    bot.ticks(at=T0 + 4 * HOUR_MS - 200)
    bot.timer()
    bot.close_bar(T0 + 3 * HOUR_MS)
    return bot


def test_status_before_the_bot_ever_ran(tmp_path):
    out = status.render(tmp_path / "state", tmp_path / "logs")
    assert "never started" in out and "SHIELD   ok" in out and "(none)" in out


def test_status_shows_running_ticks_books_shield_and_decisions(tmp_path):
    bot = Bot(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "always", "symbols": [B]},
                                {"id": "runner", "strategy": "always", "params": {"stop_pct": 0.0}, "symbols": ["ETHUSDT"]}]).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.timer()
    bot.close_bar(T0)
    bot.tick(B, 60100.0, at=T0 + HOUR_MS + 6000)
    bot.engine.feed_stats = {"public": {"connected": True, "connects": 2, "errors": 1}, "queue": 0}
    bot.timer()
    out = status.render(bot.cfg.state_dir, bot.cfg.log_dir, now_ms=bot.now + 2000)
    assert f"RUNNING  pid {os.getpid()}" in out and "mode paper" in out
    assert "last tick 2.1s ago" in out and "BTCUSDT 60100.0/60100.1" in out and "public:up (connects 2, errors 1)" in out
    assert "SHIELD   ok (no halt)" in out
    assert "leader BTCUSDT long 0.008 @ 60006.1000  stop 59400.0  target 61200.0  uPnL +0.75" in out
    line = [x for x in out.splitlines() if x.strip().startswith("leader ")][0]
    assert "always" in line and "1 pos, 0 ord" in line and "+0.51" in line        # today = mark-to-market incl. fee
    runner = [x for x in out.splitlines() if x.strip().startswith("runner ")][0]
    assert "1 block(s) today" in runner
    assert "HISTORY  BTCUSDT last bar 2026-11-18T00:00:00.000Z  own bars 1  contiguous" in out
    assert "BLOCKED runner:ETHUSDT" in out and "no stop" in out
    assert "heartbeat" not in out.split("LAST 20 DECISIONS")[1].split("\n", 1)[1]   # routine lines hidden by default
    assert "unverified assumptions" in out
    full = status.render(bot.cfg.state_dir, bot.cfg.log_dir, show_all=True, now_ms=bot.now + 2000)
    assert " bar " in full and "persisted" in full
    # the bot dies: the snapshot goes stale
    late = status.render(bot.cfg.state_dir, bot.cfg.log_dir, now_ms=bot.now + 120_000)
    assert "NOT RUNNING" in late and "last tick 120.1s ago" in late


def test_status_shows_halts_flags_and_kill(tmp_path):
    bot = session(tmp_path)
    (bot.cfg.state_dir / "KILL").write_text("stop")
    bot.timer(at=bot.now + 1000)
    out = status.render(bot.cfg.state_dir, bot.cfg.log_dir, now_ms=bot.now + 1000)
    assert "SHIELD   HALTED (1)  ** needs a human: delete state/HALT **" in out
    assert "- kill [account]" in out and "KILL file present" in out
    assert "flag warming leader/BTCUSDT: WARMING: history gap" in out
    assert "3 bar(s) MISSING 2026-11-18T00:00:00.000Z .. 2026-11-18T02:00:00.000Z -> WARMING" in out
    from bot import snapshot
    assert "SNAPSHOT none in" in status.render(bot.cfg.state_dir, bot.cfg.log_dir, now_ms=bot.now, snapshot_dir=tmp_path / "ps")
    snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, tmp_path / "ps", now_ms=bot.now - 125_000)
    line = [x for x in status.render(bot.cfg.state_dir, bot.cfg.log_dir, now_ms=bot.now,
                                     snapshot_dir=tmp_path / "ps").splitlines() if x.startswith("SNAPSHOT")][0]
    assert "2m05s ago" in line and "files" in line
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / "HALT").write_text("by hand")
    assert "state/HALT exists (not written by Shield)" in status.render(lone, lone, now_ms=bot.now)


def test_report_for_the_morning_brief(tmp_path):
    setups = [{"id": "leader", "role": "leader", "strategy": "always", "symbols": [B]},
              {"id": "runner", "strategy": "always", "params": {"stop_pct": 0.0}, "symbols": ["ETHUSDT"]},
              {"id": "oi", "strategy": "needs_oi", "symbols": ["SOLUSDT"]}]
    bot = Bot(tmp_path, setups=setups).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.timer()
    bot.close_bar(T0)                                             # leader long, runner blocked (no stop), oi refused
    bot.tick(B, 61200.0, at=T0 + HOUR_MS + 600_000)               # target
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)                                   # long again
    bot.tick(B, 59000.0, at=T0 + 2 * HOUR_MS + 60_000)            # stopped (gap)
    bot.ticks(at=T0 + 3 * HOUR_MS - 200)
    bot.close_bar(T0 + 2 * HOUR_MS)                               # third position stays open
    for s in range(1, 63):
        bot.tick(B, 60000.0, at=T0 + 3 * HOUR_MS + s * 1000)
        bot.tick("ETHUSDT", 1800.0)
        bot.timer()                                               # SOL silent -> stale feed halt
    md = report.render(bot.cfg.state_dir, bot.cfg.log_dir, hours=24, now_ms=bot.now + 1000)
    assert md.startswith("# Paper bot report - last 24 h")
    row = [x for x in md.splitlines() if x.startswith("| leader |")][0].split("|")
    assert row[2].strip() == "leader" and row[4].strip() == "3" and row[5].strip() == "1" and row[6].strip() == "2"
    assert "| leader-00001 | BTCUSDT | long | 0.008 |" in md and "| target |" in md and "| stop |" in md
    assert "halt:stale_feed" in md
    assert "Active halts now: 1" in md and "**stale_feed** [account]" in md
    assert "Orders blocked in the window: 3" in md and "x no stop" in md
    assert "REFUSED (strategy reads data that is not available live): oi/SOLUSDT x3" in md
    assert "Heartbeats: " in md and "Signals seen: " in md and "NO (hard-coded assumptions" in md
    assert "Open now: 0 position(s), 0 resting order(s)." in md
    # a window that excludes everything
    empty = report.render(bot.cfg.state_dir, bot.cfg.log_dir, hours=1, now_ms=bot.now + 48 * HOUR_MS)
    assert "None." in empty and "Orders blocked in the window: 0" in empty and "**not running**" in empty
    assert "never started" in report.render(tmp_path / "x", tmp_path / "y", hours=24)


def test_scripts_run_from_the_command_line(tmp_path):
    bot = session(tmp_path)
    for script, extra in (("status.py", ["--all", "-n", "5"]), ("report.py", ["--hours", "24"])):
        r = subprocess.run([sys.executable, str(ROOT / "bot" / script), "--state-dir", str(bot.cfg.state_dir),
                            "--log-dir", str(bot.cfg.log_dir), *extra], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert ("WARMING" in r.stdout) and ("leader" in r.stdout)
