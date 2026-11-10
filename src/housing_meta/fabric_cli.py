"""housing-fabric: generate, deploy and run the Fabric pipeline.

  housing-fabric bundle            write the deployable files (pipeline, notebook, Warehouse SQL, requests)
  housing-fabric deploy-pipeline   create (or update) the pipeline in a workspace via the REST API
  housing-fabric run               start one province-year run with parameters and wait for the result

IDs you don't pass are written as <PLACEHOLDERS>; `deploy-pipeline` refuses to send those.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import fabric_api, fabric_pipeline as fp


def _target(args) -> fp.Target:
    t = fp.Target()
    for field in vars(t):
        val = getattr(args, field, None)
        if val:
            setattr(t, field, val)
    return t


def _add_target_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("Fabric items (find the IDs in each item's URL)")
    g.add_argument("--workspace-id")
    g.add_argument("--notebook-id", help="nb_housing_build item ID")
    g.add_argument("--warehouse-id", help="Warehouse item ID")
    g.add_argument("--warehouse-endpoint",
                   help="Warehouse SQL connection string host, e.g. abc123.datawarehouse.fabric.microsoft.com")
    g.add_argument("--warehouse-name", help="default: wh_housing")
    g.add_argument("--lakehouse-name", help="default: lh_housing")
    g.add_argument("--pipeline-name", help="default: pl_housing_load")


def _write(path: Path, content: str | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content, indent=2) + "\n")
    print(f"  wrote {path}")


def cmd_bundle(args) -> int:
    t = _target(args)
    out = Path(args.out)
    item = out / f"{t.pipeline_name}.DataPipeline"
    _write(item / "pipeline-content.json", fp.pipeline_content(t))
    _write(item / ".platform", fp.platform_file(t))
    _write(out / "nb_housing_build.ipynb", fp.notebook(t))
    for name, sql in fp.warehouse_sql(t).items():
        _write(out / "warehouse" / name, sql)
    _write(out / "requests" / "create_pipeline.json", fp.create_request(t))
    _write(out / "requests" / "run_pipeline_example.json", fp.run_request("ON", 2025))
    _write(out / "requests" / "run_pipeline.http", _http_example(t))
    if t.placeholders():
        print(f"\nStill placeholders: {', '.join(t.placeholders())}. "
              "Re-run with those options once the Fabric items exist.")
    return 0


def _http_example(t: fp.Target) -> str:
    body = json.dumps(fp.run_request("ON", 2025), indent=2)
    return f"""### Start one province-year run (the call your scheduling tool makes)
# Token audience: https://api.fabric.microsoft.com  (user, service principal or managed identity)
POST https://api.fabric.microsoft.com/v1/workspaces/{t.workspace_id}/items/<PIPELINE_ID>/jobs/instances?jobType=Pipeline
Authorization: Bearer <TOKEN>
Content-Type: application/json

{body}

### Response: 202 Accepted with a Location header. Poll it until status is
### Completed, Failed or Cancelled; failureReason explains a failed run (e.g. blocking QA issues).
GET <Location header from the call above>
Authorization: Bearer <TOKEN>
"""


def cmd_deploy(args) -> int:
    t = _target(args)
    missing = [p for p in t.placeholders()]
    if missing:
        print(f"Missing: {', '.join('--' + m.replace('_', '-') for m in missing)}", file=sys.stderr)
        return 2
    token = fabric_api.get_token(args.token)
    if args.pipeline_id:
        body = fp.create_request(t)
        fabric_api.request(
            "POST", f"/workspaces/{t.workspace_id}/items/{args.pipeline_id}/updateDefinition",
            token, {"definition": body["definition"]},
        )
        print(f"updated pipeline {args.pipeline_id}")
    else:
        status, headers, item = fabric_api.request("POST", f"/workspaces/{t.workspace_id}/items",
                                                   token, fp.create_request(t))
        if status == 202:
            item = fabric_api.wait_operation(headers["Location"], token)
        print(f"created pipeline {item.get('displayName')} id={item.get('id')}")
    return 0


def cmd_run(args) -> int:
    body = fp.run_request(args.source_system, args.reference_year, args.metadata_release, args.mode)
    url = (f"{fabric_api.API}/workspaces/{args.workspace_id}/items/{args.pipeline_id}"
           f"/jobs/instances?jobType=Pipeline")
    if args.dry_run:
        print(f"POST {url}\n{json.dumps(body, indent=2)}")
        return 0
    token = fabric_api.get_token(args.token)
    job_url = fabric_api.run_pipeline(args.workspace_id, args.pipeline_id,
                                      body["executionData"]["parameters"], token)
    print(f"started: {job_url}")
    if args.no_wait:
        return 0
    job = fabric_api.wait_pipeline(job_url, token)
    print(json.dumps(job, indent=2))
    return 0 if job.get("status") == "Completed" else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="housing-fabric", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("bundle", help="write pipeline, notebook, Warehouse SQL and request files")
    _add_target_args(b)
    b.add_argument("--out", default="fabric/bundle")
    b.set_defaults(func=cmd_bundle)

    d = sub.add_parser("deploy-pipeline", help="create or update the pipeline via the Fabric REST API")
    _add_target_args(d)
    d.add_argument("--pipeline-id", help="update this existing pipeline instead of creating one")
    d.add_argument("--token", help="bearer token (default: $FABRIC_TOKEN or azure-identity)")
    d.set_defaults(func=cmd_deploy)

    r = sub.add_parser("run", help="run the pipeline for one province-year")
    r.add_argument("--workspace-id", required=True)
    r.add_argument("--pipeline-id", required=True)
    r.add_argument("--source-system", required=True, help="province code, e.g. ON")
    r.add_argument("--reference-year", required=True, type=int)
    r.add_argument("--metadata-release", default="current")
    r.add_argument("--mode", choices=["validate", "publish"], default="validate")
    r.add_argument("--no-wait", action="store_true", help="return once the run has started")
    r.add_argument("--dry-run", action="store_true", help="print the request instead of sending it")
    r.add_argument("--token", help="bearer token (default: $FABRIC_TOKEN or azure-identity)")
    r.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except fabric_api.FabricError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
