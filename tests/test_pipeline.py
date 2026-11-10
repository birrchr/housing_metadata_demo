"""Tests for the build, the one-province staging run and the generated Fabric bundle.

The Fabric parts that need a workspace (notebookutils, the Warehouse, the REST API)
can't run here; these tests cover everything up to that boundary, plus consistency
between the pipeline definition, the notebook and the stored procedure.
"""

from __future__ import annotations

import ast
import json
import random
import re
from pathlib import Path

import duckdb
import pyarrow.parquet as pq
import pytest

from housing_meta import build, fabric_cli, fabric_pipeline as fp, fabric_run, generate_sample, staging

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "metadata"


@pytest.fixture(scope="session")
def raw(tmp_path_factory) -> Path:
    """Sample deliveries for 2024 and 2025, generated fresh (same seeds as housing-sample)."""
    out = tmp_path_factory.mktemp("raw")
    for year in (2024, 2025):
        for prov, make in generate_sample.BUILDERS.items():
            path = out / f"year={year}" / f"province={prov}" / "households.parquet"
            path.parent.mkdir(parents=True)
            pq.write_table(make(year, random.Random(f"{prov}{year}")), path)
    return out


@pytest.fixture
def con():
    c = duckdb.connect(config={"threads": 2, "memory_limit": "1GB"})
    yield c
    c.close()


def qa(con) -> dict[str, int]:
    return dict(con.execute("SELECT * FROM meta.v_qa_summary").fetchall())


# ---------------------------------------------------------------- build
def test_full_build_finds_planted_problems(raw, con):
    build.build(con, META, raw)
    assert qa(con) == {"unmapped_columns": 2, "missing_variables": 0, "unmapped_values": 1,
                       "invalid_codes": 0, "bad_numerics": 0}


def test_build_one_province_year(raw, con):
    files = build.build(con, META, raw, years={2025}, provinces={"on"})
    assert [(p, y) for p, y, _ in files] == [("ON", 2025)]
    assert con.execute("SELECT list(dataset_id) FROM meta.dataset").fetchone()[0] == ["ON_2025"]
    assert all(n == 0 for n in qa(con).values())


def test_missing_delivery_is_an_error_when_required(raw, con):
    with pytest.raises(FileNotFoundError):
        build.build(con, META, raw, years={2030}, provinces={"ON"}, require_files=True)


# ---------------------------------------------------------------- staging
deltalake = pytest.importorskip("deltalake")


def run(raw, tables, prov, year, run_id, **kw):
    return fabric_run.run_delivery(raw_dir=raw, meta_dir=META, tables_dir=tables, source_system=prov,
                                   reference_year=year, run_id=run_id, metadata_release="r1", **kw)


def test_run_delivery_stages_one_province(raw, tmp_path):
    result = run(raw, tmp_path, "ON", 2025, "run-on")
    assert result["dataset_id"] == "ON_2025"
    assert result["qa_status"] == "ok"
    assert result["staged_rows"]["stg_record_value"] == 200 * 7
    assert set(result["staged_rows"]) == set(staging.STAGING_TABLES)

    ds = deltalake.DeltaTable(tmp_path / "stg_dataset").to_pyarrow_table().to_pylist()
    assert [(r["dataset_id"], r["run_id"], r["metadata_release"]) for r in ds] == [("ON_2025", "run-on", "r1")]


def test_blocking_qa_is_reported_not_raised(raw, tmp_path):
    result = run(raw, tmp_path, "BC", 2025, "run-bc")
    assert result["qa_status"] == "blocked"
    assert result["blocking_issues"] == 1          # BC tenure code '3'
    assert result["warnings"] == 1                 # BC REGION_INTERNAL column
    detail = deltalake.DeltaTable(tmp_path / "stg_qa_detail").to_pyarrow_table().to_pylist()
    assert {d["check_name"] for d in detail} == {"unmapped_values", "unmapped_columns"}
    assert json.loads(next(d["detail"] for d in detail if d["check_name"] == "unmapped_values"))["source_value"] == "3"


def test_runs_append_and_old_runs_are_pruned(raw, tmp_path):
    run(raw, tmp_path, "ON", 2025, "run-1")
    run(raw, tmp_path, "NS", 2025, "run-2")
    runs = set(deltalake.DeltaTable(tmp_path / "stg_dataset").to_pyarrow_table()["run_id"].to_pylist())
    assert runs == {"run-1", "run-2"}

    assert staging.prune_staging(tmp_path, keep_days=0, keep_run_ids={"run-2"}) == 1
    for table in staging.STAGING_TABLES:
        runs = set(deltalake.DeltaTable(tmp_path / table).to_pyarrow_table()["run_id"].to_pylist())
        assert runs <= {"run-2"}, table


def test_metadata_rows_have_stable_keys(raw, tmp_path):
    run(raw, tmp_path, "ON", 2025, "run-1")
    rows = deltalake.DeltaTable(tmp_path / "stg_metadata").to_pyarrow_table().to_pylist()
    keys = [(r["table_name"], r["row_key"]) for r in rows]
    assert len(keys) == len(set(keys)), "natural keys must be unique per metadata table"
    assert ("source_column_map", "ON|shelter_cost_mth|2020") in keys


def test_metadata_release_folders():
    assert fabric_run.metadata_dir("/lh/Files", "current") == Path("/lh/Files/metadata/current")
    assert fabric_run.metadata_dir("/lh/Files", "2026-11_r1") == Path("/lh/Files/metadata/releases/2026-11_r1")


# ---------------------------------------------------------------- generated bundle
def test_pipeline_notebook_and_procedure_agree():
    t = fp.Target()
    content = fp.pipeline_content(t)["properties"]
    pipeline_params = set(content["parameters"])
    nb_act, sp_act = content["activities"]

    # notebook activity passes every pipeline parameter + run_id, and the notebook declares them
    nb = fp.notebook(t)
    param_cell = next(c for c in nb["cells"] if "parameters" in c["metadata"].get("tags", []))
    declared = {n.targets[0].id for n in ast.parse("".join(param_cell["source"])).body}
    assert set(nb_act["typeProperties"]["parameters"]) == pipeline_params | {"run_id"} == declared

    # stored procedure activity matches the procedure's signature
    sql = fp.warehouse_sql(t)["02_usp_load_run.sql"]
    signature = sql[sql.index("CREATE OR ALTER PROCEDURE"):sql.index("\nAS\n")]
    assert set(re.findall(r"@(\w+)", signature)) == set(sp_act["typeProperties"]["storedProcedureParameters"])
    assert sp_act["dependsOn"] == [{"activity": nb_act["name"], "dependencyConditions": ["Succeeded"]}]


def test_notebook_code_cells_are_valid_python():
    for cell in fp.notebook(fp.Target())["cells"]:
        if cell["cell_type"] == "code":
            src = "".join(cell["source"])
            if not src.startswith("%"):
                ast.parse(src)


def test_lakehouse_name_is_substituted():
    sql = fp.warehouse_sql(fp.Target(lakehouse_name="lh_prod"))
    assert "lh_housing." not in "".join(sql.values())
    assert "lh_prod.dbo.stg_record_value" in sql["02_usp_load_run.sql"]


def test_create_request_round_trips():
    import base64
    t = fp.Target(workspace_id="w", notebook_id="n", warehouse_id="h", warehouse_endpoint="e")
    part = fp.create_request(t)["definition"]["parts"][0]
    assert part["path"] == "pipeline-content.json"
    assert json.loads(base64.b64decode(part["payload"])) == fp.pipeline_content(t)
    assert t.placeholders() == []


def test_cli_bundle_and_dry_run(tmp_path, capsys):
    assert fabric_cli.main(["bundle", "--out", str(tmp_path), "--lakehouse-name", "lh_x"]) == 0
    assert (tmp_path / "pl_housing_load.DataPipeline" / "pipeline-content.json").exists()
    assert "lh_x.dbo" in (tmp_path / "warehouse" / "02_usp_load_run.sql").read_text()

    assert fabric_cli.main(["deploy-pipeline"]) == 2          # refuses placeholders

    assert fabric_cli.main(["run", "--workspace-id", "w", "--pipeline-id", "p", "--source-system", "nb",
                            "--reference-year", "2026", "--mode", "publish", "--dry-run"]) == 0
    out = capsys.readouterr().out
    body = json.loads(out[out.index("{"):])
    assert body == {"executionData": {"parameters": {
        "source_system": "NB", "reference_year": 2026, "metadata_release": "current", "mode": "publish"}}}
