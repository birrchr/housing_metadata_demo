"""Build the housing metadata DuckDB database.

Steps
  1. (Re)create reference + mapping tables from metadata/*.csv
  2. Discover Parquet files under data/raw/year=YYYY/province=XX/
  3. For each file: register it, record its columns + mappings, and
     unpivot every mapped column into meta.record_value (recoding as needed)
  4. Create the generic views and print the QA summary
  5. Optionally export every table to Parquet (for Fabric / Delta pickup)

There is no province-specific code here: everything province-specific is a
row in source_column_map / source_code_map.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
PARTITION_RE = re.compile(r"year=(\d{4})[/\\]province=([A-Za-z]+)")

REFERENCE_TABLES = [  # load order respects foreign keys
    "codeset",
    "code",
    "variable",
    "source_column_map",
    "source_code_map",
]


def run_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text())


def load_reference(con: duckdb.DuckDBPyConnection, meta_dir: Path) -> None:
    for table in REFERENCE_TABLES:
        csv = meta_dir / f"{table}.csv"
        # all_varchar keeps codes like '01' intact; DuckDB casts to target types on insert
        con.execute(
            f"INSERT INTO meta.{table} BY NAME "
            f"SELECT * FROM read_csv(?, header=true, all_varchar=true)",
            [str(csv)],
        )
        n = con.execute(f"SELECT count(*) FROM meta.{table}").fetchone()[0]
        print(f"  meta.{table:<18} {n:>5} rows")


def discover(raw_dir: Path, years: set[int] | None) -> list[tuple[str, int, Path]]:
    found = []
    for f in sorted(raw_dir.rglob("*.parquet")):
        m = PARTITION_RE.search(f.as_posix())
        if not m:
            print(f"  skip (no year=/province= in path): {f}")
            continue
        year, prov = int(m.group(1)), m.group(2).upper()
        if years is None or year in years:
            found.append((prov, year, f))
    return found


def load_dataset(con: duckdb.DuckDBPyConnection, prov: str, year: int, path: Path) -> None:
    dataset_id = f"{prov}_{year}"

    # Idempotent per dataset: annual re-runs replace just that province-year
    for t in ("record_value", "dataset_column", "dataset"):
        con.execute(f"DELETE FROM meta.{t} WHERE dataset_id = ?", [dataset_id])

    src = f"read_parquet('{path.as_posix()}', hive_partitioning = false)"
    n_rows = con.execute(f"SELECT count(*) FROM {src}").fetchone()[0]
    con.execute(
        "INSERT INTO meta.dataset (dataset_id, source_system, reference_year, source_path, record_count) "
        "VALUES (?, ?, ?, ?, ?)",
        [dataset_id, prov, year, path.as_posix(), n_rows],
    )

    # 1. Lineage: every physical column + the variable it maps to for this year
    con.execute(
        f"""
        INSERT INTO meta.dataset_column
        SELECT ?, d.column_name, d.column_type, m.variable_id
        FROM (DESCRIBE SELECT * FROM {src}) d
        LEFT JOIN meta.source_column_map m
          ON m.source_system = ? AND m.source_column = d.column_name
         AND ? BETWEEN m.valid_from_year AND coalesce(m.valid_to_year, 9999)
        """,
        [dataset_id, prov, year],
    )

    mapped = con.execute(
        """
        SELECT dc.source_column, dc.variable_id, v.role
        FROM meta.dataset_column dc JOIN meta.variable v USING (variable_id)
        WHERE dc.dataset_id = ?
        """,
        [dataset_id],
    ).fetchall()

    keys = [c for c, _, role in mapped if role == "key"]
    if len(keys) > 1:
        raise ValueError(f"{dataset_id}: expected exactly one key column, found {keys}")
    if not keys:
        # Typically a new province with no mappings yet. Keep the dataset and its
        # columns registered so the QA views report exactly what to map, rather
        # than failing the whole build.
        print(f"  {dataset_id:<8} {n_rows:>6} records  no key column mapped - values not loaded (see QA)")
        return
    key_col = keys[0]
    attrs = [(c, v) for c, v, role in mapped if role == "attribute"]

    # 2. Rename to canonical ids, everything as VARCHAR, then unpivot to long
    select_list = ", ".join(f'CAST("{c}" AS VARCHAR) AS "{v}"' for c, v in attrs)
    con.execute(
        f"""
        INSERT INTO meta.record_value
        WITH wide AS (
            SELECT CAST("{key_col}" AS VARCHAR) AS record_id, {select_list}
            FROM {src}
        ),
        long AS (
            UNPIVOT wide ON COLUMNS(* EXCLUDE (record_id))
            INTO NAME variable_id VALUE source_value
        ),
        has_map AS (
            SELECT DISTINCT variable_id FROM meta.source_code_map
            WHERE source_system = $prov
              AND $year BETWEEN valid_from_year AND coalesce(valid_to_year, 9999)
        )
        SELECT
            $ds,
            l.record_id,
            l.variable_id,
            l.source_value,
            CASE WHEN h.variable_id IS NULL THEN l.source_value ELSE m.code END AS value,
            CASE WHEN h.variable_id IS NULL THEN 'passthrough' ELSE 'code_map' END AS mapped_via
        FROM long l
        LEFT JOIN has_map h USING (variable_id)
        LEFT JOIN meta.source_code_map m
          ON m.source_system = $prov
         AND m.variable_id   = l.variable_id
         AND m.source_value  = l.source_value
         AND $year BETWEEN m.valid_from_year AND coalesce(m.valid_to_year, 9999)
        """,
        {"ds": dataset_id, "prov": prov, "year": year},
    )
    n_vals = con.execute(
        "SELECT count(*) FROM meta.record_value WHERE dataset_id = ?", [dataset_id]
    ).fetchone()[0]
    print(f"  {dataset_id:<8} {n_rows:>6} records  {len(attrs):>2} variables  {n_vals:>7} values")


def build_wide(con: duckdb.DuckDBPyConnection) -> None:
    """Analysis-ready table: one row per record, canonical column names, typed."""
    vars_ = con.execute(
        "SELECT variable_id, variable_name, data_type FROM meta.variable "
        "WHERE role = 'attribute' ORDER BY variable_id"
    ).fetchall()
    cols = ",\n  ".join(
        f"try_cast(max(value) FILTER (WHERE variable_id = '{vid}') AS {dtype}) AS {name}"
        for vid, name, dtype in vars_
    )
    con.execute(
        f"""
        CREATE OR REPLACE TABLE meta.household_harmonised AS
        SELECT d.reference_year, d.source_system, r.dataset_id, r.record_id,
          {cols}
        FROM meta.record_value r JOIN meta.dataset d USING (dataset_id)
        GROUP BY ALL
        """
    )


def export(con: duckdb.DuckDBPyConnection, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = [r[0] for r in con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE schema_name = 'meta'"
    ).fetchall()]
    for t in tables:
        con.execute(f"COPY meta.{t} TO '{(out_dir / t).as_posix()}.parquet' (FORMAT parquet)")
    print(f"  exported {len(tables)} tables to {out_dir}")


def build(
    con: duckdb.DuckDBPyConnection,
    meta_dir: Path,
    raw_dir: Path,
    years: set[int] | None = None,
) -> None:
    """Run the whole pipeline against an open connection (file or in-memory)."""
    print("Reference metadata")
    run_sql_file(con, ROOT / "sql" / "01_schema.sql")
    load_reference(con, Path(meta_dir))

    print("Datasets")
    files = discover(Path(raw_dir), years)
    if not files:
        print("  no parquet files found - run generate_sample first")
    for prov, year, path in files:
        load_dataset(con, prov, year, path)

    run_sql_file(con, ROOT / "sql" / "02_views.sql")
    build_wide(con)

    print("QA summary")
    for name, n in con.execute("SELECT * FROM meta.v_qa_summary").fetchall():
        print(f"  {'OK ' if n == 0 else '!! '} {name:<18} {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(ROOT / "housing_meta.duckdb"))
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--metadata", default=str(ROOT / "metadata"))
    ap.add_argument("--year", type=int, nargs="*", help="Only (re)load these reference years")
    ap.add_argument("--export", metavar="DIR", help="Write every table to Parquet in DIR")
    args = ap.parse_args()

    con = duckdb.connect(args.db)
    build(con, Path(args.metadata), Path(args.raw), set(args.year) if args.year else None)

    if args.export:
        export(con, Path(args.export))
    con.close()


if __name__ == "__main__":
    main()
