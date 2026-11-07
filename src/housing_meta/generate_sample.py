"""Generate fake province Parquet files that mimic real-world inconsistency.

Each province uses its own column names and code values, and ON renames a
column in 2025. BC 2025 also contains a tenure code ('3') that has no
mapping, so the QA views have something to catch.

Layout:  data/raw/year=YYYY/province=XX/households.parquet
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

N = 200


def _on(year: int, rng: random.Random) -> pa.Table:
    cost_col = "shelter_cost_mth" if year <= 2024 else "monthly_shelter_cost"
    cols = {
        "hh_id": [f"ON{year}{i:05d}" for i in range(N)],
        "tenure_cd": rng.choices(["O", "R", "X"], [60, 38, 2], k=N),
        "dwelling": rng.choices(["SD", "SEMI", "ROW", "APT", "OTH"], [50, 8, 10, 30, 2], k=N),
        "bedrooms": [rng.randint(0, 5) for _ in range(N)],
        "hh_size": [rng.randint(1, 6) for _ in range(N)],
        cost_col: [round(rng.uniform(600, 3500), 2) for _ in range(N)],
        "chn_flag": rng.choices(["Y", "N"], [12, 88], k=N),
    }
    if year >= 2025:
        cols["yr_built"] = rng.choices(["PRE1980", "1980-1999", "2000+"], k=N)
    return pa.table(cols)


def _bc(year: int, rng: random.Random) -> pa.Table:
    ten_vals, ten_w = ([1, 2], [55, 45]) if year <= 2024 else ([1, 2, 3], [54, 44, 2])
    cols = {
        "HHID": list(range(year * 100000, year * 100000 + N)),        # integer keys
        "TEN": rng.choices(ten_vals, ten_w, k=N),
        "DTYPE": rng.choices([10, 20, 30, 99], [40, 15, 43, 2], k=N),
        "BEDRMS": [rng.randint(0, 5) for _ in range(N)],
        "HHSIZE": [rng.randint(1, 6) for _ in range(N)],
        "SHLCO": [round(rng.uniform(800, 4200), 2) for _ in range(N)],
        "CHN": rng.choices([1, 2], [15, 85], k=N),
        "REGION_INTERNAL": rng.choices(["LM", "VI", "INT"], k=N),     # unmapped on purpose
    }
    if year >= 2025:
        cols["PERIOD"] = rng.choices(["1", "2", "3"], k=N)            # already canonical
    return pa.table(cols)


def _ns(year: int, rng: random.Random) -> pa.Table:
    # NS already delivers canonical codes -> no source_code_map rows needed
    cols = {
        "household_id": [f"NS-{year}-{i}" for i in range(N)],
        "tenure": rng.choices(["1", "2", "9"], [65, 33, 2], k=N),
        "dwelling_type": rng.choices(["1", "2", "3", "9"], [60, 12, 26, 2], k=N),
        "bedrooms": [rng.randint(0, 5) for _ in range(N)],
        "household_size": [rng.randint(1, 6) for _ in range(N)],
        "monthly_shelter_cost": [round(rng.uniform(500, 2600), 2) for _ in range(N)],
        "core_housing_need": rng.choices(["1", "0"], [10, 90], k=N),
    }
    if year >= 2025:
        cols["period_built"] = rng.choices(["1", "2", "3"], k=N)
    return pa.table(cols)


BUILDERS = {"ON": _on, "BC": _bc, "NS": _ns}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--years", nargs="+", type=int, default=[2024, 2025])
    args = ap.parse_args()

    for year in args.years:
        for prov, build in BUILDERS.items():
            rng = random.Random(f"{prov}{year}")
            path = Path(args.out) / f"year={year}" / f"province={prov}" / "households.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(build(year, rng), path)
            print(f"wrote {path}")


if __name__ == "__main__":
    main()
