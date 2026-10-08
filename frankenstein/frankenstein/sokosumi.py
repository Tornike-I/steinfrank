"""Importable Sokosumi client; same API as sokosumi-test/soko.py but raises instead of exiting."""
import asyncio
import os

import httpx

BASE_URL = os.environ.get("SOKOSUMI_BASE_URL", "https://api.sokosumi.com/v1")
TERMINAL = {"completed", "failed", "input_required", "payment_failed", "refund_resolved", "dispute_resolved"}
NUMBER_TYPES = {"number", "range"}
BOOL_TYPES = {"boolean", "checkbox"}
LIST_TYPES = {"option", "multiselect"}


class SokosumiError(RuntimeError):
    pass


async def request(method: str, path: str, body=None, auth=True):
    headers = {"Accept": "application/json"}
    if auth:
        key = os.environ.get("SOKOSUMI_API_KEY")
        if not key:
            raise SokosumiError("SOKOSUMI_API_KEY is not set")
        headers["Authorization"] = f"Bearer {key}"
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.request(method, BASE_URL + path, json=body, headers=headers)
    if resp.status_code >= 400:
        raise SokosumiError(f"{method} {path} -> {resp.status_code}: {resp.text[:1000]}")
    return resp.json()


async def list_agents() -> list[dict]:
    agents, cursor = [], None
    while True:
        qs = f"?cursor={cursor}" if cursor else ""
        res = await request("GET", f"/agents{qs}", auth=bool(os.environ.get("SOKOSUMI_API_KEY")))
        agents.extend(res["data"])
        cursor = res["meta"]["pagination"].get("nextCursor")
        if not cursor:
            return agents


async def get_schema(agent_id: str) -> dict:
    return (await request("GET", f"/agents/{agent_id}/input-schema"))["data"]


def fields(schema: dict) -> list[dict]:
    return [f for f in schema.get("input_data", []) if f.get("type") != "none"]


def is_optional(field: dict) -> bool:
    return any(v.get("validation") == "optional" and v.get("value") == "true" for v in field.get("validations") or [])


def coerce_inputs(schema: dict, inputs: dict) -> dict:
    by_id = {f["id"]: f for f in fields(schema)}
    unknown = set(inputs) - set(by_id)
    if unknown:
        raise SokosumiError(f"unknown input fields: {sorted(unknown)}")
    out = {}
    for fid, f in by_id.items():
        t = f["type"]
        if t == "hidden":
            out[fid] = (f.get("data") or {}).get("value")
            continue
        v = inputs.get(fid)
        if v is None or v == "":
            if not is_optional(f):
                raise SokosumiError(f"missing required input {fid!r}")
            continue
        if t in NUMBER_TYPES:
            v = float(v)
            v = int(v) if v.is_integer() else v
        elif t in BOOL_TYPES:
            v = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
        elif t in LIST_TYPES:
            v = v if isinstance(v, list) else [v]
        elif t == "file":
            raise SokosumiError(f"file inputs are not supported ({fid!r})")
        else:
            v = v if isinstance(v, str) else str(v)
        out[fid] = v
    return out


async def create_job(agent_id: str, inputs: dict, max_credits: float, name: str | None = None) -> dict:
    schema = await get_schema(agent_id)
    body = {"inputSchema": schema, "inputData": coerce_inputs(schema, inputs), "maxCredits": max_credits}
    if name:
        body["name"] = name
    return (await request("POST", f"/agents/{agent_id}/jobs", body))["data"]


async def get_job(job_id: str) -> dict:
    return (await request("GET", f"/jobs/{job_id}"))["data"]


async def get_files(job_id: str) -> list[dict]:
    return (await request("GET", f"/jobs/{job_id}/files"))["data"]


async def get_input_request(job_id: str) -> dict:
    return (await request("GET", f"/jobs/{job_id}/input-request"))["data"]


async def provide_input(job_id: str, event_id: str, input_data: dict) -> dict:
    return (await request("POST", f"/jobs/{job_id}/inputs", {"eventId": event_id, "inputData": input_data}))["data"]


async def wait(job_id: str, interval: float = 10) -> dict:
    while True:
        job = await get_job(job_id)
        if job["status"] in TERMINAL:
            return job
        await asyncio.sleep(interval)
