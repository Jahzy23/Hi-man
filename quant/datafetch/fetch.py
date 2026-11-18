"""Download Binance USDT-M futures history from the public archive (data.binance.vision).

Source: https://data.binance.vision/data/futures/um/  (official Binance public data)
Every zip is verified against its published SHA256 .CHECKSUM file.
Outputs: data/{SYM}_1h.csv.gz, data/{SYM}_funding.csv.gz, data/{SYM}_oi_1h.csv.gz, data/MANIFEST.json
"""
import io, sys, json, hashlib, zipfile, datetime as dt
from concurrent.futures import ThreadPoolExecutor
import requests, pandas as pd

BASE = "https://data.binance.vision/data/futures/um"
SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
START = dt.date(2022, 8, 1)          # 3 months warm-up before the 4-year window (2022-11-18 →)
TODAY = dt.datetime.now(dt.timezone.utc).date()
OUT = __import__("pathlib").Path(__file__).resolve().parents[1] / "data"
S = requests.Session()

def get(url):
    for i in range(4):
        try:
            r = S.get(url, timeout=60)
            if r.status_code == 404: return None
            r.raise_for_status(); return r.content
        except Exception as e:
            if i == 3: raise
    return None

def fetch_zip(url):
    """Return (csv_bytes, verified) or None when the file does not exist."""
    z = get(url)
    if z is None: return None
    ck = get(url + ".CHECKSUM")
    ok = bool(ck) and ck.decode().split()[0] == hashlib.sha256(z).hexdigest()
    if not ok: raise RuntimeError(f"checksum mismatch {url}")
    zf = zipfile.ZipFile(io.BytesIO(z))
    return zf.read(zf.namelist()[0])

def months(a, b):
    y, m = a.year, a.month
    while (y, m) <= (b.year, b.month):
        yield y, m
        m += 1
        if m == 13: y, m = y + 1, 1

def days(a, b):
    d = a
    while d <= b:
        yield d; d += dt.timedelta(days=1)

KCOLS = ["open_time","open","high","low","close","volume","close_time","quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]

def read_k(b):
    df = pd.read_csv(io.BytesIO(b), header=None, names=KCOLS)
    if str(df.iloc[0, 0]).startswith("open"): df = df.iloc[1:]
    return df.apply(pd.to_numeric)

def klines(sym):
    urls = [f"{BASE}/monthly/klines/{sym}/1h/{sym}-1h-{y}-{m:02d}.zip" for y, m in months(START, TODAY)]
    with ThreadPoolExecutor(12) as ex: parts = list(ex.map(fetch_zip, urls))
    missing_m = [u for u, p in zip(urls, parts) if p is None]
    dfs = [read_k(p) for p in parts if p is not None]
    # months not yet published as monthly files → daily files
    last = pd.concat(dfs)["open_time"].max()
    d0 = (pd.to_datetime(last, unit="ms") + pd.Timedelta(hours=1)).date()
    durls = [f"{BASE}/daily/klines/{sym}/1h/{sym}-1h-{d}.zip" for d in days(d0, TODAY)]
    with ThreadPoolExecutor(12) as ex: dparts = list(ex.map(fetch_zip, durls))
    dfs += [read_k(p) for p in dparts if p is not None]
    df = pd.concat(dfs).drop_duplicates("open_time").sort_values("open_time")
    df["time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df[["time","open","high","low","close","volume","quote_volume","trades","taker_buy_base","taker_buy_quote"]]
    df.to_csv(OUT / f"{sym}_1h.csv.gz", index=False)
    return df, len(urls) - len(missing_m) + sum(p is not None for p in dparts)

def funding(sym):
    urls = [f"{BASE}/monthly/fundingRate/{sym}/{sym}-fundingRate-{y}-{m:02d}.zip" for y, m in months(START, TODAY)]
    with ThreadPoolExecutor(12) as ex: parts = list(ex.map(fetch_zip, urls))
    dfs = [pd.read_csv(io.BytesIO(p)) for p in parts if p is not None]
    df = pd.concat(dfs)
    df.columns = [c.strip() for c in df.columns]
    df["time"] = pd.to_datetime(df["calc_time"], unit="ms", utc=True).dt.round("1h")
    df = df.rename(columns={"last_funding_rate": "funding_rate"})[["time","funding_interval_hours","funding_rate"]]
    df = df.drop_duplicates("time").sort_values("time")
    df.to_csv(OUT / f"{sym}_funding.csv.gz", index=False)
    return df, sum(p is not None for p in parts)

def oi(sym):
    urls = [f"{BASE}/daily/metrics/{sym}/{sym}-metrics-{d}.zip" for d in days(START, TODAY)]
    def one(u):
        try: return fetch_zip(u)
        except Exception as e: return None
    with ThreadPoolExecutor(24) as ex: parts = list(ex.map(one, urls))
    dfs = [pd.read_csv(io.BytesIO(p)) for p in parts if p is not None]
    df = pd.concat(dfs)
    df["time"] = pd.to_datetime(df["create_time"], utc=True)
    df = df.sort_values("time").set_index("time")
    num = ["sum_open_interest","sum_open_interest_value","count_toptrader_long_short_ratio","sum_toptrader_long_short_ratio","count_long_short_ratio","sum_taker_long_short_vol_ratio"]
    df[num] = df[num].apply(pd.to_numeric, errors="coerce")
    # value AT the hour boundary = last 5-min snapshot stamped <= that hour (label right, closed right)
    h = df[num].resample("1h", label="right", closed="right").last().reset_index()
    h.to_csv(OUT / f"{sym}_oi_1h.csv.gz", index=False)
    return h, sum(p is not None for p in parts), len(urls)

if __name__ == "__main__":
    man = {"source": BASE, "fetched_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "checksum": "sha256 verified per file", "symbols": {}}
    for s in SYMS:
        k, nk = klines(s); f, nf = funding(s); o, no, nt = oi(s)
        exp = pd.date_range(k.time.min(), k.time.max(), freq="1h")
        man["symbols"][s] = {
            "klines_1h": {"rows": len(k), "first": str(k.time.min()), "last": str(k.time.max()), "files": nk, "missing_hours": int(len(exp) - len(k))},
            "funding": {"rows": len(f), "first": str(f.time.min()), "last": str(f.time.max()), "files": nf},
            "open_interest_1h": {"rows": len(o), "first": str(o.time.min()), "last": str(o.time.max()), "daily_files_found": no, "daily_files_expected": nt, "null_oi_hours": int(o.sum_open_interest.isna().sum())},
        }
        print(s, json.dumps(man["symbols"][s]), flush=True)
    json.dump(man, open(OUT / "MANIFEST.json", "w"), indent=1)
