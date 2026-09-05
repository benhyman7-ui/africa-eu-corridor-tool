## Recommendations

_Interpretation of the tables above; written September 2026 against the build
of that date. Re-read if the numbers move materially on a rebuild._

### What predicts migration in a dyad

Our factors genuinely predict: without any network variable, gradient boosting
explains **R² ≈ 0.66 of log flows on origins the model never saw** (0.61 on
unseen destinations). With network variables the ceiling is ~0.77.

Four findings dominate:

1. **The strongest single predictor is the student pipeline** (+0.82 within
   destination). Where students go, work permits follow. It is not a lever a
   fund pulls directly, but it is the best early-warning signal of a corridor
   opening, and it is measurable annually.
2. **The levers we actually hold are consular and legal infrastructure.**
   Visa consulate in country (+0.61), VFS centre (+0.56), embassy (+0.54),
   EU instruments (+0.52), any agreement (+0.51) — and in count space with
   destination fixed effects, agreements carry z = +11, VFS z = +3.9,
   consulate z = +3.1. These survive every control we can throw at them.
   One caution: agreements are partly endogenous — states sign them where
   flows already exist — so treat the coefficient as an upper bound.
3. **Culture does not predict. Religion similarity is nil-to-negative**
   (−0.20 within destination), Hofstede distance is incoherent (−0.17 in one
   view, +0.12 in the other). The concept note's cultural-proximity measure
   fails empirically as a predictor of flows.
4. **Income gain is an anti-predictor.** Doublings correlate −0.28 with flows
   within destination: the poorest origins send *fewer* migrants, not more —
   the classic poverty constraint on mobility. Income gain is the right
   *objective*; it is the wrong *feasibility signal*, and anything that scores
   opportunity must gate on feasibility precisely because the two point in
   opposite directions.

Also nil, and worth naming because they carry weight in the current scores:
destination vacancy rate (0.00), destination unemployment (~0), origin youth
unemployment (−0.01), literacy (+0.14), religion terms, comcol. Origin
**tertiary enrolment** (+0.47) and **working-age population** (+0.50) are the
origin traits that matter; "idle young people" as measured by youth
unemployment adds nothing.

### Best predictive factor set

For forecasting a dyad's flow: student permits, diaspora, distance, consular
presence (consulate/VFS/embassy), agreements and EU instruments, working-age
population, tertiary enrolment, colonial tie, language proximity. For a
*deployable* model restricted to levers and structure, drop the first two and
keep the rest — that is the 0.66-R² set.

### AJF calibration (feasible × big opportunity × big income gain)

- **Cut `f_cultural` from 0.12 of ease to ~0.03, or remove it.** Zero-to-
  negative predictive power. Its 0.12 weight is the largest weight the tool
  gives to a factor with no empirical support.
- **Rebuild `p_labour_supply` on working-age population alone.** The youth-
  unemployment multiplier adds nothing (−0.01) and injects noise.
- **Shrink `p_dest_demand` from 0.15 to ~0.05.** The vacancy rate does not
  predict corridor formation; demand expresses itself through routes and
  shortage-list designations, which access/ease already carry.
- **Keep income doublings at 0.40 of potential — but keep the access gate
  strong**, because doublings point *against* feasibility (−0.28). The
  harmonic access gate and the geometric scale fold are what stop the score
  drifting toward poor-but-unreachable corridors; this analysis is direct
  evidence for them.
- **Redistribute the freed ease weight toward `f_agreement` and
  `f_language`** (both predictive: +0.51 and +0.34/+0.22), with the
  endogeneity caution on agreements noted above.
- **Add a "student pipeline" signal to potential** (~0.10): student permits
  per capita is the best leading indicator we hold and is currently unused in
  any score. It is tier-N, so frame it as momentum, like diaspora growth.

### CGD calibration (underserved = actual far below predicted)

- **Keep policy levers OUT of the gravity benchmark.** Consulates, VFS and
  agreements predict strongly — which is exactly why they must not enter the
  prediction: a corridor with no consulate would have its low flows
  "explained" and stop reading as underserved, when missing consular
  infrastructure is precisely the fixable friction CGD wants to surface.
- **Add structural, non-fixable predictors to the spec:** `language_proximity`
  (beyond the comlang dummy already there). Drop `comcol` (nil).
- **Sharpest upgrade: decompose the gap.** With the PPML machinery already in
  place, fit once with policy levers included and once without; the difference
  in a corridor's prediction is the share of its shortfall attributable to
  missing consular/legal infrastructure — the *fixable* share — versus
  unexplained residual. That turns "underserved" from a flag into an agenda.

## Decisions taken (September 2026)

The recommendations above were reviewed and applied with two deliberate
overrides:

- **`f_cultural` stays at 0.12.** Decision: it scores expected quality of
  integration, not a forecast of flow volume, so its nil predictive power is
  not disqualifying.
- **Policy levers went INTO the CGD benchmark**, not out of it. Decision: the
  underserved corridor worth surfacing is one that already has visa capacity
  and a legal channel yet still runs below what those predict. The pure
  allocation gap is retained alongside as `*_alloc`.

Applied as recommended: labour supply rebuilt on working-age population alone;
destination demand cut 0.15 -> 0.05; student pipeline added at 0.10 of
potential; ease redistributed toward agreement and language; f_admin and
literacy dropped from scoring (duplication and nil signal respectively);
comcol excluded from the policy spec; the neighbouring-embassy consular tier
raised 40 -> 75 on the PPML estimate (~0.9 of the in-country effect,
discounted for endogeneity).
