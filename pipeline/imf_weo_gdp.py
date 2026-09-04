"""IMF WEO PPP GDP per capita - fallback for the two WDI gaps.

The blended income measure averages the PIP median with World Bank PPP GDP per
capita, but WDI has no PPP GDP series for Eritrea or South Sudan, so their
blend was falling back to the weakest input in the table - the PIP median
alone. The IMF's World Economic Outlook covers both through the DataMapper API.

Two caveats, carried into the source register rather than hidden. Eritrea's
series ends in 2019, when the country stopped engaging with the Fund, so its
figure is stale by construction. And WEO years past the last outturn are
PROJECTIONS - South Sudan's show a modelled rebound from $997 to $1,467 - so
the rule here is the latest year at or before last year, never a projection.

Unit note: PPPPC is current international dollars, where the WDI series used
for everyone else is constant 2021 international dollars. For one half of a
blended input the mismatch is acceptable; it is named, not ignored.
"""
from __future__ import annotations

import datetime
import json

import pandas as pd

from _common import countries, fetch, save

URL = "https://www.imf.org/external/datamapper/api/v1/PPPPC"


def main() -> None:
    # The IMF WAF is contrary: it 403s both the default urllib agent AND a
    # browser-impersonating one from this client, but accepts a plain curl
    # identity. Tested all three; only this passes.
    doc = json.loads(fetch(URL, suffix=".json", timeout=180, headers={
        "User-Agent": "curl/8.9.1", "Accept": "*/*",
    }).decode("utf-8"))
    series = doc["values"]["PPPPC"]

    # Two years back, not one. The DataMapper API does not say where outturns
    # end and projections begin, and last year is exactly where a fresh WEO
    # vintage mixes the two - South Sudan's 2025 value is a modelled rebound of
    # +47% over its 2024 outturn, which no measurement produced. Year - 2 is
    # solidly historical in any vintage.
    cutoff = datetime.date.today().year - 2
    cc = countries()
    rows = []
    for iso3 in cc.iso3:
        s = series.get(iso3)
        if not s:
            continue
        years = sorted(int(y) for y in s if int(y) <= cutoff)
        if not years:
            continue
        y = years[-1]
        rows.append({"iso3": iso3, "gdp_pc_ppp_imf": float(s[str(y)]),
                     "imf_year": y})
    df = pd.DataFrame(rows)
    save(df, "imf_weo_gdp")

    # The point of this table is the countries WDI cannot cover.
    try:
        wdi = pd.read_parquet("../data/raw/worldbank_wide.parquet")
        missing = set(cc.iso3) - set(wdi[wdi.gdp_pc_ppp.notna()].iso3)
    except Exception:  # noqa: BLE001
        missing = set()
    nm = dict(zip(cc.iso3, cc.name))
    print(f"    {len(df)} corridor countries in WEO (latest year <= {cutoff})")
    for iso3 in sorted(missing):
        r = df[df.iso3 == iso3]
        if len(r):
            r = r.iloc[0]
            print(f"    fills WDI gap: {nm.get(iso3, iso3):<14}"
                  f"${r.gdp_pc_ppp_imf:>8,.0f}  ({int(r.imf_year)})")
        else:
            print(f"    WDI gap NOT covered by WEO either: {nm.get(iso3, iso3)}")


if __name__ == "__main__":
    main()
