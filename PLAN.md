# Africa→EU Labour Mobility Corridor Tool — Build Plan

**Version:** v1 draft, 26 Aug 2026
**Source brief:** CGD/TMO concept note, *Identifying Underutilized, Low-Cost Migration Corridors* (June 2026)
**Goal:** a fully coded, self-hosting interactive tool that scores every Africa→EU labour corridor on potential, underutilisation, and cost-to-unlock — built end-to-end from public data, in one build pass.

---

## 1. Feasibility — already verified

Before planning I hit the live endpoints. Results:

| Source | Endpoint | Status |
|---|---|---|
| Eurostat `migr_resocc` | REST/JSON-stat API | **200** — bilateral first permits for *remunerated activities*, dims `reason(6) × citizen(178) × geo × duration × year`. This is the core panel. |
| Eurostat `migr_resfirst` | REST/JSON-stat | **200** — all-reason first permits (family/education/employment/other) |
| World Bank WDI + WGI | `api.worldbank.org/v2` | **200** |
| UNHCR Refugee Data Finder | `api.unhcr.org/population/v1` | **200** |
| CEPII GeoDist | `cepii.fr/distance/dist_cepii.zip` | **200**, 2.1 MB |
| CEPII Gravity (full) | `cepii.fr/DATA_DOWNLOAD/gravity/...zip` | **200**, 207 MB |
| CIA World Factbook JSON | `raw.githubusercontent.com/factbook/factbook.json` | **200** — religion shares + languages per country |
| Wikidata SPARQL | `query.wikidata.org/sparql` | **200** — returned 155 German diplomatic missions. **This is how we get embassy presence.** |
| REST Countries | `restcountries.com/v3.1` | **200** |

Local toolchain: Python 3.14.2, pandas 3.0.1, numpy 2.4.2, `pip` working (installed `statsmodels` 0.14.6, `pyarrow`, `requests` successfully), Node 24.12.

**Conclusion: the whole thing is buildable purely through code.** One material exception, see §9.

---

## 2. Scope and grain

| Dimension | Values | N |
|---|---|---|
| Origin | African Union member states | 54 |
| Destination | EU-27 (extension slot: UK, CH, NO, IS) | 27 |
| **Corridors** | origin × destination | **1,458** |
| Permit category | Total employment, EU Blue Card, Highly-skilled, Researchers, Seasonal, Other employment | 6 |
| Year | 2018–2024 | 7 |
| Occupation family | Health & care, ICT, Construction & trades, Agriculture, Hospitality & food, Transport & logistics, Engineering & technical, Manufacturing, Green/energy transition, Education, Domestic & personal services, Professional services | 12 |

Core flow panel ≈ **61,000 observed cells** (1,458 × 6 × 7). Occupation overlay ≈ 17,500 modelled cells. The gravity model is estimated on the **global** panel (178 citizenships × 27 destinations × 7 years ≈ 33,600 rows) so coefficients are properly identified, then *predicted onto* the 1,458 Africa–EU cells.

---

## 3. Data layers

### Layer A — Flows (the dependent variable)

| Source | Gives us | Access |
|---|---|---|
| Eurostat `migr_resocc` | First permits for remunerated activity by permit category × nationality × member state × year | API ✅ verified |
| Eurostat `migr_resfirst` | All-reason first permits (context + family/study base rates) | API ✅ |
| Eurostat `migr_resvalid` | Stock of valid permits (network denominator) | API |
| Eurostat `migr_resbc2` | Blue Card family reunification (an intensity signal) | API ✅ |
| OECD International Migration Database | Independent bilateral inflows for cross-validation | SDMX |

### Layer B — Corridor structure (gravity inputs)

| Source | Gives us | Access |
|---|---|---|
| CEPII GeoDist | Distance, contiguity, common official language, common ethnic language, colonial relationship, common coloniser | ZIP ✅ |
| CEPII Gravity / Language | Melitz–Toubal **linguistic proximity index** (continuous, far better than the binary) | ZIP ✅ |
| UN DESA Migrant Stock 2020 | Bilateral diaspora stock — the single strongest gravity predictor | XLSX download |
| OpenFlights | Country-pair direct airline route counts (connectivity) | GitHub CSV |
| UChicago Bilateral Labour Agreements | Treaty record 1945–2020 | CSV |

### Layer C — Origin push factors

| Source | Gives us | Access |
|---|---|---|
| World Bank WDI (16 indicators) | GNI pc PPP, unemployment (total + youth), tertiary enrolment, urbanisation, internet penetration, remittances %GDP, LFPR, population growth, secondary completion | API ✅ |
| World Bank WGI (6 indicators) | Corruption control, government effectiveness, political stability, rule of law, regulatory quality, voice & accountability | API ✅ |
| UN WPP 2024 | Working-age population 2025–2050, youth bulge, median age, net migration | CSV |
| UCDP/PRIO | Conflict intensity, battle deaths at origin | API/CSV |
| UNHCR Refugee Data Finder | Bilateral refugee + asylum-seeker stocks 2018–2024 | API ✅ |
| ILOSTAT | Employment by ISCO-1 occupation at origin — the **skills-supply** side of the occupation overlay | SDMX/bulk |
| World Bank ID4D | National-ID coverage (can people even get documents?) | CSV |

### Layer D — Destination pull factors

| Source | Gives us | Access |
|---|---|---|
| Eurostat `jvs_a_rate_r2` | Job vacancy rate, total and by NACE sector | API |
| Eurostat `une_rt_a` | Unemployment rate | API |
| Eurostat `nama_10_a10` / `earn_ses` | Sectoral wages (the income-gain numerator) | API |
| Eurostat `lfsa_egan2` | Employment by sector (absorptive capacity scale) | API |
| Eurostat `demo_pjanind` | Old-age dependency ratio (structural demand) | API |
| EURES Labour Shortages & Surpluses 2024 | Country × ISCO shortage flags | Annex parse → CSV |
| National shortage occupation lists | Member-state declared shortage rosters | Curated CSV |

### Layer E — The added factors you asked for

See §4 — this is where the tool goes beyond the concept note.

---

## 4. Your added factors — how each gets operationalised

### 4.1 Religious / cultural proximity

Build a religion-share vector per country (~8 groups: Sunni Islam, Shia Islam, Catholic, Orthodox, Protestant, other Christian, traditional/folk, none/other) from **CIA World Factbook JSON**, supplemented by Pew/ARDA where the Factbook only says "Muslim 90%". Corridor score = **cosine similarity** (and 1 − Hellinger distance as a robustness variant) between origin and destination vectors.

Also carry **Hofstede six-dimension cultural distance** (Kogut–Singh index) as a separate, better-established cultural measure.

> **Framing note.** Religious similarity is a weak and contested predictor of migration outcomes, and it is politically loaded as a selection criterion. I will ship it as a *tunable input inside a "social proximity" bundle* alongside language, diaspora and Hofstede — **default weight low, clearly labelled as a proxy for settlement ease, not a causal or normative claim**, and fully switchable to zero. That keeps it useful for analysis without the tool implying anyone should be selected on religion.

### 4.2 Embassy / consular presence — *high value, underrated*

Wikidata SPARQL over `diplomatic mission (Q3917681)` with `operator (P137)` and `country (P17)`. Yields per corridor:

- `dest_has_embassy_in_origin` (0/1)
- `dest_consulate_count_in_origin`
- `origin_has_embassy_in_dest` (0/1)
- `nearest_accredited_mission_km` — where there is no mission, distance to the covering embassy in a third country

Cross-validated against destination foreign-ministry mission lists. Supplemented by **VFS Global / TLScontact visa-centre presence** (published centre lists) — because outsourced application centres substitute for embassies in practice.

**Why this matters:** most EU work-visa processes require in-person biometric appearance. A corridor with no mission and no VFS centre means an applicant must fly to a third country before they can even apply. That is frequently the binding constraint, and it appears in no existing corridor dataset.

### 4.3 MoUs and bilateral agreements

Two-part build:

1. **Historical/structured:** UChicago BLA dataset (1945–2020).
2. **Current/curated CSV** (`ref/agreements.csv`, ~150–250 rows) covering EU Talent Partnerships, Mobility Partnerships, Global Skill Partnerships, and national bilateral labour agreements — e.g. Spain–Morocco/Senegal, Germany–Kenya/Morocco/Egypt, Portugal–CPLP states, France–Tunisia/Senegal/Morocco, Italy's *Decreto Flussi* preferential-quota country annex, Greece–Egypt/Morocco, Belgium, Malta.
   Fields: `dest, origin, instrument_type, year_signed, sector_scope, binding_vs_declaratory, status, source_url`.

**Italy's Decreto Flussi preferential-quota list deserves special treatment** — it is a hard, legally published, annually updated list of countries with reserved quotas. Fully codable from the decree annexes and a genuinely strong signal.

### 4.4 Language similarity — three tiers

- **(a)** Shared official language, binary (CEPII `comlang_off`)
- **(b)** Continuous **linguistic proximity** (CEPII / Melitz–Toubal `lp1`, `lp2`) — captures e.g. Portuguese↔Spanish, and French↔Wolof-region multilingualism
- **(c)** **Destination language burden** — how far the destination's labour market can be entered in English, from EF EPI and Eurostat adult foreign-language-skills. Netherlands/Ireland/Nordics score low-burden; France/Italy/Poland high.

Combined into a single `language_access` score, decomposable in the UI.

### 4.5 Additional factors I would add (high signal, all codable)

| Factor | Source | Why |
|---|---|---|
| **Schengen visa refusal rate** by consulate × nationality | EU Home Affairs annual XLSX | The single most direct public measure of administrative friction |
| **Income gain per mover (PPP)** | Eurostat sectoral wages ÷ WDI/ILOSTAT origin wages, per occupation family | This is the *benefit* side AJF actually optimises for — a first-class column, not a footnote |
| **Recruitment cost** as multiple of monthly earnings | KNOMAD/ILO SDG 10.7.1 surveys (sparse) + modelled fallback | The migrant's out-of-pocket barrier |
| **Qualification recognition regime** | EU Regulated Professions Database + curated recognition arrangements | Decides whether a Kenyan nurse can practise or must re-qualify |
| **Diaspora density** | UN DESA stock ÷ origin population, plus stock growth rate | Network effects, normalised |
| **Remittance corridor cost** | World Bank Remittance Prices Worldwide | Proxy for corridor infrastructure maturity |
| **Direct flight connectivity** | OpenFlights | Cost and friction of the physical move |
| **Passport / document access** | ID4D coverage + passport-issuance capacity | Upstream blocker |
| **Origin emigration restrictions** | Curated (e.g. domestic-worker deployment bans) | Origin-side legal blockers most datasets ignore |
| **Permit-category restrictiveness** | Blue Card national salary thresholds, labour-market tests, quota regimes, MIPEX | Public substitute for the ICMPD cost index (see §9) |

---

## 5. The model

Four scores per corridor (and per corridor × permit category where data allows):

### Score 1 — Potential (benefit side)

`expected income gain per mover × plausible scale`

- Income gain: PPP-adjusted destination sectoral wage ÷ origin wage, per occupation family
- Destination absorptive capacity: vacancy rate × sector employment size
- Origin labour surplus: youth bulge × unemployment × relevant skill stock

### Score 2 — Underutilisation (the gap)

**PPML gravity** (Poisson pseudo-maximum-likelihood, Santos Silva & Tenreyro) — the right estimator because a large share of Africa–EU cells are genuine zeros, which PPML handles natively and log-OLS cannot.

```
permits_odt ~ Poisson( exp( β1·log dist + β2·comlang + β3·colonial
                          + β4·log(1+diaspora) + β5·BLA + β6·routes
                          + β7·conflict + β8·refugee_stock
                          + α_ot + γ_dt ) )
```

Origin×year and destination×year fixed effects absorb all multilateral resistance, so the residual is *precisely* the corridor-specific anomaly — which is exactly what "underutilised" means. Estimated globally, predicted onto Africa–EU.

`underutilisation = log( (actual + 0.5) / (predicted + 0.5) )`, percentile-ranked.

Robustness variants also computed and exposed: log-OLS on positive flows, and a two-part (hurdle) model.

### Score 3 — Friction (cost to unlock)

Composite of embassy/VFS presence, visa refusal rate, language access, MoU presence, recognition regime, permit restrictiveness, recruitment cost, document access, connectivity. Sub-weights user-adjustable.

### Score 4 — Actionability (headline)

`f(Potential, Underutilisation, 1−Friction)` with live user weights. Offered as both arithmetic and **geometric** mean — geometric because a hard zero on one dimension (no embassy, no legal pathway) really should collapse the whole score, which an arithmetic mean hides.

All sub-indices normalised 0–100 by **percentile rank within the Africa–EU universe** (robust to outliers), direction-aligned so higher = more attractive.

### Bridge to AJF cost-effectiveness

Each corridor also carries an estimated **$/DCY**: `expected additional movers × income multiple × years of gain`, against an editable intervention-cost assumption block. This makes the tool directly comparable to the AJF BOTEC rather than a parallel exercise.

---

## 6. Architecture

**Static single-page app + Python ETL.** No server, no database, hostable on GitHub Pages / Cloudflare Pages / Netlify / Vercel / S3 / a Claude Artifact.

```
pipeline/   one fetcher per source → data/raw/*.parquet   (cached, re-runnable, offline after first run)
build.py    assemble → model → index → export
web/        index.html + vendored D3 + vanilla JS/Preact
```

Exports:

- `web/data/corridors.json` — 1,458 rows × ~80 fields (~250 KB gzipped)
- `web/data/panel.json` — flows by year × permit category, columnar (~200 KB gzipped)
- `web/data/meta.json` — countries, indicator definitions, source registry

**All weighting is recomputed client-side**, so sliders re-rank instantly with no round-trip. A `build_singlefile.py` also emits a single self-contained HTML you can email or publish as an Artifact.

Rejected: Streamlit/Dash — needs a live server, worse UX, much harder for CGD to host publicly.

### UI

1. **Map** — Africa↔EU arc map, weighted by the active score; click an origin to rank its destinations
2. **Ranked table** — all 1,458 corridors, sortable, with score-decomposition bars
3. **Weight panel** — sliders per factor group, live re-rank, plus presets (*AJF income-max*, *CGD low-hanging fruit*, *EPIM ecosystem*)
4. **Corridor detail** — flow time series by permit type, actual-vs-predicted gravity chart, friction checklist (embassy ✓/✗, MoU ✓/✗, language, refusal rate), occupation overlay, $/DCY, provenance footnotes
5. **Matrix heatmap** — 54 × 27 grid on any selected metric
6. **Occupation view** — pick a profession family, see its best corridors
7. **Methodology & sources** — every source with vintage and confidence badge
8. **Export** — CSV/JSON of the current view

### Provenance discipline

Every field carries `source_id` and `confidence` ∈ {high = official statistic, medium = modelled/derived, low = curated/hand-coded}, and the UI shows it. This is what makes the tool defensible to CGD rather than a black box.

---

## 7. Repo layout

```
├── PLAN.md
├── README.md
├── requirements.txt
├── ref/
│   ├── countries.csv          # iso2/iso3/m49/eurostat_geo/wb/cepii + region + AU flag
│   ├── agreements.csv         # curated MoU/BLA/Talent Partnership roster
│   ├── occupations.csv        # 12 families ↔ ISCO/NACE crosswalk
│   ├── religion_groups.csv
│   └── sources.csv            # source registry (id, name, url, vintage, licence, confidence)
├── pipeline/
│   ├── _common.py             # HTTP cache, retry, JSON-stat parser, ISO harmoniser
│   ├── eurostat_permits.py    ├── eurostat_labour.py
│   ├── worldbank.py           ├── unhcr.py
│   ├── undesa_stock.py        ├── unwpp.py
│   ├── cepii.py               ├── ucdp.py
│   ├── openflights.py         ├── ilostat.py
│   ├── factbook_religion.py   ├── wikidata_embassies.py
│   ├── schengen_refusals.py   ├── eures_shortages.py
│   └── hofstede.py
├── model/
│   ├── assemble.py            # the corridor panel
│   ├── gravity.py             # PPML + robustness variants
│   ├── indices.py             # potential / friction / actionability
│   └── cea.py                 # $/DCY bridge
├── build.py                   # orchestrates everything
├── build_singlefile.py
└── web/
    ├── index.html  app.js  style.css  vendor/d3.min.js
    └── data/{corridors,panel,meta}.json
```

---

## 8. Build sequence

| Phase | What | Note |
|---|---|---|
| **0** | Scaffold + `ref/countries.csv` + ISO harmoniser | Where projects like this usually die. Eurostat uses `EL` not `GR`, plus `UK`, `XK`; WB uses ISO3; UN uses M49. Getting this right first is non-negotiable. |
| **1** | 15 fetchers, each cached to parquet | Re-runnable, offline after first pass |
| **2** | Assemble panel, fit PPML, compute indices, $/DCY | |
| **3** | Frontend: 7 views | |
| **4** | Methodology page, provenance, exports, single-file build | |
| **5** | Deploy (Vercel/Pages) + README | |

~40–50 files. Buildable in one pass.

---

## 9. Known gaps — stated up front

1. **The ICMPD/ECDPM 354-pathway file is not public.** This is the biggest fidelity gap versus the concept note; we cannot replicate its Step-2 pathway cost index exactly. **Substitute:** MIPEX 2020 labour-market-mobility strand (public, all EU-27) + published Blue Card salary thresholds + national shortage lists + a curated roster of ~60–100 documentable major pathways. The schema will reserve a defined drop-in slot so that if CGD shares the file, it plugs straight in and upgrades the index without rework.
2. **No ISCO breakdown in bilateral flows.** Eurostat gives permit *category*, not occupation. The occupation overlay is therefore **modelled** (destination shortage × origin skill supply × sector wage gap), not observed — and will be labelled as such everywhere it appears.
3. **Curated fields** (MoUs, emigration bans, recognition regimes) are hand-assembled and need CGD/TMO expert review. They ship as editable CSVs with per-row source URLs so corrections are a one-line edit, not a code change.
4. **Eurostat suppression.** Small cells are suppressed or flagged `:`. Zero vs. missing must be distinguished explicitly — conflating them would fabricate underutilisation signals. Handled in the parser and documented.
5. **Religion weighting** — see §4.1. Included, defaulted low, clearly labelled, switchable off.
6. **Cross-sectional causality.** The tool ranks and diagnoses; it does not identify causal effects. The methodology page will say so plainly.

---

## 10. What I need from you before building

Only two things are genuinely blocking-adjacent; I will proceed on stated defaults otherwise.

1. **Destination set** — EU-27 only (concept note scope), or add UK/CH/NO? *Default: EU-27, with the extension slot wired but unpopulated.*
2. **Headline metric** — should the default ranking optimise **AJF-style income gain per dollar ($/DCY)**, or **CGD-style "underutilisation × low friction"**? *Default: ship both as presets, open on the AJF preset.*

Everything else I will build on the defaults above and flag as I go.
