"""Two push factors the concept note lists as "in hand" that were missing here.

UCDP/PRIO armed conflict  - country-year conflict intensity and battle deaths at
                            origin. The gravity specification names conflict as a
                            fundamental; until now it was not in the panel at all.
UNHCR Refugee Data Finder - bilateral refugee and asylum-seeker stocks. This one
                            matters more than it looks: a corridor carrying large
                            refugee or asylum numbers has REVEALED demand that the
                            legal labour channel is not capturing, which is a
                            direct signal of underutilisation.

The UNHCR API needs coo_all/coa_all flags; plain coo=/coa= filters silently
return an empty item list rather than an error.
"""
from __future__ import annotations

import json

import pandas as pd

from _common import countries, fetch, save, step

UCDP = ("https://ucdpapi.pcr.uu.se/api/gedevents/24.1"
        "?pagesize=1000&StartDate=2018-01-01&EndDate=2024-12-31")
UNHCR = "https://api.unhcr.org/population/v1/population/"
YEARS = list(range(2018, 2025))


def _unhcr_year(year: int, coa: str) -> list[dict]:
    url = (f"{UNHCR}?year={year}&coa={coa}&coo_all=true&limit=1000&page=1")
    doc = json.loads(fetch(url, suffix=".json", timeout=120).decode("utf-8"))
    items = doc.get("items", []) or []
    pages = int(doc.get("maxPages") or 1)
    for p in range(2, min(pages, 6) + 1):
        u = f"{UNHCR}?year={year}&coa={coa}&coo_all=true&limit=1000&page={p}"
        items += json.loads(fetch(u, suffix=".json", timeout=120).decode("utf-8")).get("items", []) or []
    return items


def main() -> None:
    cc = countries()
    dests = cc[cc.side == "destination"].iso3.tolist()
    origins = set(cc[cc.side == "origin"].iso3)

    # ---------------------------------------------------------------- UNHCR --
    step(f"UNHCR bilateral refugee and asylum stocks, {YEARS[0]}-{YEARS[-1]}")
    rows = []
    for i, d in enumerate(dests, 1):
        for y in YEARS:
            try:
                rows += _unhcr_year(y, d)
            except Exception as exc:  # noqa: BLE001
                print(f"      {d} {y}: {str(exc)[:70]}")
        print(f"      [{i:>2}/{len(dests)}] {d}", end="\r", flush=True)
    print(" " * 40, end="\r")

    if rows:
        u = pd.DataFrame(rows)
        num = lambda s: pd.to_numeric(s, errors="coerce").fillna(0)  # noqa: E731
        u = u.rename(columns={"coo_iso": "orig", "coa_iso": "dest"})
        u["refugees"] = num(u.get("refugees"))
        u["asylum_seekers"] = num(u.get("asylum_seekers"))
        u = u[u.orig.isin(origins) & u.dest.isin(dests)]
        latest = u[u.year == u.year.max()]
        agg = (latest.groupby(["orig", "dest"], as_index=False)
                     .agg(refugees=("refugees", "sum"),
                          asylum_seekers=("asylum_seekers", "sum")))
        agg["forced_total"] = agg.refugees + agg.asylum_seekers
        agg["unhcr_year"] = int(latest.year.max())
        save(agg, "unhcr_bilateral")
        print(f"    {len(agg):,} Africa-EU pairs; "
              f"{agg.forced_total.sum():,.0f} refugees + asylum seekers in "
              f"{int(latest.year.max())}")
        top = agg.nlargest(5, "forced_total")
        nm = dict(zip(cc.iso3, cc.name))
        for r in top.itertuples():
            print(f"      {nm.get(r.orig, r.orig)} -> {nm.get(r.dest, r.dest)}: "
                  f"{r.forced_total:,.0f}")
    else:
        print("    UNHCR returned nothing; skipping")

    # ----------------------------------------------------------------- UCDP --
    step("UCDP georeferenced event dataset - battle deaths at origin")
    try:
        events, url, page = [], UCDP, 0
        while url and page < 40:
            doc = json.loads(fetch(url, suffix=".json", timeout=180).decode("utf-8"))
            events += doc.get("Result", []) or []
            url = doc.get("NextPageUrl") or ""
            page += 1
            print(f"      page {page}, {len(events):,} events", end="\r", flush=True)
        print(" " * 44, end="\r")
        e = pd.DataFrame(events)
        if len(e):
            e["iso3"] = e.get("country_id")  # replaced below by name join
            e["deaths"] = pd.to_numeric(e.get("best"), errors="coerce").fillna(0)
            e["year"] = pd.to_numeric(e.get("year"), errors="coerce")
            name2iso = {r.name.lower(): r.iso3 for r in cc.itertuples()}
            name2iso.update({"dr congo (zaire)": "COD", "ivory coast": "CIV",
                             "madagascar (malagasy)": "MDG", "zimbabwe (rhodesia)": "ZWE",
                             "tanzania": "TZA", "cambodia (kampuchea)": "KHM"})
            e["iso3"] = e["country"].str.lower().map(name2iso)
            e = e.dropna(subset=["iso3"])
            g = (e.groupby(["iso3", "year"], as_index=False)
                   .agg(battle_deaths=("deaths", "sum"), events=("deaths", "size")))
            recent = g[g.year >= g.year.max() - 4]
            out = (recent.groupby("iso3", as_index=False)
                         .agg(battle_deaths_5yr=("battle_deaths", "sum"),
                              conflict_events_5yr=("events", "sum")))
            out["conflict_any"] = (out.battle_deaths_5yr > 25).astype(int)
            save(out, "ucdp_conflict")
            nm = dict(zip(cc.iso3, cc.name))
            print(f"    {len(out)} countries with recorded conflict events")
            for r in out.nlargest(5, "battle_deaths_5yr").itertuples():
                print(f"      {nm.get(r.iso3, r.iso3):<22}{r.battle_deaths_5yr:>9,.0f} deaths")
    except Exception as exc:  # noqa: BLE001
        print(f"    UCDP unavailable ({type(exc).__name__}: {str(exc)[:90]}); skipping")


if __name__ == "__main__":
    main()
