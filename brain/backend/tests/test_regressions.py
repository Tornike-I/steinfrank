"""Регрессии по двум реальным ошибкам из живой базы пользователя.

1) ПОГОДА. Задача #14 «Tel Aviv и Dubai» сохранила воркфлоу как рецепт, а код рецепта ЗАШИЛ города литералами.
   Задачи #15/#16 «Kiev и Prague» выбрали этот рецепт и без вызова модели вернули погоду ТЕЛЬ-АВИВА И ДУБАЯ.
2) СОБЕСЕДОВАНИЯ. Навык prepare_it_interview v1.0.0: общий банк вопросов шёл ПЕРВЫМ, ролевые вопросы — в конец, а
   format_result показывал первые 5 -> «Cybersecurity» и «UX/UI» получили один и тот же текст; «UX/UI» вообще
   разбиралось в "general IT".

Код «навыков» и «рецептов» ниже воспроизводит то, что написала модель пользователя; мозг — сценарий ответов,
интернет — локальный HTTP-сервер (со счётчиком запросов по городам). Песочница, реестр, кэш, БД — настоящие.
"""
import json
import threading
import time

import pytest

from app import config, recipes
from app.brain import Brain
from helpers import ScriptedLLM, batch, files
from test_skills import Provider, ask, calls, learn_plan, types, wait

# ============================================================== обобщённый навык погоды (несколько городов)
MULTI_SPEC = {"name": "get_weather", "description": "Weather forecast for one or several locations",
              "purpose_en": "Retrieve weather forecasts for any locations and dates", "purpose_cs": "Předpověď počasí",
              "inputs": {"location": "str", "start_date": "ISO date", "forecast_days": "int 1-16"},
              "outputs": {"location": "str", "days": "list"}, "dependencies": [], "network": True}

MULTI_CODE = '''"""get_weather: forecast for ONE location; parse_request returns a list for several locations."""
import json, re, urllib.error, urllib.parse, urllib.request
from datetime import date, timedelta
BASE = "http://127.0.0.1:{port}"
MAX_DAYS = 16
SKILL = {"intents": ["weather forecast"], "keywords": ["weather", "forecast", "temperature", "počasí", "погода", "temperatures"],
         "examples": ["What's the weather in Prague tomorrow?", "Weather in Dubai and Tel Aviv", "Forecast for Berlin for 10 days"],
         "limits": {"forecast_days": "1-16"}, "freshness_seconds": 1800, "llm_slots": {}}
NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

def _get(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "frankenstein"}), timeout=10) as r:
            return json.load(r)
    except (urllib.error.URLError, TimeoutError) as e:
        raise ConnectionError(f"provider unavailable: {e}")

def run(inp):
    loc, days = inp.get("location"), int(inp.get("forecast_days", 1))
    if not isinstance(loc, str) or not loc.strip():
        raise ValueError("location is required")
    if days < 1 or days > MAX_DAYS:
        raise ValueError(f"forecasts are available for 1-{MAX_DAYS} days only, not {days}")
    geo = _get("/geo", {"name": loc})
    if not geo.get("results"):
        raise ValueError(f"unknown location: {loc}")
    g = geo["results"][0]
    d = _get("/forecast", {"lat": g["latitude"], "lon": g["longitude"], "days": days, "start": inp.get("start_date", "")})["daily"]
    return {"location": g["name"], "days": [{"date": d["time"][i], "t_max": d["temperature_2m_max"][i], "t_min": d["temperature_2m_min"][i]}
                                            for i in range(len(d["time"]))]}

def parse_request(text, context):
    t = text.lower()
    if not any(k in t for k in SKILL["keywords"]):
        return None
    cities = []
    m = re.search(r"\\b(?:in|for|v|ve|в)\\s+(.+)", text)
    if m:
        seg = re.split(r"\\b(?:tomorrow|today|for|this|next|zítra|завтра)\\b|[?.!]", m.group(1))[0]
        parts = re.split(r",\\s*(?:and\\s+)?|\\s+and\\s+|\\s+и\\s+|\\s+a\\s+", seg)
        cities = [p.strip() for p in parts if p.strip() and p.strip()[0].isupper()]
    if not cities and context.get("previous"):
        prev = context["previous"] if isinstance(context["previous"], list) else [context["previous"]]
        cities = [p["location"] for p in prev]
    if not cities:
        return None
    today = date.fromisoformat(context["today"])
    start, days = today, 1
    n = re.search(r"(\\d+|" + "|".join(NUM) + r")\\s*(?:days|day)", t)
    if n:
        days = int(n.group(1)) if n.group(1).isdigit() else NUM[n.group(1)]
    elif "tomorrow" in t or "завтра" in t:
        start = today + timedelta(days=1)
    items = [{"location": c, "start_date": start.isoformat(), "forecast_days": days} for c in cities]
    return items if len(items) > 1 else items[0]

def format_result(result, lang):
    rows = "".join(f"| {d['date']} | {d['t_max']} | {d['t_min']} |\\n" for d in result["days"])
    return f"### {result['location']}\\n\\n| Date | Max °C | Min °C |\\n|---|---|---|\\n{rows}"
'''

MULTI_TESTS = '''import unittest
import capability
CTX = {"today": "2026-10-09", "lang": "en"}
class T(unittest.TestCase):
    def test_one(self): self.assertEqual(capability.parse_request("Weather in Prague tomorrow", CTX)["location"], "Prague")
    def test_two(self): self.assertEqual([p["location"] for p in capability.parse_request("Weather in Dubai and Tel Aviv", CTX)], ["Dubai", "Tel Aviv"])
    def test_four(self):
        self.assertEqual([p["location"] for p in capability.parse_request("Weather in London, Paris, Berlin, and Rome", CTX)], ["London", "Paris", "Berlin", "Rome"])
    def test_ten_words(self): self.assertEqual(capability.parse_request("Weather in Prague for ten days", CTX)["forecast_days"], 10)
    def test_russian(self): self.assertEqual(len(capability.parse_request("Погода в Киеве и Праге", CTX)), 2)
    def test_previous(self):
        p = capability.parse_request("What about the weather tomorrow in those same cities?", {**CTX, "previous": [{"location": "Kyiv"}, {"location": "Prague"}]})
        self.assertEqual([x["location"] for x in p], ["Kyiv", "Prague"])
    def test_no_previous(self): self.assertIsNone(capability.parse_request("weather in those same cities", CTX))
    def test_other(self): self.assertIsNone(capability.parse_request("Translate hello", CTX))
    def test_limit(self):
        with self.assertRaises(ValueError): capability.run({"location": "Oslo", "forecast_days": 40})
    def test_format_differs(self):
        a = capability.format_result({"location": "Kyiv", "days": [{"date": "d", "t_max": 1, "t_min": 0}]}, "en")
        b = capability.format_result({"location": "Dubai", "days": [{"date": "d", "t_max": 1, "t_min": 0}]}, "en")
        self.assertNotEqual(a, b)
'''


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "r.db")
    monkeypatch.setattr(config, "ALLOW_NETWORK_CAPABILITIES", True)
    provider = Provider()
    brain = Brain(llm=ScriptedLLM([]))
    yield brain, provider
    provider.srv.shutdown()


def teach_multi(brain, provider):
    brain.llm.replies += [learn_plan(MULTI_SPEC, "learn weather"), batch(("get_weather", MULTI_CODE.replace("{port}", str(provider.port)), MULTI_TESTS))]
    out = brain.start_task("Learn how to check the weather", [], "en"); wait(brain)
    assert brain.task(out["task_id"])["status"] == "completed", brain.task(out["task_id"])["error"]
    return out["monster_id"]


def locations(task):
    items = task["result"]["data"]["output"]["items"]
    return [it["params"]["location"] for it in items]


# ============================================================== TEST 1 + 9: новые города, тот же навык, 0 вызовов модели
def test_1_weather_switches_cities_and_reuses_the_skill(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    _, t1 = ask(brain, "What's the weather in Dubai and Tel Aviv?", mode="reuse", monster_id=mid)
    assert locations(t1) == ["Dubai", "Tel Aviv"]
    provider.queries.clear()
    _, t2 = ask(brain, "What's the weather in Kyiv and Prague?", mode="reuse", monster_id=mid)
    assert t2["status"] == "completed" and locations(t2) == ["Kyiv", "Prague"]
    answer = t2["result"]["summary"]["en"]
    assert "### Kyiv" in answer and "### Prague" in answer and "Dubai" not in answer and "Tel Aviv" not in answer
    assert sorted(provider.queries) == ["Kyiv", "Prague"]                      # провайдер получил НОВЫЕ города
    _, t3 = ask(brain, "Weather in Warsaw, London, and Berlin", mode="reuse", monster_id=mid)
    assert locations(t3) == ["Warsaw", "London", "Berlin"]
    for t in (t1, t2, t3):                                                      # навык переиспользован, не пересобран
        assert calls(brain, t["id"]) == 0 and "CAPABILITY_BUILD_STARTED" not in types(brain, t["id"])
        assert t["result"]["data"]["skill"] == "get_weather"
    assert [c["name"] for c in brain.registry.list()] == ["get_weather"]


# ============================================================== TEST 2: четыре города — каждый вызов со своим городом
def test_2_four_locations_each_call_gets_its_own_location(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    provider.queries.clear()
    _, t = ask(brain, "Weather in London, Paris, Berlin, and Rome", mode="reuse", monster_id=mid)
    items = t["result"]["data"]["output"]["items"]
    assert [it["params"]["location"] for it in items] == ["London", "Paris", "Berlin", "Rome"]
    assert all(it["output"]["location"] == it["params"]["location"] for it in items)   # результат привязан к своему городу
    assert sorted(provider.queries) == ["Berlin", "London", "Paris", "Rome"]
    fan = next(e for e in brain.bus.history(limit=5000) if e["task_id"] == t["id"] and e["type"] == "SKILL_FANOUT")
    assert fan["data"]["count"] == 4


# ============================================================== TEST 3: тот же навык, другой горизонт
def test_3_same_skill_different_forecast_parameters(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    _, a = ask(brain, "Weather in Prague tomorrow", mode="reuse", monster_id=mid)
    _, b = ask(brain, "Weather in Prague for ten days", mode="reuse", monster_id=mid)
    pa, pb = a["result"]["data"]["params"], b["result"]["data"]["params"]
    assert pa["forecast_days"] == 1 and pb["forecast_days"] == 10 and pa["start_date"] != pb["start_date"]
    assert len(b["result"]["data"]["output"]["days"]) == 10 and b["result"]["data"]["skill"] == a["result"]["data"]["skill"]
    _, c = ask(brain, "Weather in Prague for 40 days", mode="reuse", monster_id=mid)   # горизонт провайдера — честный отказ
    assert "1-16 days only" in c["result"]["summary"]["en"] and calls(brain, c["id"]) == 0


# ============================================================== TEST 4: кэш с учётом параметров
def test_4_cache_is_parameter_aware_and_per_location(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    _, p = ask(brain, "Weather in Prague", mode="reuse", monster_id=mid)
    _, b = ask(brain, "Weather in Berlin", mode="reuse", monster_id=mid)
    assert "Prague" not in b["result"]["summary"]["en"] and "### Berlin" in b["result"]["summary"]["en"]
    assert b["result"]["data"]["mode"] == "deterministic"                       # Берлин не взят из кэша Праги
    ask(brain, "Weather in Kyiv and Prague", mode="reuse", monster_id=mid)
    provider.queries.clear()
    _, t = ask(brain, "Weather in Prague and Vienna", mode="reuse", monster_id=mid)
    modes = {it["params"]["location"]: it["mode"] for it in t["result"]["data"]["output"]["items"]}
    assert modes == {"Prague": "cache", "Vienna": "deterministic"} and provider.queries == ["Vienna"]   # Прага — из кэша
    keys = [r["key"] for r in brain.db.query("SELECT key FROM knowledge WHERE skill='get_weather'")]
    assert len(keys) == len(set(keys)) == 4                                     # Prague, Berlin, Kyiv, Vienna — разные ключи


def test_partial_failure_is_reported_per_location(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    _, t = ask(brain, "Weather in Atlantis and Oslo", mode="reuse", monster_id=mid)
    items = {it["params"]["location"]: it for it in t["result"]["data"]["output"]["items"]}
    assert items["Atlantis"]["limitation"] == "unknown location: Atlantis" and items["Oslo"]["output"]["location"] == "Oslo"
    assert "unknown location: Atlantis" in t["result"]["summary"]["en"] and "### Oslo" in t["result"]["summary"]["en"]


def test_explicit_reference_inherits_previous_cities_only_when_asked(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    ask(brain, "Weather in Kyiv and Prague", mode="reuse", monster_id=mid)
    _, t = ask(brain, "What about the weather tomorrow in those same cities?", mode="reuse", monster_id=mid)
    assert locations(t) == ["Kyiv", "Prague"] and "CONTEXT_REFERENCE" in types(brain, t["id"])
    _, n = ask(brain, "Weather in Rome tomorrow", mode="reuse", monster_id=mid)   # без ссылки — прошлые города не наследуются
    assert n["result"]["data"]["params"]["location"] == "Rome" and "CONTEXT_REFERENCE" not in types(brain, n["id"])


# ============================================================== TEST 7: изоляция запросов
def test_7_parallel_items_never_mix_and_tasks_are_isolated(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    provider.delay = {"Slowtown": 1.0}                                          # первый город отвечает ПОСЛЕДНИМ
    _, t = ask(brain, "Weather in Slowtown, Quickville and Fastburg", mode="reuse", monster_id=mid)
    items = t["result"]["data"]["output"]["items"]
    assert [(it["params"]["location"], it["output"]["location"]) for it in items] == \
        [("Slowtown", "Slowtown"), ("Quickville", "Quickville"), ("Fastburg", "Fastburg")]
    # вторая задача, пока идёт первая, не начинается (и не может перезаписать её состояние)
    provider.delay = {"Slowtown": 1.5}
    first = brain.start_task("Weather in Slowtown", [], "en", mode="reuse", monster_id=mid)
    with pytest.raises(RuntimeError, match="busy"):
        brain.start_task("Weather in Oslo", [], "en", mode="reuse", monster_id=mid)
    wait(brain)
    assert brain.task(first["task_id"])["result"]["data"]["params"]["location"] == "Slowtown"
    # события каждого элемента помечены своей задачей и своими параметрами
    ex = [e for e in brain.bus.history(limit=5000) if e["type"] == "SKILL_EXECUTED" and e["task_id"] == t["id"]]
    assert sorted(e["data"]["params"]["location"] for e in ex) == ["Fastburg", "Quickville", "Slowtown"]


# ============================================================== TEST 10: перезапуск
def test_10_restart_keeps_skill_and_takes_new_arguments(env):
    brain, provider = env
    mid = teach_multi(brain, provider)
    ask(brain, "Weather in Dubai and Tel Aviv", mode="reuse", monster_id=mid)
    restarted = Brain(llm=ScriptedLLM([]))
    _, t = ask(restarted, "Weather in Oslo and Rome", mode="reuse", monster_id=mid)
    assert locations(t) == ["Oslo", "Rome"] and calls(restarted, t["id"]) == 0
    assert "Dubai" not in t["result"]["summary"]["en"]


# ============================================================== РЕЦЕПТЫ (настоящая причина ошибки с погодой)
ORGAN_SPEC = {"name": "city_weather", "description": "Weather for one city (offline fake)", "purpose_en": "Weather for a city",
              "purpose_cs": "Počasí", "inputs": {"location": "str"}, "outputs": {"location": "str", "t_max": "int"},
              "dependencies": [], "network": False}
ORGAN_CODE = '''"""city_weather: deterministic fake weather. inputs: location. outputs: location, t_max."""
def run(inp):
    loc = inp.get("location")
    if not isinstance(loc, str) or not loc.strip():
        raise ValueError("location is required")
    return {"location": loc, "t_max": 10 + len(loc)}
'''
ORGAN_TESTS = "import unittest, capability\nclass T(unittest.TestCase):\n" + "".join(
    f"    def test_{i}(self):\n        self.assertEqual(capability.run({{'location': 'X{i}'}})['location'], 'X{i}')\n" for i in range(8))
# что написала модель пользователя: города вписаны прямо в код (задача #14)
HARDCODED_WF = ('=====FILE: workflow.py=====\nimport city_weather\ndef main(task):\n'
                '    rows = [city_weather.run({"location": "Dubai"}), city_weather.run({"location": "Tel Aviv"})]\n'
                '    return {"summary": {"en": ", ".join(f"{r[\'location\']}: {r[\'t_max\']}C" for r in rows), "cs": "ok"},'
                ' "data": {"rows": rows}, "files": []}\n=====END=====')
PARAM_WF = ('=====FILE: workflow.py=====\nimport city_weather\ndef main(task):\n'
            '    rows = [city_weather.run({"location": c}) for c in task["params"]["locations"]]\n'
            '    return {"summary": {"en": "### Weather\\n" + ", ".join(f"{r[\'location\']}: {r[\'t_max\']}C" for r in rows), "cs": "ok"},'
            ' "data": {"rows": rows}, "files": []}\n=====END=====')


def plan(text, decision, locs, recipe=None):
    req = {"id": "r1", "need": {"en": "weather", "cs": "počasí"}, "decision": decision}
    req.update({"spec": ORGAN_SPEC} if decision == "CREATE" else {"capabilities": ["city_weather"]})
    return json.dumps({"intent": "task", "understanding": {"en": text, "cs": text}, "expects_files": False, "recipe": recipe,
                       "params": {"locations": locs}, "requirements": [req]})


def test_recipe_with_hardcoded_cities_is_never_reused_for_other_cities(env):
    brain, _ = env
    brain.llm.replies += [plan("weather", "CREATE", ["Dubai", "Tel Aviv"]), batch(("city_weather", ORGAN_CODE, ORGAN_TESTS)), HARDCODED_WF]
    _, t1 = ask(brain, "Tell me the weather in Tel Aviv and Dubai")
    rid = brain.db.one("SELECT id FROM recipes")["id"]
    assert json.loads(brain.db.one("SELECT bound FROM recipes")["bound"]) == ["Dubai", "Tel Aviv"]   # зашитые значения найдены
    assert brain.registry.recipes_for_llm()[0]["hardcoded"] is True
    # было: рецепт запускался и возвращал Dubai/Tel Aviv. Стало: рецепт отклонён, воркфлоу читает task["params"]
    brain.llm.replies += [plan("weather", "REUSE", ["Kyiv", "Prague"], recipe=rid), PARAM_WF]
    _, t2 = ask(brain, "Give me the weather in Kiev and Prague")
    ev = types(brain, t2["id"])
    assert "RECIPE_REJECTED" in ev and "WORKFLOW_RECIPE_REUSED" not in ev
    assert t2["result"]["data"]["rows"] == [{"location": "Kyiv", "t_max": 14}, {"location": "Prague", "t_max": 16}]
    assert "Dubai" not in t2["result"]["summary"]["en"]
    assert "Dubai" not in brain.llm.prompts[-1] and '"locations":["Kyiv","Prague"]' in brain.llm.prompts[-1]
    # новый рецепт — параметризованный: третий запрос с ДРУГИМИ городами идёт без модели для воркфлоу
    assert json.loads(brain.db.one("SELECT bound FROM recipes WHERE id=?", (rid,))["bound"]) == []
    brain.llm.replies += [plan("weather", "REUSE", ["Warsaw", "London", "Berlin"], recipe=rid)]
    _, t3 = ask(brain, "Weather in Warsaw, London and Berlin")
    assert "WORKFLOW_RECIPE_REUSED" in types(brain, t3["id"]) and calls(brain, t3["id"]) == 1     # только планировщик
    assert [r["location"] for r in t3["result"]["data"]["rows"]] == ["Warsaw", "London", "Berlin"]


def test_legacy_recipe_from_live_database_is_rejected_for_new_cities():
    """Код рецепта №2 из живой базы пользователя (город зашит)."""
    live = {"params": None, "code": 'import get_weather_by_location\ndef main(task):\n'
                                    '    w = get_weather_by_location.run({"location": "Kiev", "days": 7})\n    return {}\n'}
    assert "Kiev" in recipes.guard(live, {}, "Give me what the weather in Kyiv and in Prague")
    assert recipes.guard(live, {}, "what weather in Kiev") is None
    leaked = recipes.contamination({"params": json.dumps({"locations": ["Dubai", "Tel Aviv"]})}, {"locations": ["Kyiv"]},
                                   {"summary": "Current weather: Tel Aviv 22.5C, Dubai 30.4C"})
    assert sorted(leaked) == ["Dubai", "Tel Aviv"]


# ============================================================== СОБЕСЕДОВАНИЯ
INTERVIEW_SPEC = {"name": "prepare_it_interview", "description": "IT interview preparation for any role",
                  "purpose_en": "Prepare users for IT job interviews", "purpose_cs": "Příprava na IT pohovor",
                  "inputs": {"role": "str", "level": "junior|mid|senior"}, "outputs": {"questions": "list"},
                  "dependencies": [], "network": False}
# точная модель ошибки пользователя: общий банк первым, ролевые вопросы после обрезки, UX/UI -> "general IT"
BAD_CODE = '''"""prepare_it_interview v1 (generic bank first)."""
SKILL = {"intents": ["interview preparation"], "keywords": ["interview", "pohovor"],
         "examples": ["Prepare me for a software developer interview"], "limits": {}, "freshness_seconds": None, "llm_slots": {}}
ROLES = {"security engineer": ["security", "cyber"], "frontend developer": ["frontend"]}
BASE = ["Explain the difference between a process and a thread.", "What is Git for?", "Describe the OSI model.",
        "How do you troubleshoot a performance issue?", "Why does security matter in IT?"]
ROLE_Q = {"security engineer": ["Explain SQL injection."], "frontend developer": ["What is the virtual DOM?"]}
def parse_request(text, context):
    t = text.lower()
    if "interview" not in t:
        return None
    role = "general IT"
    for r, pats in ROLES.items():
        if any(p in t for p in pats):
            role = r
            break
    return {"role": role}
def run(inp):
    if not inp.get("role"):
        raise ValueError("role is required")
    return {"role": inp["role"], "questions": BASE + ROLE_Q.get(inp["role"], [])}
def format_result(result, lang):
    return "# Interview Preparation\\n\\n" + "\\n".join(f"{i}. {q}" for i, q in enumerate(result["questions"][:5], 1))
'''
BAD_TESTS = "import unittest, capability\nclass T(unittest.TestCase):\n" + "".join(
    f"    def test_{i}(self):\n        self.assertEqual(len(capability.run({{'role': 'r{i}'}})['questions']), 5)\n" for i in range(8))

GOOD_CODE = '''"""prepare_it_interview v2: reusable METHOD + role catalog + llm slot for roles outside the catalog."""
import re
SKILL = {"intents": ["interview preparation"], "keywords": ["interview", "pohovor", "собеседован"],
         "examples": ["Prepare me for a cybersecurity interview", "Prepare me for a UX/UI designer interview",
                      "Prepare me for a senior frontend interview"],
         "limits": {"level": "junior, mid, senior"}, "freshness_seconds": None,
         "llm_slots": {"role_material": {"prompt": "Competencies and 5 interview questions for a {level} {role}", "freshness_seconds": 1209600}}}
CATALOG = {
    "cybersecurity engineer": {"aliases": ["cybersecurity", "cyber security", "security engineer", "infosec"],
        "competencies": ["Networking and security fundamentals", "Authentication and authorization", "OWASP Top 10",
                         "Threat modeling", "Incident response", "Cryptography fundamentals"],
        "questions": ["What is the difference between authentication and authorization?",
                      "How would you investigate suspicious login activity?", "Explain SQL injection and appropriate defenses."],
        "exercises": ["Triage a phishing alert end to end", "Threat-model a login page"]},
    "ux/ui designer": {"aliases": ["ux/ui", "ux/ ui", "ux / ui", "ui/ux", "ux designer", "ui designer"],
        "competencies": ["User research", "Usability testing", "Wireframing and prototyping", "Design systems",
                         "Accessibility", "Figma workflows"],
        "questions": ["How do you approach designing a user flow?", "What is the difference between UX and UI?",
                      "How would you explain your design decisions to stakeholders?"],
        "exercises": ["Redesign a checkout flow in Figma", "Run a 5-user usability test"]},
    "frontend developer": {"aliases": ["frontend", "front-end", "front end"],
        "competencies": ["HTML/CSS layout", "JavaScript and TypeScript", "React state management", "Web performance"],
        "questions": ["How does the browser render a page?", "When would you memoize a React component?"],
        "exercises": ["Build an accessible dropdown component"]},
}
LEVELS = {"junior": {"focus": "fundamentals and learning ability", "extra": "Walk me through how you would debug a bug you have never seen."},
          "mid": {"focus": "independent delivery", "extra": "Tell me about a feature you owned end to end."},
          "senior": {"focus": "architecture, trade-offs and mentoring", "extra": "Design the architecture of a large application and justify the trade-offs."}}

def parse_request(text, context):
    t = text.lower()
    if not any(k in t for k in SKILL["keywords"]):
        return None
    level = next((lv for lv in LEVELS if lv in t), "mid")
    for role, info in CATALOG.items():
        if any(a in t for a in info["aliases"]):
            return {"role": role, "level": level, "_slots": []}
    m = re.search(r"(?:for|to)\\s+(?:an?\\s+|the\\s+)?(?:junior\\s+|mid\\s+|senior\\s+)?(.+?)\\s+interview", t)
    if not m:
        return None
    return {"role": m.group(1).strip(), "level": level, "_slots": ["role_material"]}

def run(inp):
    role, level = inp.get("role"), inp.get("level", "mid")
    if not role:
        raise ValueError("role is required")
    if level not in LEVELS:
        raise ValueError("level must be junior, mid or senior")
    info = CATALOG.get(role)
    if info is None:
        mat = (inp.get("slots") or {}).get("role_material")
        if not mat:
            raise ValueError(f"no material for role {role}")
        info = {"competencies": [str(mat)], "questions": [], "exercises": []}
    return {"role": role, "level": level, "competencies": info["competencies"],
            "questions": info["questions"] + [LEVELS[level]["extra"]], "exercises": info["exercises"], "focus": LEVELS[level]["focus"]}

def format_result(r, lang):
    q = "\\n".join(f"{i}. {x}" for i, x in enumerate(r["questions"], 1))
    c = "\\n".join(f"- {x}" for x in r["competencies"])
    e = "\\n".join(f"- {x}" for x in r["exercises"]) or "- (ask me for exercises)"
    return (f"### {r['role'].title()} ({r['level']}) interview preparation\\n\\n**1. Core competencies**\\n{c}\\n\\n"
            f"**2. Common interview questions**\\n{q}\\n\\n**3. Practical exercises**\\n{e}\\n\\n**Level focus:** {r['focus']}")
'''
GOOD_TESTS = '''import unittest, capability
CTX = {"today": "2026-10-09", "lang": "en"}
class T(unittest.TestCase):
    def test_cyber(self): self.assertEqual(capability.parse_request("Prepare me to Cybersecurity interview", CTX)["role"], "cybersecurity engineer")
    def test_ux(self): self.assertEqual(capability.parse_request("Prepare me to UX/ UI interview", CTX)["role"], "ux/ui designer")
    def test_unknown(self): self.assertEqual(capability.parse_request("Prepare me for a blockchain auditor interview", CTX)["_slots"], ["role_material"])
    def test_level(self): self.assertEqual(capability.parse_request("Prepare me for a senior frontend interview", CTX)["level"], "senior")
    def test_differ(self):
        a = capability.run({"role": "cybersecurity engineer"}); b = capability.run({"role": "ux/ui designer"})
        self.assertNotEqual(a["questions"], b["questions"])
    def test_ux_no_security(self): self.assertNotIn("OWASP", str(capability.run({"role": "ux/ui designer"})))
    def test_slot(self): self.assertIn("ledger", str(capability.run({"role": "auditor", "slots": {"role_material": "ledger"}})))
    def test_missing(self):
        with self.assertRaises(ValueError): capability.run({})
    def test_format(self): self.assertIn("### Ux/Ui Designer", capability.format_result(capability.run({"role": "ux/ui designer"}), "en"))
'''


def teach_interviews(brain, code, tests):
    brain.llm.replies += [learn_plan(INTERVIEW_SPEC, "learn interviews"), batch(("prepare_it_interview", code, tests))]
    out = brain.start_task("Learn my monster to prepare me for interview in any IT work", [], "en"); wait(brain)
    assert brain.task(out["task_id"])["status"] == "completed", brain.task(out["task_id"])["error"]
    return out["monster_id"]


def test_5_interview_skill_that_memorised_one_answer_is_detected_and_repaired(env):
    brain, _ = env
    mid = teach_interviews(brain, BAD_CODE, BAD_TESTS)
    _, cyber = ask(brain, "Prepare me to Cybersecurity interview", mode="reuse", monster_id=mid)
    assert "process and a thread" in cyber["result"]["summary"]["en"]          # так было у пользователя (задача #19)
    # БАРЬЕР: дефект найден, но переписывать код без подтверждения нельзя — мгновенный ответ, 0 вызовов, ничего не построено
    _, wait_ux = ask(brain, "Prepare me to UX/ UI interview", mode="reuse", monster_id=mid)
    assert wait_ux["status"] == "completed" and wait_ux["result"]["data"]["decision"]["action"] == "repair_skill"
    assert "SKILL_NOT_PARAMETERIZED" in types(brain, wait_ux["id"]) and calls(brain, wait_ux["id"]) == 0
    assert "process and a thread" not in wait_ux["result"]["summary"]["en"]            # чужой (общий) ответ не выдаётся
    brain.llm.replies += [files(GOOD_CODE, GOOD_TESTS, name="prepare_it_interview")]   # ОДНА мутация после подтверждения
    _, ux = ask(brain, "Prepare me to UX/ UI interview", mode="reuse", monster_id=mid, allow_build=True)
    ev = types(brain, ux["id"])
    assert "SKILL_NOT_PARAMETERIZED" in ev and "CAPABILITY_MUTATED" in ev and "CAPABILITY_BUILD_STARTED" not in ev
    text = ux["result"]["summary"]["en"]
    assert ux["status"] == "completed" and "### Ux/Ui Designer" in text and "Figma" in text and "usability" in text.lower()
    assert "OWASP" not in text and "process and a thread" not in text
    assert ux["result"]["data"]["version"] == "1.1.0" and calls(brain, ux["id"]) == 1
    # тот же навык (не новый монстр и не новый орган) теперь даёт разное содержание для разных профессий
    _, cyber2 = ask(brain, "Prepare me for a cybersecurity interview", mode="reuse", monster_id=mid)
    t2 = cyber2["result"]["summary"]["en"]
    assert "OWASP" in t2 and "authentication and authorization" in t2 and "Figma" not in t2 and calls(brain, cyber2["id"]) == 0
    assert [c["name"] for c in brain.registry.list()] == ["prepare_it_interview"]


def test_repair_is_rejected_if_the_new_version_is_still_generic(env):
    brain, _ = env
    mid = teach_interviews(brain, BAD_CODE, BAD_TESTS)
    ask(brain, "Prepare me to Cybersecurity interview", mode="reuse", monster_id=mid)
    brain.llm.replies += [files(BAD_CODE.replace("v1", "v1b"), BAD_TESTS, name="prepare_it_interview")]
    _, ux = ask(brain, "Prepare me to UX/ UI interview", mode="reuse", monster_id=mid, allow_build=True)
    assert ux["status"] == "failed" and "not generalized" in ux["error"]           # честно, без выдачи чужого ответа
    assert brain.registry.get("prepare_it_interview")["version"] == "1.0.0"


def test_learning_audit_catches_a_generic_skill_before_it_is_used(env):
    brain, _ = env
    generic = BAD_CODE.replace('"examples": ["Prepare me for a software developer interview"]',
                               '"examples": ["Prepare me for a cybersecurity interview", "Prepare me for a UX designer interview"]')
    brain.llm.replies += [learn_plan(INTERVIEW_SPEC, "learn interviews"), batch(("prepare_it_interview", generic, BAD_TESTS)),
                          files(GOOD_CODE, GOOD_TESTS, name="prepare_it_interview")]
    out = brain.start_task("Learn to prepare people for IT interviews", [], "en"); wait(brain)
    audit = [e for e in brain.bus.history(limit=5000) if e["task_id"] == out["task_id"] and e["type"] == "SKILL_AUDITED"]
    assert audit and audit[0]["data"]["ok"] is False
    assert brain.registry.get("prepare_it_interview")["version"] == "1.1.0"


def test_6_seniority_and_unknown_roles(env):
    brain, _ = env
    mid = teach_interviews(brain, GOOD_CODE, GOOD_TESTS)
    _, jr = ask(brain, "Prepare me for a junior frontend interview", mode="reuse", monster_id=mid)
    _, sr = ask(brain, "Prepare me for a senior frontend interview", mode="reuse", monster_id=mid)
    assert "debug a bug" in jr["result"]["summary"]["en"] and "architecture" in sr["result"]["summary"]["en"]
    assert jr["result"]["summary"]["en"] != sr["result"]["summary"]["en"] and calls(brain, sr["id"]) == 0
    # профессии нет в каталоге: модель заполняет ТОЛЬКО слот, результат запоминается
    brain.llm.replies += [json.dumps({"value": "Smart-contract auditing, Solidity security, gas analysis"})]
    _, bc = ask(brain, "Prepare me for a blockchain auditor interview", mode="reuse", monster_id=mid)
    assert "Smart-contract" in bc["result"]["summary"]["en"] and calls(brain, bc["id"]) == 1
    _, bc2 = ask(brain, "Prepare me for a blockchain auditor interview", mode="reuse", monster_id=mid)
    assert calls(brain, bc2["id"]) == 0
