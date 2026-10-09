"""ДВИЖОК НАВЫКОВ — главный механизм экономии токенов.

Идея: навык учится ОДИН раз (генерация + тесты), а дальше исполняется много раз без пересборки.
Для этого «вызываемый» навык несёт в своём коде детерминированный разбор запроса (parse_request) и
форматирование ответа (format_result) — их тоже написала модель, и они тоже покрыты тестами.

Строгое разделение (каждый запрос — свой контекст исполнения):
  навык        — КАК делать (код в реестре, версия);           живёт постоянно
  параметры    — ЧТО просили СЕЙЧАС (города, роль, даты);      извлекаются из ТЕКУЩЕГО текста, прошлые не наследуются,
                                                               кроме явной ссылки («те же города» -> context["previous"])
  результат    — ответ на ЭТОТ запрос;                          строится только из текущего исполнения
  кэш          — данные по КЛЮЧУ (навык + версия + параметры + язык); у каждого города свой ключ и свой срок годности

Порядок выбора стратегии (от дешёвой к дорогой):
  MODE 1 deterministic — навык найден по метаданным (без LLM), его parse_request разобрал запрос (один или НЕСКОЛЬКО
                         наборов параметров — «Киев и Прага»), run() выполнился в песочнице. 0 вызовов модели.
         cache         — свежий результат для ТЕХ ЖЕ параметров уже есть в памяти — даже песочница не нужна.
  MODE 2 light         — навык явно подходит, но его разбор не справился с формулировкой: БЫСТРАЯ модель
                         извлекает только параметры (маленький промпт).
  MODE 3 partial       — навык объявил LLM-слоты: модель заполняет ТОЛЬКО их (по параметрам, с кэшем).
  иначе                — None, задача идёт в полный планировщик (MODE 4) или в команду (MODE 5).

Проверка ответа (без LLM): если навык с РАЗНЫМИ параметрами выдаёт ОДИНАКОВЫЙ ответ (найденная ошибка: «кибербезопасность»
и «UX/UI» получили один и тот же текст), навык не обобщён — он «выучил ответ». Это поломка: ограниченный ремонт
(мутация) принимается, только если новый код проходит проверку зависимости ответа от параметров (probe).

Сбои при исполнении:
  ValueError                         -> честное ограничение («прогноз на 40 дней недоступен»), навык не трогаем;
  ConnectionError/таймаут/HTTP 5xx    -> временный сбой провайдера: один повтор, затем честное сообщение;
  остальное (KeyError, TypeError…)   -> навык сломан: ограниченный ремонт (мутация с тестами), новая версия,
                                        старая остаётся для отката. Монстр целиком НЕ пересобирается.
"""
import contextvars
import difflib
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import config, skillmeta
from .db import loads
from .events import EventBus
from .knowledge import Knowledge, make_key
from .knowledge_packs import KnowledgePacks
from .llm import LLM, LLMError
from .registry import Registry
from .sandbox import Sandbox
from .usage import Ledger

TEMPORARY_ERRORS = {"ConnectionError", "TimeoutError", "URLError", "timeout", "RemoteDisconnected", "ConnectionResetError",
                    "ConnectionRefusedError", "IncompleteRead", "SSLError", "gaierror"}
LEARN_RE = re.compile(r"\b(learn|teach|naučit|nauč|научи|выучи)\w*", re.I)
# явная ссылка на прошлый запрос — только тогда навык получает прошлые параметры
REFERENCE_RE = re.compile(r"\b(same|those|these|them|previous|stejn\w*|těch(to)?|тех же|те же|там же|этих|этим|тем же)\b", re.I)
MAX_ITEMS = 10                 # не больше 10 городов/сущностей в одном запросе
PARALLEL = 4                   # параллельных исполнений навыка (вежливо к бесплатным API)

SLOT_SYSTEM = ("You fill ONE slot of an existing, already-built skill. Answer only what the slot asks, concisely and "
               "factually, specifically for the given parameter values; if you are unsure about current facts, say so inside "
               'the value. Answer JSON: {"value": <the requested content>}')
ROUTE_SYSTEM = ("You map a user request onto ONE existing skill. Extract its input parameters exactly as the skill's inputs "
                "describe (use absolute ISO dates computed from TODAY), ONLY from this request's text. If the request names "
                "several entities of the same kind (cities, roles...), return a list with one parameter object per entity. "
                "If the skill cannot serve the request at all, say so. "
                'Answer JSON: {"applicable": true|false, "params": {...} | [{...}, ...], "reason": "short"}')


def _related_count(request: set[str], vocabulary: set[str]) -> int:
    """Count exact and very close words so a small typo does not trigger a slow LLM plan."""
    exact = request & vocabulary
    long_vocabulary = {word for word in vocabulary if len(word) >= 5}
    fuzzy = sum(1 for word in request - exact
                if len(word) >= 5 and difflib.get_close_matches(word, long_vocabulary, n=1, cutoff=0.88))
    return len(exact) + fuzzy


def _normalise_skill_words(text: str, cap: dict) -> str:
    """Repair only words that are almost identical to this skill's declared keywords."""
    keywords = {word.lower() for phrase in (cap.get("skill") or {}).get("keywords", [])
                for word in re.findall(r"(?u)\b[^\W\d_]{5,}\b", phrase)}
    if not keywords:
        return text

    def replace(match: re.Match) -> str:
        word = match.group(0)
        close = difflib.get_close_matches(word.lower(), keywords, n=1, cutoff=0.88)
        return close[0] if close else word

    return re.sub(r"(?u)\b[^\W\d_]{5,}\b", replace, text)


@dataclass
class SkillRun:
    ok: bool
    skill: str
    version: str = ""
    mode: str = "deterministic"
    params: dict | list = field(default_factory=dict)
    answer: str | None = None
    output: dict | None = None
    files: list[str] = field(default_factory=list)
    limitation: str | None = None
    llm_calls: int = 0
    cache: str | None = None
    error: str | None = None
    repaired: bool = False
    broken: bool = False         # структурная поломка, ремонт ещё не делали (параллельный запуск)
    knowledge: dict | None = None    # пакет знаний: {kind, key, version, source: loaded|generated}
    quality: list | None = None      # проблемы, найденные проверкой качества навыка (пусто = ок)
    decision: dict | None = None     # нужна дорогая операция — решение барьера (ждём подтверждения пользователя)
    needs_input: dict | None = None  # навык узнал запрос, но не хватает значения: {"skill", "missing", "params", "question"}
    offer: dict | None = None        # платное улучшение по желанию (исследование Sokosumi), ответ при этом уже дан
    research: dict | None = None     # {"started": bool, "available": bool} — свежие знания через Sokosumi


def _fill(template: str, params: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(params.get(m.group(1), "")), template)


def _norm_answer(s) -> str:
    return " ".join(str(s or "").lower().split())


def _label(params: dict) -> str:
    """Короткое имя сущности для сообщений («Kyiv»)."""
    for v in params.values():
        if isinstance(v, str) and v.strip():
            return v.strip()
    return json.dumps(params, ensure_ascii=False)[:60]


class SkillEngine:
    def __init__(self, llm: LLM, sandbox: Sandbox, registry: Registry, knowledge: Knowledge, bus: EventBus,
                 ledger: Ledger, evolution, packs: KnowledgePacks | None = None):
        self.llm, self.sandbox, self.registry, self.knowledge, self.bus = llm, sandbox, registry, knowledge, bus
        self.ledger, self.evolution = ledger, evolution
        self.packs = packs or KnowledgePacks(registry.db)
        self.allow_build = False         # пользователь подтвердил дорогую операцию для ТЕКУЩЕЙ задачи
        self.researcher = None           # app.researcher.Researcher — свежие знания через Sokosumi (подключает Brain)
        self._text = ""

    # ------------------------------------------------------------------ поиск без LLM
    def allowed(self, cap: dict, monster: dict | None) -> bool:
        """Права: приватный навык — только владельцу; сетевой — только если сеть разрешена оператором."""
        if cap.get("network") and not config.ALLOW_NETWORK_CAPABILITIES:
            return False
        if cap.get("visibility") == "private":
            return bool(monster) and cap.get("owner_monster_id") == monster["id"]
        return True

    def candidates(self, text: str, monster: dict | None = None) -> list[tuple[dict, float]]:
        """Ранжирование по метаданным навыка (ключевые слова ×2, намерения, примеры, имя, назначение). Без LLM."""
        req = skillmeta.tokens(text)
        owned = {c["name"] for c in (monster or {}).get("capabilities", [])}
        out = []
        for cap in self.registry.callable_skills():
            if not self.allowed(cap, monster):
                continue
            sk = cap["skill"]
            kw = skillmeta.tokens(" ".join(sk.get("keywords", [])))
            words = kw | skillmeta.tokens(" ".join(sk.get("intents", []) + sk.get("examples", []) + [cap["name"].replace("_", " "), cap["purpose_en"]]))
            score = _related_count(req, words) + _related_count(req, kw)
            if score > 0:
                out.append((cap, score + (1 if cap["name"] in owned else 0)))
        return sorted(out, key=lambda x: -x[1])

    # ------------------------------------------------------------------ главный вход
    def try_handle(self, text: str, lang: str, task_id: int, monster: dict | None, files: list[Path],
                   allow_build: bool = False) -> SkillRun | None:
        self.allow_build = allow_build
        self._text = text
        emit = lambda t, **kw: self.bus.emit(t, task_id=task_id, **kw)   # noqa: E731
        ctx = {"today": date.today().isoformat(), "lang": lang}
        cands = self.candidates(text, monster)[:3]
        # ОТВЕТ НА ВОПРОС МОНСТРА: прошлый ответ был «Для какого города?» — короткое сообщение («Prague») и есть недостающее
        # значение. Не принимаем за ответ новый запрос к навыку (в нём есть ключевые слова какого-либо навыка) и длинный текст.
        pending = self._pending_input(monster)
        req = skillmeta.tokens(text)
        new_request = any(_related_count(req, skillmeta.tokens(" ".join((c.get("skill") or {}).get("keywords", []))))
                          for c, _ in cands)
        if pending and len(text) <= 60 and not new_request:
            cap = self.registry.get(pending["skill"])
            if cap and cap.get("status") == "active" and self.allowed(cap, monster):
                value = text.strip().strip(" .!?\"'")
                emit("SKILL_INPUT_RECEIVED", capability=cap["name"], missing=pending["missing"], value=value[:80])
                return self._dispatch(cap, {**pending["params"], pending["missing"]: value}, ctx, text, task_id, monster, files,
                                      "deterministic")
        emit("SKILL_SEARCH", candidates=[{"skill": c["name"], "score": sc} for c, sc in cands])
        if not cands:
            emit("SKILL_NO_MATCH", reason="no installed skill matches this request")
            return None
        referenced = bool(REFERENCE_RE.search(text))
        # MODE 1: собственный разбор навыка (детерминированный код в песочнице)
        for cap, _ in cands:
            pctx = dict(ctx)
            kspec = (cap.get("skill") or {}).get("knowledge")
            if isinstance(kspec, dict) and kspec.get("kind"):
                # таксономия из пакетов знаний — навык распознаёт роль/тему по синонимам без LLM
                pctx["knowledge_index"] = self.packs.index(kspec["kind"])
            if referenced:
                prev = self._previous_params(cap["name"], monster)
                if prev is not None:
                    pctx["previous"] = prev
                    emit("CONTEXT_REFERENCE", capability=cap["name"], previous=prev)
            parse_text = _normalise_skill_words(text, cap)
            res = self._invoke(cap, monster, {"text": parse_text, "context": pctx, "steps": ["parse"]}, files, network=False)
            rep = res.report or {}
            if rep.get("ok") and isinstance(rep.get("params"), dict) and rep["params"].get("_missing"):
                return self._ask(cap, rep["params"], task_id, lang)          # спросить, а не платить за догадку
            if rep.get("ok") and rep.get("params") is not None:
                return self._dispatch(cap, rep["params"], ctx, text, task_id, monster, files, "deterministic")
        # MODE 2: навык явно подходит, но формулировка нестандартная — быстрая модель извлекает только параметры
        top, score = cands[0]
        # платим за маршрутизацию, только если в запросе есть КЛЮЧЕВОЕ слово навыка (а не случайное слово из примеров)
        kw_hit = _related_count(skillmeta.tokens(text),
                                skillmeta.tokens(" ".join((top.get("skill") or {}).get("keywords", []))))
        if score >= 2 and kw_hit and self.llm.configured():
            try:
                routed = self.llm.json(ROUTE_SYSTEM, self._route_prompt(top, text, ctx), purpose="routing",
                                       validate=lambda o: [] if isinstance(o.get("applicable"), bool) else ['"applicable" must be true/false'])
            except LLMError:
                routed = {"applicable": False}
            if routed.get("applicable") and isinstance(routed.get("params"), (dict, list)):
                run = self._dispatch(top, routed["params"], ctx, text, task_id, monster, files, "light")
                run.llm_calls += 1
                return run
        emit("SKILL_NO_MATCH", reason="installed skills cannot interpret this request")
        return None

    def _freshness(self, pack: dict, task_id: int, lang: str) -> dict:
        """Пользователь просит АКТУАЛЬНОЕ: отвечаем сразу из пакета, честно пишем дату знаний и (если можно) предлагаем
        или запускаем исследование Sokosumi. Ничего платного без подтверждения."""
        r = self.researcher
        if not r or not r.should_offer(self._text) or r.is_fresh(pack):
            return {}
        day = (r.researched_at(pack) or pack.get("updated_at") or "")[:10]
        cs = lang == "cs"
        if not r.available():
            note = (f"Znalosti k {day}, z paměti modelu — živý průzkum není nastaven (SOKOSUMI_API_KEY / SOKOSUMI_RESEARCH_AGENT_ID)."
                    if cs else f"Knowledge as of {day}, from model knowledge — live research is not configured "
                               "(SOKOSUMI_API_KEY / SOKOSUMI_RESEARCH_AGENT_ID).")
            return {"note": note, "research": {"available": False, "started": False}}
        if self.allow_build:
            started = r.start(pack, task_id)
            note = ((f"Výzkumník Sokosumi právě ověřuje aktuální zdroje; znalosti se samy aktualizují (tato odpověď je z balíčku k {day})."
                     if cs else f"A Sokosumi research agent is checking current sources now; the knowledge will update automatically "
                                f"(this answer uses the pack as of {day}).") if started else
                    (f"Znalosti k {day}. Průzkum nelze spustit (běží nebo limit kreditů)." if cs else
                     f"Knowledge as of {day}. Research could not start (already running or credit limits)."))
            return {"note": note, "research": {"available": True, "started": started}}
        offer = r.offer(pack, task_id)
        note = ((f"Znalosti k {day}, z paměti modelu. Pro aktuální data ze skutečných zdrojů můžeš najmout výzkumníka Sokosumi (níže)."
                 if cs else f"Knowledge as of {day}, from model knowledge. For current data from real sources you can hire a "
                            "Sokosumi research agent (below).") if offer else
                (f"Znalosti k {day}. Živý průzkum přeskočen (limit kreditů nebo už běží)." if cs else
                 f"Knowledge as of {day}. Live research skipped (credit limits or already running)."))
        return {"note": note, "offer": offer, "research": {"available": True, "started": False}}

    @staticmethod
    def _with_extra(run: SkillRun, extra: dict) -> SkillRun:
        if extra.get("note") and run.answer:
            run.answer = f"{run.answer}\n\n> ℹ {extra['note']}"
        run.offer, run.research = extra.get("offer"), extra.get("research")
        return run

    def _ask(self, cap: dict, params: dict, task_id: int, lang: str) -> SkillRun:
        """Навык узнал запрос, но обязательного значения нет: спрашиваем пользователя (0 вызовов модели)."""
        missing = str(params["_missing"])
        q = params.get("_question") or ({"cs": f"Upřesni prosím: {missing}?", "en": f"Which {missing}?"}.get(lang, f"Which {missing}?"))
        kept = {k: v for k, v in params.items() if not str(k).startswith("_")}
        self.bus.emit("SKILL_NEEDS_INPUT", task_id=task_id, capability=cap["name"], missing=missing, question=q)
        run = SkillRun(True, cap["name"], cap["version"], "ask", kept, str(q), None, [], str(q), 0)
        run.needs_input = {"skill": cap["name"], "missing": missing, "params": kept, "question": q}
        return run

    def _pending_input(self, monster: dict | None) -> dict | None:
        """Последняя задача ЭТОГО монстра закончилась вопросом — ждём ответ (только сразу следующим сообщением)."""
        if not monster:
            return None
        row = self.registry.db.one("SELECT result FROM tasks WHERE monster_id=? AND status='completed' ORDER BY id DESC LIMIT 1",
                                   (monster["id"],))
        data = ((loads(row["result"], {}) or {}).get("data") or {}) if row else {}
        ni = data.get("needs_input") if isinstance(data, dict) else None
        return ni if isinstance(ni, dict) and ni.get("skill") and ni.get("missing") else None

    def _expected(self, purposes: tuple[str, ...]) -> dict | None:
        """Сколько обычно стоит такой вызов — по ИЗМЕРЕННЫМ прошлым вызовам (оценка до запуска; без замеров — None)."""
        q = ",".join("?" * len(purposes))
        r = self.registry.db.one(f"SELECT COUNT(*) n, AVG(prompt_tokens+completion_tokens) tok, AVG(seconds) sec FROM usage"
                                 f" WHERE kind='llm' AND purpose IN ({q})", purposes)
        return {"tokens": round(r["tok"]), "seconds": round(r["sec"], 1), "basis": f"average of {r['n']} measured call(s)"} if r["n"] else None

    def _route_prompt(self, cap: dict, text: str, ctx: dict) -> str:
        sk = cap["skill"]
        return (f"TODAY: {ctx['today']}\nSKILL: {cap['name']} — {cap['purpose_en']}\nINPUTS: {cap['inputs']}\n"
                f"LIMITS: {sk.get('limits')}\nEXAMPLES: {sk.get('examples')}\nREQUEST: {text}")

    def _previous_params(self, name: str, monster: dict | None):
        """Параметры последнего успешного исполнения этого навыка (этим монстром) — только для явной ссылки."""
        rows = self.registry.db.query(
            "SELECT strategy, monster_id FROM tasks WHERE status='completed' AND strategy IS NOT NULL ORDER BY id DESC LIMIT 40")
        for r in rows:
            st = loads(r["strategy"], {}) or {}
            if st.get("skill") == name and (not monster or r["monster_id"] == monster["id"]):
                return st.get("params")
        return None

    # ------------------------------------------------------------------ один или несколько наборов параметров
    def _dispatch(self, cap: dict, params, ctx: dict, text: str, task_id: int, monster: dict | None, files: list[Path],
                  mode: str, _checked: bool = False) -> SkillRun:
        items = [p for p in params if isinstance(p, dict)][:MAX_ITEMS] if isinstance(params, list) else [params]
        if len(items) > 1:
            run = self._execute_many(cap, items, ctx, task_id, monster, files, mode)
        else:
            run = self._execute(cap, items[0] if items else {}, ctx, task_id, monster, files, mode)
        if _checked or not run.ok:
            return run
        # ПРОВЕРКА ОТВЕТА: разные параметры -> одинаковый ответ = навык не обобщён («выучил ответ»)
        if (cap.get("skill") or {}).get("knowledge"):
            return run                      # содержание приходит из пакетов знаний — код органа не «запоминает» ответы
        clash = self._not_parameterized(cap["name"], run)
        if clash:
            if not self.allow_build:
                # БАРЬЕР: переписывать код органа долго и дорого — не делаем этого молча из-за смены параметра
                self.bus.emit("SKILL_NOT_PARAMETERIZED", task_id=task_id, capability=cap["name"], params=clash["params"],
                              other_params=clash["other_params"], other_request=clash["other_request"])
                run.ok = False
                run.decision = {
                    "decision": "confirm", "action": "repair_skill", "selected_skill": cap["name"],
                    "missing_functionality": None, "missing_knowledge": None,
                    "reasoning_summary": (f"'{cap['name']}' gave the same answer for different requests "
                                          f"({clash['other_params']} vs {clash['params']}). Its code stores one answer instead of a "
                                          "method; fixing it means regenerating the organ once."),
                    "user_confirmation_required": True}
                run.error = run.decision["reasoning_summary"]
                return run
            return self._repair_generalization(cap, clash, text, ctx, task_id, monster, files, mode, run)
        return run

    def _execute_many(self, cap: dict, items: list[dict], ctx: dict, task_id: int, monster: dict | None,
                      files: list[Path], mode: str) -> SkillRun:
        """«Погода в Киеве и Праге»: каждый город — отдельное исполнение навыка (параллельно, свой кэш, своя ошибка)."""
        self.bus.emit("SKILL_FANOUT", task_id=task_id, capability=cap["name"], count=len(items),
                      items=[_label({k: v for k, v in p.items() if not str(k).startswith("_")}) for p in items])

        def one(p):
            return self._execute(cap, p, ctx, task_id, monster, files, mode, repair=False)
        with ThreadPoolExecutor(max_workers=min(PARALLEL, len(items))) as pool:
            # copy_context: учёт расхода (ContextVar текущей задачи) и отмена работают и в потоках
            runs = list(pool.map(lambda p: contextvars.copy_context().run(one, p), items))
        broken = [i for i, r in enumerate(runs) if r.broken]
        if broken and self.llm.configured():
            # ремонт навыка — ОДИН раз на запрос, затем повтор только сломанных элементов
            first = runs[broken[0]]
            out = self.evolution.mutate(cap["name"], reason=f"runtime failure for params {first.params}: {first.error}",
                                        task_id=task_id)
            if out.accepted:
                fresh = self.registry.get(cap["name"])
                for i in broken:
                    runs[i] = self._execute(fresh, items[i], ctx, task_id, monster, files, mode, repair=False)
                    runs[i].repaired = True
        return self._combine(cap, items, runs)

    def _combine(self, cap: dict, items: list[dict], runs: list[SkillRun]) -> SkillRun:
        ok = [r for r in runs if r.ok]
        parts = []
        for p, r in zip(items, runs):
            if r.ok:
                parts.append(r.answer or "")
            else:
                parts.append(f"### {_label(r.params or p)}\n\n⚠ {r.error}")
        modes = {r.mode for r in ok}
        mode = "cache" if modes == {"cache"} else "partial" if "partial" in modes else "light" if "light" in modes else "deterministic"
        limitations = [r.limitation for r in ok if r.limitation]
        return SkillRun(
            ok=bool(ok), skill=cap["name"], version=(ok or runs)[0].version, mode=mode, params=[r.params for r in runs],
            answer="\n\n".join(parts), files=[f for r in runs for f in r.files],
            output={"items": [{"params": r.params, "ok": r.ok, "answer": r.answer, "output": r.output, "error": r.error,
                               "limitation": r.limitation, "mode": r.mode} for r in runs]},
            limitation="; ".join(limitations) if limitations and len(limitations) == len(ok) else None,
            llm_calls=sum(r.llm_calls for r in runs), cache="hit" if mode == "cache" else None,
            error=None if ok else "; ".join(f"{_label(r.params or {})}: {r.error}" for r in runs),
            repaired=any(r.repaired for r in runs),
            knowledge=next((r.knowledge for r in runs if r.knowledge), None),
            quality=[q for r in runs for q in (r.quality or [])])

    # ------------------------------------------------------------------ исполнение с памятью и слотами
    def _execute(self, cap: dict, params: dict, ctx: dict, task_id: int, monster: dict | None, files: list[Path],
                 mode: str, retry: bool = True, repair: bool = True) -> SkillRun:
        emit = lambda t, **kw: self.bus.emit(t, task_id=task_id, capability=cap["name"], **kw)   # noqa: E731
        name, sk = cap["name"], cap["skill"]
        version = self._version_for(cap, monster)
        refresh = set(params.get("_refresh") or [])
        wanted_slots = params.get("_slots")
        clean = {k: v for k, v in params.items() if not str(k).startswith("_")}
        emit("SKILL_MATCHED", version=version, params=clean, mode=mode)
        slots_meta = sk.get("llm_slots") or {}
        if isinstance(wanted_slots, list):
            slots_meta = {k: v for k, v in slots_meta.items() if k in wanted_slots}
        ttl = sk.get("freshness_seconds") or (900 if cap.get("network") else None)

        # ПАКЕТ ЗНАНИЙ (Knowledge Cache): новая профессия/тема = новые ДАННЫЕ для того же органа, а не новый орган
        knowledge, kinfo, llm_calls, extra = None, None, 0, {}
        kspec = sk.get("knowledge") if isinstance(sk.get("knowledge"), dict) else None
        if kspec and kspec.get("kind"):
            kind, param, key_param = kspec["kind"], kspec.get("param"), kspec.get("key_param")
            subject = str(clean.get(param) or "").strip()
            # что именно нужно ЭТОМУ запросу (уровень, количество, подробность) — генерируем только это
            want_level = clean.get(kspec.get("level_param") or "") if kspec.get("level_param") else None
            want_count = clean.get(kspec.get("count_param") or "") if kspec.get("count_param") else None
            want_hints = bool(clean.get(kspec.get("detail_param") or "")) if kspec.get("detail_param") else False
            pack = self.packs.get(kind, clean[key_param]) if key_param and clean.get(key_param) else None
            pack = pack or (self.packs.match(kind, subject) if subject else None)
            source = "loaded"
            if pack is None:
                if not subject:
                    return SkillRun(True, name, version, mode, clean, f"### ?\n\nPlease tell me the {param or 'subject'}.", None, [],
                                    f"missing {param}", 0)
                emit("KNOWLEDGE_PACK_MISSING", kind=kind, subject=subject)
                if not self.llm.configured():
                    msg = f"I have no knowledge about '{subject}' yet and the brain is not configured to learn it."
                    return SkillRun(True, name, version, mode, clean, f"### {subject}\n\n{msg}", None, [], msg, 0)
                emit("KNOWLEDGE_GENERATING", kind=kind, subject=subject, expected=self._expected(("knowledge", "knowledge_main")))
                t0 = time.time()
                try:
                    pack = self.packs.generate(kind, subject, self.llm, spec=kspec, context={"params": clean},
                                               level=want_level, count=want_count, hints=want_hints)
                except LLMError as exc:
                    return SkillRun(False, name, version, mode, clean, error=f"could not prepare knowledge about '{subject}': {exc}")
                llm_calls += 1
                if pack.get("ambiguous"):
                    q = pack.get("clarification") or f"What exactly do you mean by '{subject}'?"
                    emit("KNOWLEDGE_CLARIFICATION", subject=subject, question=q)
                    return SkillRun(True, name, version, "smart", clean, f"### {subject}\n\n{q}", None, [], q, llm_calls)
                emit("KNOWLEDGE_PACK_SAVED", kind=kind, key=pack["key"], title=pack["title"], version=pack["version"],
                     seconds=round(time.time() - t0, 1))
                source, mode = "generated", "smart"
            else:
                emit("KNOWLEDGE_PACK_LOADED", kind=kind, key=pack["key"], title=pack["title"], version=pack["version"],
                     source=pack["source"])
            # ДОЗАКАЗ: в пакете нет вопросов нужного уровня / подсказок — маленький запрос только за недостающим
            for _ in range(2):
                gap = self.packs.gap(kind, pack, want_level, want_count, want_hints)
                if not gap or not self.llm.configured():
                    break
                emit("KNOWLEDGE_EXTENDING", kind=kind, key=pack["key"], title=pack["title"], what=gap["what"], level=gap["level"], n=gap["n"])
                t1 = time.time()
                try:
                    pack = (self.packs.extend_level(kind, pack, gap["level"], gap["n"], self.llm, hints=want_hints) if gap["what"] == "level"
                            else self.packs.add_hints(kind, pack, gap["level"], gap["n"], self.llm))
                except LLMError:
                    break                                    # не вышло — честно работаем с тем, что есть (метод сообщит о нехватке)
                llm_calls += 1
                mode = "smart"
                source = "generated" if source == "generated" else "extended"
                emit("KNOWLEDGE_PACK_SAVED", kind=kind, key=pack["key"], title=pack["title"], version=pack["version"],
                     seconds=round(time.time() - t1, 1), extended=gap["what"])
            extra = self._freshness(pack, task_id, ctx["lang"])
            self.packs.touch(pack)
            if key_param:
                clean[key_param] = pack["key"]
            knowledge = {"key": pack["key"], "version": pack["version"], "source": pack["source"], **pack["content"],
                         "title": pack["title"]}
            kinfo = {"kind": kind, "key": pack["key"], "title": pack["title"], "version": pack["version"], "source": source}

        # ключ кэша = навык + ВЕРСИЯ + ВСЕ параметры + язык + ВЕРСИЯ ПАКЕТА: разные города/роли никогда не совпадут
        key = make_key("skill", name, version, clean, ctx["lang"], *((kinfo["key"], kinfo["version"]) if kinfo else ()))

        # свежий результат для ЭТИХ ЖЕ параметров уже есть — даже песочница не нужна
        if not slots_meta and "all" not in refresh:
            rec = self.knowledge.get(key)
            if rec and rec["fresh"]:
                self.knowledge.hit(key)
                emit("KNOWLEDGE_CACHE_HIT", age_seconds=rec["age_seconds"], expires_at=rec["expires_at"], params=clean)
                c = rec["content"]
                return self._with_extra(SkillRun(True, name, version, "cache", clean, c.get("answer"), c.get("output"), [], None,
                                                 llm_calls, "hit", knowledge=kinfo), extra)
            if rec:
                emit("KNOWLEDGE_STALE", age_seconds=rec["age_seconds"], params=clean)

        # MODE 3: LLM-слоты — модель заполняет только то, что требует рассуждения, свежие слоты берутся из памяти
        slots = None if not (sk.get("llm_slots")) else {}
        for slot, spec in slots_meta.items():
            skey = make_key("slot", name, slot, clean, ctx["lang"])
            rec = self.knowledge.get(skey)
            if rec and rec["fresh"] and slot not in refresh and "all" not in refresh:
                self.knowledge.hit(skey)
                slots[slot] = rec["content"]
                emit("KNOWLEDGE_CACHE_HIT", slot=slot, age_seconds=rec["age_seconds"])
                continue
            why = "requested" if (slot in refresh or "all" in refresh) else ("expired" if rec else "new")
            emit("SLOT_FILLING", slot=slot, reason=why)
            value = self.llm.json(SLOT_SYSTEM, f"LANGUAGE: {ctx['lang']}\nTODAY: {ctx['today']}\nPARAMETERS: "
                                  f"{json.dumps(clean, ensure_ascii=False)}\n" + _fill(spec["prompt"], clean),
                                  validate=lambda o: [] if "value" in o else ['missing "value"'], purpose="slot")
            llm_calls += 1
            slots[slot] = value["value"]
            fresh = spec.get("freshness_seconds")
            self.knowledge.put(skey, category="dynamic" if fresh else "stable", skill=f"{name}.{slot}", params=clean,
                               content=value["value"], source=f"llm:{self.llm.model_for('slot')}",
                               ttl_seconds=int(fresh) if isinstance(fresh, (int, float)) and fresh > 0 else None)
        if llm_calls and mode != "smart":
            mode = "partial"

        # исполнение кода навыка (песочница) — с параметрами ЭТОГО запроса
        emit("SKILL_EXECUTING", network=bool(cap.get("network")), params=clean)
        t0 = time.time()
        res = self._invoke(cap, monster, {"params": clean, "context": ctx, "steps": ["run", "format", "check"], "slots": slots,
                                          "knowledge": knowledge},
                           files, network=bool(cap.get("network")), artifacts=config.ARTIFACTS_DIR / str(task_id))
        rep = res.report or {}
        if cap.get("network"):
            self.ledger.record("tool", provider=name, model=version, seconds=round(time.time() - t0, 2))   # не-LLM расход отдельно
        honest_refusal = rep.get("error_type") == "ValueError"     # навык по контракту отказал на неподдерживаемом вводе — это не поломка
        for c in rep.get("calls", []):
            self.registry.record_run(c["capability"], version=None, task_id=task_id, input_hash=c.get("input_hash", ""),
                                     success=bool(c.get("ok")) or (honest_refusal and c["capability"] == name),
                                     error=c.get("error"), duration=c.get("duration", 0))
        if rep.get("ok"):
            answer = rep.get("answer") or _plain(rep.get("output"))
            if not slots_meta:
                self.knowledge.put(key, category="dynamic" if ttl else "stable", skill=name, params=clean,
                                   content={"answer": answer, "output": rep.get("output")}, source=f"{name} v{version}",
                                   ttl_seconds=ttl)
            problems = rep.get("problems") or []
            if "problems" in rep:
                emit("QUALITY_CHECK", ok=not problems, problems=problems[:5])
            emit("SKILL_EXECUTED", mode=mode, llm_calls=llm_calls, files=res.out_files, params=clean)
            return self._with_extra(SkillRun(True, name, version, mode, clean, answer, rep.get("output"), res.out_files, None, llm_calls,
                                             "refreshed" if ttl else None, knowledge=kinfo, quality=problems), extra)

        etype, err = rep.get("error_type") or "", rep.get("error") or res.stderr[-400:] or f"sandbox: {res.killed_reason}"
        if etype == "ValueError":
            # честное ограничение навыка — это НЕ поломка
            msg = err.split(": ", 1)[-1]
            emit("SKILL_LIMITATION", message=msg[:300], params=clean)
            return SkillRun(True, name, version, mode, clean, f"### {_label(clean)}\n\n{msg}", None, [], msg, llm_calls)
        status = rep.get("http_status")
        if etype in TEMPORARY_ERRORS or (isinstance(status, int) and (status >= 500 or status == 429)) or res.killed_reason == "timeout":
            if retry:
                emit("SKILL_RETRY", error=err[:200])
                time.sleep(1.5)
                return self._execute(cap, {**params}, ctx, task_id, monster, files, mode, retry=False, repair=repair)
            emit("SKILL_PROVIDER_UNAVAILABLE", error=err[:300], params=clean)
            return SkillRun(False, name, version, mode, clean, error=f"the data provider of '{name}' is unavailable right now ({err[:200]}). "
                                                                       "The skill itself is fine; try again later.")
        # структурная поломка — ограниченный ремонт ТОЛЬКО этого навыка
        emit("SKILL_BROKEN", error=err[:300], params=clean)
        if repair and self.llm.configured():
            out = self.evolution.mutate(name, reason=f"runtime failure for params {clean}: {(rep.get('traceback') or err)[-1500:]}",
                                        task_id=task_id)
            if out.accepted:
                fresh_cap = self.registry.get(name)
                run = self._execute(fresh_cap, params, ctx, task_id, monster, files, mode, retry=False, repair=False)
                run.repaired, run.llm_calls = True, run.llm_calls + llm_calls + 1
                return run
        return SkillRun(False, name, version, mode, clean, error=f"skill '{name}' failed: {err[:300]}", broken=not repair)

    # ------------------------------------------------------------------ навык «выучил ответ» вместо процедуры
    def _past_answers(self, name: str, version: str) -> list[tuple[dict, str, str]]:
        """(параметры, ответ, текст запроса) прошлых исполнений этого навыка той же версии."""
        out = []
        for r in self.registry.db.query(
                "SELECT input, strategy, result FROM tasks WHERE status='completed' AND strategy IS NOT NULL ORDER BY id DESC LIMIT 60"):
            st = loads(r["strategy"], {}) or {}
            if st.get("skill") != name or st.get("version") != version or st.get("limitation"):
                continue
            res = loads(r["result"], {}) or {}
            items = ((res.get("data") or {}).get("output") or {}).get("items") if isinstance((res.get("data") or {}).get("output"), dict) else None
            if isinstance(items, list) and isinstance(st.get("params"), list):
                out += [(it.get("params") or {}, it.get("answer") or "", r["input"]) for it in items if it.get("ok") and not it.get("limitation")]
            else:
                summ = res.get("summary")
                out.append((st.get("params") or {}, (summ or {}).get("en", "") if isinstance(summ, dict) else str(summ or ""), r["input"]))
        return out

    def _not_parameterized(self, name: str, run: SkillRun) -> dict | None:
        """Ищем пару: разные параметры -> одинаковый (непустой) ответ. Внутри запроса и с прошлыми запросами."""
        now = []
        if isinstance(run.params, list):
            for it in (run.output or {}).get("items", []):
                if it.get("ok") and not it.get("limitation") and it.get("answer"):
                    now.append((it["params"], it["answer"]))
        elif not run.limitation and run.answer:
            now.append((run.params, run.answer))
        seen = {}
        for p, a in now:                                   # внутри одного запроса («Киев и Прага» одинаковым текстом)
            k = _norm_answer(a)
            if k in seen and seen[k] != p:
                return {"params": p, "other_params": seen[k], "other_request": None, "answer": a}
            seen[k] = p
        for p_old, a_old, req in self._past_answers(name, run.version):
            k = _norm_answer(a_old)
            if k and k in seen and seen[k] != p_old:
                return {"params": seen[k], "other_params": p_old, "other_request": req, "answer": a_old}
        return None

    def _repair_generalization(self, cap: dict, clash: dict, text: str, ctx: dict, task_id: int, monster: dict | None,
                               files: list[Path], mode: str, run: SkillRun) -> SkillRun:
        name = cap["name"]
        self.bus.emit("SKILL_NOT_PARAMETERIZED", task_id=task_id, capability=name, params=clash["params"],
                      other_params=clash["other_params"], other_request=clash["other_request"])
        if not self.llm.configured():
            run.ok, run.error = False, (f"skill '{name}' gives the same answer for different requests "
                                        f"({clash['other_params']} vs {clash['params']}); it needs repair, but the brain is not configured")
            return run
        probes = [t for t in [clash["other_request"], text] if t]
        reason = (
            "THE SKILL IS NOT GENERALIZED: different requests produce the SAME answer.\n"
            f"Request A: {clash['other_request'] or '(earlier request)'} -> params {json.dumps(clash['other_params'], ensure_ascii=False)}\n"
            f"Request B: {text} -> params {json.dumps(clash['params'], ensure_ascii=False)}\n"
            f"Identical answer (start): {str(clash['answer'])[:600]}\n"
            "Fix the ROOT CAUSE: parse_request must extract the concrete value each request names (never map an explicit, "
            "unrecognised value to a generic default - pass it through verbatim); run()/format_result must produce "
            "value-specific content FIRST (no generic bank in front of a truncation). For open-ended values (professions, "
            "topics) keep a built-in catalog for common values AND declare an llm_slot that supplies material for values "
            "outside the catalog (parse_request may return \"_slots\" to request it only then). Format the answer as Markdown "
            "with a '### ' title naming the values. Add tests proving that requests A and B give different, value-specific "
            "results; old tests that encode the generic behaviour may be rewritten.")
        out = self.evolution.mutate(name, reason=reason, task_id=task_id,
                                    probe=lambda code: self.probe(cap, code, probes))
        if not out.accepted:
            run.ok, run.error = False, f"skill '{name}' is not generalized and the repair was rejected: {out.reason}"
            return run
        fresh = self.registry.get(name)
        # повторяем ТЕКУЩИЙ запрос новой версией: снова разбор (он тоже мог быть исправлен) и исполнение
        res = self._invoke(fresh, monster, {"text": text, "context": ctx, "steps": ["parse"]}, files, network=False)
        params = (res.report or {}).get("params")
        if params is None:
            params = run.params
        again = self._dispatch(fresh, params, ctx, text, task_id, monster, files, mode, _checked=True)
        again.repaired, again.llm_calls = True, again.llm_calls + run.llm_calls + 1
        return again

    def probe(self, cap: dict, code: str, requests: list[str]) -> tuple[bool, str]:
        """Проверка ОБОБЩЁННОСТИ кода навыка (без LLM): запросы из жалобы + примеры навыка.
        Требуем: запросы из жалобы разбираются; разные запросы -> разные параметры; разные параметры -> разные ответы."""
        meta = skillmeta.extract(code)
        if not meta.get("callable"):
            return False, "the new version is not a callable skill (SKILL + parse_request + run)"
        texts = list(dict.fromkeys([t for t in requests if t] + list(meta.get("examples") or [])[:4]))
        ctx = {"today": date.today().isoformat(), "lang": "en"}
        sources = self.registry.collect_dependencies(cap["dependencies"]) if cap.get("dependencies") else {}
        sources[cap["name"]] = code
        slots_meta = meta.get("llm_slots") or {}
        parsed = []
        for t in texts:
            rep = self.sandbox.run_invoke(capabilities=sources, job={"skill": cap["name"], "text": t, "context": ctx, "steps": ["parse"]},
                                          input_files=[], artifacts_dir=None, network=False).report or {}
            if not rep.get("ok"):
                return False, f"parse_request crashed on {t!r}: {rep.get('error')}"
            p = rep.get("params")
            if p is None:
                if t in requests:
                    return False, f"parse_request does not understand {t!r}"
                continue
            parsed.append((t, p[0] if isinstance(p, list) else p))
        req_params = [json.dumps(p, sort_keys=True) for t, p in parsed if t in requests]
        if len(req_params) != len(set(req_params)):
            return False, f"different requests {requests} are parsed to the same parameters {req_params[0]}"
        answers = {}
        for t, p in parsed:
            clean = {k: v for k, v in p.items() if not str(k).startswith("_")}
            slots = {s: f"[{s} for {json.dumps(clean, ensure_ascii=False)}]" for s in slots_meta} if slots_meta else None
            rep = self.sandbox.run_invoke(capabilities=sources, job={"skill": cap["name"], "params": clean, "context": ctx,
                                                                    "steps": ["run", "format"], "slots": slots},
                                          input_files=[], artifacts_dir=None, network=bool(cap.get("network"))).report or {}
            if not rep.get("ok"):
                if rep.get("error_type") in TEMPORARY_ERRORS | {"ValueError"}:
                    continue                      # честный отказ / недоступный провайдер — не доказательство поломки
                return False, f"run/format failed for {t!r}: {rep.get('error')}"
            k = _norm_answer(rep.get("answer") or json.dumps(rep.get("output"), sort_keys=True, default=str))
            key = json.dumps(clean, sort_keys=True)
            for other_key, other_answer in answers.items():
                if other_key != key and other_answer == k:
                    return False, f"params {other_key} and {key} still produce the same answer"
            answers[key] = k
        return True, f"{len(answers)} parameter sets give {len(set(answers.values()))} different answers"

    def audit(self, name: str, task_id: int | None = None) -> tuple[bool, str]:
        """После обучения: проверяем, что новый навык действительно обобщён (на его же примерах). Без LLM."""
        cap = self.registry.get(name)
        info = self.registry.code_of(name)
        if not cap or not info or not (cap.get("skill") or {}).get("callable"):
            return True, "not a callable skill"
        if (cap.get("skill") or {}).get("knowledge"):
            return True, "knowledge-driven skill: content comes from knowledge packs"
        ok, why = self.probe(cap, info["code"], [])
        self.bus.emit("SKILL_AUDITED", task_id=task_id, capability=name, ok=ok, detail=why[:300])
        if ok or not self.llm.configured():
            return ok, why
        examples = list((cap.get("skill") or {}).get("examples") or [])[:4]
        out = self.evolution.mutate(name, reason=(
            f"THE SKILL IS NOT GENERALIZED: {why}. Its own examples {examples} must produce value-specific, different answers. "
            "Extract every concrete value; never map explicit values to a generic default; value-specific content first; "
            "catalog + llm_slot for open-ended values; Markdown answer with a '### ' title naming the values."),
            task_id=task_id, probe=lambda code: self.probe(cap, code, []))
        return out.accepted, out.reason or why

    # ------------------------------------------------------------------ песочница
    def _version_for(self, cap: dict, monster: dict | None) -> str:
        if monster:
            row = self.registry.db.one("SELECT pinned_version FROM monster_capabilities WHERE monster_id=? AND capability=?",
                                       (monster["id"], cap["name"]))
            if row and row["pinned_version"]:
                return row["pinned_version"]
        return cap["version"]

    def _invoke(self, cap: dict, monster: dict | None, job: dict, files: list[Path], *, network: bool, artifacts=None):
        version = self._version_for(cap, monster)
        own = self.registry.code_of(cap["name"], version if version != cap["version"] else None)
        sources = self.registry.collect_dependencies(cap["dependencies"]) if cap["dependencies"] else {}
        sources[cap["name"]] = own["code"]
        return self.sandbox.run_invoke(capabilities=sources, job={**job, "skill": cap["name"]}, input_files=list(files),
                                       artifacts_dir=artifacts, network=network)


def _plain(output) -> str:
    if isinstance(output, dict):
        return "; ".join(f"{k}: {v}" for k, v in list(output.items())[:8])[:600]
    return str(output)[:600]
