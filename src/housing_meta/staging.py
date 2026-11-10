"""Export one build's results as staging Delta tables for the Fabric Warehouse.

Every row carries `run_id` (the pipeline run) and `metadata_release`, and each
table is partitioned by `run_id`. Runs append to their own partition, so two
deliveries never overwrite each other's staging data. The Warehouse procedure
reads `WHERE run_id = @run_id`.

Tables written (all under one `tables_dir`, e.g. /lakehouse/default/Tables):

  stg_dataset, stg_dataset_column, stg_record_value     the delivery itself
  stg_codeset, stg_code, stg_variable                   reference metadata (for dimensions)
  stg_metadata                                          all 5 metadata tables, one row per
                                                        metadata row (for version history)
  stg_qa_summary, stg_qa_detail                         QA results and which checks block
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

# QA checks that stop a publish. unmapped_columns only warns until the model can
# mark a column as deliberately ignored (README, known limitation 1).
BLOCKING_CHECKS = {"missing_variables", "unmapped_values", "invalid_codes", "bad_numerics"}

QA_VIEWS = {
    "unmapped_columns": "v_qa_unmapped_columns",
    "missing_variables": "v_qa_missing_variables",
    "unmapped_values": "v_qa_unmapped_values",
    "invalid_codes": "v_qa_invalid_codes",
    "bad_numerics": "v_qa_bad_numerics",
}

# Natural key of each metadata table: used to version rows in the Warehouse.
METADATA_KEYS = {
    "codeset": ["codeset_id"],
    "code": ["codeset_id", "code", "valid_from_year"],
    "variable": ["variable_id"],
    "source_column_map": ["source_system", "source_column", "valid_from_year"],
    "source_code_map": ["source_system", "variable_id", "source_value", "valid_from_year"],
}

STAGING_TABLES = [
    "stg_dataset", "stg_dataset_column", "stg_record_value",
    "stg_codeset", "stg_code", "stg_variable", "stg_metadata",
    "stg_qa_summary", "stg_qa_detail",
]


def _queries() -> dict[str, str]:
    """SELECT for each staging table, against the in-memory build."""
    q = {
        "stg_dataset": "SELECT dataset_id, source_system, reference_year, source_path, record_count FROM meta.dataset",
        "stg_dataset_column": "SELECT dataset_id, source_column, source_type, variable_id FROM meta.dataset_column",
        "stg_record_value": "SELECT dataset_id, record_id, variable_id, source_value, value, mapped_via FROM meta.record_value",
        "stg_codeset": "SELECT * FROM meta.codeset",
        "stg_code": "SELECT * FROM meta.code",
        "stg_variable": "SELECT * FROM meta.variable",
    }
    parts = []
    for table, keys in METADATA_KEYS.items():
        has_source = "source_system" in keys
        has_years = table != "codeset"
        parts.append(f"""
            SELECT '{table}' AS table_name,
                   concat_ws('|', {', '.join(f'CAST({k} AS VARCHAR)' for k in keys)}) AS row_key,
                   CAST(to_json(t) AS VARCHAR) AS row_sig,
                   {'source_system' if has_source else 'CAST(NULL AS VARCHAR)'} AS source_system,
                   {'CAST(valid_from_year AS INTEGER)' if has_years else 'CAST(NULL AS INTEGER)'} AS valid_from_year,
                   {'CAST(valid_to_year AS INTEGER)' if has_years else 'CAST(NULL AS INTEGER)'} AS valid_to_year
            FROM meta.{table} t""")
    q["stg_metadata"] = " UNION ALL ".join(parts)
    q["stg_qa_summary"] = " UNION ALL ".join(
        f"SELECT '{name}' AS check_name, CAST(count(*) AS INTEGER) AS n_issues, "
        f"{'true' if name in BLOCKING_CHECKS else 'false'} AS is_blocking FROM meta.{view}"
        for name, view in QA_VIEWS.items()
    )
    q["stg_qa_detail"] = " UNION ALL ".join(
        f"SELECT '{name}' AS check_name, dataset_id, CAST(to_json(t) AS VARCHAR) AS detail FROM meta.{view} t"
        for name, view in QA_VIEWS.items()
    )
    return q


def _arrow(rel):
    """Arrow table from a DuckDB relation: `to_arrow_table` (DuckDB 2.x) or `fetch_arrow_table` (1.x)."""
    fn = getattr(rel, "to_arrow_table", None) or rel.fetch_arrow_table
    return fn()


def _lit(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def qa_summary(con: duckdb.DuckDBPyConnection) -> list[dict]:
    rows = con.execute(_queries()["stg_qa_summary"]).fetchall()
    return [{"check": c, "n_issues": n, "blocking": b} for c, n, b in rows]


def export_staging(
    con: duckdb.DuckDBPyConnection,
    tables_dir: str | Path,
    run_id: str,
    metadata_release: str,
) -> dict[str, int]:
    """Append this build's results to the staging Delta tables. Returns row counts."""
    from deltalake import write_deltalake  # optional dependency: only needed for Fabric

    staged_at = datetime.now(timezone.utc)
    counts = {}
    for table, sql in _queries().items():
        tbl = _arrow(con.sql(
            f"SELECT *, {_lit(run_id)}::VARCHAR AS run_id, "
            f"{_lit(metadata_release)}::VARCHAR AS metadata_release, "
            f"{_lit(staged_at.isoformat())}::TIMESTAMPTZ AS staged_at "
            f"FROM ({sql})"
        ))
        write_deltalake(
            f"{Path(tables_dir).as_posix()}/{table}",
            tbl,
            mode="append",
            partition_by=["run_id"],
            schema_mode="merge",
        )
        counts[table] = tbl.num_rows
    return counts


def prune_staging(tables_dir: str | Path, keep_days: int, keep_run_ids: set[str] = frozenset()) -> int:
    """Delete staging partitions older than `keep_days`. Returns the number of runs removed."""
    from deltalake import DeltaTable

    cutoff = datetime.now(timezone.utc) - timedelta(days=keep_days)
    removed: set[str] = set()
    for table in STAGING_TABLES:
        path = f"{Path(tables_dir).as_posix()}/{table}"
        try:
            dt = DeltaTable(path)
        except Exception:  # table not created yet
            continue
        runs = dt.to_pyarrow_table(columns=["run_id", "staged_at"]).group_by("run_id").aggregate(
            [("staged_at", "max")]
        )
        old = [
            r for r, ts in zip(runs["run_id"].to_pylist(), runs["staged_at_max"].to_pylist())
            if ts < cutoff and r not in keep_run_ids
        ]
        if old:
            in_list = ", ".join("'" + r.replace("'", "''") + "'" for r in old)
            dt.delete(f"run_id IN ({in_list})")
            removed.update(old)
    return len(removed)
