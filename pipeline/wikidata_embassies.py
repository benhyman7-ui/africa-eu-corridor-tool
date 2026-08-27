"""Consular access, from Wikidata.

Most EU work-visa routes require an in-person biometric appointment. Where the
destination operates no mission in the origin country, an applicant must travel
to a third country before they can even apply. That is frequently the binding
constraint on a corridor and it appears in no standard migration dataset - which
is precisely why it is worth building.

We query diplomatic missions (Q3917681 and subclasses) by operator (P137) and
host country (P17), in both directions, and derive:
  dest_embassy_in_orig     destination has an embassy in the origin country
  dest_missions_in_orig    count of destination missions (embassy + consulates)
  orig_embassy_in_dest     origin has an embassy in the destination country
"""
from __future__ import annotations

import urllib.parse

import pandas as pd

from _common import countries, fetch, load, save, step

SPARQL = "https://query.wikidata.org/sparql"

# operator -> host country, with the ISO-3166 alpha-3 of each side.
#
# The P576 filter is essential, not cosmetic. Wikidata keeps CLOSED missions as
# items and only marks them with a dissolution date. Without this filter Hungary
# appeared to hold 16 African embassies including Guinea, Chad and Zimbabwe -
# Cold War posts long since shut. That inflated consular access directly, and
# once the neighbouring-country tier was added it propagated to neighbours too.
QUERY = """
SELECT ?opIso ?hostIso ?kindLabel WHERE {
  ?m wdt:P31 ?kind .
  ?kind wdt:P279* wd:Q3917681 .
  ?m wdt:P137 ?op .
  ?m wdt:P17 ?host .
  ?op wdt:P298 ?opIso .
  ?host wdt:P298 ?hostIso .
  FILTER NOT EXISTS { ?m wdt:P576 ?dissolved }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""

# Consulates are not consistently modelled under the diplomatic-mission subclass
# tree, so they need their own root. This is a supplement, not a dependency: WDQS
# rate-limits hard during outages, and the embassy measure above is the one that
# actually drives the friction index.
CONSULATE_QUERY = """
SELECT ?opIso ?hostIso ?kindLabel WHERE {
  ?m wdt:P31 ?kind .
  ?kind wdt:P279* wd:Q207694 .
  ?m wdt:P137 ?op .
  ?m wdt:P17 ?host .
  ?op wdt:P298 ?opIso .
  ?host wdt:P298 ?hostIso .
  FILTER NOT EXISTS { ?m wdt:P576 ?dissolved }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""


def _run(query: str) -> pd.DataFrame:
    url = SPARQL + "?" + urllib.parse.urlencode({"query": query, "format": "json"})
    # WDQS throttles to 1 request/minute during outages, so this one call
    # gets a much longer retry budget than the default.
    doc = fetch(url, suffix=".json", headers={"Accept": "application/sparql-results+json"},
                timeout=180, tries=15)
    import json
    rows = json.loads(doc)["results"]["bindings"]
    return pd.DataFrame([{k: v["value"] for k, v in r.items()} for r in rows])


# CEPII GeoDist is 2011 vintage and has no South Sudan row at all, so its land
# borders have to be stated. These are not in dispute.
BORDER_FILL = {"SSD": ["SDN", "ETH", "KEN", "UGA", "COD", "CAF"]}

# Island states have no land border, so contiguity alone would score them as
# having no regional fallback whatsoever. In practice their applicants fly to a
# regional hub anyway, which is what the distance tier below is for.
ISLANDS = {"CPV", "COM", "MUS", "STP", "SYC", "MDG"}


def _haversine(lat1, lon1, lat2, lon2) -> float:
    import math
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return float("nan")
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def _regional_access(cc: pd.DataFrame, origins: list[str]):
    """Land-border graph and origin-to-origin distances, both Africa-internal.

    Used to give partial credit where the destination keeps no mission in the
    origin country but does keep one next door.
    """
    neighbours: dict[str, set[str]] = {o: set() for o in origins}
    try:
        geo = load("cepii_geodist")
        sub = geo[geo.orig.isin(origins) & geo.dest.isin(origins)][["orig", "dest", "contig", "dist", "distcap"]]
        for r in sub[sub.contig == 1].itertuples():
            neighbours[r.orig].add(r.dest)
        # distcap, not dist: missions sit in capitals and the question is how far
        # the applicant must travel to one. CEPII's `dist` is population-weighted,
        # which made Eswatini look closer to Lesotho than South Africa is.
        km = {(r.orig, r.dest): (r.distcap if pd.notna(r.distcap) else r.dist)
              for r in sub.itertuples()
              if pd.notna(r.distcap) or pd.notna(r.dist)}
    except Exception as exc:  # noqa: BLE001 - fall back to coordinates alone
        print(f"      GeoDist unavailable ({exc}); distances from capitals only")
        km = {}

    lat = dict(zip(cc.iso3, cc.lat))
    lon = dict(zip(cc.iso3, cc.lon))
    for a in origins:
        for b in origins:
            if a != b and (a, b) not in km:
                d = _haversine(lat.get(a), lon.get(a), lat.get(b), lon.get(b))
                if pd.notna(d):
                    km[(a, b)] = d

    for o, ns in BORDER_FILL.items():
        if o in neighbours:
            neighbours[o].update(n for n in ns if n in origins)
            for n in ns:
                if n in neighbours:
                    neighbours[n].add(o)

    filled = sum(1 for o in origins if not neighbours[o])
    print(f"      land-border graph: {sum(len(v) for v in neighbours.values()) // 2} "
          f"borders; {filled} landless origins "
          f"({sorted(o for o in origins if not neighbours[o])})")
    return neighbours, km


def main() -> None:
    step("Wikidata SPARQL - diplomatic missions worldwide")
    df = _run(QUERY)
    if df.empty:
        raise RuntimeError("Wikidata returned no missions")

    try:
        con = _run(CONSULATE_QUERY)
        if len(con):
            df = pd.concat([df, con], ignore_index=True).drop_duplicates()
            print(f"      + {len(con):,} consulate records")
    except Exception as exc:  # noqa: BLE001 - WDQS rate limits during outages
        print(f"      consulate supplement unavailable ({type(exc).__name__}); "
              f"embassies only")

    df = df.rename(columns={"opIso": "operator", "hostIso": "host",
                            "kindLabel": "kind"})
    df["kind"] = df["kind"].fillna("").str.lower()
    df["is_embassy"] = df["kind"].str.contains("embassy|high commission|nunciature",
                                               regex=True).astype(int)
    df = df[df.operator != df.host]
    save(df, "wikidata_missions")

    cc = countries()
    origins = cc[cc.side == "origin"].iso3.tolist()
    dests = cc[cc.side == "destination"].iso3.tolist()

    miss = df.groupby(["operator", "host"]).agg(
        n=("is_embassy", "size"), n_embassy=("is_embassy", "sum")).reset_index()
    lut = {(r.operator, r.host): r for r in miss.itertuples()}
    # host countries where each destination keeps an embassy
    emb_hosts: dict[str, set[str]] = {}
    for r in miss.itertuples():
        if r.n_embassy > 0:
            emb_hosts.setdefault(r.operator, set()).add(r.host)

    neighbours, km = _regional_access(cc, origins)

    rows = []
    for o in origins:
        for d in dests:
            fwd = lut.get((d, o))   # destination's mission inside the origin country
            rev = lut.get((o, d))   # origin's mission inside the destination country
            hosts = emb_hosts.get(d, set())

            nb = sorted(n for n in neighbours.get(o, set()) if n in hosts)
            # nearest AFRICAN country holding this destination's embassy. Restricted
            # to Africa deliberately: an applicant who would have to reach Europe to
            # apply for a European visa is not a realistic fallback, it is circular.
            reach = [(km[(o, c)], c) for c in origins
                     if c != o and c in hosts and (o, c) in km]
            near_km, near_c = min(reach) if reach else (None, None)

            rows.append({
                "orig": o, "dest": d,
                "dest_missions_in_orig": int(fwd.n) if fwd is not None else 0,
                "dest_embassy_in_orig": int(bool(fwd is not None and fwd.n_embassy > 0)),
                "orig_missions_in_dest": int(rev.n) if rev is not None else 0,
                "orig_embassy_in_dest": int(bool(rev is not None and rev.n_embassy > 0)),
                "neighbour_embassy": int(bool(nb)),
                "n_neighbour_embassies": len(nb),
                "neighbour_embassy_countries": ";".join(nb),
                "nearest_embassy_km": None if near_km is None else round(float(near_km)),
                "nearest_embassy_country": near_c or "",
            })
    out = pd.DataFrame(rows)
    save(out, "consular_presence")

    n = len(out)
    print(f"    {len(df):,} missions worldwide -> {n:,} corridors scored")
    print(f"    destination has ANY mission in origin: "
          f"{(out.dest_missions_in_orig > 0).sum():,} / {n:,} "
          f"({(out.dest_missions_in_orig > 0).mean():.0%})")
    print(f"    destination has an EMBASSY in origin:  "
          f"{out.dest_embassy_in_orig.sum():,} / {n:,} "
          f"({out.dest_embassy_in_orig.mean():.0%})")
    worst = (out.groupby("orig").dest_embassy_in_orig.sum()
                .sort_values().head(8))
    print("    origins with fewest EU embassies present:")
    nm = dict(zip(cc.iso3, cc.name))
    for k, v in worst.items():
        print(f"      {nm.get(k, k):<24} {int(v):>2} / 27")


if __name__ == "__main__":
    main()
