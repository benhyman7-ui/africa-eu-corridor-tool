"""Build the whole tool, end to end.

    python build.py            full run (HTTP cached, so a rerun is fast)
    python build.py --refresh  ignore the HTTP cache and refetch everything
    python build.py --model    skip fetching, re-run model and export only
    python build.py --serve    build, then serve web/ on localhost:8000

Every fetcher writes to data/cache first, so the second run of this script does
no network I/O at all.
"""
from __future__ import annotations

import runpy
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPELINE = ROOT / "pipeline"
MODEL = ROOT / "model"

FETCHERS = [
    ("build_countries", "country code spine"),
    ("eurostat_permits", "bilateral permit flows"),
    ("eurostat_context", "diaspora and destination labour demand"),
    ("worldbank", "income, demography, governance"),
    ("cepii", "distance, language, colonial ties"),
    ("openflights", "air connectivity"),
    ("wikidata_embassies", "consular presence"),
    ("factbook", "religion and language profiles"),
    ("hofstede", "cultural dimensions"),
    ("skills_supply", "origin skills: UIS graduates by field, WHO health workforce"),
    ("icmpd_pathways", "ICMPD/ECDPM legal pathway roster"),
    ("conflict_refugees", "UNHCR bilateral stocks (+ UCDP where reachable)"),
    ("schengen_visas", "visa refusal rates and consulate presence"),
    ("pip_income", "median income"),
    ("eures_shortages", "EURES widespread shortage occupations"),
    ("geometry", "country outlines for the map"),
]
STAGES = [
    ("assemble", "corridor panel"),
    ("gravity", "PPML gravity model"),
    ("indices", "scores"),
    ("export", "web payloads"),
]


def run(folder: Path, module: str, label: str) -> bool:
    print(f"\n\033[1m>> {module}\033[0m  ({label})")
    t0 = time.time()
    sys.path.insert(0, str(folder))
    try:
        runpy.run_path(str(folder / f"{module}.py"), run_name="__main__")
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001
        print(f"   FAILED: {type(exc).__name__}: {exc}")
        return False
    finally:
        sys.path.remove(str(folder))
    print(f"   done in {time.time() - t0:.1f}s")
    return True


def main() -> None:
    args = set(sys.argv[1:])

    if "--refresh" in args:
        cache = ROOT / "data" / "cache"
        if cache.exists():
            shutil.rmtree(cache)
            print("cleared data/cache")

    failed = []
    if "--model" not in args:
        for mod, label in FETCHERS:
            if not run(PIPELINE, mod, label):
                failed.append(mod)

    for mod, label in STAGES:
        if not run(MODEL, mod, label):
            failed.append(mod)
            break  # the stages are strictly sequential

    print("\n" + "=" * 62)
    if failed:
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)
    out = ROOT / "web" / "data"
    total = sum(f.stat().st_size for f in out.glob("*.json")) / 1024
    print(f"Build complete. web/data is {total:,.0f} KB across "
          f"{len(list(out.glob('*.json')))} files.")
    print("Serve it with:  python -m http.server -d web 8000")

    if "--serve" in args:
        print("\nserving http://localhost:8000 - ctrl-c to stop")
        subprocess.run([sys.executable, "-m", "http.server", "-d", str(ROOT / "web"), "8000"])


if __name__ == "__main__":
    main()
