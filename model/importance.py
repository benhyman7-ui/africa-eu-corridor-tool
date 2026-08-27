"""What is the model actually hinging on?

Two separate questions, answered separately because they have different answers.

1. GRAVITY: which predictors move expected flows? Reported as the standardised
   effect - the coefficient times the standard deviation of that regressor -
   which is what a one-standard-deviation move is worth in log permits. Raw
   coefficients are not comparable across variables on different scales.

2. RANKING: which components move the final ordering? Two views. Correlation of
   each component with the composite tells you what the score tracks; a
   drop-one test - zero a component's weight and count how much of the top 25
   changes - tells you what would actually alter a funding decision. They can
   disagree, and where they do the drop-one is the one that matters.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _common import INTERIM, load, step  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gravity as G  # noqa: E402
from indices import EASE_WEIGHTS, POTENTIAL_WEIGHTS, PRESETS  # noqa: E402


def gravity_importance() -> pd.DataFrame:
    panel = load("panel_global", INTERIM)
    res = G.fit(panel, G.SPEC_ALLOCATION, [["orig", "year"], ["dest", "year"]],
                "allocation (headline)")
    d = G._design(panel).dropna(subset=res["spec"] + ["permits"])
    d = G._drop_separated(d, res["fe_keys"])
    rows = []
    for name, b, se in zip(res["spec"], res["beta"], res["se"]):
        sd = d[name].std()
        rows.append({"regressor": name, "coef": b, "se": se,
                     "sd_of_x": sd, "std_effect": abs(b * sd),
                     "z": b / se if se else np.nan})
    out = pd.DataFrame(rows).sort_values("std_effect", ascending=False)
    out["share"] = out.std_effect / out.std_effect.sum()
    return out


def ranking_importance(preset: str = "cgd") -> tuple[pd.DataFrame, pd.DataFrame]:
    df = load("corridors_scored", INTERIM)
    w = PRESETS[preset]

    def composite(ease_w=None, pot_w=None, top_w=None):
        ew = {**EASE_WEIGHTS, **(ease_w or {})}
        pw = {**POTENTIAL_WEIGHTS, **(pot_w or {})}
        tw = {**w, **(top_w or {})}
        ease = sum(df[k] * v for k, v in ew.items()) / max(sum(ew.values()), 1e-9)
        pot = sum(df[k] * v for k, v in pw.items()) / max(sum(pw.values()), 1e-9)
        tot = (tw["underutilisation"] * df["underutilisation_score"]
               + tw["ease"] * ease + tw["potential"] * pot)
        return tot / max(sum(tw.values()), 1e-9)

    base = composite()
    base_top = set(df.assign(s=base).nlargest(25, "s").apply(
        lambda r: f"{r.orig}-{r.dest}", axis=1))

    rows = []
    # top-level dimensions
    for k in ("underutilisation", "ease", "potential"):
        alt = composite(top_w={k: 0.0})
        alt_top = set(df.assign(s=alt).nlargest(25, "s").apply(
            lambda r: f"{r.orig}-{r.dest}", axis=1))
        comp = df["underutilisation_score"] if k == "underutilisation" else (
            sum(df[c] * v for c, v in (EASE_WEIGHTS if k == "ease" else POTENTIAL_WEIGHTS).items())
            / sum((EASE_WEIGHTS if k == "ease" else POTENTIAL_WEIGHTS).values()))
        rows.append({"level": "dimension", "factor": k,
                     "weight": w[k],
                     "corr_with_score": float(np.corrcoef(comp, base)[0, 1]),
                     "top25_changed": 25 - len(base_top & alt_top)})
    # ease and potential components
    for k, v in EASE_WEIGHTS.items():
        alt = composite(ease_w={k: 0.0})
        alt_top = set(df.assign(s=alt).nlargest(25, "s").apply(
            lambda r: f"{r.orig}-{r.dest}", axis=1))
        rows.append({"level": "ease component", "factor": k, "weight": v,
                     "corr_with_score": float(np.corrcoef(df[k], base)[0, 1]),
                     "top25_changed": 25 - len(base_top & alt_top)})
    for k, v in POTENTIAL_WEIGHTS.items():
        alt = composite(pot_w={k: 0.0})
        alt_top = set(df.assign(s=alt).nlargest(25, "s").apply(
            lambda r: f"{r.orig}-{r.dest}", axis=1))
        rows.append({"level": "potential component", "factor": k, "weight": v,
                     "corr_with_score": float(np.corrcoef(df[k], base)[0, 1]),
                     "top25_changed": 25 - len(base_top & alt_top)})

    imp = pd.DataFrame(rows).sort_values(["level", "top25_changed"], ascending=[True, False])

    # what separates the leaders from everyone else
    lead = df.assign(s=base).nlargest(50, "s")
    rest = df.assign(s=base).nsmallest(len(df) - 50, "s")
    cols = list(EASE_WEIGHTS) + list(POTENTIAL_WEIGHTS) + ["underutilisation_score"]
    gap = pd.DataFrame([{
        "factor": c,
        "top50_mean": lead[c].mean(),
        "rest_mean": rest[c].mean(),
        "gap": lead[c].mean() - rest[c].mean(),
    } for c in cols]).sort_values("gap", ascending=False)
    return imp, gap


def main() -> None:
    step("gravity: standardised effect of each predictor")
    gi = gravity_importance()
    print(f"\n    {'regressor':<24}{'coef':>8}{'sd(x)':>8}{'|b*sd|':>9}{'share':>8}{'z':>7}")
    for r in gi.itertuples():
        print(f"    {r.regressor:<24}{r.coef:>8.3f}{r.sd_of_x:>8.2f}"
              f"{r.std_effect:>9.3f}{r.share:>7.0%}{r.z:>7.1f}")

    step("ranking: what moves the CGD-preset ordering")
    imp, gap = ranking_importance("cgd")
    for lvl in ["dimension", "ease component", "potential component"]:
        sub = imp[imp.level == lvl]
        print(f"\n    {lvl.upper()}")
        print(f"    {'factor':<26}{'weight':>7}{'corr':>7}{'top25 moved if removed':>25}")
        for r in sub.itertuples():
            print(f"    {r.factor:<26}{r.weight:>7.2f}{r.corr_with_score:>7.2f}"
                  f"{r.top25_changed:>21} / 25")

    step("what separates the top 50 corridors from the rest")
    print(f"    {'factor':<26}{'top50':>8}{'rest':>8}{'gap':>8}")
    for r in gap.itertuples():
        print(f"    {r.factor:<26}{r.top50_mean:>8.1f}{r.rest_mean:>8.1f}{r.gap:>8.1f}")


if __name__ == "__main__":
    main()
