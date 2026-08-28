# Africa → EU Labour Mobility Corridor Tool

An interactive, self-hosting tool that scores **all 1,458 labour mobility corridors**
from the 54 African countries to the EU-27 on three dimensions — how far actual flows
fall below what a gravity model predicts, how hard the corridor would be to unlock, and
how much a mover would gain — and lets you re-weight all three live.

Built end to end from public data. Everything in `web/` is static: no server, no
database, no API keys.

Implements the CGD/TMO concept note *Identifying Underutilized, Low-Cost Migration
Corridors* (June 2026), with additional factors on consular access, bilateral
agreements, language and cultural proximity. See [PLAN.md](PLAN.md) for the design and
[§ Known limits](#known-limits) before citing anything.

---

## Quick start

```bash
pip install -r requirements.txt
```

```bash
python build.py --serve
```

First run fetches ~15 sources (a few minutes). Every response is cached to
`data/cache/`, so reruns do no network I/O. Then open <http://localhost:8000>.

Other entry points:

```bash
python build.py --model
```

(skip fetching, re-run model and export only)

```bash
python model/gravity.py --validate
```

(check the PPML estimator against a dense-dummy statsmodels GLM)

```bash
python build_singlefile.py
```

(emit `dist/corridor-tool.html`, one self-contained file, no server needed)

---

## What it found

Numbers below are from the current build, 2018–2024 Eurostat data.

| | |
|---|---|
| Corridors scored | **1,458** (54 African origins × EU-27) |
| Africa→EU employment permits, 2024 | **120,135** |
| Corridors with **zero** permits in 2024 | **584** (40%) |
| Corridors where a destination **embassy exists** | **383** (26%) |
| Corridors with **any direct flight** | **127** (9%) |
| Corridors below the model's prediction | **1,036** |
| Flagged **low-hanging fruit** | **75** |
| Median income multiple across corridors | **12.5×** |

Two findings stand out and are visible in no existing corridor dataset:

- **Three quarters of Africa–EU corridors have no destination embassy in the origin
  country.** Most EU work visas require an in-person biometric appointment, so an
  applicant in one of those countries must travel abroad before they can even apply.
  Lesotho has no EU embassy at all; Latvia, Estonia and Cyprus each maintain one
  embassy across the whole continent.
- **Poland issues enormous volumes of work permits and almost none to Africans.** Its
  intake is dominated by Ukrainian and Belarusian workers. That makes it simultaneously
  the largest apparent opportunity and the one most in need of a reality check — which
  is exactly why the ranking does not let it dominate (see below).

---

## Method in brief

**Underutilisation.** A PPML gravity model (Poisson pseudo-maximum-likelihood) fitted
on the **global** panel — every reported citizenship into every EU state, 31,214
corridor-years — then predicted onto the Africa–EU corridors. Fitting on Africa alone
would fold the anomaly being hunted into the coefficients. PPML because 41% of the
panel is genuine zeros: log-OLS cannot use a zero and would discard exactly the
observations that matter.

The headline spec carries **origin×year and destination×year fixed effects**, so
everything multilateral is absorbed and the residual is the corridor-specific anomaly.
Regressors are dyadic only.

A second **absolute** spec swaps the origin fixed effect for origin fundamentals. It is
shown on the corridor card but deliberately does not drive the ranking: its destination
effects are identified off each member state's total third-country intake, and
extrapolating Poland's Ukraine-driven volume to Chad produced predictions above 1,700
permits a year that no policy could deliver.

The fixed-effect estimator is implemented directly (IRLS with alternating weighted
within-transformations) and **verified to 1e-13 against a dense-dummy statsmodels
Poisson GLM**. Standard errors are clustered by corridor.

Fitted coefficients are all correctly signed and significant: log distance −1.26,
shared official language +1.75, colonial tie +0.50, log diaspora +0.22, log air routes
+0.63.

**Ease of unlocking.** Seven percentile-ranked components: consular access, language
access, legal channel (curated agreements register), air connectivity, proximity, origin
administrative capacity, and cultural proximity.

Consular access is **tiered**, because "no embassy" is not a single state — an applicant
in Lesotho reaches the South African capital in 406 km, one in Chad may face thousands:

| Situation | Score | Corridors |
|---|---|---|
| Destination embassy in the origin country | 100 | 383 |
| Lesser consular post in country | 65 | – |
| **A land neighbour holds one** | **40** | **445** |
| Nearest mission elsewhere in the region | 5–35 by distance | 630 |
| Nothing reachable in Africa | 0 | 0 |

The partial credit sits well below parity because crossing a border still means a second
visa, a flight and time off work. The fallback search is restricted to African countries
by design: an applicant who would have to reach Europe to apply for a European visa is
not a fallback, it is circular. Island states with no land border (Cabo Verde, Comoros,
Mauritius, São Tomé, Seychelles, Madagascar) are covered by the distance tier, whose
floor at 800 km keeps them from being punished for geography.

**Potential.** Income gain (PPP GNI per capita ratio), labour supply at origin, and
destination demand.

All components are 0–100 percentile ranks, oriented so higher is always better. The
composite is assembled **in the browser**, so the sliders re-rank instantly.

### On the cultural-proximity component

Religious composition is parsed from the CIA World Factbook into a coarse eight-group
vector and compared by cosine similarity. It carries the **smallest default weight in
the index (3%)** and can be set to zero. It is included because ease of settlement
genuinely affects whether a corridor sustains itself, and it is exposed as a visible
component so anyone can see exactly how much it moved a ranking. It is **not** a
recommendation to select workers on religion.

---

## MCP server (Cowork custom connector)

The deployment also speaks [MCP](https://modelcontextprotocol.io), so the corridor
data can be queried directly from Cowork, Claude Desktop or any MCP client rather
than read off the web page.

**To add it in Cowork:** Settings -> Connectors -> Add custom connector, and give it

```
https://<your-deployment>.vercel.app/mcp
```

No authentication - the endpoint is read-only and serves the same public data the
site already publishes. Opening that URL in a browser returns a short description
of the server and its tools, which is a quick way to confirm a deployment is live.

Five tools:

| Tool | What it answers |
|---|---|
| `list_countries` | The 54 origins and 27 destinations, with ISO3 codes and subregions |
| `search_corridors` | Ranked corridors, filtered by country, subregion, preset, score, current flow, low-hanging-fruit, WHO flag |
| `get_corridor` | Everything behind one corridor: components, gravity comparison, and the underlying facts |
| `get_methodology` | Weights, fitted gravity coefficients, and the source register with vintage and confidence |
| `get_sector_shortages` | EURES widespread shortage occupations by sector family |

`api/mcp.js` implements JSON-RPC over Streamable HTTP directly rather than using
the MCP SDK. The site deploys with `buildCommand` and `installCommand` both null,
so a dependency would mean adding a build pipeline to serve a small and stable
protocol surface. Its data comes from `api/corridors.json`, which `build.py`
writes alongside the browser payload - so the connector and the web app can never
disagree about a score.

## Repository layout

```
build.py                  one-command build
build_singlefile.py       inline everything into dist/corridor-tool.html
pipeline/                 one fetcher per source, all HTTP-cached
  _common.py              cached HTTP, JSON-stat parser, paths
  build_countries.py      the ISO code spine  <- run first
  eurostat_permits.py     bilateral flows (the dependent variable)
  eurostat_context.py     diaspora, vacancies, sector pay, unemployment
  worldbank.py            WDI + WGI
  cepii.py                distance, language, colonial ties
  openflights.py          legacy scheduled routes (fallback only)
  eurostat_air.py         airport-pair passengers, incl. charter
  undesa_migrant_stock.py bilateral diaspora
  eures_shortages.py      EU shortage occupations
  wikidata_embassies.py   consular presence
  factbook.py             religion and language profiles
model/
  assemble.py             corridor panel + global estimation panel
  gravity.py              PPML with high-dimensional fixed effects (+ --validate)
  indices.py              percentile components and composites
  export.py               compact JSON for the front end + api/corridors.json
api/
  mcp.js                  MCP server (Cowork custom connector)
  corridors.json          its data, written by build.py
ref/
  countries.csv           generated code spine
  agreements.csv          curated agreements register  <- edit me
  sources.csv             source registry with confidence flags
web/                      static site: index.html, app.js, style.css, data/*.json
data/{cache,raw,interim}/ HTTP cache and intermediate parquet
```

Payload is **84 KB gzipped** for the corridor table (604 KB raw, columnar), ~103 KB
total. It will serve from GitHub Pages, Cloudflare Pages, Netlify, Vercel or S3 with no
configuration.

---

## Known limits

Read these before citing anything.

1. **The ICMPD/ECDPM 354-pathway file is not public.** The concept note's pathway-level
   cost index cannot be reproduced. Ease of unlocking is built from public substitutes.
   This is the single largest fidelity gap against the brief. The schema has a defined
   slot for it if CGD shares the file.
2. **No occupation detail exists in the flow data.** Eurostat reports permit *category*
   (Blue Card, seasonal, researcher), never ISCO. The sector overlay is **modelled**
   from destination-side vacancy and pay, and is labelled as such in the UI.
3. **The agreements register is hand-assembled** and needs expert review. It ships as an
   editable CSV with a source URL and confidence flag on every row; correcting it is a
   one-line edit, not a code change.
4. **Zero is not the same as missing.** Eurostat suppresses small cells. Unreported
   cells are dropped, never zero-filled — conflating them would manufacture
   underutilisation signals.
5. **Diaspora is a right-hand-side variable.** It is a stock of past migration, so a
   corridor can look "as expected" simply because no network ever formed. A no-diaspora
   variant is computed for this reason and exported alongside.
6. **South Sudan** records no employment permits into any EU state, so the allocation
   model has nothing to say about it. Its 27 corridors are flagged rather than scored.
   Its CEPII distances are haversine-filled (GeoDist predates independence).
7. **This is a cross-sectional ranking, not a causal estimate.** It cannot tell you what
   an intervention would actually unlock — that is the qualitative work the concept note
   assigns to TMO.
8. **The $/DCY panel is illustrative.** It applies your sidebar assumptions to a
   corridor's income multiple. It does not estimate how many movers a programme would
   unlock.

---

## Extending it

- **Add destinations** (UK, Switzerland, Norway): add them to `EU27` in
  `pipeline/build_countries.py`. Eurostat's `geo` dimension already carries `UK`, `CH`,
  `NO`, `IS` for several of these tables.
- **Add a factor**: emit a corridor-level column from a fetcher, join it in
  `model/assemble.py`, percentile-rank it in `model/indices.py`, and add it to
  `EASE_WEIGHTS`. The UI picks up new components from `meta.json` automatically.
- **Correct an agreement**: edit `ref/agreements.csv` and run `python build.py --model`.
