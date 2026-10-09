import asyncio
import logging
import time
import uuid
from dataclasses import asdict

import httpx
import openai

from . import channels, config, safety, speech as speech_mod, store
from .forge.estimator import estimate
from .limbs import REGISTRY, BudgetExceeded, Cost, LimbError, NeedsInput, Pending, RunContext, dry_run_limb, get_limb
from .refs import eval_when, lookup, render
from .spec import MonsterSpec, Step

log = logging.getLogger("frankenstein.runtime")

DEFAULT_MAX_STRING = 4000
JSON_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict}
TRANSIENT = (httpx.TransportError, openai.APIConnectionError, openai.RateLimitError, openai.InternalServerError)
RETRY_DELAYS = (1, 3)


class InputError(ValueError):
    pass


def check_inputs(schema: dict, inputs: dict) -> dict:
    props = schema.get("properties") or {}
    unknown = set(inputs) - set(props)
    if unknown:
        raise InputError(f"unknown inputs: {sorted(unknown)}")
    out = {}
    for name, p in props.items():
        v = inputs.get(name, p.get("default"))
        if v is None:
            if name in (schema.get("required") or []):
                raise InputError(f"missing input {name!r}")
            continue
        t = p.get("type", "string")
        if t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
            v = int(v)
        elif t == "number" and isinstance(v, str):
            try:
                v = float(v)
            except ValueError:
                pass
        if t in JSON_TYPES and (not isinstance(v, JSON_TYPES[t]) or (t != "boolean" and isinstance(v, bool))):
            raise InputError(f"input {name!r} must be {t}")
        if isinstance(v, str):
            if len(v) > p.get("maxLength", DEFAULT_MAX_STRING):
                raise InputError(f"input {name!r} is too long")
            if "enum" in p and v not in p["enum"]:
                raise InputError(f"input {name!r} must be one of {p['enum']}")
        out[name] = v
    return out


def _text_of(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_text_of(v) for v in value.values())
    if isinstance(value, list):
        return "\n".join(_text_of(v) for v in value)
    return ""


def clip_speech(text, limit: int = config.SPEECH_MAX_CHARS) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > limit // 2 else cut.rsplit(" ", 1)[0] + "…"


def _delta(after: Cost, before: Cost) -> Cost:
    return Cost(**{k: v - getattr(before, k) for k, v in asdict(after).items()})


def _step_usage(ctx: RunContext, base: RunContext) -> dict:
    return _delta(ctx.usage, base.usage).as_dict()


class Runner:
    def __init__(self, limb_for=get_limb, dry_run: bool = False, decline_paid: bool = False):
        self.dry_run = dry_run
        self.decline_paid = decline_paid
        self.limb_for = dry_run_limb if dry_run and limb_for is get_limb else limb_for
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, run_id: str) -> asyncio.Lock:
        return self._locks.setdefault(run_id, asyncio.Lock())

    async def create(self, spec: MonsterSpec, inputs: dict, *, deliver_to: list[str] | None = None,
                     notify_on_complete: bool = False, memory: dict | None = None, watch_id: str | None = None) -> dict:
        run = {
            "id": uuid.uuid4().hex[:12],
            "monster_id": spec.id,
            "status": "queued",
            "inputs": check_inputs(spec.inputs, inputs),
            "memory": memory or {},
            "clock": {
                "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "today": time.strftime("%Y-%m-%d", time.gmtime()),
                "timestamp": int(time.time()),
            },
            "steps": {},
            "cursor": 0,
            "pending": {},
            "needs_input": None,
            "confirm": None,
            "approved": [],
            "declined": [],
            "usage": Cost().as_dict(),
            "notifications": 0,
            "deliver_to": list(deliver_to or []),
            "notify_on_complete": notify_on_complete,
            "watch_id": watch_id,
            "dry_run": self.dry_run,
            "estimate": estimate(spec).model_dump(),
            "error": None,
            "output": None,
            "log": [],
            "spec": spec.dump(),
        }
        flagged = await safety.moderate(_text_of(run["inputs"]))
        if flagged:
            run["status"], run["error"] = "blocked", f"input flagged: {', '.join(flagged)}"
        store.save_run(run)
        return run

    async def start(self, spec: MonsterSpec, inputs: dict, **kw) -> dict:
        run = await self.create(spec, inputs, **kw)
        if run["status"] == "queued":
            await self.advance(run)
        return run

    def _ctx(self, run: dict, spec: MonsterSpec, step_id: str) -> RunContext:
        return RunContext(run["id"], step_id, spec, Cost(**run["usage"]), run["deliver_to"], run["notifications"])

    def _absorb(self, run: dict, base: RunContext, ctxs: list[RunContext]):
        usage = Cost(**run["usage"])
        for c in ctxs:
            usage = usage + _delta(c.usage, base.usage)
            run["notifications"] += c.notifications - base.notifications
        run["usage"] = usage.as_dict()

    def _fail(self, run, step_id, err):
        run["status"] = "failed"
        run["error"] = f"{step_id}: {err}" if step_id else str(err)
        run["log"].append({"step": step_id, "event": "error", "detail": str(err)})

    async def _call(self, limb, args, ctx):
        for delay in (*RETRY_DELAYS, None):
            try:
                return await limb.run(args, ctx)
            except TRANSIENT as e:
                if not limb.retryable or delay is None:
                    raise
                log.warning("retrying %s after %s", ctx.step_id, e)
                await asyncio.sleep(delay)

    async def _run_leaf(self, leaf: Step, limb, args, ctx):
        if leaf.for_each:
            return {"items": [await self._call(limb, a, ctx) for a in args]}
        return await self._call(limb, args, ctx)

    async def advance(self, run: dict) -> dict:
        async with self._lock(run["id"]):
            return await self._advance(run)

    def _plan_group(self, run, spec, group, data) -> list[tuple]:
        launch = []
        for leaf in group.leaves():
            if leaf.id in run["steps"] or leaf.id in run["pending"]:
                continue
            if leaf.id in run["declined"] or (leaf.when and not eval_when(leaf.when, data)):
                run["steps"][leaf.id] = None
                run["log"].append({"step": leaf.id, "event": "declined" if leaf.id in run["declined"] else "skipped"})
                continue
            limb = self.limb_for(leaf.limb, spec)
            if limb is None:
                raise RuntimeError(f"unknown limb {leaf.limb}")
            if leaf.for_each:
                items = lookup(leaf.for_each.over, data) or []
                if not isinstance(items, list):
                    raise RuntimeError(f"for_each.over {leaf.for_each.over} is not a list")
                items = items[: min(leaf.for_each.max, config.CAPS["max_fanout"])]
                args = [render(leaf.args, {**data, leaf.for_each.as_: it}) for it in items]
                cost = Cost()
                for a in args:
                    cost = cost + limb.precheck(a)
            else:
                args = render(leaf.args, data)
                cost = limb.precheck(args)
            launch.append((leaf, limb, args, cost))
        return launch

    async def _advance(self, run: dict) -> dict:
        spec = MonsterSpec.model_validate(run["spec"])
        data = {"inputs": run["inputs"], "steps": run["steps"], "memory": run["memory"], "run": run["clock"]}
        run["status"] = "running"
        step_id = None
        try:
            while run["cursor"] < len(spec.steps):
                if run["cursor"] >= min(spec.policy.max_steps, config.CAPS["max_steps"]):
                    raise BudgetExceeded("max_steps reached")
                group = spec.steps[run["cursor"]]
                step_id = group.id
                launch = self._plan_group(run, spec, group, data)

                if self._needs_confirmation(run, spec, launch):
                    store.save_run(run)
                    await self._alert(run, spec, "needs your OK", run["confirm"]["message"])
                    return run

                base = self._ctx(run, spec, group.id)
                total = Cost()
                for *_, cost in launch:
                    total = total + cost
                base.check(total)
                ctxs = [self._ctx(run, spec, leaf.id) for leaf, *_ in launch]
                results = await asyncio.gather(
                    *(self._run_leaf(leaf, limb, args, c) for (leaf, limb, args, _), c in zip(launch, ctxs))
                )
                self._absorb(run, base, ctxs)
                for (leaf, *_), result, c in zip(launch, results, ctxs):
                    used = _step_usage(c, base)
                    if isinstance(result, Pending):
                        run["pending"][leaf.id] = result.state
                        run["log"].append({"step": leaf.id, "event": "waiting", "detail": result.state, "usage": used})
                    else:
                        run["steps"][leaf.id] = result
                        run["log"].append({"step": leaf.id, "event": "done", "usage": used})
                if run["pending"]:
                    run["status"] = "waiting"
                    store.save_run(run)
                    return run
                if group.parallel is not None:
                    run["steps"][group.id] = {leaf.id: run["steps"].get(leaf.id) for leaf in group.leaves()}
                run["cursor"] += 1
                store.save_run(run)
            step_id = None
            await self._finish(run, spec, data)
        except LimbError as e:
            log.warning("run %s failed at %s: %s", run["id"], step_id, e)
            self._fail(run, step_id, e)
        except Exception as e:
            log.exception("run %s failed", run["id"])
            self._fail(run, step_id, e)
        if run["status"] == "failed":
            await self._alert(run, spec, "failed", run["error"])
        store.save_run(run)
        return run

    def _needs_confirmation(self, run, spec, launch) -> bool:
        if self.dry_run and not self.decline_paid:
            return False
        gated = [(leaf, cost) for leaf, _, _, cost in launch if cost.credits > 0 and leaf.id not in run["approved"]]
        if self.decline_paid and gated:
            run["declined"] += [leaf.id for leaf, _ in gated]
            for leaf, _ in gated:
                run["steps"][leaf.id] = None
                run["log"].append({"step": leaf.id, "event": "declined"})
            launch[:] = [entry for entry in launch if entry[0].id not in run["declined"]]
            return False
        credits = sum(c.credits for _, c in gated)
        if not gated or credits <= config.CONFIRM_ABOVE_CREDITS:
            run["approved"] += [leaf.id for leaf, _ in gated]
            return False
        names = ", ".join(leaf.note or leaf.id for leaf, _ in gated)
        run["confirm"] = {
            "steps": [leaf.id for leaf, _ in gated],
            "credits": credits,
            "message": f"{spec.name} wants to hire paid help ({names}) for up to {credits:g} credits. Go ahead?",
        }
        run["status"] = "needs_confirmation"
        run["log"].append({"step": None, "event": "needs_confirmation", "detail": run["confirm"]})
        return True

    async def _finish(self, run, spec, data):
        output = render(spec.output, data)
        speech = clip_speech(output.get("speech", ""))
        # Template-built speech can contain URLs and symbols that TTS reads out literally.
        rewrite = Cost()
        if spec.speak and speech and safety.available():
            try:
                speech, used = await speech_mod.speakable(speech, _text_of(output.get("report", "")))
                speech = clip_speech(speech)
                rewrite = Cost(llm_tokens=used)
                run["log"].append({"step": "speech", "event": "rewritten", "usage": rewrite.as_dict()})
            except Exception as e:
                run["log"].append({"step": "speech", "event": "error", "detail": f"rewrite: {e}"})
        output["speech"] = speech
        flagged = await safety.moderate(speech + "\n" + _text_of(output.get("report", "")))
        if flagged:
            run["output"] = None
            run["status"], run["error"] = "blocked", f"output flagged: {', '.join(flagged)}"
            return
        tts = REGISTRY["tts"]
        if spec.speak and speech and not self.dry_run and tts.enabled():
            ctx = self._ctx(run, spec, "speech")
            try:
                speak = {"text": speech, "model_id": config.SPEECH_TTS_MODEL}
                if spec.voice.voice_id:
                    speak["voice_id"] = spec.voice.voice_id
                output["speech_audio_url"] = (await self._call(tts, speak, ctx))["url"]
                run["usage"] = ctx.usage.as_dict()
            except Exception as e:
                run["log"].append({"step": "speech", "event": "error", "detail": str(e)})
        # Added after the TTS budget check: older specs' llm budgets don't include the rewrite.
        run["usage"] = (Cost(**run["usage"]) + rewrite).as_dict()
        run["output"] = output
        run["status"] = "completed"
        if run["notify_on_complete"] and not self.dry_run:
            msg = channels.Message(
                title=spec.name, body=speech, report=_text_of(output.get("report", "")) or None,
                audio_url=output.get("speech_audio_url"), url=f"{config.PUBLIC_BASE_URL}/runs/{run['id']}",
                monster_id=spec.id, run_id=run["id"],
            )
            run["log"].append({"step": None, "event": "delivered", "detail": await channels.deliver(run["deliver_to"], msg)})

    async def _alert(self, run, spec, what, detail):
        """Watch runs have nobody looking at the screen, so pauses and failures go out as notifications."""
        if not run.get("watch_id") or self.dry_run:
            return
        await channels.deliver(run["deliver_to"], channels.Message(
            title=f"{spec.name} {what}", body=str(detail)[:1000], priority="high", monster_id=spec.id, run_id=run["id"],
        ))

    def _leaf(self, spec: MonsterSpec, step_id: str) -> Step:
        return next(s for s in spec.leaves() if s.id == step_id)

    async def poll(self, run: dict) -> dict:
        async with self._lock(run["id"]):
            run = store.get_run(run["id"]) or run
            if run["status"] != "waiting":
                return run
            spec = MonsterSpec.model_validate(run["spec"])
            for step_id, state in list(run["pending"].items()):
                ctx = self._ctx(run, spec, step_id)
                before = Cost(**run["usage"])
                try:
                    res = await self.limb_for(self._leaf(spec, step_id).limb, spec).poll(state, ctx)
                except Exception as e:
                    self._fail(run, step_id, e)
                    store.save_run(run)
                    await self._alert(run, spec, "failed", run["error"])
                    return run
                run["usage"] = ctx.usage.as_dict()
                if isinstance(res, Pending):
                    run["pending"][step_id] = res.state
                elif isinstance(res, NeedsInput):
                    run["pending"][step_id] = res.state
                    run["needs_input"] = {"step": step_id, "message": res.message, "input_schema": res.input_schema}
                    run["status"] = "needs_input"
                    run["log"].append({"step": step_id, "event": "needs_input", "detail": res.message})
                    store.save_run(run)
                    await self._alert(run, spec, "has a question", res.message)
                    return run
                else:
                    run["steps"][step_id] = res
                    del run["pending"][step_id]
                    run["log"].append({"step": step_id, "event": "done", "usage": _delta(ctx.usage, before).as_dict()})
            store.save_run(run)
            if run["pending"]:
                return run
        return await self.advance(run)

    async def answer(self, run_id: str, data: dict) -> dict:
        async with self._lock(run_id):
            run = store.get_run(run_id)
            if not run or run["status"] != "needs_input":
                raise InputError("run is not waiting for input")
            flagged = await safety.moderate(_text_of(data))
            if flagged:
                raise InputError(f"input flagged: {', '.join(flagged)}")
            spec = MonsterSpec.model_validate(run["spec"])
            step_id = run["needs_input"]["step"]
            ctx = self._ctx(run, spec, step_id)
            res = await self.limb_for(self._leaf(spec, step_id).limb, spec).answer(run["pending"][step_id], data, ctx)
            run["pending"][step_id] = res.state
            run["needs_input"] = None
            run["status"] = "waiting"
            store.save_run(run)
            return run

    async def confirm(self, run_id: str, approve: bool) -> dict:
        async with self._lock(run_id):
            run = store.get_run(run_id)
            if not run or run["status"] != "needs_confirmation":
                raise InputError("run is not waiting for confirmation")
            run["approved" if approve else "declined"] += run["confirm"]["steps"]
            run["log"].append({"step": None, "event": "approved" if approve else "declined", "detail": run["confirm"]})
            run["confirm"] = None
            run["status"] = "queued"
            store.save_run(run)
        return await self.advance(run)

    async def resume_all(self):
        for run in store.runs_with_status("queued", "running"):
            await self.advance(run)

    async def poll_forever(self, interval: float = config.SOKOSUMI_POLL_SECONDS):
        while True:
            for run in store.runs_with_status("waiting"):
                try:
                    await self.poll(run)
                except Exception:
                    log.exception("poll failed for run %s", run["id"])
            await asyncio.sleep(interval)

    async def run_to_end(self, spec: MonsterSpec, inputs: dict, interval: float = config.SOKOSUMI_POLL_SECONDS, **kw) -> dict:
        run = await self.start(spec, inputs, **kw)
        while run["status"] == "waiting":
            await asyncio.sleep(interval)
            run = await self.poll(run)
        return run
