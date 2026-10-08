import asyncio
import logging

from .. import sokosumi, store
from ..limbs import REGISTRY

log = logging.getLogger("frankenstein.catalog")
CACHE_KEY = "catalog:sokosumi"
CACHE_SECONDS = 3600


def limb_catalog() -> list[dict]:
    return [limb.describe() for limb in REGISTRY.values()]


def _field(f: dict) -> dict:
    data = f.get("data") or {}
    out = {"id": f["id"], "type": f["type"], "label": f.get("name"), "optional": sokosumi.is_optional(f)}
    for k in ("placeholder", "description", "values", "default"):
        if data.get(k) not in (None, ""):
            out[k] = data[k]
    return out


def _newest_per_name(agents: list[dict]) -> list[dict]:
    """The marketplace lists some agents twice; the UUIDv7-style ids (with dashes) are the newer listings."""
    by_name: dict[str, dict] = {}
    for a in agents:
        cur = by_name.get(a["name"])
        if cur is None or ("-" in a["id"] and "-" not in cur["id"]):
            by_name[a["name"]] = a
    return list(by_name.values())


async def sokosumi_agents(refresh: bool = False) -> list[dict]:
    if not refresh:
        cached = store.cache_get(CACHE_KEY, CACHE_SECONDS)
        if cached is not None:
            return cached
    agents = _newest_per_name(await sokosumi.list_agents())
    sem = asyncio.Semaphore(8)

    async def detail(a):
        async with sem:
            try:
                schema = await sokosumi.get_schema(a["id"])
            except sokosumi.SokosumiError as e:
                log.warning("skipping agent %s: %s", a["id"], e)
                return None
        info = next((f for f in schema.get("input_data", []) if f.get("type") == "none"), None)
        return {
            "id": a["id"],
            "name": a["name"],
            "credits": a["credits"],
            "summary": a.get("summary") or "",
            "about": ((info or {}).get("data") or {}).get("description", "")[:600],
            "fields": [_field(f) for f in sokosumi.fields(schema)],
        }

    out = [d for d in await asyncio.gather(*(detail(a) for a in agents)) if d]
    store.cache_put(CACHE_KEY, out)
    return out
