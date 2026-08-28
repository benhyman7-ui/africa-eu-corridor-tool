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


def language_proximity() -> callable:
    """Closeness of two languages on 0-1, from ref/language_families.csv.

    Tiered rather than continuous because that is all the underlying
    classification supports: same language, same subfamily, same family, or
    unrelated. A handful of pairs where the tier badly misstates real
    intelligibility carry an explicit override with its basis recorded -
    Afrikaans and Dutch sit far above the West-Germanic default, Maltese and
    Arabic well below the Semitic one.
    """
    fam = pd.read_csv(REF / "language_families.csv", encoding="utf-8",
                      keep_default_na=False, na_values=[""])
    ov = pd.read_csv(REF / "language_proximity_overrides.csv", encoding="utf-8")
    f = {r.language: (r.family, r.subfamily) for r in fam.itertuples()}
    o = {}
    for r in ov.itertuples():
        o[(r.language_a, r.language_b)] = float(r.proximity)
        o[(r.language_b, r.language_a)] = float(r.proximity)

    def prox(a: str, b: str) -> float:
        if not a or not b:
            return 0.0
        if a == b:
            return 1.0
        if (a, b) in o:
            return o[(a, b)]
        fa, fb = f.get(a), f.get(b)
        if fa is None or fb is None:
            return 0.0            # unclassified, e.g. bare "Creole"
        if fa[1] == fb[1]:
            return 0.60
        if fa[0] == fb[0]:
            # Same family only. Indo-European spans English to Greek to Polish,
            # so this is worth a token rather than a real score: shared script
            # and cognate vocabulary make the language easier to learn, which
            # is not nothing, but it is not intelligibility.
            return 0.15
        return 0.0

    return prox


def main() -> None:
    cc = countries()
    to3 = iso2_to_iso3()
    lang_prox = language_proximity()
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
    # UN DESA, not Eurostat. Eurostat migr_pop1ctz is a reporting series and
    # seven destinations publish no third-country citizenship detail at all:
    # France gave African figures for 2018 and never again, Denmark stopped
    # after 2022, and Cyprus, Greece, Croatia, Malta and Poland have never
    # published any. Spain reports ten of fifty-four African citizenships.
    # Zero-filling that made the panel assert there are no Moroccans in France,
    # and since diaspora is a gravity regressor the error propagated into the
    # underutilisation benchmark for all 54 French corridors.
    #
    # UN DESA estimates the full matrix instead of passing through what states
    # choose to publish. It carries 9.27m Africa-EU migrants against Eurostat's
    # 3.89m, and the 5.4m difference is overwhelmingly France.
    #
    # Unlisted pairs here ARE genuine zeros, unlike in Eurostat: the UN table
    # omits pairs with negligible estimated stock rather than suppressing
    # unreported ones, and every pair it does list carries a value.
    und = _opt("undesa_migrant_stock")
    if und is not None:
        dia_stock = und[["orig", "dest", "stock_2024"]].rename(
            columns={"stock_2024": "diaspora"})
        dgrowth = und[["orig", "dest", "diaspora_cagr"]]
        print(f"    UN DESA stock on {len(dia_stock)} corridors, "
              f"growth on {int(und.diaspora_cagr.notna().sum())}")
    else:
        dia_stock = pd.DataFrame(columns=["orig", "dest", "diaspora"])
        dgrowth = pd.DataFrame(columns=["orig", "dest", "diaspora_cagr"])

    # --------------------------------------------------------- dyadic terms --
    step("dyadic covariates")
    geo = load("cepii_geodist")[["orig", "dest", "dist", "contig", "comlang_off",
                                 "comlang_ethno", "colony", "comcol"]]
    routes = load("openflights_pairs")[["orig", "dest", "routes", "airlines"]]

    # ------------------------------------------------------- global panel ----
    step("global estimation panel")
    g = flows.merge(geo, on=["orig", "dest"], how="left")
    g = g.merge(routes, on=["orig", "dest"], how="left")
    # UN DESA is five-yearly to 2020 then 2024, so the panel years in between
    # are filled at the constant growth rate the two endpoints imply. That is
    # what the CAGR already asserts; it adds no precision the source lacks, it
    # just stops a slow-moving stock being held flat across seven years.
    if len(dia_stock):
        g = g.merge(und[["orig", "dest", "stock_2020", "stock_2024",
                         "diaspora_cagr"]], on=["orig", "dest"], how="left")
        yrs = (g.year - 2020).clip(lower=0, upper=4)
        grown = g.stock_2020 * (1 + g.diaspora_cagr.fillna(0.0)) ** yrs
        g["diaspora"] = grown.where(grown.notna(), g.stock_2024)
        g = g.drop(columns=["stock_2020", "stock_2024", "diaspora_cagr"])
    else:
        g["diaspora"] = np.nan
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
    C = C.merge(dia_stock, on=["orig", "dest"], how="left")
    C = C.merge(dgrowth, on=["orig", "dest"], how="left")
    C = C.merge(load("consular_presence"), on=["orig", "dest"], how="left")
    _air = _opt("eurostat_air_pairs")
    if _air is not None:
        C = C.merge(_air, on=["orig", "dest"], how="left")
    for _c in ("air_passengers", "air_year"):
        if _c not in C.columns:
            C[_c] = np.nan

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

        # Closest related pair across the two countries, for corridors that
        # share no language outright. A Lusophone origin is genuinely closer to
        # Spain than a Swahili-speaking one is, and the old binary overlap
        # scored both identically at the floor.
        best, best_pair = 0.0, ""
        for a in o_all:
            for b in d_off:
                pr = lang_prox(a, b)
                if pr > best:
                    best, best_pair = pr, f"{a}-{b}"
        rows.append({
            "language_proximity": best,
            "closest_language_pair": best_pair,
            "religion_similarity": rel_sim,
            "religion_catholic_similarity": cath_sim,
            "same_majority_religion": int(maj_o == maj_d and maj_o != ""),
            "majority_religion_orig": maj_o.replace("rel_", ""),
            "majority_religion_dest": maj_d.replace("rel_", ""),
            "shared_official_language": int(shared),
            "dest_language_present_at_origin": int(usable),
            "origin_official_languages": ";".join(sorted(o_off)),
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

    # ---- student flows: already downloaded, never used until now ----------- #
    step("student permits as a feeder indicator")
    allr = _opt("eurostat_permits_allreason")
    if allr is not None:
        st = allr[allr.reason == "EDUC"].copy()
        st["orig"] = st.orig_es.map(lambda c: to3.get(c))
        st["dest"] = st.dest_es.map(es2iso3_dest)
        st = st.dropna(subset=["orig", "dest"])
        st = st[st.year >= ymax - 2]
        st = (st.groupby(["orig", "dest"], as_index=False).value.mean()
                .rename(columns={"value": "student_permits"}))
        C = C.merge(st, on=["orig", "dest"], how="left")
        C["student_permits"] = C.student_permits.fillna(0.0)
        # Students are a documented feeder into work permits: a corridor already
        # moving graduates has recruitment channels, credential recognition and
        # alumni networks that a cold corridor does not.
        C["student_to_work_ratio"] = (C.student_permits
                                      / C.permits_mean3.replace(0, np.nan))
        print(f"    {int((C.student_permits > 0).sum()):,} corridors carry student "
              f"permits; {C.student_permits.sum():,.0f}/yr total vs "
              f"{C.permits_mean3.sum():,.0f} work permits")

    # ---- destination requirements and VFS ---------------------------------- #
    step("salary thresholds, recognition regimes and VFS centres")
    req = pd.read_csv(REF / "destination_requirements.csv", encoding="utf-8")
    C = C.merge(req.drop(columns=["country"]).add_prefix("req_")
                  .rename(columns={"req_iso3": "dest"}), on="dest", how="left")
    vfs = pd.read_csv(REF / "vfs_centres.csv", encoding="utf-8")
    vfs["vfs_present"] = 1
    C = C.merge(vfs.rename(columns={"dest_iso3": "dest", "orig_iso3": "orig",
                                    "operator": "vfs_operator",
                                    "note": "vfs_note"})[
                    ["dest", "orig", "vfs_present", "vfs_operator", "vfs_note"]],
                on=["dest", "orig"], how="left")
    C["vfs_present"] = C.vfs_present.fillna(0).astype(int)
    print(f"    VFS/TLS/BLS centres on {int(C.vfs_present.sum()):,} corridors; "
          f"salary threshold on file for "
          f"{int(C.req_blue_card_salary_eur.notna().sum() / 54)} destinations")

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

    step("ICMPD pathway promise by destination")
    icm = _opt("icmpd_by_destination")
    if icm is not None:
        C = C.merge(icm, on="dest", how="left")
        print(f"    ICMPD promise attached to {int(C.icmpd_promise_mean.notna().sum()):,} "
              f"corridors ({C.dest.nunique()} destinations)")

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

    # ---- permit processing time -------------------------------------------- #
    step("permit processing deadlines")
    pt = pd.read_csv(REF / "processing_times.csv", encoding="utf-8",
                     keep_default_na=False, na_values=[""])
    C = C.merge(pt.drop(columns=["country", "notes"]).add_prefix("pt_")
                  .rename(columns={"pt_iso3": "dest"}), on="dest", how="left")
    C["pt_has_fast_track"] = C.pt_fast_track_scheme.fillna("").ne("").astype(int)

    # The recast Single Permit Directive cuts the deadline from 90 to 45 days
    # for applicants selected through an EU Talent Partnership. That is the one
    # part of processing time that varies by CORRIDOR rather than destination,
    # and Talent Partnerships are exactly the Africa-EU instruments already in
    # the agreements register - Morocco, Tunisia, Egypt, Nigeria, Senegal.
    C["pt_talent_partnership"] = C.eu_instruments.fillna("").str.contains(
        "Talent Partnership").astype(int)
    # Denmark and Ireland sit outside the Directive on Protocols 22 and 21, so
    # they carry no EU deadline; both run their own regime with a fast-track.
    C["pt_eu_deadline"] = C.pt_regime.eq("eu_directive").astype(int)
    print(f"    {int(C.pt_has_fast_track.sum() / 54)} destinations with a fast-track scheme; "
          f"{int(C.pt_talent_partnership.sum())} corridors on the 45-day Talent Partnership track")

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
