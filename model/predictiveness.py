"""Which factors actually predict current migration in a dyad?

The tool weights ~60 factors by judgement. This asks the data: for every
parameter on file, how predictive is it of current annual migration
(permits_mean3, the 3-year mean of first work permits) between an origin and a
destination?

Three methods, because they answer different questions:

1. Correlations - pooled, within-origin and within-destination. The within
   views matter: this project has already been burned by pooled correlations
   that were really country size in disguise (a factor at -0.31 pooled carried
   zero within-destination signal).

2. Gradient boosting with permutation importance, cross-validated with
   GroupKFold by origin and, separately, by destination - "does this
   generalise to a country the model never saw", which is the deployment
   question. Run twice: once with everything, once with the network variables
   held out, because diaspora and air traffic FORECAST flows without being
   levers anyone can pull.

3. A PPML count-space check on the factors the boosting ranks highly, reusing
   the gravity machinery.

Analysis, not pipeline: this is deliberately NOT in build.py's STAGES. Run it
by hand when the question comes up. Output goes to
reports/factor_predictiveness.md and data/interim/factor_predictiveness.csv.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _common import INTERIM, ROOT, load, step  # noqa: E402

REPORTS = ROOT / "reports"
REPORTS.mkdir(exist_ok=True)

# --------------------------------------------------------------------------- #
# Every factor, its tier, and how to transform it before correlating.
#
# Tiers are the honesty device. A naive "correlate everything" contest is won
# by variables that migration itself causes, and a recommendation built on that
# would tell the fund to buy diasporas.
#   N  network/endogenous - predictive but partly CAUSED by migration
#   P  policy/access      - things a state or programme can change
#   S  structural dyadic  - fixed facts of the pair
#   O  origin traits
#   D  destination traits
# The derived f_*/p_* score components are correlated for completeness
# (tier "derived") but excluded from the ML feature set: they are monotone
# transforms of raws already present, and several embed panel-wide ranks.
# --------------------------------------------------------------------------- #
LOG = "log1p"          # heavy right skew: correlate the log
ID = "identity"

FACTORS = {
    # --- N: network / endogenous ------------------------------------------- #
    "diaspora":                 ("N", LOG),
    "diaspora_cagr":            ("N", ID),
    "air_passengers":           ("N", LOG),
    "routes":                   ("N", LOG),
    "student_permits":          ("N", LOG),
    "forced_total":             ("N", LOG),
    "refugees":                 ("N", LOG),
    "asylum_seekers":           ("N", LOG),
    "visa_applications":        ("N", LOG),
    # --- P: policy / access ------------------------------------------------ #
    "dest_embassy_in_orig":     ("P", ID),
    "dest_missions_in_orig":    ("P", ID),
    "neighbour_embassy":        ("P", ID),
    "nearest_embassy_km":       ("P", LOG),
    "vfs_present":              ("P", ID),
    "visa_consulate_present":   ("P", ID),
    "visa_refusal_rate":        ("P", ID),
    "n_agreements":             ("P", ID),
    "has_binding":              ("P", ID),
    "has_any_agreement":        ("P", ID),
    "n_eu_instruments":         ("P", ID),
    "icmpd_promise_mean":       ("P", ID),
    "icmpd_promise_best":       ("P", ID),
    "icmpd_restriction_load":   ("P", ID),
    "africa_orientation_penalty": ("P", ID),
    "pt_has_fast_track":        ("P", ID),
    "pt_talent_partnership":    ("P", ID),
    "req_blue_card_salary_eur": ("P", LOG),
    "salary_threshold_ratio":   ("P", ID),
    # --- S: structural dyadic ---------------------------------------------- #
    "dist":                     ("S", LOG),
    "contig":                   ("S", ID),
    "comlang_off":              ("S", ID),
    "comlang_ethno":            ("S", ID),
    "colony":                   ("S", ID),
    "comcol":                   ("S", ID),
    "shared_official_language": ("S", ID),
    "dest_language_present_at_origin": ("S", ID),
    "language_proximity":       ("S", ID),
    "religion_similarity":      ("S", ID),
    "religion_catholic_similarity": ("S", ID),
    "same_majority_religion":   ("S", ID),
    "hofstede_distance":        ("S", ID),
    "income_doublings":         ("S", ID),
    "median_income_gap":        ("S", LOG),
    "income_multiple":          ("S", LOG),
    # --- O: origin traits --------------------------------------------------- #
    "o_literacy":               ("O", ID),
    "o_tertiary_enrolment":     ("O", ID),
    "o_vocational_enrolment_share": ("O", ID),
    "o_grad_health_welfare":    ("O", ID),
    "o_grad_ict":               ("O", ID),
    "o_grad_engineering_construction": ("O", ID),
    "o_grad_stem":              ("O", ID),
    "o_nurses_midwives_per_10k": ("O", LOG),
    "o_youth_unemployment":     ("O", ID),
    "o_working_age_pop":        ("O", LOG),
    "o_internet_users":         ("O", ID),
    "o_wgi_govt_effectiveness": ("O", ID),
    "o_gni_pc_ppp":             ("O", LOG),
    "o_gdp_pc_ppp":             ("O", LOG),
    "o_inc_median_annual_ppp":  ("O", LOG),
    "o_income_blend":           ("O", LOG),
    "who_safeguard_origin":     ("O", ID),
    # --- D: destination traits ---------------------------------------------- #
    "d_es_vacancy_rate":        ("D", ID),
    "d_es_unemployment":        ("D", ID),
    "d_es_wage_eur":            ("D", LOG),
    "d_inc_median_annual_ppp":  ("D", LOG),
    "d_gdp_pc_ppp":             ("D", LOG),
    "d_nurses_midwives_per_10k": ("D", ID),
}

DERIVED = ["f_consular", "f_visa_success", "f_pathways", "f_language",
           "f_agreement", "f_salary_threshold", "f_recognition", "f_cultural",
           "f_processing", "f_connectivity", "f_admin", "f_proximity",
           "p_income_gain", "p_skills_supply", "p_labour_supply",
           "p_dest_demand", "p_diaspora_growth"]

# Contain the outcome by construction; never correlated, never features.
LEAKAGE = ["predicted", "predicted_absolute", "predicted_structural",
           "gap_log", "gap_log_absolute", "gap_log_structural",
           "u_gap", "u_gap_absolute", "u_gap_structural",
           "unrealised_flow", "dcy_catalysable", "scale_score",
           "score_cgd", "score_ajf", "score_balanced", "score_cgd_geometric",
           "underutilisation_score", "access_score", "ease_score",
           "potential_score", "low_hanging_fruit",
           "permits_latest", "permits_total", "permits_blue_card",
           "permits_high_skilled", "permits_researcher", "permits_seasonal",
           "permits_other_emp", "student_to_work_ratio", "p_student_feeder"]


def transform(s: pd.Series, how: str) -> pd.Series:
    v = pd.to_numeric(s, errors="coerce")
    return np.log1p(v.clip(lower=0)) if how == LOG else v


def demean(s: pd.Series, g: pd.Series) -> pd.Series:
    return s - s.groupby(g).transform("mean")


# --------------------------------------------------------------------------- #
# 1. Correlations
# --------------------------------------------------------------------------- #
def correlations(df: pd.DataFrame) -> pd.DataFrame:
    y = np.log1p(df["permits_mean3"].fillna(0))
    rows = []
    items = [(k, t, h) for k, (t, h) in FACTORS.items()] + \
            [(k, "derived", ID) for k in DERIVED]
    for name, tier, how in items:
        if name not in df.columns:
            continue
        x = transform(df[name], how)
        ok = x.notna()
        if ok.sum() < 60 or x[ok].nunique() < 2:
            continue
        sub_x, sub_y = x[ok], y[ok]
        rows.append({
            "factor": name, "tier": tier, "n": int(ok.sum()),
            "pearson": sub_x.corr(sub_y),
            "spearman": stats.spearmanr(sub_x, sub_y).statistic,
            "within_orig": demean(sub_x, df.orig[ok]).corr(demean(sub_y, df.orig[ok])),
            "within_dest": demean(sub_x, df.dest[ok]).corr(demean(sub_y, df.dest[ok])),
        })
    out = pd.DataFrame(rows)
    out["abs_within"] = out[["within_orig", "within_dest"]].abs().max(axis=1)
    return out.sort_values("abs_within", ascending=False)


# --------------------------------------------------------------------------- #
# 2. Gradient boosting + permutation importance, grouped CV
# --------------------------------------------------------------------------- #
def ml_importance(df: pd.DataFrame, tiers: str, label: str) -> dict:
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.inspection import permutation_importance
    from sklearn.model_selection import GroupKFold, cross_val_score

    feats = [k for k, (t, _) in FACTORS.items()
             if t in tiers and k in df.columns]
    X = pd.DataFrame({k: transform(df[k], FACTORS[k][1]) for k in feats})
    y = np.log1p(df["permits_mean3"].fillna(0))

    model = HistGradientBoostingRegressor(
        max_iter=400, learning_rate=0.06, max_leaf_nodes=31,
        l2_regularization=1.0, random_state=7)

    scores = {}
    for gname, groups in (("origin", df.orig), ("destination", df.dest)):
        cv = GroupKFold(n_splits=5)
        scores[gname] = cross_val_score(model, X, y, groups=groups, cv=cv,
                                        scoring="r2").mean()

    # Permutation importance on held-out data: one grouped split by origin, so
    # importances measure what helps on origins the model never saw.
    cv = GroupKFold(n_splits=5)
    tr, te = next(iter(cv.split(X, y, groups=df.orig)))
    fitted = model.fit(X.iloc[tr], y.iloc[tr])
    pi = permutation_importance(fitted, X.iloc[te], y.iloc[te],
                                n_repeats=12, random_state=7)
    imp = (pd.DataFrame({"factor": feats, "importance": pi.importances_mean,
                         "imp_sd": pi.importances_std})
           .sort_values("importance", ascending=False))
    imp["tier"] = imp.factor.map({k: t for k, (t, _) in FACTORS.items()})
    print(f"    [{label}] CV R2: by-origin {scores['origin']:.3f}, "
          f"by-destination {scores['destination']:.3f}; "
          f"top: {', '.join(imp.head(5).factor)}")
    return {"label": label, "scores": scores, "importance": imp}


# --------------------------------------------------------------------------- #
# 3. PPML count-space check on the top exogenous factors
# --------------------------------------------------------------------------- #
def ppml_check(df: pd.DataFrame, top_feats: list[str]) -> pd.DataFrame:
    import gravity as G

    keep = []
    frame = pd.DataFrame({"orig": df.orig, "dest": df.dest,
                          "permits": df.permits_mean3.fillna(0)})
    for k in top_feats:
        t, how = FACTORS[k]
        v = transform(df[k], how)
        if v.notna().mean() < 0.85:      # PPML drops NaN rows listwise
            continue
        # Destination-constant factors are absorbed by the destination FE and
        # come back as a mechanical zero, which reads as "no effect" when it
        # means "not identifiable here". Leave them out instead.
        if demean(v, df.dest).var() < 1e-9:
            continue
        frame[k] = v.fillna(v.median())
        keep.append(k)
    # ppml_hdfe takes raw arrays: y, X, and FE groups as (codes, n_levels).
    # Destination FE absorbs country size, so the coefficients answer the
    # within-destination question the correlations asked less formally.
    y = frame.permits.to_numpy()
    X = frame[keep].to_numpy(dtype=float)
    X = (X - X.mean(axis=0)) / np.where(X.std(axis=0) > 0, X.std(axis=0), 1.0)
    codes = frame.dest.astype("category").cat.codes.to_numpy()
    beta, se, _mu, info = G.ppml_hdfe(
        y, X, [(codes, int(codes.max()) + 1)],
        cluster=frame.orig.astype("category").cat.codes.to_numpy())
    rows = []
    for name, b, s in zip(keep, beta, se):
        rows.append({"factor": name, "coef": b, "se": s,
                     "z": b / s if s else np.nan})
    return pd.DataFrame(rows).sort_values("z", key=abs, ascending=False)


# --------------------------------------------------------------------------- #
def md_table(df: pd.DataFrame, cols: list[str], n: int | None = None) -> str:
    d = df if n is None else df.head(n)
    fmtd = d[cols].copy()
    for c in fmtd.columns:
        if fmtd[c].dtype.kind == "f":
            fmtd[c] = fmtd[c].map(lambda v: f"{v:+.2f}" if pd.notna(v) else "")
    lines = ["| " + " | ".join(cols) + " |",
             "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in fmtd.iterrows():
        lines.append("| " + " | ".join(str(v) for v in r) + " |")
    return "\n".join(lines)


def main() -> None:
    step("factor predictiveness")
    df = load("corridors_scored", INTERIM)

    corr = correlations(df)
    corr.to_csv(INTERIM / "factor_predictiveness.csv", index=False)
    print(f"    correlations for {len(corr)} factors "
          f"-> data/interim/factor_predictiveness.csv")

    step("gradient boosting, grouped cross-validation")
    run_all = ml_importance(df, "NPSOD", "all factors incl. network")
    run_exo = ml_importance(df, "PSOD", "exogenous only (no network)")

    step("PPML count-space check on top exogenous factors")
    top = [f for f in run_exo["importance"].factor.head(12)]
    ppml = ppml_check(df, top)
    for r in ppml.itertuples():
        sig = "***" if abs(r.z) > 2.58 else "**" if abs(r.z) > 1.96 else ""
        print(f"      {r.factor:<28}{r.coef:>+8.3f}  z={r.z:>+6.1f} {sig}")

    # ------------------------------------------------------------- report ---
    rep = ["# Which factors predict current migration in a dyad?", "",
           "Outcome: `log1p(permits_mean3)` - the 3-year mean of annual first",
           "work permits, 1,458 Africa->EU corridors. Generated by",
           "`model/predictiveness.py`; regenerate after any rebuild.", "",
           "Tier key: **N** network/endogenous (forecasts flows, is not a",
           "lever) - **P** policy/access - **S** structural dyadic - **O**",
           "origin traits - **D** destination traits - derived = the tool's",
           "own percentile components, shown for reference only.", "",
           "## Correlations (top 25 by within-country signal)", "",
           "`within_orig`/`within_dest` strip country size out; pooled",
           "correlations flatter destination-constant factors.", "",
           md_table(corr, ["factor", "tier", "n", "pearson", "spearman",
                           "within_orig", "within_dest"], 25), "",
           "## Gradient boosting, grouped cross-validation", ""]
    for run in (run_all, run_exo):
        rep += [f"### {run['label']}",
                f"R2 predicting unseen **origins**: "
                f"{run['scores']['origin']:.3f}; unseen **destinations**: "
                f"{run['scores']['destination']:.3f}", "",
                md_table(run["importance"],
                         ["factor", "tier", "importance", "imp_sd"], 15), ""]
    rep += ["## PPML count-space check (dest FE, top exogenous factors)", "",
            md_table(ppml, ["factor", "coef", "se", "z"]), "",
            ]
    # The recommendations are interpretation, not computation, so they live in
    # a hand-written include that survives regeneration. If the numbers above
    # move materially on a rebuild, the include is what needs re-reading.
    inc = REPORTS / "_recommendations.md"
    if inc.exists():
        rep += [inc.read_text(encoding="utf-8")]
    else:
        rep += ["## Recommendations", "",
                "_Not yet written: create reports/_recommendations.md._", ""]
    (REPORTS / "factor_predictiveness.md").write_text(
        "\n".join(rep), encoding="utf-8")
    print(f"    report -> reports/factor_predictiveness.md")


if __name__ == "__main__":
    main()
