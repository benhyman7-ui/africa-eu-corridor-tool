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
    "cgd": {"underutilisation": 0.40, "access": 0.30, "ease": 0.15, "potential": 0.15},
    "ajf": {"underutilisation": 0.20, "access": 0.25, "ease": 0.15, "potential": 0.40},
    "balanced": {"underutilisation": 0.25, "access": 0.25, "ease": 0.25, "potential": 0.25},
}

# ACCESS is separated out from ease because it answers a different question:
# not "how hard is this" but "can this legally and physically happen at all".
# Buried among eleven ease components, a consular score of 24 was diluted by
# ten others and Slovenia scored 65 overall while having almost no consular
# presence and no legal channel pointed at Africa. These four combine
# HARMONICALLY, so the weakest one dominates - which is how gates actually work.
# Only the two genuine gates. The first attempt also put legal channel and
# pathway permissiveness in here, and access collapsed to about 8 for every
# destination - because f_agreement is zero on 1,241 of 1,458 corridors and a
# harmonic mean is dominated by its smallest term. A dimension that reads 8
# everywhere carries no information. An absent bilateral agreement is a negative
# signal, not a gate: most labour migration runs on ordinary national permits.
# Agreement and pathway quality therefore sit in ease, where they belong.
ACCESS_WEIGHTS = {
    "f_consular": 0.50,      # can an application physically be lodged
    "f_visa_success": 0.30,  # and does that consulate say yes
    "f_pathways": 0.20,      # and is there a route an African applicant can use
}

# Sub-weights inside the ease-of-unlocking index. The UI shows these on a 0-10
# scale; only the ratios matter, not the absolute numbers.
# EASE is what remains once access is carved out: real frictions, but ones a
# determined applicant or a well-designed programme can work around.
EASE_WEIGHTS = {
    "f_language": 0.25,           # can you work in the destination's language
    "f_agreement": 0.22,          # is a legal channel already pointed at you
    "f_salary_threshold": 0.17,   # is the wage floor reachable
    "f_cultural": 0.12,           # religion and Hofstede, evenly balanced
    "f_recognition": 0.09,        # coarse: language requirement only, see note
    "f_processing": 0.08,         # how long a decision takes
    "f_connectivity": 0.05,       # can you get there
    "f_admin": 0.02,              # can the origin issue you documents
}

# f_proximity is deliberately NOT in the weights above. Great-circle distance is
# already a regressor in the PPML gravity model, so it is priced into the
# underutilisation benchmark; scoring it a second time in ease double-counted
# it. It carried a 0.01 weight - about 0.15% of the headline score - so it was
# never actually moving rankings, but it rendered at full height in the score
# decomposition next to factors 100x its weight, which read as far more
# important than it was. The value is still computed and still shown in the
# corridor detail as context, just not scored.
UNSCORED_CONTEXT = ("f_proximity",)

# Inside f_cultural. The concept note specifies Hofstede; religion is our own
# addition. They are weighted evenly, and where Hofstede is unavailable the
# religion side carries the component alone rather than the corridor being
# penalised for a gap in Hofstede's country coverage.
CULTURAL_MIX = {"religion": 0.5, "hofstede": 0.5}

POTENTIAL_WEIGHTS = {
    "p_income_gain": 0.35,
    "p_skills_supply": 0.18,      # can the origin actually produce these workers
    "p_labour_supply": 0.17,      # and how many idle working-age people are there
    "p_dest_demand": 0.15,
    "p_diaspora_growth": 0.15,    # is the corridor already opening
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
    # Winsorised at the 2nd/98th percentile before scaling. Raw min-max let two
    # outliers set both endpoints - Seychelles, a rich origin, pinned the floor
    # and Luxembourg the ceiling - which squeezed the middle half of all
    # corridors into 18 of the 100 points. At 40% of Potential a flat factor
    # cannot move a ranking. Clipping the tails doubles the spread and costs
    # only the ordering WITHIN the extreme 2% tails, which is not information
    # anyone should be acting on anyway.
    lg = df["log_income_gap"]
    lo, hi = lg.quantile(0.02), lg.quantile(0.98)
    df["p_income_gain"] = (100 * (lg.clip(lo, hi) - lo) / (hi - lo)) if hi > lo \
        else pd.Series(np.nan, index=df.index)

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

    # Diaspora momentum. Corridors already growing are the ones where employers,
    # recruiters and informal networks are in place, which is a different signal
    # from a large static stock (that is already a gravity regressor).
    #
    # Left NaN, not 50, wherever the base stock was too small to give a
    # meaningful rate - that is 540 corridors with no measurable diaspora, and
    # asserting average momentum for them would be an invention. wmean() below
    # renormalises around whatever is actually present.
    cagr = df.get("diaspora_cagr", pd.Series(np.nan, index=df.index))
    df["p_diaspora_growth"] = pct(cagr).where(cagr.notna())

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
    vfs = df.get("vfs_present", pd.Series(0, index=df.index)) == 1

    # An outsourced visa centre is a real place in the applicant's own country
    # that takes biometrics and forwards the file. Slightly below a mission,
    # because the decision still happens elsewhere and fees are higher, but far
    # above having to cross a border.
    df["f_consular"] = np.select(
        [in_country,
         vfs,
         df["dest_missions_in_orig"] > 0,
         df["neighbour_embassy"] == 1],
        [100.0, 82.0, 65.0, np.maximum(40.0, reach)],
        default=reach)

    df["consular_tier"] = np.select(
        [df["visa_consulate_present"] == 1,
         df["dest_embassy_in_orig"] == 1,
         vfs,
         df["dest_missions_in_orig"] > 0,
         df["neighbour_embassy"] == 1],
        ["Visa consulate in country", "Embassy in country",
         "Outsourced visa centre in country", "Consular post in country",
         "Embassy in a neighbour"],
        default=np.where(near.isna(), "No mission reachable in Africa",
                         "Embassy elsewhere in region"))

    # Schengen short-stay refusal rate. A proxy - it is not work-visa data, and
    # no comparable work-visa series is published - but it is the most direct
    # public measure of whether a consulate actually says yes. Corridors with no
    # visa operation at all get the null-to-median treatment rather than a zero,
    # because "no applications" is not the same as "everyone refused"; the
    # absence itself is already penalised through f_consular.
    # NaN, not 50, where the destination runs no visa operation in that country.
    # Schengen data covers only 19% of corridors, and inserting a fabricated 50
    # into a HARMONIC gate for the other 81% pulled every corridor towards the
    # middle and cost real signal. A missing term is now dropped and the
    # remaining weights renormalise.
    df["f_visa_success"] = pct(df["visa_refusal_rate"], invert=True)
    df.loc[df.visa_refusal_rate.isna(), "f_visa_success"] = np.nan

    # Language: does the destination's working language exist at the origin at all
    bridge_overlap = [
        len(set(str(a).split(";")) & set(str(b).split(";")) - {""}) > 0
        for a, b in zip(df["origin_bridge_languages"], df["dest_official_languages"])]
    tier = np.select(
        [df["shared_official_language"] == 1,
         df["dest_language_present_at_origin"] == 1,
         np.array(bridge_overlap)],
        [100.0, 70.0, 40.0], default=10.0)

    # Relatedness, for corridors that share no language outright. The tiers
    # above are set overlap, so they scored a Lusophone origin looking at Spain
    # exactly the same as a Swahili-speaking one - both at the floor - when a
    # Portuguese speaker plainly starts closer to Spanish. This floors the
    # corridor at 10 and adds up to 45 points for how close the nearest pair of
    # languages actually is, then takes whichever of the two readings is higher
    # so a shared language is never scored below a merely related one.
    prox = df.get("language_proximity", pd.Series(0.0, index=df.index)).fillna(0.0)
    df["f_language"] = np.maximum(tier, 10.0 + 45.0 * prox)

    df["f_agreement"] = np.select(
        [df["has_binding"] == 1, df["n_agreements"] > 0, df["n_eu_instruments"] > 0],
        [100.0, 75.0, 50.0], default=0.0)

    # Air connectivity, from Eurostat airport-pair passenger counts rather than
    # the OpenFlights route list. OpenFlights is a 2014 snapshot of SCHEDULED
    # routes and returned zero for 91% of corridors, Egypt-Poland among them.
    # Eurostat counts people actually carried and includes non-scheduled
    # traffic, so the seasonal tourist charters that make up much of the
    # Europe-Africa network are in it.
    #
    # OpenFlights survives only as a fallback: Czechia does not disseminate the
    # airport-pair dataset at all, so its corridors would otherwise read as
    # unconnected. A corridor with neither is scored low but not zero - it means
    # no direct service is on record, which usually means a one-stop connection.
    pax = df.get("air_passengers", pd.Series(np.nan, index=df.index))
    has_pax = pax.notna() & (pax > 0)
    routes_only = ~has_pax & (df["routes"].fillna(0) > 0)
    rank = pct(np.log1p(pax.where(has_pax)))
    df["f_connectivity"] = np.select(
        [has_pax, routes_only],
        [50.0 + 0.5 * rank, 45.0],
        default=10.0)
    df["f_proximity"] = pct(np.log(df["dist"].clip(lower=1)), invert=True)

    # How long a decision takes. Built only from things that are written down:
    #   50  the 90-day deadline in the recast Single Permit Directive
    #       (EU) 2024/1233 Art 5, which binds 25 of the 27 destinations
    #  +25  the corridor qualifies for that Directive's 45-day track, given to
    #       applicants selected through an EU Talent Partnership - the only
    #       part of processing time that varies by corridor rather than
    #       destination
    #  +20  the destination runs a published statutory fast-track scheme
    #       (Germany's accelerated procedure, the Dutch recognised sponsor,
    #       Denmark's Fast-track Scheme, Ireland's Trusted Partner)
    #
    # Denmark and Ireland are outside the Directive on Protocols 22 and 21, so
    # no EU deadline binds them; both publish their own decision targets, so
    # they keep the 50 baseline rather than being scored as unknown.
    #
    # This is mostly a destination constant, which is why it carries only 0.08
    # of ease. Statutory deadlines are near-uniform by design; what actually
    # varies is who gets a fast lane.
    df["f_processing"] = (50.0
                          + 25.0 * df.get("pt_talent_partnership", 0)
                          + 20.0 * df.get("pt_has_fast_track", 0)).clip(0, 100)
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

    # ---- salary threshold ---------------------------------------------------
    # A wage floor is an absolute gate, and it bites hardest exactly where the
    # volume is: a EUR 48k Blue Card threshold is irrelevant to a surgeon and
    # fatal to a care worker. Scored against the destination's own median income
    # so a high threshold in a high-wage country is not double-counted.
    thr = df.get("req_blue_card_salary_eur")
    gen = df.get("req_general_permit_salary_eur")
    if thr is not None:
        eff = thr.fillna(gen) if gen is not None else thr
        # ratio of the threshold to what a median worker there actually earns
        df["salary_threshold_ratio"] = eff / df["d_inc_median_annual_ppp"]
        df["f_salary_threshold"] = pct(df["salary_threshold_ratio"], invert=True)
        # Unknown is unknown. Only 3 of 27 destinations publish a Blue Card
        # figure and 5 a general-permit figure, so scoring the rest at 50
        # made this component dead weight on 70% of corridors AND created a
        # documentation bias: the destinations that publish a number are the
        # ones that get marked down for it.
        df.loc[eff.isna(), "f_salary_threshold"] = np.nan
    else:
        df["f_salary_threshold"] = 50.0
        df["salary_threshold_ratio"] = np.nan

    # ---- recognition of qualifications --------------------------------------
    # Curated regime notes, scored coarsely: whether the destination runs a
    # recognition process that a third-country applicant can realistically
    # complete, and whether language sits on top of it.
    reg = df.get("req_recognition_regime", pd.Series("", index=df.index)).fillna("")
    lang_req = df.get("req_language_requirement", pd.Series("", index=df.index)).fillna("")
    # The "slow" adjective was mine, written unevenly across a curated file, and
    # it fired mostly on the big destinations because those are the ones I wrote
    # most about. Scoring on my own prose density is documentation bias, not
    # measurement, so only the stated language requirement survives - that is a
    # fact in the source rather than a judgement in the note.
    heavy_lang = lang_req.str.contains("B1|B2|required", case=False)
    # Same documentation bias, and worse: this scored -0.29 against actual
    # flows, because the destinations with richly documented recognition
    # regimes are the large ones that actually move people. Penalising a
    # destination for HAVING a described process is backwards. Countries with
    # nothing on file are now NaN rather than being handed a free pass.
    df["f_recognition"] = 100.0 - 30.0 * heavy_lang.astype(float)
    df.loc[reg.eq(""), "f_recognition"] = np.nan
    df["f_recognition"] = df["f_recognition"].clip(0, 100)

    # ---- ICMPD pathway permissiveness ---------------------------------------
    if "icmpd_promise_mean" in df.columns:
        base = pct(0.6 * df["icmpd_promise_mean"].fillna(50)
                   + 0.4 * df["icmpd_promise_best"].fillna(50))
        # Africa-orientation penalty. Where a destination's mapped routes are
        # substantially reserved for non-African nationalities - Western Balkans
        # directives, Serbia and Bosnia agreements, Moldova and Georgia seasonal
        # schemes - that is evidence the country channels labour migration away
        # from Africa, and it applies to EVERY corridor from that destination.
        # Scaled by breadth, so a bloc-wide general work agreement costs more
        # than one narrow occupational route.
        # NOT named `load`: that shadows the imported load() for the whole of
        # main(), which breaks the corridors_base read at the top of the function.
        restr = df.get("icmpd_restriction_load", pd.Series(0.0, index=df.index)).fillna(0)
        rmax = max(restr.max(), 1e-9)
        df["africa_orientation_penalty"] = 0.40 * (restr / rmax)   # up to -40%
        df["f_pathways"] = base * (1 - df["africa_orientation_penalty"])
        df["f_pathways_unpenalised"] = base
    else:
        df["f_pathways"] = 50.0
        df["africa_orientation_penalty"] = 0.0

    # ---- students as a feeder ----------------------------------------------
    if "student_permits" in df.columns:
        df["p_student_feeder"] = pct(np.log1p(df["student_permits"]))

    # ------------------------------------------------------------ composites --
    step("composites")
    # Harmonic, not arithmetic: a gate is only as open as its narrowest point.
    def wmean(weights, harmonic=False, floor=5.0):
        """Weighted mean that renormalises around whatever is actually present."""
        num = pd.Series(0.0, index=df.index)
        den = pd.Series(0.0, index=df.index)
        for k, w in weights.items():
            v = df[k]
            ok = v.notna()
            num = num + np.where(ok, w / v.clip(lower=floor) if harmonic else v * w, 0.0)
            den = den + np.where(ok, w, 0.0)
        den = den.replace(0, np.nan)
        out = den / num if harmonic else num / den
        return out.fillna(50.0)

    df["access_score"] = wmean(ACCESS_WEIGHTS, harmonic=True)
    df["ease_score"] = wmean(EASE_WEIGHTS)
    # wmean, not a plain weighted sum: p_diaspora_growth is NaN wherever the
    # base stock was too small to give a meaningful rate, and a plain sum
    # propagates that NaN straight through to the headline score.
    df["potential_score"] = wmean(POTENTIAL_WEIGHTS)
    df["underutilisation_score"] = df["u_gap"]

    for name, w in PRESETS.items():
        df[f"score_{name}"] = (w["underutilisation"] * df["underutilisation_score"]
                               + w["access"] * df["access_score"]
                               + w["ease"] * df["ease_score"]
                               + w["potential"] * df["potential_score"])

    # A hard zero on any dimension should collapse the score, which an arithmetic
    # mean hides. Offered alongside, not instead.
    w = PRESETS["cgd"]
    df["score_cgd_geometric"] = (
        df["underutilisation_score"].clip(lower=1) ** w["underutilisation"]
        * df["access_score"].clip(lower=1) ** w["access"]
        * df["ease_score"].clip(lower=1) ** w["ease"]
        * df["potential_score"].clip(lower=1) ** w["potential"])

    # --------------------------------------------------- low-hanging fruit ----
    # The brief's own test: the legal channel exists, this origin is already
    # eligible, and almost nobody uses it.
    # Access, not ease. Under the old test 34 of 74 flagged corridors had an
    # access score under 30 - "easy" corridors with nowhere to lodge an
    # application. A corridor cannot be low-hanging if it cannot be reached.
    df["low_hanging_fruit"] = (
        (df["gap_log"] < -0.7)              # materially below prediction
        & (df["access_score"] > df["access_score"].median())
        & (df["ease_score"] > df["ease_score"].median())
        & (df["has_any_agreement"] == 1)
    ).astype(int)

    # ---------------------------------------------------------------- scale ---
    # How many people a corridor could plausibly carry that it does not already,
    # in absolute terms: the model's expected annual flow minus what actually
    # moves. Every other component here is a RATE or a rank, so a corridor
    # running at 2% of expectation scores the same whether that means 3 people
    # or 700. This is the term that separates them.
    step("scale of the unrealised opportunity")
    # Scale is the SIZE of the corridor the model expects, not the shortfall.
    #
    # It used to be (predicted - actual) clipped at zero, which was wrong twice
    # over. It double-counted underutilisation, which already measures the
    # shortfall and is a headline term in its own right. And because the clip
    # sends every over-performing corridor to zero, it actively penalised the
    # big ones: Germany runs above its prediction, so its gap clipped to nothing
    # and it scored 30, while Luxembourg's slight underperformance on a tiny
    # base scored 43. Turning scale weighting on promoted the smallest
    # destinations in the EU, which is the opposite of what it is for.
    #
    # Predicted flow is the model's estimate of how many people the corridor
    # can carry, which is what "how big is this opportunity" means.
    df["unrealised_flow"] = (df["predicted"] - df["actual_mean3"]).clip(lower=0)
    df["scale_score"] = pct(np.log1p(df["predicted"].clip(lower=0)))
    print(f"    unrealised annual flow: total {df.unrealised_flow.sum():,.0f}; "
          f"median {df.unrealised_flow.median():.0f}; "
          f"top corridor {df.unrealised_flow.max():,.0f}")
    big = df.nlargest(5, "unrealised_flow")
    for r in big.itertuples():
        print(f"      {r.orig_name} -> {r.dest_name}: {r.unrealised_flow:,.0f}/yr "
              f"(actual {r.actual_mean3:,.0f}, predicted {r.predicted:,.0f})")

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
    print(f"    {'corridor':<34}{'score':>6}{'under':>7}{'acces':>6}{'ease':>6}"
          f"{'poten':>7}{'actual':>8}{'pred':>7}")
    for r in top.itertuples():
        pair = f"{nm.get(r.orig, r.orig)} -> {nd.get(r.dest, r.dest)}"
        print(f"    {pair:<34}{r.score_cgd:>6.0f}{r.underutilisation_score:>7.0f}"
              f"{r.access_score:>6.0f}{r.ease_score:>6.0f}{r.potential_score:>7.0f}"
              f"{r.actual_mean3:>8.0f}{r.predicted:>7.0f}")
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
