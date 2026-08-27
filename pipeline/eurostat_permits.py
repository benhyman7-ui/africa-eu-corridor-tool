"""Eurostat bilateral first-permit flows - the dependent variable.

migr_resocc  first permits issued for REMUNERATED ACTIVITIES, split by permit
             category (EU Blue Card / highly-skilled / researchers / seasonal /
             other employment), by citizenship x member state x year.
migr_resfirst all-reason first permits (employment / family / education / other),
             for context and for the share-of-total denominator.

We pull the FULL world panel, not just Africa: the gravity model is estimated on
all ~178 reported citizenships so the coefficients are identified off global
variation, then predicted onto the 1,458 Africa-EU corridors. Estimating on
Africa alone would fold the very anomaly we are hunting into the coefficients.

Eurostat distinguishes "not available" (:) from a true zero. Conflating them
would manufacture underutilisation signals, so we keep the flag column and drop
missing rather than filling it with 0.
"""
from __future__ import annotations

import pandas as pd

from _common import countries, eurostat, save, step

YEARS = list(range(2018, 2025))

REASONS = {
    "EMP": "All employment",
    "EMP_BLUE": "EU Blue Card",
    "EMP_HSW": "Highly skilled workers",
    "EMP_RSE": "Researchers",
    "EMP_WRK_SEAS": "Seasonal workers",
    "EMP_OTH": "Other employment",
}


def _pull(dataset: str, geos: list[str], extra: dict) -> pd.DataFrame:
    frames = []
    for i, g in enumerate(geos, 1):
        for y in YEARS:
            try:
                df = eurostat(dataset, geo=g, time=y, **extra)
            except Exception as exc:  # noqa: BLE001
                print(f"      {dataset} {g} {y}: {exc}")
                continue
            if len(df):
                frames.append(df)
        print(f"      [{i:>2}/{len(geos)}] {g}", end="\r", flush=True)
    print(" " * 40, end="\r")
    if not frames:
        raise RuntimeError(f"{dataset}: nothing returned")
    return pd.concat(frames, ignore_index=True)


def main() -> None:
    cc = countries()
    dests = cc[cc.side == "destination"].eurostat_code.tolist()

    step("migr_resocc - employment permits by category (world panel, 2018-2024)")
    occ = _pull("migr_resocc", dests, {"duration": "TOTAL", "unit": "PER"})
    occ = occ.rename(columns={"time": "year", "geo": "dest_es", "citizen": "orig_es",
                              "reason": "permit_category"})
    occ["year"] = occ["year"].astype(int)
    occ = occ[["dest_es", "orig_es", "permit_category", "year", "value", "flag"]]
    # ':' arrives as a null value with a flag; keep only genuinely reported cells.
    occ = occ[occ["value"].notna()]
    occ["value"] = occ["value"].astype(float)
    save(occ, "eurostat_permits_employment")

    step("migr_resfirst - all-reason first permits (context + denominators)")
    first = _pull("migr_resfirst", dests, {"duration": "TOTAL", "unit": "PER"})
    first = first.rename(columns={"time": "year", "geo": "dest_es", "citizen": "orig_es",
                                  "reason": "reason"})
    first["year"] = first["year"].astype(int)
    first = first[["dest_es", "orig_es", "reason", "year", "value", "flag"]]
    first = first[first["value"].notna()]
    first["value"] = first["value"].astype(float)
    save(first, "eurostat_permits_allreason")

    # ---- reporting -------------------------------------------------------- #
    origins = set(cc[cc.side == "origin"].eurostat_code)
    afr = occ[occ.orig_es.isin(origins)]
    tot = afr[afr.permit_category == "EMP"]
    latest = tot[tot.year == tot.year.max()]
    print(f"    world panel: {len(occ):,} cells, {occ.orig_es.nunique()} citizenships")
    print(f"    Africa-EU:   {len(afr):,} cells across {len(YEARS)} years")
    print(f"    {int(latest.year.iloc[0])} Africa->EU employment permits: "
          f"{latest.value.sum():,.0f}")
    print(f"    corridors with any employment permit ever: "
          f"{tot[tot.value > 0].groupby(['orig_es', 'dest_es']).ngroups:,} / 1,458")
    by_cat = afr.groupby("permit_category").value.sum().sort_values(ascending=False)
    for k, v in by_cat.items():
        print(f"      {REASONS.get(k, k):<24} {v:>12,.0f}")


if __name__ == "__main__":
    main()
