"""Entry point: exit codes, locks, the paper-only gate, the exception -> HALT path."""
from __future__ import annotations

import fcntl
import json

import pytest

from bot import main as main_mod
from bot.engine import Engine
from bot.history import History
from bot.journal import read_decisions

from .conftest import RAW
from .harness import Bot


def toml_text(raw=RAW, mode="paper", strategy="always"):
    return f'''
mode = "{mode}"
symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
[account]
equity = 1000.0
risk_per_trade = 0.005
max_leverage = 3.0
[fees]
maker = 0.0002
taker = 0.0005
[slippage]
stop_multiplier = 2.0
[slippage.bps]
BTCUSDT = 1.0
ETHUSDT = 1.0
SOLUSDT = 2.0
[[setup]]
id = "leader"
role = "leader"
strategy = "{strategy}"
'''


class FakeFeed:
    def __init__(self, symbols):
        self.started = self.stopped = False

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def get(self, timeout):
        return None

    def stats(self):
        return {}


@pytest.fixture
def env(tmp_path):
    cfgp = tmp_path / "setup.toml"
    cfgp.write_text(toml_text())
    helper = Bot(tmp_path / "h")                                  # synthetic history + strategy files

    def engine_factory(cfg, refresher=None):
        hist = History(cfg.symbols, cfg.state_dir, base_loader=lambda s: helper.frames[s],
                       funding_archive=lambda s: helper.funding_archive)
        return Engine(cfg, history=hist, strategies_dir=helper.sdir, refresher=refresher)

    args = ["--config", str(cfgp), "--state-dir", str(tmp_path / "state"), "--log-dir", str(tmp_path / "logs"),
            "--no-refresh"]
    return tmp_path, cfgp, args, engine_factory


def test_clean_run_and_stop(env):
    tmp, _, args, ef = env
    feeds = []

    def ff(symbols):
        feeds.append(FakeFeed(symbols))
        return feeds[-1]

    assert main_mod.main(args + ["--max-seconds", "0"], feed_factory=ff, engine_factory=ef) == 0
    kinds = [d["kind"] for d in read_decisions(tmp / "logs" / "decisions.jsonl")]
    assert kinds[0] == "start" and kinds[-1] == "stop"
    assert feeds[0].started and feeds[0].stopped
    assert not (tmp / "state" / "bot.pid").exists() and not (tmp / "state" / "HALT").exists()
    assert json.loads((tmp / "state" / "status.json").read_text())["running"] is False
    assert (tmp / "logs" / "bot.log").exists()


def test_live_mode_is_refused_and_nothing_starts(env):
    tmp, cfgp, args, ef = env
    cfgp.write_text(toml_text(mode="live"))
    (tmp / "state").mkdir()
    (tmp / "state" / "GO_LIVE_CONFIRMED").write_text("yes")       # even with the confirmation file
    made = []
    assert main_mod.main(args, feed_factory=lambda s: made.append(1), engine_factory=ef) == 2
    assert made == [] and not (tmp / "logs" / "decisions.jsonl").exists()


def test_config_error_writes_a_halt_that_needs_a_human(env):
    tmp, cfgp, args, ef = env
    cfgp.write_text("mode = 'paper'\nsymbols = [")
    assert main_mod.main(args, feed_factory=FakeFeed, engine_factory=ef) == 3
    h = json.loads((tmp / "state" / "HALT").read_text())
    assert h["blocks_restart"] and "config error" in h["halts"][0]["reason"]
    cfgp.write_text(toml_text())                                  # fixing the config is not enough:
    assert main_mod.main(args, feed_factory=FakeFeed, engine_factory=ef) == 3     # HALT must be deleted by a human
    (tmp / "state" / "HALT").unlink()
    assert main_mod.main(args + ["--max-seconds", "0"], feed_factory=FakeFeed, engine_factory=ef) == 0


def test_unknown_strategy_fails_at_startup_with_a_halt(env):
    tmp, cfgp, args, _ = env
    cfgp.write_text(toml_text(strategy="strategy_that_does_not_exist"))

    def ef(cfg, refresher=None):
        helper = Bot(tmp / "h2")
        hist = History(cfg.symbols, cfg.state_dir, base_loader=lambda s: helper.frames[s],
                       funding_archive=lambda s: helper.funding_archive)
        return Engine(cfg, history=hist, strategies_dir=helper.sdir)

    assert main_mod.main(args, feed_factory=FakeFeed, engine_factory=ef) == 3
    h = json.loads((tmp / "state" / "HALT").read_text())
    assert "startup failed" in h["halts"][0]["reason"] and "not found" in h["halts"][0]["reason"]


def test_exception_in_the_loop_halts_flattens_and_exits_3(env):
    tmp, _, args, ef = env

    class Exploding(FakeFeed):
        def get(self, timeout):
            raise ValueError("feed object broke")

    assert main_mod.main(args, feed_factory=Exploding, engine_factory=ef) == 3
    h = json.loads((tmp / "state" / "HALT").read_text())
    assert h["blocks_restart"] and h["needs_human"]
    assert "unhandled ValueError: feed object broke" in h["halts"][0]["reason"] and "main loop" in h["halts"][0]["reason"]
    assert any(d["kind"] == "halt" for d in read_decisions(tmp / "logs" / "decisions.jsonl"))
    assert not (tmp / "state" / "bot.pid").exists()


def test_second_instance_is_refused(env):
    tmp, _, args, ef = env
    (tmp / "state").mkdir(exist_ok=True)
    with open(tmp / "state" / "bot.lock", "a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main_mod.main(args, feed_factory=FakeFeed, engine_factory=ef) == 4
    assert main_mod.main(args + ["--max-seconds", "0"], feed_factory=FakeFeed, engine_factory=ef) == 0


def test_hand_written_halt_does_not_block_start_but_the_bot_runs_halted(env):
    tmp, _, args, ef = env
    (tmp / "state").mkdir(exist_ok=True)
    (tmp / "state" / "HALT").write_text("paused by the owner")
    assert main_mod.blocking_halt(tmp / "state") is None
    assert main_mod.main(args + ["--max-seconds", "0"], feed_factory=FakeFeed, engine_factory=ef) == 0
    h = json.loads((tmp / "state" / "HALT").read_text())
    assert h["halts"][0]["kind"] == "manual" and h["blocks_restart"] is False


def test_emergency_halt_keeps_earlier_halts(tmp_path):
    main_mod.emergency_halt(tmp_path, "first")
    main_mod.emergency_halt(tmp_path, "second")
    h = json.loads((tmp_path / "HALT").read_text())
    assert [x["reason"] for x in h["halts"]] == ["first", "second"]
    assert main_mod.blocking_halt(tmp_path) == "exception: first; exception: second"
    (tmp_path / "HALT").write_text("{broken")
    main_mod.emergency_halt(tmp_path, "third")
    assert [x["reason"] for x in json.loads((tmp_path / "HALT").read_text())["halts"]] == ["third"]
