"""Shared plumbing for every fetcher: cached HTTP, JSON-stat parsing, paths."""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REF = ROOT / "ref"
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
CACHE = ROOT / "data" / "cache"
WEBDATA = ROOT / "web" / "data"

for _d in (RAW, INTERIM, CACHE, WEBDATA, REF):
    _d.mkdir(parents=True, exist_ok=True)

UA = "AJF-CorridorTool/1.0 (research; contact via repo)"
EUROSTAT = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"


# --------------------------------------------------------------------------- #
# HTTP with on-disk cache
# --------------------------------------------------------------------------- #
def _cache_path(url: str, suffix: str) -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    return CACHE / f"{h}{suffix}"


def fetch(url: str, *, suffix: str = ".bin", refresh: bool = False,
          headers: dict | None = None, tries: int = 4, timeout: int = 120) -> bytes:
    """GET with an on-disk cache. Second run of the pipeline is offline."""
    cp = _cache_path(url, suffix)
    if cp.exists() and not refresh and cp.stat().st_size > 0:
        return cp.read_bytes()

    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)

    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            cp.write_bytes(body)
            return body
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code == 429:
                # Wikidata's query service throttles to 1 request/minute during
                # outages. A 1.5s backoff just burns the retry budget, so honour
                # Retry-After when it is sent and wait out the window otherwise.
                wait = int(exc.headers.get("Retry-After") or 0) or 65
                print(f"      rate-limited (429), waiting {wait}s "
                      f"[{attempt + 1}/{tries}]", flush=True)
                time.sleep(wait)
            else:
                time.sleep(1.5 * (attempt + 1))
        except Exception as exc:  # noqa: BLE001 - retry on anything transient
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"fetch failed after {tries} tries: {url}\n  {last}")


def fetch_json(url: str, **kw) -> dict:
    return json.loads(fetch(url, suffix=".json", **kw).decode("utf-8"))


def fetch_csv(url: str, *, sep: str = ",", **kw) -> pd.DataFrame:
    data = fetch(url, suffix=".csv", **kw)
    return pd.read_csv(io.BytesIO(data), sep=sep, low_memory=False)


def fetch_zip_member(url: str, member_match: str, **kw) -> bytes:
    """Download a zip, return the first member whose name contains `member_match`."""
    data = fetch(url, suffix=".zip", **kw)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if member_match.lower() in name.lower():
                return z.read(name)
        raise KeyError(f"no member matching {member_match!r} in {z.namelist()[:20]}")


# --------------------------------------------------------------------------- #
# Eurostat JSON-stat
# --------------------------------------------------------------------------- #
def eurostat(dataset: str, refresh: bool = False, **filters) -> pd.DataFrame:
    """Pull a Eurostat dataset and flatten JSON-stat 2.0 into a tidy frame.

    Repeated filter values are passed as lists, e.g. geo=["DE", "FR"].
    Returns columns: one per dimension (codes), plus `value` and `flag`.
    """
    parts = [("format", "JSON")]
    for k, v in filters.items():
        if isinstance(v, (list, tuple, set)):
            parts.extend((k, str(x)) for x in v)
        else:
            parts.append((k, str(v)))
    url = f"{EUROSTAT}/{dataset}?" + urllib.parse.urlencode(parts)
    doc = json.loads(fetch(url, suffix=".json", refresh=refresh).decode("utf-8"))

    if "error" in doc:
        raise RuntimeError(f"Eurostat error for {dataset}: {doc['error']}")

    dims = doc["id"]
    sizes = doc["size"]
    # index -> code, per dimension, ordered
    codes: list[list[str]] = []
    for d in dims:
        idx = doc["dimension"][d]["category"]["index"]
        if isinstance(idx, dict):
            ordered = sorted(idx.items(), key=lambda kv: kv[1])
            codes.append([k for k, _ in ordered])
        else:  # already a list
            codes.append(list(idx))

    # strides for row-major unflattening
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]

    status = doc.get("status", {}) or {}
    rows = []
    for flat_idx, val in doc.get("value", {}).items():
        n = int(flat_idx)
        rec = {}
        rem = n
        for i, d in enumerate(dims):
            rec[d] = codes[i][rem // strides[i]]
            rem = rem % strides[i]
        rec["value"] = val
        rec["flag"] = status.get(flat_idx) or status.get(str(n)) or ""
        rows.append(rec)

    df = pd.DataFrame(rows, columns=[*dims, "value", "flag"])
    return df


def eurostat_labels(dataset: str, dimension: str, refresh: bool = False, **filters) -> dict:
    """Return {code: label} for one dimension of a dataset."""
    parts = [("format", "JSON")]
    for k, v in filters.items():
        if isinstance(v, (list, tuple, set)):
            parts.extend((k, str(x)) for x in v)
        else:
            parts.append((k, str(v)))
    url = f"{EUROSTAT}/{dataset}?" + urllib.parse.urlencode(parts)
    doc = json.loads(fetch(url, suffix=".json", refresh=refresh).decode("utf-8"))
    return doc["dimension"][dimension]["category"]["label"]


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def save(df: pd.DataFrame, name: str, folder: Path = RAW) -> Path:
    p = folder / f"{name}.parquet"
    df.to_parquet(p, index=False)
    print(f"    saved {name}: {len(df):,} rows -> {p.relative_to(ROOT)}")
    return p


def load(name: str, folder: Path = RAW) -> pd.DataFrame:
    return pd.read_parquet(folder / f"{name}.parquet")


def exists(name: str, folder: Path = RAW) -> bool:
    return (folder / f"{name}.parquet").exists()


def countries() -> pd.DataFrame:
    return pd.read_csv(REF / "countries.csv", encoding="utf-8", keep_default_na=False,
                       na_values=[""])


def step(msg: str) -> None:
    print(f"  - {msg}", flush=True)
