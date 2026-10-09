"""HTTP API Frankenstein (FastAPI).

Запуск:  uvicorn app.main:app --port 8000
Фронтенд (Vite) в режиме разработки проксирует /api сюда; собранный фронтенд (frontend/dist),
если он есть, раздаётся этим же сервером.
"""
import asyncio
import json
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, demo, economy, eleven, metrics
from .brain import Brain
from .security import RateLimiter, check_token, validate_upload
from .settings import Settings
from .tts import VoiceError

app = FastAPI(title="Frankenstein")
brain = Brain()
limiter = RateLimiter()


@app.middleware("http")
async def guard(request: Request, call_next):
    """Токен доступа и ограничение частоты для всех /api-запросов."""
    try:
        check_token(request)
        limiter.check(request)
    except HTTPException as exc:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.get("/api/health")
def health():
    return {"ok": True, "auth_required": bool(config.APP_ACCESS_TOKEN)}


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name)[:120] or "file"


def _resolve_files(names: list[str]) -> list[Path]:
    """Имя файла -> путь. Принимаем только "demo:set_a/x.pdf" или имя загруженного файла,
    выйти за пределы этих двух папок нельзя."""
    found: list[Path] = []
    for raw in names:
        if raw.startswith("demo:"):
            base = config.DEMO_DIR.resolve()
            path = (base / raw[5:]).resolve()
        else:
            base = config.UPLOADS_DIR.resolve()
            path = (base / _safe_name(raw)).resolve()
        if base not in path.parents or not path.is_file():
            raise HTTPException(400, f"file not found: {raw}")
        found.append(path)
    return found


# ---------------------------------------------------------------- состояние
@app.get("/api/state")
def state(events: int = 500):
    caps = brain.registry.list()
    return {
        "generation": brain.registry.generation(),
        "busy": brain.is_busy(),
        "current_task_id": brain.current_task_id,
        "llm": brain.llm.describe(),
        "voice": {"configured": brain.voice.configured(), "voice_id": config.ELEVENLABS_VOICE_ID},
        "settings": brain.settings.get(),
        "monsters": brain.monsters.list(),
        "usage": {**brain.ledger.summary(), "per_task": brain.ledger.per_task(), "credit_usd": config.CREDIT_USD, "budget_credits": config.BUDGET_CREDITS},
        "sandbox": {"mode": brain.sandbox.mode() if _sandbox_ok() else "unavailable",
                    "docker_available": brain.sandbox.docker_available()},
        "capabilities": caps,
        "graveyard": brain.registry.graveyard(),
        "generations": brain.registry.generations(),
        "metrics": metrics.collect(brain.db, brain.registry),
        "events": brain.bus.history(limit=events) if events else [],
        "tasks": [brain.task(r["id"]) for r in brain.db.query("SELECT id FROM tasks ORDER BY id DESC LIMIT 10")],
    }


def _sandbox_ok() -> bool:
    try:
        brain.sandbox.mode()
        return True
    except Exception:  # noqa: BLE001
        return False


@app.get("/api/capabilities/{name}")
def capability(name: str):
    cap = brain.registry.get(name)
    if not cap:
        raise HTTPException(404, "unknown capability")
    info = brain.registry.code_of(name) or {}
    return {**cap, "code": info.get("code"), "test_code": info.get("tests"),
            "failure_history": brain.registry.failure_history(name)}


# ---------------------------------------------------------------- поток событий (SSE)
@app.get("/api/events/stream")
async def stream(after: int = 0):
    loop = asyncio.get_running_loop()
    queue = brain.bus.subscribe(loop)

    async def gen():
        last = after
        try:
            for ev in brain.bus.history(after=after, limit=1000):   # сначала пропущенное
                last = ev["id"]
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            while True:
                try:
                    ev = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if ev["id"] > last:
                    last = ev["id"]
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            brain.bus.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- задачи
class TaskIn(BaseModel):
    text: str
    files: list[str] = []
    lang: str = "en"
    mode: str = "new"                 # new — собрать нового монстра; reuse — выполнить существующим
    monster_id: int | None = None
    emotion: str = "neutral"          # эмоция нового монстра
    upgrade: bool = False             # reuse + «пусть доучится»: планировщик охотнее мутирует/создаёт органы
    allow_build: bool = False         # пользователь подтвердил дорогую сборку/переписывание органа (барьер gate.py)
    monster_ids: list[int] = []       # mode=team: участники (первый — ведущий)


def _require_ready() -> None:
    if not brain.llm.configured():
        raise HTTPException(503, f"{brain.llm.describe()['missing']} is not set. Put it into backend/.env and restart the server.")
    if brain.is_busy():
        raise HTTPException(409, "Frankenstein is busy with another task")


@app.post("/api/tasks", status_code=202)
def create_task(body: TaskIn):
    _require_ready()
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "empty task")
    if len(text) > 4000:
        raise HTTPException(400, "task is too long (max 4000 characters)")
    if body.mode not in ("new", "reuse", "team"):
        raise HTTPException(400, "mode must be 'new', 'reuse' or 'team'")
    try:
        return brain.start_task(text, _resolve_files(body.files), body.lang if body.lang in ("en", "cs") else "en",
                                monster_id=body.monster_id, mode=body.mode, emotion=body.emotion, upgrade=body.upgrade,
                                monster_ids=body.monster_ids, allow_build=body.allow_build)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: int):
    if not brain.cancel_task(task_id):
        raise HTTPException(409, "this task is not running")
    return {"ok": True}


# ---------------------------------------------------------------- монстры
@app.get("/api/monsters")
def monsters(include_failed: bool = False):
    return brain.monsters.list(include_failed)


@app.get("/api/monsters/{monster_id}")
def monster(monster_id: int):
    m = brain.monsters.get(monster_id)
    if not m:
        raise HTTPException(404, "unknown monster")
    return {**m, "memory": brain.monsters.memory(monster_id, 10)}


class RecommendIn(BaseModel):
    text: str


@app.post("/api/recommend")
def recommend(body: RecommendIn):
    """Подсказка «этот монстр уже умеет…» (эвристика без вызова модели)."""
    return brain.monsters.recommend(body.text)


@app.post("/api/recommend/team")
def recommend_team(body: RecommendIn):
    """Команда монстров, чьи РЕАЛЬНЫЕ навыки покрывают разные части запроса (без вызова модели)."""
    return brain.monsters.recommend_team(body.text)


class SkillIn(BaseModel):
    name: str


@app.post("/api/monsters/{monster_id}/skills")
def assign_skill(monster_id: int, body: SkillIn):
    """Выдать монстру существующий навык из общего реестра (проверка прав; реализация не копируется)."""
    if not brain.monsters.get(monster_id):
        raise HTTPException(404, "unknown monster")
    ok, why = brain.monsters.assign(monster_id, body.name)
    if not ok:
        raise HTTPException(403 if "private" in why or "internet" in why else 409, why)
    brain.bus.emit("SKILL_SHARED", capability=body.name, monster_id=monster_id)
    return brain.monsters.get(monster_id)


@app.delete("/api/monsters/{monster_id}/skills/{name}")
def revoke_skill(monster_id: int, name: str):
    if not brain.monsters.revoke(monster_id, name):
        raise HTTPException(404, "the monster does not have this skill")
    brain.bus.emit("SKILL_REVOKED", capability=name, monster_id=monster_id)
    return brain.monsters.get(monster_id)


class PinIn(BaseModel):
    version: str | None = None


@app.post("/api/monsters/{monster_id}/skills/{name}/pin")
def pin_skill(monster_id: int, name: str, body: PinIn):
    """Закрепить версию навыка у монстра (None — всегда последняя рабочая)."""
    if body.version and not brain.registry.code_of(name, body.version):
        raise HTTPException(404, "unknown version")
    brain.db.execute("UPDATE monster_capabilities SET pinned_version=? WHERE monster_id=? AND capability=?",
                     (body.version, monster_id, name))
    return {"ok": True}


@app.post("/api/capabilities/{name}/rollback")
def rollback(name: str):
    if brain.is_busy():
        raise HTTPException(409, "Frankenstein is busy")
    v = brain.registry.rollback(name)
    if not v:
        raise HTTPException(409, "no previous version to roll back to")
    brain.knowledge.expire_skill(name)
    brain.bus.emit("CAPABILITY_ROLLED_BACK", capability=name, version=v)
    return {"ok": True, "version": v}


class VisibilityIn(BaseModel):
    visibility: str


@app.put("/api/capabilities/{name}/visibility")
def set_visibility(name: str, body: VisibilityIn):
    if body.visibility not in ("global", "private"):
        raise HTTPException(400, "visibility must be 'global' or 'private'")
    brain.registry.set_owner(name, None, body.visibility)
    return brain.registry.get(name)


@app.get("/api/economy")
def token_economy():
    return {**economy.report(brain.db), "knowledge": brain.knowledge.summary()}


@app.get("/api/monsters/{monster_id}/theatrics")
def monster_theatrics(monster_id: int):
    """Звуки монстра (по характеру) и его сцена рождения — только уже готовые файлы."""
    m = brain.monsters.get(monster_id)
    if not m:
        raise HTTPException(404, "unknown monster")
    return brain.theatrics.urls(m)


@app.get("/api/theatrics/{rel:path}")
def theatrics_file(rel: str):
    p = brain.theatrics.file(rel)
    if not p:
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="audio/mpeg")


@app.get("/api/sokosumi/agents")
def sokosumi_agents(need: str = "", limit: int = 5):
    """Какого агента Sokosumi система выберет под потребность и почему (по локальному каталогу, без ИИ)."""
    from .agent_market import timed_select
    market = brain.skills.researcher.market
    picked, ms = timed_select(market, need, limit=limit) if need else ([], 0.0)
    return {"enabled": market.enabled(), "catalog_size": len(market.catalog()), "need": need, "selected": picked, "select_ms": ms,
            "error": market.last_error}


@app.get("/api/knowledge-packs")
def knowledge_packs(kind: str | None = None):
    """Пакеты знаний (предметные данные отдельно от органов): что монстры уже знают и откуда."""
    return brain.packs.summary() if not kind else [p for p in brain.packs.summary() if p["kind"] == kind]


@app.get("/api/knowledge-packs/{kind}/{key}")
def knowledge_pack(kind: str, key: str):
    p = brain.packs.get(kind, key)
    if not p:
        raise HTTPException(404, "unknown knowledge pack")
    return p


@app.get("/api/knowledge")
def knowledge(limit: int = 30):
    return brain.knowledge.list(limit)


# ---------------------------------------------------------------- баланс, расход и провайдеры
@app.get("/api/balance")
def balance():
    sokosumi = None
    if config.SOKOSUMI_API_KEY and brain.settings.get()["llm_provider"] == "sokosumi":
        try:
            from .sokosumi import SokosumiClient
            sokosumi = SokosumiClient().credits()
        except Exception:  # noqa: BLE001
            sokosumi = None
    return {"elevenlabs": eleven.subscription(), "sokosumi": sokosumi,
            "usage": {**brain.ledger.summary(), **brain.ledger.breakdown()},
            "credit_usd": config.CREDIT_USD}


@app.get("/api/settings")
def get_settings():
    return {"current": brain.settings.get(), "options": Settings.options(), "llm": brain.llm.describe(),
            "keys": {"elevenlabs": bool(config.ELEVENLABS_API_KEY), "sokosumi": bool(config.SOKOSUMI_API_KEY),
                     "openai": bool(config.OPENAI_API_KEY)},
            "sandbox": brain.sandbox.mode() if _sandbox_ok() else "unavailable"}


@app.put("/api/settings")
def put_settings(changes: dict):
    if brain.is_busy():
        raise HTTPException(409, "cannot change providers while a task is running")
    try:
        return {"current": brain.settings.update(changes), "llm": brain.llm.describe()}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ---------------------------------------------------------------- распознавание речи
@app.post("/api/transcribe")
async def transcribe(file: UploadFile = File(...), lang: str = Form("en")):
    data = await file.read()
    if len(data) < 200:
        raise HTTPException(400, "recording is too short")
    if len(data) > 25 * 1024 * 1024:
        raise HTTPException(413, "recording is too large")
    if brain.settings.get()["stt_provider"] != "elevenlabs":
        raise HTTPException(409, "server speech recognition is disabled; the browser recognizer is used instead")
    try:
        out = eleven.transcribe(data, file.filename or "speech.webm", file.content_type or "audio/webm", lang)
    except eleven.ElevenError as exc:
        raise HTTPException(503 if not config.ELEVENLABS_API_KEY else 502, str(exc))
    brain.ledger.record("stt", provider="elevenlabs", model=config.ELEVENLABS_STT_MODEL, seconds=out["seconds"])
    return out


@app.get("/api/tasks/{task_id}")
def get_task(task_id: int):
    task = brain.task(task_id)
    if not task:
        raise HTTPException(404, "unknown task")
    return task


@app.post("/api/self-audit", status_code=202)
def self_audit(lang: str = "en"):
    _require_ready()
    return {"task_id": brain.start_self_audit(lang)}


@app.post("/api/capabilities/{name}/mutate", status_code=202)
def mutate(name: str):
    _require_ready()
    if name not in brain.registry.active_names():
        raise HTTPException(404, "unknown or retired capability")
    return {"task_id": brain.start_mutation(name)}


@app.post("/api/capabilities/{name}/retire")
def retire(name: str):
    if brain.is_busy():
        raise HTTPException(409, "Frankenstein is busy")
    if name not in brain.registry.active_names():
        raise HTTPException(404, "unknown or retired capability")
    result = brain.evolution.retire_manually(name)
    if result["ok"]:
        gen = brain.registry.bump_generation("manual retirement")
        brain.bus.emit("GENERATION_UPDATED", generation=gen, previous=gen - 1)
    return result


# ---------------------------------------------------------------- демо и файлы
@app.get("/api/demo/tasks")
def demo_tasks():
    return demo.demo_definitions()


@app.post("/api/demo/reset")
def demo_reset():
    if brain.is_busy():
        raise HTTPException(409, "Frankenstein is busy")
    brain.reset()
    return {"ok": True}


class SpeakIn(BaseModel):
    text: str
    mood: str = "neutral"
    monster_id: int | None = None


@app.post("/api/speak")
def speak(body: SpeakIn):
    """Озвучка с таймкодами слов (ElevenLabs). Возвращает base64-аудио и список слов для «караоке»."""
    try:
        if brain.settings.get()["tts_provider"] == "off":
            raise VoiceError("voice output is turned off in settings")
        m = brain.monsters.get(body.monster_id) if body.monster_id else None
        return brain.voice.speak(body.text, body.mood, voice_id=m["voice_id"] if m else None)
    except VoiceError as exc:
        raise HTTPException(503 if not brain.voice.configured() else 502, str(exc))


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    name = _safe_name(file.filename or "file")
    data = await file.read()
    validate_upload(name, data)
    (config.UPLOADS_DIR / name).write_bytes(data)
    return {"name": name, "bytes": len(data)}


@app.get("/api/artifacts/{task_id}/{filename:path}")
def artifact(task_id: int, filename: str):
    base = (config.ARTIFACTS_DIR / str(task_id)).resolve()
    path = (base / filename).resolve()
    if base not in path.parents or not path.is_file():
        raise HTTPException(404, "file not found")
    return FileResponse(path, filename=path.name)


# Собранный фронтенд (npm run build) раздаём этим же сервером
# ---------------------------------------------------------------- интерфейс monster-lab коллег (их фронтенд, наш движок)
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from . import lab_api  # noqa: E402

lab_api.attach(brain)
app.include_router(lab_api.router)
app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])

_dist = config.ROOT_DIR / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="ui")
