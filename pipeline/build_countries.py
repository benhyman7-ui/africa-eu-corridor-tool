"""Build ref/countries.csv - the code spine everything else joins on.

Eurostat uses ISO2 with quirks (EL for Greece, XK for Kosovo); the World Bank uses
ISO3; UN DESA uses M49; CEPII uses ISO3. Getting this table right first is the
difference between a working panel and a silently half-empty one.
"""
from __future__ import annotations

import pandas as pd

from _common import REF, eurostat_labels, fetch_json, step

ISO_URL = ("https://raw.githubusercontent.com/lukes/"
           "ISO-3166-Countries-with-Regional-Codes/master/all/all.json")
WB_URL = "https://api.worldbank.org/v2/country?format=json&per_page=400"

EU27 = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "CY": "Cyprus",
    "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EE": "Estonia",
    "EL": "Greece", "ES": "Spain", "FI": "Finland", "FR": "France",
    "HR": "Croatia", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "MT": "Malta",
    "NL": "Netherlands", "PL": "Poland", "PT": "Portugal", "RO": "Romania",
    "SE": "Sweden", "SI": "Slovenia", "SK": "Slovakia",
}
# Eurostat geo code -> real ISO2 (Greece is EL in Eurostat, GR in ISO 3166)
EUROSTAT_TO_ISO2 = {"EL": "GR"}

EU_REGION = {
    "Western Europe": ["AT", "BE", "FR", "DE", "LU", "NL", "IE"],
    "Southern Europe": ["HR", "CY", "EL", "IT", "MT", "PT", "SI", "ES"],
    "Northern Europe": ["DK", "EE", "FI", "LV", "LT", "SE"],
    "Eastern Europe": ["BG", "CZ", "HU", "PL", "RO", "SK"],
}

# The 54 UN member states in Africa (AU minus Western Sahara, which has no
# Eurostat citizenship code and therefore no observable flow).
AFRICA54 = [
    "DZA", "AGO", "BEN", "BWA", "BFA", "BDI", "CPV", "CMR", "CAF", "TCD",
    "COM", "COG", "COD", "DJI", "EGY", "GNQ", "ERI", "SWZ", "ETH", "GAB",
    "GMB", "GHA", "GIN", "GNB", "CIV", "KEN", "LSO", "LBR", "LBY", "MDG",
    "MWI", "MLI", "MRT", "MUS", "MAR", "MOZ", "NAM", "NER", "NGA", "RWA",
    "STP", "SEN", "SYC", "SLE", "SOM", "ZAF", "SSD", "SDN", "TZA", "TGO",
    "TUN", "UGA", "ZMB", "ZWE",
]

# Short, display-friendly names where the ISO long form is unwieldy.
SHORT_NAME = {
    "COD": "DR Congo", "COG": "Congo-Brazzaville", "CIV": "Côte d'Ivoire",
    "TZA": "Tanzania", "CAF": "Central African Rep.", "STP": "São Tomé & Príncipe",
    "GNQ": "Equatorial Guinea", "CPV": "Cabo Verde", "SWZ": "Eswatini",
    "LBY": "Libya", "SSD": "South Sudan", "GMB": "The Gambia",
    "MRT": "Mauritania", "BFA": "Burkina Faso", "ZAF": "South Africa",
}


def main() -> None:
    step("fetching ISO 3166 / UN M49 reference")
    iso = {r["alpha-3"]: r for r in fetch_json(ISO_URL)}

    step("fetching World Bank country metadata")
    wb = {r["id"]: r for r in fetch_json(WB_URL)[1] if r["region"]["value"] != "Aggregates"}

    step("fetching Eurostat citizenship dimension (which origins are reported at all)")
    cit = eurostat_labels("migr_resocc", "citizen", geo="DE", time=2023)

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    rows = []
    for iso3 in AFRICA54:
        i, w = iso.get(iso3, {}), wb.get(iso3, {})
        iso2 = i.get("alpha-2", "")
        # UN geoscheme: Northern Africa sits at sub-region level, the rest at
        # intermediate-region level (Eastern / Middle / Western / Southern).
        sub = i.get("intermediate-region") or i.get("sub-region") or ""
        rows.append({
            "iso3": iso3,
            "iso2": iso2,
            "m49": i.get("country-code", ""),
            "name": SHORT_NAME.get(iso3) or w.get("name") or i.get("name", iso3),
            "iso_name": i.get("name", ""),
            "side": "origin",
            "region": "Africa",
            "subregion": sub,
            "eurostat_code": iso2,
            "in_eurostat_citizen": int(iso2 in cit),
            "wb_income": (w.get("incomeLevel") or {}).get("value", ""),
            "capital": w.get("capitalCity", ""),
            "lat": num(w.get("latitude")),
            "lon": num(w.get("longitude")),
        })

    for est, nm in EU27.items():
        iso2 = EUROSTAT_TO_ISO2.get(est, est)
        iso3 = next((k for k, v in iso.items() if v.get("alpha-2") == iso2), "")
        i, w = iso.get(iso3, {}), wb.get(iso3, {})
        rows.append({
            "iso3": iso3,
            "iso2": iso2,
            "m49": i.get("country-code", ""),
            "name": nm,
            "iso_name": i.get("name", ""),
            "side": "destination",
            "region": "EU-27",
            "subregion": next((k for k, v in EU_REGION.items() if est in v), ""),
            "eurostat_code": est,
            "in_eurostat_citizen": int(iso2 in cit),
            "wb_income": (w.get("incomeLevel") or {}).get("value", ""),
            "capital": w.get("capitalCity", ""),
            "lat": num(w.get("latitude")),
            "lon": num(w.get("longitude")),
        })

    df = pd.DataFrame(rows)
    bad = df[(df.iso3 == "") | (df.iso2 == "") | df.lat.isna()]
    if len(bad):
        print(f"    WARNING incomplete rows:\n{bad[['iso3', 'name']]}")
    df.to_csv(REF / "countries.csv", index=False, encoding="utf-8")

    o = df[df.side == "origin"]
    d = df[df.side == "destination"]
    print(f"    origins {len(o)}  destinations {len(d)}  corridors {len(o) * len(d):,}")
    print(f"    subregions: {sorted(o.subregion.unique())}")
    miss = o[o.in_eurostat_citizen == 0]["name"].tolist()
    print(f"    origins missing from Eurostat citizen dim: {miss or 'none - all 54 present'}")


if __name__ == "__main__":
    main()
