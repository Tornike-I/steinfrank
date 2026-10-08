"""Usage:
  python -m frankenstein.cli forge "<task description>"
  python -m frankenstein.cli publish <draft_id | path/to/monster.json> [--no-voice]
  python -m frankenstein.cli validate <path/to/monster.json> [--dry-run]
  python -m frankenstein.cli list
  python -m frankenstein.cli run <monster_id> key=value ... [--to <dest_id>,...] [--notify]
  python -m frankenstein.cli status <run_id>
  python -m frankenstein.cli confirm <run_id> yes|no
  python -m frankenstein.cli agents [--refresh]
  python -m frankenstein.cli channels
  python -m frankenstein.cli dest add <channel> key=value ... [--label NAME]
  python -m frankenstein.cli dest list | dest test <dest_id>
  python -m frankenstein.cli watch add <monster_id> <every_seconds> key=value ... [--to <dest_id>,...]
  python -m frankenstein.cli watch list | watch tick
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

from . import channels, store, watches
from .forge import pipeline
from .forge.catalog import sokosumi_agents
from .forge.estimator import estimate
from .runtime import Runner


def _show(obj):
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _load_raw(ref: str) -> dict:
    p = Path(ref)
    if p.suffix == ".json" and p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    draft = store.get_draft(ref)
    if draft is None:
        sys.exit(f"no draft or file {ref!r}")
    return draft


def _split(args: list[str]) -> tuple[dict, dict]:
    pairs, opts, it = {}, {}, iter(args)
    for a in it:
        if a.startswith("--"):
            opts[a[2:]] = True if a in ("--notify", "--no-voice", "--refresh", "--dry-run") else next(it)
        else:
            k, v = a.split("=", 1)
            try:
                pairs[k] = json.loads(v)
            except json.JSONDecodeError:
                pairs[k] = v
    return pairs, opts


def _range(lo, hi, unit):
    return f"{lo:g} {unit}" if lo == hi else f"{lo:g}-{hi:g} {unit}"


async def main(argv: list[str]):
    if not argv:
        sys.exit(__doc__)
    cmd, rest = argv[0], argv[1:]
    if cmd == "forge":
        _show((await pipeline.forge(" ".join(rest))).as_dict())
    elif cmd == "publish":
        spec = await pipeline.publish(_load_raw(rest[0]), with_voice="--no-voice" not in rest)
        print(f"published {spec.id} v{spec.version}  agent={spec.voice.elevenlabs_agent_id}")
    elif cmd == "validate":
        spec, errors = await pipeline.check(_load_raw(rest[0]), await pipeline._agents_by_id())
        if spec and not errors and "--dry-run" in rest:
            errors = await pipeline.dry_run(spec)
        _show({"errors": errors, "estimate": estimate(spec).model_dump() if spec else None})
    elif cmd == "list":
        for s in store.list_monsters():
            e = estimate(s)
            print(f"{s.id:<26} v{s.version}  {_range(e.min.llm_tokens, e.max.llm_tokens, 'tok'):<14} "
                  f"{_range(e.min.credits, e.max.credits, 'cr'):<12} {s.purpose}")
    elif cmd == "run":
        spec = store.load_monster(rest[0]) or sys.exit(f"no monster {rest[0]!r}")
        inputs, opts = _split(rest[1:])
        to = [d for d in str(opts.get("to", "")).split(",") if d]
        run = await Runner().run_to_end(spec, inputs, deliver_to=to, notify_on_complete=bool(opts.get("notify")))
        _show({k: run[k] for k in ("id", "status", "output", "error", "confirm", "usage", "estimate")})
    elif cmd == "status":
        _show(store.get_run(rest[0]))
    elif cmd == "confirm":
        run = await Runner().confirm(rest[0], rest[1].lower() in ("y", "yes", "true"))
        _show({k: run[k] for k in ("id", "status", "output", "error")})
    elif cmd == "agents":
        for a in await sokosumi_agents(refresh="--refresh" in rest):
            print(f"{a['id']}  {a['credits']:>5} cr  {a['name']}  fields={[f['id'] for f in a['fields']]}")
    elif cmd == "channels":
        for c in channels.CHANNELS.values():
            print(f"{c.name:<9} {'on ' if c.enabled() else 'off'}  needs={list(c.requires)}  target={c.target_fields}")
    elif cmd == "dest":
        sub = rest[0]
        if sub == "add":
            target, opts = _split(rest[2:])
            _show(channels.public_destination(channels.create_destination(rest[1], target, opts.get("label", ""))))
        elif sub == "list":
            _show([channels.public_destination(d) for d in store.list_destinations()])
        elif sub == "test":
            _show(await channels.deliver([rest[1]], channels.Message(title="Steinfrank test", body="Your monster can reach you here.")))
    elif cmd == "watch":
        sub = rest[0]
        if sub == "add":
            inputs, opts = _split(rest[3:])
            to = [d for d in str(opts.get("to", "")).split(",") if d]
            _show(watches.create_watch(rest[1], inputs, int(rest[2]), to))
        elif sub == "list":
            _show(store.list_watches())
        elif sub == "tick":
            _show(await watches.tick(Runner()))
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(main(sys.argv[1:]))
