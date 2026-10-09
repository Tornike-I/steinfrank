"""Интерфейс monster-lab коллег на НАШЕМ движке: контракт их API (forge/publish/runs/SSE/inputs/confirm/voice).
Мозг — сценарий ответов (считаем вызовы), погода — локальный сервер Open-Meteo, ElevenLabs — подменённый транспорт."""
import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import config, lab_api
from app.brain import Brain
from app.lab_voice import LabVoice
from helpers import ScriptedLLM
from test_architecture import OpenMeteoFake

WEATHER_BRIEF = ('A weather assistant monster: the one assistant for everything about weather. It is run again and again.\n'
                 'Inputs: exactly one required string input named "question".\nIt must handle these jobs well:\n'
                 '- what is the weather in Prague tomorrow\nVoice archetype: golem.')


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "lab.db")
    monkeypatch.setattr(config, "ALLOW_NETWORK_CAPABILITIES", True)
    monkeypatch.setattr(config, "USE_TEMPLATES", True)
    monkeypatch.setattr(config, "LAB_FAST_RESPONSES", True)
    meteo = OpenMeteoFake()
    monkeypatch.setenv("FRANK_WEATHER_GEO_URL", f"http://127.0.0.1:{meteo.port}/geo")
    monkeypatch.setenv("FRANK_WEATHER_FORECAST_URL", f"http://127.0.0.1:{meteo.port}/forecast")
    monkeypatch.setattr(lab_api, "SPEECH_DIR", tmp_path / "speech")
    brain = Brain(llm=ScriptedLLM([]))
    lab_api.attach(brain)
    app = FastAPI()
    app.include_router(lab_api.router)
    yield TestClient(app), brain, meteo
    meteo.srv.shutdown()


def wait_run(client, rid, until=("completed", "failed", "blocked", "needs_input", "needs_confirmation"), timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.get(f"/runs/{rid}").json()
        if r["status"] in until:
            return r
        time.sleep(0.2)
    raise AssertionError(f"run {rid} stuck: {r['status']}")


def forge_weather(client):
    res = client.post("/forge", json={"description": WEATHER_BRIEF}).json()
    assert res["status"] == "draft" and res["design_tokens"] == 0
    card = client.post(f"/monsters/{res['spec']['id']}/publish", json={"voice": False}).json()
    return res, card


def test_forge_is_instant_and_uses_the_verified_template(lab):
    client, brain, _ = lab
    t0 = time.time()
    res, card = forge_weather(client)
    spec = res["spec"]
    assert spec["id"] == "weather-monster" and spec["inputs"]["required"] == ["question"]
    assert [s["id"] for s in spec["steps"]] == ["understand", "knowledge", "skill", "think", "build", "research", "compose"]
    assert [s["name"] for s in spec["skills"]] == ["get_weather"]            # шаблон поставлен сразу, без модели
    assert brain.llm.prompts == [] and time.time() - t0 < 60
    assert card["id"] == "weather-monster" and "http_fetch" in card["limbs"] and card["has_voice"] is False
    assert [c["id"] for c in client.get("/monsters").json()] == ["weather-monster"]
    assert client.get("/pricing").json() == {"voice_call_per_min": 0.08}
    # повторная кузница той же темы: тот же монстр, новая версия, ни одного нового монстра
    again = client.post("/forge", json={"description": WEATHER_BRIEF.replace("Prague tomorrow", "Dubai this week")}).json()
    assert again["spec"]["id"] == "weather-monster" and again["spec"]["version"] == 2
    assert len(brain.monsters.list()) == 1 and len(again["spec"]["jobs"]) == 2


def test_run_goes_through_our_engine_with_real_steps_and_usage(lab):
    client, brain, meteo = lab
    forge_weather(client)
    run = client.post("/monsters/weather-monster/runs", json={"inputs": {"question": "Weather in Kyiv and Prague"}}).json()
    assert run["status"] in ("queued", "running")
    r = wait_run(client, run["id"])
    assert r["status"] == "completed", r
    assert "### Kyiv" in r["output"]["report"] and "### Prague" in r["output"]["report"]
    assert r["output"]["speech"] and "|" not in r["output"]["speech"] and "#" not in r["output"]["speech"]   # речь без разметки
    log = {e["step"]: e["event"] for e in r["log"]}
    assert log["understand"] == "done" and log["skill"] == "done" and log["think"] == "skipped" and log["build"] == "skipped"
    assert r["usage"]["llm_tokens"] == 0 and sorted(meteo.queries) == ["Kyiv", "Prague"]


def test_common_typo_still_uses_the_fast_skill(lab):
    client, brain, meteo = lab
    forge_weather(client)
    rid = client.post("/monsters/weather-monster/runs", json={"inputs": {"question": "Wheather in Oslo"}}).json()["id"]
    r = wait_run(client, rid)
    assert r["status"] == "completed", r
    assert "### Oslo" in r["output"]["report"] and meteo.queries[-1] == "Oslo"
    assert brain.llm.prompts == []


def test_answer_voice_matches_friend_archetype_and_plays_through_ui(lab, monkeypatch):
    client, brain, _ = lab
    monkeypatch.setattr(config, "LAB_TYPED_TTS", True)

    class FriendVoice:
        voice_id = None

        @staticmethod
        def configured():
            return True

        def speak_lab(self, text, voice_id=None):
            self.voice_id = voice_id
            return {"audio": "SUQz", "text": text, "chars": len(text)}

    voice = FriendVoice()
    brain.voice = voice
    draft, _ = forge_weather(client)
    expected = lab_api._friend_voice_id(lab_api._row("weather-monster"), "")
    assert draft["spec"]["speak"] is True and draft["spec"]["voice"]["voice_id"] == expected

    rid = client.post("/monsters/weather-monster/runs", json={"inputs": {"question": "Weather in Oslo"}}).json()["id"]
    r = wait_run(client, rid)
    assert r["status"] == "completed" and r["output"]["speech_audio_url"] == f"/artifacts/lab/{rid}.mp3"
    assert voice.voice_id == expected
    assert client.get(r["output"]["speech_audio_url"]).content == b"ID3"


def test_current_words_do_not_trigger_paid_research_without_an_explicit_request(lab):
    client, brain, _ = lab
    assert {"planning", "composing"} <= brain.llm.extra_fast_purposes
    forge_weather(client)
    brain.llm.replies.append(json.dumps({
        "intent": "task", "understanding": {"en": "current job market", "cs": "trh práce"}, "params": {},
        "requirements": [], "needs_internet": True,
        "direct_answer": {"en": "Software and healthcare roles remain broadly in demand.", "cs": "Software a zdravotnictví."},
    }))
    rid = client.post("/monsters/weather-monster/runs",
                      json={"inputs": {"question": "What is the current top job on the market?"}}).json()["id"]
    r = wait_run(client, rid)

    assert r["status"] == "completed", r
    assert "Software and healthcare" in r["output"]["report"]
    assert "hire" not in r["output"]["report"].lower() and "credits" not in r["output"]["report"].lower()
    rs = brain.skills.researcher
    assert not rs.should_offer("latest jobs on the market", needs_internet=True)
    assert rs.should_offer("research the latest jobs on the internet", needs_internet=True)


def test_missing_city_becomes_needs_input_and_the_answer_continues(lab):
    client, _, meteo = lab
    forge_weather(client)
    rid = client.post("/monsters/weather-monster/runs", json={"inputs": {"question": "What's the weather tomorrow?"}}).json()["id"]
    r = wait_run(client, rid)
    assert r["status"] == "needs_input" and r["needs_input"]["message"] == "For which city?"
    client.post(f"/runs/{rid}/inputs", json={"answer": "Prague"})
    r = wait_run(client, rid, until=("completed", "failed"))
    assert r["status"] == "completed" and "### Prague" in r["output"]["report"] and meteo.queries[-1] == "Prague"


def test_new_ability_waits_for_confirmation_and_decline_builds_nothing(lab):
    client, brain, _ = lab
    forge_weather(client)
    spec = {"name": "translator", "description": "translates", "purpose_en": "Translate text", "purpose_cs": "Překlad",
            "inputs": {"text": "str"}, "outputs": {"text": "str"}, "dependencies": [], "network": False}
    brain.llm.replies.append(json.dumps({"intent": "task", "understanding": {"en": "translate", "cs": "x"}, "params": {},
                                         "requirements": [{"id": "r1", "need": {"en": "t", "cs": "t"}, "decision": "CREATE", "spec": spec}]}))
    rid = client.post("/monsters/weather-monster/runs", json={"inputs": {"question": "Translate hello into French"}}).json()["id"]
    r = wait_run(client, rid)
    assert r["status"] == "needs_confirmation" and r["confirm"]["steps"] == ["build"] and "Build it?" in r["confirm"]["message"]
    client.post(f"/runs/{rid}/confirm", json={"approve": False})
    r = wait_run(client, rid, until=("completed", "failed"))
    assert r["status"] == "completed" and "nothing was built" in r["output"]["report"]
    assert brain.registry.get("translator") is None


def test_empty_lab_monster_never_builds_without_ok(lab):
    """Кузница мгновенная, монстр пустой: правило «первые органы без вопросов» здесь выключено."""
    client, brain, _ = lab
    brief = WEATHER_BRIEF.replace("weather", "cooking").replace("what is the cooking in Prague tomorrow", "plan a dinner menu")
    sid = client.post("/forge", json={"description": brief}).json()["spec"]["id"]
    client.post(f"/monsters/{sid}/publish", json={"voice": False})
    spec = {"name": "menu_planner", "description": "plans menus", "purpose_en": "Plan a menu", "purpose_cs": "Menu",
            "inputs": {"guests": "int"}, "outputs": {"menu": "str"}, "dependencies": [], "network": False}
    brain.llm.replies.append(json.dumps({"intent": "task", "understanding": {"en": "menu", "cs": "x"}, "params": {},
                                         "requirements": [{"id": "r1", "need": {"en": "m", "cs": "m"}, "decision": "CREATE", "spec": spec}]}))
    rid = client.post(f"/monsters/{sid}/runs", json={"inputs": {"question": "Plan a dinner menu for 6 guests"}}).json()["id"]
    r = wait_run(client, rid)
    assert r["status"] == "needs_confirmation" and r["confirm"]["steps"] == ["build"] and "no organ for this yet" in r["confirm"]["message"]
    assert brain.registry.get("menu_planner") is None


def test_sse_stream_ends_on_completion(lab):
    client, _, _ = lab
    forge_weather(client)
    rid = client.post("/monsters/weather-monster/runs", json={"inputs": {"question": "Weather in Oslo"}}).json()["id"]
    statuses = []
    with client.stream("GET", f"/runs/{rid}/events") as s:
        for line in s.iter_lines():
            if line.startswith("data: "):
                statuses.append(json.loads(line[6:])["status"])
    assert statuses[-1] == "completed" and len(statuses) >= 2


def test_voice_agent_with_client_tools(tmp_path, monkeypatch):
    calls = []

    def fake(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content) if req.content else None
        calls.append((req.method, req.url.path, body))
        if req.url.path.endswith("/tools"):
            return httpx.Response(200, json={"id": f"tool_{len(calls)}"})
        if req.url.path.endswith("/agents/create"):
            return httpx.Response(200, json={"agent_id": "agent_1"})
        if "get-signed-url" in req.url.path:
            return httpx.Response(200, json={"signed_url": "wss://signed"})
        return httpx.Response(200, json={})
    from app.db import Database
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "k")
    v = LabVoice(Database(tmp_path / "v.db"), transport=httpx.MockTransport(fake))
    agent = v.ensure_agent(sid="weather-monster", name="Herr Gauss", topic="weather", purpose="p", language="cs", voice_id="voice1")
    assert agent == "agent_1"
    tools = [b["tool_config"]["name"] for m, p, b in calls if p.endswith("/tools")]
    assert tools == ["start_run", "check_run", "answer_input", "confirm_spend"]
    create = next(b for m, p, b in calls if p.endswith("/agents/create"))
    assert create["conversation_config"]["tts"]["voice_id"] == "voice1" and create["conversation_config"]["agent"]["language"] == "cs"
    assert "Always speak Czech" in create["conversation_config"]["agent"]["prompt"]["prompt"]
    v.ensure_agent(sid="x", name="n", topic="t", purpose="p", language="en", voice_id="v")           # инструменты — один раз
    assert len([1 for m, p, b in calls if p.endswith("/tools")]) == 4
    assert v.signed_url("agent_1") == "wss://signed"
