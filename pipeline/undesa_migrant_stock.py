"""UN DESA International Migrant Stock 2024 - bilateral diaspora.

Replaces Eurostat migr_pop1ctz as the diaspora source. Eurostat is a reporting
series, and seven destinations simply do not report third-country citizenships:
France published African detail in 2018 and never again, Denmark stopped after
2022, and Cyprus, Greece, Croatia, Malta and Poland have never published any.
Spain reports ten African citizenships out of fifty-four. Because the panel
zero-filled anything unreported, the tool was asserting that no Moroccans live
in France - which is not a small error, France being the largest African
diaspora destination in the EU, and diaspora being a regressor in the gravity
model that sets every underutilisation score.

UN DESA estimates the full origin-by-destination matrix rather than passing
through what states choose to publish, so all 1,458 corridors get a value.

Two things to know about the concept. UN DESA counts the foreign-BORN
population where a country has that data and falls back to foreign citizenship
where it does not; the data_type column carries which, and it is kept. For a
diaspora variable foreign-born is arguably the better construct anyway, since
naturalisation does not erase someone from it. And the series is five-yearly to
2020 then 2024, so growth is measured over that final four-year step.
"""
from __future__ import annotations

import pandas as pd

from _common import RAW, countries, fetch, fetch_json, save

ISO_URL = ("https://raw.githubusercontent.com/lukes/"
           "ISO-3166-Countries-with-Regional-Codes/master/all/all.json")

URL = ("https://www.un.org/development/desa/pd/sites/www.un.org.development.desa.pd/"
       "files/undesa_pd_2024_ims_stock_by_sex_destination_and_origin.xlsx")
XLSX = RAW / "xlsx" / "undesa_ims_2024.xlsx"

# Table 1, row 11 is the header. Columns are 1-based in openpyxl terms:
# 5 = destination M49, 6 = origin name, 7 = origin M49, then the both-sexes
# year block 1990/95/2000/05/10/15/20/2024 in columns 8-15.
HEADER_ROW = 11
COL = {"dest_m49": 5, "data_type": 4, "orig_m49": 7, "y2020": 14, "y2024": 15}


def main() -> None:
    if not XLSX.exists():
        XLSX.parent.mkdir(parents=True, exist_ok=True)
        # The UN CDN refuses the default urllib agent, hence a browser string.
        XLSX.write_bytes(fetch(URL, suffix=".xlsx", timeout=300, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"}))

    import openpyxl
    ws = openpyxl.load_workbook(str(XLSX), read_only=True)["Table 1"]

    head = [c for c in next(ws.iter_rows(min_row=HEADER_ROW, max_row=HEADER_ROW,
                                         values_only=True))]
    assert str(head[COL["y2024"] - 1]).strip() == "2024", \
        f"column {COL['y2024']} is {head[COL['y2024'] - 1]!r}, expected 2024"
    assert str(head[COL["y2020"] - 1]).strip() == "2020", \
        f"column {COL['y2020']} is {head[COL['y2020'] - 1]!r}, expected 2020"

    cc = countries()
    dests = set(cc[cc.side == "destination"].iso3)

    # Every origin in the world, not just the African ones. The gravity model is
    # estimated on the global panel, so the diaspora regressor has to exist for
    # all 178 reported citizenships or the swap would fix Africa and corrupt the
    # comparison group that Africa is being measured against.
    m49 = {int(r["country-code"]): r["alpha-3"] for r in fetch_json(ISO_URL)
           if str(r.get("country-code", "")).isdigit()}
    m49.update({int(m): i for m, i in zip(cc.m49, cc.iso3)
                if str(m).strip().isdigit()})
    origins = set(m49.values())

    rows = []
    for r in ws.iter_rows(min_row=HEADER_ROW + 1, values_only=True):
        try:
            dm, om = int(r[COL["dest_m49"] - 1]), int(r[COL["orig_m49"] - 1])
        except (TypeError, ValueError):
            continue                       # aggregate rows carry non-numeric codes
        o, d = m49.get(om), m49.get(dm)
        if o not in origins or d not in dests:
            continue
        v24, v20 = r[COL["y2024"] - 1], r[COL["y2020"] - 1]
        rows.append({"orig": o, "dest": d,
                     "data_type": r[COL["data_type"] - 1],
                     "stock_2024": pd.to_numeric(v24, errors="coerce"),
                     "stock_2020": pd.to_numeric(v20, errors="coerce")})

    df = pd.DataFrame(rows).dropna(subset=["stock_2024"])
    df = df.groupby(["orig", "dest"], as_index=False).agg(
        stock_2024=("stock_2024", "sum"), stock_2020=("stock_2020", "sum"),
        data_type=("data_type", "first"))

    # Four-year step, so the exponent is 1/4.
    ok = (df.stock_2020 > 0) & df.stock_2024.notna()
    df["diaspora_cagr"] = ((df.stock_2024 / df.stock_2020) ** 0.25 - 1).where(ok)

    save(df, "undesa_migrant_stock")
    afr = set(cc[cc.side == "origin"].iso3)
    a = df[df.orig.isin(afr)]
    print(f"    {len(df):,} origin-destination pairs into the EU-27")
    print(f"    of which Africa-EU: {len(a)} of a possible {len(afr) * len(dests)}")
    print(f"    total Africa->EU migrant stock: {a.stock_2024.sum():,.0f}")
    print(f"    growth measurable on {int(df.diaspora_cagr.notna().sum())} corridors")
    nm = dict(zip(cc.iso3, cc.name))
    print("    largest Africa-EU corridors:")
    for r in a.nlargest(8, "stock_2024").itertuples():
        print(f"      {nm.get(r.orig, r.orig) + ' -> ' + nm.get(r.dest, r.dest):<34}"
              f"{r.stock_2024:>10,.0f}")
    missing = (len(afr) * len(dests)) - len(a)
    if missing:
        print(f"    {missing} Africa-EU corridors absent from the UN matrix "
              "(negligible estimated stock, which is a real zero here)")


if __name__ == "__main__":
    main()
