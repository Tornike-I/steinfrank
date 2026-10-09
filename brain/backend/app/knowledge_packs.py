"""ПАКЕТЫ ЗНАНИЙ — предметные знания отдельно от органов. Работает для ЛЮБОГО навыка, не только для собеседований.

Три разные вещи (и три слоя кэша):
  орган (навык)     — КАК делать: код, версия, тесты.                         registry      (Skill Cache)
  пакет знаний      — ЧТО знать о предмете: роль, язык, страна, библиотека…  knowledge_packs (Knowledge Cache)
  результат задачи  — ответ на конкретные параметры.                         knowledge     (Result Cache)

Любой навык может объявить в своём коде:
  SKILL["knowledge"] = {"kind": "interview_role",          # вид знаний (общий для всех навыков этого вида)
                        "param": "role",                    # параметр, по значению которого выбирается пакет
                        "key_param": "role_key",            # (необязательно) сюда parse_request кладёт ключ распознанного пакета
                        "describe": "what one pack must contain (fields)",   # для генерации пакета моделью
                        "schema": {"field": "type/meaning", ...}}            # обязательные поля пакета
Движок (skills.py) перед исполнением:
  1) распознаёт пакет по синонимам (индекс передаётся в parse_request как context["knowledge_index"]);
  2) нет пакета -> ГЕНЕРИРУЕТ ТОЛЬКО ЗНАНИЯ (один вызов модели, не орган), проверяет структуру и релевантность, сохраняет;
  3) передаёт пакет в run() как inp["knowledge"]. Код органа при этом не меняется.
Новая профессия / тема / страна = новый ПАКЕТ, а не новый орган.
"""
import json
import re
from datetime import datetime, timedelta, timezone

from .db import Database, dumps, loads, now
from .llm import LLM, LLMError

GENERIC_FILLER = re.compile(r"process(es)? and (a )?thread|version control|\bgit\b|osi model|tell me about yourself|"
                            r"strengths and weaknesses|where do you see yourself", re.I)
LEVELS = ("junior", "mid", "senior", "lead")


def slug(text: str) -> str:
    return re.sub(r"[^\w]+", "-", str(text).lower(), flags=re.UNICODE).strip("-")[:60] or "item"


def norm(text: str) -> str:
    """Нормализация для распознавания: регистр, «UX/ UI» -> «ux/ui», лишние пробелы."""
    t = str(text).lower()
    t = re.sub(r"\s*([/\-])\s*", r"\1", t)
    return " ".join(t.split())


# ------------------------------------------------------------------ виды знаний: как генерировать и проверять пакет
# ЭКОНОМИЯ: модель генерирует ТОЛЬКО то, что нужно сейчас (уровень и число вопросов текущего запроса), в КОМПАКТНОМ виде
# (строки-массивы вместо JSON с повторяющимися ключами), без подсказок к ответам, если их не просили. Недостающее позже
# дозаказывается маленькими запросами (extend_level / add_hints). Первый замер полного пакета: 6605 выходных токенов, 127 с.
PACK_SYSTEM = (
    "You build a reusable KNOWLEDGE PACK: structured domain knowledge that an already-installed skill (a generic method) will "
    "use. You do NOT write code. Be specific to the requested subject - never generic filler that would fit any subject. "
    "Be concise: short phrases, one sentence per question. If the subject is ambiguous in a way that changes the content "
    "(e.g. 'SWIFT' = Apple language or banking network), answer only {\"ambiguous\": true, \"clarification\": \"one short "
    "question listing the interpretations\"}. Answer ONE compact JSON object.")

LV = {"j": "junior", "m": "mid", "s": "senior", "l": "lead"}
LV_CODE = {v: k for k, v in LV.items()}
PREFERENCE = {"junior": ["junior", "mid"], "mid": ["mid", "junior", "senior"], "senior": ["senior", "mid", "lead"],
              "lead": ["lead", "senior", "mid"]}

KINDS = {
    # вопросы и подсказки дозаказываются по уровням; поля навыка, которые на это влияют, объявлены в SKILL["knowledge"]
    "interview_role": {"param": "role", "schema": {"title": "str", "competencies": "list", "topics": "list"}},
}


def interview_plan(level: str | None, count: int | None) -> dict[str, int]:
    """Сколько вопросов каких уровней нужно СЕЙЧАС: основной уровень с небольшим запасом + немного соседнего."""
    level = level if level in PREFERENCE else "mid"
    n = max(8, min(int(count or 12), 20))
    return {level: n, PREFERENCE[level][1]: 3}


def interview_prompt(subject: str, plan: dict[str, int], hints: bool) -> str:
    want = ", ".join(f"{n} {LV_CODE[lv]}" for lv, n in plan.items())
    item = '[topic_index, "j|m|s|l", "question", "key points, max 12 words"]' if hints else '[topic_index, "j|m|s|l", "question"]'
    return (f'PROFESSION: "{subject}" (keep this exact meaning; do not generalize it).\n'
            'JSON: {"title": "canonical role name", "domain": "...", "aliases": [5-8 synonyms/abbreviations incl. Czech and Russian], '
            '"competencies": [6-8 short role-specific items], "topics": [5-7 short role-specific topic names], '
            f'"questions": [{item}, ...], "exercises": [2-3 short practical tasks], "rubric": [3 short criteria], '
            '"recommendations": [3 short tips]}\n'
            f"QUESTIONS NEEDED NOW: {want} (j=junior, m=mid, s=senior, l=lead), spread over the topics. Senior/lead questions need "
            "architecture, trade-off and production-troubleshooting reasoning in THIS domain. No generic IT filler (processes vs "
            "threads, Git basics, OSI layers, 'tell me about yourself').")


def _check_questions(subject: str, qs, n_topics: int, plan: dict[str, int], hints: bool, existing=()) -> list[str]:
    errors = []
    if not isinstance(qs, list) or not qs:
        return ['"questions" must be a non-empty list of [topic_index, level, question]']
    texts = []
    for q in qs:
        if not (isinstance(q, list) and len(q) >= 3 and isinstance(q[0], int) and 0 <= q[0] < n_topics and q[1] in LV
                and isinstance(q[2], str)):
            return [f"bad question item {str(q)[:80]}: use [topic_index 0..{n_topics - 1}, \"j|m|s|l\", \"question\"]"]
        texts.append(q[2].strip())
    if hints and any(len(q) < 4 or not str(q[3]).strip() for q in qs):
        errors.append("every question needs its key points (4th element)")
    seen = {str(x).lower() for x in existing}
    if len({t.lower() for t in texts} | seen) < len(texts) + len(seen):
        errors.append("duplicate questions")
    for lv, need in plan.items():
        have = sum(1 for q in qs if LV[q[1]] == lv)
        if have < max(2, int(need * 0.7)):
            errors.append(f"only {have} {lv} questions; need {need}")
    filler = [t for t in texts if GENERIC_FILLER.search(t)]
    if len(filler) > max(1, len(texts) // 8):
        errors.append(f"too much generic filler not specific to '{subject}': {filler[:3]}")
    if any(len(t) < 15 for t in texts):
        errors.append("some questions are too short to be meaningful")
    return errors


def validate_interview(subject: str, obj: dict, plan: dict[str, int], hints: bool) -> list[str]:
    if obj.get("ambiguous"):
        return [] if str(obj.get("clarification", "")).strip() else ['"ambiguous" needs a "clarification" question']
    errors = [f'missing or empty "{f}"' for f in ("title", "competencies", "topics") if not obj.get(f)]
    topics = obj.get("topics") or []
    if topics and (not isinstance(topics, list) or not all(isinstance(t, str) for t in topics) or len(topics) < 3):
        errors.append('"topics" must be a list of 3-7 topic names (strings)')
    if errors:
        return errors
    return _check_questions(subject, obj.get("questions"), len(topics), plan, hints)


def compact_to_content(obj: dict, plan: dict[str, int]) -> dict:
    """Компактный ответ модели -> внутренний формат пакета (тот же, что у курированных пакетов)."""
    topics = [{"name": n, "questions": []} for n in obj["topics"]]
    for q in obj["questions"]:
        item = {"q": q[2].strip(), "level": LV[q[1]], "kind": "concept"}
        if len(q) > 3 and str(q[3]).strip():
            item["answer_hint"] = str(q[3]).strip()
        topics[q[0]]["questions"].append(item)
    primary = next(iter(plan))
    return {"title": obj["title"], "domain": obj.get("domain"), "competencies": obj["competencies"], "topics": topics,
            "exercises": [{"level": primary, "title": str(e)} for e in (obj.get("exercises") or [])],
            "rubric": obj.get("rubric") or [], "recommendations": obj.get("recommendations") or []}


def interview_gap(pack: dict, level: str | None, count: int | None, hints: bool) -> dict | None:
    """Чего не хватает для ЭТОГО запроса. Экономно: число вопросов не указано — работаем с тем, что есть (метод добирает
    соседние уровни); дозаказ, только если нужного уровня почти нет или явно попросили больше, чем есть."""
    level = level if level in PREFERENCE else "mid"
    by_level = {}
    for t in pack["content"].get("topics", []):
        for q in t.get("questions", []):
            by_level.setdefault(q.get("level"), []).append(q)
    primary = by_level.get(level, [])
    usable = sum(len(by_level.get(lv, [])) for lv in PREFERENCE[level][:2])
    if count:
        need = max(3, min(int(count), 40))
        if len(primary) < min(need, max(3, need // 2)) or usable < need:
            return {"what": "level", "level": level, "n": max(5, min(need - len(primary), 20))}
    elif len(primary) < 3:
        return {"what": "level", "level": level, "n": 8}
    if hints:
        shown = primary[: (int(count) if count else 12)]
        if any(not q.get("answer_hint") for q in shown):
            return {"what": "hints", "level": level, "n": len(shown)}
    return None


def validate_pack(kind: str, subject: str, pack: dict, schema: dict | None = None) -> list[str]:
    """Общая проверка пакета ЛЮБОГО вида: обязательные поля по схеме навыка (или неоднозначность с вопросом)."""
    if pack.get("ambiguous"):
        return [] if str(pack.get("clarification", "")).strip() else ['"ambiguous" needs a "clarification" question']
    errors = []
    for field, typ in (schema or {}).items():
        v = pack.get(field)
        if v in (None, "", [], {}):
            errors.append(f'missing or empty "{field}"')
        elif "list" in str(typ) and not isinstance(v, list):
            errors.append(f'"{field}" must be a list')
    return errors


def ask_knowledge(llm: LLM, user: str, validate) -> dict:
    """Сначала БЫСТРАЯ модель (дёшево и в разы быстрее); если её ответ не прошёл проверку — один раз основная."""
    try:
        return llm.json(PACK_SYSTEM, user, validate=validate, purpose="knowledge", retries=1)
    except LLMError:
        return llm.json(PACK_SYSTEM, user, validate=validate, purpose="knowledge_main", retries=0)


class KnowledgePacks:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ чтение
    def _row(self, r: dict | None) -> dict | None:
        if not r:
            return None
        return {"id": r["id"], "kind": r["kind"], "key": r["key"], "title": r["title"], "domain": r["domain"],
                "specialization": r["specialization"], "aliases": loads(r["aliases"], []), "content": loads(r["content"], {}),
                "source": r["source"], "validation_status": r["validation_status"], "version": r["version"], "uses": r["uses"],
                "created_at": r["created_at"], "updated_at": r["updated_at"], "expires_at": r["expires_at"]}

    def get(self, kind: str, key: str) -> dict | None:
        return self._row(self.db.one("SELECT * FROM knowledge_packs WHERE kind=? AND key=?", (kind, key)))

    def list(self, kind: str | None = None) -> list[dict]:
        rows = self.db.query("SELECT * FROM knowledge_packs" + (" WHERE kind=?" if kind else "") + " ORDER BY kind, title",
                             (kind,) if kind else ())
        return [self._row(r) for r in rows]

    def summary(self) -> list[dict]:
        return [{k: p[k] for k in ("kind", "key", "title", "source", "validation_status", "version", "uses", "updated_at")}
                for p in self.list()]

    def index(self, kind: str) -> list[dict]:
        """Таксономия для распознавания (передаётся в parse_request): ключ, название и все синонимы."""
        return [{"key": p["key"], "title": p["title"], "aliases": sorted(set([p["title"]] + p["aliases"]), key=len, reverse=True)}
                for p in self.list(kind)]

    def match(self, kind: str, text: str) -> dict | None:
        """Распознавание по синонимам: побеждает САМЫЙ ДЛИННЫЙ совпавший синоним (VoIP Engineer > VoIP)."""
        t, best = norm(text), None
        for p in self.list(kind):
            for a in [p["title"], p["key"].replace("-", " ")] + p["aliases"]:
                a = norm(a)
                if a and re.search(r"(?<![\w])" + re.escape(a) + r"(?![\w])", t) and (best is None or len(a) > best[1]):
                    best = (p, len(a))
        return best[0] if best else None

    def touch(self, pack: dict) -> None:
        self.db.execute("UPDATE knowledge_packs SET uses=uses+1 WHERE id=?", (pack["id"],))

    # ------------------------------------------------------------------ запись
    def put(self, kind: str, key: str, title: str, content: dict, *, aliases=(), domain=None, specialization=None,
            source="user", validation_status="validated", ttl_days: int | None = None) -> dict:
        """Новый пакет или новая ВЕРСИЯ существующего (старое содержание заменяется, версия растёт)."""
        exp = (datetime.now(timezone.utc) + timedelta(days=ttl_days)).isoformat(timespec="seconds") if ttl_days else None
        old = self.get(kind, key)
        if old:
            al = sorted(set(old["aliases"]) | set(aliases))
            self.db.execute("UPDATE knowledge_packs SET title=?, aliases=?, content=?, source=?, validation_status=?, version=version+1,"
                            " updated_at=?, expires_at=? WHERE id=?",
                            (title, dumps(al), dumps(content), source, validation_status, now(), exp, old["id"]))
        else:
            self.db.execute("INSERT INTO knowledge_packs(kind,key,title,domain,specialization,aliases,content,source,validation_status,"
                            "version,uses,created_at,updated_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,1,0,?,?,?)",
                            (kind, key, title, domain, specialization, dumps(sorted(set(aliases))), dumps(content), source,
                             validation_status, now(), now(), exp))
        return self.get(kind, key)

    def seed(self) -> int:
        """Курированные пакеты (только если их ещё нет — правки пользователя и новые версии не перезаписываются)."""
        from .knowledge_seed import seed_packs
        added = 0
        for p in seed_packs():
            if not self.get(p["kind"], p["key"]):
                self.put(p["kind"], p["key"], p["title"], p["content"], aliases=p["aliases"], domain=p["domain"],
                         specialization=p["specialization"], source="curated", validation_status="curated")
                added += 1
        return added

    # ------------------------------------------------------------------ генерация ТОЛЬКО знаний (не органа)
    def generate(self, kind: str, subject: str, llm: LLM, *, spec: dict | None = None, context: dict | None = None,
                 level: str | None = None, count: int | None = None, hints: bool = False) -> dict:
        """Один вызов модели -> проверенный пакет -> сохранение. Возвращает пакет или {"ambiguous": True, "clarification": ...}."""
        if kind == "interview_role":
            plan = interview_plan(level, count)
            obj = ask_knowledge(llm, interview_prompt(subject, plan, hints), lambda o: validate_interview(subject, o, plan, hints))
            if obj.get("ambiguous"):
                return {"ambiguous": True, "clarification": obj.get("clarification")}
            content = compact_to_content(obj, plan)
        else:
            spec = {**KINDS.get(kind, {}), **(spec or {})}
            describe = spec.get("describe") or f"Knowledge needed by a skill for one value of '{spec.get('param', 'subject')}'."
            schema = spec.get("schema") or {}
            user = (f"KIND: {kind}\nSUBJECT (keep the user's meaning exactly): {subject}\nCONTEXT: "
                    f"{json.dumps(context or {}, ensure_ascii=False)}\nPACK FORMAT: {describe}\nREQUIRED FIELDS: {json.dumps(schema)}")
            obj = ask_knowledge(llm, user, lambda o: validate_pack(kind, subject, o, schema))
            if obj.get("ambiguous"):
                return {"ambiguous": True, "clarification": obj.get("clarification")}
            content = obj
        title = str(obj.get("title") or subject).strip()
        aliases = [a for a in (obj.get("aliases") or []) if isinstance(a, str)] + [subject, title]
        return self.put(kind, slug(title), title, content, aliases=aliases, domain=obj.get("domain"),
                        specialization=obj.get("specialization"), source=f"llm:{llm.model_for('knowledge')}",
                        validation_status="validated", ttl_days=180)

    def gap(self, kind: str, pack: dict, level: str | None, count: int | None, hints: bool) -> dict | None:
        return interview_gap(pack, level, count, hints) if kind == "interview_role" else None

    def extend_level(self, kind: str, pack: dict, level: str, n: int, llm: LLM, hints: bool = False) -> dict:
        """ДОЗАКАЗ: только n вопросов одного уровня к существующим темам пакета (маленький запрос, без остального пакета)."""
        c = pack["content"]
        names = [t["name"] for t in c["topics"]]
        have = [q["q"] for t in c["topics"] for q in t["questions"] if q.get("level") == level]
        item = '[topic_index, "j|m|s|l", "question", "key points, max 12 words"]' if hints else '[topic_index, "j|m|s|l", "question"]'
        user = (f'PROFESSION: "{pack["title"]}". TOPICS (index: name): {json.dumps(dict(enumerate(names)), ensure_ascii=False)}\n'
                f"ALREADY HAVE ({level}): {json.dumps(have, ensure_ascii=False)}\n"
                f'Give {n} NEW {level} questions ("{LV_CODE[level]}"), spread over the topics, as JSON {{"questions": [{item}, ...]}}. '
                "No generic IT filler.")
        plan = {level: n}
        obj = ask_knowledge(llm, user, lambda o: _check_questions(pack["title"], o.get("questions"), len(names), plan, hints, have))
        for q in obj["questions"]:
            item_ = {"q": q[2].strip(), "level": LV[q[1]], "kind": "concept"}
            if len(q) > 3 and str(q[3]).strip():
                item_["answer_hint"] = str(q[3]).strip()
            c["topics"][q[0]]["questions"].append(item_)
        return self.put(kind, pack["key"], pack["title"], c, source=pack["source"], validation_status=pack["validation_status"])

    def add_hints(self, kind: str, pack: dict, level: str, n: int, llm: LLM) -> dict:
        """ДОЗАКАЗ: ключевые пункты ответа только для вопросов, которые будут показаны (по просьбе «with answers»)."""
        c = pack["content"]
        targets = [q for t in c["topics"] for q in t["questions"] if q.get("level") == level and not q.get("answer_hint")][:n]
        if not targets:
            return pack
        user = (f'PROFESSION: "{pack["title"]}". For each question give the key points of a strong answer (max 15 words each).\n'
                f"QUESTIONS: {json.dumps([q['q'] for q in targets], ensure_ascii=False)}\n"
                'JSON: {"hints": ["...", ...]} - same order, same count.')
        obj = ask_knowledge(llm, user, lambda o: [] if isinstance(o.get("hints"), list) and len(o["hints"]) == len(targets)
                            else [f'"hints" must be a list of exactly {len(targets)} strings'])
        for q, h in zip(targets, obj["hints"]):
            q["answer_hint"] = str(h).strip()
        return self.put(kind, pack["key"], pack["title"], c, source=pack["source"], validation_status=pack["validation_status"])
