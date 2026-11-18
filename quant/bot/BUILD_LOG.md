# Build log - paper-trading bot (Forge)

Built 2026-11-18 (UTC) in six phases: Orient, Brainstorm (spec), Architecture, Build (test-first),
Review, Ship.

**About the process.** The owner asked for the Agenkit kit. Agenkit is a paid, licence-keyed
product that could not be installed here, so nothing from it was used. Its phase structure was
followed by hand; this file is the record of each phase.

Contract files, read first and not modified: `SPEC.md`, `strategies/data.py`, `data/*.csv.gz`.
Other teams' files (Radar's strategies, Lab's engine) were read, imported by tests, never edited.
The only file written outside `bot/` and `config/` is `strategies/_reference_breakout.py`, as briefed.

---

## 1. Orient

What the contract says, and what was found on this machine that the design had to absorb.

| Finding | Consequence |
|---|---|
| `load(sym, tf)` returns 15 columns, index = bar OPEN time (UTC, microsecond resolution), 37,680 contiguous 1h bars per symbol ending 2026-11-17 23:00. | The bot's frames reproduce exactly this shape; a test compares them with `load()` row for row. |
| The funding archive is monthly and ends 2026-10-31 16:00. From 2026-11-01 `load()` shows `funding_paid = 0.0` and a forward-filled `funding_rate` - not real values. | The bot marks those cells unknown (NaN) instead of passing stale numbers to a strategy. |
| Binance REST is geo-blocked (HTTP 451). | No REST call anywhere in the running bot. History only from data.binance.vision. |
| **The websocket URL in the brief no longer carries klines.** On `wss://fstream.binance.com/stream?streams=...` only `bookTicker` arrived; `kline_1h` and `markPrice` stayed silent (probed 08:15 UTC). They do arrive on `/market/stream?streams=`, book tickers on `/public/stream?streams=` (same host, not a mirror). | The feed keeps separate connections per path family. Without this the bot would never have seen a bar close. |
| No open-interest stream exists (`<sym>@openInterest` is silent on both paths). | `oi`, `oi_value`, `top_ls_ratio`, `acct_ls_ratio` are never known for live bars. |
| Connections through this machine's proxy are reset often: in a 45-minute probe the book socket was reset 3 times and the kline socket once; reconnect took about 2 s. | Reconnect logic was exercised live. The kline/markPrice stream is subscribed twice on independent sockets, because a reset across the top of the hour would lose the closed bar. |
| Rates observed: about 1,400 book messages/s in total, of which about 20/s change a price; exchange event time is about 100 ms behind the local clock (p99 about 300 ms, worst 1.3 s); closed klines arrive 180-250 ms after the hour; markPrice once a second. | Size-only book updates are dropped in the reader thread; thresholds (5 s clock skew, 10 s quote age, 15 s bar grace, 30 s late decision) have wide margins. |
| pandas 3.0.5 / Python 3.13. | Index resolutions are normalised; the float/Decimal handling for ticks does not rely on pandas. |

Limits of this phase: the stream facts above come from about an hour of observation on one day.

## 2. Brainstorm (spec)

Decisions taken where the brief or SPEC left room, with the alternative that was rejected.

1. **How "trades through" is read from a best bid/ask stream.** A resting buy at L is filled when the
   best ask is at or below L. An offer can only rest at L after every bid at L - ours included - has
   been consumed, so this is the one book condition that proves the fill. A bid touching L, or
   dropping below it while nothing is offered at L, does not fill (rejected alternative: "bid < L",
   which also fires when bids are merely cancelled). Sells mirror it. This is slightly more
   conservative than the backtester's "low < limit": in a market with a spread wider than one tick a
   fill the bar rule would grant can be missed. A missed fill is the safe direction.
2. **Unknown data is NaN, never a stale number, and a strategy that reads it is refused.** SPEC gives a
   module no way to say what it reads, so the loader finds out (section 3, "dependency probe") and
   also honours Radar's `REQUIRED_COLUMNS`. Rejected: trusting forward-filled values; silently
   letting NaN comparisons turn a strategy off.
3. **Shield's P&L rules are per book.** Daily loss (-3% of the book's start-of-UTC-day equity, marked to
   market) and the 3-loss streak halt the book they happen in; stale feed, exception, clock, KILL halt
   everything. Reason: runners-up are separate $1,000 accounts that exist to be compared with the
   leader; a runner-up's bad day must not stop the leader, which it would not do live. The brief's
   "across the account" is read as "across all symbols of that account". **This is an
   interpretation - say so if account-wide was meant.**
4. **An outage longer than 60 s with exposure is a stale feed.** On restart the bot cannot know what
   happened to its stops, so Shield halts (stale_feed), resting orders are cancelled, positions are
   flattened at the first fresh quote (through a gapped stop at the gap price), and trading resumes
   after 5 healthy minutes. A restart within 60 s resumes with orders and stops intact.
5. **Late is not placed.** If the bot is down or slow when a bar closes, no order is placed for that
   bar (Shield blocks an entry more than 30 s after the close); the backtest assumes the open.
6. **One book per setup, shared across that setup's symbols.** The owner's account is one $1,000
   account. Lab backtests each (setup, symbol) pair on its own $1,000. To compare one to one, give
   each symbol its own `[[setup]]` entry (same strategy, different `id` and `symbols`).
7. **Exits are never blocked.** Shield has the final say on every order that opens risk; an exit gets an
   approval object too, but it is always granted.
8. **`max_leverage` is also a gross cap per book** (open positions + resting orders + the new order
   <= 3x equity). SPEC caps each position at 3x; the owner's line reads like an account limit. This
   can only block, never enlarge, and did not trigger in any replay.
9. **Exchange minimum order size is enforced** (and logged as a block), as the coordinator confirmed.
   With $1,000 and 0.5% risk this bites on BTC: one lot step is about $65 and the assumed minimum
   notional is 100 USDT, so a BTC stop wider than about 3.8% of price yields a size below the minimum.

## 3. Architecture

```
            wss /public/stream            wss /market/stream (x2, redundant)
            bookTicker                    kline_1h + markPrice@1s
                 \                              /
                  feed.py  (reader thread per socket, reconnect, conflation) -> one queue
                                   |
                              engine.py  (single thread, explicit time)
          book tick ---------->  funding settlement if due -> paper.py (fills, stops, targets, time exits)
          kline (running) ---->  stop safety net
          kline (closed) ----->  history.py: persist bar -> decide once per (setup, symbol)
                                   strategy_loader.py: signals() on the FULL frame, last row
                                   sizing.py + instruments.py: size, tick/step rounding
                                   shield.py: approve / block   --->  exchange.py interface
                                                                          |-- paper.py (PaperBroker)
                                                                          '-- binance_live.py (gated, unused)
          timer (250 ms) ----->  shield monitors, order expiry, archive refresh, heartbeat, status.json
          archive.py (thread) ->  data.binance.vision daily klines / monthly funding -> state/archive/
```

| Module | Role |
|---|---|
| `config.py` | `config/setup.toml` -> validated `Config`. The only source of settings. |
| `feed.py` | Websocket connections, parsing, reconnect, mid-hour recycle before Binance's 24 h cut. |
| `history.py` | Frames = research archive + refreshed archive + own live bars; gap and unknown-cell bookkeeping; 4h resample. |
| `archive.py` | Checksum-verified incremental downloads into `state/archive/` (never touches `data/`). |
| `strategy_loader.py` | Load by module name, enforce the SPEC contract, dependency probe. |
| `engine.py` | Event handling, decision rules, funding observation, heartbeat, status. |
| `shield.py` | Order review and halts. Owns `state/shield.json` and `state/HALT`. |
| `paper.py` | Paper books and the execution model. Owns `state/books.json`. |
| `exchange.py` | The `Exchange` interface; refuses any order without a matching Shield approval. |
| `binance_live.py` | Signed REST request builder for Binance. Refuses to construct in this build. |
| `journal.py` | `logs/decisions.jsonl`, `logs/trades.csv` (append-only, fsync per line, strict JSON). |
| `main.py`, `run.sh` | Entry point with exit codes; supervisor loop. |
| `status.py`, `report.py` | Read-only views over the files above. |
| `snapshot.py` | Durable copy of the state to `paper_state/` and `--restore`. |
| `replay.py` | Test tool: replays archived bars through the live path. |

**State on disk** (everything needed to resume; all JSON written by write-then-rename):

| File | Content |
|---|---|
| `state/books.json` | Per book: equity, resting orders, positions with stop/target/time exit, last decided bar per symbol, last 200 trades. Rewritten after every change. |
| `state/shield.json`, `state/HALT`, `state/KILL` | Halts, day baselines, loss streaks, flags; HALT mirrors active halts for humans and `run.sh`; KILL is the manual kill switch. |
| `state/bars/<SYM>_1h.csv` | Own closed bars, append-only, one fsync'd line per bar. |
| `state/archive/*.csv` | Refreshed archive rows. |
| `state/market.json`, `state/status.json` | Funding observation state; snapshot for `status.py` (also the "last alive" time). |
| `state/bot.lock`, `state/bot.pid`, `state/supervisor.pid` | Single-instance lock and pids. |
| `logs/decisions.jsonl`, `logs/trades.csv`, `logs/bot.log`, `logs/bot.out`, `logs/supervisor.log` | Logs. |

**Surviving a lost container.** `bot/state` and `bot/logs` are git-ignored, so they vanish with the
container. `bot/snapshot.py` copies the minimal durable state into `paper_state/` (meant to be
committed): `books.json`, `shield.json`, `HALT`/`KILL` if present, `market.json`, `status.json`, the
own-bar CSVs, `trades.csv` and the last 2,000 decision lines - an allowlist, with a SHA-256 per file
in `MANIFEST.json`. No secret is ever on disk; as a second lock the snapshot is refused if a value of
`BINANCE_API_KEY`/`BINANCE_API_SECRET` appears in any copied file, and `GO_LIVE_CONFIRMED` is never
copied. `--restore` writes the files back only when `bot/state` and `bot/logs` hold no file at all
and every checksum matches; otherwise it writes nothing. With `snapshot_dir` set in the config (it
is, in the shipped file) the engine refreshes the snapshot itself: shortly after start, within
seconds of a bar close or any order/fill/exit, at most every 5 minutes otherwise, and at shutdown; a
failing snapshot raises a flag and never stops trading. `run.sh start` refuses to start silently
when `bot/state` has no books but a snapshot exists: the human picks `start --restore` or
`start --fresh`. After a restore the time since the snapshot counts as an outage (decision 4 above).
**The bot does not commit or push `paper_state/`** (no git commands were run in this build): whoever
operates it has to, or the snapshot dies with the container too.

**No double entry, no lost stop.** A decision for bar t is made at most once: the book stores the last
signal bar it processed in the same atomic write as the order. Order ids are deterministic
(`<setup>:<symbol>:<bar>`), Shield blocks a second order per (setup, symbol), and the broker refuses
one as well. Stops live in `books.json` and are checked on the first tick after a restart.
`trades.csv` and Shield's loss count are repaired from `books.json` at start if a crash fell between
the writes.

**History gap and warm-up.** On start the bot merges the three layers. A bar it did not see live can
only come from the next day's archive file, so a background thread retries the archive every 30
minutes while bars are missing. A strategy trades a symbol only when the last `WARMUP_BARS` bars of
its timeframe are contiguous and end at the bar that just closed (and, if the strategy reads other
symbols, theirs too). Otherwise the decision is logged as `WARMING: history gap ...` and raised as a
Shield flag, visible in `status.py` and `report.py`. Open positions keep their stop, target and time
exit while warming; exit *signals* cannot be evaluated then.

**What is available live.**

| Column | Live source | Status on a live bar |
|---|---|---|
| open, high, low, close, volume, quote_volume, trades, taker_buy_base, taker_buy_quote | closed `kline_1h` | known |
| funding_paid | markPrice stream: the rate seen within 10 s before a settlement the bot watched | known if the mark stream was watched without a gap since before the bar's close, else unknown |
| funding_rate (last settled) | same | unknown until the first settlement the bot watches (at most 8 h after a start), and again after any settlement it misses |
| oi, oi_value, top_ls_ratio, acct_ls_ratio | none (archive is D+1) | always unknown |

History between the last archived funding month and the bot's first observation (today: 1-18
November) stays unknown until the monthly funding file is published in early December.

**Dependency probe.** At start each strategy is run on fully known archive rows, then again with one
column removed, blanked on recent bars, and scrambled, in its own frame and in the context frames. If
the output changes or the call fails, the strategy reads that column. At each decision a strategy
that reads a column with unknown cells in its warm-up window gets `REFUSED: ...` instead of an
order. Of Radar's 29 modules present at build time, the 11 that declare `oi` or `funding_paid` are
refused under this rule (the OI ones permanently, the funding ones until the warm-up window is fully
observed); 18 read only prices and volume.

**Execution model.** SPEC plus the backtester's rules R1-R12 (`lab/engine.py`), as the coordinator
directed. On book ticks:

| Event | Bot rule |
|---|---|
| Resting buy limit L | fills at L, maker, when best ask <= L (sell: best bid >= L). Lives `entry_valid` bars from the open it was placed at. |
| Limit through the market on arrival (R1) | taker fill at the ask/bid +/- slippage, sized from that price. |
| Marketable entry | ask x (1 + slip) / bid x (1 - slip), taker. Sized from the ask/bid (R9). |
| Stop (long) | best bid <= stop: fills at min(stop, bid) x (1 - 2 slip), taker. The min() is "gap -> fill at the first price seen". |
| Target (long) | resting limit: fills at the target, maker, when best bid >= target. Checked after the stop, before any market exit (R4, R5). |
| Time exit | at the first tick at or after fill bar + max_hold bars (the fill bar is bar 1, R6), taker. |
| Signal exit | at the bar close, taker. Beyond the stop it is a stop; beyond the target the target fills (R5). |
| Which signals count (R7) | flat at the bar close: act. Position open and this row schedules its exit (exit flag or max_hold): exit at the open and act on the new signal at the same open (stop-and-reverse). Position open otherwise: dropped. Entry limit resting: ignored. |
| Funding | every position held at a settlement pays side x qty x mark x rate. A stop/target exit inside a bar that ends on a settlement is charged that funding if it is a cost (R10), from the current estimate, and the trade is flagged. |
| Size | 0.5% x book equity / stop distance, capped at 3x equity, floored to the lot step (same arithmetic as the backtester); below the exchange minimum -> Shield block (R12). |

Differences that remain by construction: the bot rounds limit, stop and target to the exchange tick
(the backtester does not); the bot sees real tick order inside a bar (the backtester assumes stop
first); a tick that hits a stop in the fraction of a second between a bar's close and the arrival of
its kline is booked as a stop even if that row carried an exit flag.

**Shield.**

| Rule | Scope | Clears |
|---|---|---|
| day P&L <= -3% of start-of-day equity (UTC, marked to market) | book | next UTC day |
| 3 consecutive losing trades | book | human deletes `state/HALT` |
| feed stale > 60 s (any symbol's book tick or markPrice, by exchange time; or the bot itself was down > 60 s with exposure) | account | after 5 healthy minutes |
| unhandled exception | account, process exits with code 3 | human deletes `state/HALT` |
| clock anomaly: local clock backwards > 5 s (also across restarts), event time > 5 s ahead of the local clock or backwards > 5 s, a kline that is not a past hour boundary | account | human deletes `state/HALT` |
| `state/KILL` exists, or `state/HALT` created by hand | account | human deletes KILL and HALT |

Every halt cancels resting orders and flattens at market through the engine (a position without a
fresh quote is flattened at its next tick). Order blocks: no stop, stop or target on the wrong side,
size above the leverage cap / risk budget / gross cap, size below exchange minimum, off-grid price or
quantity, duplicate position or resting order, no fresh quote, late decision, halted, unknown setup or
symbol, mode not paper. Each block is a `block` line in the decision log.

**Supervisor.** `run.sh` restarts a crashed bot with backoff 2 s .. 300 s and logs the reason. It does
not restart past a HALT whose `blocks_restart` is true (the exception halt): it waits until a human
deletes the file. Halts that leave the process alive (loss streak, clock, KILL) persist in
`shield.json`, so a restart cannot trade past them either.

## 4. Build (test-first)

Order of work, as it happened: the paper broker and Shield tests were written before their modules
(`test_paper_broker.py`, `test_shield.py`) and the modules were written to make them pass. For the
other modules the tests were written directly after the code, in the same sitting. The tests found
real defects before anything ran live, among them: a wrong unit conversion in the funding-archive
parser, price strings losing a trailing zero for the REST API, and a status snapshot that could be
five seconds behind a halt.

Test suites (`python3 -m pytest bot/tests`):

| File | What it pins down |
|---|---|
| `test_paper_broker.py` | Trade-through, maker/taker fees, slippage, stop on the tick of the fill, gap through stop, stop before target, time exit, funding, R1/R5/R10, restart, config swap, parallel books, flatten. |
| `test_shield.py` | Every block and every halt, auto-clear, human clear, persistence, HALT file handling. |
| `test_engine.py` | Bar-close path, sizing, R7 (stop-and-reverse and the drop rules), limit lifecycle, warm-up gating, column refusal, 4h decisions, funding observation, kill-and-resume, long outage, stale feed, daily loss, exception, KILL, heartbeat, status. |
| `test_history.py` | Frame equality with `load()` on the real archive (1h and 4h), gaps, unknown cells, 4h alignment, torn CSV line, archive checksum handling. |
| `test_parity.py` | Replays the last ten days of archived 1h ETHUSDT bars through the bar-close path; every decision equals the row `signals()` gives on the full frame, for 1h and for 4h bars. |
| `test_parity_lab.py` | Replays archived bars through Engine -> Shield -> PaperBroker and compares the trade list with `lab.engine` for three of Radar's strategies (below). |
| `test_snapshot.py` | Snapshot and restore in both directions: exact file set, resume of positions/stops/halts/bars/ledger, long-outage flatten after restore, refusal on non-empty directories, checksum and manifest tampering, no secrets, engine-driven snapshots. |
| `test_strategy_loader.py`, `test_config.py`, `test_feed.py`, `test_binance_live.py`, `test_main.py`, `test_run_sh.py`, `test_status_report.py`, `test_misc.py` | Loader contract and probe, config validation, feed plumbing with fake sockets, live-adapter gates/requests/signing, exit codes, supervisor loop with a fake bot, status/report output, sizing and rounding. |

Trade-list parity with the backtester (exchange filters on in both, Shield's loss-streak halt off
because the backtester has none):

| Strategy | Symbol, TF | Window | Trades | Result |
|---|---|---|---|---|
| donchian_trend | ETHUSDT 4h | 2026-05-01 .. 10-31 | 15 | identical list (signal bar, entry bar, exit bar, side, entry type, exit reason, quantity), including a stop-and-reverse; exit prices within 2e-6 (tick rounding); realised equity 1014.5761 vs 1014.5769 |
| pullback_limit_sma | BTCUSDT 4h | 2026-08-01 .. 10-31 | 16 | identical list (limit entries, targets, max_hold). 28 further signals were below the exchange minimum size: Shield blocked 28, the backtester rejected the same 28. Realised equity 978.1424 vs 978.1390 |
| breakout_retest | SOLUSDT 1h | 2026-10-01 .. 10-31 | 12 | identical list; quantity differs by up to 2 lot steps where tick rounding changes the stop distance; realised equity 1010.2210 vs 1010.2658 |

Result of the full run: **269 passed, 0 failed** (about 4 minutes; 3 of them are the backtester replays). Statement coverage of `bot/` (tests excluded): **96%** (3,124 statements, 117 not executed).
Per module:

```
Name                     Stmts   Miss  Cover
------------------------------------------------------
bot/__init__.py              0      0   100%
bot/archive.py             134      2    99%
bot/binance_live.py        123      3    98%
bot/config.py              142      0   100%
bot/engine.py              532     13    98%
bot/exchange.py             23      0   100%
bot/feed.py                149      6    96%
bot/history.py             319      7    98%
bot/instruments.py          40      0   100%
bot/journal.py              80      0   100%
bot/main.py                129     11    91%
bot/models.py               86      0   100%
bot/paper.py               368     21    94%
bot/replay.py               80      1    99%
bot/report.py              103     12    88%
bot/shield.py              283      9    97%
bot/sizing.py               11      0   100%
bot/snapshot.py            171     11    94%
bot/status.py              133     17    87%
bot/strategy_loader.py     155      4    97%
bot/util.py                 63      0   100%
------------------------------------------------------
TOTAL                     3124    117    96%
```

Not covered by any test: the real network paths (`websocket.create_connection`, `requests` to the
archive, the `_send` of the live adapter), and `status.py`/`report.py` `main()` when run as scripts
(they are run in a subprocess, which the coverage tool does not follow).

## 5. Review

Every file was re-read as a hostile reviewer. Findings, all fixed unless marked otherwise:

| # | Finding | Fix |
|---|---|---|
| 1 | The "feed healthy for 5 minutes" clock was persisted, so after a restart an old timestamp would have cleared a stale-feed halt immediately. | Never loaded from disk; reset on every stale halt. Test added. |
| 2 | A bar closing seconds after the bot started on a funding time got `funding_paid = 0.0` although the settlement had not been observed. | A bar's funding is "0.0" only if the mark stream was watched without a gap since before its close; else unknown. Test added. |
| 3 | After a restart the "last settled funding rate" was kept even when a settlement could have been missed. | Dropped to unknown unless the persisted next funding time is still in the future. |
| 4 | Only book ticks counted for staleness; a dead kline connection would have gone unnoticed by Shield. | markPrice age is part of the stale check. Test added. |
| 5 | Look-ahead: context frames of other symbols were passed without a cut. | Cut at the decision bar. (The decision frame itself is checked to end at the closed bar.) |
| 6 | Time exit could come a bar early: a marketable fill is stamped with the last quote, which can be a few ms older than the bar open. | The fill bar is taken from max(quote time, order time). Test added. |
| 7 | Double fill / double funding: a second socket delivers every close and settlement twice. | Bars, decisions and settlements are keyed by time; funding carries the last settlement applied per position. Tests. |
| 8 | A strategy that cannot be probed (no fully known rows) was treated as reading nothing. | Treated as reading everything, so it is refused. Test added. |
| 9 | A position whose symbol was removed from the config would never be priced again. | Flagged at start (`orphan_position`), reported by Shield. Not auto-closed: there is no price. |
| 10 | Rounding: quantity floor used exact decimal floor, the backtester floors with a 1e-9 guard; a size that is a whole number of steps up to float noise could differ by one step. | Same arithmetic as the backtester. Prices are snapped with Decimal on the shortest float repr, so 0.1 + 0.2 lands on 0.3. |
| 11 | Limit prices: a passive limit must not be rounded towards the market. | Buy limits round down, sell limits up; stops and targets to the nearest tick. Slippage-adjusted fill prices are model prices and stay off-grid (as in the backtester). |
| 12 | `decisions.jsonl` and state files could contain `NaN`, which strict JSON readers reject. | Everything written is strict JSON (NaN/inf -> null). |
| 13 | A hand-written HALT file blocked nothing if `shield.json` had no halt. | Adopted as a `manual` halt at start and while running. |
| 14 | History assembly rebuilt every live row on each bar (cost grew with uptime; a 6-month replay took minutes). | Incremental. |
| 15 | Feed: one odd message would drop the connection; the first reconnect waited 1 s. | Bad payloads are counted and skipped; first retry is immediate; silent sockets are cut after 5 s. |

| 16 | Restoring a snapshot without the HALT file would have made Shield read its absence as "a human cleared the halt" and resume a book halted for a loss streak. | HALT and KILL are part of the snapshot. Test: a restored loss-streak halt is still in force. |
| 17 | Snapshot files copied in the wrong order could show a trade as closed in the ledger but still open in the books (it would be closed twice). | Ledger first, books last; the start-up reconciliation covers the other direction. An interrupted restore leaves no `books.json`, so nothing starts on half a state. |
| 18 | A restored manifest could name any path. | Allowlist on restore; a manifest entry outside it (or with `..`) is refused before anything is written. |

Assumptions flagged for verification before go-live (they are hard-coded; `exchangeInfo` is unreachable):

| Symbol | tick | lot step | min qty | min notional |
|---|---|---|---|---|
| BTCUSDT | 0.1 | 0.001 | 0.001 | 100 USDT |
| ETHUSDT | 0.01 | 0.001 | 0.001 | 20 USDT |
| SOLUSDT | 0.0100 | 0.01 | 0.01 | 5 USDT |

Tick and step are from the brief; minimum quantity and minimum notional are from memory.
`instruments.FILTERS_VERIFIED` is `False` and every status and report says so.

Known and accepted (not fixed):

- A feed outage that spans the top of an hour on all sockets loses that bar; the setup is WARMING until
  the next day's archive file. Two kline sockets reduce the odds; they do not remove them.
- A cold start is WARMING until the daily archive file for the start day is published.
- The dependency probe is a behavioural test. A strategy that reads a column only when it exists
  *and* whose output happens not to move on the probe rows would be missed; `REQUIRED_COLUMNS` in the
  module closes that hole.
- Paper fills ignore size at the best quote and queue position; partial fills do not exist here.
- Live funding uses the last estimate before the settlement, not Binance's published rate.

## 6. Ship

SMOKE_SECTION

### How to run

```
bot/run.sh start        # supervisor + bot in the background
bot/run.sh status       # or: python3 bot/status.py [--all]
python3 bot/report.py --hours 24
bot/run.sh stop         # clean stop; paper positions stay in state/
bot/run.sh restart      # after editing config/setup.toml
touch bot/state/KILL    # flatten and halt now; remove KILL and HALT to resume
python3 bot/snapshot.py [--show | --restore]    # durable copy in paper_state/ (the bot refreshes it itself)
bot/run.sh start --restore                      # after a lost container: restore, then start
rm bot/state/HALT       # clear a halt that needs a human
```

Changing setups: edit `config/setup.toml` (`[[setup]]` blocks: `id`, `role`, `strategy`, optional
`params`, optional `symbols`), then `bot/run.sh restart`. A setup whose strategy, params or symbols
changed gets a fresh $1,000 book; the old book is retired under `<id>@<fingerprint>`, its orders
cancelled and its positions closed at the first tick, its P&L kept.

### Before real money

1. Verify tick, step, minimum quantity and minimum notional against `exchangeInfo`.
2. The live adapter is a request builder only. It has never talked to Binance. Missing entirely: fill
   tracking (user-data stream), partial fills, reconciliation of exchange and bot state, the wiring of
   the adapter into the engine. Request building and HMAC signing are unit-tested (including the
   signature example from Binance's API documentation); nothing else about it is.
3. Verify the stop order endpoint. From the author's memory Binance moved conditional orders to
   `POST /fapi/v1/algoOrder` (`triggerPrice`) in late 2025; this could not be checked from here. Both
   request shapes are built; the default is the newer one.
4. Decide how Shield's loss-streak halt is to be handled in practice: it needs a human every time, and
   the backtests do not model it.
5. `main.py` refuses any mode other than `paper`; going live is a code change, on purpose.
