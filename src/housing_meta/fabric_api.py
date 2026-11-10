"""Minimal Fabric REST client (standard library only).

Used by the notebook (to refresh the Lakehouse SQL endpoint) and by the
`housing-fabric run` command (to start a pipeline run and wait for it).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

API = "https://api.fabric.microsoft.com/v1"
SCOPE = "https://api.fabric.microsoft.com/.default"


class FabricError(RuntimeError):
    pass


def get_token(token: str | None = None) -> str:
    """Token from the argument, $FABRIC_TOKEN, or azure-identity (az login / service principal env vars)."""
    token = token or os.environ.get("FABRIC_TOKEN")
    if token:
        return token
    try:
        from azure.identity import DefaultAzureCredential
    except ImportError as e:  # pragma: no cover - depends on the caller's environment
        raise FabricError(
            "No token. Pass --token, set FABRIC_TOKEN, or `pip install azure-identity` "
            "and sign in (az login) or set AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_CLIENT_SECRET."
        ) from e
    return DefaultAzureCredential().get_token(SCOPE).token


def request(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, dict, dict]:
    """Returns (status, headers, json_body). Raises FabricError on 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url if url.startswith("http") else API + url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, dict(resp.headers), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raise FabricError(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')}") from e


def wait_operation(location: str, token: str, retry_after: int = 5, timeout_s: int = 1800) -> dict:
    """Poll a long-running operation (202 + Location) until it finishes; return its result."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(retry_after)
        _, headers, body = request("GET", location, token)
        status = body.get("status")
        if status == "Succeeded":
            try:
                return request("GET", location.rstrip("/") + "/result", token)[2]
            except FabricError:
                return body
        if status in ("Failed", "Cancelled"):
            raise FabricError(f"operation {status}: {json.dumps(body.get('error', body))}")
        retry_after = int(headers.get("Retry-After", retry_after))
    raise FabricError(f"operation did not finish within {timeout_s}s: {location}")


def refresh_sql_endpoint(workspace_id: str, lakehouse_id: str, tables: list[str], token: str,
                         schema: str = "dbo") -> list[dict]:
    """Force the Lakehouse SQL analytics endpoint to pick up new staging data.

    Without this the Warehouse can read stale staging tables (metadata sync lag).
    """
    _, _, lh = request("GET", f"/workspaces/{workspace_id}/lakehouses/{lakehouse_id}", token)
    endpoint_id = lh["properties"]["sqlEndpointProperties"]["id"]
    status, headers, body = request(
        "POST",
        f"/workspaces/{workspace_id}/sqlEndpoints/{endpoint_id}/refreshMetadata",
        token,
        {"tables": [{"schema": schema, "tableNames": tables}]},
    )
    if status == 202:
        body = wait_operation(headers["Location"], token, int(headers.get("Retry-After", 5)))
    results = body.get("value", [])
    failed = [r for r in results if r.get("status") == "Failure"]
    if failed:
        raise FabricError(f"SQL endpoint refresh failed for: {json.dumps(failed)}")
    return results


def run_pipeline(workspace_id: str, pipeline_id: str, parameters: dict, token: str) -> str:
    """Start a pipeline run. Returns the job-instance URL to poll."""
    status, headers, _ = request(
        "POST",
        f"/workspaces/{workspace_id}/items/{pipeline_id}/jobs/instances?jobType=Pipeline",
        token,
        {"executionData": {"parameters": parameters}},
    )
    if status != 202 or "Location" not in headers:
        raise FabricError(f"unexpected response starting pipeline: {status} {headers}")
    return headers["Location"]


def wait_pipeline(job_url: str, token: str, poll_s: int = 20, timeout_s: int = 7200) -> dict:
    """Poll a pipeline job instance until it reaches a final state."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        _, _, body = request("GET", job_url, token)
        if body.get("status") in ("Completed", "Failed", "Cancelled", "Deduped"):
            return body
        time.sleep(poll_s)
    raise FabricError(f"pipeline run did not finish within {timeout_s}s: {job_url}")
