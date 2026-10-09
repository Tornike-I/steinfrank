"""Приёмочные сценарии экономики навыков (TEST A–H из задания).

Настоящие: песочница, сборщик и тесты навыков, реестр, память со сроком годности, учёт токенов, БД.
Подменены: «мозг» (сценарий ответов — именно он «написал» код навыков ниже) и интернет (локальный HTTP-сервер
вместо погодного провайдера, со счётчиком запросов — чтобы проверить, когда данные реально запрашиваются).
"""
import http.server
import json
import threading
import time

import pytest

from app import config
from app.brain import Brain
from app.economy import report
from helpers import ScriptedLLM, batch, files

# ============================================================== локальный «погодный провайдер»
class Provider:
    def __init__(self):
        self.hits, self.format = 0, "v1"
        self.queries: list[str] = []          # какие города реально запрашивались (проверка параметров)
        self.delay: dict[str, float] = {}     # искусственная задержка по городу (проверка параллельности)
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                outer.hits += 1
                from urllib.parse import urlparse, parse_qs
                u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/geo":
                    outer.queries.append(q["name"])
                    time.sleep(outer.delay.get(q["name"], 0))
                    body = {"results": [{"name": q["name"], "latitude": 50.0, "longitude": 14.0}]} if q["name"] != "Atlantis" else {}
                else:
                    n = int(q["days"])
                    from datetime import date, timedelta
                    first = date.fromisoformat(q["start"]) if q.get("start") else date(2026, 10, 10)
                    days = {"time": [(first + timedelta(days=i)).isoformat() for i in range(n)], "temperature_2m_max": [15 + i for i in range(n)],
                            "temperature_2m_min": [5 + i for i in range(n)], "precipitation_sum": [0.5] * n}
                    body = {"daily": days} if outer.format == "v1" else {"forecast": days}      # «провайдер сменил формат»
                data = json.dumps(body).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


# ============================================================== «сгенерированный моделью» навык погоды
WEATHER_SPEC = {"name": "get_weather", "description": "Weather forecast for any supported location and horizon",
                "purpose_en": "Retrieve weather forecasts for locations and dates", "purpose_cs": "Předpověď počasí",
                "inputs": {"location": "str", "start_date": "ISO date", "forecast_days": "int 1-16"},
                "outputs": {"location": "str", "days": "list"}, "dependencies": [], "network": True}

WEATHER_CODE = '''"""get_weather: forecast for a location. inputs: location, start_date, forecast_days. outputs: location, days, source."""
import json, re, urllib.error, urllib.parse, urllib.request
from datetime import date, timedelta
BASE = "http://127.0.0.1:{port}"
MAX_DAYS = 16
SKILL = {"intents": ["weather forecast", "current weather"],
         "keywords": ["weather", "forecast", "temperature", "rain", "počasí", "předpověď"],
         "examples": ["What's the weather in Prague tomorrow?", "Forecast for Berlin for 10 days", "Weather in London today"],
         "limits": {"forecast_days": "1-16"}, "freshness_seconds": 1800, "llm_slots": {}}

def _get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "frankenstein"}), timeout=10) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        if e.code >= 500:
            raise ConnectionError(f"provider unavailable: {e}")
        raise
    except (urllib.error.URLError, TimeoutError) as e:
        raise ConnectionError(f"provider unavailable: {e}")

def _days(fc):
    d = fc["daily"]
    return [{"date": d["time"][i], "t_max": d["temperature_2m_max"][i], "t_min": d["temperature_2m_min"][i],
             "rain_mm": d["precipitation_sum"][i]} for i in range(len(d["time"]))]

def run(inp):
    loc = inp.get("location")
    days = int(inp.get("forecast_days", 1))
    if not isinstance(loc, str) or not loc.strip():
        raise ValueError("location is required")
    if days < 1 or days > MAX_DAYS:
        raise ValueError(f"forecasts are available for 1-{MAX_DAYS} days only, not {days}")
    geo = _get("/geo", {"name": loc})
    if not geo.get("results"):
        raise ValueError(f"unknown location: {loc}")
    g = geo["results"][0]
    fc = _get("/forecast", {"lat": g["latitude"], "lon": g["longitude"], "days": days, "start": inp.get("start_date", "")})
    return {"location": g["name"], "days": _days(fc), "source": "local-meteo"}

def parse_request(text, context):
    t = text.lower()
    if not any(k in t for k in SKILL["keywords"]):
        return None
    m = re.search(r"\\b(?:in|for|v|ve)\\s+([A-Z\\u00c0-\\u017d][\\w-]+)", text)
    if not m or m.group(1).lower() in ("the", "next"):
        return None
    today = date.fromisoformat(context["today"])
    start, days = today, 1
    n = re.search(r"(\\d+)\\s*(?:days|day|dní|dny)", t)
    if n:
        days = int(n.group(1))
    elif "tomorrow" in t or "zítra" in t:
        start = today + timedelta(days=1)
    elif "week" in t:
        days = 7
    return {"location": m.group(1), "start_date": start.isoformat(), "forecast_days": days}

def format_result(result, lang):
    d = result["days"][0]
    return f"{result['location']}: {len(result['days'])} day(s), {d['date']} max {d['t_max']}C min {d['t_min']}C"
'''

WEATHER_TESTS = '''import unittest
import capability
CTX = {"today": "2026-10-09", "lang": "en"}
class T(unittest.TestCase):
    def test_tomorrow(self):
        p = capability.parse_request("What's the weather in Prague tomorrow?", CTX)
        self.assertEqual((p["location"], p["start_date"], p["forecast_days"]), ("Prague", "2026-10-10", 1))
    def test_horizon(self):
        self.assertEqual(capability.parse_request("Forecast for Berlin for 10 days", CTX)["forecast_days"], 10)
    def test_today(self):
        self.assertEqual(capability.parse_request("Weather in London today", CTX)["start_date"], "2026-10-09")
    def test_week(self):
        self.assertEqual(capability.parse_request("Will it rain in Paris this week?", CTX)["forecast_days"], 7)
    def test_czech(self):
        self.assertEqual(capability.parse_request("Jaké bude počasí v Brně zítra?", CTX)["location"], "Brně")
    def test_not_weather(self):
        self.assertIsNone(capability.parse_request("Count the words in this text", CTX))
    def test_no_location(self):
        self.assertIsNone(capability.parse_request("weather please", CTX))
    def test_too_long(self):
        with self.assertRaises(ValueError):
            capability.run({"location": "Prague", "forecast_days": 40})
    def test_missing_location(self):
        with self.assertRaises(ValueError):
            capability.run({"forecast_days": 2})
    def test_format(self):
        s = capability.format_result({"location": "Oslo", "days": [{"date": "2026-10-10", "t_max": 9, "t_min": 2}]}, "en")
        self.assertIn("Oslo", s); self.assertIn("max 9", s)
'''

FIXED_DAYS = '''def _days(fc):
    d = fc.get("daily") or fc.get("forecast")          # провайдер переименовал поле — поддерживаем оба формата
    return [{"date": d["time"][i], "t_max": d["temperature_2m_max"][i], "t_min": d["temperature_2m_min"][i],
             "rain_mm": d["precipitation_sum"][i]} for i in range(len(d["time"]))]
'''
NEW_FORMAT_TEST = '''    def test_new_provider_format(self):
        fc = {"forecast": {"time": ["2026-10-10"], "temperature_2m_max": [7], "temperature_2m_min": [1], "precipitation_sum": [0]}}
        self.assertEqual(capability._days(fc)[0]["t_max"], 7)
'''

# ============================================================== навык подготовки к собеседованию (MODE 3)
INTERVIEW_SPEC = {"name": "interview_prep", "description": "Reusable interview preparation workflow",
                  "purpose_en": "Prepare a structured interview plan for any role and company", "purpose_cs": "Příprava na pohovor",
                  "inputs": {"role": "str", "company": "str"}, "outputs": {"plan": "dict"}, "dependencies": [], "network": False}
INTERVIEW_CODE = '''"""interview_prep: reusable interview-preparation workflow (structure + rubric are code; fresh parts are LLM slots)."""
import re
SKILL = {"intents": ["interview preparation"], "keywords": ["interview", "pohovor"],
         "examples": ["Prepare me for a frontend developer interview at Acme"], "limits": {},
         "freshness_seconds": None,
         "llm_slots": {"role_requirements": {"prompt": "Current requirements for a {role} at {company}", "freshness_seconds": 1209600},
                       "new_questions": {"prompt": "Five new interview questions for a {role} at {company}", "freshness_seconds": None}}}
CATEGORIES = ["fundamentals", "system design", "behavioural", "role-specific"]
RUBRIC = {"clarity": 3, "correctness": 4, "depth": 3}

def run(inp):
    role, company = inp.get("role"), inp.get("company")
    if not role or not company:
        raise ValueError("role and company are required")
    slots = inp.get("slots") or {}
    return {"plan": {"role": role, "company": company, "categories": CATEGORIES, "rubric": RUBRIC,
                     "requirements": slots.get("role_requirements"), "questions": slots.get("new_questions"),
                     "progression": ["warm-up", "core", "stretch"]}}

def parse_request(text, context):
    m = re.search(r"for (?:the same |a |an )?(.+?) interview at (\\w+)", text, re.I)
    if not m:
        return None
    out = {"role": m.group(1).strip().lower(), "company": m.group(2)}
    if re.search(r"updated|new questions|latest", text, re.I):
        out["_refresh"] = ["new_questions"]
    return out

def format_result(result, lang):
    p = result["plan"]
    return f"Plan for {p['role']} at {p['company']}: {len(p['categories'])} areas; requirements: {p['requirements']}; questions: {p['questions']}"
'''
INTERVIEW_TESTS = '''import unittest
import capability
CTX = {"today": "2026-10-09", "lang": "en"}
class T(unittest.TestCase):
    def test_parse(self): self.assertEqual(capability.parse_request("Prepare me for a frontend developer interview at Acme", CTX)["company"], "Acme")
    def test_parse_refresh(self): self.assertEqual(capability.parse_request("Prepare me for the same frontend developer interview at Acme with updated questions", CTX)["_refresh"], ["new_questions"])
    def test_parse_other(self): self.assertIsNone(capability.parse_request("weather in Prague", CTX))
    def test_run(self): self.assertEqual(capability.run({"role": "dev", "company": "X", "slots": {"new_questions": ["q"]}})["plan"]["questions"], ["q"])
    def test_rubric(self): self.assertIn("depth", capability.run({"role": "dev", "company": "X"})["plan"]["rubric"])
    def test_missing(self):
        with self.assertRaises(ValueError): capability.run({"role": "dev"})
    def test_categories(self): self.assertEqual(len(capability.run({"role": "a", "company": "b"})["plan"]["categories"]), 4)
    def test_format(self): self.assertIn("Acme", capability.format_result(capability.run({"role": "a", "company": "Acme"}), "en"))
'''


def learn_plan(spec, name="learn"):
    return json.dumps({"intent": "learn", "understanding": {"en": name, "cs": name}, "requirements": [
        {"id": "r1", "need": {"en": "skill", "cs": "skill"}, "decision": "CREATE", "spec": spec}]})


def wait(brain, timeout=120):
    t0 = time.time()
    while brain.is_busy() and time.time() - t0 < timeout:
        time.sleep(0.1)
    assert not brain.is_busy()


def types(brain, tid):
    return [e["type"] for e in brain.bus.history(limit=5000) if e["task_id"] == tid]


def calls(brain, tid):
    return brain.ledger.summary(tid)["task"]["llm_calls"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "s.db")
    monkeypatch.setattr(config, "ALLOW_NETWORK_CAPABILITIES", True)
    provider = Provider()
    brain = Brain(llm=ScriptedLLM([]))
    yield brain, provider
    provider.srv.shutdown()


def teach_weather(brain, provider, emotion="happy"):
    brain.llm.replies += [learn_plan(WEATHER_SPEC, "learn weather"),
                          batch(("get_weather", WEATHER_CODE.replace("{port}", str(provider.port)), WEATHER_TESTS))]
    out = brain.start_task("Learn how to check the weather", [], "en", emotion=emotion); wait(brain)
    assert brain.task(out["task_id"])["status"] == "completed", brain.task(out["task_id"])["error"]
    return out


def ask(brain, text, **kw):
    out = brain.start_task(text, [], "en", **kw); wait(brain)
    return out, brain.task(out["task_id"])


# ============================================================== TEST A + H
def test_A_weather_learned_once_reused_with_different_parameters(env):
    brain, provider = env
    learned = teach_weather(brain, provider)
    assert calls(brain, learned["task_id"]) == 2                                  # план + генерация навыка (с тестами)
    mid = learned["monster_id"]
    assert [c["name"] for c in brain.monsters.get(mid)["capabilities"]] == ["get_weather"]
    skill = brain.registry.get("get_weather")
    assert skill["skill"]["callable"] and skill["tests_passed"] == skill["tests"] == 10

    _, t1 = ask(brain, "What's the weather in Prague tomorrow?", mode="reuse", monster_id=mid)
    assert t1["status"] == "completed" and t1["result"]["data"]["params"]["location"] == "Prague"
    assert t1["result"]["data"]["params"]["forecast_days"] == 1 and "Prague" in t1["result"]["summary"]["en"]
    hits_after_first = provider.hits

    _, t2 = ask(brain, "Show me the forecast for Berlin for 10 days", mode="reuse", monster_id=mid)
    assert t2["result"]["data"]["params"] == {"location": "Berlin", "start_date": t2["result"]["data"]["params"]["start_date"], "forecast_days": 10}
    assert len(t2["result"]["data"]["output"]["days"]) == 10

    for t in (t1, t2):                                                            # тот же навык, ноль вызовов модели
        assert t["result"]["data"]["skill"] == "get_weather" and calls(brain, t["id"]) == 0
        assert "CAPABILITY_BUILD_STARTED" not in types(brain, t["id"])
    assert len([c for c in brain.registry.list() if "weather" in c["name"]]) == 1   # никаких get_weather_berlin

    # тот же запрос ещё раз — свежие данные уже в памяти: ни модели, ни провайдера
    hits = provider.hits
    _, t3 = ask(brain, "What's the weather in Prague tomorrow?", mode="reuse", monster_id=mid)
    assert t3["result"]["data"]["mode"] == "cache" and provider.hits == hits and calls(brain, t3["id"]) == 0
    # устаревшие данные — обновляются, навык не пересобирается
    brain.db.execute("UPDATE knowledge SET expires_at='2000-01-01T00:00:00+00:00' WHERE skill='get_weather'")
    _, t4 = ask(brain, "What's the weather in Prague tomorrow?", mode="reuse", monster_id=mid)
    assert t4["result"]["data"]["mode"] == "deterministic" and provider.hits > hits and "KNOWLEDGE_STALE" in types(brain, t4["id"])
    assert hits_after_first >= 2

    # неподдерживаемый горизонт — честное ограничение, без выдумок и без пересборки
    _, t5 = ask(brain, "Weather forecast in Paris for 40 days", mode="reuse", monster_id=mid)
    assert t5["status"] == "completed" and "1-16 days only" in t5["result"]["summary"]["en"]
    assert t5["result"]["data"]["limitation"] and calls(brain, t5["id"]) == 0
    assert brain.registry.get("get_weather")["success_rate"] == 1.0              # честный отказ — не поломка навыка

    # TEST H: измеренная экономия с прозрачной базой
    eco = report(brain.db)
    m = eco["measured"]
    assert m["tasks_without_llm"] == 5 and m["skill_executions"] >= 3 and m["cache_hits"] >= 1 and m["tool_calls"] >= 3
    s = next(x for x in eco["estimated"]["per_skill"] if x["skill"] == "get_weather")
    assert s["baseline_task"] == learned["task_id"] and s["baseline_tokens"] > 0 and s["reuse_tokens"] == 0
    assert s["estimated_saved_tokens"] == s["baseline_tokens"] * s["reuses"]
    assert s["reuses"] == 4                                                       # Prague, Berlin, кэш, обновление; отказ на 40 дней не считается
    assert eco["measured"]["by_mode"]["learn"]["tokens"] > 0 and eco["measured"]["by_mode"]["deterministic"]["tokens"] == 0


# ============================================================== TEST B
def test_B_skill_survives_restart(env, monkeypatch):
    brain, provider = env
    teach_weather(brain, provider)
    restarted = Brain(llm=ScriptedLLM([]))                                       # «перезапуск»: новый процесс, та же БД
    _, t = ask(restarted, "Weather in Rome tomorrow")
    assert t["status"] == "completed" and t["result"]["data"]["skill"] == "get_weather"
    assert calls(restarted, t["id"]) == 0 and "CAPABILITY_BUILD_STARTED" not in types(restarted, t["id"])


# ============================================================== TEST F
def test_F_skill_shared_with_another_monster_without_duplication(env, monkeypatch):
    brain, provider = env
    weatherbot = teach_weather(brain, provider)["monster_id"]
    out, t = ask(brain, "What's the weather in Vienna tomorrow?", emotion="sad")   # TravelBot — новый монстр
    travel = brain.monsters.get(out["monster_id"])
    cap = next(c for c in travel["capabilities"] if c["name"] == "get_weather")
    assert cap["source"] == "library" and calls(brain, t["id"]) == 0               # подключён из реестра, не сгенерирован
    assert brain.registry.get("get_weather")["owner_monster_id"] == weatherbot
    assert len(brain.registry.list()) == 1
    # права: приватный навык другому монстру не выдаётся
    brain.registry.set_owner("get_weather", None, "private")
    brain.monsters.revoke(travel["id"], "get_weather")
    ok, why = brain.monsters.assign(travel["id"], "get_weather")
    assert not ok and "private" in why
    assert brain.skills.candidates("weather in Oslo", brain.monsters.get(travel["id"])) == []
    # сеть выключена оператором — сетевой навык не выдаётся и не исполняется
    brain.registry.set_owner("get_weather", None, "global")
    monkeypatch.setattr(config, "ALLOW_NETWORK_CAPABILITIES", False)
    assert brain.monsters.assign(travel["id"], "get_weather")[0] is False


# ============================================================== TEST G
def test_G_broken_skill_is_repaired_independently_and_can_roll_back(env):
    brain, provider = env
    weatherbot = teach_weather(brain, provider)["monster_id"]
    ask(brain, "Weather in Vienna tomorrow", emotion="neutral")                   # второй монстр с тем же навыком
    provider.format = "v2"                                                       # провайдер сменил формат ответа
    fixed = WEATHER_CODE.replace("{port}", str(provider.port))
    fixed = fixed[:fixed.index("def _days(fc):")] + FIXED_DAYS + fixed[fixed.index("\ndef run(inp):"):]
    brain.llm.replies += [files(fixed, WEATHER_TESTS + NEW_FORMAT_TEST, name="get_weather")]    # одна мутация
    _, t = ask(brain, "Weather in Madrid tomorrow", mode="reuse", monster_id=weatherbot)
    ev = types(brain, t["id"])
    assert t["status"] == "completed", t["error"]
    assert "SKILL_BROKEN" in ev and "CAPABILITY_MUTATED" in ev and t["result"]["data"]["version"] == "1.1.0"
    assert "CAPABILITY_BUILD_STARTED" not in ev and ev.count("CAPABILITY_MUTATION_STARTED") == 1   # ремонт ограничен одним навыком
    assert calls(brain, t["id"]) == 1
    cap = brain.registry.get("get_weather")
    assert [v["status"] for v in cap["versions"]] == ["retired", "active"]          # старая версия сохранена
    # оба монстра продолжают работать с новой версией
    for m in brain.monsters.list():
        _, tm = ask(brain, f"Weather in Lisbon tomorrow {m['id']}", mode="reuse", monster_id=m["id"])
        assert tm["status"] == "completed"
    # откат доступен
    assert brain.registry.rollback("get_weather") == "1.0.0"
    assert brain.registry.get("get_weather")["version"] == "1.0.0"


# ============================================================== TEST D + E
def test_D_interview_method_reused_only_stale_facts_refreshed(env):
    brain, provider = env
    brain.llm.replies += [learn_plan(INTERVIEW_SPEC, "learn interviews"), batch(("interview_prep", INTERVIEW_CODE, INTERVIEW_TESTS))]
    learned = brain.start_task("Learn how to prepare people for job interviews", [], "en"); wait(brain)
    mid = learned["monster_id"]
    slot = lambda v: json.dumps({"value": v})   # noqa: E731
    # 1) первый план: структура и рубрика — код навыка; модель заполняет только 2 слота
    brain.llm.replies += [slot("React, TypeScript, testing"), slot(["Q1", "Q2"])]
    _, t1 = ask(brain, "Prepare me for a frontend developer interview at Acme", mode="reuse", monster_id=mid)
    assert t1["status"] == "completed" and t1["result"]["data"]["mode"] == "partial" and calls(brain, t1["id"]) == 2
    plan1 = t1["result"]["data"]["output"]["plan"]
    assert plan1["rubric"] == {"clarity": 3, "correctness": 4, "depth": 3} and plan1["questions"] == ["Q1", "Q2"]
    # 2) повтор — всё свежее: ноль вызовов модели
    _, t2 = ask(brain, "Prepare me for a frontend developer interview at Acme", mode="reuse", monster_id=mid)
    assert calls(brain, t2["id"]) == 0 and t2["result"]["data"]["output"]["plan"]["requirements"] == "React, TypeScript, testing"
    # 3) прошло время: требования устарели; пользователь просит обновлённые вопросы
    brain.db.execute("UPDATE knowledge SET expires_at='2000-01-01T00:00:00+00:00' WHERE skill='interview_prep.role_requirements'")
    brain.llm.replies += [slot("React 19, Next.js, accessibility"), slot(["Q3", "Q4"])]
    _, t3 = ask(brain, "Prepare me for the same frontend developer interview at Acme again, but use updated questions",
                mode="reuse", monster_id=mid)
    plan3 = t3["result"]["data"]["output"]["plan"]
    assert plan3["requirements"] == "React 19, Next.js, accessibility" and plan3["questions"] == ["Q3", "Q4"]
    assert plan3["rubric"] == plan1["rubric"] and plan3["categories"] == plan1["categories"]        # метод тот же
    assert calls(brain, t3["id"]) == 2 and "CAPABILITY_BUILD_STARTED" not in types(brain, t3["id"])
    reasons = [e["data"]["reason"] for e in brain.bus.history(limit=5000) if e["task_id"] == t3["id"] and e["type"] == "SLOT_FILLING"]
    assert sorted(reasons) == ["expired", "requested"]
    assert brain.registry.get("interview_prep")["version"] == "1.0.0"
    # TEST E: замеренный расход повторов ниже замеренной первой сборки
    assert calls(brain, learned["task_id"]) == 2 and calls(brain, t2["id"]) < calls(brain, learned["task_id"])


# ============================================================== TEST C
FRONT_SPEC = {"name": "html_page_builder", "description": "Builds a static web page from a component spec",
              "purpose_en": "Generate a responsive frontend web page (UI) from a component specification", "purpose_cs": "Webová stránka",
              "inputs": {"title": "str", "sections": "list"}, "outputs": {"file": "str"}, "dependencies": [], "network": False}
FRONT_CODE = '''"""html_page_builder: writes index.html from {title, sections:[{heading, rows}]}."""
import html, os
SKILL = {"intents": ["build frontend"], "keywords": ["frontend", "website", "page", "ui", "web"], "examples": [], "limits": {},
         "freshness_seconds": None, "llm_slots": {}}
def run(inp):
    if not inp.get("title"):
        raise ValueError("title is required")
    out = inp.get("out_dir", "out"); os.makedirs(out, exist_ok=True)
    body = "".join(f"<h2>{html.escape(s['heading'])}</h2><ul>" + "".join(f"<li>{html.escape(str(r))}</li>" for r in s.get("rows", [])) + "</ul>"
                   for s in inp.get("sections", []))
    path = os.path.join(out, "index.html")
    open(path, "w", encoding="utf-8").write(f"<!doctype html><title>{html.escape(inp['title'])}</title><h1>{html.escape(inp['title'])}</h1>{body}")
    return {"file": "index.html"}
def parse_request(text, context):
    return None
def format_result(result, lang):
    return "page " + result["file"]
'''
BACK_SPEC = {"name": "rest_api_builder", "description": "Generates an OpenAPI contract and a server stub",
             "purpose_en": "Create a backend REST API contract and server code", "purpose_cs": "Backend API",
             "inputs": {"endpoints": "list"}, "outputs": {"files": "list"}, "dependencies": [], "network": False}
BACK_CODE = '''"""rest_api_builder: writes openapi.json + server.py for the given endpoints."""
import json, os
SKILL = {"intents": ["build backend api"], "keywords": ["backend", "api", "rest", "server"], "examples": [], "limits": {},
         "freshness_seconds": None, "llm_slots": {}}
def run(inp):
    eps = inp.get("endpoints")
    if not isinstance(eps, list) or not eps:
        raise ValueError("endpoints are required")
    out = inp.get("out_dir", "out"); os.makedirs(out, exist_ok=True)
    spec = {"openapi": "3.0.0", "paths": {e["path"]: {"get": {"summary": e.get("summary", "")}} for e in eps}}
    json.dump(spec, open(os.path.join(out, "openapi.json"), "w"))
    routes = "".join(f"@app.get('{e['path']}')\\ndef h{i}():\\n    return DATA\\n" for i, e in enumerate(eps))
    open(os.path.join(out, "server.py"), "w").write("from fastapi import FastAPI\\napp = FastAPI()\\nDATA = {}\\n" + routes)
    return {"files": ["openapi.json", "server.py"], "paths": list(spec["paths"])}
def parse_request(text, context):
    return None
def format_result(result, lang):
    return ", ".join(result["files"])
'''
GENERIC_TESTS = lambda body: "import unittest, capability, tempfile\nclass T(unittest.TestCase):\n" + "".join(   # noqa: E731
    f"    def test_{i}(self):\n        {body}\n" for i in range(8))


def test_C_existing_monsters_collaborate_without_rebuilding(env):
    brain, provider = env
    weather = teach_weather(brain, provider)["monster_id"]
    brain.llm.replies += [learn_plan(FRONT_SPEC), batch(("html_page_builder", FRONT_CODE, GENERIC_TESTS(
        "self.assertEqual(capability.run({'title': 'x', 'sections': [], 'out_dir': tempfile.mkdtemp()})['file'], 'index.html')")))]
    front = brain.start_task("Learn to build frontend pages", [], "en"); wait(brain)
    brain.llm.replies += [learn_plan(BACK_SPEC), batch(("rest_api_builder", BACK_CODE, GENERIC_TESTS(
        "self.assertIn('server.py', capability.run({'endpoints': [{'path': '/w'}], 'out_dir': tempfile.mkdtemp()})['files'])")))]
    back = brain.start_task("Learn to build backend APIs", [], "en"); wait(brain)
    request = "Create a complete weather website with a frontend page, a backend REST api and live weather forecasts"
    team = brain.monsters.recommend_team(request)
    ids = [m["monster"]["id"] for m in team["members"]]
    assert sorted(ids) == sorted([weather, front["monster_id"], back["monster_id"]])            # по РЕАЛЬНЫМ навыкам
    skills_by = {m["monster"]["id"]: m["skills"] for m in team["members"]}
    assert skills_by[weather] == ["get_weather"] and skills_by[back["monster_id"]] == ["rest_api_builder"]
    names = {m["monster"]["id"]: m["monster"]["name"] for m in team["members"]}
    plan = json.dumps({"intent": "task", "understanding": {"en": "weather site", "cs": "web"}, "expects_files": True, "requirements": [
        {"id": "a", "need": {"en": "data", "cs": "data"}, "decision": "REUSE", "capabilities": ["get_weather"], "assign_to": names[weather]},
        {"id": "b", "need": {"en": "api", "cs": "api"}, "decision": "REUSE", "capabilities": ["rest_api_builder"], "assign_to": names[back["monster_id"]]},
        {"id": "c", "need": {"en": "ui", "cs": "ui"}, "decision": "REUSE", "capabilities": ["html_page_builder"], "assign_to": names[front["monster_id"]]}]})
    workflow = ("=====FILE: workflow.py=====\nimport get_weather, rest_api_builder, html_page_builder, json\n"
                "def main(task):\n    w = get_weather.run({'location': 'Prague', 'forecast_days': 3})\n"
                "    api = rest_api_builder.run({'endpoints': [{'path': '/forecast', 'summary': 'forecast'}], 'out_dir': task['out_dir']})\n"
                "    rows = [f\"{d['date']}: {d['t_min']}..{d['t_max']}C\" for d in w['days']]\n"
                "    html_page_builder.run({'title': 'Weather in ' + w['location'], 'sections': [{'heading': 'Forecast', 'rows': rows}], 'out_dir': task['out_dir']})\n"
                "    return {'summary': {'en': 'Weather site built', 'cs': 'Hotovo'}, 'data': {'paths': api['paths'], 'days': len(rows)},"
                " 'files': ['out/index.html', 'out/openapi.json', 'out/server.py']}\n=====END=====")
    brain.llm.replies += [plan, workflow]
    out = brain.start_task(request, [], "en", mode="team", monster_ids=ids); wait(brain)
    t = brain.task(out["task_id"])
    assert t["status"] == "completed", t["error"]
    ev = [e for e in brain.bus.history(limit=5000) if e["task_id"] == out["task_id"]]
    delegated = {e["data"]["monster"] for e in ev if e["type"] == "DELEGATED"}
    assert delegated == set(names.values())
    assert "CAPABILITY_BUILD_STARTED" not in [e["type"] for e in ev] and calls(brain, out["task_id"]) == 2
    art = config.ARTIFACTS_DIR / str(out["task_id"])
    page = (art / "index.html").read_text(encoding="utf-8")
    assert "Weather in Prague" in page and page.count("<li>") == 3                              # данные WeatherBot в UI FrontendBot
    assert json.loads((art / "openapi.json").read_text())["paths"] == {"/forecast": {"get": {"summary": "forecast"}}}
    compile((art / "server.py").read_text(), "server.py", "exec")                              # интеграционная проверка артефактов
    for i in ids:
        assert brain.monsters.get(i)["tasks_ok"] >= 2
    ready = next(e for e in ev if e["type"] == "MONSTER_READY")
    assert len(ready["data"]["team"]) == 3
