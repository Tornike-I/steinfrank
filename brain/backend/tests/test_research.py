"""Свежие знания через Sokosumi: предложение с ценой -> подтверждение -> фоновое исследование -> пакет обновлён для всех.
Sokosumi подменён транспортом httpx (кредиты не тратятся), мозг — сценарий ответов."""
import json
import time

import httpx
import pytest

from app import config, researcher, sokosumi
from app.sokosumi import SokosumiClient
from test_architecture import INTERVIEW_SPEC, env, teach, text  # noqa: F401  (env — фикстура)
from test_skills import ask, calls, types

AGENT = "agent_research_1"
CATALOG = [
    {"id": "agent_img", "name": "Pixel Painter", "description": "Generate images and avatars from text", "tags": ["image"], "credits": 40},
    {"id": AGENT, "name": "Web Research Analyst", "description": "Deep web research with cited sources and a written report",
     "tags": [{"name": "research"}, {"name": "web"}], "credits": 120, "status": "online"},
    {"id": "agent_legal", "name": "Contract Reviewer", "description": "Reviews legal contracts and compliance terms",
     "tags": ["legal"], "credits": 90},
    {"id": "agent_deep", "name": "Enterprise Market Research", "description": "Market research reports with analysts",
     "tags": ["research", "market"], "credits": 900},
    {"id": "agent_off", "name": "Research Bot (offline)", "description": "research web", "tags": ["research"], "credits": 10,
     "status": "offline"},
]


class FakeSokosumi:
    def __init__(self, price=120, report="In 2026 employers expect SIP over TLS, Kamailio 6 and STIR/SHAKEN. Source: https://example.org/voip-jobs"):
        self.calls, self.price, self.report = [], price, report

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if request.method == "GET" and path.endswith("/agents"):
            return httpx.Response(200, json={"data": [{**a, "credits": self.price} if a["id"] == AGENT else a for a in CATALOG]})
        if path.endswith(f"/agents/{AGENT}"):
            return httpx.Response(200, json={"data": {"id": AGENT, "credits": self.price}})
        if path.endswith("/input-schema"):
            return httpx.Response(200, json={"data": {"input_data": [{"id": "research_question", "type": "string"},
                                                                     {"id": "additional_context", "type": "string",
                                                                      "validations": [{"validation": "optional"}]}]}})
        if request.method == "POST" and path.endswith("/jobs"):
            return httpx.Response(200, json={"data": {"id": "job_42"}})
        if path.endswith("/jobs/job_42"):
            return httpx.Response(200, json={"data": {"id": "job_42", "status": "completed", "credits": self.price, "result": self.report}})
        return httpx.Response(404, json={})


def digest():
    return json.dumps({"current_points": ["SIP over TLS and SRTP are expected by default", "Kamailio 6 experience is in demand",
                                          "STIR/SHAKEN attestation knowledge is required in the US"],
                       "sources": ["https://example.org/voip-jobs"],
                       "questions": [[0, "s", "How would you migrate a carrier interconnect to SIP over TLS without downtime?"],
                                     [8, "s", "Explain how STIR/SHAKEN attestation levels affect your routing decisions."]]})


@pytest.fixture()
def lab(env, monkeypatch):
    brain, _ = env
    fake = FakeSokosumi()
    monkeypatch.setattr(config, "SOKOSUMI_API_KEY", "test-key")
    monkeypatch.setattr(config, "SOKOSUMI_RESEARCH_AGENT_ID", "")              # агент выбирается САМ под тему
    monkeypatch.setattr(sokosumi.time, "sleep", lambda s: None)                    # не ждём опрос в тестах
    brain.skills.researcher.client_factory = lambda: SokosumiClient(transport=httpx.MockTransport(fake))
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    return brain, fake, mid


def wait_event(brain, typ, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        ev = [e for e in brain.bus.history(limit=5000) if e["type"] == typ]
        if ev:
            return ev[-1]
        time.sleep(0.05)
    raise AssertionError(f"no {typ}")


def test_offer_then_background_research_updates_the_pack_for_everyone(lab):
    brain, fake, mid = lab
    q = "Prepare me for a senior VoIP interview with the latest requirements"
    # 1) сразу ответ из пакета (0 вызовов модели) + предложение с ценой; платного задания НЕТ
    _, t1 = ask(brain, q, mode="reuse", monster_id=mid)
    offer = t1["result"]["data"]["offer"]
    assert t1["status"] == "completed" and calls(brain, t1["id"]) == 0 and "### VoIP Engineer" in text(t1)
    assert offer["action"] == "research_knowledge" and offer["estimated_cost"]["credits"] == 120
    assert offer["estimated_cost"]["usd"] == round(120 * 0.018, 2) and "Knowledge as of" in text(t1)
    assert offer["agent"]["id"] == AGENT and "intent:research" in offer["agent"]["why"]      # выбран сам, с объяснением
    assert not [c for c in fake.calls if c[0] == "POST"]
    # 2) подтверждение -> задание в фоне с потолком кредитов; ответ не ждёт исследования
    brain.llm.replies.append(digest())
    _, t2 = ask(brain, q, mode="reuse", monster_id=mid, allow_build=True)
    assert t2["result"]["data"]["research"]["started"] is True and "research agent is checking" in text(t2)
    done = wait_event(brain, "KNOWLEDGE_RESEARCH_DONE")
    post = next(c for c in fake.calls if c[0] == "POST")
    assert post[1].endswith(f"/agents/{AGENT}/jobs")
    assert post[2]["maxCredits"] == config.SOKOSUMI_MAX_CREDITS_PER_JOB and "VoIP Engineer" in post[2]["inputData"]["research_question"]
    assert done["data"]["points"] == 3 and done["data"]["credits"] == 120
    pack = brain.packs.get("interview_role", "voip-engineer")
    assert pack["validation_status"] == "researched" and "sokosumi" in pack["source"] and pack["version"] == 2
    credits = brain.db.one("SELECT SUM(credits) c FROM usage WHERE kind='sokosumi'")["c"]
    assert credits == 120
    # 3) дальше — бесплатно для любого монстра: актуальный раздел, без предложения, без новых заданий
    posts = len([c for c in fake.calls if c[0] == "POST"])
    _, t3 = ask(brain, q, mode="reuse", monster_id=mid)
    assert "Current market notes" in text(t3) and "SIP over TLS" in text(t3) and calls(brain, t3["id"]) == 0
    assert t3["result"]["data"]["offer"] is None and len([c for c in fake.calls if c[0] == "POST"]) == posts
    assert "STIR/SHAKEN attestation levels" in text(t3) or "SIP over TLS without downtime" in text(t3)


def test_ordinary_request_never_offers_paid_research(lab):
    brain, fake, mid = lab
    _, t = ask(brain, "Prepare me for a senior VoIP interview", mode="reuse", monster_id=mid)
    assert t["result"]["data"]["offer"] is None and "Knowledge as of" not in text(t) and fake.calls == []


def test_credit_caps_block_the_offer(lab, monkeypatch):
    brain, fake, mid = lab
    monkeypatch.setattr(config, "SOKOSUMI_MAX_CREDITS_PER_JOB", 100)               # агент стоит 120
    monkeypatch.setattr(config, "SOKOSUMI_DAILY_CREDIT_CAP", 50)
    _, t = ask(brain, "Prepare me for a senior VoIP interview with current requirements", mode="reuse", monster_id=mid)
    assert t["result"]["data"]["offer"] is None and "skipped" in text(t)
    assert "KNOWLEDGE_RESEARCH_SKIPPED" in types(brain, t["id"]) and not [c for c in fake.calls if c[0] == "POST"]


def test_without_sokosumi_key_the_answer_is_honest_about_its_date(env, monkeypatch):
    brain, _ = env
    monkeypatch.setattr(config, "SOKOSUMI_API_KEY", "")
    mid = teach(brain, INTERVIEW_SPEC, "Learn to prepare people for IT interviews")["monster_id"]
    _, t = ask(brain, "Prepare me for a UX/UI designer interview with the latest trends", mode="reuse", monster_id=mid)
    assert t["status"] == "completed" and "live research is not configured" in text(t) and t["result"]["data"]["offer"] is None


def test_fresh_words_detection():
    w = researcher.Researcher.wants_fresh
    assert w("latest requirements") and w("актуальные вопросы") and w("aktuální požadavky") and not w("Prepare me for QA")
    assert w("What the best job for 2026") and w("current top 1 work on the market") and w("топ профессий на рынке труда")
    assert not w("What happened in 2008") and not w("Weather in Prague tomorrow")


def test_explicit_research_request_offers_an_agent_at_once_without_any_model_call(lab):
    """Живой случай: «Do reserch what main strength in software» ушло в выдуманный орган. Теперь — сразу агент."""
    brain, fake, mid = lab
    q = "Do reserch what main strength in software"
    assert researcher.Researcher.wants_research(q) and researcher.Researcher.wants_research("research: best job")
    assert researcher.Researcher.wants_research("найди в интернете зарплаты") and not researcher.Researcher.wants_research("Prepare me for QA")
    _, t1 = ask(brain, q, mode="reuse", monster_id=mid)
    offer = (t1.get("result") or {}).get("data", {}).get("offer")
    assert offer, (t1["status"], t1.get("error"), text(t1)[:300], [e.get("payload") or e for e in brain.bus.history(limit=5000) if e["type"] == "KNOWLEDGE_RESEARCH_SKIPPED"][0].get("reason", "?") if False else str([e for e in brain.bus.history(limit=5000) if e["type"] == "KNOWLEDGE_RESEARCH_SKIPPED"])[-300:])
    assert offer["agent"]["id"] == AGENT and calls(brain, t1["id"]) == 0
    assert "RESEARCH_REQUESTED" in types(brain, t1["id"]) and not [c for c in fake.calls if c[0] == "POST"]
    _, t2 = ask(brain, q, mode="reuse", monster_id=mid, allow_build=True)          # «да» — агент нанят, модель не нужна
    assert t2["result"]["data"]["research"]["started"] is True and calls(brain, t2["id"]) == 0
    wait_event(brain, "KNOWLEDGE_RESEARCH_DONE")


def test_offline_organ_answer_to_a_current_question_is_marked_and_offers_an_agent(lab):
    """Орган без интернета («Job Market Catalog» в коде) на вопрос про актуальное: честная пометка + агент по кнопке."""
    brain, fake, mid = lab
    _, t = ask(brain, "Prepare me for a senior VoIP interview", mode="reuse", monster_id=mid)
    res = {"summary": {"en": "Software Engineer is #1.", "cs": "x"}, "data": {"job": "Software Engineer"}, "files": []}
    out = brain._offline_honesty(res, ["prepare_interview"], "current top 1 work on the market", "en", None, t["id"], False)
    assert "built-in data of the organ" in out["summary"]["en"] and out["data"]["offer"]["agent"]["id"] == AGENT
    assert brain._offline_honesty(res, ["prepare_interview"], "Prepare me for QA", "en", None, t["id"], False) is res


def test_current_question_that_wants_code_hires_an_agent_instead_of_building(lab):
    """Живой случай: «current top 1 work on the market» -> план «построить fetch_job_market_data» -> 442 с сборки и
    ремонтов. Теперь: предложение агента Sokosumi; «да» запускает агента, орган не строится никогда."""
    brain, fake, mid = lab
    spec = {"name": "fetch_job_market_data", "description": "job market rankings", "purpose_en": "Fetch job market data",
            "purpose_cs": "Trh práce", "inputs": {"scope": "str"}, "outputs": {"jobs": "list"}, "dependencies": [], "network": False}
    plan = json.dumps({"intent": "task", "understanding": {"en": "top job", "cs": "x"}, "params": {},
                       "requirements": [{"id": "r1", "need": {"en": "job market", "cs": "trh"}, "decision": "CREATE", "spec": spec}]})
    brain.llm.replies.append(plan)
    q = "current top 1 work on the market"
    _, t1 = ask(brain, q, mode="reuse", monster_id=mid)
    offer = t1["result"]["data"]["offer"]
    assert t1["status"] == "completed" and offer["agent"]["id"] == AGENT and "won't write code" in text(t1)
    assert "RESEARCH_INSTEAD_OF_BUILD" in types(brain, t1["id"]) and "CAPABILITY_BUILD_STARTED" not in types(brain, t1["id"])
    _, t2 = ask(brain, q, mode="reuse", monster_id=mid, allow_build=True)          # «да» = нанять агента, не строить
    assert t2["result"]["data"]["research"]["started"] is True and "CAPABILITY_BUILD_STARTED" not in types(brain, t2["id"])
    wait_event(brain, "KNOWLEDGE_RESEARCH_DONE")
    assert brain.registry.get("fetch_job_market_data") is None


# ============================================================== автоматический выбор агента под потребность
def test_agent_is_chosen_by_need_fast_and_without_network(lab):
    brain, fake, _ = lab
    market = brain.skills.researcher.market
    market.refresh(force=True)
    market.prefetch_all()                 # формы агентов (в приложении это делает фон после загрузки каталога)
    time.sleep(0.3)
    before = len(fake.calls)
    from app.agent_market import timed_select
    picks, ms = timed_select(market, "research current market requirements and trends for a VoIP Engineer")
    assert picks[0]["id"] == AGENT and ms < 100 and len(fake.calls) == before          # по локальному каталогу, без сети
    ids = [p["id"] for p in picks]
    assert "agent_deep" not in ids and "agent_off" not in ids and "agent_img" not in ids   # дороже потолка / офлайн / не та область
    assert market.select("review my legal contract for compliance")[0]["id"] == "agent_legal"
    assert market.select("draw an avatar image")[0]["id"] == "agent_img"


def test_agent_without_text_field_is_not_chosen(lab):
    brain, _, _ = lab
    market = brain.skills.researcher.market
    market.refresh(force=True)
    brain.db.execute("UPDATE sokosumi_agents SET input_fields=? WHERE id=?", (json.dumps([{"id": "file", "type": "file"}]), AGENT))
    assert AGENT not in [p["id"] for p in market.select("research current market trends")]


def test_catalog_api_shows_choice_and_reason(lab, monkeypatch):
    from fastapi.testclient import TestClient
    import app.main as main
    brain, _, _ = lab
    monkeypatch.setattr(main, "brain", brain)                                  # подмена откатывается после теста
    r = TestClient(main.app).get("/api/sokosumi/agents", params={"need": "latest market research about cloud engineers"}).json()
    assert r["enabled"] and r["catalog_size"] == len(CATALOG) and r["selected"][0]["id"] == AGENT and r["select_ms"] < 100


def test_key_with_cyrillic_letter_gives_a_clear_message(monkeypatch):
    """Найдено у пользователя: латинская M, набранная в русской раскладке, превратилась в «Ь»."""
    from app.sokosumi import SokosumiError
    monkeypatch.setattr(config, "SOKOSUMI_API_KEY", "abc" + "Ь" + "def")
    with pytest.raises(SokosumiError, match="position 4"):
        SokosumiClient()


# ============================================================== любой вопрос про актуальное (не только собеседования)
def test_any_current_question_offers_an_auto_chosen_agent_and_the_report_is_reused(lab):
    brain, fake, mid = lab
    q = "What are the current requirements for VoIP engineers?"
    direct = json.dumps({"intent": "task", "understanding": {"en": "voip reqs", "cs": "voip"}, "params": {}, "requirements": [],
                         "direct_answer": {"en": "Usually SIP, RTP and Kamailio.", "cs": "Obvykle SIP."}})
    brain.llm.replies.append(direct)
    _, t1 = ask(brain, q, mode="reuse", monster_id=mid)
    offer = t1["result"]["data"]["offer"]
    assert t1["status"] == "completed" and offer["action"] == "research_question" and offer["agent"]["id"] == AGENT
    assert "model knowledge" in text(t1) and not [c for c in fake.calls if c[0] == "POST"]
    # подтверждение: план — из кэша (0 вызовов модели), агент работает в фоне, отчёт приходит НОВЫМ ответом
    _, t2 = ask(brain, q, mode="reuse", monster_id=mid, allow_build=True)
    assert calls(brain, t2["id"]) == 0 and t2["result"]["data"]["research"]["started"] is True
    done = wait_event(brain, "KNOWLEDGE_RESEARCH_DONE")
    report = brain.task(done["task_id"])
    assert report["status"] == "completed" and "SIP over TLS" in report["result"]["summary"]["en"]
    assert "https://example.org/voip-jobs" in report["result"]["data"]["research"]["sources"]
    assert brain.db.one("SELECT SUM(credits) c FROM usage WHERE kind='sokosumi'")["c"] == 120
    # тот же вопрос (и похожая формулировка) — из сохранённого отчёта: 0 вызовов, 0 новых заданий
    posts = len([c for c in fake.calls if c[0] == "POST"])
    _, t3 = ask(brain, "What are the latest requirements for VoIP engineers?", mode="reuse", monster_id=mid)
    assert "SIP over TLS" in text(t3) and calls(brain, t3["id"]) == 0 and len([c for c in fake.calls if c[0] == "POST"]) == posts


def test_agent_with_unfillable_required_fields_is_never_hired():
    """Найдено вживую: «Company Researcher» требует Company Name и Industry; вопрос ушёл в необязательное поле,
    обязательные — пустыми, Sokosumi вернул ZodError. Теперь такая форма отбраковывается ДО создания задания."""
    from app.sokosumi import SokosumiError, _fill_inputs, fill_plan
    company = [{"id": "info", "type": "none"}, {"id": "company_name", "type": "text", "name": "Company Name", "validations": None},
               {"id": "industry", "type": "text", "name": "Industry", "validations": None},
               {"id": "prompt", "type": "textarea", "name": "Additional Focus", "validations": [{"validation": "optional", "value": "true"}]}]
    main, missing = fill_plan(company)
    assert missing == ["Industry"] or missing == ["Company Name"] or len(missing) == 1
    with pytest.raises(SokosumiError, match="cannot fill automatically"):
        _fill_inputs(company, "current president in USA")
    simple = [{"id": "info", "type": "none"}, {"id": "question", "type": "text", "name": "Question", "validations": None},
              {"id": "notes", "type": "textarea", "validations": [{"validation": "optional"}]}]
    data, _ = _fill_inputs(simple, "Who is the current president of the USA?")
    assert data == {"question": "Who is the current president of the USA?"}            # info-блок не отправляется


def test_short_fact_question_prefers_a_cheap_answer_agent(lab):
    brain, _, _ = lab
    market = brain.skills.researcher.market
    market.refresh(force=True)
    brain.db.execute("INSERT OR REPLACE INTO sokosumi_agents(id,name,description,tags,price,status,input_fields,raw,fetched_at)"
                     " VALUES('agent_fact','Web Single Answer','Answers factual questions using live web search','[]',60,'',NULL,'{}','2099-01-01')")
    brain.db.execute("INSERT OR REPLACE INTO sokosumi_agents(id,name,description,tags,price,status,input_fields,raw,fetched_at)"
                     " VALUES('agent_company','Company Researcher','Builds company profiles by researching the web','[\"research\"]',30,'',NULL,'{}','2099-01-01')")
    assert market.select("current president in USA")[0]["id"] == "agent_fact"
    assert "agent_company" not in [p["id"] for p in market.select("current president in USA")]
