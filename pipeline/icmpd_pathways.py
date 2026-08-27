"""ICMPD / ECDPM / MPF mapping of EU legal labour migration pathways.

The dataset the concept note leans on for its Step 2 pathway cost index. Public
access is through the tool's unauthenticated WordPress REST API.

WHAT WE GET: 295 pathway records - id, slug, title, description, link, modified.
WHAT WE DO NOT GET: the structured attributes the tool filters on (country,
sector, duration, education level, quota, labour market test, shortage list,
income threshold, language requirement). Those are Pods custom fields that are
not registered to show_in_rest, and /pods/v1 returns 401. Nor is the
country-to-pathway join exposed anywhere.

We tested the "records are grouped by country in ID order" hypothesis in both
directions against language fingerprints in the slugs (passeport-talent for
France, decreto/flussi for Italy, and so on). Following-order scores 19% and
leaves Italy and Belgium with zero pathways; preceding-order scores 0%. Neither
is usable, so NO COUNTRY IS ASSIGNED. Inventing one would put a fabricated
policy index into the ease score, which is worse than leaving the gap visible.

What is extracted instead: policy signals stated in the free-text description.
These are labelled as derived, never presented as the ICMPD attribute fields.

Counts: the API reports 295. ICMPD's release says "close to 300"; the MPF
practitioner note says 294; the concept note says 354. We record what the API
actually returned and flag the discrepancy rather than adopting any of them.
"""
from __future__ import annotations

import html
import json
import re
import time

import pandas as pd

from _common import fetch, save, step

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
DURATION = re.compile(r"at least (\d+)\s*(month|year)", re.I)


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
            # Not recoverable from the public API - see module docstring.
            "country": None,
            "country_confidence": "unavailable",
        }
        for name, pat in SIGNALS.items():
            rec[name] = int(bool(re.search(pat, desc, re.I)))
        rows.append(rec)

    df = pd.DataFrame(rows)
    save(df, "icmpd_pathways")
    save(pd.DataFrame([{"country_id": c["id"], "slug": c["slug"],
                        "name": _text(c["title"]["rendered"]), "link": c["link"],
                        "retrieved_at": RETRIEVED} for c in countries_raw]),
         "icmpd_countries")

    print(f"    {len(df)} pathways, {len(countries_raw)} countries")
    print(f"    count check: API reports {len(df)}; MPF practitioner note says 294; "
          f"the concept note says 354 (unverified anywhere public)")
    print(f"    descriptions present: {(df.description.str.len() > 0).sum()} "
          f"({df.description.str.len().mean():.0f} chars mean)")
    print(f"    country join: NOT AVAILABLE from the public API - left null")
    sig = {k: int(df[k].sum()) for k in SIGNALS}
    print("    derived signals across all pathways:")
    for k, v in sorted(sig.items(), key=lambda kv: -kv[1]):
        print(f"      {k.replace('mentions_', ''):<22}{v:>4} / {len(df)}")
    print(f"    stated minimum duration parsed for {int(df.min_months.notna().sum())} pathways")


if __name__ == "__main__":
    main()
