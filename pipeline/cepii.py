"""CEPII GeoDist - the classical gravity regressors.

Distance, contiguity, shared official/ethnic language, colonial relationship and
common coloniser. Ships as an .xls inside the zip (not the .dta the docs imply).
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd

from _common import countries, fetch, save, step

URL = "http://www.cepii.fr/distance/dist_cepii.zip"

KEEP = ["iso_o", "iso_d", "contig", "comlang_off", "comlang_ethno",
        "colony", "comcol", "curcol", "col45", "smctry",
        "dist", "distcap", "distw", "distwces"]

# GeoDist ships 2011-vintage codes: Zaire for DR Congo, ROM for Romania, and no
# South Sudan at all (independent 2011). South Sudan is filled by the haversine
# fallback in model/assemble.py.
ALIAS = {"ZAR": "COD", "ROM": "ROU", "TMP": "TLS", "YUG": "SRB"}


def main() -> None:
    step("CEPII GeoDist")
    raw = fetch(URL, suffix=".zip")
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        member = next(n for n in z.namelist() if n.lower().endswith((".xls", ".xlsx")))
        df = pd.read_excel(io.BytesIO(z.read(member)))

    df = df[[c for c in KEEP if c in df.columns]].copy()
    df = df.rename(columns={"iso_o": "orig", "iso_d": "dest"})
    df["orig"] = df["orig"].replace(ALIAS)
    df["dest"] = df["dest"].replace(ALIAS)
    # CEPII writes Stata missings as '.', which makes the column object dtype.
    for c in df.columns:
        if c not in ("orig", "dest"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
    save(df, "cepii_geodist")

    cc = countries()
    o = set(cc[cc.side == "origin"].iso3)
    d = set(cc[cc.side == "destination"].iso3)
    pair = df[df.orig.isin(o) & df.dest.isin(d)]
    print(f"    {len(df):,} global pairs; {len(pair):,} / 1,458 Africa-EU pairs matched")
    miss_o = o - set(pair.orig)
    miss_d = d - set(pair.dest)
    if miss_o or miss_d:
        print(f"    NOT in GeoDist -> origins {sorted(miss_o)}  dests {sorted(miss_d)}")
    print(f"    corridors sharing an official language: {int(pair.comlang_off.sum()):,}")
    print(f"    corridors with a colonial relationship: {int(pair.colony.sum()):,}")
    print(f"    median distance: {pair.dist.median():,.0f} km")


if __name__ == "__main__":
    main()
