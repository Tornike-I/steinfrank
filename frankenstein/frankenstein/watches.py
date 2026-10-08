import asyncio
import logging
import time
import uuid

from . import config, store
from .runtime import InputError, Runner, check_inputs

log = logging.getLogger("frankenstein.watches")
ACTIVE = {"queued", "running", "waiting", "needs_input", "needs_confirmation"}


class WatchError(ValueError):
    pass


def create_watch(monster_id: str, inputs: dict, every_seconds: int, deliver_to: list[str], label: str = "") -> dict:
    spec = store.load_monster(monster_id)
    if spec is None:
        raise WatchError(f"no monster {monster_id!r}")
    if every_seconds < config.MIN_WATCH_SECONDS:
        raise WatchError(f"every_seconds must be >= {config.MIN_WATCH_SECONDS}")
    try:
        inputs = check_inputs(spec.inputs, inputs)
    except InputError as e:
        raise WatchError(str(e)) from e
    missing = [d for d in deliver_to if store.get_destination(d) is None]
    if missing:
        raise WatchError(f"unknown destinations {missing}")
    watch = {
        "id": uuid.uuid4().hex[:10], "monster_id": monster_id, "inputs": inputs, "every_seconds": every_seconds,
        "deliver_to": deliver_to, "label": label or spec.name, "memory": {}, "last_run_id": None,
        "runs": 0, "enabled": True, "next_at": time.time(),
    }
    store.save_watch(watch)
    return watch


def _absorb_last_run(watch: dict) -> bool:
    """Returns False while the previous run is still in flight, so runs of one watch never overlap."""
    if not watch["last_run_id"]:
        return True
    run = store.get_run(watch["last_run_id"])
    if run is None:
        return True
    if run["status"] in ACTIVE:
        return False
    if run["status"] == "completed" and isinstance((run["output"] or {}).get("memory"), dict):
        watch["memory"] = run["output"]["memory"]
    return True


async def tick(runner: Runner, now: float | None = None) -> list[str]:
    now = now or time.time()
    started = []
    for watch in store.due_watches(now):
        watch["next_at"] = now + watch["every_seconds"]
        if not _absorb_last_run(watch):
            store.save_watch(watch)
            continue
        spec = store.load_monster(watch["monster_id"])
        if spec is None:
            watch["enabled"] = False
            store.save_watch(watch)
            continue
        run = await runner.create(spec, watch["inputs"], deliver_to=watch["deliver_to"],
                                  memory=watch["memory"], watch_id=watch["id"])
        watch["last_run_id"], watch["runs"] = run["id"], watch["runs"] + 1
        store.save_watch(watch)
        if run["status"] == "queued":
            await runner.advance(run)
        _absorb_last_run(watch)
        store.save_watch(watch)
        started.append(run["id"])
    return started


async def loop(runner: Runner, interval: float = 15):
    while True:
        try:
            await tick(runner)
        except Exception:
            log.exception("watch tick failed")
        await asyncio.sleep(interval)
