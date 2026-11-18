"""bot/snapshot.py: durable state out to paper_state/ and back, in both directions."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

import pytest

from bot import snapshot
from bot.journal import read_decisions
from bot.util import HOUR_MS

from .conftest import ROOT, T0
from .harness import Bot

B = "BTCUSDT"


def session(tmp_path, **cfg_over):
    """A book with a closed losing trade, an open position, a resting order, a loss-streak halt on a
    second book, own bars, and more than 2,000 decision lines."""
    setups = [{"id": "leader", "role": "leader", "strategy": "script", "symbols": [B],
               "params": {"long_hours": [0, 1]}},
              {"id": "runner", "strategy": "script", "symbols": ["ETHUSDT"],
               "params": {"long_hours": [1], "limit_pct": 0.002, "entry_valid": 5}}]
    bot = Bot(tmp_path, setups=setups, **cfg_over).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.close_bar(T0)                                             # leader long BTC
    bot.tick(B, 59300.0, at=T0 + HOUR_MS + 600_000)               # stopped out: one closed trade
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)                                   # leader long again, runner rests a limit on ETH
    assert bot.pos(B) is not None and bot.order("ETHUSDT", "runner") is not None
    from .test_shield import trade
    for i in range(1, 4):
        bot.engine.shield.on_trade_closed(trade(i, -5.0, setup="runner", symbol="ETHUSDT"), bot.now)
    assert [h["kind"] for h in bot.engine.shield.halts] == ["loss_streak"]
    assert bot.order("ETHUSDT", "runner") is None                 # the halt cancelled it
    for i in range(2100):
        bot.engine.journal.decision("eval", f"filler line {i}", ts=bot.now)
    bot.timer(at=bot.now + 2000)
    return bot


def wipe(bot):
    shutil.rmtree(bot.cfg.state_dir)
    shutil.rmtree(bot.cfg.log_dir)


def test_snapshot_holds_exactly_the_durable_state(tmp_path):
    bot = session(tmp_path)
    dest = tmp_path / "paper_state"
    m = snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, dest, now_ms=bot.now)
    names = set(m["files"])
    assert names == {"state/books.json", "state/shield.json", "state/HALT", "state/market.json", "state/status.json",
                     "state/bars/BTCUSDT_1h.csv", "state/bars/ETHUSDT_1h.csv", "state/bars/SOLUSDT_1h.csv",
                     "logs/trades.csv", "logs/decisions.jsonl"}
    on_disk = {str(p.relative_to(dest)) for p in dest.rglob("*") if p.is_file()}
    assert on_disk == names | {"MANIFEST.json"}                   # nothing else: no lock, pid, log, archive
    assert m["files"]["logs/decisions.jsonl"]["lines"] == 2000
    kept = (dest / "logs" / "decisions.jsonl").read_text().splitlines()
    assert len(kept) == 2000 and json.loads(kept[-1])["reason"] == read_decisions(
        bot.cfg.log_dir / "decisions.jsonl")[-1]["reason"]
    assert (dest / "state" / "books.json").read_bytes() == (bot.cfg.state_dir / "books.json").read_bytes()
    assert not list(tmp_path.glob("paper_state.*"))               # temp and old directories are gone
    assert "book leader: equity" in snapshot.describe(dest) and "1 position(s)" in snapshot.describe(dest)


def test_restore_into_an_empty_bot_resumes_positions_stops_halts_bars_and_ledger(tmp_path):
    bot = session(tmp_path)
    dest = tmp_path / "paper_state"
    pos = bot.pos(B)
    equity = bot.engine.broker.equity("leader")
    snap_time = bot.now
    snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, dest, now_ms=bot.now)
    bot.kill()
    wipe(bot)                                                     # the container is gone
    m = snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    assert len(m["files"]) == 10
    bot.restart(at=snap_time + 40_000)                            # back within the stale-feed limit
    p = bot.pos(B)
    assert (p.id, p.qty, p.entry_price, p.stop) == (pos.id, pos.qty, pos.entry_price, pos.stop)
    assert bot.engine.broker.equity("leader") == pytest.approx(equity)
    assert [(h["kind"], h["scope"]) for h in bot.engine.shield.halts] == [("loss_streak", "runner")]   # not "cleared"
    assert len(bot.engine.history.frame(B)) == 602                # the two bars the bot recorded itself
    assert [t["exit_reason"] for t in bot.trades()] == ["stop"] and bot.trades()[0]["id"] == "leader-00001"
    assert len(read_decisions(bot.cfg.log_dir / "decisions.jsonl")) >= 2000
    bot.tick(B, 59000.0, at=snap_time + 45_000)                   # the restored stop works
    assert bot.pos(B) is None and len(bot.trades()) == 2
    bot.ticks(at=T0 + 3 * HOUR_MS - 200)
    bot.close_bar(T0 + 2 * HOUR_MS)
    assert bot.engine.history.ready(B, "1h", 50, T0 + 2 * HOUR_MS)[0]     # history is contiguous across the restore


def test_restore_after_a_long_outage_flattens_at_the_first_quote(tmp_path):
    bot = session(tmp_path)
    dest = tmp_path / "paper_state"
    snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, dest, now_ms=bot.now)
    snap_time = bot.now
    bot.kill()
    wipe(bot)
    snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    bot.restart(at=snap_time + 3 * HOUR_MS)                       # hours later, in a new container
    kinds = sorted(h["kind"] for h in bot.engine.shield.halts)
    assert kinds == ["loss_streak", "stale_feed"]
    assert "bot was down for 10800 s" in [h for h in bot.engine.shield.halts if h["kind"] == "stale_feed"][0]["reason"]
    assert bot.pos(B).flatten_reason == "halt:stale_feed"
    bot.tick(B, 60100.0, at=bot.now + 2000)
    assert bot.pos(B) is None and bot.trades()[-1]["exit_reason"] == "halt:stale_feed"


def test_restore_refuses_when_state_or_logs_are_not_empty_and_writes_nothing(tmp_path):
    bot = session(tmp_path)
    dest = tmp_path / "paper_state"
    snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, dest, now_ms=bot.now)
    before = (bot.cfg.state_dir / "books.json").read_bytes()
    with pytest.raises(snapshot.SnapshotError, match="not empty"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    assert (bot.cfg.state_dir / "books.json").read_bytes() == before
    wipe(bot)
    bot.cfg.log_dir.mkdir()
    (bot.cfg.log_dir / "bot.log").write_text("something ran here\n")
    with pytest.raises(snapshot.SnapshotError, match="logs"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    assert not bot.cfg.state_dir.exists()
    (bot.cfg.log_dir / "bot.log").unlink()
    (bot.cfg.state_dir / "bars").mkdir(parents=True)              # empty directories do not count
    snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    assert (bot.cfg.state_dir / "books.json").read_bytes() == before


def test_restore_verifies_the_snapshot_before_writing(tmp_path):
    bot = session(tmp_path)
    dest = tmp_path / "paper_state"
    snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, dest, now_ms=bot.now)
    wipe(bot)
    good = (dest / "state" / "books.json").read_text()
    (dest / "state" / "books.json").write_text(good.replace('"cash"', '"cash" '))        # edited by hand / half-committed
    with pytest.raises(snapshot.SnapshotError, match="checksum"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    (dest / "state" / "books.json").write_text(good)
    (dest / "logs" / "trades.csv").unlink()
    with pytest.raises(snapshot.SnapshotError, match="incomplete"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    man = json.loads((dest / "MANIFEST.json").read_text())
    man["files"].pop("logs/trades.csv")
    man["files"]["state/GO_LIVE_CONFIRMED"] = {"sha256": "x"}
    (dest / "MANIFEST.json").write_text(json.dumps(man))
    with pytest.raises(snapshot.SnapshotError, match="does not belong"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    man["files"] = {"state/bars/../../../evil_1h.csv": {"sha256": "x"}}
    (dest / "MANIFEST.json").write_text(json.dumps(man))
    with pytest.raises(snapshot.SnapshotError, match="does not belong"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    (dest / "MANIFEST.json").write_text("{not json")
    with pytest.raises(snapshot.SnapshotError, match="manifest"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, dest)
    with pytest.raises(snapshot.SnapshotError, match="no snapshot"):
        snapshot.restore(bot.cfg.state_dir, bot.cfg.log_dir, tmp_path / "nowhere")
    assert not bot.cfg.state_dir.exists() and not bot.cfg.log_dir.exists()               # nothing was written


def test_snapshot_never_carries_secrets_or_the_go_live_file(tmp_path):
    bot = session(tmp_path)
    dest = tmp_path / "paper_state"
    (bot.cfg.state_dir / "GO_LIVE_CONFIRMED").write_text("owner said go\n")
    (bot.cfg.state_dir / "bot.lock").write_text("")
    (bot.cfg.log_dir / "bot.log").write_text("log\n")
    m = snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, dest, now_ms=bot.now)
    assert not any("GO_LIVE" in n or "lock" in n or "bot.log" in n for n in m["files"])
    # a credential that somehow got into a state file stops the snapshot and leaves the old one in place
    old_manifest = (dest / "MANIFEST.json").read_text()
    (bot.cfg.state_dir / "status.json").write_text(json.dumps({"oops": "sk-live-SECRETVALUE123"}))
    with pytest.raises(snapshot.SnapshotError, match="credential"):
        snapshot.take(bot.cfg.state_dir, bot.cfg.log_dir, dest, now_ms=bot.now + 1,
                      env={"BINANCE_API_SECRET": "sk-live-SECRETVALUE123"})
    assert (dest / "MANIFEST.json").read_text() == old_manifest and not list(tmp_path.glob("paper_state.*"))


def test_snapshot_needs_books_and_drops_a_torn_decision_line(tmp_path):
    with pytest.raises(snapshot.SnapshotError, match="nothing to snapshot"):
        snapshot.take(tmp_path / "state", tmp_path / "logs", tmp_path / "ps")
    (tmp_path / "state").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "state" / "books.json").write_text('{"version": 1, "books": {}}')
    (tmp_path / "logs" / "decisions.jsonl").write_text('{"kind": "a"}\n{"kind": "b"}\n{"kind": "c", "rea')
    m = snapshot.take(tmp_path / "state", tmp_path / "logs", tmp_path / "ps")
    assert m["files"]["logs/decisions.jsonl"]["lines"] == 2
    assert (tmp_path / "ps" / "logs" / "decisions.jsonl").read_text() == '{"kind": "a"}\n{"kind": "b"}\n'
    assert snapshot.describe(tmp_path / "nope").startswith("no snapshot")
    # a long log is read from its end only, and the cut never lands inside a line
    big = tmp_path / "logs" / "decisions.jsonl"
    big.write_text("".join(f'{{"kind": "eval", "n": {i}, "pad": "{"x" * 50}"}}\n' for i in range(5000)))
    out = tmp_path / "tail.jsonl"
    assert snapshot._tail_lines(big, out, 2000, chunk=4096) == 2000
    got = [json.loads(x)["n"] for x in out.read_text().splitlines()]
    assert got == list(range(3000, 5000))
    assert snapshot._tail_lines(big, out, 9999, chunk=4096) == 5000
    (tmp_path / "empty.jsonl").write_text("")
    assert snapshot._tail_lines(tmp_path / "empty.jsonl", out, 10) == 0 and out.read_text() == ""


def test_command_line_both_directions(tmp_path):
    bot = session(tmp_path)
    dest = tmp_path / "paper_state"
    args = ["--state-dir", str(bot.cfg.state_dir), "--log-dir", str(bot.cfg.log_dir), "--dir", str(dest)]
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "bot" / "snapshot.py"), *a, *args],
                                    capture_output=True, text=True, timeout=60)
    r = run()
    assert r.returncode == 0 and "snapshot written" in r.stdout
    r = run("--show")
    assert r.returncode == 0 and "state/books.json" in r.stdout and "book leader" in r.stdout
    r = run("--restore")
    assert r.returncode == 1 and "not empty" in r.stderr
    wipe(bot)
    r = run("--restore")
    assert r.returncode == 0 and "restored 10 file(s)" in r.stdout and "outage" in r.stdout
    assert (bot.cfg.state_dir / "books.json").exists() and (bot.cfg.log_dir / "trades.csv").exists()
    assert snapshot.main(["--show", "--dir", str(dest)]) == 0
    assert snapshot.main(["--restore", *args]) == 1


def test_engine_keeps_the_snapshot_fresh_and_a_failure_never_stops_trading(tmp_path):
    dest = tmp_path / "ps"
    bot = Bot(tmp_path, setups=[{"id": "leader", "strategy": "always", "symbols": [B]}],
              engine={"snapshot_dir": str(dest), "snapshot_min": 5}).start()
    bot.ticks(at=T0 + HOUR_MS - 200)
    bot.timer()
    first = json.loads((dest / "MANIFEST.json").read_text())      # a first snapshot right after start
    assert json.loads((dest / "state" / "books.json").read_text())["books"]["leader"]["positions"] == {}
    bot.ticks(at=bot.now + 20)
    bot.timer()
    assert json.loads((dest / "MANIFEST.json").read_text()) == first          # not again within the 5 minutes
    bot.close_bar(T0)                                             # bar close + a fill
    bot.ticks(at=bot.now + 6000)
    bot.timer()
    m1 = json.loads((dest / "MANIFEST.json").read_text())
    books = json.loads((dest / "state" / "books.json").read_text())["books"]["leader"]
    assert "BTCUSDT" in books["positions"] and "state/bars/BTCUSDT_1h.csv" in m1["files"]
    bot.tick(B, 59000.0, at=bot.now + 60_000)                     # stop-out: snapshot follows within seconds
    bot.ticks(at=bot.now + 6000, BTCUSDT=59000.0)
    bot.timer()
    books = json.loads((dest / "state" / "books.json").read_text())["books"]["leader"]
    assert books["positions"] == {} and books["n_trades"] == 1
    assert "logs/trades.csv" in json.loads((dest / "MANIFEST.json").read_text())["files"]
    # the directory becomes unwritable: flagged once, trading goes on
    shutil.rmtree(dest)
    dest.write_text("a file where the directory should be")
    bot.engine.snapshot_dir = dest / "sub"
    bot.ticks(at=T0 + 2 * HOUR_MS - 200)
    bot.close_bar(T0 + HOUR_MS)
    bot.ticks(at=bot.now + 6000)
    bot.timer()
    assert bot.pos(B) is not None and bot.halts() == []
    assert any(k.startswith("snapshot_error") for k in bot.engine.shield.status()["flags"])
    assert len([d for d in bot.decisions("flag") if "snapshot" in d["reason"]]) == 1
    bot.engine.snapshot_dir = tmp_path / "ps2"                    # and it recovers
    bot.engine.shutdown(bot.now + 1000)
    assert (tmp_path / "ps2" / "MANIFEST.json").exists()
    assert json.loads((tmp_path / "ps2" / "state" / "status.json").read_text())["running"] is False
