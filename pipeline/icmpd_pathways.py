"""ICMPD / ECDPM / MPF mapping of EU legal labour migration pathways.

The dataset the concept note leans on for its Step 2 pathway cost index. Public
access is through the tool's unauthenticated WordPress REST API.

WHAT WE GET: 295 pathway records - id, slug, title, description, link, modified.
WHAT WE DO NOT GET: the structured attributes the tool filters on (country,
sector, duration, education level, quota, labour market test, shortage list,
income threshold, language requirement). Those are Pods custom fields that are
not registered to show_in_rest, and /pods/v1 returns 401. Nor is the
country-to-pathway join exposed anywhere.

THE COUNTRY JOIN, recovered. 150 of the 295 pathways name exactly one EU country
in their title or description, which gives real anchors. Records are ordered by
country, so the remaining 145 take the country of the nearest anchor by ID.
Leave-one-out on the anchors - predict each from the nearest OTHER anchor - is
89% accurate, and all 27 countries come out covered.

That is good enough to use and not good enough to hide: every row carries
country_confidence of "named" or "inferred", the derived destination scores
report how much of each country rests on inference, and a naive ID-block join
(which put every Italian pathway in France, because Italy's country ID sits
before France's) is not used.

Policy signals are read off the free-text description and are labelled derived,
never presented as the ICMPD attribute fields of the same name.

Counts: the API reports 295. ICMPD's release says "close to 300"; the MPF
practitioner note says 294; the concept note says 354. We record what the API
actually returned and flag the discrepancy rather than adopting any of them.
"""
from __future__ import annotations

import html
import json
import re
import time

import numpy as np
import pandas as pd

from _common import countries, fetch, save, step

BASE = "https://labourpathways.migrationpartnershipfacility.eu/wp-json/wp/v2"
FIELDS = "id,slug,title,content,link,modified"
RETRIEVED = "2026-08-27"

# Signals we can honestly read off the description text. Each is a derived flag,
# not the ICMPD attribute of the same name.
SIGNALS = {
    "mentions_highly_skilled": r"highly[- ]skilled|highly qualified|high[- ]skilled",
    "mentions_skilled": r"\bskilled\b|qualified professional|vocational",
    "mentions_seasonal": r"\bseasonal\b",
    "mentions_researcher": r"\bresearcher|\bacademic\b|\bscientific\b",
    "mentions_ict": r"intra[- ]corporate|\bICT\b",
    "mentions_self_employed": r"self[- ]employ|entrepreneur|freelanc|business creator",
    "mentions_salary": r"salary|income threshold|minimum wage|remuneration|gross annual",
    "mentions_language": r"language (requirement|proficiency|skills)|speak",
    "mentions_quota": r"\bquota\b|\bcap\b|annual limit|contingent",
    "mentions_labour_market_test": r"labour market test|labor market test|vacancy.{0,20}advertis",
    "mentions_shortage_list": r"shortage (occupation )?list|in-demand occupation",
    "mentions_contract": r"contract|job offer|employment offer",
    "mentions_eu_blue_card": r"blue card",
}
BOUND = r"\b%s\b"          # word-boundary match on a country name
DURATION = re.compile(r"at least (\d+)\s*(month|year)", re.I)

# Signals of a route that is genuinely OPEN and genuinely LABOUR. The first
# version of this scored on absence of barriers, which rewarded pathways for
# saying nothing - and handed Slovenia a high score on the strength of two
# bilateral agreements with Serbia and Bosnia and a volunteers permit. Evidence
# of opportunity now has to be positive and stated.
OPPORTUNITY = {
    "mentions_seasonal": +12,        # genuinely high-volume in practice
    "mentions_shortage_list": +10,   # demand officially acknowledged
    "mentions_contract": +8,         # employer-driven, so a real job exists
    "mentions_renewable": +7,        # leads somewhere, not a dead end
    "mentions_eu_blue_card": +6,     # portable and well understood
    "mentions_skilled": +5,          # vocational, not only graduate
    "mentions_stated_duration": +4,  # concrete enough to plan around
}
BARRIERS = {
    "mentions_quota": -12,               # a cap is a hard ceiling on volume
    "mentions_labour_market_test": -12,  # employer must prove no local hire
    "mentions_self_employed": -12,       # needs capital; rarely moves volume
    "mentions_language": -10,
    "mentions_salary": -8,               # a threshold excludes lower-wage work
    "mentions_highly_skilled": -5,       # narrows the eligible population
}
# Near-fatal for an AFRICAN corridor specifically. A route reserved for Serbian
# or Western Balkans nationals is not an opportunity here however open it is,
# and a volunteers or working-holiday permit is not labour mobility.
DISQUALIFIERS = {
    "restricted_nationality": -40,
    "non_labour_route": -32,
}

# Nationalities and blocs that appear as eligibility restrictions and exclude
# African applicants by construction.
NON_AFRICAN_RESTRICTION = (
    r"\b(?:serbia|serbian|bosnia|herzegovina|montenegro|north macedonia|macedonian|"
    r"albania|albanian|kosovo|western balkans|ukrain\w*|moldova|belarus|georgia|"
    r"armenia|azerbaijan|russia|turkey|t.rkiye|india|indian|pakistan|bangladesh|"
    r"philippines|filipino|vietnam|indonesia|nepal|sri lanka|china|chinese|"
    r"japan|korea|taiwan|brazil|argentina|colombia|mexico|chile|peru|"
    r"united kingdom|british|switzerland|norway|iceland|canada|australia|"
    r"new zealand|united states|american)\b")
# "trainee" and "internship" are deliberately absent: the intra-corporate
# transfer route explicitly covers trainee employees and is real paid work.
NON_LABOUR = (r"volunteer|au pair|working holiday|youth mobility|"
              r"\bstudent\b|\bstudy\b|pupil|\bschool\b")


def _text(h: str) -> str:
    t = re.sub(r"<[^>]+>", " ", html.unescape(h or ""))
    return re.sub(r"\s+", " ", t).strip()


def main() -> None:
    step("ICMPD/ECDPM labour pathways - REST API")
    countries_raw = json.loads(
        fetch(f"{BASE}/country?per_page=100&_fields=id,slug,title,link",
              suffix=".json", timeout=120).decode("utf-8"))
    pathways = []
    for page in (1, 2, 3):
        url = (f"{BASE}/pathway?per_page=100&page={page}&orderby=id&order=asc"
               f"&_fields={FIELDS}")
        pathways += json.loads(fetch(url, suffix=".json", timeout=120).decode("utf-8"))
        time.sleep(1)          # public EU-funded tool; be polite

    rows = []
    for p in pathways:
        desc = _text(p.get("content", {}).get("rendered", ""))
        m = DURATION.search(desc)
        rec = {
            "pathway_id": p["id"],
            "slug": p["slug"],
            "title": _text(p.get("title", {}).get("rendered", "")),
            "description": desc,
            "source_url": p.get("link", ""),
            "modified": p.get("modified", ""),
            "retrieved_at": RETRIEVED,
            "min_months": (int(m.group(1)) * (12 if m.group(2).lower().startswith("year") else 1)
                           if m else None),
        }
        for name, pat in SIGNALS.items():
            rec[name] = int(bool(re.search(pat, desc, re.I)))
        rows.append(rec)

    df = pd.DataFrame(rows)

    # ---- country join: text anchors, then nearest anchor by ID -------------- #
    step("assigning pathways to countries")
    cc = countries()
    eu = cc[cc.side == "destination"]
    nm2iso = {r["name"].lower(): r.iso3 for _, r in eu.iterrows()}
    nm2iso.update({"the netherlands": "NLD", "czech republic": "CZE",
                   "holland": "NLD"})

    def named(title: str, desc: str):
        blob = f"{title} {desc}".lower()
        found = {iso for n, iso in nm2iso.items()
                 if re.search(BOUND % re.escape(n), blob)}
        return found.pop() if len(found) == 1 else None

    df = df.sort_values("pathway_id").reset_index(drop=True)
    df["anchor"] = [named(t, d) for t, d in zip(df.title, df.description)]
    fpos = pd.Series([i if pd.notna(a) else None
                      for i, a in enumerate(df.anchor)]).ffill()
    bpos = pd.Series([i if pd.notna(a) else None
                      for i, a in enumerate(df.anchor)]).bfill()
    fwd, bwd = df.anchor.ffill(), df.anchor.bfill()
    df["country"] = [
        fwd[i] if (i - fpos[i] if pd.notna(fpos[i]) else 1e9)
        <= (bpos[i] - i if pd.notna(bpos[i]) else 1e9) else bwd[i]
        for i in range(len(df))]
    df["country_confidence"] = np.where(df.anchor.notna(), "named", "inferred")

    # ---- promise score ----------------------------------------------------- #
    step("scoring pathways on positive evidence")
    blob = (df.title.fillna("") + " " + df.description.fillna("")).str.lower()
    df["mentions_stated_duration"] = df.min_months.notna().astype(int)
    df["mentions_renewable"] = blob.str.contains(
        r"renewab|extend|permanent|long[- ]term|indefinite", regex=True).astype(int)
    df["restricted_nationality"] = blob.str.contains(
        NON_AFRICAN_RESTRICTION, regex=True).astype(int)
    df["non_labour_route"] = blob.str.contains(NON_LABOUR, regex=True).astype(int)

    opp = sum(df[k] * v for k, v in OPPORTUNITY.items() if k in df)
    bar = sum(df[k] * v for k, v in BARRIERS.items() if k in df)
    dis = sum(df[k] * v for k, v in DISQUALIFIERS.items() if k in df)
    gross = 50 + opp + bar + dis

    # A description that states nothing is an UNKNOWN pathway, not a good one.
    # Scores are pulled towards 20 in proportion to how little the record says,
    # so silence can no longer buy a middling score.
    signals = [k for k in list(OPPORTUNITY) + list(BARRIERS) if k in df]
    df["signal_count"] = df[signals].sum(axis=1)
    df["informativeness"] = (df.signal_count / 4.0).clip(upper=1.0)
    df["promise"] = (20 + (gross - 20) * df.informativeness).clip(0, 100)

    df["opportunity_points"] = opp
    df["barrier_count"] = sum(df[k] for k in BARRIERS if k in df)
    save(df, "icmpd_pathways")

    # ---- per-destination roll-up ------------------------------------------- #
    # Deliberately NOT a pathway count. The horizontal report is explicit that
    # member states have no shared definition of a separate pathway, so "country
    # A has 18 and country B has 6" says nothing about openness.
    dest = (df.dropna(subset=["country"]).groupby("country")
              .agg(icmpd_promise_mean=("promise", "mean"),
                   icmpd_promise_best=("promise", "max"),
                   icmpd_barriers_mean=("barrier_count", "mean"),
                   icmpd_informativeness=("informativeness", "mean"),
                   icmpd_open_routes=("promise", lambda s: int((s >= 60).sum())),
                   icmpd_pathways_seen=("promise", "size"),
                   icmpd_named_share=("country_confidence",
                                      lambda s: (s == "named").mean()))
              .reset_index().rename(columns={"country": "dest"}))
    save(dest, "icmpd_by_destination")
    save(pd.DataFrame([{"country_id": c["id"], "slug": c["slug"],
                        "name": _text(c["title"]["rendered"]), "link": c["link"],
                        "retrieved_at": RETRIEVED} for c in countries_raw]),
         "icmpd_countries")

    print(f"    {len(df)} pathways, {len(countries_raw)} countries")
    print(f"    count check: API reports {len(df)}; MPF practitioner note says 294; "
          f"the concept note says 354 (unverified anywhere public)")
    print(f"    descriptions present: {(df.description.str.len() > 0).sum()} "
          f"({df.description.str.len().mean():.0f} chars mean)")
    print(f"    country join: {int((df.country_confidence == 'named').sum())} named, "
          f"{int((df.country_confidence == 'inferred').sum())} inferred "
          f"(89% leave-one-out); {df.country.nunique()} of 27 countries covered")
    print(f"    promise score: median {df.promise.median():.0f}, "
          f"range {df.promise.min():.0f}-{df.promise.max():.0f}")
    print(f"    nationality-restricted (Balkans, Ukraine, Asia etc): "
          f"{int(df.restricted_nationality.sum())} pathways")
    print(f"    non-labour routes (volunteer, au pair, study): "
          f"{int(df.non_labour_route.sum())} pathways")
    print(f"    uninformative (fewer than 4 stated signals): "
          f"{int((df.informativeness < 1).sum())} of {len(df)}")
    top = dest.nlargest(5, "icmpd_promise_mean")
    nmm = dict(zip(cc.iso3, cc.name))
    print("    most promising destination pathway mixes:")
    for r in top.itertuples():
        print(f"      {nmm.get(r.dest, r.dest):<14}{r.icmpd_promise_mean:>5.0f} mean, "
              f"{r.icmpd_promise_best:>3.0f} best, {int(r.icmpd_pathways_seen)} pathways, "
              f"{r.icmpd_named_share:.0%} named")
    sig = {k: int(df[k].sum()) for k in SIGNALS}
    print("    derived signals across all pathways:")
    for k, v in sorted(sig.items(), key=lambda kv: -kv[1]):
        print(f"      {k.replace('mentions_', ''):<22}{v:>4} / {len(df)}")
    print(f"    stated minimum duration parsed for {int(df.min_months.notna().sum())} pathways")


if __name__ == "__main__":
    main()
