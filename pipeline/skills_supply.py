"""Skills supply at origin.

The panel already knew how many idle working-age people a country has. It knew
nothing about whether any of them were trained for anything, so a country with
twenty million unemployed young people and no training system scored the same as
one with a large nursing college sector. Three sources close that:

UNESCO UIS  tertiary graduates by ISCED field of study, and the share of 15-24
            year-olds in vocational education (TVET). The field codes map onto
            the tool's occupation families, which is what lets the sector
            overlay finally use origin-side supply instead of destination-side
            demand alone.
WHO GHO     health workforce density - nurses, midwives, doctors per 10,000.
            The one occupation where a direct headcount exists, and the one the
            sector overlay leans on hardest.
World Bank  vocational share of secondary enrolment, tertiary attainment and
            literacy, which were already being fetched and used nowhere.

Everything here is a STOCK measure of who exists, not a claim that they want to
move or could get a visa.
"""
from __future__ import annotations

import json
import time

import pandas as pd

from _common import countries, fetch, save, step

UIS = "https://api.uis.unesco.org/api/public/data/indicators"

# ISCED-F broad fields, named for the occupation families they feed.
UIS_FIELDS = {
    "FOSGP.5T8.F900": "grad_health_welfare",
    "FOSGP.5T8.F600": "grad_ict",
    "FOSGP.5T8.F700": "grad_engineering_construction",
    "FOSGP.5T8.F800": "grad_agriculture",
    "FOSGP.5T8.F1000": "grad_services",
    "FOSGP.5T8.F500": "grad_natural_sciences",
    "FOSGP.5T8.F400": "grad_business_law",
    "FOSGP.5T8.F110": "grad_education",
    "FOSGP.5T8.F500600700": "grad_stem",
    "EV1524P.2T5.V": "vocational_enrolment_share",
}

# WHO Global Health Observatory, density per 10,000 population.
GHO = {
    "HWF_0006": "nurses_midwives_per_10k",
    "HWF_0001": "doctors_per_10k",
}
GHO_URL = "https://ghoapi.azureedge.net/api/{code}"


def _uis(codes: list[str], geos: list[str]) -> pd.DataFrame:
    rows = []
    for i in range(0, len(geos), 20):
        chunk = geos[i:i + 20]
        q = ("&".join(f"indicator={c}" for c in codes) + "&"
             + "&".join(f"geoUnit={g}" for g in chunk)
             + "&start=2012&end=2025")
        doc = json.loads(fetch(f"{UIS}?{q}", suffix=".json", timeout=180).decode("utf-8"))
        rows += doc.get("records", []) or []
        time.sleep(0.5)
    return pd.DataFrame(rows)


def main() -> None:
    cc = countries()
    geos = cc.iso3.tolist()

    # ------------------------------------------------------------- UNESCO --
    step(f"UNESCO UIS - graduates by field and vocational enrolment "
         f"({len(UIS_FIELDS)} series)")
    u = _uis(list(UIS_FIELDS), geos)
    if len(u):
        u = u.rename(columns={"indicatorId": "code", "geoUnit": "iso3"})
        u["field"] = u.code.map(UIS_FIELDS)
        u = u[u.field.notna() & u.value.notna()]
        # most recent year per country x series
        latest = (u.sort_values("year").groupby(["iso3", "field"], as_index=False).last())
        wide = latest.pivot(index="iso3", columns="field", values="value")
        yrs = latest.pivot(index="iso3", columns="field", values="year")
        wide["uis_year"] = yrs.max(axis=1)
        wide = wide.reset_index()
        save(wide, "uis_skills")
        o = wide[wide.iso3.isin(cc[cc.side == "origin"].iso3)]
        print(f"    {len(wide)} countries; {len(o)} of 54 origins")
        for f in ["grad_health_welfare", "grad_ict", "grad_engineering_construction",
                  "vocational_enrolment_share"]:
            if f in o:
                print(f"      {f:<32}{o[f].notna().sum():>3}/54 origins  "
                      f"median {o[f].median():.1f}%")
    else:
        print("    UIS returned no records")

    # ---------------------------------------------------------------- WHO --
    step("WHO Global Health Observatory - health workforce density")
    frames = []
    for code, name in GHO.items():
        try:
            doc = json.loads(fetch(GHO_URL.format(code=code), suffix=".json",
                                   timeout=180).decode("utf-8"))
            v = pd.DataFrame(doc.get("value", []))
            if not len(v):
                print(f"      {code}: empty")
                continue
            v = v[["SpatialDim", "TimeDim", "NumericValue"]].rename(
                columns={"SpatialDim": "iso3", "TimeDim": "year", "NumericValue": name})
            v = v[v[name].notna()]
            v = v.sort_values("year").groupby("iso3", as_index=False).last()
            frames.append(v[["iso3", name]])
        except Exception as exc:  # noqa: BLE001
            print(f"      {code}: {str(exc)[:70]}")
    if frames:
        w = frames[0]
        for f in frames[1:]:
            w = w.merge(f, on="iso3", how="outer")
        w = w[w.iso3.isin(geos)]
        save(w, "who_workforce")
        o = w[w.iso3.isin(cc[cc.side == "origin"].iso3)]
        d = w[w.iso3.isin(cc[cc.side == "destination"].iso3)]
        nm = dict(zip(cc.iso3, cc.name))
        print(f"    {len(w)} countries; origins {len(o)}, destinations {len(d)}")
        if "nurses_midwives_per_10k" in w:
            print(f"    nurses+midwives per 10k: origin median "
                  f"{o.nurses_midwives_per_10k.median():.1f}, destination median "
                  f"{d.nurses_midwives_per_10k.median():.1f}")
            top = o.nlargest(5, "nurses_midwives_per_10k")
            print("    best-staffed origins:")
            for r in top.itertuples():
                print(f"      {nm.get(r.iso3, r.iso3):<22}{r.nurses_midwives_per_10k:>6.1f}")


if __name__ == "__main__":
    main()
