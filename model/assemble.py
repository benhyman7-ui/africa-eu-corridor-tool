"""Assemble the corridor panel.

Two products:
  panel_global   every reported citizenship x EU-27 destination x year. This is
                 the ESTIMATION sample for the gravity model - fitting on Africa
                 alone would fold the very anomaly we are hunting into the
                 coefficients.
  corridors      the 1,458 Africa-EU corridors with every covariate attached.
                 This is the PREDICTION sample and the spine of the tool.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _common import INTERIM, REF, countries, fetch_json, load, save, step  # noqa: E402

ISO_URL = ("https://raw.githubusercontent.com/lukes/"
           "ISO-3166-Countries-with-Regional-Codes/master/all/all.json")

# Eurostat citizenship codes that are not ISO 3166 alpha-2.
ES_QUIRK = {"EL": "GRC", "UK": "GBR", "XK": "XKX"}

RELIGION_COLS = ["rel_christian", "rel_muslim", "rel_folk", "rel_hindu",
                 "rel_buddhist", "rel_jewish", "rel_other", "rel_none"]


def iso2_to_iso3() -> dict:
    m = {r["alpha-2"]: r["alpha-3"] for r in fetch_json(ISO_URL)}
    m.update(ES_QUIRK)
    return m


def haversine(lat1, lon1, lat2, lon2) -> float:
    """Great-circle km. Fills the corridors CEPII GeoDist predates (South Sudan)."""
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return float("nan")
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    return float(a @ b / (na * nb)) if na > 0 and nb > 0 else float("nan")


def _opt(name: str) -> pd.DataFrame | None:
    """Load a raw table if the fetcher produced it, else None."""
    try:
        return load(name)
    except Exception:  # noqa: BLE001
        print(f"      (optional table missing: {name})")
        return None


def main() -> None:
    cc = countries()
    to3 = iso2_to_iso3()
    o3 = cc[cc.side == "origin"].set_index("iso3")
    d3 = cc[cc.side == "destination"].set_index("iso3")
    es2iso3_dest = dict(zip(cc[cc.side == "destination"].eurostat_code,
                            cc[cc.side == "destination"].iso3))

    # ---------------------------------------------------------------- flows --
    step("flows")
    perm = load("eurostat_permits_employment")
    perm["orig"] = perm.orig_es.map(lambda c: to3.get(c))
    perm["dest"] = perm.dest_es.map(es2iso3_dest)
    perm = perm.dropna(subset=["orig", "dest"])

    emp = perm[perm.permit_category == "EMP"].copy()
    flows = (emp.groupby(["orig", "dest", "year"], as_index=False)
                .value.sum().rename(columns={"value": "permits"}))

    # ------------------------------------------------------------- diaspora --
    dia = _opt("eurostat_diaspora")
    if dia is not None:
        dia["orig"] = dia.orig_es.map(lambda c: to3.get(c))
        dia["dest"] = dia.dest_es.map(es2iso3_dest)
        dia = (dia.dropna(subset=["orig", "dest"])
                  .groupby(["orig", "dest", "year"], as_index=False)
                  .value.sum().rename(columns={"value": "diaspora"}))
    else:
        dia = pd.DataFrame(columns=["orig", "dest", "year", "diaspora"])

    # --------------------------------------------------------- dyadic terms --
    step("dyadic covariates")
    geo = load("cepii_geodist")[["orig", "dest", "dist", "contig", "comlang_off",
                                 "comlang_ethno", "colony", "comcol"]]
    routes = load("openflights_pairs")[["orig", "dest", "routes", "airlines"]]

    # ------------------------------------------------------- global panel ----
    step("global estimation panel")
    g = flows.merge(geo, on=["orig", "dest"], how="left")
    g = g.merge(routes, on=["orig", "dest"], how="left")
    g = g.merge(dia, on=["orig", "dest", "year"], how="left")
    # Bilateral forced displacement: the concept note lists UNHCR stocks as a
    # network/push covariate, and a corridor already carrying refugees has
    # revealed demand the labour channel is not serving.
    _ref = None
    try:
        _ref = load("unhcr_bilateral")[["orig", "dest", "forced_total"]]
    except Exception:  # noqa: BLE001
        pass
    if _ref is not None:
        g = g.merge(_ref, on=["orig", "dest"], how="left")
    if "forced_total" not in g.columns:
        g["forced_total"] = 0.0
    g["forced_total"] = g["forced_total"].fillna(0.0)
    for c in ("routes", "airlines", "diaspora"):
        g[c] = g[c].fillna(0.0)
    g["comlang_off"] = g["comlang_off"].fillna(0)
    g["comlang_ethno"] = g["comlang_ethno"].fillna(0)
    g["colony"] = g["colony"].fillna(0)
    g["comcol"] = g["comcol"].fillna(0)
    g["contig"] = g["contig"].fillna(0)
    g = g[g.dist.notna()]

    # Origin covariates for the "absolute" gravity spec. That spec replaces the
    # origin fixed effect with fundamentals, which is the only way to detect an
    # origin that under-sends to the EU as a whole rather than just to one
    # destination - and those are precisely the corridors the brief is about.
    wbg = load("worldbank_wide_global")
    ocols = ["gni_pc_ppp", "population", "youth_unemployment", "tertiary_enrolment",
             "internet_users", "urban_share", "wgi_govt_effectiveness",
             "wgi_political_stability", "battle_deaths"]
    have = [c for c in ocols if c in wbg.columns]
    g = g.merge(wbg[["iso3"] + have].add_prefix("ov_").rename(columns={"ov_iso3": "orig"}),
                on="orig", how="left")
    save(g, "panel_global", INTERIM)
    print(f"    estimation sample: {len(g):,} corridor-years, "
          f"{g.orig.nunique()} origins, {g.dest.nunique()} destinations")
    print(f"    zero-flow share: {(g.permits == 0).mean():.0%} "
          f"(PPML handles these natively; log-OLS could not)")

    # ------------------------------------------------ Africa-EU corridors ----
    step("Africa-EU corridor table")
    origins = list(o3.index)
    dests = list(d3.index)
    idx = pd.MultiIndex.from_product([origins, dests], names=["orig", "dest"])
    C = pd.DataFrame(index=idx).reset_index()

    # flows: latest year, and a 3-year mean to damp single-year noise
    ymax = int(flows.year.max())
    recent = flows[flows.year >= ymax - 2]
    C = C.merge(flows[flows.year == ymax][["orig", "dest", "permits"]]
                .rename(columns={"permits": "permits_latest"}),
                on=["orig", "dest"], how="left")
    C = C.merge(recent.groupby(["orig", "dest"], as_index=False)
                .permits.mean().rename(columns={"permits": "permits_mean3"}),
                on=["orig", "dest"], how="left")
    C = C.merge(flows.groupby(["orig", "dest"], as_index=False)
                .permits.sum().rename(columns={"permits": "permits_total"}),
                on=["orig", "dest"], how="left")
    for c in ("permits_latest", "permits_mean3", "permits_total"):
        C[c] = C[c].fillna(0.0)
    C["latest_year"] = ymax

    # permit mix
    for cat, label in [("EMP_BLUE", "blue_card"), ("EMP_HSW", "high_skilled"),
                       ("EMP_RSE", "researcher"), ("EMP_WRK_SEAS", "seasonal"),
                       ("EMP_OTH", "other_emp")]:
        sub = (perm[(perm.permit_category == cat) & (perm.year >= ymax - 2)]
               .groupby(["orig", "dest"], as_index=False).value.mean()
               .rename(columns={"value": f"permits_{label}"}))
        C = C.merge(sub, on=["orig", "dest"], how="left")
        C[f"permits_{label}"] = C[f"permits_{label}"].fillna(0.0)

    # dyadic
    C = C.merge(geo, on=["orig", "dest"], how="left")
    C = C.merge(routes, on=["orig", "dest"], how="left")
    C = C.merge(dia[dia.year == dia.year.max()][["orig", "dest", "diaspora"]]
                if len(dia) else dia, on=["orig", "dest"], how="left")
    C = C.merge(load("consular_presence"), on=["orig", "dest"], how="left")

    # distance fallback for pairs CEPII predates
    need = C.dist.isna()
    if need.any():
        C.loc[need, "dist"] = [
            haversine(o3.lat.get(r.orig), o3.lon.get(r.orig),
                      d3.lat.get(r.dest), d3.lon.get(r.dest))
            for r in C[need].itertuples()]
        print(f"    filled {int(need.sum())} distances by haversine "
              f"({sorted(C[need].orig.unique())})")

    for c in ("routes", "airlines", "diaspora", "comlang_off", "comlang_ethno",
              "colony", "comcol", "contig"):
        C[c] = C[c].fillna(0.0)

    # ---------------------------------------------------- proximity factors --
    step("cultural, religious and language proximity")
    fb = load("factbook_profile").set_index("iso3")
    relv = {i: fb.loc[i, RELIGION_COLS].to_numpy(dtype=float)
            for i in fb.index if i in set(origins) | set(dests)}

    def langs(i, col="lang_official"):
        v = fb.loc[i, col] if i in fb.index else ""
        return set(str(v).split(";")) - {""}

    rows = []
    for r in C.itertuples():
        ro, rd = relv.get(r.orig), relv.get(r.dest)
        rel_sim = cosine(ro, rd) if ro is not None and rd is not None else np.nan
        oc = float(fb.loc[r.orig, "rel_catholic"]) if r.orig in fb.index else np.nan
        dc = float(fb.loc[r.dest, "rel_catholic"]) if r.dest in fb.index else np.nan
        cath_sim = 1 - abs(oc - dc) if not (pd.isna(oc) or pd.isna(dc)) else np.nan
        maj_o = RELIGION_COLS[int(np.argmax(ro))] if ro is not None else ""
        maj_d = RELIGION_COLS[int(np.argmax(rd))] if rd is not None else ""

        o_off, d_off = langs(r.orig), langs(r.dest)
        o_all = langs(r.orig, "lang_all")
        shared = len(o_off & d_off) > 0
        # the destination working language present in the origin at all
        usable = len(o_all & d_off) > 0
        o_bridge = langs(r.orig, "lang_bridge")
        rows.append({
            "religion_similarity": rel_sim,
            "religion_catholic_similarity": cath_sim,
            "same_majority_religion": int(maj_o == maj_d and maj_o != ""),
            "majority_religion_orig": maj_o.replace("rel_", ""),
            "majority_religion_dest": maj_d.replace("rel_", ""),
            "shared_official_language": int(shared),
            "dest_language_present_at_origin": int(usable),
            "origin_bridge_languages": ";".join(sorted(o_bridge)),
            "dest_official_languages": ";".join(sorted(d_off)),
        })
    C = pd.concat([C, pd.DataFrame(rows, index=C.index)], axis=1)

    # ------------------------------------------------------- country traits --
    step("country covariates")
    wb = load("worldbank_wide")
    keep = [c for c in wb.columns if not c.endswith("__year") and c != "iso3"]
    C = C.merge(wb[["iso3"] + keep].add_prefix("o_").rename(columns={"o_iso3": "orig"}),
                on="orig", how="left")
    C = C.merge(wb[["iso3"] + keep].add_prefix("d_").rename(columns={"d_iso3": "dest"}),
                on="dest", how="left")

    # ov_* mirrors the estimation panel's naming so gravity.py can score the
    # absolute spec on these corridors with the same design matrix code.
    wbg = load("worldbank_wide_global")
    ocols = ["gni_pc_ppp", "population", "youth_unemployment", "tertiary_enrolment",
             "internet_users", "urban_share", "wgi_govt_effectiveness",
             "wgi_political_stability", "battle_deaths"]
    have = [c for c in ocols if c in wbg.columns]
    C = C.merge(wbg[["iso3"] + have].add_prefix("ov_").rename(columns={"ov_iso3": "orig"}),
                on="orig", how="left")

    for name, col, pre in [("eurostat_unemployment", "unemployment", "d_es_"),
                           ("eurostat_dependency", "old_age_dependency", "d_es_")]:
        t = _opt(name)
        if t is None:
            continue
        t = t[t.year == t.year.max()]
        t["dest"] = t.dest_es.map(es2iso3_dest)
        C = C.merge(t[["dest", "value"]].rename(columns={"value": pre + col}),
                    on="dest", how="left")

    vac = _opt("eurostat_vacancy")
    if vac is not None:
        v = vac[(vac.nace_r2.isin(["A-S", "B-S"])) & (vac.year == vac.year.max())]
        v = v.groupby("dest_es", as_index=False).value.mean()
        v["dest"] = v.dest_es.map(es2iso3_dest)
        C = C.merge(v[["dest", "value"]].rename(columns={"value": "d_es_vacancy_rate"}),
                    on="dest", how="left")

    wag = _opt("eurostat_sector_wages")
    if wag is not None:
        w = wag[(wag.nace_r2 == "TOTAL") & (wag.year == wag.year.max())]
        w["dest"] = w.dest_es.map(es2iso3_dest)
        C = C.merge(w[["dest", "comp_per_worker_eur"]]
                    .rename(columns={"comp_per_worker_eur": "d_es_wage_eur"}),
                    on="dest", how="left")

    # ------------------------------------------------- visas, income, WHO ----
    step("Schengen visa refusals and consulate presence")
    visas = _opt("schengen_visas")
    if visas is not None:
        C = C.merge(visas[["orig", "dest", "visa_applications", "visa_refused",
                           "visa_refusal_rate", "visa_consulates",
                           "visa_consulate_present"]],
                    on=["orig", "dest"], how="left")
        # Absent means the destination runs no visa operation in that country -
        # a real zero. The refusal RATE stays null: there is no rate without
        # applications, and filling it would invent a number.
        for c in ("visa_applications", "visa_refused", "visa_consulates",
                  "visa_consulate_present"):
            C[c] = C[c].fillna(0)
    else:
        for c in ("visa_applications", "visa_refused", "visa_consulates",
                  "visa_consulate_present"):
            C[c] = 0
        C["visa_refusal_rate"] = np.nan

    step("median income (World Bank PIP)")
    pip = _opt("pip_income")
    if pip is not None:
        p = pip[["iso3", "median_annual_ppp", "welfare_type", "income_year"]]
        C = C.merge(p.add_prefix("o_inc_").rename(columns={"o_inc_iso3": "orig"}),
                    on="orig", how="left")
        C = C.merge(p.add_prefix("d_inc_").rename(columns={"d_inc_iso3": "dest"}),
                    on="dest", how="left")
        # The AJF framing: what a mover actually gains per year, in PPP dollars.
        C["median_income_gap"] = (C.d_inc_median_annual_ppp
                                  - C.o_inc_median_annual_ppp)
        C["median_income_ratio"] = (C.d_inc_median_annual_ppp
                                    / C.o_inc_median_annual_ppp)

    step("UNHCR bilateral refugee and asylum stocks")
    ref = _opt("unhcr_bilateral")
    if ref is not None:
        C = C.merge(ref[["orig", "dest", "refugees", "asylum_seekers", "forced_total"]],
                    on=["orig", "dest"], how="left")
        for c in ("refugees", "asylum_seekers", "forced_total"):
            C[c] = C[c].fillna(0)
        print(f"    {int((C.forced_total > 0).sum()):,} corridors carry recorded "
              f"forced-displacement stock; {C.forced_total.sum():,.0f} people")
    else:
        for c in ("refugees", "asylum_seekers", "forced_total"):
            C[c] = 0.0

    step("skills supply at origin (UNESCO UIS, WHO)")
    uis = _opt("uis_skills")
    if uis is not None:
        cols = [c for c in uis.columns if c != "iso3"]
        C = C.merge(uis.add_prefix("o_").rename(columns={"o_iso3": "orig"}),
                    on="orig", how="left")
        got = C[[f"o_{c}" for c in cols if f"o_{c}" in C.columns]].notna().any(axis=1)
        print(f"    UIS field-of-study data for {C[got].orig.nunique()} of "
              f"{C.orig.nunique()} origins")
    who_w = _opt("who_workforce")
    if who_w is not None:
        C = C.merge(who_w.add_prefix("o_").rename(columns={"o_iso3": "orig"}),
                    on="orig", how="left")
        C = C.merge(who_w.add_prefix("d_").rename(columns={"d_iso3": "dest"}),
                    on="dest", how="left")
        if "o_nurses_midwives_per_10k" in C.columns:
            # The health-workforce ratio is the sharpest single statement of how
            # differently staffed the two ends of a corridor are.
            C["health_workforce_ratio"] = (C.d_nurses_midwives_per_10k
                                           / C.o_nurses_midwives_per_10k.replace(0, pd.NA))
            print(f"    health workforce density on both sides for "
                  f"{int(C.health_workforce_ratio.notna().sum()):,} corridors; "
                  f"median destination/origin ratio "
                  f"{C.health_workforce_ratio.median():.1f}x")

    step("Hofstede cultural distance (Kogut-Singh)")
    hof = _opt("hofstede")
    if hof is not None:
        dims = [c for c in hof.columns if c.startswith("h_")]
        # Kogut-Singh: mean squared difference per dimension, each divided by
        # that dimension's variance across countries so a dimension with a wide
        # spread does not dominate purely because of its scale.
        var = {d: hof[d].var(ddof=0) for d in dims}
        hi = hof.set_index("iso3")
        C = C.merge(hof[["iso3", "hofstede_basis"]]
                    .rename(columns={"iso3": "orig", "hofstede_basis": "o_hofstede_basis"}),
                    on="orig", how="left")
        C = C.merge(hof[["iso3", "hofstede_basis"]]
                    .rename(columns={"iso3": "dest", "hofstede_basis": "d_hofstede_basis"}),
                    on="dest", how="left")
        ks, used = [], []
        for r in C.itertuples():
            if r.orig not in hi.index or r.dest not in hi.index:
                ks.append(np.nan), used.append(0)
                continue
            a, b = hi.loc[r.orig], hi.loc[r.dest]
            terms = [((a[d] - b[d]) ** 2) / var[d] for d in dims
                     if pd.notna(a[d]) and pd.notna(b[d]) and var[d] > 0]
            ks.append(float(np.mean(terms)) if terms else np.nan)
            used.append(len(terms))
        C["hofstede_distance"] = ks
        C["hofstede_dims_used"] = used
        ok = C.hofstede_distance.notna()
        print(f"    Kogut-Singh distance computed for {int(ok.sum()):,} / {len(C):,} "
              f"corridors (mean {C.hofstede_dims_used[ok].mean():.1f} of 6 dimensions)")

    step("WHO health workforce safeguards list")
    who = pd.read_csv(REF / "who_safeguards.csv", encoding="utf-8")
    on23 = set(who[who.on_ssl_2023 == 1].iso3)
    on26 = set(who[who.expected_ssl_2026 == 1].iso3)
    C["who_ssl_2023"] = C.orig.isin(on23).astype(int)
    C["who_ssl_2026"] = C.orig.isin(on26).astype(int)
    # The live flag follows the 2026 list, since that is what will be in force.
    C["who_safeguard_origin"] = C["who_ssl_2026"]
    C["who_expected_removal"] = (C.who_ssl_2023 & ~C.who_ssl_2026.astype(bool)).astype(int)
    C["who_status"] = np.where(
        C.who_ssl_2026 == 1, "On SSL 2023 and expected on SSL 2026",
        np.where(C.who_ssl_2023 == 1, "On SSL 2023, expected to be removed in 2026",
                 "Not listed"))
    print(f"    origins on SSL 2023: {C[C.who_ssl_2023 == 1].orig.nunique()}; "
          f"expected on SSL 2026: {C[C.who_ssl_2026 == 1].orig.nunique()}; "
          f"expected removal: {C[C.who_expected_removal == 1].orig.nunique()}")

    # ----------------------------------------------------------- agreements --
    step("agreements register")
    ag = pd.read_csv(REF / "agreements.csv", encoding="utf-8")
    eu_level = ag[ag.dest_iso3 == "EU"]
    bilat = ag[ag.dest_iso3 != "EU"]

    b = (bilat.groupby(["dest_iso3", "orig_iso3"])
              .agg(n_agreements=("instrument", "size"),
                   agreement_types=("instrument_type", lambda s: ";".join(sorted(set(s)))),
                   has_binding=("binding", lambda s: int((s == "binding").any())),
                   any_active=("status", lambda s: int((s == "active").any())))
              .reset_index().rename(columns={"dest_iso3": "dest", "orig_iso3": "orig"}))
    C = C.merge(b, on=["orig", "dest"], how="left")
    for c in ("n_agreements", "has_binding", "any_active"):
        C[c] = C[c].fillna(0).astype(int)
    C["agreement_types"] = C["agreement_types"].fillna("")

    eu = (eu_level.groupby("orig_iso3")
                  .agg(eu_instruments=("instrument", lambda s: ";".join(sorted(set(s)))),
                       n_eu_instruments=("instrument", "size"))
                  .reset_index().rename(columns={"orig_iso3": "orig"}))
    C = C.merge(eu, on="orig", how="left")
    C["n_eu_instruments"] = C["n_eu_instruments"].fillna(0).astype(int)
    C["eu_instruments"] = C["eu_instruments"].fillna("")
    C["has_any_agreement"] = ((C.n_agreements > 0) | (C.n_eu_instruments > 0)).astype(int)

    # ------------------------------------------------------------- labels ----
    C = C.merge(cc[["iso3", "name", "subregion"]].rename(
        columns={"iso3": "orig", "name": "orig_name", "subregion": "orig_subregion"}),
        on="orig", how="left")
    C = C.merge(cc[["iso3", "name", "subregion"]].rename(
        columns={"iso3": "dest", "name": "dest_name", "subregion": "dest_subregion"}),
        on="dest", how="left")

    save(C, "corridors_base", INTERIM)
    print(f"    {len(C):,} corridors x {len(C.columns)} fields")
    print(f"    with a bilateral agreement on file: {int(C.n_agreements.gt(0).sum()):,}")
    print(f"    zero permits in {ymax}: {int((C.permits_latest == 0).sum()):,} / {len(C):,}")


if __name__ == "__main__":
    main()
