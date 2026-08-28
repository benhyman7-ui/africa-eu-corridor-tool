"""Air connectivity from Eurostat airport-pair passenger statistics.

Replaces the OpenFlights route counts. OpenFlights is a community snapshot from
2014 that lists SCHEDULED routes only, and on this panel it returned zero direct
routes for 1,331 of 1,458 corridors - including Egypt-Poland and Morocco-Poland,
which plainly have service. A variable that is zero on 91% of the panel cannot
discriminate between corridors, and where it was non-zero it was a decade stale.

avia_par_<cc> reports passengers carried between each named airport in the
reporting country and each partner airport anywhere in the world. Two things
make it the right source here. It counts people rather than route entries, so a
daily widebody outweighs a weekly turboprop. And it covers non-scheduled
traffic, so the seasonal tourist charters that are much of the Europe-Africa
network - Hurghada, Agadir, Djerba, Zanzibar - are in it. The airport-pair code
carries the partner country in its third field, which is what makes the
country-level rollup possible: PL_EPWA_TZ_HTZA is Warsaw to Zanzibar.
"""
from __future__ import annotations

import pandas as pd

from _common import countries, eurostat, save

# Passengers carried, both directions, annual.
UNIT, MEASURE = "PAS", "PAS_CRD"
# Six years, because charter networks are intermittent: Warsaw-Zanzibar reports
# traffic in 2021 and in no other year, and a three-year window missed it
# entirely. The rollup takes the best single year rather than a sum, so the
# COVID years can only ever help a corridor that has no other observation.
YEARS = [2024, 2023, 2022, 2021, 2020, 2019]


def main() -> None:
    cc = countries()
    dests = cc[cc.side == "destination"]
    iso2_to_iso3 = dict(zip(cc.iso2, cc.iso3))
    origins = set(cc[cc.side == "origin"].iso3)

    frames, misses = [], []
    for row in dests.itertuples():
        es = (row.eurostat_code or row.iso2).lower()
        # Every year in the window, not just the most recent one. Seasonal
        # charters are intermittent by nature - Warsaw-Zanzibar reports traffic
        # in some years and none in others - and a corridor that carried a
        # charter last year is connected whether or not it ran again this year.
        per_year = []
        for yr in YEARS:
            try:
                df = eurostat(f"avia_par_{es}", unit=UNIT, tra_meas=MEASURE, time=yr)
            except Exception:  # noqa: BLE001
                continue
            if len(df):
                per_year.append(df.assign(year=yr))
        if not per_year:
            misses.append(row.iso3)
            continue
        got = pd.concat(per_year, ignore_index=True)

        # PL_EPWA_TZ_HTZA -> reporting PL, partner TZ. Codes that do not split
        # into four fields are aggregates and are dropped.
        parts = got.airp_pr.str.split("_")
        got = got[parts.str.len() == 4].copy()
        got["partner_iso2"] = got.airp_pr.str.split("_").str[2]
        got["orig"] = got.partner_iso2.map(iso2_to_iso3)
        got = got[got.orig.isin(origins)]
        got["dest"] = row.iso3
        frames.append(got[["orig", "dest", "year", "value"]])
        print(f"      {row.iso3}: {got.orig.nunique():>2} African partners "
              f"across {got.year.nunique()} years")

    if not frames:
        raise RuntimeError("no airport-pair data returned for any destination")

    # Sum within a year, then take the best year: summing across years would
    # count the same annual traffic three times over.
    per = (pd.concat(frames, ignore_index=True)
             .groupby(["orig", "dest", "year"], as_index=False).value.sum())
    air = (per.sort_values("value", ascending=False)
              .groupby(["orig", "dest"], as_index=False)
              .first().rename(columns={"value": "air_passengers",
                                       "year": "air_year"}))
    air["air_routes"] = 1                      # at least one airport pair exists
    save(air, "eurostat_air_pairs")

    print(f"    {len(air)} corridors with direct air service "
          f"(OpenFlights found 127)")
    print(f"    total passengers: {air.air_passengers.sum():,.0f}")
    if misses:
        print(f"    no data for: {', '.join(misses)}")
    nm = dict(zip(cc.iso3, cc.name))
    print("    busiest corridors:")
    for r in air.nlargest(8, "air_passengers").itertuples():
        print(f"      {nm.get(r.orig, r.orig) + ' -> ' + nm.get(r.dest, r.dest):<34}"
              f"{r.air_passengers:>11,.0f}")


if __name__ == "__main__":
    main()
