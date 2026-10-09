"""Новая архитектура: органы (метод) отдельно от пакетов знаний (данные); барьер создания органов; шаблоны.

Сценарии из задания (TEST 1-10). Мозг — сценарий ответов (считаем КАЖДЫЙ вызов модели), погода — локальный сервер
в формате Open-Meteo. Песочница, реестр, пакеты знаний, кэш, учёт расхода и БД — настоящие.
"""
import http.server
import json
import threading
import time
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app import config
from app.brain import Brain
from app.economy import report
from helpers import ScriptedLLM, batch
from test_skills import ask, calls, types, wait


class OpenMeteoFake:
    """Локальный сервер в формате Open-Meteo: /geo и /forecast, журнал запросов по городам."""

    def __init__(self):
        self.queries: list[str] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlparse(self.path); q = {k: v[0] for k, v in parse_qs(u.query).items()}
                if u.path == "/geo":
                    outer.queries.append(q["name"])
                    body = {} if q["name"] == "Atlantis" else {"results": [{"name": q["name"], "country": "Testland",
                                                                            "latitude": 50.0 + len(q["name"]), "longitude": 14.0}]}
                else:
                    s, e = date.fromisoformat(q["start_date"]), date.fromisoformat(q["end_date"])
                    n = (e - s).days + 1
                    t = float(q["latitude"])                     # «погода» зависит от города
                    body = {"daily": {"time": [(s + timedelta(days=i)).isoformat() for i in range(n)], "weather_code": [61] * n,
                                      "temperature_2m_max": [round(t / 3, 1)] * n, "temperature_2m_min": [round(t / 5, 1)] * n,
                                      "precipitation_sum": [0.4] * n, "wind_speed_10m_max": [12.0] * n}}
                data = json.dumps(body).encode()
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers(); self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()


INTERVIEW_SPEC = {"name": "prepare_interview", "description": "Interview preparation for any profession",
                  "purpose_en": "Prepare users for job interviews in any IT profession", "purpose_cs": "Příprava na pohovor",
                  "inputs": {"role": "str"}, "outputs": {"questions": "list"}, "dependencies": [], "network": False}
WEATHER_SPEC = {"name": "get_weather", "description": "Weather forecast", "purpose_en": "Weather forecasts for any city",
                "purpose_cs": "Počasí", "inputs": {"location": "str"}, "outputs": {"days": "list"}, "dependencies": [], "network": True}


def learn(spec, what):
    return json.dumps({"intent": "learn", "understanding": {"en": what, "cs": what}, "params": {}, "requirements": [
        {"id": "r1", "need": {"en": what, "cs": what}, "decision": "CREATE", "spec": spec}]})


def generated_pack(title="Blockchain Auditor", filler=False, levels=(("m", 12), ("j", 3))):
    """КОМПАКТНЫЙ пакет, который «вернула» модель: только нужные уровни, без подсказок (их не просили)."""
    qs = []
    for code, n in levels:
        for i in range(n):
            q = ("Explain the difference between a process and a thread." if filler and len(qs) < 6 else
                 f"How would you audit smart-contract scenario {code}{i} for reentrancy and access-control flaws?")
            qs.append([len(qs) % 5, code, q])
    return json.dumps({"title": title, "domain": "blockchain", "aliases": ["blockchain auditor", "smart contract auditor"],
                       "competencies": ["Solidity security", "Reentrancy", "Access control", "Gas analysis", "Audit reporting", "DeFi"],
                       "topics": ["Reentrancy", "Access control", "Gas", "Upgradeability", "Reporting"], "questions": qs,
                       "exercises": ["Audit a staking contract"], "rubric": ["finds real bugs"], "recommendations": ["read past audit reports"]})


def senior_extension(n=17):
    return json.dumps({"questions": [[i % 5, "s", f"Design an audit process for a cross-chain bridge, case {i}, and defend the trade-offs."]
                                     for i in range(n)]})


def hints_for_prompt(prompt):
    """Ответ на дозаказ подсказок: ровно столько, сколько вопросов прислали."""
    qs = json.loads(prompt.split("QUESTIONS: ", 1)[1].split("\n", 1)[0])
    return json.dumps({"hints": [f"threat model, invariants, test {i}" for i in range(len(qs))]})


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "a.db")
    monkeypatch.setattr(config, "ALLOW_NETWORK_CAPABILITIES", True)
    monkeypatch.setattr(config, "USE_TEMPLATES", True)
    meteo = OpenMeteoFake()
    monkeypatch.setenv("FRANK_WEATHER_GEO_URL", f"http://127.0.0.1:{meteo.port}/geo")
    monkeypatch.setenv("FRANK_WEATHER_FORECAST_URL", f"http://127.0.0.1:{meteo.port}/forecast")
    brain = Brain(llm=ScriptedLLM([]))
    yield brain, meteo
    meteo.srv.shutdown()


def teach(brain, spec, what):
    brain.llm.replies.append(learn(spec, what))
    out = brain.start_task(what, [], "en"); wait(brain)
    t = brain.task(out["task_id"])
    assert t["status"] == "completed", t["error"]
    return out


def metrics(brain, tid):
    return json.loads(brain.db.one("SELECT metrics FROM tasks WHERE id=?", (tid,))["metrics"])


def text(t):
    return t["result"]["summary"]["en"]


# ============================================================== TEST 1, 2, 10: смена профессии — тот же орган, 0 сборок
def test_1_profession_switching_reuses_one_organ_with_domain_knowledge(env):
    brain, _ = env
    learned = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")
    ev = types(brain, learned["task_id"])
    assert "TEMPLATE_INSTALL_STARTED" in ev and "CAPABILITY_BUILD_STARTED" not in ev
    assert calls(brain, learned["task_id"]) == 1                           # только планировщик: код из проверенного шаблона
    mid = learned["monster_id"]
    _, cyber = ask(brain, "Prepare me for a cybersecurity interview", mode="reuse", monster_id=mid)
    _, ux = ask(brain, "Prepare me for a UX/UI Designer interview", mode="reuse", monster_id=mid)
    _, voip = ask(brain, "Prepare me to SENIOR VOIP interview", mode="reuse", monster_id=mid)
    for t in (cyber, ux, voip):
        assert t["status"] == "completed"
        assert calls(brain, t["id"]) == 0 and "CAPABILITY_BUILD_STARTED" not in types(brain, t["id"])
        assert "KNOWLEDGE_PACK_LOADED" in types(brain, t["id"])
        m = metrics(brain, t["id"])
        assert m["tier"] == "instant" and m["organ_created"] is False and m["knowledge"]["source"] == "loaded"
        assert "general it" not in text(t).lower() and "process and a thread" not in text(t)
    # TEST 10: качество — содержимое по профессии
    v = text(voip)
    assert "### VoIP Engineer — senior interview preparation" in v
    for term in ("SIP", "RTP", "SDP", "one-way audio", "ASR"):
        assert term in v, term
    assert voip["result"]["data"]["params"]["seniority"] == "senior"
    assert "OWASP" in text(cyber) and "incident" in text(cyber).lower()
    assert "usability" in text(ux).lower() and "OWASP" not in text(ux) and "SIP" not in text(ux)
    assert len({text(cyber), text(ux), text(voip)}) == 3
    assert [c["name"] for c in brain.registry.list()] == ["prepare_interview"]   # ОДИН орган на все профессии
    assert all(not e["data"]["problems"] for e in brain.bus.history(limit=5000) if e["type"] == "QUALITY_CHECK")


# ============================================================== TEST 3: тот же пакет, другое число вопросов
def test_3_existing_knowledge_with_different_question_counts(env):
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    _, a = ask(brain, "Prepare me for a senior VoIP interview with 10 questions", mode="reuse", monster_id=mid)
    _, b = ask(brain, "Prepare me for a senior VoIP interview with 20 questions", mode="reuse", monster_id=mid)
    assert a["result"]["data"]["output"]["question_count"] == 10 and b["result"]["data"]["output"]["question_count"] == 20
    assert calls(brain, a["id"]) == calls(brain, b["id"]) == 0
    assert brain.packs.get("interview_role", "voip-engineer")["uses"] == 2


# ============================================================== TEST 4, 6, 9: неизвестная профессия -> ТОЛЬКО знания
def test_4_unknown_specialization_generates_only_knowledge_once(env):
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    version_before = brain.registry.get("prepare_interview")["version"]
    brain.llm.replies.append(generated_pack())
    _, t = ask(brain, "Prepare me for a blockchain auditor interview", mode="reuse", monster_id=mid)
    ev = types(brain, t["id"])
    assert t["status"] == "completed", t["error"]
    assert ev.index("KNOWLEDGE_PACK_MISSING") < ev.index("KNOWLEDGE_GENERATING") < ev.index("KNOWLEDGE_PACK_SAVED")
    assert "CAPABILITY_BUILD_STARTED" not in ev and "CAPABILITY_MUTATION_STARTED" not in ev
    assert calls(brain, t["id"]) == 1 and metrics(brain, t["id"])["tier"] == "smart"
    assert "### Blockchain Auditor" in text(t) and "reentrancy" in text(t)
    assert brain.registry.get("prepare_interview")["version"] == version_before        # орган не тронут (TEST 6)
    pack = brain.packs.get("interview_role", "blockchain-auditor")
    assert pack["source"].startswith("llm:") and pack["validation_status"] == "validated"
    # повтор (и даже синоним) — из сохранённого пакета, без модели
    _, again = ask(brain, "Prepare me for a smart contract auditor interview", mode="reuse", monster_id=mid)
    assert calls(brain, again["id"]) == 0 and "KNOWLEDGE_PACK_LOADED" in types(brain, again["id"])
    # TEST 9: перезапуск — органы, пакеты и назначения на месте
    restarted = Brain(llm=ScriptedLLM([]))
    _, after = ask(restarted, "Prepare me again for a blockchain auditor interview", mode="reuse", monster_id=mid)
    assert after["status"] == "completed" and calls(restarted, after["id"]) == 0
    assert [c["name"] for c in restarted.monsters.get(mid)["capabilities"]] == ["prepare_interview"]


def test_knowledge_is_generated_compactly_and_only_missing_parts_are_added_later(env):
    """ЭКОНОМИЯ: первый раз — только нужный уровень (компактно, без подсказок); другой уровень и подсказки — маленькими
    дозаказами; повтор — 0 вызовов. Орган при этом не меняется."""
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    brain.llm.replies.append(generated_pack())
    ask(brain, "Prepare me for a blockchain auditor interview", mode="reuse", monster_id=mid)
    first_prompt = brain.llm.prompts[-1]
    assert "12 m, 3 j" in first_prompt and "key points" not in first_prompt          # только mid (+3 junior), без подсказок
    assert len(first_prompt) < 1500
    # senior: дозаказ ТОЛЬКО senior-вопросов к существующим темам
    brain.llm.replies.append(senior_extension())
    _, sr = ask(brain, "Prepare me for a senior blockchain auditor interview", mode="reuse", monster_id=mid)
    assert calls(brain, sr["id"]) == 1 and "KNOWLEDGE_EXTENDING" in types(brain, sr["id"])
    assert "NEW senior questions" in brain.llm.prompts[-1] and '"competencies"' not in brain.llm.prompts[-1]
    assert all(q["level"] == "senior" for s_ in sr["result"]["data"]["output"]["sections"] for q in s_["questions"])
    _, sr2 = ask(brain, "Prepare me for a senior blockchain auditor interview with 10 questions", mode="reuse", monster_id=mid)
    assert calls(brain, sr2["id"]) == 0
    # «с ответами»: дозаказ подсказок только для показанных вопросов
    brain.llm.replies.append(hints_for_prompt)
    _, ans = ask(brain, "Prepare me for a senior blockchain auditor interview with answers", mode="reuse", monster_id=mid)
    assert calls(brain, ans["id"]) == 1 and "Key points" in text(ans)
    _, ans2 = ask(brain, "Prepare me for a senior blockchain auditor interview with answers", mode="reuse", monster_id=mid)
    assert calls(brain, ans2["id"]) == 0
    assert brain.registry.get("prepare_interview")["version"] == "1.0.0"


def test_generic_filler_pack_is_rejected_and_regenerated(env):
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    brain.llm.replies += [generated_pack(filler=True), generated_pack()]          # первый — «вода», проверка возвращает модели
    _, t = ask(brain, "Prepare me for a blockchain auditor interview", mode="reuse", monster_id=mid)
    assert calls(brain, t["id"]) == 2 and "process and a thread" not in text(t)
    assert "generic filler" in brain.llm.prompts[-1]


def test_ambiguous_specialization_asks_instead_of_guessing(env):
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    brain.llm.replies.append(json.dumps({"ambiguous": True, "clarification": "Do you mean the Swift language (iOS) or the SWIFT banking network?"}))
    _, t = ask(brain, "Prepare me to SWIFT interview", mode="reuse", monster_id=mid)
    assert "Swift language" in text(t) and "general it" not in text(t).lower()
    assert brain.packs.get("interview_role", "swift") is None and calls(brain, t["id"]) == 1


# ============================================================== TEST 5: погода на шаблоне
def test_5_weather_from_template_switches_cities_without_new_organ(env):
    brain, meteo = env
    mid = teach(brain, WEATHER_SPEC, "Learn how to check the weather")["monster_id"]
    _, a = ask(brain, "Weather in Dubai and Tel Aviv", mode="reuse", monster_id=mid)
    meteo.queries.clear()
    _, b = ask(brain, "Weather in Kyiv and Prague", mode="reuse", monster_id=mid)
    assert [it["params"]["location"] for it in b["result"]["data"]["output"]["items"]] == ["Kyiv", "Prague"]
    assert sorted(meteo.queries) == ["Kyiv", "Prague"] and "Dubai" not in text(b)
    assert calls(brain, a["id"]) == calls(brain, b["id"]) == 0 and len(brain.registry.list()) == 1


# ============================================================== барьер: существующий монстр не строит органы молча
def test_gate_refuses_silent_organ_creation_for_existing_monster(env):
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    spec = {"name": "salary_estimator", "description": "estimates salaries", "purpose_en": "Estimate salary ranges",
            "purpose_cs": "Mzdy", "inputs": {"role": "str"}, "outputs": {"range": "str"}, "dependencies": [], "network": False}
    brain.llm.replies.append(json.dumps({"intent": "task", "understanding": {"en": "salary", "cs": "mzda"}, "params": {"role": "x"},
                                         "requirements": [{"id": "r1", "need": {"en": "salary", "cs": "mzda"}, "decision": "CREATE", "spec": spec}]}))
    t0 = time.time()
    _, t = ask(brain, "What salary can a VoIP engineer expect in Prague?", mode="reuse", monster_id=mid)
    d = t["result"]["data"]["decision"]
    assert d["decision"] == "confirm" and d["missing_functionality"] == ["salary_estimator"] and d["user_confirmation_required"]
    assert "CAPABILITY_BUILD_STARTED" not in types(brain, t["id"]) and brain.registry.get("salary_estimator") is None
    assert calls(brain, t["id"]) == 1 and time.time() - t0 < 30


# ============================================================== миграция старого «узкого» органа
def test_legacy_interview_organ_is_upgraded_to_the_method_with_rollback(env, monkeypatch):
    brain, _ = env
    monkeypatch.setattr(config, "USE_TEMPLATES", False)                   # «старая» установка: орган написан моделью
    from test_regressions import BAD_CODE, BAD_TESTS
    from test_regressions import INTERVIEW_SPEC as OLD_SPEC
    brain.llm.replies += [learn(OLD_SPEC, "learn interviews"), batch(("prepare_it_interview", BAD_CODE, BAD_TESTS))]
    out = brain.start_task("Learn interviews", [], "en"); wait(brain)
    mid = out["monster_id"]
    monkeypatch.setattr(config, "USE_TEMPLATES", True)
    restarted = Brain(llm=ScriptedLLM([]))                                 # запуск новой версии приложения
    assert restarted.adopted == ["prepare_it_interview"]
    cap = restarted.registry.get("prepare_it_interview")
    assert cap["version"] == "1.1.0" and (cap["skill"] or {}).get("knowledge")
    _, voip = ask(restarted, "Prepare me to SENIOR VOIP interview", mode="reuse", monster_id=mid)
    assert "### VoIP Engineer — senior" in text(voip) and calls(restarted, voip["id"]) == 0
    assert restarted.registry.rollback("prepare_it_interview") == "1.0.0"   # откат доступен
    assert Brain(llm=ScriptedLLM([])).adopted == []                        # повторно не мигрирует (уважаем откат)


# ============================================================== TEST 8: честный учёт
def test_8_metrics_and_economy_are_measured(env):
    brain, _ = env
    learned = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")
    mid = learned["monster_id"]
    ask(brain, "Prepare me for a QA interview", mode="reuse", monster_id=mid)
    brain.llm.replies.append(generated_pack())
    _, g = ask(brain, "Prepare me for a blockchain auditor interview", mode="reuse", monster_id=mid)
    m = metrics(brain, g["id"])
    assert m["llm_calls"] == 1 and m["prompt_tokens"] > 0 and m["completion_tokens"] > 0 and m["organ_created"] is False
    assert metrics(brain, learned["task_id"])["template_used"] is True
    eco = report(brain.db)
    tiers = eco["measured"]["by_tier"]
    assert tiers["instant"]["tasks"] == 1 and tiers["instant"]["llm_calls"] == 0 and tiers["smart"]["llm_calls"] >= 1
    assert eco["measured"]["knowledge_generated"] == 1 and eco["measured"]["knowledge_loaded"] >= 1
    assert eco["measured"]["organs_generated_by_llm"] == 0


# ============================================================== из проекта коллеги: вопрос вместо догадки, кэш, бюджет, $
def test_missing_value_is_asked_without_ai_and_the_answer_continues_the_skill(env):
    brain, meteo = env
    mid = teach(brain, WEATHER_SPEC, "Learn how to check the weather")["monster_id"]
    _, q = ask(brain, "What's the weather tomorrow?", mode="reuse", monster_id=mid)
    assert text(q) == "For which city?" and calls(brain, q["id"]) == 0
    assert q["result"]["data"]["needs_input"]["missing"] == "location"
    meteo.queries.clear()
    _, a = ask(brain, "Prague", mode="reuse", monster_id=mid)                 # ответ на вопрос монстра
    assert a["status"] == "completed" and "### Prague" in text(a) and calls(brain, a["id"]) == 0
    assert meteo.queries == ["Prague"] and "SKILL_INPUT_RECEIVED" in types(brain, a["id"])
    assert a["result"]["data"]["params"]["start_date"] == (date.today() + timedelta(days=1)).isoformat()   # «завтра» сохранилось
    # новый запрос (а не ответ) не принимается за название города
    _, other = ask(brain, "Weather in Oslo", mode="reuse", monster_id=mid)
    assert "### Oslo" in text(other)


def test_missing_profession_is_asked_and_answer_loads_knowledge(env):
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    _, q = ask(brain, "Prepare me for an interview", mode="reuse", monster_id=mid)
    assert text(q) == "Which profession is the interview for?" and calls(brain, q["id"]) == 0
    _, a = ask(brain, "VoIP", mode="reuse", monster_id=mid)
    assert "### VoIP Engineer" in text(a) and calls(brain, a["id"]) == 0


def test_identical_request_reuses_the_verified_plan_and_a_failed_task_forgets_it(env):
    brain, _ = env
    plan = json.dumps({"intent": "task", "understanding": {"en": "advice", "cs": "rada"}, "params": {}, "requirements": [],
                       "direct_answer": {"en": "Sleep well before the exam.", "cs": "Vyspi se."}})
    brain.llm.replies.append(plan)
    _, a = ask(brain, "Any advice before my exam?")
    _, b = ask(brain, "Any advice before my exam?")
    assert calls(brain, a["id"]) == 1 and calls(brain, b["id"]) == 0 and "LLM_CACHE_HIT" in types(brain, b["id"])
    assert text(b) == "Sleep well before the exam."
    eco = report(brain.db)["measured"]["llm_cache"]
    assert eco["hits"] == 1 and eco["saved_tokens"] > 0
    # провал задачи стирает её ответы из кэша: неудачный план не повторится
    bad = json.dumps({"intent": "task", "understanding": {"en": "x", "cs": "x"}, "params": {}, "requirements": [
        {"id": "r1", "need": {"en": "x", "cs": "x"}, "decision": "CREATE", "spec": {"name": "x_tool", "description": "d",
         "purpose_en": "p", "purpose_cs": "p", "inputs": {}, "outputs": {}, "dependencies": [], "network": False}}]})
    brain.llm.replies += [bad, "no files here"]                               # сборка провалится
    _, f = ask(brain, "Build me an x tool")
    assert f["status"] == "failed"
    assert brain.db.one("SELECT COUNT(*) n FROM llm_cache WHERE task_id=?", (f["id"],))["n"] == 0


def test_token_budget_stops_a_runaway_task(env, monkeypatch):
    brain, _ = env
    monkeypatch.setattr(config, "MAX_TOKENS_PER_TASK", 50)                    # крошечный бюджет обычной задачи
    brain.llm.replies.append(json.dumps({"intent": "task", "understanding": {"en": "x", "cs": "x"}, "params": {},
                                         "requirements": [], "direct_answer": {"en": "ok", "cs": "ok"}}))
    _, t = ask(brain, "Tell me something interesting about lightning and laboratories " * 5)
    assert t["status"] == "failed" and "token budget" in t["error"]
    assert "LLM_BUDGET_EXCEEDED" in types(brain, t["id"]) and calls(brain, t["id"]) == 0


def test_economy_reports_dollars_from_measured_usage(env):
    brain, _ = env
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    brain.llm.replies.append(generated_pack())
    ask(brain, "Prepare me for a blockchain auditor interview", mode="reuse", monster_id=mid)
    m = report(brain.db)["measured"]
    assert m["usd_total"] > 0 and m["by_tier"]["smart"]["usd"] > 0
    assert m["executions"][0]["usd"] > 0
