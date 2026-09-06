"""What route does an applicant on this corridor actually have?

Built because a challenge to the consular scoring found that 21 corridors were
scored as having almost no access while moving 1,398 permits a year between
them - Mauritius-Luxembourg at 172/yr scoring 6, Nigeria-Malta at 151/yr
scoring 17. The tool modelled a destination's OWN posts and nothing else, so it
was blind to the two mechanisms small member states actually use: outsourcing
to VFS/TLS/BLS, and Article 8 representation, where another Schengen state
receives applications on their behalf.

The register classifies every corridor into one route, best first:

  own_post_vfs     the destination's own visa post in country, plus an
                   outsourced centre taking biometrics
  own_post         the destination's own visa post in country
  vfs_only         an outsourced centre with no own post behind it; the file is
                   decided remotely
  represented_d    another member state receives the application AND that
                   arrangement covers long-stay type D - the visa an incoming
                   worker actually needs
  represented_c    another member state receives, but for short-stay type C
                   only; a work applicant still has to travel
  neighbour_post   nearest own post is in a land neighbour
  regional_post    nearest own post is further away
  none             no route identified

The type C / type D split is the point of the exercise. Article 8 representation
is overwhelmingly a short-stay mechanism, so for LABOUR migration most
representation is close to worthless - the applicant still travels. Luxembourg
is the documented exception: its long-stay applications are handled by Belgian
(and Dutch) posts under the Benelux arrangement. Whether that distinction shows
up in the flow data is exactly what regress() tests.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))
from _common import INTERIM, REF, load, save, step  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROUTE_ORDER = ["own_post_vfs", "own_post", "vfs_only", "represented_d",
               "represented_c", "neighbour_post", "regional_post", "none"]

# Kept in ROUTE_ORDER for completeness, but in practice "none" never occurs:
# every corridor has at least a regional post to travel to.


def build() -> pd.DataFrame:
    """Read the route classification out of the panel.

    assemble.py owns the classification now - it has the consular, VFS and
    representation inputs in hand while building the corridor table, and
    indices.py scores f_consular from the result. This function only reshapes
    it for reporting, so there is one source of truth rather than two
    implementations that can drift.
    """
    df = load("corridors_scored", INTERIM)
    cols = ["orig", "dest", "orig_name", "dest_name", "visa_route",
            "route_covers_long_stay", "represented_by", "vfs_operator",
            "nearest_embassy_km", "permits_mean3", "f_consular"]
    reg = df[[c for c in cols if c in df.columns]].copy()
    return reg.rename(columns={"visa_route": "route",
                               "route_covers_long_stay": "covers_long_stay",
                               "vfs_operator": "operator",
                               "nearest_embassy_km": "nearest_post_km"})


def regress(reg: pd.DataFrame) -> None:
    """Do the route types actually differ in the flows they carry?"""
    import statsmodels.formula.api as smf

    df = load("corridors_scored", INTERIM)
    d = df.merge(reg[["orig", "dest", "route", "covers_long_stay"]],
                 on=["orig", "dest"], how="left")
    d["lf"] = np.log1p(d.permits_mean3.fillna(0))
    d["ldist"] = np.log(pd.to_numeric(d.dist, errors="coerce").clip(lower=1))
    # Only categories that actually occur, so the empty "none" level cannot
    # emit a meaningless coefficient.
    present = [c for c in ROUTE_ORDER if (d.route == c).any()]
    d["route"] = pd.Categorical(d.route, categories=present)

    print("\n  route composition and raw flows:")
    g = d.groupby("route", observed=True).agg(
        corridors=("lf", "size"), total_permits=("permits_mean3", "sum"),
        median=("permits_mean3", "median"), pct_zero=("permits_mean3",
                                                      lambda s: (s.fillna(0) == 0).mean()))
    for k, r in g.iterrows():
        print(f"    {k:<16}{int(r.corridors):>5} corridors  "
              f"{r.total_permits:>9,.0f}/yr total  median {r['median']:>6.1f}  "
              f"{r.pct_zero:>4.0%} zero")

    # OLS with origin and destination effects: every corridor weighted equally,
    # so a handful of employer-sponsored pipelines cannot drive the estimate
    # the way they did when this was first tried in count space.
    # Baseline is regional_post, not "none": every corridor turns out to have
    # SOME route, so the none category is empty and its dummies would be
    # perfectly collinear with the intercept - the level estimates against it
    # are not identified. regional_post, travelling to a post outside the
    # region, is the worst real option and the honest comparator.
    m = smf.ols("lf ~ C(orig) + C(dest) + ldist + colony + comlang_off"
                " + C(route, Treatment('regional_post'))", data=d).fit()
    print("\n  OLS on log flow, origin + destination effects,"
          " baseline = regional_post (travel outside the region):")
    for k, v in m.params.items():
        if "route" in k:
            se = m.bse[k]
            name = k.split("T.")[-1].rstrip("]")
            stars = "***" if abs(v / se) > 2.58 else "**" if abs(v / se) > 1.96 else ""
            print(f"    {name:<18}{v:>+7.2f}  (se {se:.2f}, t {v / se:>+5.1f}) {stars}")
    print(f"    n = {int(m.nobs)}, R2 = {m.rsquared:.3f}")

    # The question the register was built to answer.
    print("\n  does long-stay coverage matter, holding route quality aside?")
    sub = d[d.route.isin(["represented_d", "represented_c", "neighbour_post"])]
    for k, gg in sub.groupby("route", observed=True):
        print(f"    {k:<16}mean log flow {gg.lf.mean():.2f}  "
              f"median {gg.permits_mean3.median():.1f}/yr  n={len(gg)}")


def main() -> None:
    step("visa access routes")
    reg = build()
    save(reg, "visa_access_routes", INTERIM)
    reg.to_csv(REF / "visa_access_routes.csv", index=False)
    print(f"    {len(reg)} corridors classified -> ref/visa_access_routes.csv")
    print("    " + ", ".join(f"{k} {v}" for k, v in
                             reg.route.value_counts().items()))
    step("regressions on route type")
    regress(reg)


if __name__ == "__main__":
    main()
