"""OpenFlights - direct air connectivity between country pairs.

A corridor with no direct flight is materially more expensive to traverse, and
route count is the standard connectivity proxy in the gravity literature.
"""
from __future__ import annotations

import io

import pandas as pd

from _common import countries, fetch, fetch_json, save, step

AIRPORTS = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/airports.dat"
ISO_URL = ("https://raw.githubusercontent.com/lukes/"
           "ISO-3166-Countries-with-Regional-Codes/master/all/all.json")
ROUTES = "https://raw.githubusercontent.com/jpatokal/openflights/master/data/routes.dat"

A_COLS = ["id", "name", "city", "country", "iata", "icao", "lat", "lon",
          "alt", "tz", "dst", "tzdb", "type", "source"]
R_COLS = ["airline", "airline_id", "src", "src_id", "dst", "dst_id",
          "codeshare", "stops", "equipment"]


def main() -> None:
    step("OpenFlights airports + routes")
    air = pd.read_csv(io.BytesIO(fetch(AIRPORTS, suffix=".csv")), header=None,
                      names=A_COLS, na_values=["\\N"])
    rte = pd.read_csv(io.BytesIO(fetch(ROUTES, suffix=".csv")), header=None,
                      names=R_COLS, na_values=["\\N"])

    # OpenFlights identifies countries by name. The gravity model is estimated on
    # the whole world, so the name -> ISO3 map has to cover the whole world, not
    # just the 81 countries in our corridor spine.
    cc = countries()
    name2iso = {}
    for rec in fetch_json(ISO_URL):
        name2iso[rec["name"].lower()] = rec["alpha-3"]
        # ISO writes inverted forms: "Korea, Republic of"
        if "," in rec["name"]:
            head, tail = rec["name"].split(",", 1)
            name2iso[f"{tail.strip()} {head.strip()}".lower()] = rec["alpha-3"]
    for r in cc.itertuples():
        for n in (r.name, r.iso_name):
            if isinstance(n, str) and n:
                name2iso[n.lower()] = r.iso3
    # OpenFlights spellings that differ from ISO
    name2iso.update({
        "congo (kinshasa)": "COD", "congo (brazzaville)": "COG",
        "cote d'ivoire": "CIV", "ivory coast": "CIV", "cape verde": "CPV",
        "gambia": "GMB", "swaziland": "SWZ", "czech republic": "CZE",
        "sao tome and principe": "STP", "central african republic": "CAF",
        "south korea": "KOR", "north korea": "PRK", "russia": "RUS",
        "iran": "IRN", "syria": "SYR", "laos": "LAO", "vietnam": "VNM",
        "taiwan": "TWN", "macau": "MAC", "hong kong": "HKG",
        "bolivia": "BOL", "venezuela": "VEN", "tanzania": "TZA",
        "moldova": "MDA", "brunei": "BRN", "burma": "MMR", "myanmar": "MMR",
        "united states": "USA", "united kingdom": "GBR", "turkey": "TUR",
        "cape verde islands": "CPV", "west bank": "PSE", "palestine": "PSE",
    })
    air["iso3"] = air["country"].str.lower().map(name2iso)
    unmapped = air[air.iso3.isna()].country.value_counts()
    if len(unmapped):
        print(f"      unmapped airport countries (top): {dict(unmapped.head(6))}")

    iata2iso = dict(zip(air.loc[air.iata.notna(), "iata"],
                        air.loc[air.iata.notna(), "iso3"]))
    rte["o"] = rte["src"].map(iata2iso)
    rte["d"] = rte["dst"].map(iata2iso)
    rte = rte.dropna(subset=["o", "d"])
    rte = rte[rte.o != rte.d]

    # route = a unique (airline, origin airport, destination airport) triple
    pair = (rte.groupby(["o", "d"])
               .agg(routes=("airline", "size"), airlines=("airline", "nunique"))
               .reset_index())
    apairs = (rte[["o", "d", "src", "dst"]].drop_duplicates()
                 .groupby(["o", "d"]).size().rename("airport_pairs").reset_index())
    pair = (pair.merge(apairs, on=["o", "d"], how="left")
                .rename(columns={"o": "orig", "d": "dest"}))

    # air links are effectively symmetric for accessibility purposes
    rev = pair.rename(columns={"orig": "dest", "dest": "orig"})
    both = (pd.concat([pair, rev])
              .groupby(["orig", "dest"], as_index=False)
              .agg({"routes": "max", "airlines": "max", "airport_pairs": "max"}))
    save(both, "openflights_pairs")

    o = set(cc[cc.side == "origin"].iso3)
    d = set(cc[cc.side == "destination"].iso3)
    sub = both[both.orig.isin(o) & both.dest.isin(d)]
    print(f"    {len(both):,} connected country pairs worldwide")
    print(f"    Africa-EU corridors with a direct flight: {len(sub):,} / 1,458 "
          f"({len(sub) / 1458:.0%})")
    top = sub.nlargest(5, "routes")
    nm = dict(zip(cc.iso3, cc.name))
    for r in top.itertuples():
        print(f"      {nm.get(r.orig, r.orig)} -> {nm.get(r.dest, r.dest)}: "
              f"{r.routes} routes, {r.airlines} airlines")


if __name__ == "__main__":
    main()
