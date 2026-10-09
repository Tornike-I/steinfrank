import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import channels, config, pricing, store, watches
from .forge import pipeline, voice
from .forge.estimator import estimate, step_estimates
from .runtime import InputError, Runner

logging.basicConfig(level=logging.INFO)
runner = Runner()
_tasks: set[asyncio.Task] = set()


def _spawn(coro):
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@asynccontextmanager
async def lifespan(app):
    _spawn(runner.resume_all())
    _spawn(runner.poll_forever())
    _spawn(watches.loop(runner))
    yield


app = FastAPI(title="Frankenstein", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?", allow_methods=["*"], allow_headers=["*"]
)
config.ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/artifacts", StaticFiles(directory=config.ARTIFACTS_DIR), name="artifacts")


class ForgeBody(BaseModel):
    description: str


class PublishBody(BaseModel):
    voice: bool = True


class RunBody(BaseModel):
    inputs: dict = {}
    deliver_to: list[str] = []
    notify_on_complete: bool = False


class AnswerBody(BaseModel):
    answer: str | None = None
    data: dict | None = None


class ConfirmBody(BaseModel):
    approve: bool


class DestinationBody(BaseModel):
    channel: str
    target: dict = {}
    label: str = ""


class WatchBody(BaseModel):
    monster_id: str
    inputs: dict = {}
    every_seconds: int
    deliver_to: list[str] = []
    label: str = ""


def _observed(monster_id: str) -> dict | None:
    done = [r for r in store.runs_for_monster(monster_id) if r["status"] == "completed" and not r.get("dry_run")]
    if not done:
        return None
    n = len(done)
    return {
        "runs": n,
        "avg_llm_tokens": round(sum(r["usage"]["llm_tokens"] for r in done) / n),
        "avg_credits": round(sum(r["usage"]["credits"] for r in done) / n, 1),
        "avg_tts_chars": round(sum(r["usage"]["tts_chars"] for r in done) / n),
    }


def _card(spec) -> dict:
    return {
        "id": spec.id, "name": spec.name, "version": spec.version, "purpose": spec.purpose,
        "inputs": spec.inputs, "estimate": estimate(spec).model_dump(), "workflow_estimate": estimate(spec, voice=False).model_dump(), "observed": _observed(spec.id),
        "limbs": sorted({s.limb for s in spec.leaves()}), "has_voice": bool(spec.voice.elevenlabs_agent_id),
        "voice": spec.voice.model_dump(include={"archetype", "language", "sounds", "birth_url"}),
    }


def _public_run(run: dict) -> dict:
    keys = ("id", "monster_id", "status", "output", "error", "needs_input", "confirm", "usage", "estimate", "log", "watch_id")
    return {k: run.get(k) for k in keys}


def _monster(monster_id: str):
    spec = store.load_monster(monster_id)
    if not spec:
        raise HTTPException(404, "no such monster")
    return spec


def _run(run_id: str) -> dict:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "no such run")
    return run


@app.post("/forge")
async def forge(body: ForgeBody):
    try:
        return (await pipeline.forge(body.description)).as_dict()
    except RuntimeError as e:  # e.g. "forging needs OPENAI_API_KEY": tell the UI why
        raise HTTPException(503, str(e))


@app.get("/drafts/{monster_id}")
def get_draft(monster_id: str):
    draft = store.get_draft(monster_id)
    if not draft:
        raise HTTPException(404, "no such draft")
    return draft


@app.post("/monsters/{monster_id}/publish")
async def publish(monster_id: str, body: PublishBody = PublishBody()):
    draft = store.get_draft(monster_id)
    if not draft:
        raise HTTPException(404, "no such draft")
    try:
        spec = await pipeline.publish(draft, with_voice=body.voice)
    except (ValueError, voice.VoiceError) as e:
        raise HTTPException(422, str(e))
    return _card(spec)


@app.get("/monsters")
def list_monsters():
    return [_card(s) for s in store.list_monsters()]


@app.get("/monsters/{monster_id}")
def get_monster(monster_id: str):
    spec = _monster(monster_id)
    return {**spec.dump(), "step_estimates": step_estimates(spec)}


@app.get("/monsters/{monster_id}/voice-session")
async def voice_session(monster_id: str):
    spec = _monster(monster_id)
    if not spec.voice.elevenlabs_agent_id:
        raise HTTPException(409, "monster has no voice agent; publish it with voice")
    try:
        return {"signed_url": await voice.signed_url(spec.voice.elevenlabs_agent_id)}
    except voice.VoiceError as e:
        raise HTTPException(502, str(e))


@app.post("/monsters/{monster_id}/runs")
async def start_run(monster_id: str, body: RunBody):
    spec = _monster(monster_id)
    missing = [d for d in body.deliver_to if store.get_destination(d) is None]
    if missing:
        raise HTTPException(422, f"unknown destinations {missing}")
    try:
        run = await runner.create(spec, body.inputs, deliver_to=body.deliver_to, notify_on_complete=body.notify_on_complete)
    except InputError as e:
        raise HTTPException(422, str(e))
    if run["status"] == "queued":
        _spawn(runner.advance(run))
    return _public_run(run)


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    return _public_run(_run(run_id))


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    _run(run_id)

    async def stream():
        last = None
        while True:
            run = store.get_run(run_id)
            if run["updated"] != last:
                last = run["updated"]
                yield f"data: {json.dumps(_public_run(run), default=str)}\n\n"
                if run["status"] in ("completed", "failed", "blocked"):
                    return
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/runs/{run_id}/inputs")
async def answer(run_id: str, body: AnswerBody):
    run = _run(run_id)
    data = body.data
    if data is None:
        schema = (run.get("needs_input") or {}).get("input_schema") or {}
        fields = [f for f in (schema.get("input_data") or []) if f.get("type") != "none"] if isinstance(schema, dict) else []
        if len(fields) != 1:
            raise HTTPException(422, "this question needs structured data, not a single answer")
        data = {fields[0]["id"]: body.answer}
    try:
        return _public_run(await runner.answer(run_id, data))
    except InputError as e:
        raise HTTPException(409, str(e))


@app.post("/runs/{run_id}/confirm")
async def confirm(run_id: str, body: ConfirmBody):
    _run(run_id)
    try:
        run = await runner.confirm(run_id, body.approve)
    except InputError as e:
        raise HTTPException(409, str(e))
    return _public_run(run)


@app.get("/channels")
def list_channels():
    return [c.describe() for c in channels.CHANNELS.values()]


@app.post("/destinations")
def create_destination(body: DestinationBody):
    try:
        return channels.public_destination(channels.create_destination(body.channel, body.target, body.label))
    except channels.ChannelError as e:
        raise HTTPException(422, str(e))


@app.get("/destinations")
def list_destinations():
    return [channels.public_destination(d) for d in store.list_destinations()]


@app.delete("/destinations/{dest_id}")
def delete_destination(dest_id: str):
    store.delete_destination(dest_id)
    return {"deleted": dest_id}


@app.post("/destinations/{dest_id}/test")
async def test_destination(dest_id: str):
    if store.get_destination(dest_id) is None:
        raise HTTPException(404, "no such destination")
    return await channels.deliver([dest_id], channels.Message(title="Steinfrank test", body="Your monster can reach you here."))


@app.get("/inbox")
def inbox(after: int = 0):
    return store.inbox_since(after)


@app.get("/inbox/stream")
async def inbox_stream(after: int = 0):
    async def stream():
        cursor = after
        while True:
            for msg in store.inbox_since(cursor):
                cursor = msg["id"]
                yield f"data: {json.dumps(msg, default=str)}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/watches")
def create_watch(body: WatchBody):
    try:
        return watches.create_watch(body.monster_id, body.inputs, body.every_seconds, body.deliver_to, body.label)
    except watches.WatchError as e:
        raise HTTPException(422, str(e))


@app.get("/watches")
def list_watches():
    return store.list_watches()


@app.delete("/watches/{watch_id}")
def delete_watch(watch_id: str):
    store.delete_watch(watch_id)
    return {"deleted": watch_id}


@app.get("/pricing")
def get_pricing():
    return {"voice_call_per_min": pricing.VOICE_CALL_PER_MIN}


@app.get("/dev/voice")
def voice_test_page():
    return FileResponse(config.PKG_ROOT / "dev" / "voice_test.html")
