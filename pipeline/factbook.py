"""CIA World Factbook - religious composition and official languages.

Feeds two of the requested proximity factors. Both come as free text, so both are
parsed rather than read off a field, and both are stored with the raw source
string alongside the parsed shares so any number in the tool can be traced back.

Religion granularity is uneven: Kenya reports "Christian 85.5%" while Germany
reports "Roman Catholic 24.8%, Protestant 22.6%". Comparing those on a
denominational taxonomy would show spurious distance, so the primary vector is
deliberately COARSE (Christian / Muslim / folk / Hindu / Buddhist / Jewish /
other / none) and denominational detail is kept only as a secondary Catholic and
Orthodox share.
"""
from __future__ import annotations

import html
import json
import re
import unicodedata

import pandas as pd

from _common import countries, fetch, fetch_json, save, step

TREE = "https://api.github.com/repos/factbook/factbook.json/git/trees/master?recursive=1"
RAW = "https://raw.githubusercontent.com/factbook/factbook.json/master/"

# Coarse religion groups. Order fixes the vector layout.
GROUPS = ["christian", "muslim", "folk", "hindu", "buddhist", "jewish",
          "other", "none"]

# Matched longest-first so "roman catholic" wins over "roman".
RELIGION_MAP = [
    ("roman catholic", "christian"), ("catholic", "christian"),
    ("protestant", "christian"), ("orthodox", "christian"),
    ("evangelical", "christian"), ("anglican", "christian"),
    ("pentecostal", "christian"), ("presbyterian", "christian"),
    ("methodist", "christian"), ("baptist", "christian"),
    ("lutheran", "christian"), ("adventist", "christian"),
    ("jehovah", "christian"), ("mormon", "christian"),
    ("latter-day", "christian"), ("copt", "christian"),
    ("kimbanguist", "christian"), ("african instituted", "christian"),
    ("christian", "christian"),
    ("sunni", "muslim"), ("shia", "muslim"), ("shi'a", "muslim"),
    ("ibadhi", "muslim"), ("ahmadi", "muslim"), ("muslim", "muslim"),
    ("islam", "muslim"),
    ("hindu", "hindu"),
    ("buddhist", "buddhist"),
    ("jewish", "jewish"),
    ("animist", "folk"), ("traditional", "folk"), ("indigenous", "folk"),
    ("voodoo", "folk"), ("vodoun", "folk"), ("folk", "folk"),
    ("none", "none"), ("unaffiliated", "none"), ("atheist", "none"),
    ("agnostic", "none"), ("no religion", "none"), ("nonreligious", "none"),
]

# Languages that matter for EU labour-market access, normalised.
LANG_CANON = {
    "english": "English", "french": "French", "portuguese": "Portuguese",
    "spanish": "Spanish", "castilian": "Spanish", "arabic": "Arabic",
    "italian": "Italian", "german": "German", "dutch": "Dutch",
    "greek": "Greek", "swedish": "Swedish", "danish": "Danish",
    "finnish": "Finnish", "polish": "Polish", "czech": "Czech",
    "slovak": "Slovak", "hungarian": "Hungarian", "romanian": "Romanian",
    "bulgarian": "Bulgarian", "croatian": "Croatian", "slovene": "Slovene",
    "slovenian": "Slovene", "estonian": "Estonian", "latvian": "Latvian",
    "lithuanian": "Lithuanian", "maltese": "Maltese", "irish": "Irish",
    "swahili": "Swahili", "kiswahili": "Swahili", "amharic": "Amharic",
    "somali": "Somali", "afrikaans": "Afrikaans", "malagasy": "Malagasy",
    "tigrinya": "Tigrinya", "berber": "Berber", "tamazight": "Berber",
    "creole": "Creole", "crioulo": "Creole", "kreol": "Creole",
    "luxembourgish": "Luxembourgish", "catalan": "Catalan",
}


# The Factbook names a few countries differently from ISO 3166, and neither Congo
# matches on any ISO form.
NAME_ALIAS = {
    "democratic republic of the congo": "COD", "drc": "COD",
    "congo kinshasa": "COD", "zaire": "COD",
    "republic of the congo": "COG", "congo brazzaville": "COG",
    "cote d ivoire": "CIV", "ivory coast": "CIV",
    "cabo verde": "CPV", "cape verde": "CPV",
    "the gambia": "GMB", "gambia the": "GMB",
    "eswatini": "SWZ", "swaziland": "SWZ",
    "burma": "MMR", "czechia": "CZE", "czech republic": "CZE",
    "holland": "NLD", "netherlands": "NLD",
}


# The Factbook marks "(official)" inconsistently - Denmark's entry never says
# Danish is official, Ghana's lists ethnic languages without flagging English.
# These are the corrections, and every EU-27 working language is asserted rather
# than parsed because it is unambiguous and it is the side that gates employment.
OFFICIAL_OVERRIDE = {
    # EU-27 working languages
    "AUT": ["German"], "BEL": ["Dutch", "French", "German"], "BGR": ["Bulgarian"],
    "CYP": ["Greek"], "CZE": ["Czech"], "DEU": ["German"], "DNK": ["Danish"],
    "EST": ["Estonian"], "GRC": ["Greek"], "ESP": ["Spanish"], "FIN": ["Finnish", "Swedish"],
    "FRA": ["French"], "HRV": ["Croatian"], "HUN": ["Hungarian"],
    "IRL": ["English", "Irish"], "ITA": ["Italian"], "LTU": ["Lithuanian"],
    "LUX": ["Luxembourgish", "French", "German"], "LVA": ["Latvian"],
    "MLT": ["Maltese", "English"], "NLD": ["Dutch"], "POL": ["Polish"],
    "PRT": ["Portuguese"], "ROU": ["Romanian"], "SWE": ["Swedish"],
    "SVN": ["Slovene"], "SVK": ["Slovak"],
    # African origins the parser could not resolve
    "GHA": ["English"], "MLI": ["French"], "MUS": ["English", "French"],
    "SSD": ["English"], "ERI": ["Tigrinya", "Arabic", "English"],
}

# European languages that function as a labour-market lingua franca.
BRIDGE_LANGS = {"English", "French", "Portuguese", "Spanish"}


def _norm(s: str) -> str:
    # Factbook country names carry HTML entities: "C&ocirc;te d'Ivoire".
    s = html.unescape(str(s))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]", " ", s.lower())).strip()


def _clean(text: str) -> str:
    text = html.unescape(str(text)).replace("&nbsp;", " ").replace("<br>", " ")
    # "Christian 93/1%" is a typo in the DR Congo entry; "81-90%" is a reported
    # range (Greece). Normalise both to a single usable number.
    text = re.sub(r"(\d+)/(\d+)\s*%", r"\1.\2%", text)
    text = re.sub(r"(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*%",
                  lambda m: f"{(float(m.group(1)) + float(m.group(2))) / 2}%", text)
    return re.sub(r"\s+", " ", text)


def parse_religions(text: str) -> tuple[dict, float, float]:
    """Top-level shares only - parenthetical sub-breakdowns would double-count.

    Returns (coarse shares summing to <=1, catholic share, orthodox share).
    """
    t = _clean(text)
    # capture parentheticals before stripping, for the denominational extras
    inner = " ".join(re.findall(r"\(([^()]*)\)", t))
    top = re.sub(r"\([^()]*\)", " ", t)

    def shares(s: str) -> list[tuple[str, float]]:
        return [(m.group(1).strip().lower(), float(m.group(2)))
                for m in re.finditer(r"([A-Za-z][A-Za-z'\- ]{2,40}?)\s*<?\s*([\d.]+)\s*%", s)]

    coarse: dict[str, float] = {g: 0.0 for g in GROUPS}
    for label, pct in shares(top):
        if pct <= 0 or pct > 100:
            continue
        grp = next((g for k, g in RELIGION_MAP if k in label), "other")
        coarse[grp] += pct

    # denominational detail can sit at top level (Germany) or inside parens (Kenya)
    cath = orth = 0.0
    for label, pct in shares(top) + shares(inner):
        if "catholic" in label:
            cath = max(cath, pct)
        if "orthodox" in label:
            orth = max(orth, pct)

    total = sum(coarse.values())

    # A few entries report no percentages at all - Sudan is "Sunni Muslim, small
    # Christian minority", Eritrea lists four faiths bare. Fall back to the
    # ordering: the first named faith is the plurality, anything flagged small or
    # a minority gets a token share, the rest split what is left.
    if total <= 50:
        named: list[tuple[str, float]] = []
        for chunk in re.split(r"[;,]", top):
            low = chunk.lower().strip()
            if not low:
                continue
            grp = next((g for k, g in RELIGION_MAP if k in low), None)
            if grp is None:
                continue
            weight = 0.15 if re.search(r"small|minority|few|tiny", low) else 1.0
            named.append((grp, weight))
        if named:
            named[0] = (named[0][0], named[0][1] * 2.5)  # first listed = plurality
            wsum = sum(w for _, w in named)
            coarse = {g: 0.0 for g in GROUPS}
            for grp, w in named:
                coarse[grp] += 100.0 * w / wsum
            total = 100.0

    if total > 0:
        coarse = {k: v / max(total, 100.0) for k, v in coarse.items()}
    return coarse, cath / 100.0, orth / 100.0


def parse_languages(block) -> tuple[list[str], list[str]]:
    """Return (official languages, all recognised languages)."""
    if isinstance(block, dict):
        text = (block.get("Languages") or block).get("text", "") \
            if isinstance(block.get("Languages"), dict) else block.get("text", "")
    else:
        text = str(block or "")
    text = _clean(text)
    text = re.split(r"note\s*-", text)[0]

    official: list[str] = []
    every: list[str] = []
    for chunk in re.split(r"[;,]", text):
        low = chunk.lower()
        for key, canon in LANG_CANON.items():
            if re.search(rf"\b{re.escape(key)}\b", low):
                if canon not in every:
                    every.append(canon)
                if "official" in low and canon not in official:
                    official.append(canon)
                break
    return official, every


def main() -> None:
    cc = countries()
    want = {}
    for r in cc.itertuples():
        want[_norm(r.name)] = r.iso3
        if r.iso_name:
            want[_norm(r.iso_name)] = r.iso3

    step("listing Factbook country files")
    tree = fetch_json(TREE)["tree"]
    files = [t["path"] for t in tree
             if t["path"].startswith(("africa/", "europe/", "middle-east/"))
             and t["path"].endswith(".json")]

    step(f"parsing {len(files)} Factbook entries")
    rows = []
    seen = set()
    for i, path in enumerate(files, 1):
        try:
            doc = json.loads(fetch(RAW + path, suffix=".json").decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        gov = doc.get("Government", {})
        names = gov.get("Country name", {})
        cands = []
        for k in ("conventional short form", "conventional long form",
                  "local short form"):
            v = names.get(k)
            if isinstance(v, dict) and v.get("text"):
                cands.append(_norm(v["text"]))
        iso3 = (next((want[c] for c in cands if c in want), None)
                or next((NAME_ALIAS[c] for c in cands if c in NAME_ALIAS), None))
        if not iso3 or iso3 in seen:
            continue
        seen.add(iso3)

        ps = doc.get("People and Society", {})
        rel_txt = (ps.get("Religions") or {}).get("text", "")
        coarse, cath, orth = parse_religions(rel_txt)
        official, everylang = parse_languages(ps.get("Languages"))

        override = OFFICIAL_OVERRIDE.get(iso3)
        if override:
            official = override
            everylang = list(dict.fromkeys(override + everylang))

        # A European language in wide use is the operative variable for EU labour
        # access, whether or not it is constitutionally official.
        bridges = sorted(BRIDGE_LANGS.intersection(everylang) | BRIDGE_LANGS.intersection(official))

        rows.append({
            "iso3": iso3,
            **{f"rel_{g}": coarse.get(g, 0.0) for g in GROUPS},
            "rel_catholic": cath, "rel_orthodox": orth,
            "rel_source_text": rel_txt[:400],
            "lang_official": ";".join(official),
            "lang_all": ";".join(everylang),
            "lang_bridge": ";".join(bridges),
            "lang_overridden": int(bool(override)),
            "lang_source_text": str(ps.get("Languages"))[:300],
        })
        print(f"      [{i}/{len(files)}] {len(rows)} matched", end="\r", flush=True)
    print(" " * 50, end="\r")

    df = pd.DataFrame(rows)
    save(df, "factbook_profile")

    missing = sorted(set(cc.iso3) - set(df.iso3))
    relsum = df[[f"rel_{g}" for g in GROUPS]].sum(axis=1)
    print(f"    matched {len(df)} / {len(cc)} countries")
    if missing:
        nm = dict(zip(cc.iso3, cc.name))
        print(f"    unmatched: {[nm.get(m, m) for m in missing]}")
    print(f"    religion shares parsed (sum>0.5): {(relsum > 0.5).sum()} / {len(df)}")
    print(f"    official language parsed:         {(df.lang_official != '').sum()} / {len(df)}")


if __name__ == "__main__":
    main()
