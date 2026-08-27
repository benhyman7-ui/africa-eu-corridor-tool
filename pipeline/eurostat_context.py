"""Eurostat context series.

Two jobs:
  1. Bilateral diaspora - migr_pop1ctz gives the resident population of each EU
     member state broken out by citizenship. For an EU-facing corridor model this
     is a better network variable than UN DESA's global stock file: it is annual,
     EU-consistent, and it is the population that actually pulls newcomers in.
     (UN DESA's 2020 stock workbook returns 403 to programmatic clients.)
  2. Destination labour demand - unemployment, job-vacancy rate by sector,
     compensation per employee by sector, and the old-age dependency ratio.
"""
from __future__ import annotations

import pandas as pd

from _common import countries, eurostat, save, step

YEARS = list(range(2018, 2025))

# NACE A*10 sectors mapped to the tool's occupation families.
NACE = {
    "A": "Agriculture", "B-E": "Manufacturing", "F": "Construction",
    "G-I": "Hospitality & trade", "J": "ICT", "K": "Finance",
    "L": "Real estate", "M_N": "Professional services",
    "O-Q": "Health, care & public", "R-U": "Other services",
}


def _many(dataset: str, geos: list[str], per_year: bool, **extra) -> pd.DataFrame:
    frames = []
    for i, g in enumerate(geos, 1):
        targets = YEARS if per_year else [None]
        for y in targets:
            kw = dict(extra)
            if y is not None:
                kw["time"] = y
            try:
                df = eurostat(dataset, geo=g, **kw)
            except Exception as exc:  # noqa: BLE001
                print(f"      {dataset} {g} {y}: {str(exc)[:90]}")
                continue
            if len(df):
                frames.append(df)
        print(f"      [{i:>2}/{len(geos)}] {g}", end="\r", flush=True)
    print(" " * 40, end="\r")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main() -> None:
    cc = countries()
    dests = cc[cc.side == "destination"].eurostat_code.tolist()

    # ---- 1. bilateral diaspora ------------------------------------------- #
    step("migr_pop1ctz - resident population by citizenship (diaspora)")
    dia = _many("migr_pop1ctz", dests, True, age="TOTAL", sex="T", unit="NR")
    if len(dia):
        dia = (dia.rename(columns={"time": "year", "geo": "dest_es",
                                   "citizen": "orig_es"})
                  .loc[:, ["dest_es", "orig_es", "year", "value", "flag"]])
        dia["year"] = dia["year"].astype(int)
        dia = dia[dia["value"].notna()]
        save(dia, "eurostat_diaspora")
        origins = set(cc[cc.side == "origin"].eurostat_code)
        sub = dia[dia.orig_es.isin(origins)]
        last = sub[sub.year == sub.year.max()]
        print(f"    {len(dia):,} cells; African nationals resident in EU-27 "
              f"({int(last.year.iloc[0])}): {last.value.sum():,.0f}")

    # ---- 2. destination labour demand ------------------------------------ #
    step("une_rt_a - unemployment rate")
    une = _many("une_rt_a", dests, False, age="Y15-74", sex="T", unit="PC_ACT")
    if len(une):
        une = une.rename(columns={"time": "year", "geo": "dest_es"})
        une = une[une.year.astype(int).isin(YEARS)]
        save(une[["dest_es", "year", "value"]].assign(year=lambda d: d.year.astype(int)),
             "eurostat_unemployment")

    # This dataset has no s_adj dimension; passing one makes every request fail.
    step("jvs_a_rate_r2 - job vacancy rate by sector")
    jvs = _many("jvs_a_rate_r2", dests, False, sizeclas="TOTAL", unit="AVG_A",
                nace_r2=["A", "B-E", "F", "G-I", "J", "K", "L", "M_N", "O-Q", "R_S",
                         "A-S", "B-S"])
    if len(jvs):
        jvs = jvs.rename(columns={"time": "year", "geo": "dest_es"})
        jvs = jvs[jvs.year.astype(int).isin(YEARS)]
        jvs["year"] = jvs["year"].astype(int)
        save(jvs[["dest_es", "nace_r2", "year", "value"]], "eurostat_vacancy")

    step("nama_10_a10 - compensation of employees by sector")
    comp = _many("nama_10_a10", dests, False, na_item="D1", unit="CP_MEUR",
                 nace_r2=list(NACE) + ["TOTAL"])
    step("nama_10_a10_e - employment by sector")
    emp = _many("nama_10_a10_e", dests, False, na_item="EMP_DC", unit="THS_PER",
                nace_r2=list(NACE) + ["TOTAL"])
    if len(comp) and len(emp):
        c = (comp.rename(columns={"time": "year", "geo": "dest_es", "value": "comp_meur"})
                 [["dest_es", "nace_r2", "year", "comp_meur"]])
        e = (emp.rename(columns={"time": "year", "geo": "dest_es", "value": "emp_ths"})
                [["dest_es", "nace_r2", "year", "emp_ths"]])
        w = c.merge(e, on=["dest_es", "nace_r2", "year"])
        w["year"] = w["year"].astype(int)
        w = w[w.year.isin(YEARS) & (w.emp_ths > 0)]
        # EUR million / thousand persons -> EUR per person per year
        w["comp_per_worker_eur"] = w.comp_meur * 1e6 / (w.emp_ths * 1e3)
        save(w, "eurostat_sector_wages")
        latest = w[w.year == w.year.max()]
        print(f"    sector wage rows: {len(w):,}; "
              f"median compensation/worker {latest.comp_per_worker_eur.median():,.0f} EUR")

    step("demo_pjanind - old-age dependency ratio")
    dep = _many("demo_pjanind", dests, False, indic_de="OLDDEP1")
    if len(dep):
        dep = dep.rename(columns={"time": "year", "geo": "dest_es"})
        dep = dep[dep.year.astype(int).isin(YEARS)]
        save(dep[["dest_es", "year", "value"]].assign(year=lambda d: d.year.astype(int)),
             "eurostat_dependency")


if __name__ == "__main__":
    main()
