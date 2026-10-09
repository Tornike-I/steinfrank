"""РЫНОК АГЕНТОВ SOKOSUMI: агент подбирается САМ под потребность, которую написал человек, — быстро и без ИИ.

Скорость:
  * каталог агентов хранится локально (таблица sokosumi_agents): скачивается в фоне при старте и раз в сутки
    (бесплатный запрос). Подбор идёт по локальной копии — миллисекунды, ни одного сетевого запроса;
  * схема полей выбранного агента подгружается ЗАРАНЕЕ (пока человек читает предложение), поэтому при подтверждении
    задание создаётся сразу.
Как выбираем (детерминированно, объяснимо):
  * слова потребности ∩ слова агента: название ×3, теги ×2, описание ×1 (с простым стеммингом, как у навыков);
  * бонус за совпадение НАМЕРЕНИЯ (исследование, юриспруденция, финансы, код, перевод…) и штраф за чужую область
    (картинки/видео/музыка/крипта), если о ней не просили;
  * отсев: цена выше потолка, агент недоступен, у агента нет текстового поля, куда можно положить задание;
  * при равенстве — дешевле. Если никто не подошёл — запасной агент из настройки (SOKOSUMI_RESEARCH_AGENT_ID).
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, skillmeta
from .db import Database, dumps, loads, now
import re

from .sokosumi import SokosumiClient, SokosumiError, _schema_fields, fill_plan, job_credits

REFRESH_HOURS = 24
INTENTS = {   # намерение -> слова в потребности и слова в описании агента
    "research": ({"research", "trend", "trends", "market", "requir", "industr", "compet", "analys", "report", "invest", "overview",
                  "compare"}, {"research", "search", "web", "analyst", "analysis", "report", "insight", "market", "intellig"}),
    "news": ({"news", "recent", "this week", "today", "breaking"}, {"news"}),
    "legal": ({"legal", "contract", "law", "compliance", "gdpr", "terms"}, {"legal", "contract", "law", "lawyer", "complian"}),
    "finance": ({"finance", "financial", "stock", "price", "valuation", "budget", "tax"}, {"financ", "stock", "valuat", "account", "tax"}),
    "code": ({"code", "coding", "bug", "repository", "python", "javascript", "api"}, {"code", "coding", "develop", "program", "github"}),
    "translate": ({"translat", "language", "localiz"}, {"translat", "localiz", "language"}),
    "marketing": ({"marketing", "seo", "campaign", "social", "brand"}, {"marketing", "seo", "social", "brand", "campaign"}),
}
FOREIGN = {"image", "video", "audio", "music", "nft", "crypto", "trading", "meme", "avatar"}
# короткий ФАКТИЧЕСКИЙ вопрос («who is the current president…») -> агенты-«ответчики», а не многочасовые исследования
FACT_RE = re.compile(r"^\s*(who|what|when|where|which|how many|how much|is|are|does|кто|что|когда|где|сколько|kdo|co|kdy|kde)\b"
                     r"|\b(current|currently|now|today|right now|сейчас|сегодня|teď|dnes)\b", re.I)
FACT_NAME = {"answer", "answers", "factual", "fact", "facts", "single", "quick", "concise"}
# слова в НАЗВАНИИ агента, которые не делают его узким специалистом (сравниваются ЦЕЛЫЕ слова, не обрезанные основы)
GENERIC_NAME = {"research", "researcher", "agent", "assistant", "analysis", "analyzer", "analyst", "insight", "insights", "web",
                "single", "answer", "answers", "advanced", "deep", "general", "search", "smart", "pro", "expert", "bot", "tool",
                "the", "and", "for", "with", "ai", "quick"}


def _name_words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) > 2]


def _specialist_coverage(name: str, words: set[str]) -> float | None:
    """Доля ПРЕДМЕТНЫХ слов названия агента (Company, Instagram, Prompt…), которые есть в запросе; None — агент общий."""
    special = [w for w in _name_words(name) if w not in GENERIC_NAME]
    if not special:
        return None
    hit = sum(1 for w in special if w in words or w[:5] in words)
    return hit / len(special)
ONLINE = {"", "online", "active", "available", "ready", "published"}


def _tags(agent: dict) -> list[str]:
    out = []
    for t in agent.get("tags") or agent.get("categories") or []:
        out.append(str(t.get("name") if isinstance(t, dict) else t))
    return out


class AgentMarket:
    def __init__(self, db: Database, bus=None, client_factory=None):
        self.db, self.bus = db, bus
        self.client_factory = client_factory or (lambda: SokosumiClient())
        self._lock = threading.Lock()
        self.last_error: str | None = None      # почему каталог не загрузился (показывается в /api/sokosumi/agents)

    def enabled(self) -> bool:
        return bool(config.SOKOSUMI_API_KEY)

    # ------------------------------------------------------------------ каталог (локальная копия)
    def _stale(self) -> bool:
        row = self.db.one("SELECT MIN(fetched_at) t, COUNT(*) n FROM sokosumi_agents")
        if not row["n"]:
            return True
        return row["t"] < (datetime.now(timezone.utc) - timedelta(hours=REFRESH_HOURS)).isoformat(timespec="seconds")

    def refresh(self, force: bool = False) -> int:
        """Скачать список агентов (один бесплатный запрос). Схемы полей не трогаем — они подгружаются по мере надобности."""
        if not self.enabled() or (not force and not self._stale()):
            return 0
        with self._lock:
            agents = self.client_factory().list_agents(limit=100)    # Sokosumi отдаёт не больше 100 за запрос
            keep = {r["id"]: r["input_fields"] for r in self.db.query("SELECT id, input_fields FROM sokosumi_agents")}
            self.db.execute("DELETE FROM sokosumi_agents")
            for a in agents:
                if not isinstance(a, dict) or not a.get("id"):
                    continue
                self.db.execute("INSERT OR REPLACE INTO sokosumi_agents(id,name,description,tags,price,status,input_fields,raw,fetched_at)"
                                " VALUES(?,?,?,?,?,?,?,?,?)",
                                (str(a["id"]), str(a.get("name") or ""), str(a.get("description") or a.get("summary") or ""),
                                 dumps(_tags(a)), job_credits(a, -1), str(a.get("status") or "").lower(), keep.get(str(a["id"])),
                                 dumps(a)[:20000], now()))
            self.last_error = None
            if self.bus:
                self.bus.emit("SOKOSUMI_CATALOG_UPDATED", agents=len(agents))
        # формы полей всех агентов — заранее, В ФОНЕ (бесплатно), чтобы подбор учитывал, можно ли заполнить форму агента
        threading.Thread(target=self.prefetch_all, daemon=True).start()
        return len(agents)

    def refresh_async(self) -> None:
        if self.enabled():
            threading.Thread(target=self._safe_refresh, daemon=True).start()

    def _safe_refresh(self) -> None:
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001 — каталог не должен мешать запуску
            self.last_error = str(exc)[:300]
            if self.bus:
                self.bus.emit("SOKOSUMI_CATALOG_FAILED", error=str(exc)[:300])

    def catalog(self) -> list[dict]:
        return [{**r, "tags": loads(r["tags"], [])} for r in self.db.query("SELECT * FROM sokosumi_agents")]

    # ------------------------------------------------------------------ подбор под потребность (без ИИ, миллисекунды)
    def select(self, need: str, *, max_credits: float | None = None, limit: int = 3) -> list[dict]:
        if not self.db.one("SELECT COUNT(*) n FROM sokosumi_agents")["n"] and self.enabled():
            try:
                self.refresh(force=True)                      # первый раз — синхронно, один запрос
            except (SokosumiError, Exception) as exc:  # noqa: BLE001
                self.last_error = str(exc)[:300]
                return []
        cap = config.SOKOSUMI_MAX_CREDITS_PER_JOB if max_credits is None else max_credits
        words = skillmeta.tokens(need)
        low = need.lower()
        intents = [k for k, (need_w, _) in INTENTS.items()
                   if any(w.startswith(tuple(need_w)) for w in words) or any(" " in n and n in low for n in need_w)]
        is_fact = bool(FACT_RE.search(need)) and len(need.split()) <= 14 and "research" not in intents
        ranked = []
        for a in self.catalog():
            if a["status"] not in ONLINE:
                continue
            if a["price"] is not None and a["price"] > cap:
                continue
            fields = loads(a["input_fields"], None)
            if fields is not None:
                main, missing = fill_plan(fields)
                if main is None or missing:
                    continue          # некуда положить вопрос или есть обязательные поля, которые нечем заполнить
            name, tags, desc = skillmeta.tokens(a["name"]), skillmeta.tokens(" ".join(a["tags"])), skillmeta.tokens(a["description"])
            hit = words & (name | tags | desc)
            score = 3 * len(words & name) + 2 * len(words & tags) + len(words & desc)
            why = sorted(hit)[:6]
            doc = name | tags | desc
            for k in intents:
                if any(d.startswith(tuple(INTENTS[k][1])) for d in doc):
                    score += 4
                    why.append(f"intent:{k}")
            if doc & FOREIGN and not words & FOREIGN:
                score -= 5
            # узкий специалист (Company/Instagram/Prompt…): запрос не про его предмет — ни по названию, ни по тегам/описанию — штраф
            cov = _specialist_coverage(a["name"], words)
            if cov is not None and cov < 0.5 and len(words & (tags | desc)) < 2:
                score -= 6
                why.append("specialist")
            if is_fact:
                nw = set(_name_words(a["name"]))
                if nw & FACT_NAME:
                    score += 6
                    why.append("intent:fact")
                elif any(d.startswith(("answer", "factual")) for d in desc):
                    score += 1
                if nw & {"advanced", "deep"}:
                    score -= 3                                 # для одного факта не нанимаем дорогое большое исследование
            if score > 0:
                ranked.append({"id": a["id"], "name": a["name"], "price": a["price"] if a["price"] is not None and a["price"] >= 0 else None,
                               "score": score, "why": why, "_known": fields is not None})
        ranked.sort(key=lambda x: (-x["score"], x["price"] if x["price"] is not None else 1e9))
        # форма ещё не загружена — догружаем ТОЛЬКО для лучших кандидатов и пропускаем тех, чью форму нельзя заполнить
        checked, verified = 0, []
        for r in ranked:
            if len(verified) >= limit:
                break
            if not r.pop("_known"):
                if checked >= 4:
                    continue
                checked += 1
                sch = self.schema(r["id"])
                if sch is not None:
                    main, missing = fill_plan(sch.get("input_data") or [])
                    if main is None or missing:
                        continue
            verified.append(r)
        ranked = verified
        if not ranked and config.SOKOSUMI_RESEARCH_AGENT_ID:
            ranked = [{"id": config.SOKOSUMI_RESEARCH_AGENT_ID, "name": "configured agent", "price": None, "score": 0,
                       "why": ["fallback: SOKOSUMI_RESEARCH_AGENT_ID"]}]
        return ranked[:limit]

    # ------------------------------------------------------------------ схема полей (заранее, с кэшем)
    def schema(self, agent_id: str) -> dict | None:
        row = self.db.one("SELECT input_fields FROM sokosumi_agents WHERE id=?", (agent_id,))
        if row and row["input_fields"]:
            return {"input_data": loads(row["input_fields"], [])}
        try:
            s = self.client_factory().input_schema(agent_id)
        except (SokosumiError, Exception):  # noqa: BLE001
            return None
        fields = _schema_fields(s)
        if row:
            self.db.execute("UPDATE sokosumi_agents SET input_fields=? WHERE id=?", (dumps(fields), agent_id))
        return {"input_data": fields}

    def prefetch_all(self) -> int:
        """Скачать формы полей всех агентов, у кого их ещё нет (по одному бесплатному GET на агента)."""
        n = 0
        for r in self.db.query("SELECT id FROM sokosumi_agents WHERE input_fields IS NULL"):
            if self.schema(r["id"]) is not None:
                n += 1
        return n

    def prefetch(self, agent_id: str) -> None:
        """Подгрузить схему в фоне, пока человек читает предложение."""
        threading.Thread(target=self.schema, args=(agent_id,), daemon=True).start()


def _has_text_field(fields: list[dict]) -> bool:
    return any(str(f.get("type", "")).lower() in ("string", "text", "textarea", "") for f in fields or [])


def timed_select(market: AgentMarket, need: str, **kw) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    return market.select(need, **kw), round((time.perf_counter() - t0) * 1000, 1)
