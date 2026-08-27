"""Country outlines for the map.

Natural Earth-derived GeoJSON, filtered to the 81 corridor countries and
aggressively simplified: coordinates rounded to two decimals (about 1 km, far
finer than a 1,000 px map needs) and tiny outlying rings dropped. That takes the
world file from 257 KB to something the page can hold alongside everything else.
"""
from __future__ import annotations

import json

from _common import WEBDATA, countries, fetch, step

URL = ("https://raw.githubusercontent.com/johan/world.geo.json/"
       "master/countries.geo.json")

# world.geo.json keys a few countries differently from ISO 3166.
ID_ALIAS = {"SDS": "SSD", "ZAR": "COD", "ROM": "ROU"}

DP = 2          # coordinate decimals (~1.1 km, finer than a 1000px map resolves)
MIN_RING = 4    # a closed ring needs 4 points; 12 silently dropped Malta and Luxembourg


def _round_ring(ring):
    out, last = [], None
    for x, y in ring:
        p = [round(float(x), DP), round(float(y), DP)]
        if p != last:            # rounding collapses runs of near-identical points
            out.append(p)
            last = p
    if len(out) >= 4 and out[0] != out[-1]:
        out.append(out[0])       # keep rings closed
    return out


def _simplify(geom):
    t = geom["type"]
    if t == "Polygon":
        rings = [_round_ring(r) for r in geom["coordinates"]]
        rings = [r for r in rings if len(r) >= MIN_RING]
        return {"type": "Polygon", "coordinates": rings} if rings else None
    if t == "MultiPolygon":
        polys = []
        for poly in geom["coordinates"]:
            rings = [_round_ring(r) for r in poly]
            rings = [r for r in rings if len(r) >= MIN_RING]
            if rings:
                polys.append(rings)
        return {"type": "MultiPolygon", "coordinates": polys} if polys else None
    return None


def main() -> None:
    step("country outlines")
    world = json.loads(fetch(URL, suffix=".json", timeout=180).decode("utf-8"))
    cc = countries()
    want = {r.iso3: r for r in cc.itertuples()}

    feats, seen = [], set()
    for f in world["features"]:
        iso = ID_ALIAS.get(f.get("id"), f.get("id"))
        if iso not in want or iso in seen:
            continue
        geom = _simplify(f["geometry"])
        if geom is None:
            continue
        seen.add(iso)
        r = want[iso]
        feats.append({"type": "Feature", "id": iso,
                      "properties": {"name": r.name, "side": r.side,
                                     "subregion": r.subregion},
                      "geometry": geom})

    out = {"type": "FeatureCollection", "features": feats}
    path = WEBDATA / "geo.json"
    path.write_text(json.dumps(out, separators=(",", ":")), encoding="utf-8")

    nm = dict(zip(cc.iso3, cc.name))
    missing = sorted(set(want) - seen)
    kb = path.stat().st_size / 1024
    print(f"    {len(feats)} / {len(want)} countries, {kb:,.0f} KB")
    if missing:
        print(f"    no outline available: {[nm.get(m, m) for m in missing]}")


if __name__ == "__main__":
    main()
