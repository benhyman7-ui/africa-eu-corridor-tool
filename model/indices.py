"""Turn the corridor panel into scores.

Every component is a percentile rank within the 1,458-corridor universe, on a
0-100 scale, oriented so that HIGHER IS ALWAYS BETTER for a funder. Percentile
rather than z-score because several inputs (permits, diaspora, routes) are
extremely skewed and a z-score would let one corridor dominate a whole index.

The composite weights live in the browser so the sliders re-rank instantly; the
defaults here are the same numbers the front end starts with, so the static
ranking and the interactive one agree on first load.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _common import INTERIM, load, save, step  # noqa: E402

# CGD framing: find corridors where flows are unexpectedly low AND the barriers
# to fixing that are unexpectedly small. Potential matters but is not the lead.
PRESETS = {
    "cgd": {"underutilisation": 0.50, "ease": 0.35, "potential": 0.15},
    "ajf": {"underutilisation": 0.25, "ease": 0.25, "potential": 0.50},
    "balanced": {"underutilisation": 0.34, "ease": 0.33, "potential": 0.33},
}

# Sub-weights inside the ease-of-unlocking index. The UI shows these on a 0-10
# scale; only the ratios matter, not the absolute numbers.
EASE_WEIGHTS = {
    "f_consular": 0.19,      # can you physically lodge an application
    "f_visa_success": 0.14,  # and does that consulate actually say yes
    "f_language": 0.15,      # can you work in the destination's language
    "f_agreement": 0.14,     # is there a legal channel already pointed at you
    "f_cultural": 0.15,      # religion and Hofstede, evenly balanced
    "f_connectivity": 0.09,  # can you get there
    "f_admin": 0.09,         # can the origin issue you documents
    "f_proximity": 0.05,
}

# Inside f_cultural. The concept note specifies Hofstede; religion is our own
# addition. They are weighted evenly, and where Hofstede is unavailable the
# religion side carries the component alone rather than the corridor being
# penalised for a gap in Hofstede's country coverage.
CULTURAL_MIX = {"religion": 0.5, "hofstede": 0.5}

POTENTIAL_WEIGHTS = {
    "p_income_gain": 0.40,
    "p_skills_supply": 0.20,   # can the origin actually produce these workers
    "p_labour_supply": 0.20,   # and how many idle working-age people are there
    "p_dest_demand": 0.20,
}


def pct(s: pd.Series, invert: bool = False) -> pd.Series:
    """Percentile rank on 0-100. NaNs land at the median so they neither reward
    nor punish a corridor for missing data."""
    v = -s if invert else s
    r = v.rank(pct=True, na_option="keep") * 100
    return r.fillna(50.0)


def main() -> None:
    step("loading corridor panel and gravity predictions")
    C = load("corridors_base", INTERIM)
    G = load("gravity_predictions", INTERIM)
    df = C.merge(G, on=["orig", "dest"], how="left")

    # ------------------------------------------------------ underutilisation --
    step("underutilisation")
    # gap_log < 0 means fewer permits than the model expects. Invert so that a
    # bigger shortfall scores higher.
    df["u_gap"] = pct(df["gap_log"], invert=True)
    df["u_gap_absolute"] = pct(df["gap_log_absolute"], invert=True)
    df["u_gap_structural"] = pct(df["gap_log_structural"], invert=True)
    # South Sudan sends no recorded employment migrants to any EU state, so the
    # allocation model cannot speak to it. Say so instead of scoring it.
    df.loc[df.origin_in_allocation_fit == 0, ["u_gap", "u_gap_structural"]] = np.nan
    df["u_gap"] = df["u_gap"].fillna(50.0)

    # ------------------------------------------------------------- potential --
    step("potential")
    # Income gain, on MEDIAN income rather than GNI per capita. GNI pc is a mean
    # over the whole economy and is dragged upward by the top of the
    # distribution, so it overstates what a typical mover would actually earn on
    # either side. PIP medians are much closer to the person who would move.
    #
    # The headline is the log of the ABSOLUTE annual gap - what a mover banks in
    # a year - which is what the AJF framing asks for. The ratio is kept
    # alongside it because a 10x gain on a small base and a 10x gain on a large
    # one are very different propositions.
    df["median_income_gap"] = df.get("median_income_gap", pd.Series(np.nan, index=df.index))
    df["log_income_gap"] = np.log(df["median_income_gap"].clip(lower=1))

    # NOT a percentile rank, unlike every other component here, and that is
    # deliberate. Percentile ranking is a monotone transform, so ranking the log
    # gives byte-identical results to ranking the raw gap - the log would have
    # been purely decorative. Min-max scaling the log keeps the compression the
    # log is there to provide: the step from a $2k gap to $12k counts for far
    # more than the step from $22k to $32k.
    lg = df["log_income_gap"]
    lo, hi = lg.min(), lg.max()
    df["p_income_gain"] = (100 * (lg - lo) / (hi - lo)).fillna(50.0) if hi > lo \
        else pd.Series(50.0, index=df.index)

    df["income_multiple"] = (df["d_gni_pc_ppp"] / df["o_gni_pc_ppp"]).replace(
        [np.inf, -np.inf], np.nan)

    # Quantity: how many working-age people are available and idle.
    df["labour_supply"] = (df["o_working_age_pop"].fillna(0)
                           * df["o_youth_unemployment"].fillna(0) / 100.0)
    df["p_labour_supply"] = pct(np.log1p(df["labour_supply"]))

    # Quality: whether the origin trains anyone. Until now the panel carried
    # literacy, tertiary enrolment and secondary attainment and used none of
    # them in any score, so a country with twenty million idle young people and
    # no training system ranked identically to one with a large college sector.
    skills = {
        "o_literacy": 1.0,
        "o_tertiary_enrolment": 1.0,
        "o_upper_secondary_attain": 0.8,
        "o_vocational_enrolment_share": 1.0,     # UIS TVET
        "o_vocational_share_secondary": 0.6,     # World Bank fallback
        "o_grad_stem": 0.8,
        "o_bachelor_attainment": 0.6,
    }
    have = {k: w for k, w in skills.items() if k in df.columns}
    num = sum(pct(df[k]) * w for k, w in have.items())
    df["p_skills_supply"] = num / sum(have.values()) if have else 50.0
    df["skills_inputs_present"] = sum(df[k].notna().astype(int) for k in have)

    demand = (pct(df.get("d_es_vacancy_rate", pd.Series(np.nan, index=df.index)))
              + pct(df.get("d_es_old_age_dependency", pd.Series(np.nan, index=df.index)))
              + pct(df.get("d_es_unemployment", pd.Series(np.nan, index=df.index)),
                    invert=True)) / 3.0
    df["p_dest_demand"] = demand

    # ------------------------------------------------------------------ ease --
    step("ease of unlocking")
    # Consular access, tiered. The biometric appointment is the binding step, so a
    # mission in the origin country is worth most - but "no embassy" is not one
    # state. An applicant in Lesotho reaches the South African capital in 406 km;
    # one in Chad may face several thousand. Partial credit reflects that, at a
    # deliberately reduced weight, because crossing a border still means a second
    # visa, a flight and time off work.
    near = df["nearest_embassy_km"]
    # 35 points at 800 km or less, decaying linearly to 5 at 4,000 km and beyond.
    # The floor at 800 km keeps the island states (Cabo Verde is 652 km from
    # Dakar) from being punished for having no land border.
    reach = np.clip(35.0 - 30.0 * (near - 800.0) / 3200.0, 5.0, 35.0)
    reach = np.where(near.isna(), 0.0, reach)

    # A visa operation recorded in the DG HOME consulate statistics is the
    # strongest evidence there is that applications can actually be lodged in
    # country - it means the post processed real applications last year.
    # Wikidata is the fallback for Ireland and Cyprus, which are outside the
    # Schengen visa statistics entirely.
    in_country = (df["visa_consulate_present"] == 1) | (df["dest_embassy_in_orig"] == 1)

    df["f_consular"] = np.select(
        [in_country,
         df["dest_missions_in_orig"] > 0,
         df["neighbour_embassy"] == 1],
        [100.0, 65.0, np.maximum(40.0, reach)],
        default=reach)

    df["consular_tier"] = np.select(
        [df["visa_consulate_present"] == 1,
         df["dest_embassy_in_orig"] == 1,
         df["dest_missions_in_orig"] > 0,
         df["neighbour_embassy"] == 1],
        ["Visa consulate in country", "Embassy in country",
         "Consular post in country", "Embassy in a neighbour"],
        default=np.where(near.isna(), "No mission reachable in Africa",
                         "Embassy elsewhere in region"))

    # Schengen short-stay refusal rate. A proxy - it is not work-visa data, and
    # no comparable work-visa series is published - but it is the most direct
    # public measure of whether a consulate actually says yes. Corridors with no
    # visa operation at all get the null-to-median treatment rather than a zero,
    # because "no applications" is not the same as "everyone refused"; the
    # absence itself is already penalised through f_consular.
    df["f_visa_success"] = pct(df["visa_refusal_rate"], invert=True)

    # Language: does the destination's working language exist at the origin at all
    bridge_overlap = [
        len(set(str(a).split(";")) & set(str(b).split(";")) - {""}) > 0
        for a, b in zip(df["origin_bridge_languages"], df["dest_official_languages"])]
    df["f_language"] = np.select(
        [df["shared_official_language"] == 1,
         df["dest_language_present_at_origin"] == 1,
         np.array(bridge_overlap)],
        [100.0, 70.0, 40.0], default=10.0)

    df["f_agreement"] = np.select(
        [df["has_binding"] == 1, df["n_agreements"] > 0, df["n_eu_instruments"] > 0],
        [100.0, 75.0, 50.0], default=0.0)

    df["f_connectivity"] = pct(np.log1p(df["routes"]))
    df["f_proximity"] = pct(np.log(df["dist"].clip(lower=1)), invert=True)
    # Can the origin state actually issue documents and can applicants transact
    df["f_admin"] = (pct(df["o_wgi_govt_effectiveness"])
                     + pct(df["o_internet_users"])) / 2.0

    # Cultural proximity, in two halves.
    #
    # Religion: coarse eight-group composition vectors compared by cosine
    # similarity, plus a Catholic-share term (the meaningful tie on Lusophone and
    # Francophone corridors) and a shared-majority indicator.
    df["religion_proximity"] = (pct(df["religion_similarity"]) * 0.5
                                + pct(df["religion_catholic_similarity"]) * 0.2
                                + pct(df["same_majority_religion"].astype(float)) * 0.3)

    # Hofstede: the measure the concept note actually specifies. Kogut-Singh
    # distance inverted, so a short cultural distance scores high.
    if "hofstede_distance" in df.columns:
        df["hofstede_proximity"] = pct(df["hofstede_distance"], invert=True)
        df.loc[df.hofstede_distance.isna(), "hofstede_proximity"] = np.nan
    else:
        df["hofstede_proximity"] = np.nan

    # Where Hofstede has no basis for a country - Middle Africa and most of
    # Southern Africa - religion carries the component rather than the corridor
    # being marked down for a hole in someone else's survey coverage.
    w_r, w_h = CULTURAL_MIX["religion"], CULTURAL_MIX["hofstede"]
    has_h = df["hofstede_proximity"].notna()
    df["f_cultural"] = np.where(
        has_h,
        df["religion_proximity"] * w_r + df["hofstede_proximity"].fillna(0) * w_h,
        df["religion_proximity"])
    df["cultural_basis"] = np.where(has_h, "religion + Hofstede", "religion only")

    # ------------------------------------------------------------ composites --
    step("composites")
    df["ease_score"] = sum(df[k] * w for k, w in EASE_WEIGHTS.items())
    df["potential_score"] = sum(df[k] * w for k, w in POTENTIAL_WEIGHTS.items())
    df["underutilisation_score"] = df["u_gap"]

    for name, w in PRESETS.items():
        df[f"score_{name}"] = (w["underutilisation"] * df["underutilisation_score"]
                               + w["ease"] * df["ease_score"]
                               + w["potential"] * df["potential_score"])

    # A hard zero on any dimension should collapse the score, which an arithmetic
    # mean hides. Offered alongside, not instead.
    df["score_cgd_geometric"] = (
        df["underutilisation_score"].clip(lower=1) ** PRESETS["cgd"]["underutilisation"]
        * df["ease_score"].clip(lower=1) ** PRESETS["cgd"]["ease"]
        * df["potential_score"].clip(lower=1) ** PRESETS["cgd"]["potential"])

    # --------------------------------------------------- low-hanging fruit ----
    # The brief's own test: the legal channel exists, this origin is already
    # eligible, and almost nobody uses it.
    df["low_hanging_fruit"] = (
        (df["gap_log"] < -0.7)              # materially below prediction
        & (df["ease_score"] > df["ease_score"].median())
        & (df["has_any_agreement"] == 1)
    ).astype(int)

    # ------------------------------------------------------------ WHO flag ---
    # Reported, never scored. Ranking a corridor down because its origin has a
    # fragile health workforce would be the wrong instrument - the list is about
    # how you recruit, not whether the corridor has potential - but a tool that
    # surfaces health corridors without showing it would be worse.
    step("WHO safeguards flag (surfaced, never scored)")
    df["who_safeguard_origin"] = df.get(
        "who_safeguard_origin", pd.Series(0, index=df.index)).fillna(0).astype(int)

    save(df, "corridors_scored", INTERIM)

    # -------------------------------------------------------------- report ----
    nm = dict(zip(df.orig, df.orig_name))
    nd = dict(zip(df.dest, df.dest_name))
    top = df.nlargest(15, "score_cgd")
    print(f"\n    Top 15 corridors, CGD weighting "
          f"({int(PRESETS['cgd']['underutilisation'] * 100)}% underutilisation / "
          f"{int(PRESETS['cgd']['ease'] * 100)}% ease / "
          f"{int(PRESETS['cgd']['potential'] * 100)}% potential)")
    print(f"    {'corridor':<34}{'score':>6}{'under':>7}{'ease':>6}{'poten':>7}"
          f"{'actual':>8}{'pred':>7}{'emb':>5}{'agr':>5}")
    for r in top.itertuples():
        pair = f"{nm.get(r.orig, r.orig)} -> {nd.get(r.dest, r.dest)}"
        print(f"    {pair:<34}{r.score_cgd:>6.0f}{r.underutilisation_score:>7.0f}"
              f"{r.ease_score:>6.0f}{r.potential_score:>7.0f}"
              f"{r.actual_mean3:>8.0f}{r.predicted:>7.0f}"
              f"{'Y' if r.dest_embassy_in_orig else '-':>5}"
              f"{'Y' if r.has_any_agreement else '-':>5}")
    print(f"\n    low-hanging-fruit corridors (below prediction, easy, "
          f"channel already exists): {int(df.low_hanging_fruit.sum())}")
    print(f"    median annual income gap: ${df.median_income_gap.median():,.0f} "
          f"(range ${df.median_income_gap.min():,.0f}-"
          f"${df.median_income_gap.max():,.0f})")
    have = df.visa_refusal_rate.notna()
    print(f"    Schengen refusal rate available for {int(have.sum()):,} corridors; "
          f"median {df.loc[have, 'visa_refusal_rate'].median():.1%}")
    print(f"    origins on the WHO safeguards list: "
          f"{df[df.who_safeguard_origin == 1].orig.nunique()} of {df.orig.nunique()} "
          f"(flagged only, never scored)")


if __name__ == "__main__":
    main()
