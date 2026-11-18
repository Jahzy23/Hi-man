"""The supervisor loop, driven with a fake bot command (BOT_CMD) so no real bot or network is involved."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from .conftest import ROOT

RUN = str(ROOT / "bot" / "run.sh")

FAKE = r'''
import json, os, sys, time, signal
state = os.environ["BOT_STATE_DIR"]
plan = os.environ["FAKE_PLAN"].split(",")
cnt_path = os.path.join(state, "runs")
n = int(open(cnt_path).read()) if os.path.exists(cnt_path) else 0
open(cnt_path, "w").write(str(n + 1))
step = plan[min(n, len(plan) - 1)]
print(f"fake bot run {n + 1}: {step}", flush=True)
if step == "halt":
    json.dump({"halted": True, "blocks_restart": True, "needs_human": True,
               "halts": [{"kind": "exception", "reason": "boom in fake bot"}]}, open(os.path.join(state, "HALT"), "w"))
    sys.exit(3)
if step == "softhalt":
    json.dump({"halted": True, "blocks_restart": False, "needs_human": False,
               "halts": [{"kind": "stale_feed", "reason": "auto-clearing"}]}, open(os.path.join(state, "HALT"), "w"))
    sys.exit(1)
if step == "kill9":
    os.kill(os.getpid(), signal.SIGKILL)
if step == "serve":
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
    time.sleep(60)
sys.exit(int(step))
'''


def env_for(tmp_path, plan):
    fake = tmp_path / "fake_bot.py"
    fake.write_text(FAKE)
    e = dict(os.environ, BOT_STATE_DIR=str(tmp_path / "state"), BOT_LOG_DIR=str(tmp_path / "logs"),
             BOT_CMD=f"{sys.executable} {fake}", FAKE_PLAN=plan, BOT_BACKOFF_START="1", BOT_BACKOFF_MAX="2",
             BOT_HALT_POLL="1")
    return e


def sup_log(tmp_path):
    p = tmp_path / "logs" / "supervisor.log"
    return p.read_text() if p.exists() else ""


def wait_for(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def test_crashes_are_restarted_with_backoff_and_the_reason_is_logged(tmp_path):
    r = subprocess.run([RUN, "supervise"], env=env_for(tmp_path, "1,kill9,0"), timeout=60, capture_output=True, text=True)
    log = sup_log(tmp_path)
    assert r.returncode == 0, log
    assert (tmp_path / "state" / "runs").read_text() == "3"
    assert "bot CRASHED (exit code 1)" in log and "fake bot run 1: 1" in log            # crash reason + last output
    assert "bot CRASHED (killed by signal 9)" in log
    assert "restarting in 1s" in log and "restarting in 2s" in log                        # exponential backoff
    assert "bot exited cleanly" in log and not (tmp_path / "state" / "supervisor.pid").exists()


def test_never_restarts_past_a_halt_that_needs_a_human(tmp_path):
    p = subprocess.Popen([RUN, "supervise"], env=env_for(tmp_path, "halt,0"))
    try:
        assert wait_for(lambda: "NOT restarting" in sup_log(tmp_path))
        time.sleep(3.5)                                           # several poll intervals: still only one run
        assert (tmp_path / "state" / "runs").read_text() == "1"
        log = sup_log(tmp_path)
        assert "bot halted itself (exit 3)" in log and "exception: boom in fake bot" in log
        assert log.count("NOT restarting") == 1 and p.poll() is None
        (tmp_path / "state" / "HALT").unlink()                    # the human resumes
        assert wait_for(lambda: p.poll() is not None)
        assert p.returncode == 0 and (tmp_path / "state" / "runs").read_text() == "2"
        assert "HALT was removed: resuming" in sup_log(tmp_path)
    finally:
        if p.poll() is None:
            p.kill()


def test_a_self_clearing_halt_does_not_stop_the_supervisor(tmp_path):
    r = subprocess.run([RUN, "supervise"], env=env_for(tmp_path, "softhalt,0"), timeout=60)
    assert r.returncode == 0 and (tmp_path / "state" / "runs").read_text() == "2"
    assert "NOT restarting" not in sup_log(tmp_path)


def test_exit_codes_that_must_not_loop(tmp_path):
    for code, text in (("2", "refused to run"), ("4", "another bot instance")):
        d = tmp_path / code
        d.mkdir()
        r = subprocess.run([RUN, "supervise"], env=env_for(d, code), timeout=30)
        assert r.returncode == int(code) and (d / "state" / "runs").read_text() == "1" and text in sup_log(d)


def test_start_stop_status_commands(tmp_path):
    e = env_for(tmp_path, "serve")
    try:
        out = subprocess.run([RUN, "start"], env=e, capture_output=True, text=True, timeout=30).stdout
        assert "supervisor started" in out
        assert wait_for(lambda: (tmp_path / "state" / "runs").exists())
        pid = int((tmp_path / "state" / "supervisor.pid").read_text())
        os.kill(pid, 0)
        assert "already running" in subprocess.run([RUN, "start"], env=e, capture_output=True, text=True, timeout=30).stdout
        out = subprocess.run([RUN, "stop"], env=e, capture_output=True, text=True, timeout=40).stdout
        assert "stopped" in out
        assert wait_for(lambda: not _alive(pid)) and "bot stopped on request" in sup_log(tmp_path)
        assert (tmp_path / "state" / "runs").read_text() == "1"   # stop is not followed by a restart
        assert "not running" in subprocess.run([RUN, "stop"], env=e, capture_output=True, text=True, timeout=30).stdout
        st = subprocess.run([RUN, "status", "--state-dir", str(tmp_path / "state"), "--log-dir", str(tmp_path / "logs")],
                            env=e, capture_output=True, text=True, timeout=30)
        assert st.returncode == 0 and "never started" in st.stdout
        assert subprocess.run([RUN, "bogus"], env=e, capture_output=True, text=True).returncode == 64
    finally:
        try:
            os.kill(int((tmp_path / "state" / "supervisor.pid").read_text()), signal.SIGKILL)
        except Exception:
            pass


def test_start_next_to_an_unrestored_snapshot_makes_the_human_choose(tmp_path):
    """bot/state is gone (container reclaimed) but paper_state/ has a snapshot: no silent fresh start."""
    import json
    e = env_for(tmp_path, "serve")
    snap = tmp_path / "paper_state"
    src_state, src_logs = tmp_path / "old_state", tmp_path / "old_logs"
    src_state.mkdir()
    src_logs.mkdir()
    (src_state / "books.json").write_text(json.dumps({"version": 1, "books": {}}))
    from bot import snapshot
    snapshot.take(src_state, src_logs, snap)
    e["BOT_SNAPSHOT_DIR"] = str(snap)
    try:
        r = subprocess.run([RUN, "start"], env=e, capture_output=True, text=True, timeout=30)
        assert r.returncode == 3 and "start --restore" in r.stdout and "start --fresh" in r.stdout
        assert "snapshot at" in r.stdout and not (tmp_path / "state" / "runs").exists()      # nothing was started
        r = subprocess.run([RUN, "start", "--restore"], env=e, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and "restored 1 file(s)" in r.stdout and "supervisor started" in r.stdout
        assert (tmp_path / "state" / "books.json").exists()
        assert wait_for(lambda: (tmp_path / "state" / "runs").exists())
        subprocess.run([RUN, "stop"], env=e, capture_output=True, text=True, timeout=40)
        # with books in place the snapshot is no longer a question
        d2 = tmp_path / "second"
        d2.mkdir()
        e2 = env_for(d2, "serve")
        e2["BOT_SNAPSHOT_DIR"] = str(snap)
        r = subprocess.run([RUN, "start", "--fresh"], env=e2, capture_output=True, text=True, timeout=30)
        assert r.returncode == 0 and "new books" in r.stdout and "supervisor started" in r.stdout
        subprocess.run([RUN, "stop"], env=e2, capture_output=True, text=True, timeout=40)
    finally:
        for d in (tmp_path, tmp_path / "second"):
            try:
                os.kill(int((d / "state" / "supervisor.pid").read_text()), signal.SIGKILL)
            except Exception:
                pass


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
