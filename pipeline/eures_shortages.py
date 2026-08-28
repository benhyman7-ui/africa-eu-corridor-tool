"""EURES shortage occupations -> the tool's sector families.

The European Labour Authority asks every national coordination office whether
each of the 436 four-digit ISCO-08 occupations is in shortage. The per-country
answers live only in a PowerBI embed with no documented query interface, so
what is taken here is the published Table 2: the occupations more than half of
reporting countries call a shortage, with how many countries and how severe.

That is EU-wide rather than per country. The per-country dimension already
exists in the tool through the Eurostat job vacancy rate by NACE, which is
quantitative but unofficial; this adds the official designation, which is what
actually opens shortage-list visa routes. The two are complements.

IMPORTANT: absence from this table is NOT evidence of no shortage. The
threshold is "more than half of reporting countries", so ICT - a real and
well-documented shortage area - does not appear, because shortage there is
concentrated rather than widespread. The UI says so.
"""
from __future__ import annotations

import re

import pandas as pd
import pypdf

from _common import RAW, save

REPORT = ("https://www.ela.europa.eu/sites/default/files/2026-06/"
          "labour-shortages-report-ela-2025.pdf")
PDF = RAW / "pdf" / "eures_main.pdf"

# The page index is 0-based and pinned to the 2025 edition. The parse asserts
# its expected row count, so a reissued PDF fails loudly instead of silently
# yielding a short table.
P_WIDESPREAD, N_WIDESPREAD = 28, 24

ANCHOR = re.compile(r"(\d{1,2})\s+(\d{1,3})\s*%\s+(\d{1,3})\s*%")

# ISCO occupation -> the NACE A*10 family the tool already exposes. Assignment
# is by where the occupation is actually employed, not by ISCO skill level:
# electrical engineers sit in industry because that is where they work.
FAMILY = {
    "O-Q": ["medical practitioners", "nursing", "health care assistants",
            "physiotherap", "personal care", "ambulance", "medical assistants",
            "medical secretaries", "dispensing opticians", "community health",
            "health associate", "health inspectors"],
    "F": ["building and related electricians", "plumbers", "carpenters",
          "roofers", "floor layers"],
    "G-I": ["truck and lorry drivers", "bus and tram drivers", "cooks", "chefs"],
    "B-E": ["welders", "sheet metal", "electrical mechanics", "butchers",
            "structural metal", "air conditioning", "motor vehicle mechanics",
            "machinery mechanics", "electrical engineers",
            "machine tool setters"],
}


def family_of(name: str) -> str | None:
    low = name.lower()
    for fam, keys in FAMILY.items():
        if any(k in low for k in keys):
            return fam
    return None


def parse_table(page_text: str, after: str) -> list[tuple[str, int, int, int]]:
    """Occupation names sit between the numeric blocks, wrapped across lines.

    Line-by-line buffering does not work here - the greedy match swallows
    several records at once - so the numeric block is the anchor and the name
    is whatever text precedes it since the previous anchor ended.
    """
    flat = re.sub(r"\s+", " ", page_text.split(after)[-1])
    rows, prev = [], 0
    for m in ANCHOR.finditer(flat):
        name = re.sub(r"^[-\d .%]+", "", flat[prev:m.start()]).strip(" ,")
        if name:
            rows.append((name, int(m.group(1)), int(m.group(2)), int(m.group(3))))
        prev = m.end()
    return rows


def main() -> None:
    if not PDF.exists():
        PDF.parent.mkdir(parents=True, exist_ok=True)
        from _common import fetch
        PDF.write_bytes(fetch(REPORT, suffix=".pdf", timeout=300))

    pages = pypdf.PdfReader(str(PDF)).pages

    wide = parse_table(pages[P_WIDESPREAD].extract_text(),
                       "high-severity \nshortages \nsince 2024")
    assert len(wide) == N_WIDESPREAD, \
        f"widespread table: got {len(wide)} rows, expected {N_WIDESPREAD}"

    df = pd.DataFrame(wide, columns=["occupation", "n_countries",
                                     "pct_high", "pct_high_med"])
    df["sector"] = df.occupation.map(family_of)
    unmapped = df[df.sector.isna()]
    if len(unmapped):
        print(f"    unmapped occupations: {list(unmapped.occupation)}")

    save(df, "eures_shortage_occupations")

    # Per-family rollup. Breadth is the share of the 28 reporting countries that
    # call it a shortage; severity is the share of those reports rated high.
    fam = (df[df.sector.notna()]
           .groupby("sector")
           .agg(n_occupations=("occupation", "size"),
                mean_countries=("n_countries", "mean"),
                mean_pct_high=("pct_high", "mean"))
           .reset_index())
    fam["breadth"] = fam.mean_countries / 28.0
    fam["shortage_intensity"] = (100 * (0.7 * fam.breadth
                                        + 0.3 * fam.mean_pct_high / 100)).round(1)
    save(fam, "eures_shortage_sectors")

    print(f"    {len(df)} widespread shortage occupations parsed")
    print("    sector families:")
    for r in fam.sort_values("shortage_intensity", ascending=False).itertuples():
        print(f"      {r.sector:<6}{r.n_occupations:>3} occupations  "
              f"breadth {r.breadth:>4.0%}  high-severity {r.mean_pct_high:>4.0f}%"
              f"  intensity {r.shortage_intensity:>5.1f}")


if __name__ == "__main__":
    main()
