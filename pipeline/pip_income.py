"""World Bank Poverty and Inequality Platform - median income.

GNI per capita is a mean over the whole economy and is pulled upward by the top
of the distribution, so it overstates what a typical worker actually earns on
both sides of a corridor. PIP publishes the MEDIAN daily welfare in PPP terms,
which is much closer to the income of the person who would actually move.

One comparability caveat that cannot be engineered away: PIP reports consumption
for most African countries and income for most European ones, because that is
what the underlying surveys measure. The welfare_type column is carried through
so the difference stays visible rather than buried.
"""
from __future__ import annotations

import json

import pandas as pd

from _common import countries, fetch, save, step

URL = ("https://api.worldbank.org/pip/v1/pip"
       "?country=all&year=MRV&povline=2.15&fill_gaps=true&format=json"
       "&ppp_version=2021")

DAYS = 365.0


def main() -> None:
    step("World Bank PIP - median daily welfare, 2021 PPP")
    rows = json.loads(fetch(URL, suffix=".json", timeout=180).decode("utf-8"))
    df = pd.DataFrame(rows)

    keep = ["country_code", "reporting_year", "welfare_type", "median", "mean",
            "gini", "headcount"]
    df = df[[c for c in keep if c in df.columns]].rename(
        columns={"country_code": "iso3", "reporting_year": "income_year",
                 "median": "median_daily", "mean": "mean_daily"})
    df = df[df.median_daily.notna() & (df.median_daily > 0)]
    df["median_annual_ppp"] = df.median_daily * DAYS
    df = df.sort_values("income_year").groupby("iso3", as_index=False).last()
    save(df, "pip_income")

    cc = countries()
    sub = df[df.iso3.isin(cc.iso3)]
    nm = dict(zip(cc.iso3, cc.name))
    miss = sorted(set(cc.iso3) - set(sub.iso3))
    print(f"    {len(sub)} / {len(cc)} corridor countries covered"
          f"{'' if not miss else '  missing: ' + str([nm.get(m, m) for m in miss])}")
    o = sub[sub.iso3.isin(cc[cc.side == 'origin'].iso3)]
    d = sub[sub.iso3.isin(cc[cc.side == 'destination'].iso3)]
    print(f"    origin median      ${o.median_annual_ppp.median():,.0f}/yr "
          f"(range ${o.median_annual_ppp.min():,.0f}-${o.median_annual_ppp.max():,.0f})")
    print(f"    destination median ${d.median_annual_ppp.median():,.0f}/yr "
          f"(range ${d.median_annual_ppp.min():,.0f}-${d.median_annual_ppp.max():,.0f})")
    mix = sub.groupby("welfare_type").size().to_dict()
    print(f"    welfare basis: {mix}")


if __name__ == "__main__":
    main()
