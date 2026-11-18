"""config/setup.toml is the single switchboard: parsing, validation, setup identity."""
from __future__ import annotations

import copy

import pytest

from bot.config import ConfigError, load_config, parse

from .conftest import RAW, ROOT, make_cfg


def test_the_shipped_config_loads_and_matches_the_owner_settings():
    c = load_config(ROOT / "config" / "setup.toml")
    assert c.mode == "paper" and c.symbols == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert (c.equity, c.risk_per_trade, c.max_leverage) == (1000.0, 0.005, 3.0)
    assert (c.maker_fee, c.taker_fee) == (0.0002, 0.0005)
    assert c.slip("BTCUSDT") == 0.0001 and c.slip("ETHUSDT") == 0.0001 and c.slip("SOLUSDT") == 0.0002
    assert c.stop_slippage_mult == 2.0
    assert (c.shield.daily_loss_pct, c.shield.max_consecutive_losses, c.shield.stale_feed_s) == (0.03, 3, 60)
    assert c.shield.stale_clear_s == 300 and c.engine.heartbeat_s == 300 and c.engine.default_warmup_bars == 300
    assert len(c.setups) >= 1 and sum(s.role == "leader" for s in c.setups) == 1
    assert c.state_dir.name == "state" and c.log_dir.name == "logs" and c.source.endswith("setup.toml")
    assert c.setup(c.setups[0].id) is c.setups[0]
    with pytest.raises(KeyError):
        c.setup("nope")


def test_default_config_path(monkeypatch):
    assert load_config().source.endswith("config/setup.toml")


def test_swapping_setups_is_only_a_config_change(tmp_path):
    a = make_cfg(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "alpha", "params": {"n": 20}}])
    b = make_cfg(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "alpha", "params": {"n": 21}}])
    c = make_cfg(tmp_path, setups=[{"id": "leader", "role": "leader", "strategy": "beta", "params": {"n": 20}}])
    d = make_cfg(tmp_path, setups=[{"id": "leader", "role": "runner-up", "strategy": "alpha", "params": {"n": 20}}])
    e = make_cfg(tmp_path, setups=[{"id": "leader", "strategy": "alpha", "params": {"n": 20}, "symbols": ["BTCUSDT"]}])
    fp = [x.setups[0].fingerprint for x in (a, b, c, d, e)]
    assert fp[0] != fp[1] and fp[0] != fp[2] and fp[0] != fp[4]
    assert fp[0] == fp[3]                                         # role is a label, not what the book trades
    many = make_cfg(tmp_path, setups=[{"strategy": "alpha", "role": "leader"}, {"strategy": "beta"},
                                      {"id": "beta_fast", "strategy": "beta", "params": {"n": 5}}])
    assert [s.id for s in many.setups] == ["alpha", "beta", "beta_fast"]
    assert [s.role for s in many.setups] == ["leader", "runner-up", "runner-up"]
    assert many.setups[2].symbols == many.symbols


def bad(**change):
    raw = copy.deepcopy(RAW)
    for path, v in change.items():
        cur = raw
        keys = path.split("__")
        for k in keys[:-1]:
            cur = cur[k]
        if v is KeyError:
            cur.pop(keys[-1])
        else:
            cur[keys[-1]] = v
    return raw


@pytest.mark.parametrize("change, msg", [
    (dict(mode="backtest"), "mode"),
    (dict(mode=KeyError), "mode"),
    (dict(symbols=[]), "symbols is empty"),
    (dict(symbols=["BTCUSDT", "DOGEUSDT"]), "not in scope"),
    (dict(symbols=["BTCUSDT", "BTCUSDT"]), "duplicate symbols"),
    (dict(account__equity=KeyError), "missing config key"),
    (dict(account__equity=0), "equity"),
    (dict(account__risk_per_trade=0.5), "risk_per_trade"),
    (dict(account__max_leverage=50), "max_leverage"),
    (dict(fees__taker=0.05), "fees look wrong"),
    (dict(slippage__stop_multiplier=0.5), "slippage"),
    (dict(slippage__bps={"BTCUSDT": 1.0}), "missing config key"),
    (dict(shield={"daily_loss": 0.03}), "unknown keys"),
    (dict(shield={"daily_loss_pct": 3}), "daily_loss_pct"),
    (dict(shield={"max_consecutive_losses": 0}), "must be positive"),
    (dict(engine={"heartbeat": 5}), "unknown keys"),
    (dict(setup=[]), "no \\[\\[setup\\]\\]"),
    (dict(setup=[{"id": "x"}]), "'strategy' is required"),
    (dict(setup=[{"strategy": "a"}, {"strategy": "a"}]), "duplicate setup id"),
    (dict(setup=[{"id": "a:b", "strategy": "a"}]), "must not contain"),
    (dict(setup=[{"strategy": "a", "symbols": ["XRPUSDT"]}]), "not in the top-level symbols"),
    (dict(setup=[{"strategy": "a", "paramz": {}}]), "unknown keys"),
    (dict(setup=[{"strategy": "a", "role": "leader"}, {"strategy": "b", "role": "leader"}]), "more than one"),
])
def test_invalid_configs_are_rejected_with_a_clear_message(change, msg):
    with pytest.raises(ConfigError, match=msg):
        parse(bad(**change))


def test_live_mode_parses_but_nothing_in_this_build_runs_it(tmp_path):
    assert make_cfg(tmp_path, mode="live").mode == "live"       # main.py and Shield refuse it (see their tests)
