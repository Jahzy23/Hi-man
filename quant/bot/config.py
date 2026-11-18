"""config/setup.toml -> validated Config. This file is the only place settings come from."""
from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .util import BOT_DIR, ROOT

DEFAULT_CONFIG = ROOT / "config" / "setup.toml"
KNOWN_SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class SetupCfg:
    id: str
    strategy: str
    role: str
    params: dict
    symbols: tuple[str, ...]

    @property
    def fingerprint(self) -> str:
        """Identity of what the book trades. A change starts a fresh book (see paper.PaperBroker)."""
        blob = json.dumps({"strategy": self.strategy, "params": self.params, "symbols": list(self.symbols)},
                          sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]


@dataclass(frozen=True)
class ShieldCfg:
    daily_loss_pct: float = 0.03
    max_consecutive_losses: int = 3
    stale_feed_s: float = 60.0
    stale_clear_s: float = 300.0
    max_clock_skew_s: float = 5.0
    max_tick_age_s: float = 10.0
    max_decision_delay_s: float = 30.0


@dataclass(frozen=True)
class EngineCfg:
    heartbeat_s: int = 300
    default_warmup_bars: int = 300
    archive_refresh_min: float = 30.0
    bar_close_grace_s: float = 15.0
    snapshot_dir: str = ""           # "" = the bot takes no snapshots itself; relative paths are under quant/
    snapshot_min: float = 5.0        # at most one snapshot per this many minutes (plus bar closes and shutdown)


@dataclass(frozen=True)
class Config:
    mode: str
    symbols: tuple[str, ...]
    equity: float
    risk_per_trade: float
    max_leverage: float
    maker_fee: float
    taker_fee: float
    slippage_bps: dict
    stop_slippage_mult: float
    shield: ShieldCfg
    engine: EngineCfg
    setups: tuple[SetupCfg, ...]
    state_dir: Path = field(default=BOT_DIR / "state")
    log_dir: Path = field(default=BOT_DIR / "logs")
    source: str = ""

    def slip(self, symbol: str) -> float:
        """Slippage as a fraction (1 bp = 0.0001)."""
        return self.slippage_bps[symbol] / 10_000.0

    def setup(self, setup_id: str) -> SetupCfg:
        for s in self.setups:
            if s.id == setup_id:
                return s
        raise KeyError(setup_id)


def _section(raw: dict, name: str, cls, allowed_extra=()):
    d = dict(raw.get(name, {}))
    fields_ = set(cls.__dataclass_fields__)
    unknown = set(d) - fields_ - set(allowed_extra)
    if unknown:
        raise ConfigError(f"[{name}] unknown keys: {sorted(unknown)}")
    return cls(**{k: v for k, v in d.items() if k in fields_})


def parse(raw: dict, state_dir: Path | None = None, log_dir: Path | None = None, source: str = "") -> Config:
    mode = raw.get("mode")
    if mode not in ("paper", "live"):
        raise ConfigError(f"mode must be 'paper' (got {mode!r})")
    symbols = tuple(raw.get("symbols", ()))
    if not symbols:
        raise ConfigError("symbols is empty")
    for s in symbols:
        if s not in KNOWN_SYMBOLS:
            raise ConfigError(f"symbol {s!r} is not in scope {KNOWN_SYMBOLS}")
    if len(set(symbols)) != len(symbols):
        raise ConfigError("duplicate symbols")
    acct = raw.get("account", {})
    fees = raw.get("fees", {})
    slp = raw.get("slippage", {})
    try:
        equity = float(acct["equity"])
        risk = float(acct["risk_per_trade"])
        lev = float(acct["max_leverage"])
        maker = float(fees["maker"])
        taker = float(fees["taker"])
        bps = {s: float(slp["bps"][s]) for s in symbols}
        stop_mult = float(slp.get("stop_multiplier", 2.0))
    except KeyError as e:
        raise ConfigError(f"missing config key: {e}") from None
    if not equity > 0:
        raise ConfigError("account.equity must be > 0")
    if not 0 < risk <= 0.05:
        raise ConfigError("account.risk_per_trade must be in (0, 0.05]")
    if not 0 < lev <= 20:
        raise ConfigError("account.max_leverage must be in (0, 20]")
    if not (0 <= maker < 0.01 and 0 <= taker < 0.01):
        raise ConfigError("fees look wrong (expected fractions, e.g. 0.0005)")
    if any(v < 0 for v in bps.values()) or stop_mult < 1:
        raise ConfigError("slippage must be >= 0 and stop_multiplier >= 1")
    shield = _section(raw, "shield", ShieldCfg)
    engine = _section(raw, "engine", EngineCfg)
    if not 0 < shield.daily_loss_pct < 1:
        raise ConfigError("shield.daily_loss_pct must be a fraction in (0, 1)")
    if shield.max_consecutive_losses < 1 or shield.stale_feed_s <= 0:
        raise ConfigError("shield limits must be positive")
    setups_raw = raw.get("setup", [])
    if not setups_raw:
        raise ConfigError("no [[setup]] entries")
    setups, seen = [], set()
    for i, s in enumerate(setups_raw):
        try:
            name = s["strategy"]
        except KeyError:
            raise ConfigError(f"setup #{i + 1}: 'strategy' is required") from None
        sid = s.get("id", name)
        if sid in seen:
            raise ConfigError(f"duplicate setup id {sid!r}")
        if not sid or any(c in sid for c in ":/\\ \t\n,@"):
            raise ConfigError(f"setup id {sid!r} must not contain ':/\\,@' or whitespace")
        seen.add(sid)
        ssyms = tuple(s.get("symbols", symbols))
        bad = [x for x in ssyms if x not in symbols]
        if bad:
            raise ConfigError(f"setup {sid!r}: symbols {bad} are not in the top-level symbols list")
        unknown = set(s) - {"id", "strategy", "role", "params", "symbols"}
        if unknown:
            raise ConfigError(f"setup {sid!r}: unknown keys {sorted(unknown)}")
        setups.append(SetupCfg(id=sid, strategy=name, role=s.get("role", "runner-up"),
                               params=dict(s.get("params", {})), symbols=ssyms))
    if sum(1 for s in setups if s.role == "leader") > 1:
        raise ConfigError("more than one setup has role = 'leader'")
    return Config(mode=mode, symbols=symbols, equity=equity, risk_per_trade=risk, max_leverage=lev,
                  maker_fee=maker, taker_fee=taker, slippage_bps=bps, stop_slippage_mult=stop_mult,
                  shield=shield, engine=engine, setups=tuple(setups),
                  state_dir=Path(state_dir) if state_dir else BOT_DIR / "state",
                  log_dir=Path(log_dir) if log_dir else BOT_DIR / "logs", source=source)


def load_config(path: Path | str | None = None, state_dir: Path | None = None,
                log_dir: Path | None = None) -> Config:
    path = Path(path) if path else DEFAULT_CONFIG
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    return parse(raw, state_dir=state_dir, log_dir=log_dir, source=str(path))
