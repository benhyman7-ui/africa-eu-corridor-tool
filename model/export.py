"""Export the scored panel as compact JSON for the static front end.

Everything the browser needs, nothing it does not. Floats are rounded hard - the
difference between a corridor scoring 71.4 and 71.43 is noise, and it is a third
of the payload.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _common import INTERIM, REF, WEBDATA, countries, load, step  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from indices import EASE_WEIGHTS, POTENTIAL_WEIGHTS, PRESETS  # noqa: E402

# NACE A*10 -> the occupation families the tool exposes.
SECTORS = {
    "A": "Agriculture & fishing",
    "B-E": "Industry & manufacturing",
    "F": "Construction & trades",
    "G-I": "Hospitality, retail & transport",
    "J": "ICT",
    "K": "Finance",
    "L": "Real estate",
    "M_N": "Professional & support services",
    "O-Q": "Health, care & public services",
    "R_S": "Arts, recreation & other services",
}

CORRIDOR_FIELDS = {
    # identity
    "orig": 0, "dest": 0, "orig_name": 0, "dest_name": 0,
    "orig_subregion": 0, "dest_subregion": 0,
    # flows and the model
    "permits_latest": 0, "permits_mean3": 1, "permits_total": 0,
    "predicted": 1, "gap_log": 2, "predicted_absolute": 1, "gap_log_absolute": 2,
    "predicted_structural": 1, "gap_log_structural": 2,
    "permits_blue_card": 1, "permits_high_skilled": 1, "permits_researcher": 1,
    "permits_seasonal": 1, "permits_other_emp": 1,
    # composites
    "score_cgd": 1, "score_ajf": 1, "score_balanced": 1, "score_cgd_geometric": 1,
    "underutilisation_score": 1, "ease_score": 1, "potential_score": 1,
    # components
    "u_gap": 1, "u_gap_absolute": 1, "u_gap_structural": 1,
    "p_income_gain": 1, "p_labour_supply": 1, "p_dest_demand": 1, "p_skills_supply": 1,
    "skills_inputs_present": 0, "health_workforce_ratio": 1,
    "o_nurses_midwives_per_10k": 1, "d_nurses_midwives_per_10k": 1,
    "o_grad_health_welfare": 1, "o_grad_ict": 1, "o_grad_engineering_construction": 1,
    "o_grad_agriculture": 1, "o_grad_services": 1, "o_grad_business_law": 1,
    "o_grad_stem": 1, "o_vocational_enrolment_share": 1, "o_literacy": 1,
    "o_tertiary_enrolment": 1,
    "f_consular": 1, "f_language": 1, "f_agreement": 1, "f_connectivity": 1,
    "f_proximity": 1, "f_admin": 1, "f_cultural": 1,
    # raw facts for the corridor card
    "dist": 0, "routes": 0, "diaspora": 0,
    "dest_embassy_in_orig": 0, "dest_missions_in_orig": 0, "orig_embassy_in_dest": 0,
    "neighbour_embassy": 0, "n_neighbour_embassies": 0,
    "neighbour_embassy_countries": 0, "nearest_embassy_km": 0,
    "nearest_embassy_country": 0,
    "shared_official_language": 0, "dest_language_present_at_origin": 0,
    "origin_bridge_languages": 0, "dest_official_languages": 0,
    "religion_similarity": 3, "religion_catholic_similarity": 3,
    "same_majority_religion": 0, "majority_religion_orig": 0,
    "majority_religion_dest": 0,
    "n_agreements": 0, "agreement_types": 0, "eu_instruments": 0, "has_binding": 0,
    "n_eu_instruments": 0, "has_any_agreement": 0,
    "income_multiple": 2, "median_income_gap": 0, "median_income_ratio": 2,
    "o_inc_median_annual_ppp": 0, "d_inc_median_annual_ppp": 0,
    "o_inc_welfare_type": 0, "d_inc_welfare_type": 0,
    "visa_applications": 0, "visa_refused": 0, "visa_refusal_rate": 3,
    "visa_consulates": 0, "visa_consulate_present": 0, "consular_tier": 0,
    "who_safeguard_origin": 0, "who_ssl_2023": 0, "who_ssl_2026": 0,
    "who_expected_removal": 0, "who_status": 0, "f_visa_success": 1,
    "refugees": 0, "asylum_seekers": 0, "forced_total": 0,
    "religion_proximity": 1, "hofstede_proximity": 1, "hofstede_distance": 3,
    "hofstede_dims_used": 0, "cultural_basis": 0, "log_income_gap": 3,
    "o_gni_pc_ppp": 0, "d_gni_pc_ppp": 0, "colony": 0, "comlang_off": 0,
    "o_youth_unemployment": 1, "o_working_age_pop": 0, "o_wgi_govt_effectiveness": 2,
    "d_es_vacancy_rate": 2, "d_es_unemployment": 1, "d_es_wage_eur": 0,
    # flags
    "low_hanging_fruit": 0, "origin_in_allocation_fit": 0, "latest_year": 0,
}


def clean(v, dp: int):
    """Coerce one cell to a JSON scalar.

    Series.tolist() hands back PYTHON ints, not numpy ints. An earlier version
    only tested numpy types, so every integer fell through to str() and shipped
    as "0"/"1" - and "0" is truthy in JavaScript, which made every corridor claim
    it had an embassy. bool is checked before int because bool subclasses int.
    """
    if v is None or v is pd.NA:
        return None
    if isinstance(v, (np.bool_, bool)):
        return int(v)
    if isinstance(v, (np.integer, int)):
        return int(v)
    if isinstance(v, (np.floating, float)):
        return None if not np.isfinite(v) else (
            int(round(v)) if dp == 0 else round(float(v), dp))
    return str(v)


def main() -> None:
    step("loading scored panel")
    df = load("corridors_scored", INTERIM)
    cc = countries()

    # ------------------------------------------------------------ corridors --
    step("corridors.json")
    missing = [c for c in CORRIDOR_FIELDS if c not in df.columns]
    if missing:
        print(f"      absent from panel, skipped: {missing}")
    fields = {k: v for k, v in CORRIDOR_FIELDS.items() if k in df.columns}
    # Columnar, not an array of objects: repeating 100 key names 1,458 times
    # costs more than the data itself. The browser rehydrates on load.
    cols = {k: [clean(v, dp) for v in df[k].tolist()] for k, dp in fields.items()}

    # String columns are then dictionary-encoded. They were 350 KB of an 854 KB
    # payload, and most hold a handful of distinct values repeated 1,458 times -
    # who_status alone was 48 KB for three possible strings.
    dicts: dict[str, list] = {}
    for k, vals in cols.items():
        if not any(isinstance(v, str) for v in vals):
            continue
        uniq = sorted({v for v in vals if isinstance(v, str)})
        if len(uniq) > len(vals) * 0.7:      # nothing to gain on near-unique text
            continue
        lut = {s: i for i, s in enumerate(uniq)}
        dicts[k] = uniq
        cols[k] = [lut.get(v) if isinstance(v, str) else None for v in vals]

    (WEBDATA / "corridors.json").write_text(
        json.dumps({"fields": list(fields), "columns": cols, "dicts": dicts},
                   separators=(",", ":")), encoding="utf-8")
    print(f"      dictionary-encoded {len(dicts)} string fields")

    # ------------------------------------------------------- yearly series --
    step("series.json")
    perm = load("eurostat_permits_employment")
    to3 = dict(zip(cc.eurostat_code, cc.iso3))
    perm = perm[perm.permit_category == "EMP"].copy()
    perm["orig"] = perm.orig_es.map(to3)
    perm["dest"] = perm.dest_es.map(to3)
    perm = perm.dropna(subset=["orig", "dest"])
    years = sorted(perm.year.unique().tolist())
    piv = (perm.groupby(["orig", "dest", "year"]).value.sum()
               .unstack("year").reindex(columns=years))
    series = {f"{o}-{d}": [None if pd.isna(v) else int(round(v)) for v in row]
              for (o, d), row in zip(piv.index, piv.to_numpy())}
    (WEBDATA / "series.json").write_text(
        json.dumps({"years": years, "series": series}, separators=(",", ":")),
        encoding="utf-8")

    # --------------------------------------------------- destination sectors --
    step("sectors.json")
    sectors: dict[str, dict] = {}
    try:
        vac = load("eurostat_vacancy")
        wag = load("eurostat_sector_wages")
        vac = vac[vac.year == vac.year.max()]
        wag = wag[wag.year == wag.year.max()]
        for es, iso3 in zip(cc[cc.side == "destination"].eurostat_code,
                            cc[cc.side == "destination"].iso3):
            v = vac[vac.dest_es == es].set_index("nace_r2").value.to_dict()
            w = wag[wag.dest_es == es].set_index("nace_r2")
            entry = {}
            for code, label in SECTORS.items():
                row = w.loc[code] if code in w.index else None
                entry[code] = {
                    "label": label,
                    "vacancy": clean(v.get(code), 2),
                    "wage_eur": clean(row.comp_per_worker_eur, 0) if row is not None else None,
                    "employment_ths": clean(row.emp_ths, 0) if row is not None else None,
                }
            sectors[iso3] = entry
    except Exception as exc:  # noqa: BLE001
        print(f"      sector tables unavailable: {exc}")

    (WEBDATA / "sectors.json").write_text(
        json.dumps(sectors, separators=(",", ":")), encoding="utf-8")

    # ----------------------------------------------------------------- meta --
    step("meta.json")
    ag = pd.read_csv(REF / "agreements.csv", encoding="utf-8")
    meta = {
        "generated_note": "Africa->EU labour mobility corridors, EU-27",
        "latest_year": int(df.latest_year.iloc[0]),
        "series_years": years,
        "n_corridors": int(len(df)),
        "n_origins": int(df.orig.nunique()),
        "n_destinations": int(df.dest.nunique()),
        "presets": PRESETS,
        "ease_weights": EASE_WEIGHTS,
        "potential_weights": POTENTIAL_WEIGHTS,
        "sectors": SECTORS,
        "countries": [
            {"iso3": r.iso3, "name": r.name, "side": r.side,
             "subregion": r.subregion, "lat": clean(r.lat, 3), "lon": clean(r.lon, 3)}
            for r in cc.itertuples()],
        "agreements": [
            {k: (None if pd.isna(v) else v) for k, v in row.items()}
            for row in ag.to_dict("records")],
        "who_list_version": "2026 (expected)",
        "visa_year": int(load("schengen_visas").visa_year.iloc[0])
        if "visa_refusal_rate" in df.columns else None,
        "totals": {
            "actual_mean3": int(df.permits_mean3.sum()),
            "predicted": int(df.predicted.sum()),
            "corridors_below_prediction": int((df.gap_log < 0).sum()),
            "corridors_zero_latest": int((df.permits_latest == 0).sum()),
            "low_hanging_fruit": int(df.low_hanging_fruit.sum()),
            "embassy_present": int(df.dest_embassy_in_orig.sum()),
            "can_apply_in_country": int(((df.visa_consulate_present == 1)
                                         | (df.dest_embassy_in_orig == 1)).sum()),
            "direct_flight": int((df.routes > 0).sum()),
            "any_agreement": int(df.has_any_agreement.sum()),
            "africa_refusal_rate": float(df.visa_refused.sum()
                                         / max(df.visa_applications.sum(), 1)),
            "median_income_gap": int(df.median_income_gap.median()),
            "who_origins": int(df[df.who_ssl_2026 == 1].orig.nunique()),
            "who_origins_2023": int(df[df.who_ssl_2023 == 1].orig.nunique()),
            "who_expected_removal": int(df[df.who_expected_removal == 1].orig.nunique()),
            "cultural_with_hofstede": int((df.cultural_basis == "religion + Hofstede").sum()),
        },
    }
    (WEBDATA / "meta.json").write_text(
        json.dumps(meta, separators=(",", ":")), encoding="utf-8")

    for f in ("corridors.json", "series.json", "sectors.json", "meta.json"):
        kb = (WEBDATA / f).stat().st_size / 1024
        print(f"    {f:<18}{kb:>8.0f} KB")


if __name__ == "__main__":
    main()
