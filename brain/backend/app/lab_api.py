"""СЛОЙ СОВМЕСТИМОСТИ с интерфейсом monster-lab (Three.js) коллег — их фронтенд, наш движок.

Их интерфейс говорит с бэкендом по контракту frankenstein/api.py:
  POST /forge {description}            -> черновик монстра темы        GET /monsters, GET /monsters/{id}
  POST /monsters/{id}/publish {voice}  -> карточка монстра             GET /pricing
  POST /monsters/{id}/runs {inputs}    -> запуск                       GET /runs/{id}, GET /runs/{id}/events (SSE)
  POST /runs/{id}/inputs {answer}      -> ответ на вопрос монстра      POST /runs/{id}/confirm {approve}
  GET  /monsters/{id}/voice-session    -> подписанная ссылка живого голосового звонка
Здесь каждый вызов переводится на НАШ движок (Brain): навыки и пакеты знаний (0 токенов на повтор), кэш ответов,
барьер создания органов, шаблоны, Sokosumi с автоподбором агента, бюджеты, учёт ≈ $.

Соответствия:
  их «монстр темы» (weather-monster)  = наш монстр (monsters) + строка lab_monsters;
  их «кузница»                         = МГНОВЕННОЕ создание монстра (0 токенов) + проверенный шаблон, если тема им покрыта;
                                         органы вырастают при первом вопросе, которому они нужны;
  их «запуск»                          = цепочка наших задач (ответ на вопрос и подтверждение — новые задачи той же цепочки);
  их needs_input                       = наш вопрос монстра (навык узнал запрос, но не хватает значения);
  их needs_confirmation                = наш барьер органа ИЛИ предложение нанять агента Sokosumi;
  их шаги воркфлоу                     = наш реальный путь: понять -> знания -> навык -> ИИ -> орган -> Sokosumi -> ответ.
"""
import asyncio
import base64
import hashlib
import json
import re
import threading
import time
import uuid
from functools import cache

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from . import config, pricing, templates
from .brain import speakable
from .db import dumps, loads, now
from .knowledge_packs import slug
from .lab_voice import LabVoice, VoiceAgentError

router = APIRouter(tags=["monster-lab"])
B = None                                   # Brain — подключает main.attach_lab()
VOICE: LabVoice | None = None
SPEECH_DIR = config.DATA_DIR / "lab_speech"
TERMINAL = {"completed", "failed", "blocked"}
QUESTION_SCHEMA = {"type": "object", "required": ["question"],
                   "properties": {"question": {"type": "string", "maxLength": 500,
                                               "description": "What you want from the monster, in your own words"}}}
# характер (archetype их голосов) -> эмоция нашего монстра (внешность, голос, звуки)
ARCH_EMOTION = {"brute": "angry", "gremlin": "happy", "lich": "sad", "butler": "neutral", "hag": "sad", "golem": "neutral",
                "igor": "happy", "swarm": "angry"}
VOICE_CALL_PER_MIN = 0.08                  # как у коллег (pricing.py): цена минуты живого звонка ElevenLabs, ≈ $

# Шаги воркфлоу = НАШ реальный путь. limb определяет иконку и «конечность» монстра в их 3D (limbParts.js).
STEPS = [
    {"id": "understand", "limb": "forged:skill_matcher", "note": "Find a learned skill for the request (no AI)"},
    {"id": "knowledge", "limb": "forged:knowledge_packs", "note": "Load saved knowledge; fetch only what is missing"},
    {"id": "skill", "limb": "forged:learned_skill", "note": "Run the learned skill / recipe"},
    {"id": "think", "limb": "llm", "note": "AI only for what is not known yet", "when": "no learned skill can answer"},
    {"id": "build", "limb": "forged:new_organ", "note": "Grow a new organ — only with your OK", "when": "a genuinely new ability is needed"},
    {"id": "research", "limb": "sokosumi", "note": "Hire a Sokosumi agent for current data — only with your OK",
     "when": "you ask for current data"},
    {"id": "compose", "limb": "forged:compose", "note": "Compose the answer and the report"},
]
THINK_PURPOSES = {"planning", "routing", "slot", "knowledge", "knowledge_extend", "knowledge_main", "composing", "diagnosing"}
BUILD_PURPOSES = {"building", "repairing", "mutating", "abstracting", "pruning"}


def attach(brain) -> None:
    global B, VOICE
    B = brain
    VOICE = LabVoice(brain.db)
    # кузница здесь мгновенная и монстр рождается пустым — «первые органы бесплатно» превратили бы любой вопрос
    # в минуты генерации кода (было: 442 с на «current top job on the market»). Сборка — только после «да».
    brain.first_organs_free = False
    # В основном Friend UI исследование доступно по кнопке Research или прямой просьбе человека, но не навязывается
    # на каждый вопрос со словами current/latest/рынок. Это сохраняет Sokosumi как опцию без задержек и случайных трат.
    brain.skills.researcher.explicit_only = config.LAB_RESEARCH_MODE != "automatic"
    if config.LAB_FAST_RESPONSES:
        brain.llm.extra_fast_purposes.update({"planning", "composing"})


# ====================================================================== монстры
class ForgeBody(BaseModel):
    description: str


class PublishBody(BaseModel):
    voice: bool = True


def parse_brief(desc: str) -> dict:
    """Бриф их кузницы: «A weather assistant monster: … jobs: - … Voice archetype: golem.»"""
    m = re.search(r"\bA[n]?\s+(.+?)\s+assistant monster", desc, re.I)
    topic = (m.group(1) if m else desc.split(".")[0][:40]).strip().lower() or "general"
    jobs = [j.strip()[2:].strip() for j in desc.splitlines() if j.strip().startswith("- ")]
    arch = (re.search(r"Voice archetype:\s*(\w+)", desc) or [None, "brute"])[1]
    cs = bool(re.search(r'voice\.language "cs"|Czech', desc)) or bool(re.search(r"[ěščřžůťďň]", " ".join(jobs), re.I))
    return {"topic": topic, "jobs": jobs or [desc.strip()[:300]], "archetype": arch, "language": "cs" if cs else "en"}


def _row(sid: str) -> dict | None:
    return B.db.one("SELECT * FROM lab_monsters WHERE sid=?", (sid,))


@cache
def _friend_voice_table() -> dict:
    """The canonical voice archetypes owned by Friend/frankenstein."""
    path = config.ROOT_DIR.parent / "frankenstein" / "frankenstein" / "voices.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("archetypes", {})
    except (OSError, json.JSONDecodeError):
        return {}


def _friend_voice_id(row: dict, fallback: str) -> str:
    choices = (_friend_voice_table().get(row.get("archetype")) or
               _friend_voice_table().get("brute") or {}).get("voices", [])
    if not choices:
        return fallback
    # Identical to Friend/frankenstein/voices.py:pick_voice(archetype, spec.id).
    index = int(hashlib.sha256(row["sid"].encode()).hexdigest(), 16) % len(choices)
    return choices[index]


def _monster(row: dict) -> dict:
    m = B.monsters.get(row["monster_id"])
    if not m:
        raise HTTPException(404, "the monster behind this card is gone")
    voice_id = _friend_voice_id(row, m["voice_id"])
    if voice_id != m["voice_id"]:
        B.db.execute("UPDATE monsters SET voice_id=? WHERE id=?", (voice_id, m["id"]))
        m["voice_id"] = voice_id
    return m


def _purpose(row: dict) -> str:
    return (f"Your assistant for everything about {row['topic']}: learns each kind of job once, then answers related "
            "requests from learned skills and saved knowledge, spending AI tokens only on what is missing.")


def _limbs(row: dict, m: dict) -> list[str]:
    caps = [c for c in m["capabilities"]]
    out = {"forged:skill_matcher", "forged:knowledge_packs", "llm", "forged:compose"}
    out |= {f"forged:{c['name']}" for c in caps[:4]}
    if any(c.get("network") for c in caps):
        out.add("http_fetch")
    if B.skills.researcher.available():
        out.add("sokosumi")
    return sorted(out)


def _usage(llm_tokens=0, credits=0.0, seconds=0.0, tts_chars=0, usd=0.0) -> dict:
    return {"llm_tokens": int(llm_tokens), "credits": round(float(credits), 2), "seconds": round(float(seconds), 1), "images": 0,
            "tts_chars": int(tts_chars), "usd": round(float(usd), 6)}


def _step_estimates() -> dict:
    """Оценка по ИЗМЕРЕННЫМ задачам этого мозга (средние по режимам); без замеров — нули."""
    rows = B.db.query("SELECT metrics FROM tasks WHERE metrics IS NOT NULL ORDER BY id DESC LIMIT 200")
    tiers: dict[str, list] = {}
    for r in rows:
        mt = loads(r["metrics"], {}) or {}
        tiers.setdefault(mt.get("tier") or "smart", []).append(mt)

    def avg(tier, key):
        xs = [float(x.get(key) or 0) for x in tiers.get(tier, [])]
        return sum(xs) / len(xs) if xs else 0.0
    smart_tok = avg("smart", "prompt_tokens") + avg("smart", "completion_tokens")
    build_tok = avg("build", "prompt_tokens") + avg("build", "completion_tokens")
    # только локальный каталог (без сети): эта функция вызывается и проверкой «бэкенд жив?» с таймаутом 2,5 с
    have_catalog = B.db.one("SELECT COUNT(*) n FROM sokosumi_agents")["n"] > 0
    price = B.skills.researcher.market.select("research current data") if have_catalog else []
    credits = (price[0].get("price") or 0) if price else 0
    return {"understand": _usage(seconds=0.5), "knowledge": _usage(seconds=0.3), "skill": _usage(seconds=1),
            "think": _usage(llm_tokens=smart_tok, seconds=avg("smart", "seconds"), usd=pricing.llm(config.ELEVENLABS_LLM, smart_tok * .3, smart_tok * .7)),
            "build": _usage(llm_tokens=build_tok, seconds=avg("build", "seconds"), usd=pricing.llm(config.ELEVENLABS_LLM, build_tok * .3, build_tok * .7)),
            "research": _usage(credits=credits, seconds=180, usd=credits * pricing.SOKOSUMI_CREDIT),
            "compose": _usage(seconds=0.2),
            "speech": _usage(tts_chars=400, usd=pricing.tts(config.ELEVENLABS_MODEL, 400))}


def _estimate(est: dict) -> dict:
    def total(keys):
        out = _usage()
        for k in keys:
            for f in ("llm_tokens", "credits", "seconds", "tts_chars", "usd"):
                out[f] += est[k][f]
        return out
    return {"min": total(["understand", "knowledge", "skill", "compose"]), "max": total(list(est))}


def _sounds(m: dict) -> dict:
    u = B.theatrics.urls(m)
    return {**{k: u["sounds"].get(k) for k in ("arrive", "working", "done")}}, u["birth"]


def spec_of(row: dict) -> dict:
    m = _monster(row)
    sounds, birth = _sounds(m)
    est = _step_estimates()
    jobs = loads(row["jobs"], [])
    return {"id": row["sid"], "name": m["name"], "version": row["version"], "purpose": _purpose(row),
            "persona": f"{m['personality'] or m['emotion']} monster of the topic {row['topic']}",
            "inputs": QUESTION_SCHEMA, "policy": {}, "steps": STEPS, "output": {"speech": "", "report": ""},
            "speak": config.LAB_TYPED_TTS,
            "forged_limbs": [], "examples": [{"question": j} for j in jobs[:2]], "estimate": _estimate(est),
            "safety": {"verdict": "allow", "notes": "frankenstein brain"},
            "voice": {"elevenlabs_agent_id": row["voice_agent_id"], "archetype": row["archetype"], "language": row["language"],
                      "first_message": "", "cast": [], "voice_id": m["voice_id"], "sounds": sounds, "birth_url": birth},
            "step_estimates": est,
            "skills": [{"name": c["name"], "version": c["version"]} for c in m["capabilities"]], "jobs": jobs}


def card_of(row: dict) -> dict:
    m = _monster(row)
    sounds, birth = _sounds(m)
    est = _step_estimates()
    done = B.db.query("SELECT t.metrics FROM tasks t WHERE t.monster_id=? AND t.status='completed' AND t.metrics IS NOT NULL",
                      (row["monster_id"],))
    ms = [loads(r["metrics"], {}) or {} for r in done]
    observed = ({"runs": len(ms), "avg_llm_tokens": round(sum((x.get("prompt_tokens") or 0) + (x.get("completion_tokens") or 0) for x in ms) / len(ms)),
                 "avg_credits": round(sum(float(x.get("credits") or 0) for x in ms) / len(ms), 1), "avg_tts_chars": 0} if ms else None)
    return {"id": row["sid"], "name": m["name"], "version": row["version"], "purpose": _purpose(row), "inputs": QUESTION_SCHEMA,
            "estimate": _estimate(est), "workflow_estimate": _estimate({k: v for k, v in est.items() if k != "speech"}),
            "observed": observed, "limbs": _limbs(row, m), "has_voice": bool(row["voice_agent_id"]),
            "voice": {"archetype": row["archetype"], "language": row["language"], "sounds": sounds, "birth_url": birth}}


@router.post("/forge")
def forge(body: ForgeBody):
    """МГНОВЕННАЯ кузница: монстр темы создаётся сразу (0 токенов); проверенный шаблон — если тема им покрыта."""
    if B is None:
        raise HTTPException(503, "brain is not ready")
    b = parse_brief(body.description)
    sid = f"{slug(b['topic'])[:40]}-monster"
    row = _row(sid)
    if row:          # тема уже есть: монстр «учит» новые задачи (запоминаем; органы вырастут при первом нужном вопросе)
        jobs = list(dict.fromkeys(loads(row["jobs"], []) + b["jobs"]))
        B.db.execute("UPDATE lab_monsters SET jobs=?, version=version+1, updated_at=? WHERE sid=?", (dumps(jobs), now(), sid))
    else:
        m = B.monsters.create(ARCH_EMOTION.get(b["archetype"], "neutral"))
        B.db.execute("UPDATE monsters SET status='alive' WHERE id=?", (m["id"],))
        B.db.execute("INSERT INTO lab_monsters(sid,monster_id,topic,purpose,archetype,language,jobs,published,version,created_at,updated_at)"
                     " VALUES(?,?,?,?,?,?,?,0,1,?,?)",
                     (sid, m["id"], b["topic"], "", b["archetype"], b["language"], dumps(b["jobs"]), now(), now()))
    row = _row(sid)
    m = _monster(row)
    # проверенный шаблон (погода, собеседования): навык готов сразу, без генерации кода моделью
    tid = templates.match_spec({"description": f"{b['topic']} " + " ".join(b["jobs"])})
    if tid:
        name = templates.TEMPLATES[tid]["name"]
        have = B.registry.get(name)
        if not have or have.get("status") != "active":
            templates.install(tid, builder=B.builder, registry=B.registry, bus=B.bus, name=name)
        if B.registry.get(name):
            B.monsters.attach(m["id"], name, source="library", task_id=None)
    # Старые процедурные звуки интерфейса играют мгновенно в браузере. Дополнительные
    # ElevenLabs-эффекты можно включить отдельно, но даже тогда кузница их не ждёт.
    if config.LAB_GENERATED_AUDIO:
        B.theatrics.prepare_async(_monster(row), task=b["jobs"][0], lang=b["language"], birth=True)
    return {"status": "draft", "spec": spec_of(_row(sid)), "errors": [], "warnings": [], "reason": "", "design_tokens": 0}


@router.get("/drafts/{sid}")
def draft(sid: str):
    row = _row(sid)
    if not row:
        raise HTTPException(404, "no such draft")
    return spec_of(row)


@router.post("/monsters/{sid}/publish")
def publish(sid: str, body: PublishBody = PublishBody()):
    row = _row(sid)
    if not row:
        raise HTTPException(404, "no such draft")
    agent = row["voice_agent_id"]
    if body.voice and VOICE.configured():
        m = _monster(row)
        try:
            agent = VOICE.ensure_agent(sid=sid, name=m["name"], topic=row["topic"], purpose=_purpose(row), language=row["language"],
                                       voice_id=m["voice_id"], agent_id=agent)
        except VoiceAgentError as exc:
            B.bus.emit("LAB_VOICE_FAILED", monster=sid, error=str(exc)[:300])     # без голоса — но монстр работает
    B.db.execute("UPDATE lab_monsters SET published=1, voice_agent_id=?, updated_at=? WHERE sid=?", (agent, now(), sid))
    return card_of(_row(sid))


@router.get("/monsters")
def list_monsters():
    return [card_of(r) for r in B.db.query("SELECT * FROM lab_monsters WHERE published=1 ORDER BY created_at")
            if B.monsters.get(r["monster_id"])]


@router.get("/monsters/{sid}")
def get_monster(sid: str):
    row = _row(sid)
    if not row:
        raise HTTPException(404, "no such monster")
    return spec_of(row)


@router.get("/pricing")
def lab_pricing():
    return {"voice_call_per_min": VOICE_CALL_PER_MIN}


@router.get("/monsters/{sid}/voice-session")
def voice_session(sid: str):
    row = _row(sid)
    if not row:
        raise HTTPException(404, "no such monster")
    if not row["voice_agent_id"]:
        raise HTTPException(409, "monster has no voice agent; publish it with voice")
    try:
        return {"signed_url": VOICE.signed_url(row["voice_agent_id"])}
    except VoiceAgentError as exc:
        raise HTTPException(502, str(exc))


# ====================================================================== запуски
class RunBody(BaseModel):
    inputs: dict = {}
    deliver_to: list[str] = []
    notify_on_complete: bool = False


class AnswerBody(BaseModel):
    answer: str | None = None
    data: dict | None = None


class ConfirmBody(BaseModel):
    approve: bool


def _run_row(rid: str) -> dict:
    r = B.db.one("SELECT * FROM lab_runs WHERE rid=?", (rid,))
    if not r:
        raise HTTPException(404, "no such run")
    return r


def _state(rid: str) -> dict:
    return loads(_run_row(rid)["state"], {}) or {}


def _save(rid: str, **changes) -> None:
    r = _run_row(rid)
    st = {**(loads(r["state"], {}) or {}), **changes.pop("state", {})}
    tasks = changes.pop("tasks", loads(r["tasks"], []))
    B.db.execute("UPDATE lab_runs SET state=?, tasks=?, updated_at=? WHERE rid=?", (dumps(st), dumps(tasks), now(), rid))


def _lang(text: str) -> str:
    return "cs" if re.search(r"[ěščřžůťďň]", text, re.I) else "en"


def _work(rid: str, text: str, allow: bool = False) -> None:
    """Фоновый работник запуска: ждёт свободный мозг, запускает задачу, ждёт её конца, готовит озвучку."""
    row = _run_row(rid)
    lab = _row(row["sid"])
    try:
        deadline = time.time() + 900
        while True:
            try:
                out = B.start_task(text, [], _lang(text), mode="reuse", monster_id=lab["monster_id"], allow_build=allow)
                break
            except RuntimeError as exc:                      # мозг занят другой задачей — ждём своей очереди
                if "busy" not in str(exc) or time.time() > deadline:
                    raise
                time.sleep(0.3)
        tid = out["task_id"]
        _save(rid, tasks=loads(_run_row(rid)["tasks"], []) + [tid], state={"phase": "running", "error": None})
        while B.task(tid)["status"] == "running":
            time.sleep(0.25)
        t = B.task(tid)
        data = ((t.get("result") or {}).get("data") or {}) if t["status"] == "completed" else {}
        if (data.get("research") or {}).get("started"):
            _save(rid, state={"phase": "researching"})
            kind, report = _wait_research(tid)
            if kind == "report":                      # отчёт агента пришёл отдельным ответом — он и есть результат
                _save(rid, tasks=loads(_run_row(rid)["tasks"], []) + [report])
            elif kind == "rerun":                     # агент обновил пакет знаний — переспрашиваем: ответ уже со свежими данными
                return _work(rid, text, False)
        _finalize(rid)
    except Exception as exc:  # noqa: BLE001
        _save(rid, state={"phase": "failed", "error": str(exc)[:400]})


def _wait_research(tid: int, timeout: float = 1800) -> tuple[str | None, int | None]:
    """Агент Sokosumi работает в фоне; запуск ждёт его (в их интерфейсе тикают часы «Hired agent working»).
    ("report", id) — отчёт пришёл отдельным ответом; ("rerun", None) — агент обновил пакет знаний; (None, None) — не вышло."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        for r in B.db.query("SELECT task_id, event_type, payload FROM evolution_events WHERE event_type IN "
                            "('KNOWLEDGE_RESEARCH_DONE','KNOWLEDGE_RESEARCH_FAILED') ORDER BY id DESC LIMIT 20"):
            p = loads(r["payload"], {}) or {}
            if p.get("for_task") != tid and r["task_id"] != tid:
                continue
            if r["event_type"] == "KNOWLEDGE_RESEARCH_FAILED":
                return None, None
            return ("report", r["task_id"]) if r["task_id"] != tid else ("rerun", None)
        time.sleep(0.5)
    return None, None


def _finalize(rid: str) -> None:
    """Задача закончилась: вопрос/подтверждение — ждём человека; иначе готовим озвучку и закрываем запуск."""
    pub = public_run(rid)
    if pub["status"] in ("needs_input", "needs_confirmation", "failed"):
        _save(rid, state={"phase": "waiting_user" if pub["status"] != "failed" else "failed"})
        return
    _save(rid, state={"phase": "speaking"})
    speech = _speech_text(rid)
    url = None
    if config.LAB_TYPED_TTS and speech and B.voice.configured():
        try:
            lab = _row(_run_row(rid)["sid"])
            m = _monster(lab)
            v = B.voice.speak_lab(speech, voice_id=m["voice_id"])
            SPEECH_DIR.mkdir(parents=True, exist_ok=True)
            (SPEECH_DIR / f"{rid}.mp3").write_bytes(base64.b64decode(v["audio"]))
            url, speech = f"/artifacts/lab/{rid}.mp3", v["text"]
            _save(rid, state={"tts_chars": v["chars"]})
        except Exception as exc:  # noqa: BLE001 — без озвучки ответ всё равно показывается
            B.bus.emit("LAB_SPEECH_FAILED", error=str(exc)[:200])
    _save(rid, state={"phase": "done", "speech": speech, "speech_url": url})


def _speech_text(rid: str) -> str:
    t = _last_task(rid)
    s = (t.get("result") or {}).get("summary") if t else ""
    text = (s.get("en") if isinstance(s, dict) else s) or ""
    return speakable(text)[:900]


def _last_task(rid: str) -> dict | None:
    tasks = loads(_run_row(rid)["tasks"], [])
    return B.task(tasks[-1]) if tasks else None


def _summary(t: dict, lang: str = "en") -> str:
    s = (t.get("result") or {}).get("summary")
    return (s.get(lang) or s.get("en")) if isinstance(s, dict) else str(s or "")


def _log_and_usage(task_ids: list[int], st: dict) -> tuple[list[dict], dict]:
    """Шаги воркфлоу из НАШИХ событий и реальный расход по шагам (токены, кредиты, ≈ $)."""
    if not task_ids:
        return [], _usage()
    q = ",".join("?" * len(task_ids))
    events = [e["event_type"] for e in B.db.query(f"SELECT event_type FROM evolution_events WHERE task_id IN ({q}) ORDER BY id", task_ids)]
    rows = B.db.query(f"SELECT kind, purpose, model, prompt_tokens, completion_tokens, chars, credits, seconds FROM usage"
                      f" WHERE task_id IN ({q})", task_ids)
    by = {"think": _usage(), "build": _usage(), "research": _usage(), "skill": _usage()}
    for u in rows:
        step = ("think" if u["purpose"] in THINK_PURPOSES else "build" if u["purpose"] in BUILD_PURPOSES else
                "research" if u["kind"] == "sokosumi" else "skill" if u["kind"] == "tool" else None)
        if not step:
            continue
        x = by[step]
        x["llm_tokens"] += (u["prompt_tokens"] or 0) + (u["completion_tokens"] or 0)
        x["credits"] += float(u["credits"] or 0)
        x["seconds"] += float(u["seconds"] or 0)
        x["usd"] += pricing.usage_row(u)
    has = lambda *names: any(e in names for e in events)   # noqa: E731
    done = {
        "understand": has("SKILL_MATCHED", "SKILL_NO_MATCH", "SKILL_INPUT_RECEIVED", "SKILL_NEEDS_INPUT", "TASK_ANALYZED", "KNOWLEDGE_CACHE_HIT"),
        "knowledge": has("KNOWLEDGE_PACK_LOADED", "KNOWLEDGE_PACK_SAVED", "KNOWLEDGE_CACHE_HIT"),
        "skill": has("SKILL_EXECUTED", "CAPABILITY_REUSED", "WORKFLOW_RECIPE_REUSED") or (has("WORKFLOW_RUNNING") and has("TASK_COMPLETED")),
        "think": has("LLM_CALL_DONE", "LLM_CACHE_HIT"),
        "build": has("CAPABILITY_INSTALLED", "CAPABILITY_MUTATED"),
        "research": has("KNOWLEDGE_RESEARCH_DONE") or any(u["kind"] == "sokosumi" for u in rows),
        "compose": has("TASK_COMPLETED"),
    }
    running = {"build": has("CAPABILITY_BUILD_STARTED", "CAPABILITY_MUTATION_STARTED") and not done["build"],
               "research": st.get("phase") == "researching"}
    finished = st.get("phase") in ("done", "waiting_user", "failed", "speaking")
    # пока ждём агента Sokosumi, всё до него закрыто — их интерфейс подсвечивает именно шаг «Sokosumi» с часами
    closed_before = {s["id"] for s in STEPS[:5]} if st.get("phase") == "researching" else set()
    log = []
    for s in STEPS:
        sid = s["id"]
        if done[sid]:
            log.append({"step": sid, "event": "done", "usage": by.get(sid) or _usage()})
        elif (finished and not running.get(sid)) or sid in closed_before:
            log.append({"step": sid, "event": "skipped"})
    if st.get("phase") == "done" and st.get("speech"):
        chars = st.get("tts_chars") or 0
        log.append({"step": "speech", "event": "voiced" if st.get("speech_url") else "rewritten",
                    "usage": _usage(tts_chars=chars, usd=pricing.tts(config.ELEVENLABS_MODEL, chars))})
    total = _usage()
    for x in by.values():
        for f in ("llm_tokens", "credits", "seconds", "usd"):
            total[f] += x[f]
    total["tts_chars"] = st.get("tts_chars") or 0
    total["usd"] += pricing.tts(config.ELEVENLABS_MODEL, total["tts_chars"])
    return log, total


def public_run(rid: str) -> dict:
    r = _run_row(rid)
    st = loads(r["state"], {}) or {}
    tasks = loads(r["tasks"], [])
    t = B.task(tasks[-1]) if tasks else None
    out = {"id": rid, "monster_id": r["sid"], "status": "queued", "output": None, "error": None, "needs_input": None, "confirm": None,
           "usage": _usage(), "estimate": _estimate(_step_estimates()), "log": [], "watch_id": None}
    log, usage = _log_and_usage(tasks, st)
    out["log"], out["usage"] = log, usage
    phase = st.get("phase")
    if phase == "failed":
        out.update(status="failed", error=st.get("error") or (t or {}).get("error") or "failed")
        return out
    if not t or phase in (None, "running", "researching"):
        out["status"] = "queued" if not t else ("waiting" if phase == "researching" else "running")
        return out
    if t["status"] in ("failed", "cancelled"):
        out.update(status="failed", error=t.get("error") or t["status"])
        return out
    data = (t.get("result") or {}).get("data") or {}
    if data.get("needs_input") and not st.get("answered"):
        out.update(status="needs_input", needs_input={"step": "understand", "message": data["needs_input"].get("question"),
                                                      "input_schema": {"input_data": [{"id": "answer", "type": "string", "name": "Answer"}]}})
        return out
    decision = data.get("decision") if (data.get("decision") or {}).get("user_confirmation_required") else None
    offer = data.get("offer") if (data.get("offer") or {}).get("user_confirmation_required") else None
    if (decision or offer) and not st.get("decided"):
        d = decision or offer
        credits = float((d.get("estimated_cost") or {}).get("credits") or 0)
        if offer:
            agent = (offer.get("agent") or {}).get("name", "a Sokosumi agent")
            hire = (f"I would hire {agent} for about {credits:g} credits (≈ ${credits * pricing.SOKOSUMI_CREDIT:.2f}); "
                    "it takes minutes.")
            if str(data.get("answered_from", "")).startswith("research offer"):     # ответа из памяти нет — только живые данные
                msg = f"This needs research in current, real sources. {hire}"
            else:
                msg = (f"My answer from memory: {speakable(_summary(t))[:300]} — Want current data from real sources? {hire}")
            steps = ["research"]
        else:
            cost = d.get("estimated_cost") or {}
            msg = (f"{d.get('reasoning_summary', '')} Usual cost: ~{cost.get('tokens', '?')} tokens. Build it?" if cost else
                   f"{d.get('reasoning_summary', '')} Build it?")
            steps = ["build"]
        out.update(status="needs_confirmation", confirm={"steps": steps, "credits": credits, "message": msg})
        return out
    if st.get("phase") != "done":
        out["status"] = "running"                      # готовим озвучку
        return out
    report = _summary(t)
    if st.get("declined") and decision:
        report = "OK — nothing was built. Ask me differently or confirm next time if this needs a new ability."
    out.update(status="completed", output={"speech": st.get("speech") or speakable(report)[:900], "report": report,
                                           "speech_audio_url": st.get("speech_url"), "memory": {}})
    return out


@router.post("/monsters/{sid}/runs")
def start_run(sid: str, body: RunBody):
    if not _row(sid):
        raise HTTPException(404, "no such monster")
    text = str(body.inputs.get("question") or " ".join(str(v) for v in body.inputs.values())).strip()
    if not text:
        raise HTTPException(422, "missing input 'question'")
    rid = uuid.uuid4().hex[:12]
    B.db.execute("INSERT INTO lab_runs(rid,sid,text,tasks,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                 (rid, sid, text[:2000], "[]", dumps({"phase": None}), now(), now()))
    threading.Thread(target=_work, args=(rid, text), daemon=True).start()
    return public_run(rid)


@router.get("/runs/{rid}")
def get_run(rid: str):
    return public_run(rid)


@router.get("/runs/{rid}/events")
async def run_events(rid: str):
    _run_row(rid)

    async def stream():
        last = None
        while True:
            pub = public_run(rid)
            snap = dumps(pub)
            if snap != last:
                last = snap
                yield f"data: {json.dumps(pub, default=str)}\n\n"
                if pub["status"] in TERMINAL:
                    return
            await asyncio.sleep(0.6)
    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/runs/{rid}/inputs")
def answer(rid: str, body: AnswerBody):
    pub = public_run(rid)
    if pub["status"] != "needs_input":
        raise HTTPException(409, "run is not waiting for input")
    text = (body.answer or " ".join(str(v) for v in (body.data or {}).values())).strip()
    if not text:
        raise HTTPException(422, "empty answer")
    _save(rid, state={"phase": "running", "answered": False})
    threading.Thread(target=_work, args=(rid, text), daemon=True).start()     # ответ подставится в тот же навык
    return public_run(rid)


@router.post("/runs/{rid}/confirm")
def confirm(rid: str, body: ConfirmBody):
    pub = public_run(rid)
    if pub["status"] != "needs_confirmation":
        raise HTTPException(409, "run is not waiting for confirmation")
    if body.approve:
        _save(rid, state={"phase": "running"})
        threading.Thread(target=_work, args=(rid, _run_row(rid)["text"], True), daemon=True).start()
    else:
        _save(rid, state={"decided": True, "declined": True})
        threading.Thread(target=_finalize, args=(rid,), daemon=True).start()
    return public_run(rid)


@router.get("/artifacts/lab/{name}")
def speech_file(name: str):
    p = (SPEECH_DIR / name).resolve()
    if p.parent != SPEECH_DIR.resolve() or p.suffix != ".mp3" or not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="audio/mpeg")
