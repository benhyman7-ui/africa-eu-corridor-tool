"""Schengen short-stay visa statistics - DG HOME, consulate level.

Two things come out of this file, and the second is arguably more valuable than
the first.

1. REFUSAL RATES, bilateral. The share of uniform (Schengen C) visa applications
   refused, by Schengen state and by the country the application was lodged in.
   This is the most direct public measure of administrative friction on a
   corridor. It is short-stay, not work-permit, data - so it is a proxy, and it
   is labelled as one - but no comparable work-visa series exists.

2. WHERE APPLICATIONS CAN ACTUALLY BE LODGED. Every row is a real, operating
   consulate that processed visa applications in the reporting year. That is a
   better answer to "can an applicant apply in their own country" than Wikidata,
   which carries closed posts, so it takes precedence in the consular index.

Caveats worth knowing: Ireland is outside Schengen and never appears; Cyprus
reports its national visas separately. Both fall back to the Wikidata measure.
"""
from __future__ import annotations

import io
import re
import unicodedata

import pandas as pd

from _common import countries, fetch, fetch_json, save, step

# 2025 reporting year, published May 2026.
URL = "https://home-affairs.ec.europa.eu/document/download/56816949-e59f-442b-9091-5d03360bf860_en"
YEAR = 2025
ISO_URL = ("https://raw.githubusercontent.com/lukes/"
           "ISO-3166-Countries-with-Regional-Codes/master/all/all.json")

SHEET = "Data for consulates"
COL = {
    "Schengen State": "dest_name",
    "Country where consulate is located": "orig_name",
    "Consulate": "consulate",
    "Uniform visas applied for": "applications",
    "Uniform visas not issued": "refused",
    "Not issued rate for uniform visas": "refusal_rate",
}

ALIAS = {
    "cote divoire": "CIV", "cote d ivoire": "CIV", "ivory coast": "CIV",
    "congo democratic republic of the": "COD", "democratic republic of the congo": "COD",
    "congo dem rep": "COD", "drc": "COD",
    # DG HOME writes "CONGO (DEMOCRATIC REPUBLIC)"; _norm drops the brackets
    "congo democratic republic": "COD",
    "usa": "USA", "hong kong s a r": "HKG", "macao s a r": "MAC",
    "palestinian authority": "PSE",
    "congo": "COG", "republic of the congo": "COG", "congo brazzaville": "COG",
    "cape verde": "CPV", "cabo verde": "CPV",
    "gambia": "GMB", "the gambia": "GMB",
    "swaziland": "SWZ", "eswatini": "SWZ",
    "tanzania": "TZA", "united republic of tanzania": "TZA",
    "turkiye": "TUR", "turkey": "TUR",
    "russia": "RUS", "russian federation": "RUS",
    "south korea": "KOR", "korea republic of": "KOR",
    "united states": "USA", "united states of america": "USA",
    "united kingdom": "GBR", "great britain": "GBR",
    "bolivia": "BOL", "venezuela": "VEN", "iran": "IRN", "syria": "SYR",
    "vietnam": "VNM", "laos": "LAO", "moldova": "MDA", "brunei": "BRN",
    "kosovo": "XKX", "palestine": "PSE", "macedonia": "MKD",
    "north macedonia": "MKD", "czech republic": "CZE", "czechia": "CZE",
    "guinea bissau": "GNB", "sao tome and principe": "STP",
    "central african republic": "CAF", "equatorial guinea": "GNQ",
    "south africa": "ZAF", "south sudan": "SSD", "burkina faso": "BFA",
    "sierra leone": "SLE", "hong kong": "HKG", "taiwan": "TWN",
}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s.lower())).strip()


def main() -> None:
    cc = countries()

    step(f"DG HOME Schengen visa statistics, {YEAR} consulate data")
    raw = fetch(URL, suffix=".xlsx", timeout=180)
    book = pd.ExcelFile(io.BytesIO(raw))
    sheet = next((s for s in book.sheet_names if "consulate" in s.lower()), SHEET)
    df = pd.read_excel(book, sheet_name=sheet, header=0)
    df.columns = [str(c).strip() for c in df.columns]

    have = {c: COL[c] for c in COL if c in df.columns}
    missing = [c for c in COL if c not in df.columns]
    if missing:
        print(f"      columns not found (layout may have changed): {missing}")
    df = df[list(have)].rename(columns=have)

    step("resolving country names to ISO3")
    name2iso = {}
    for rec in fetch_json(ISO_URL):
        name2iso[_norm(rec["name"])] = rec["alpha-3"]
        if "," in rec["name"]:
            head, tail = rec["name"].split(",", 1)
            name2iso[_norm(f"{tail} {head}")] = rec["alpha-3"]
    for r in cc.itertuples():
        name2iso[_norm(r.name)] = r.iso3
        if isinstance(r.iso_name, str):
            name2iso[_norm(r.iso_name)] = r.iso3
    name2iso.update(ALIAS)

    df["orig"] = df.orig_name.map(lambda v: name2iso.get(_norm(v)))
    df["dest"] = df.dest_name.map(lambda v: name2iso.get(_norm(v)))
    unresolved = sorted(set(df.loc[df.orig.isna(), "orig_name"].dropna().astype(str)))
    if unresolved:
        print(f"      unresolved application countries ({len(unresolved)}): "
              f"{unresolved[:8]}")

    df = df.dropna(subset=["orig", "dest"])
    for c in ("applications", "refused"):
        df[c] = pd.to_numeric(df[c], errors="coerce")

    # Aggregate the consulates a state runs in one country into a country total,
    # then recompute the rate from the totals rather than averaging rates - a
    # 4-application post should not weigh the same as a 40,000-application one.
    g = (df.groupby(["dest", "orig"], as_index=False)
           .agg(visa_applications=("applications", "sum"),
                visa_refused=("refused", "sum"),
                visa_consulates=("consulate", "nunique")))
    g["visa_refusal_rate"] = (g.visa_refused / g.visa_applications.replace(0, pd.NA))
    g["visa_consulate_present"] = 1
    g["visa_year"] = YEAR
    save(g, "schengen_visas")

    o = set(cc[cc.side == "origin"].iso3)
    d = set(cc[cc.side == "destination"].iso3)
    sub = g[g.orig.isin(o) & g.dest.isin(d)]
    nm = dict(zip(cc.iso3, cc.name))
    print(f"    {len(g):,} destination-origin pairs worldwide; "
          f"{len(sub):,} / 1,458 Africa-EU")
    print(f"    Schengen states reporting: {g.dest.nunique()} "
          f"(absent: {sorted(nm.get(x, x) for x in d - set(g.dest))})")
    print(f"    Africa-EU applications {sub.visa_applications.sum():,.0f}, "
          f"refused {sub.visa_refused.sum():,.0f} "
          f"({sub.visa_refused.sum() / max(sub.visa_applications.sum(), 1):.1%})")

    worst = (sub[sub.visa_applications > 2000]
             .groupby("orig").apply(
                 lambda t: t.visa_refused.sum() / t.visa_applications.sum(),
                 include_groups=False)
             .sort_values(ascending=False).head(8))
    print("    highest refusal rates by origin (>2,000 applications):")
    for k, v in worst.items():
        print(f"      {nm.get(k, k):<24}{v:>6.1%}")


if __name__ == "__main__":
    main()
