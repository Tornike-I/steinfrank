"""СВЕЖИЕ ЗНАНИЯ ЧЕРЕЗ SOKOSUMI (спонсор хакатона): заплатить ОДИН раз — дальше бесплатно для всех монстров.

Пакеты знаний пишет модель «по памяти». Когда пользователь просит актуальное («latest requirements», «актуальные
вопросы»), а пакет давно не проверялся по реальным источникам:
  1. монстр СРАЗУ отвечает из имеющегося пакета (бесплатно) и честно пишет, на какую дату эти знания;
  2. предлагает нанять агента-исследователя Sokosumi: цена в кредитах и ≈ $, время — минуты (решение "research_knowledge");
  3. после подтверждения задание идёт В ФОНЕ (лабораторию не блокирует): агент ищет в реальных источниках;
  4. быстрая модель одним маленьким вызовом выжимает из отчёта актуальные пункты, источники и новые вопросы;
  5. пакет получает новую версию (source ...+sokosumi, срок годности 90 дней) — следующий запрос: 0 вызовов, 0 кредитов.
Защита кредитов: без ключа и агента — выключено; потолок цены задания; дневной лимит; повтор не предлагается, пока
исследование свежее (FRANK_RESEARCH_FRESH_DAYS).
"""
import contextvars
import json
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

from . import config, pricing, skillmeta
from .agent_market import AgentMarket
from .db import dumps, loads, now
from .knowledge import Knowledge, make_key
from .knowledge_packs import LV, KnowledgePacks, ask_knowledge
from .sokosumi import SokosumiClient, job_credits

FRESH_RE = re.compile(r"\b(current(ly)?|latest|up[- ]to[- ]date|updated|newest|recent|this year|today'?s|right now|nowadays|"
                      r"these days|on the market|(job|labou?r|stock|housing|crypto) market|trend(s|ing)?|"
                      r"актуальн\w*|свеж\w*|последн\w*|нов(ые|ейш)\w*|сейчас|на рынке|рын(ок|ка) труда|тренд\w*|"
                      r"aktuáln\w*|nejnověj\w*|současn\w*|na trhu|trend\w*)\b", re.I)
YEAR_RE = re.compile(r"\b(20\d\d)\b")
# явная просьба исследовать в живых источниках (с частыми опечатками: «reserch») — сразу предложение агента Sokosumi
RESEARCH_RE = re.compile(r"(?<![\w/])(/research\b|res[ea]{1,2}r?ch\w*|reser?ch\w*|look (it |this )?up online|"
                         r"search (the )?(web|internet|online)|web search|google (it|this)|sokosumi|hire (an? )?(research )?agent|"
                         r"исследу\w*|исследован\w*|(найди|поищи|ищи) в (интернете|сети)|загугли\w*|"
                         r"prozkoumej|průzkum\w*|vyhledej na (webu|internetu))", re.I)
URL_RE = re.compile(r"https?://[^\s)\]>\"']+")

DIGEST_PROMPT = (
    'ROLE: "{title}". EXISTING TOPICS (index: name): {topics}\n'
    "Below is a research report from real, current sources. Extract ONLY what it actually says (do not invent):\n"
    '{{"current_points": [up to 8 short bullet facts about current requirements, tools, trends - with years/versions if given], '
    '"sources": [up to 6 source URLs or names mentioned in the report], '
    '"questions": [[topic_index, "j|m|s|l", "new interview question based on these current facts"], ... up to 8]}}\n'
    "If the report contains nothing useful, return empty lists.\nREPORT:\n{report}")


class Researcher:
    def __init__(self, packs: KnowledgePacks, llm, ledger, bus, client_factory=None, market: AgentMarket | None = None):
        self.packs, self.llm, self.ledger, self.bus = packs, llm, ledger, bus
        self._client_factory = client_factory or (lambda: SokosumiClient())
        self.market = market or AgentMarket(ledger.db, bus, client_factory=lambda: self._client_factory())
        self._running: set[str] = set()
        self._lock = threading.Lock()
        self.explicit_only = False
        self.questions = QuestionResearch(self)      # любой вопрос про актуальное (не только пакеты знаний)

    @property
    def client_factory(self):
        return self._client_factory

    @client_factory.setter
    def client_factory(self, f):                       # тесты подменяют клиент — рынок агентов использует тот же
        self._client_factory = f
        self.market.client_factory = f

    # ------------------------------------------------------------------ условия
    def available(self) -> bool:
        return bool(config.SOKOSUMI_API_KEY)          # агента выбираем сами под тему (запасной — из настройки)

    @staticmethod
    def wants_fresh(text: str) -> bool:
        """Вопрос про актуальное: слова «current/на рынке/тренды…» или текущий/будущий год («best job for 2026»)."""
        text = text or ""
        return (bool(FRESH_RE.search(text) or RESEARCH_RE.search(text))
                or any(int(y) >= date.today().year for y in YEAR_RE.findall(text)))

    @staticmethod
    def wants_research(text: str) -> bool:
        """Человек ЯВНО просит исследование («do research…», «/research», «найди в интернете», «sokosumi»)."""
        return bool(RESEARCH_RE.search(text or ""))

    def should_offer(self, text: str, *, needs_internet: bool = False) -> bool:
        """Whether a paid research offer is appropriate under the active product policy."""
        if self.explicit_only:
            return self.wants_research(text)
        return needs_internet or self.wants_fresh(text)

    @staticmethod
    def researched_at(pack: dict) -> str | None:
        return ((pack.get("content") or {}).get("research") or {}).get("fetched_at")

    def is_fresh(self, pack: dict) -> bool:
        at = self.researched_at(pack)
        if not at:
            return False
        return datetime.fromisoformat(at) > datetime.now(timezone.utc) - timedelta(days=config.RESEARCH_FRESH_DAYS)

    def spent_today(self) -> float:
        r = self.ledger.db.one("SELECT COALESCE(SUM(credits),0) c FROM usage WHERE kind='sokosumi' AND created_at >= ?",
                               (date.today().isoformat(),))
        return float(r["c"])

    def choose(self, pack: dict) -> list[dict]:
        """Агенты под ЭТУ тему (лучший первым): подбор по локальному каталогу, без ИИ и без сетевых запросов."""
        c = pack.get("content") or {}
        need = (f"research current market requirements, trends, tools and interview topics for {pack['title']} "
                f"{c.get('domain') or ''} {c.get('specialization') or ''}")
        return self.market.select(need)

    @staticmethod
    def price_of(agent: dict) -> float:
        """Цена агента; если Sokosumi её не отдаёт — считаем по потолку (не занижаем; потолок всё равно ограничит)."""
        return float(agent["price"]) if agent.get("price") is not None else config.SOKOSUMI_MAX_CREDITS_PER_JOB

    # ------------------------------------------------------------------ предложение (до оплаты)
    def offer(self, pack: dict, task_id: int | None) -> dict | None:
        """Решение «нанять исследователя?» с ценой — или None (нельзя: потолок, дневной лимит, уже идёт)."""
        key = f"{pack['kind']}:{pack['key']}"
        agents = self.choose(pack)
        agent = agents[0] if agents else None
        price = self.price_of(agent) if agent else 0
        why = None
        if not agent:
            why = "no suitable Sokosumi agent for this topic (and no SOKOSUMI_RESEARCH_AGENT_ID fallback)"
        elif key in self._running:
            why = "research for this knowledge is already running"
        elif price > config.SOKOSUMI_MAX_CREDITS_PER_JOB:
            why = f"agent price {price:g} credits is above the per-job cap {config.SOKOSUMI_MAX_CREDITS_PER_JOB:g}"
        elif self.spent_today() + price > config.SOKOSUMI_DAILY_CREDIT_CAP:
            why = f"daily Sokosumi credit cap {config.SOKOSUMI_DAILY_CREDIT_CAP:g} would be exceeded"
        if why:
            self.bus.emit("KNOWLEDGE_RESEARCH_SKIPPED", task_id=task_id, key=pack["key"], reason=why)
            return None
        self.market.prefetch(agent["id"])               # схема полей подгрузится, пока человек читает предложение
        return {"decision": "confirm", "action": "research_knowledge", "kind": pack["kind"], "key": pack["key"],
                "agent": agent, "alternatives": agents[1:],
                "title": pack["title"], "selected_skill": None, "missing_functionality": None, "missing_knowledge": [pack["title"]],
                "estimated_cost": {"credits": price, "usd": round(price * pricing.SOKOSUMI_CREDIT, 2),
                                   "basis": "Sokosumi agent price; ≈$ from €25/1500 credits"},
                "estimated_latency": "minutes (runs in the background)",
                "reasoning_summary": (f"The answer uses the '{pack['title']}' knowledge pack (updated {pack['updated_at'][:10]}), written "
                                      "from model knowledge. A Sokosumi research agent can check current real sources once; the result "
                                      "is saved for every monster and reused for free."),
                "user_confirmation_required": True}

    # ------------------------------------------------------------------ исследование (после подтверждения, в фоне)
    def start(self, pack: dict, task_id: int | None) -> bool:
        key = f"{pack['kind']}:{pack['key']}"
        with self._lock:
            if key in self._running or self.offer(pack, task_id) is None:
                return False
            agent = self.choose(pack)[0]
            self._running.add(key)
        self.bus.emit("KNOWLEDGE_RESEARCH_STARTED", task_id=task_id, key=pack["key"], title=pack["title"],
                      agent=agent["id"], agent_name=agent["name"], why=agent["why"], max_credits=self.price_of(agent))
        ctx = contextvars.copy_context()           # кредиты и вызов модели записываются на ту же задачу
        threading.Thread(target=ctx.run, args=(self._work, pack, task_id, key, agent), daemon=True).start()
        return True

    def _work(self, pack: dict, task_id: int | None, key: str, agent: dict) -> None:
        t0 = time.time()
        try:
            question = (f"What do employers require TODAY ({date.today().isoformat()}) from a '{pack['title']}'? Current must-have "
                        "skills, tools and versions, emerging trends, typical interview topics and questions. Cite sources.")
            text, job = self.client_factory().run(agent["id"], question, max_credits=config.SOKOSUMI_MAX_CREDITS_PER_JOB,
                                                  schema=self.market.schema(agent["id"]))
            credits = job_credits(job, self.price_of(agent))
            self.ledger.record("sokosumi", purpose="research", provider="sokosumi", model=agent["id"],
                               credits=credits, seconds=round(time.time() - t0, 1), ref=str(job.get("id") or ""))
            fresh = self.packs.get(pack["kind"], pack["key"]) or pack
            topics = [t["name"] for t in fresh["content"].get("topics", [])]
            digest = ask_knowledge(self.llm, DIGEST_PROMPT.format(title=fresh["title"], topics=json.dumps(dict(enumerate(topics))),
                                                                  report=text[:12000]),
                                   lambda o: _check_digest(o, len(topics)))
            content = fresh["content"]
            for q in digest.get("questions") or []:
                content["topics"][q[0]]["questions"].insert(0, {"q": q[2].strip(), "level": LV[q[1]], "kind": "current"})
            content["research"] = {"points": digest.get("current_points") or [],
                                   "sources": (digest.get("sources") or []) or URL_RE.findall(text)[:6],
                                   "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                   "agent": agent["id"], "agent_name": agent["name"], "job_id": job.get("id"), "credits": credits}
            src = fresh["source"] if "sokosumi" in str(fresh["source"]) else f"{fresh['source']}+sokosumi"
            saved = self.packs.put(pack["kind"], pack["key"], fresh["title"], content, source=src,
                                   validation_status="researched", ttl_days=90)
            self.bus.emit("KNOWLEDGE_RESEARCH_DONE", task_id=task_id, key=pack["key"], title=fresh["title"], version=saved["version"],
                          points=len(content["research"]["points"]), questions=len(digest.get("questions") or []),
                          credits=credits, seconds=round(time.time() - t0, 1))
        except Exception as exc:  # noqa: BLE001 — исследование не должно ронять сервер
            self.bus.emit("KNOWLEDGE_RESEARCH_FAILED", task_id=task_id, key=pack["key"], error=str(exc)[:300])
        finally:
            self._running.discard(key)


# ====================================================================== любой вопрос про АКТУАЛЬНОЕ
RESEARCH_TTL_DAYS = 30


def _norm_q(text: str) -> set[str]:
    return skillmeta.tokens(FRESH_RE.sub(" ", text or ""))


class QuestionResearch:
    """Вопрос про актуальное без подходящего навыка («current requirements for VoIP engineers»):
    сразу ответ из знаний модели + предложение нанять агента; отчёт агента приходит НОВЫМ ответом и хранится 30 дней —
    тот же/похожий вопрос потом отвечается из памяти (0 вызовов, 0 кредитов)."""

    def __init__(self, researcher: "Researcher"):
        self.r = researcher
        self.knowledge = Knowledge(researcher.ledger.db)
        self._running: set[str] = set()

    def _key(self, question: str) -> str:
        return make_key("research_question", " ".join(sorted(_norm_q(question))))

    def lookup(self, question: str) -> dict | None:
        """Сохранённый свежий отчёт по тому же или очень похожему вопросу (совпадение значимых слов >= 70%)."""
        want = _norm_q(question)
        if len(want) < 2:
            return None
        best = None
        for row in self.r.ledger.db.query("SELECT key, content, expires_at FROM knowledge WHERE skill='sokosumi_research'"):
            if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
                continue
            c = loads(row["content"], {}) or {}
            have = _norm_q(c.get("question", ""))
            sim = len(want & have) / max(1, len(want | have))
            if sim >= 0.7 and (best is None or sim > best[0]):
                best = (sim, c)
        return best[1] if best else None

    def _agents(self, question: str) -> list[dict]:
        """Подбор агента: «reserch/исследуй/найди в интернете» -> намерение research; при ЯВНОЙ просьбе, если по словам
        вопроса никто не подошёл, — общий веб-исследователь (человек просил исследование, а не конкретного специалиста)."""
        agents = self.r.market.select(RESEARCH_RE.sub(" research ", question))
        if not agents and RESEARCH_RE.search(question):
            agents = self.r.market.select("web research report with sources")
        return agents

    def offer(self, question: str, task_id: int | None) -> dict | None:
        agents = self._agents(question)
        agent = agents[0] if agents else None
        key = self._key(question)
        why = None
        if not agent:
            why = "no suitable Sokosumi agent for this question"
        elif key in self._running:
            why = "research for this question is already running"
        elif self.r.price_of(agent) > config.SOKOSUMI_MAX_CREDITS_PER_JOB:
            why = "agent price is above the per-job cap"
        elif self.r.spent_today() + self.r.price_of(agent) > config.SOKOSUMI_DAILY_CREDIT_CAP:
            why = f"daily Sokosumi credit cap {config.SOKOSUMI_DAILY_CREDIT_CAP:g} would be exceeded"
        if why:
            self.r.bus.emit("KNOWLEDGE_RESEARCH_SKIPPED", task_id=task_id, key=key[:12], reason=why)
            return None
        self.r.market.prefetch(agent["id"])
        price = self.r.price_of(agent)
        return {"decision": "confirm", "action": "research_question", "title": question[:80], "question": question,
                "agent": agent, "alternatives": agents[1:], "selected_skill": None, "missing_functionality": None,
                "missing_knowledge": [question[:80]],
                "estimated_cost": {"credits": price, "usd": round(price * pricing.SOKOSUMI_CREDIT, 2),
                                   "basis": "Sokosumi agent price; ≈$ from €25/1500 credits"},
                "estimated_latency": "minutes (runs in the background)",
                "reasoning_summary": ("This answer comes from model knowledge, not live data. A Sokosumi agent can research current "
                                      "real sources; its report arrives as a new answer and is reused for free for 30 days."),
                "user_confirmation_required": True}

    def start(self, question: str, lang: str, monster_id: int | None, task_id: int | None) -> bool:
        if self.offer(question, task_id) is None:
            return False
        agent = self._agents(question)[0]
        key = self._key(question)
        self._running.add(key)
        self.r.bus.emit("KNOWLEDGE_RESEARCH_STARTED", task_id=task_id, key=key[:12], title=question[:80], agent=agent["id"],
                        agent_name=agent["name"], why=agent["why"], max_credits=self.r.price_of(agent))
        ctx = contextvars.copy_context()
        threading.Thread(target=ctx.run, args=(self._work, question, lang, monster_id, task_id, key, agent), daemon=True).start()
        return True

    def _work(self, question, lang, monster_id, task_id, key, agent) -> None:
        t0 = time.time()
        try:
            text, job = self.r.client_factory().run(agent["id"], f"{question}\n(Answer with current facts as of {date.today().isoformat()}"
                                                                 " and cite sources.)",
                                                    max_credits=config.SOKOSUMI_MAX_CREDITS_PER_JOB, schema=self.r.market.schema(agent["id"]))
            credits = job_credits(job, self.r.price_of(agent))
            self.r.ledger.record("sokosumi", purpose="research_question", provider="sokosumi", model=agent["id"], credits=credits,
                                 seconds=round(time.time() - t0, 1), ref=str(job.get("id") or ""))
            sources = URL_RE.findall(text)[:8]
            # отчёт агента — уже готовый текст (markdown): показываем как есть, без лишнего вызова модели
            answer = (f"### {question[:120]}\n\n{text.strip()[:8000]}\n\n"
                      f"_Researched {date.today().isoformat()} by Sokosumi agent {agent['name']} · {credits:g} credits_")
            content = {"question": question, "answer": answer, "sources": sources, "agent": agent["id"], "agent_name": agent["name"],
                       "credits": credits, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            self.knowledge.put(key, category="dynamic", skill="sokosumi_research", params={"question": question}, content=content,
                               source=f"sokosumi:{agent['id']}", ttl_seconds=RESEARCH_TTL_DAYS * 86400)
            # отчёт приходит в лабораторию НОВЫМ ответом (задача-результат), не дожидаясь нового вопроса
            new_id = self.r.ledger.db.execute(
                "INSERT INTO tasks(input,files,lang,status,result,generation_before,generation_after,created_at,finished_at,monster_id,mode,"
                "strategy,metrics,duration) VALUES(?,?,?,?,?,0,0,?,?,?,?,?,?,?)",
                (f"🔎 {question[:200]}", "[]", lang, "completed",
                 dumps({"summary": {"en": answer, "cs": answer}, "files": [],
                        "data": {"research": {"agent": agent["name"], "credits": credits, "sources": sources, "for_task": task_id}}}),
                 now(), now(), monster_id, "research", dumps({"research": True, "agent": agent["id"]}),
                 dumps({"tier": "research", "llm_calls": 0, "credits": credits, "seconds": round(time.time() - t0, 1)}),
                 round(time.time() - t0, 1)))
            self.r.bus.emit("KNOWLEDGE_RESEARCH_DONE", task_id=new_id, key=key[:12], title=question[:80], points=len(sources),
                            questions=0, credits=credits, seconds=round(time.time() - t0, 1), for_task=task_id)
            self.r.bus.emit("TASK_COMPLETED", task_id=new_id, duration=round(time.time() - t0, 1), research=True)
        except Exception as exc:  # noqa: BLE001
            self.r.bus.emit("KNOWLEDGE_RESEARCH_FAILED", task_id=task_id, key=key[:12], error=str(exc)[:300])
        finally:
            self._running.discard(key)


def _check_digest(o: dict, n_topics: int) -> list[str]:
    errors = []
    for f in ("current_points", "sources", "questions"):
        if not isinstance(o.get(f), list):
            errors.append(f'"{f}" must be a list')
    for q in o.get("questions") or []:
        if not (isinstance(q, list) and len(q) >= 3 and isinstance(q[0], int) and 0 <= q[0] < n_topics and q[1] in LV
                and isinstance(q[2], str) and len(q[2]) >= 15):
            errors.append(f"bad question item {str(q)[:80]}: use [topic_index 0..{n_topics - 1}, \"j|m|s|l\", \"question\"]")
            break
    return errors
