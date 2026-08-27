"""World Bank WDI + Worldwide Governance Indicators.

Push factors at origin and pull factors at destination. We take the most recent
non-null observation per country within the window rather than a fixed year,
because African coverage is patchy and a fixed year would silently drop states.
"""
from __future__ import annotations

import pandas as pd

from _common import countries, fetch_json, save, step

ISO_URL = ("https://raw.githubusercontent.com/lukes/"
           "ISO-3166-Countries-with-Regional-Codes/master/all/all.json")
WB_COUNTRIES = "https://api.worldbank.org/v2/country?format=json&per_page=400"
WINDOW = "2014:2024"

WDI = {
    # income and the wage-gap numerator/denominator
    "NY.GNP.PCAP.PP.CD": "gni_pc_ppp",
    "NY.GDP.PCAP.PP.KD": "gdp_pc_ppp",
    "NY.GDP.PCAP.CD": "gdp_pc_usd",
    # labour market
    "SL.UEM.TOTL.ZS": "unemployment",
    "SL.UEM.1524.ZS": "youth_unemployment",
    "SL.TLF.CACT.ZS": "labour_force_part",
    # human capital
    "SE.TER.ENRR": "tertiary_enrolment",
    "SE.SEC.CUAT.UP.ZS": "upper_secondary_attain",
    "SE.ADT.LITR.ZS": "literacy",
    "SE.SEC.ENRL.VO.ZS": "vocational_share_secondary",
    "SE.TER.CUAT.BA.ZS": "bachelor_attainment",
    # demography and pressure
    "SP.POP.TOTL": "population",
    "SP.POP.GROW": "population_growth",
    "SP.POP.1564.TO": "working_age_pop",
    "SP.DYN.TFRT.IN": "fertility",
    "SP.URB.TOTL.IN.ZS": "urban_share",
    # connectivity and migration infrastructure
    "IT.NET.USER.ZS": "internet_users",
    "BX.TRF.PWKR.DT.GD.ZS": "remittances_pct_gdp",
    "SI.POV.DDAY": "poverty_215",
    # Conflict at origin. The concept note names UCDP/PRIO, whose own API now
    # requires a token; the Bank republishes the same UCDP battle-deaths series.
    "VC.BTL.DETH": "battle_deaths",
    "VC.IHR.PSRC.P5": "homicide_rate",
}

# WGI now lives in source 3 under GOV_WGI_* codes. Two quirks: the legacy
# CC.EST-style codes 404, and a `country/all` query on source 3 returns rows with
# an EMPTY countryiso3code - so the country list has to be named explicitly.
WGI = {
    "GOV_WGI_CC.EST": "wgi_corruption",
    "GOV_WGI_GE.EST": "wgi_govt_effectiveness",
    "GOV_WGI_PV.EST": "wgi_political_stability",
    "GOV_WGI_RL.EST": "wgi_rule_of_law",
    "GOV_WGI_RQ.EST": "wgi_regulatory_quality",
    "GOV_WGI_VA.EST": "wgi_voice_accountability",
}


def _series(code: str, name: str, *, source: int | None = None,
            iso3s: list[str] | None = None) -> pd.DataFrame:
    who = ";".join(iso3s) if iso3s else "all"
    url = (f"https://api.worldbank.org/v2/country/{who}/indicator/{code}"
           f"?format=json&per_page=20000&date={WINDOW}")
    if source:
        url += f"&source={source}"
    doc = fetch_json(url)
    if not isinstance(doc, list) or len(doc) < 2 or doc[1] is None:
        print(f"      {code}: no data returned")
        return pd.DataFrame(columns=["iso3", "year", "indicator", "value"])
    rows = [{"iso3": r["countryiso3code"], "year": int(r["date"]),
             "indicator": name, "value": r["value"]}
            for r in doc[1] if r.get("value") is not None and r.get("countryiso3code")]
    return pd.DataFrame(rows)


def main() -> None:
    cc = countries()
    keep = set(cc.iso3)

    frames = []
    step(f"World Bank WDI - {len(WDI)} indicators, {WINDOW}")
    for i, (code, name) in enumerate(WDI.items(), 1):
        frames.append(_series(code, name))
        print(f"      [{i:>2}/{len(WDI)}] {name}", end="\r", flush=True)
    print(" " * 50, end="\r")

    # WGI must cover every origin in the gravity panel, not just our 81 corridor
    # countries - the absolute spec uses governance as an origin fundamental, and
    # restricting the request here silently starved it to 32% coverage.
    # Only codes the Bank itself recognises: one unknown ISO3 (Aland, Antarctica)
    # makes the whole batch request fail, taking 60 good countries with it.
    known = {r["id"] for r in fetch_json(WB_COUNTRIES)[1]
             if r["region"]["value"] != "Aggregates"}
    iso3s = sorted(({r["alpha-3"] for r in fetch_json(ISO_URL)} | keep) & known)
    chunks = [iso3s[i:i + 60] for i in range(0, len(iso3s), 60)]
    step(f"World Bank WGI - {len(WGI)} governance indicators, "
         f"{len(iso3s)} countries in {len(chunks)} batches")
    for i, (code, name) in enumerate(WGI.items(), 1):
        for ch in chunks:
            frames.append(_series(code, name, source=3, iso3s=ch))
        print(f"      [{i}/{len(WGI)}] {name}", end="\r", flush=True)
    print(" " * 50, end="\r")

    everything = pd.concat(frames, ignore_index=True)
    long = everything[everything.iso3.isin(keep)]
    save(long, "worldbank_long")

    def widen(src: pd.DataFrame) -> pd.DataFrame:
        # latest non-null per country x indicator
        latest = (src.sort_values("year")
                     .groupby(["iso3", "indicator"], as_index=False)
                     .last())
        w = latest.pivot(index="iso3", columns="indicator", values="value")
        yr = latest.pivot(index="iso3", columns="indicator", values="year")
        yr.columns = [f"{c}__year" for c in yr.columns]
        return w.join(yr).reset_index()

    wide = widen(long)
    save(wide, "worldbank_wide")
    # The gravity model is estimated on ~166 origins, so the origin-covariate
    # spec needs indicators for every reported citizenship, not just our 81.
    save(widen(everything), "worldbank_wide_global")

    allcols = list(WDI.values()) + list(WGI.values())
    o = wide[wide.iso3.isin(cc[cc.side == "origin"].iso3)]
    cov = {c: f"{o[c].notna().mean():.0%}" for c in allcols if c in o}
    thin = {k: v for k, v in cov.items() if int(v.rstrip('%')) < 80}
    print(f"    {len(wide)} countries x {len(allcols)} indicators")
    print(f"    origin coverage below 80%: {thin or 'none'}")


if __name__ == "__main__":
    main()
