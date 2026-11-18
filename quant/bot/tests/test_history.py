"""History assembly, gap detection, 4h alignment, unknown-cell bookkeeping, bar store, archive refresh."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from bot import archive
from bot.history import COLUMNS, FUNDING_COLS, OI_COLS, BarStore, History, index_ms, resample_4h, to_index
from bot.util import HOUR_MS, iso

from .conftest import T0, synth_frame

SYM = "BTCUSDT"


def funding_series(last="2026-10-31 16:00", periods=400):
    idx = pd.date_range(end=pd.Timestamp(last, tz="UTC"), periods=periods, freq="8h")
    return pd.Series(np.linspace(-0.0002, 0.0003, periods), index=idx, name="funding_rate")


def make_history(tmp_path, n=600, symbols=(SYM,), fund_last="2026-10-31 16:00"):
    frames = {s: synth_frame(n=n, start=T0 - n * HOUR_MS, seed=i + 1) for i, s in enumerate(symbols)}
    h = History(list(symbols), tmp_path / "state", base_loader=lambda s: frames[s],
                funding_archive=lambda s: funding_series(fund_last))
    return h, frames


def live_bar(t, price=60000.0, paid=0.0, rate=0.0001):
    return {"time_ms": t, "open": price, "high": price + 50, "low": price - 50, "close": price + 10,
            "volume": 100.0, "quote_volume": 6e6, "trades": 1234, "taker_buy_base": 50.0, "taker_buy_quote": 3e6,
            "funding_paid": paid, "funding_rate": rate, "recv_ms": t + HOUR_MS + 300}


# ---------------------------------------------------------------- frames
def test_frame_has_the_loader_columns_and_index(tmp_path):
    h, frames = make_history(tmp_path)
    f = h.frame(SYM)
    assert list(f.columns) == COLUMNS and f.index.name == "time" and str(f.index.tz) == "UTC"
    assert len(f) == 600 and int(index_ms(f.index)[-1]) == T0 - HOUR_MS
    pd.testing.assert_frame_equal(f[["open", "high", "low", "close", "volume", "trades"]],
                                  frames[SYM][["open", "high", "low", "close", "volume", "trades"]], check_freq=False)
    assert f["trades"].dtype == "int64"


def test_funding_after_the_archived_month_is_unknown_not_forward_filled(tmp_path):
    h, _ = make_history(tmp_path)
    f, unk = h.frame(SYM), h.unknown(SYM)
    cut = pd.Timestamp("2026-11-01 00:00", tz="UTC")
    close_t = f.index + pd.Timedelta(hours=1)
    after = close_t >= cut
    assert after.sum() > 300
    assert f.loc[after, FUNDING_COLS].isna().all().all() and unk.loc[after, FUNDING_COLS].all().all()
    assert f.loc[~after, FUNDING_COLS].notna().all().all() and not unk.loc[~after, FUNDING_COLS].any().any()
    # known part follows the loader's rule: paid = rate settled exactly at the bar close, else 0
    fs = funding_series()
    known = f.loc[~after]
    settle = (known.index + pd.Timedelta(hours=1)).isin(fs.index)
    assert (known.loc[~settle, "funding_paid"] == 0).all() and (known.loc[settle, "funding_paid"] != 0).any()
    assert not unk[OI_COLS].any().any()          # archive OI is known for every archive bar


def test_live_bars_extend_the_frame_with_unknown_oi(tmp_path):
    h, _ = make_history(tmp_path)
    assert h.add_live_bar(SYM, live_bar(T0, paid=None, rate=None))
    assert h.add_live_bar(SYM, live_bar(T0 + HOUR_MS, paid=0.0, rate=0.0002))
    f, unk = h.frame(SYM), h.unknown(SYM)
    assert len(f) == 602 and list(f.columns) == COLUMNS
    last2 = f.iloc[-2:]
    assert last2[OI_COLS].isna().all().all() and unk.iloc[-2:][OI_COLS].all().all()
    assert np.isnan(last2["funding_rate"].iloc[0]) and unk["funding_rate"].iloc[-2]
    assert last2["funding_rate"].iloc[1] == 0.0002 and not unk["funding_rate"].iloc[-1]
    assert f["trades"].dtype == "int64" and f.index.is_monotonic_increasing
    assert h.unknown_columns(SYM, "1h", 300)["oi"] == 2
    assert h.unknown_columns(SYM, "1h", 300)["funding_rate"] == 299


def test_live_bar_is_idempotent_and_must_be_on_the_hour(tmp_path):
    h, _ = make_history(tmp_path)
    assert h.add_live_bar(SYM, live_bar(T0))
    assert not h.add_live_bar(SYM, live_bar(T0))                 # same bar again
    assert not h.add_live_bar(SYM, live_bar(T0 - HOUR_MS))       # an older one
    with pytest.raises(ValueError):
        h.add_live_bar(SYM, live_bar(T0 + HOUR_MS + 1))
    assert len(h.frame(SYM)) == 601


def test_own_bars_survive_a_restart_and_a_torn_last_line(tmp_path):
    h, frames = make_history(tmp_path)
    h.add_live_bar(SYM, live_bar(T0, price=61000.0))
    h.add_live_bar(SYM, live_bar(T0 + HOUR_MS, price=61100.0))
    path = h.store.path(SYM)
    with open(path, "a") as f:
        f.write("2026-11-18T02:00:00.000Z,17949672")              # killed mid-write
    h2 = History([SYM], tmp_path / "state", base_loader=lambda s: frames[s], funding_archive=lambda s: funding_series())
    f2 = h2.frame(SYM)
    assert len(f2) == 602 and f2["open"].iloc[-1] == 61100.0
    assert path.read_text().count("\n") == 3                      # header + 2 good rows, torn line removed
    assert h2.add_live_bar(SYM, live_bar(T0 + 2 * HOUR_MS))


# ---------------------------------------------------------------- gaps and readiness
def test_ready_only_when_the_warmup_window_is_contiguous(tmp_path):
    h, _ = make_history(tmp_path)
    assert h.ready(SYM, "1h", 300, T0 - HOUR_MS) == (True, "ok")
    ok, why = h.ready(SYM, "1h", 300, T0)                         # the bar that just closed is not there
    assert not ok and "history gap" in why and "1 missing 1h bar(s)" in why
    ok, why = h.ready(SYM, "1h", 601, T0 - HOUR_MS)
    assert not ok and "only 600" in why
    # the bot starts 3 hours late: bars T0, T0+1h, T0+2h were never seen
    h.add_live_bar(SYM, live_bar(T0 + 3 * HOUR_MS))
    ok, why = h.ready(SYM, "1h", 300, T0 + 3 * HOUR_MS)
    assert not ok and "not contiguous" in why and "3 missing 1h bar(s)" in why
    assert h.missing_bars(SYM, T0 + 3 * HOUR_MS) == [T0, T0 + HOUR_MS, T0 + 2 * HOUR_MS]
    s = h.summary(SYM, T0 + 4 * HOUR_MS + 5)
    assert s["missing_bars"] == 3 and s["first_missing"] == iso(T0) and s["own_bars"] == 1


def test_a_gap_older_than_the_warmup_window_does_not_block(tmp_path):
    h, _ = make_history(tmp_path)
    for i in range(3, 60):
        h.add_live_bar(SYM, live_bar(T0 + i * HOUR_MS))
    last = T0 + 59 * HOUR_MS
    assert not h.ready(SYM, "1h", 300, last)[0]
    assert h.ready(SYM, "1h", 57, last)[0]                         # 57 contiguous bars since the gap
    assert not h.ready(SYM, "1h", 58, last)[0]


def test_archive_refresh_closes_the_gap(tmp_path):
    h, _ = make_history(tmp_path)
    h.add_live_bar(SYM, live_bar(T0 + 3 * HOUR_MS, price=60000.0))
    rows = pd.DataFrame([{"time_ms": T0 + i * HOUR_MS, "open": 60000.0, "high": 60050.0, "low": 59950.0,
                          "close": 60010.0, "volume": 100.0, "quote_volume": 6e6, "trades": 1234,
                          "taker_buy_base": 50.0, "taker_buy_quote": 3e6} for i in range(4)])
    assert archive._merge_csv(tmp_path / "state" / "archive" / f"{SYM}_1h.csv", rows) == 4
    h.reload(SYM)
    assert h.ready(SYM, "1h", 300, T0 + 3 * HOUR_MS) == (True, "ok")
    assert h.missing_bars(SYM, T0 + 3 * HOUR_MS) == [] and h.mismatches[SYM] == []
    assert len(h.frame(SYM)) == 604
    # the archive's version of a bar we recorded live differs -> reported
    rows.loc[3, "close"] = 60500.0
    archive._merge_csv(tmp_path / "state" / "archive" / f"{SYM}_1h.csv", rows)
    h.add_live_bar(SYM, live_bar(T0 + 4 * HOUR_MS))
    h.reload(SYM)
    assert h.mismatches[SYM] == [{"time": iso(T0 + 3 * HOUR_MS), "columns": ["close"]}]
    assert h.frame(SYM).loc[to_index([T0 + 3 * HOUR_MS])[0], "close"] == 60500.0     # the archive wins


def test_live_funding_observation_fills_an_archive_bar(tmp_path):
    """A bar first recorded live (with funding) and later delivered by the daily kline archive keeps its funding."""
    h, _ = make_history(tmp_path)
    h.add_live_bar(SYM, live_bar(T0, paid=0.00015, rate=0.00015))
    rows = pd.DataFrame([{"time_ms": T0, "open": 60000.0, "high": 60050.0, "low": 59950.0, "close": 60010.0,
                          "volume": 100.0, "quote_volume": 6e6, "trades": 1234, "taker_buy_base": 50.0,
                          "taker_buy_quote": 3e6}])
    archive._merge_csv(tmp_path / "state" / "archive" / f"{SYM}_1h.csv", rows)
    h.reload(SYM)
    f, unk = h.frame(SYM), h.unknown(SYM)
    assert len(f) == 601 and f["funding_paid"].iloc[-1] == 0.00015 and not unk["funding_paid"].iloc[-1]
    assert unk["oi"].iloc[-1]


def test_refreshed_monthly_funding_extends_what_is_known(tmp_path):
    h, _ = make_history(tmp_path)
    assert h.unknown(SYM)["funding_rate"].sum() > 300
    nov = pd.DataFrame({"time_ms": [T0 - 17 * 24 * HOUR_MS + i * 8 * HOUR_MS for i in range(90)], "funding_rate": 0.0002})
    archive._merge_csv(tmp_path / "state" / "archive" / f"{SYM}_funding.csv", nov)
    h.reload(SYM)
    assert h.unknown(SYM)["funding_rate"].sum() == 0
    assert h.frame(SYM)["funding_rate"].iloc[-1] == 0.0002


# ---------------------------------------------------------------- 4h
def test_4h_bars_are_aligned_to_midnight_utc_and_complete(tmp_path):
    h, frames = make_history(tmp_path, n=601)                     # starts 1 hour before a 4h boundary
    f1, f4 = h.frame(SYM, "1h"), h.frame(SYM, "4h")
    assert (index_ms(f4.index) % (4 * HOUR_MS) == 0).all()
    assert set(f4.index.hour) == {0, 4, 8, 12, 16, 20}
    assert int(index_ms(f4.index)[-1]) == T0 - 4 * HOUR_MS        # last complete 4h bar
    assert int(index_ms(f4.index)[0]) == int(index_ms(f1.index)[0]) + HOUR_MS       # incomplete first bucket dropped
    assert len(f4) == 150
    b = f4.iloc[-1]
    c = f1.iloc[-4:]
    assert b["open"] == c["open"].iloc[0] and b["close"] == c["close"].iloc[-1]
    assert b["high"] == c["high"].max() and b["low"] == c["low"].min()
    assert b["volume"] == pytest.approx(c["volume"].sum()) and b["trades"] == c["trades"].sum()


def test_4h_bar_appears_only_when_its_fourth_hour_closed(tmp_path):
    h, _ = make_history(tmp_path)
    n0 = len(h.frame(SYM, "4h"))
    for i in range(3):
        h.add_live_bar(SYM, live_bar(T0 + i * HOUR_MS))
        assert len(h.frame(SYM, "4h")) == n0
    h.add_live_bar(SYM, live_bar(T0 + 3 * HOUR_MS))
    f4 = h.frame(SYM, "4h")
    assert len(f4) == n0 + 1 and int(index_ms(f4.index)[-1]) == T0
    assert h.ready(SYM, "4h", 100, T0)[0]
    assert not h.ready(SYM, "4h", 100, T0 + 4 * HOUR_MS)[0]


def test_4h_cell_is_unknown_when_any_hour_in_it_is_unknown(tmp_path):
    h, _ = make_history(tmp_path)
    for i in range(4):
        h.add_live_bar(SYM, live_bar(T0 + i * HOUR_MS, paid=None if i == 1 else 0.0, rate=0.0001))
    f4, u4 = h.frame(SYM, "4h"), h.unknown(SYM, "4h")
    assert np.isnan(f4["funding_paid"].iloc[-1]) and u4["funding_paid"].iloc[-1]       # not a silent 0.0 sum
    assert f4["funding_rate"].iloc[-1] == 0.0001 and not u4["funding_rate"].iloc[-1]
    assert np.isnan(f4["oi"].iloc[-1]) and u4["oi"].iloc[-1]


def test_resample_matches_the_shared_loader_on_the_real_archive():
    """The bot's 1h and 4h frames equal strategies.data.load row for row where the archive is complete."""
    from strategies.data import load
    h = History(["SOLUSDT"], "/nonexistent-state-dir-not-written" if False else _tmp_state())
    cut1 = pd.Timestamp("2026-10-31 23:00", tz="UTC")
    b1 = load("SOLUSDT", "1h")
    pd.testing.assert_frame_equal(h.frame("SOLUSDT", "1h").loc[:cut1 - pd.Timedelta(hours=1)],
                                  b1.loc[:cut1 - pd.Timedelta(hours=1)], check_freq=False)
    cut4 = pd.Timestamp("2026-10-31 16:00", tz="UTC")
    pd.testing.assert_frame_equal(h.frame("SOLUSDT", "4h").loc[:cut4], load("SOLUSDT", "4h").loc[:cut4],
                                  check_freq=False)
    assert len(h.frame("SOLUSDT", "4h")) == len(load("SOLUSDT", "4h"))


def _tmp_state():
    import tempfile
    return tempfile.mkdtemp(prefix="bot-test-state-")


def test_bad_loader_output_is_rejected(tmp_path):
    bad = synth_frame().drop(columns=["oi"])
    with pytest.raises(RuntimeError):
        History([SYM], tmp_path / "s", base_loader=lambda s: bad, funding_archive=lambda s: funding_series())
    dup = pd.concat([synth_frame(), synth_frame().iloc[-1:]])
    with pytest.raises(RuntimeError):
        History([SYM], tmp_path / "s2", base_loader=lambda s: dup, funding_archive=lambda s: funding_series())
    h, _ = make_history(tmp_path)
    with pytest.raises(ValueError):
        h.frame(SYM, "15m")


def test_resample_of_an_empty_frame():
    e = synth_frame().iloc[:0]
    r, u = resample_4h(e, pd.DataFrame(False, index=e.index, columns=FUNDING_COLS + OI_COLS))
    assert len(r) == 0 and len(u) == 0


# ---------------------------------------------------------------- archive download (no network: fake session)
class Resp:
    def __init__(self, status, content=b""):
        self.status_code, self.content = status, content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, files):
        self.files, self.calls = files, []

    def get(self, url, timeout=None):
        self.calls.append(url)
        if url in self.files:
            return Resp(200, self.files[url])
        return Resp(404)


def zipped(name, text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def kline_csv(day_ms, header=True):
    lines = ["open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,"
             "taker_buy_quote_volume,ignore"] if header else []
    for i in range(24):
        t = day_ms + i * HOUR_MS
        lines.append(f"{t},60000.0,60050.0,59950.0,60010.0,100.0,{t + HOUR_MS - 1},6000000.0,1234,50.0,3000000.0,0")
    return "\n".join(lines) + "\n"


def test_archive_refresh_verifies_checksums_and_merges(tmp_path):
    day = dt.date(2026, 11, 18)
    url = f"{archive.BASE}/daily/klines/{SYM}/1h/{SYM}-1h-2026-11-18.zip"
    z = zipped("x.csv", kline_csv(T0))
    good = FakeSession({url: z, url + ".CHECKSUM": (hashlib.sha256(z).hexdigest() + "  x.zip\n").encode()})
    r = archive.ArchiveRefresher(tmp_path / "state", session=good)
    res = r.refresh_klines(SYM, [day, dt.date(2026, 11, 19)])
    assert res == {"added": 24, "missing": ["2026-11-19"]}
    assert r.refresh_klines(SYM, [day])["added"] == 0             # idempotent
    h, _ = make_history(tmp_path)
    assert len(h.frame(SYM)) == 624 and h.ready(SYM, "1h", 300, T0 + 23 * HOUR_MS)[0]

    bad = FakeSession({url: z, url + ".CHECKSUM": b"deadbeef  x.zip\n"})
    with pytest.raises(archive.ChecksumError):
        archive.ArchiveRefresher(tmp_path / "s2", session=bad).refresh_klines(SYM, [day])
    nock = FakeSession({url: z})
    with pytest.raises(archive.ChecksumError):
        archive.ArchiveRefresher(tmp_path / "s3", session=nock).refresh_klines(SYM, [day])
    assert not (tmp_path / "s2" / "archive").exists()             # nothing unverified was written


def test_archive_kline_file_without_header_and_funding_file(tmp_path):
    assert len(archive.parse_klines(kline_csv(T0, header=False).encode())) == 24
    url = f"{archive.BASE}/monthly/fundingRate/{SYM}/{SYM}-fundingRate-2026-11.zip"
    csv = "calc_time,funding_interval_hours,last_funding_rate\n" + "".join(
        f"{T0 - 17 * 24 * HOUR_MS + i * 8 * HOUR_MS + 7},8,0.0001\n" for i in range(90))
    z = zipped("f.csv", csv)
    s = FakeSession({url: z, url + ".CHECKSUM": hashlib.sha256(z).hexdigest().encode()})
    r = archive.ArchiveRefresher(tmp_path / "state", session=s)
    assert r.refresh_funding(SYM, [(2026, 11), (2026, 12)]) == {"added": 90, "missing": ["2026-12"]}
    got = pd.read_csv(tmp_path / "state" / "archive" / f"{SYM}_funding.csv")
    assert (got["time_ms"] % (8 * HOUR_MS) == 0).all()             # calc_time jitter rounded to the hour


def test_days_of_and_refresh_worker(tmp_path):
    assert archive.days_of([T0, T0 + HOUR_MS, T0 + 30 * HOUR_MS], dt.date(2026, 11, 18)) == [dt.date(2026, 11, 18)]

    class Boom:
        def refresh_klines(self, sym, days):
            if sym == "ETHUSDT":
                raise ConnectionError("proxy said no")
            return {"added": 5, "missing": ["2026-11-19"]}

        def refresh_funding(self, sym, months):
            return {"added": 1, "missing": []}

    w = archive.RefreshWorker(Boom())
    w.request(SYM, [dt.date(2026, 11, 18)], [(2026, 11)])
    w.request("ETHUSDT", [dt.date(2026, 11, 18)])
    w.run_once()
    res = {r["symbol"]: r for r in w.take_results()}
    assert res[SYM]["added"] == 6 and res[SYM]["error"] is None
    assert "ConnectionError" in res["ETHUSDT"]["error"]            # reported, never raised
    assert w.take_results() == []
    w.start()
    w.request(SYM, [dt.date(2026, 11, 18)])
    import time
    for _ in range(100):
        if w.take_results():
            break
        time.sleep(0.01)
    else:
        raise AssertionError("worker thread did not process the job")
    w.stop()
    w.join(timeout=2)
    assert not w.is_alive()
