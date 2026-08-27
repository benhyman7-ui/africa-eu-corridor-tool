"""Hofstede six-dimension cultural distance.

This is the cultural measure the CGD/TMO concept note names ("Hofstede cultural
distance - six-dimension cultural-distance scores"). The six dimensions are
power distance, individualism, motivation towards achievement, uncertainty
avoidance, long-term orientation and indulgence.

Corridor distance uses the standard Kogut-Singh index: the mean squared
difference across the six dimensions, each scaled by that dimension's variance
across all countries so no single dimension dominates through its spread alone.

COVERAGE IS THE LIMITATION HERE, and it is worth being blunt about it. Hofstede
surveyed 14 African countries individually. For the rest, Hofstede publishes
regional clusters - Africa East, Africa West, Arab countries - which are used
where they plausibly cover the country and left MISSING where they do not
(Middle Africa and most of Southern Africa have no cluster). Imputed rows are
flagged so the tool can say which basis a corridor rests on.
"""
from __future__ import annotations

import io

import pandas as pd

from _common import countries, fetch, save, step

URL = ("https://geerthofstede.com/wp-content/uploads/2016/08/"
       "6-dimensions-for-website-2015-08-16.csv")

DIMS = ["pdi", "idv", "mas", "uai", "ltowvs", "ivr"]

# Hofstede's spellings that differ from ours.
NAME_ALIAS = {
    "czech rep": "CZE", "slovak rep": "SVK", "great britain": "GBR",
    "u.s.a.": "USA", "korea south": "KOR", "macedonia rep": "MKD",
    "kyrgyz rep": "KGZ", "dominican rep": "DOM", "russia": "RUS",
    "vietnam": "VNM", "iran": "IRN", "turkey": "TUR", "bosnia": "BIH",
    "tanzania": "TZA", "south africa": "ZAF", "egypt": "EGY",
    "morocco": "MAR", "algeria": "DZA", "nigeria": "NGA", "ghana": "GHA",
    "ethiopia": "ETH", "zambia": "ZMB", "zimbabwe": "ZWE", "uganda": "UGA",
    "rwanda": "RWA", "mali": "MLI", "burkina faso": "BFA", "malta": "MLT",
    "luxembourg": "LUX", "cyprus": "CYP", "ireland": "IRL",
}

# Hofstede's own regional clusters, applied only where they plausibly cover the
# country. Middle Africa and Southern Africa (bar South Africa itself) get
# nothing rather than a guess.
CLUSTER_FOR_SUBREGION = {
    "Eastern Africa": "Africa East",
    "Western Africa": "Africa West",
    "Northern Africa": "Arab countries",
}


def main() -> None:
    step("Hofstede six-dimension scores")
    raw = fetch(URL, suffix=".csv", timeout=120)
    d = pd.read_csv(io.BytesIO(raw), sep=";")
    d.columns = [c.strip().lower() for c in d.columns]
    for c in DIMS:
        d[c] = pd.to_numeric(d[c], errors="coerce")

    cc = countries()
    by_name = {}
    for r in cc.itertuples():
        by_name[r.name.lower()] = r.iso3
        if isinstance(r.iso_name, str):
            by_name[r.iso_name.lower()] = r.iso3
    by_name.update(NAME_ALIAS)

    d["iso3"] = d["country"].str.strip().str.lower().map(by_name)
    # Kept as Series, not namedtuples, so cluster rows and country rows are
    # indexed the same way further down.
    wanted = set(CLUSTER_FOR_SUBREGION.values())
    clusters = {str(c).strip(): d.loc[i] for i, c in d["country"].items()
                if str(c).strip() in wanted}

    rows = []
    for r in cc.itertuples():
        own = d[d.iso3 == r.iso3]
        # "South Africa white" is a separate sub-sample; the national row wins.
        own = own[~own.country.str.contains("white", case=False, na=False)]
        basis, src = None, None
        if len(own) and own[DIMS].notna().any(axis=1).iloc[0]:
            src, basis = own.iloc[0], "country"
        else:
            cname = CLUSTER_FOR_SUBREGION.get(r.subregion)
            if cname and cname in clusters:
                src, basis = clusters[cname], f"cluster: {cname}"
        if src is None:
            rows.append({"iso3": r.iso3, "hofstede_basis": "unavailable",
                         **{f"h_{c}": None for c in DIMS}})
            continue
        rows.append({"iso3": r.iso3, "hofstede_basis": basis,
                     **{f"h_{c}": (None if pd.isna(src[c]) else float(src[c]))
                        for c in DIMS}})

    out = pd.DataFrame(rows)
    save(out, "hofstede")

    cols = [f"h_{c}" for c in DIMS]
    o = out[out.iso3.isin(cc[cc.side == "origin"].iso3)]
    dd = out[out.iso3.isin(cc[cc.side == "destination"].iso3)]
    print(f"    dimensions per country (mean): origins "
          f"{o[cols].notna().sum(axis=1).mean():.1f}/6, destinations "
          f"{dd[cols].notna().sum(axis=1).mean():.1f}/6")
    print(f"    basis: {out.hofstede_basis.value_counts().to_dict()}")
    nm = dict(zip(cc.iso3, cc.name))
    none = out[out.hofstede_basis == "unavailable"].iso3.tolist()
    if none:
        print(f"    no Hofstede basis ({len(none)}): "
              f"{sorted(nm.get(x, x) for x in none)}")


if __name__ == "__main__":
    main()
