"""One pipeline run = one province-year delivery.

`run_delivery()` is everything the Fabric notebook does, apart from refreshing the
SQL endpoint. It runs locally too (point the paths at local folders), which is how
the tests exercise it.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from . import build, staging

MODES = ("validate", "publish")


def metadata_dir(files_root: str | Path, metadata_release: str) -> Path:
    """`current` -> Files/metadata/current, anything else -> Files/metadata/releases/<release>."""
    base = Path(files_root) / "metadata"
    return base / "current" if metadata_release == "current" else base / "releases" / metadata_release


def run_delivery(
    *,
    raw_dir: str | Path,
    meta_dir: str | Path,
    tables_dir: str | Path,
    source_system: str,
    reference_year: int,
    run_id: str,
    metadata_release: str,
    mode: str = "validate",
    keep_staging_days: int | None = 30,
) -> dict:
    """Build one province-year, stage it as Delta and return a summary for the pipeline.

    The summary is what the notebook passes to `notebookutils.notebook.exit()`.
    Whether to publish is decided by the Warehouse procedure, which also enforces
    the QA gate, so this function never raises on QA issues.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not run_id:
        raise ValueError("run_id is required (the pipeline passes @pipeline().RunId)")
    source_system = source_system.upper()

    con = duckdb.connect()
    build.build(
        con, Path(meta_dir), Path(raw_dir),
        years={int(reference_year)}, provinces={source_system}, require_files=True,
    )
    counts = staging.export_staging(con, tables_dir, run_id=run_id, metadata_release=metadata_release)
    qa = staging.qa_summary(con)
    pruned = staging.prune_staging(tables_dir, keep_staging_days, {run_id}) if keep_staging_days else 0
    con.close()

    blocking = sum(c["n_issues"] for c in qa if c["blocking"])
    warnings = sum(c["n_issues"] for c in qa if not c["blocking"])
    return {
        "run_id": run_id,
        "dataset_id": f"{source_system}_{int(reference_year)}",
        "metadata_release": metadata_release,
        "mode": mode,
        "qa_status": "blocked" if blocking else "ok",
        "blocking_issues": blocking,
        "warnings": warnings,
        "qa": qa,
        "staged_rows": counts,
        "pruned_runs": pruned,
    }
