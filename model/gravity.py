"""PPML gravity model with high-dimensional fixed effects.

Poisson pseudo-maximum-likelihood (Santos Silva & Tenreyro 2006) is the right
estimator here for a specific reason: a large share of Africa-EU corridor-years
are genuine zeros. Log-OLS cannot use a zero at all, so it would silently drop
exactly the observations the whole exercise is about. PPML uses them.

Fixed effects are origin x year and destination x year. That absorbs everything
multilateral - how attractive Germany was in 2023, how much pressure Sudan was
under in 2023 - leaving the residual as the CORRIDOR-specific anomaly. That
residual is the definition of "underutilised" in the CGD framing.

Estimated on the global panel (all reported citizenships), predicted onto the
1,458 Africa-EU corridors. Fitting on Africa alone would fold the anomaly into
the coefficients.

The HDFE absorption is IRLS with alternating weighted within-transformations
(Guimaraes-Portugal / Correia). It is verified against a dense-dummy statsmodels
Poisson GLM in validate() - run `python gravity.py --validate`.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _common import INTERIM, load, save, step  # noqa: E402

# Two specifications, because one model cannot answer both questions.
#
# ALLOCATION  origin x year and destination x year fixed effects. Asks: given how
#             many workers this origin sends to the EU in total, is this
#             particular destination anomalously low? Theory-consistent and very
#             robust, but blind by construction to an origin that sends nobody
#             anywhere - its fixed effect simply goes to minus infinity and the
#             model "expects" zero.
#
# ABSOLUTE    destination x year fixed effects only; the origin effect is
#             replaced by fundamentals. Asks: given this country's income,
#             demography, education and governance, is its movement to this
#             destination low? This is the one that catches whole-origin
#             underutilisation - Lesotho, Eswatini, Malawi - which is exactly
#             what the brief is looking for.
#
# Headline underutilisation uses ABSOLUTE; ALLOCATION is reported alongside it so
# a corridor's shortfall can be split into "this origin under-sends everywhere"
# versus "this origin under-sends to this destination specifically".
DYADIC = ["log_dist", "contig", "comlang_off", "comlang_ethno", "colony",
          "comcol", "log_diaspora", "log_routes", "log_forced"]

ORIGIN_COVARS = ["log_ov_gni_pc_ppp", "log_ov_population", "ov_youth_unemployment",
                 "ov_internet_users", "ov_urban_share", "ov_wgi_govt_effectiveness",
                 "log_ov_battle_deaths"]

# Contiguity and common-coloniser are dropped from the absolute spec. Without an
# origin fixed effect they are near-separated - only 34 contiguous pairs exist in
# the whole panel and almost none survive the covariate sample - which drove the
# contiguity coefficient to -21 and pushed Spain-Morocco, one of the largest real
# corridors, towards a predicted zero.
SPEC_ABSOLUTE_DYADIC = ["log_dist", "comlang_off", "comlang_ethno", "colony",
                        "log_diaspora", "log_routes", "log_forced"]

# Diaspora is itself a stock of past migration, so a corridor can look
# "expected-low" simply because no network ever formed. The no-diaspora variant
# asks a different and equally fair question.
SPEC_ALLOCATION = list(DYADIC)
SPEC_STRUCTURAL = [c for c in DYADIC if c != "log_diaspora"]

# The policy-augmented benchmark, and the deliberate choice behind it. The
# predictiveness analysis showed consular presence and legal channels predict
# flows strongly (consulate z=+3.1, agreements z=+11 with destination FE), and
# the decision taken was to put them IN the benchmark: a corridor that already
# has the visa capacity and the legal channel, and still runs below what those
# predict, is the underserved corridor worth surfacing. The alternative -
# keeping levers out so missing infrastructure reads as friction - is served by
# the allocation gap, which is retained alongside.
#
# Fit on the Africa-EU panel only, because the policy variables (consular
# registers, the agreements file, ICMPD) exist only for these 1,458 dyads.
# comcol is excluded: nil predictive power in every view.
POLICY_VARS = ["visa_consulate_present", "vfs_present", "dest_embassy_in_orig",
               "neighbour_embassy", "has_any_agreement", "n_eu_instruments"]
SPEC_POLICY = ([c for c in DYADIC if c != "comcol"]
               + ["language_proximity"] + POLICY_VARS)


# --------------------------------------------------------------------------- #
# fixed-effect absorption
# --------------------------------------------------------------------------- #
def _wgroup_demean(v: np.ndarray, w: np.ndarray, codes: np.ndarray, n: int) -> np.ndarray:
    wsum = np.bincount(codes, weights=w, minlength=n)
    vsum = np.bincount(codes, weights=w * v, minlength=n)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = np.where(wsum > 0, vsum / np.maximum(wsum, 1e-12), 0.0)
    return v - mean[codes]


def absorb(M: np.ndarray, w: np.ndarray, groups: list[tuple[np.ndarray, int]],
           tol: float = 1e-12, maxit: int = 1000) -> np.ndarray:
    """Alternating weighted projections: strip all group means from each column."""
    M = np.asarray(M, dtype=float)
    single = M.ndim == 1          # decided BEFORE reshaping, or a 1-D vector
    A = (M[:, None] if single else M).copy()   # comes back 2-D and poisons w
    for j in range(A.shape[1]):
        v = A[:, j]
        for _ in range(maxit):
            prev = v.copy()
            for codes, n in groups:
                v = _wgroup_demean(v, w, codes, n)
            if np.max(np.abs(v - prev)) < tol * max(1.0, np.max(np.abs(v))):
                break
        A[:, j] = v
    return A[:, 0] if single else A


def ppml_hdfe(y: np.ndarray, X: np.ndarray, groups: list[tuple[np.ndarray, int]],
              cluster: np.ndarray | None = None, maxit: int = 200, tol: float = 1e-12):
    """Poisson PML with absorbed fixed effects. Returns (beta, se, mu, info)."""
    y = np.asarray(y, dtype=float)
    X = np.asarray(X, dtype=float)
    n, k = X.shape

    mu = (y + y.mean()) / 2.0
    mu = np.maximum(mu, 1e-6)
    eta = np.log(mu)
    beta = np.zeros(k)
    dev_old = np.inf

    for it in range(maxit):
        w = mu
        z = eta + (y - mu) / mu

        zt = absorb(z, w, groups)
        Xt = absorb(X, w, groups)

        WX = Xt * w[:, None]
        XtWX = Xt.T @ WX
        XtWz = WX.T @ zt
        beta = np.linalg.solve(XtWX + 1e-10 * np.eye(k), XtWz)

        # recover the fixed-effect part so eta is the full linear predictor
        r = z - X @ beta
        fe = r - absorb(r, w, groups)
        eta = np.clip(X @ beta + fe, -30, 30)
        mu = np.exp(eta)

        dev = 2.0 * np.sum(np.where(y > 0, y * np.log(np.maximum(y, 1e-12) / mu), 0.0)
                           - (y - mu))
        if abs(dev_old - dev) < tol * (abs(dev) + 1e-8):
            break
        dev_old = dev

    # cluster-robust sandwich on the absorbed design
    w = mu
    Xt = absorb(X, w, groups)
    bread = np.linalg.inv(Xt.T @ (Xt * w[:, None]) + 1e-10 * np.eye(k))
    u = (y - mu)[:, None] * Xt
    if cluster is not None:
        meat = np.zeros((k, k))
        for g in np.unique(cluster):
            s = u[cluster == g].sum(axis=0)
            meat += np.outer(s, s)
    else:
        meat = u.T @ u
    V = bread @ meat @ bread
    se = np.sqrt(np.maximum(np.diag(V), 0))
    return beta, se, mu, {"iterations": it + 1, "deviance": dev}


# --------------------------------------------------------------------------- #
def _design(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["log_dist"] = np.log(d["dist"].clip(lower=1))
    d["log_diaspora"] = np.log1p(d["diaspora"].clip(lower=0))
    d["log_routes"] = np.log1p(d["routes"].clip(lower=0))
    d["log_forced"] = np.log1p(d.get("forced_total", pd.Series(0.0, index=d.index)).clip(lower=0))
    if "ov_battle_deaths" in d.columns:
        d["log_ov_battle_deaths"] = np.log1p(d["ov_battle_deaths"].clip(lower=0))
    for c in ("ov_gni_pc_ppp", "ov_population"):
        if c in d.columns:
            d["log_" + c] = np.log(d[c].clip(lower=1))
    return d


def _codes(series: pd.Series) -> tuple[np.ndarray, int]:
    c = pd.Categorical(series)
    return c.codes.astype(np.intp), len(c.categories)


def _drop_separated(d: pd.DataFrame, keys: list[list[str]]) -> pd.DataFrame:
    """Drop groups whose outcome is zero throughout.

    Their fixed effect is minus infinity, so the model 'expects' zero and the
    corridor shows no anomaly - which is exactly backwards for our purpose. They
    are not identified and standard PPML practice is to remove them.
    """
    out = d
    for k in keys:
        tot = out.groupby(k).permits.transform("sum")
        out = out[tot > 0]
    return out


def fit(panel: pd.DataFrame, spec: list[str], fe_keys: list[list[str]],
        label: str) -> dict:
    d = _design(panel).dropna(subset=spec + ["permits"])
    d = _drop_separated(d, fe_keys)
    y = d["permits"].to_numpy(float)
    X = d[spec].to_numpy(float)
    groups, names = [], []
    for k in fe_keys:
        key = d[k[0]].astype(str)
        for extra in k[1:]:
            key = key + "_" + d[extra].astype(str)
        groups.append(_codes(key))
        names.append("x".join(k))
    clus = pd.Categorical(d["orig"].astype(str) + "_" + d["dest"].astype(str)).codes

    beta, se, mu, info = ppml_hdfe(y, X, groups, cluster=clus)

    fe_desc = "  ".join(f"{n} FE={g[1]:,}" for n, g in zip(names, groups))
    print(f"    [{label}] n={len(d):,}  {fe_desc}  iters={info['iterations']}")
    print(f"      {'regressor':<28}{'coef':>10}{'se':>9}{'z':>7}")
    for name, b, s in zip(spec, beta, se):
        z = b / s if s > 0 else np.nan
        star = "***" if abs(z) > 2.58 else "**" if abs(z) > 1.96 else "*" if abs(z) > 1.64 else ""
        print(f"      {name:<28}{b:>10.3f}{s:>9.3f}{z:>7.1f} {star}")

    return {"beta": beta, "se": se, "spec": spec, "fe_keys": fe_keys,
            "fitted": d.assign(predicted=mu), "label": label}


def _fe_tables(res: dict) -> list[pd.Series]:
    """Split the absorbed effect back into one table per fixed-effect dimension."""
    d = res["fitted"]
    lin = d[res["spec"]].to_numpy(float) @ res["beta"]
    fe = np.log(np.maximum(d["predicted"].to_numpy(float), 1e-12)) - lin
    t = d.assign(_fe=fe)
    rem = t["_fe"].to_numpy(float).copy()
    tables = [None] * len(res["fe_keys"])
    idx = [t.set_index(k).index for k in res["fe_keys"]]

    # One pass of group means does not separate the dimensions on an unbalanced
    # panel - it leaves a remainder of ~0.25 in log points, which is a 28% error
    # in predicted flows. Alternating projections until the remainder vanishes.
    for _ in range(200):
        for j, k in enumerate(res["fe_keys"]):
            adj = t.assign(_r=rem).groupby(k)._r.mean()
            tables[j] = adj if tables[j] is None else tables[j].add(adj, fill_value=0)
            rem = rem - idx[j].map(adj).to_numpy(float)
        if np.max(np.abs(rem)) < 1e-10:
            break

    err = np.max(np.abs(rem))
    print(f"      fixed-effect reconstruction max error: {err:.2e}"
          f"{'  (additive, as expected)' if err < 1e-6 else '  <-- CHECK'}")
    return tables


def _predict(res: dict, corr: pd.DataFrame, years: list[int]) -> np.ndarray:
    tables = _fe_tables(res)
    d = _design(corr)
    lin = d[res["spec"]].to_numpy(float) @ res["beta"]
    total = lin.copy()
    for k, tab in zip(res["fe_keys"], tables):
        others = [c for c in k if c != "year"]
        if not others:
            # a pure year effect is a single number per year: average the window
            total = total + float(tab[tab.index.isin(years)].mean())
        elif "year" in k:
            # average recent years so the benchmark is not hostage to one year
            base = others[0]
            m = tab[tab.index.get_level_values("year").isin(years)].groupby(base).mean()
            total = total + np.nan_to_num(d[base].map(m).to_numpy(float))
        else:
            total = total + np.nan_to_num(d[k[0]].map(tab).to_numpy(float))
    return np.exp(np.clip(total, -30, 30))


def main() -> None:
    step("loading estimation panel")
    panel = load("panel_global", INTERIM)
    corr = load("corridors_base", INTERIM)
    ymax = int(panel.year.max())
    years = [ymax, ymax - 1, ymax - 2]

    step("fitting PPML")
    alloc = fit(panel, SPEC_ALLOCATION, [["orig", "year"], ["dest", "year"]],
                "allocation: origin-year + dest-year FE")
    print()
    absol = fit(panel, SPEC_ABSOLUTE_DYADIC + ORIGIN_COVARS, [["dest", "year"], ["year"]],
                "absolute: dest-year FE + origin fundamentals")
    print()
    struct = fit(panel, SPEC_STRUCTURAL, [["orig", "year"], ["dest", "year"]],
                 "allocation, no diaspora")
    print()
    # Africa-EU corridor-years with the policy columns joined on. The policy
    # variables are time-invariant, so they merge cleanly onto the panel and
    # stay identified because they vary within an origin across destinations.
    pol_cols = ["orig", "dest", "language_proximity"] + POLICY_VARS
    africa = (panel[panel.orig.isin(corr.orig)]
              .merge(corr[pol_cols], on=["orig", "dest"], how="inner"))
    policy = fit(africa, SPEC_POLICY, [["orig", "year"], ["dest", "year"]],
                 "policy-augmented, Africa-EU only")

    step("predicting expected flows for all 1,458 corridors")
    out = corr[["orig", "dest"]].copy()
    actual = corr["permits_mean3"].to_numpy(float)
    out["actual_mean3"] = actual

    # ALLOCATION is the headline. The absolute spec is kept as a diagnostic but
    # must not drive the ranking: its destination effects are identified off each
    # member state's total third-country intake, and Poland's is dominated by
    # Ukrainian and Belarusian flows that have nothing to do with African
    # corridors. Extrapolating that volume to Chad or South Sudan produces
    # predictions of well over a thousand permits a year that no policy could
    # deliver, and they would swamp the ranking.
    for res, tag in ((alloc, ""), (absol, "_absolute"), (struct, "_structural"),
                     (policy, "_policy")):
        pred = _predict(res, corr, years)
        out[f"predicted{tag}"] = pred
        out[f"gap_log{tag}"] = np.log((actual + 0.5) / (pred + 0.5))

    # Origins the policy fit dropped (zero flows to every EU state) have no
    # policy-spec fixed effect; their "prediction" would be built on a silent
    # zero. NaN them so the scoring layer falls back to the allocation gap.
    live_policy = set(policy["fitted"].orig.unique())
    dead = ~out.orig.isin(live_policy)
    out.loc[dead, ["predicted_policy", "gap_log_policy"]] = np.nan

    # Origins dropped from the allocation fit send no recorded employment
    # migration to ANY EU state, so that spec has nothing to say about them. Flag
    # rather than silently scoring them as "exactly as expected".
    live = set(alloc["fitted"].orig.unique())
    out["origin_in_allocation_fit"] = out.orig.isin(live).astype(int)

    save(out, "gravity_predictions", INTERIM)

    # Persist the fitted coefficients so the method page can show what the model
    # actually estimated rather than a hand-written description that drifts.
    d = _design(panel).dropna(subset=alloc["spec"] + ["permits"])
    d = _drop_separated(d, alloc["fe_keys"])
    coef = pd.DataFrame({
        "regressor": alloc["spec"],
        "coef": alloc["beta"],
        "se": alloc["se"],
        "sd_x": [d[c].std() for c in alloc["spec"]],
    })
    coef["z"] = coef.coef / coef.se.replace(0, np.nan)
    coef["std_effect"] = (coef.coef * coef.sd_x).abs()
    coef["share"] = coef.std_effect / coef.std_effect.sum()
    coef["n_obs"] = len(d)
    coef["n_origins"] = int(d.orig.nunique())
    coef["zero_share"] = float((d.permits == 0).mean())
    save(coef.sort_values("std_effect", ascending=False), "gravity_coefficients", INTERIM)
    if (out.origin_in_allocation_fit == 0).any():
        miss = sorted(out.loc[out.origin_in_allocation_fit == 0, "orig"].unique())
        print(f"    origins with no recorded EU employment flow at all: {miss}")

    print(f"\n    actual (3-yr mean) {actual.sum():,.0f} vs "
          f"predicted {out.predicted.sum():,.0f}")
    print(f"    corridors below prediction: {(out.gap_log < 0).sum():,} / {len(out):,}")

    nm = dict(zip(corr.orig, corr.orig_name))
    nd = dict(zip(corr.dest, corr.dest_name))
    big = out[out.predicted > 40].nsmallest(12, "gap_log")
    print("\n    largest shortfalls vs prediction (predicted > 40/yr):")
    print(f"      {'corridor':<36}{'actual':>8}{'predicted':>11}{'gap':>7}")
    for r in big.itertuples():
        pair = f"{nm.get(r.orig, r.orig)} -> {nd.get(r.dest, r.dest)}"
        print(f"      {pair:<36}{r.actual_mean3:>8,.0f}{r.predicted:>11,.0f}"
              f"{r.gap_log:>7.2f}")


def validate() -> None:
    """Check the HDFE routine against a dense-dummy Poisson GLM on a subsample."""
    import statsmodels.api as sm

    panel = load("panel_global", INTERIM)
    sub = panel[panel.dest.isin(["DEU", "FRA", "ITA", "ESP"]) & (panel.year >= 2022)]
    d = _design(sub).dropna(subset=DYADIC + ["permits"])
    # all-zero fixed-effect groups are unidentified and make the dense reference
    # GLM diverge, so both estimators must see the same identified sample
    d = _drop_separated(d, [["orig", "year"], ["dest", "year"]])
    spec = ["log_dist", "comlang_off", "colony", "log_diaspora", "log_routes"]

    g1 = _codes(d["orig"].astype(str) + "_" + d["year"].astype(str))
    g2 = _codes(d["dest"].astype(str) + "_" + d["year"].astype(str))
    b_mine, se_mine, _, info = ppml_hdfe(d["permits"].to_numpy(float),
                                         d[spec].to_numpy(float), [g1, g2])

    # Keep every level of the first FE dimension (it spans the intercept) and drop
    # one level of the second. Dropping a level from BOTH with no constant term
    # estimates a different model and is what made the first comparison disagree.
    oy = pd.get_dummies(d["orig"].astype(str) + "_" + d["year"].astype(str),
                        drop_first=False).astype(float)
    dy = pd.get_dummies(d["dest"].astype(str) + "_" + d["year"].astype(str),
                        drop_first=True).astype(float)
    Xd = np.column_stack([d[spec].to_numpy(float), oy.to_numpy(), dy.to_numpy()])
    ref = sm.GLM(d["permits"].to_numpy(float), Xd,
                 family=sm.families.Poisson()).fit(maxiter=200)

    print(f"  validation on n={len(d):,} ({info['iterations']} HDFE iterations)")
    print(f"  {'regressor':<16}{'HDFE':>10}{'dense GLM':>12}{'abs diff':>11}")
    ok = True
    for i, name in enumerate(spec):
        diff = abs(b_mine[i] - ref.params[i])
        ok &= diff < 1e-4
        print(f"  {name:<16}{b_mine[i]:>10.5f}{ref.params[i]:>12.5f}{diff:>11.2e}")
    print(f"  -> {'MATCH' if ok else 'MISMATCH - do not trust the estimates'}")


if __name__ == "__main__":
    if "--validate" in sys.argv:
        validate()
    else:
        main()
