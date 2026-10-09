"""Мозг Frankenstein: полный цикл одной задачи (и монстр, который её выполняет).

    TASK -> UNDERSTAND/PLAN -> поиск пробелов в реестре -> (REUSE | COMPOSE | MUTATE | CREATE)
         -> выполнение -> оценка -> обучение (статистика, память, паттерны) -> новое поколение

Монстр: новая задача либо собирает НОВОГО монстра (mode=new), либо выполняется существующим (monster_id).
Органы, выращенные или взятые из реестра, прикрепляются к монстру; его версия растёт (monsters.py).

Всё, что происходит, публикуется как события в шину (events.py), интерфейс только отображает их
(анимации лаборатории — frontend/src/lab/director.ts — привязаны к этим событиям).
"""
import re
import threading
import time
from pathlib import Path

from . import answer_format, cancel, config
from .builder import Builder
from .db import Database, dumps, loads, now
from .events import EventBus
from .evolution import Evolution
from .eleven import ElevenBrain
from .executor import Executor
from .knowledge import Knowledge
from . import gate as gate_rules
from . import templates
from .executor import NeedsConfirmation
from .knowledge_packs import KnowledgePacks
from .learning_import import import_learning
from .llm_cache import LLMCache
from .researcher import Researcher
from .theatrics import Theatrics
from .skills import LEARN_RE, SkillEngine
from .llm import LLM, LLMError
from .monsters import Monsters
from .planner import Planner
from .registry import Registry
from .sandbox import Sandbox
from .settings import Settings
from .tts import Voice
from .usage import Ledger, current_task, task_budget


class Brain:
    def __init__(self, llm: LLM | None = None):
        for d in (config.DATA_DIR, config.CAPS_DIR, config.RUNS_DIR, config.ARTIFACTS_DIR, config.UPLOADS_DIR):
            d.mkdir(parents=True, exist_ok=True)
        self.db = Database(config.DB_PATH)
        self.bus = EventBus(self.db)
        self.registry = Registry(self.db)
        self.imported_learning = import_learning(self.db, self.registry, config.IMPORT_DATA_DIR)
        imported = {k: v for k, v in self.imported_learning.items() if isinstance(v, int) and v > 0}
        if imported:
            self.bus.emit("LEARNING_IMPORTED", description="Reused learning from the original brain", **imported)
        self.ledger = Ledger(self.db)
        self.llm = llm or LLM()
        self.llm.ledger = self.ledger
        self.llm_cache = LLMCache(self.db)
        self.first_organs_free = config.FIRST_ORGANS_FREE      # monster-lab выключает для своего мозга (lab_api.attach)
        self.llm.cache = self.llm_cache            # тот же запрос -> проверенный ответ из памяти (0 токенов)
        # события «мозга» (LLM_CALL_STARTED/DONE) уходят в общую шину и привязываются к текущей задаче
        self.llm.on_event = lambda event, **data: self.bus.emit(event, task_id=current_task.get(), **data)
        self.settings = Settings(self.db)
        self.llm.settings = self.settings if llm is None else None     # в тестах (сценарный LLM) настройки не нужны
        self.llm.eleven = ElevenBrain(self._get_brain_agent, self._save_brain_agent)
        self.voice = Voice(self.ledger)
        self.theatrics = Theatrics(self.ledger, self.bus)      # звуки характеров и сцена рождения (ElevenLabs, один раз)
        self.monsters = Monsters(self.db, self.registry)
        self.knowledge = Knowledge(self.db)
        self._teams: dict[int, list[int]] = {}      # task_id -> участники команды (первый — ведущий)
        self.sandbox = Sandbox()
        self.builder = Builder(self.llm, self.sandbox, self.registry, self.bus)
        self.evolution = Evolution(self.llm, self.sandbox, self.registry, self.bus, self.builder)
        # пакеты знаний (Knowledge Cache): предметные знания отдельно от органов; курированные — добавляются, если их нет
        self.packs = KnowledgePacks(self.db)
        self.packs.seed()
        self.skills = SkillEngine(self.llm, self.sandbox, self.registry, self.knowledge, self.bus, self.ledger, self.evolution,
                                  packs=self.packs)
        # свежие знания через Sokosumi (спонсор): платно -> только после подтверждения; без ключа выключено
        self.skills.researcher = Researcher(self.packs, self.llm, self.ledger, self.bus)
        self.skills.researcher.market.refresh_async()     # каталог агентов Sokosumi — в фоне, раз в сутки (бесплатно)
        self.planner = Planner(self.llm, self.registry)
        self.executor = Executor(self.llm, self.sandbox, self.registry, self.bus, self.builder, self.evolution)
        self._busy = threading.Lock()
        self.current_task_id: int | None = None
        self.last_error: str | None = None
        self._allow_build: dict[int, bool] = {}      # task_id -> пользователь подтвердил дорогую сборку органа
        # совместимость: «узкие» органы, выученные раньше, получают версию-метод из проверенного шаблона (с откатом)
        try:
            self.adopted = templates.adopt(registry=self.registry, builder=self.builder, monsters=self.monsters,
                                           bus=self.bus, db=self.db)
        except Exception as exc:  # noqa: BLE001 — миграция не должна мешать запуску
            self.adopted = []
            self.bus.emit("TEMPLATE_FAILED", template="adopt", problems=str(exc)[:300])

    def _get_brain_agent(self) -> str | None:
        row = self.db.one("SELECT value FROM meta WHERE key='eleven_brain_agent_id'")
        return row["value"] if row else None

    def _save_brain_agent(self, agent_id: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('eleven_brain_agent_id',?)", (agent_id,))

    # ------------------------------------------------------------------ состояние
    def is_busy(self) -> bool:
        return self._busy.locked()

    def reset(self) -> None:
        """DEMO RESET: полностью возвращаем организм в поколение 0 с нулём капабилити."""
        import shutil
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("Frankenstein is busy")
        try:
            self.db.reset()
            self.packs.seed()
            for d in (config.CAPS_DIR, config.RUNS_DIR, config.ARTIFACTS_DIR):
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)
            self.bus.emit("DEMO_RESET", description="reset to generation 0")
        finally:
            self._busy.release()

    # ------------------------------------------------------------------ запуск в фоне
    def start_task(self, text: str, files: list[Path], lang: str, forced_intent: str | None = None, *,
                   monster_id: int | None = None, mode: str = "new", emotion: str = "neutral", upgrade: bool = False,
                   monster_ids: list[int] | None = None, allow_build: bool = False) -> dict:
        """mode: new — собрать нового монстра; reuse — задачу выполняет существующий monster_id.
        Возвращает {"task_id", "monster_id"}."""
        if mode == "team":
            ids = list(dict.fromkeys(monster_ids or []))
            if len(ids) < 2 or any(not (m := self.monsters.get(i)) or m["status"] != "alive" for i in ids):
                raise ValueError("a team needs at least two living monsters")
            monster_id = ids[0]
        if mode in ("reuse", "team"):
            m = self.monsters.get(monster_id) if monster_id else None
            if not m or m["status"] not in ("alive", "forming"):
                raise ValueError("unknown or unavailable monster")
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("Frankenstein is busy")
        try:
            task_id = self.db.execute(
                "INSERT INTO tasks(input,files,lang,status,generation_before,created_at) VALUES(?,?,?,?,?,?)",
                (text, dumps([str(f) for f in files]), lang, "running", self.registry.generation(), now()))
            if forced_intent == "self_improve":
                monster = None
            elif mode in ("reuse", "team"):
                monster = self.monsters.get(monster_id)
                self.db.execute("UPDATE tasks SET monster_id=?, monster_ids=? WHERE id=?",
                                (monster_id, dumps(ids if mode == "team" else [monster_id]), task_id))
                if mode == "team":
                    self._teams[task_id] = ids
            else:
                monster = self.monsters.create(emotion, task_id=task_id)
        except Exception:
            self._busy.release()
            raise
        self.current_task_id = task_id
        self._allow_build[task_id] = bool(allow_build)
        threading.Thread(target=self._thread_main, args=(task_id, text, files, lang, forced_intent,
                                                         monster["id"] if monster else None, mode, upgrade), daemon=True).start()
        return {"task_id": task_id, "monster_id": monster["id"] if monster else None}

    def cancel_task(self, task_id: int) -> bool:
        if self.current_task_id != task_id:
            return False
        cancel.request(task_id)
        self.bus.emit("TASK_CANCEL_REQUESTED", task_id=task_id)
        return True

    def start_self_audit(self, lang: str) -> int:
        """Кнопка «Improve yourself»: сразу самоаудит, без вызова планировщика (экономим вызов «мозга»)."""
        return self.start_task("Improve yourself.", [], lang, forced_intent="self_improve")["task_id"]

    def start_mutation(self, name: str) -> int:
        if not self._busy.acquire(blocking=False):
            raise RuntimeError("Frankenstein is busy")
        task_id = self.db.execute(
            "INSERT INTO tasks(input,files,lang,status,generation_before,created_at) VALUES(?,?,?,?,?,?)",
            (f"Mutate capability: {name}", "[]", "en", "running", self.registry.generation(), now()))
        self.current_task_id = task_id

        def work():
            current_task.set(task_id)
            task_budget.set(config.MAX_TOKENS_PER_BUILD)
            try:
                before = self._fingerprint()
                self.bus.emit("TASK_RECEIVED", task_id=task_id, text=f"Mutate capability: {name}", files=[])
                self.evolution.mutate(name, reason="requested by the operator: improve robustness", task_id=task_id)
                self._finish(task_id, before, True, {"summary": {"en": f"Mutation of {name} finished", "cs": f"Mutace {name} dokončena"}}, None, time.time())
            except Exception as exc:  # noqa: BLE001
                self._finish(task_id, self._fingerprint(), False, None, str(exc), time.time())
            finally:
                self.current_task_id = None
                self._busy.release()
        threading.Thread(target=work, daemon=True).start()
        return task_id

    def _thread_main(self, task_id: int, text: str, files: list[Path], lang: str, forced_intent: str | None = None,
                     monster_id: int | None = None, mode: str = "new", upgrade: bool = False) -> None:
        current_task.set(task_id)       # весь расход (LLM, песочница) с этого момента пишется на эту задачу
        # бюджет токенов: обычная задача — небольшой; сборку/ремонт органа он повышает (решение барьера, «научись», аудит)
        task_budget.set(config.MAX_TOKENS_PER_BUILD if (forced_intent or self._allow_build.get(task_id) or upgrade)
                        else config.MAX_TOKENS_PER_TASK)
        try:
            self._run_task(task_id, text, files, lang, forced_intent, monster_id, mode, upgrade)
        finally:
            cancel.clear(task_id)
            self.current_task_id = None
            self._busy.release()

    # ------------------------------------------------------------------ главный цикл
    def _run_task(self, task_id: int, text: str, files: list[Path], lang: str, forced_intent: str | None = None,
                  monster_id: int | None = None, mode: str = "new", upgrade: bool = False) -> None:
        t0 = time.time()
        before = self._fingerprint()
        preexisting = self.registry.active_names()
        emit = lambda t, **kw: self.bus.emit(t, task_id=task_id, **kw)   # noqa: E731
        monster = self.monsters.get(monster_id) if monster_id else None
        emit("TASK_RECEIVED", text=text, files=[f.name for f in files], mode=mode,
             monster=self.monsters.to_event(monster) if monster else None)
        team = [self.monsters.get(i) for i in self._teams.get(task_id, [])]
        if monster:
            emit("MONSTER_FORMING" if mode == "new" else "MONSTER_ACTIVATED", monster=self.monsters.to_event(monster))
            # в фоне, пока идёт работа: звуки характера (один раз на всех) и сцена рождения нового монстра (один раз)
            self.theatrics.prepare_async(monster, task=text, lang=lang, birth=mode == "new", task_id=task_id)
        if team:
            emit("TEAM_ASSEMBLED", members=[self.monsters.to_event(m) for m in team])
        result, error, cancelled = None, None, False
        allow = self._allow_build.pop(task_id, False)
        metrics: dict = {"tier": None, "organ_created": False}
        try:
            # -1) ЯВНАЯ просьба исследовать («do research…», «/research», «найди в интернете»): ни органов, ни модели —
            #     готовый отчёт из памяти или предложение агента Sokosumi с ценой («да» = нанять)
            rs = self.skills.researcher
            if forced_intent is None and mode != "team" and rs.available() and rs.wants_research(text):
                saved = rs.questions.lookup(text)
                if saved:
                    result = _saved_research(saved)
                    emit("KNOWLEDGE_CACHE_HIT", source="sokosumi_research", age_seconds=0)
                    metrics.update(tier="instant", decision="research_cache")
                else:
                    emit("RESEARCH_REQUESTED", started=allow)
                    result = _with_research({"summary": {"en": "", "cs": ""}, "files": [],
                                             "data": {"answered_from": "research offer (no organ was built)"}},
                                            rs, text, lang, monster, task_id, allow, from_memory=False)
                    metrics.update(tier="research", decision="research")
                self._set_strategy(task_id, "research", {"decision": "research", "explicit": True})
                if monster:
                    self._evolve_monster(monster, task_id, before, [], result, emit)
                raise _Done()
            # 0) САМЫЙ ДЕШЁВЫЙ ПУТЬ: уже выученный навык исполняет запрос без пересборки (0 или 1 быстрый вызов модели)
            if forced_intent is None and mode != "team" and not LEARN_RE.search(text):
                sr = self.skills.try_handle(text, lang, task_id, monster, files, allow_build=allow)
                if sr is not None and sr.decision:
                    # навык нашёлся, но починить его можно только переписав код — без подтверждения не тратим минуты
                    result = _confirm_result(sr.decision)
                    self._set_strategy(task_id, "confirm", {"skill": sr.skill, "decision": sr.decision})
                    emit("CAPABILITY_GATE", **sr.decision)
                    metrics.update(tier="gate", skill=sr.skill, decision="confirm")
                    if monster:
                        self._evolve_monster(monster, task_id, before, [], result, emit)
                    raise _Done()
                if sr is not None and sr.needs_input:
                    # монстр задал уточняющий вопрос — 0 вызовов модели; ответ пользователя подставится в этот же навык
                    result = {"summary": {"en": sr.answer, "cs": sr.answer}, "files": [],
                              "data": {"skill": sr.skill, "version": sr.version, "mode": "ask", "tier": "instant",
                                       "params": sr.params, "needs_input": sr.needs_input}}
                    self._set_strategy(task_id, "ask", {"skill": sr.skill, "params": sr.params, "tier": "instant"})
                    emit("TASK_STRATEGY", mode="ask", tier="instant", skill=sr.skill, llm_calls=0)
                    metrics.update(tier="instant", skill=sr.skill, organ_reused=True, decision="ask")
                    if monster:
                        self._evolve_monster(monster, task_id, before, [], result, emit)
                    raise _Done()
                if sr is not None:
                    tier = TIER.get(sr.mode, "smart")
                    self._set_strategy(task_id, sr.mode, {"skill": sr.skill, "version": sr.version, "params": sr.params,
                                                          "llm_calls": sr.llm_calls, "cache": sr.cache, "repaired": sr.repaired,
                                                          "limitation": bool(sr.limitation), "tier": tier, "knowledge": sr.knowledge})
                    emit("TASK_STRATEGY", mode=sr.mode, tier=tier, skill=sr.skill, version=sr.version, llm_calls=sr.llm_calls,
                         limitation=bool(sr.limitation), repaired=sr.repaired, knowledge=sr.knowledge)
                    metrics.update(tier=tier, skill=sr.skill, skill_version=sr.version, knowledge=sr.knowledge, cache=sr.cache,
                                   quality=sr.quality or [], organ_reused=True, decision_seconds=round(time.time() - t0, 2))
                    if not sr.ok:
                        raise RuntimeError(sr.error)
                    result = {"summary": {"en": sr.answer, "cs": sr.answer},
                              "data": {"skill": sr.skill, "version": sr.version, "mode": sr.mode, "tier": TIER.get(sr.mode, "smart"),
                                       "params": sr.params, "limitation": sr.limitation, "output": sr.output,
                                       "knowledge": sr.knowledge, "quality": sr.quality or [],
                                       "offer": sr.offer, "research": sr.research},
                              "files": [f"/api/artifacts/{task_id}/{f}" for f in sr.files]}
                    if not sr.offer and not sr.knowledge:              # пакеты знаний сами следят за своей свежестью
                        result = self._offline_honesty(result, [sr.skill], text, lang, monster, task_id, allow)
                    self.db.execute("UPDATE tasks SET trace=? WHERE id=?", (dumps([sr.skill]), task_id))
                    if monster:
                        self._evolve_monster(monster, task_id, before, [sr.skill], result, emit)
                    raise _Done()
            # 0.5) уже исследованный (Sokosumi) вопрос про актуальное — ответ из памяти: 0 вызовов, 0 кредитов
            if forced_intent is None and mode != "team":
                saved = self.skills.researcher.questions.lookup(text)
                if saved and self.skills.researcher.wants_fresh(text):
                    result = _saved_research(saved)
                    self._set_strategy(task_id, "cache", {"research": True, "tier": "instant"})
                    emit("KNOWLEDGE_CACHE_HIT", source="sokosumi_research", age_seconds=0)
                    metrics.update(tier="instant", decision="research_cache")
                    if monster:
                        self._evolve_monster(monster, task_id, before, [], result, emit)
                    raise _Done()
            # 1-2) ПОНЯТЬ + СПЛАНИРОВАТЬ + найти пробелы
            if forced_intent == "self_improve":
                plan = {"intent": "self_improve", "requirements": [], "expects_files": False, "recipe": None,
                        "understanding": {"en": "Audit and improve myself", "cs": "Zkontrolovat a vylepšit sebe"}}
            else:
                plan = self.planner.analyze(text, [{"name": f.name, "bytes": f.stat().st_size} for f in files],
                                            monster=self._monster_context(monster, upgrade),
                                            team=[{"name": m["name"], "skills": [c["name"] for c in m["capabilities"]]} for m in team] or None)
            emit("TASK_ANALYZED", intent=plan["intent"], understanding=plan["understanding"],
                 requirements=[{"id": r["id"], "need": r["need"], "decision": r["decision"],
                                "capabilities": r["capabilities"] or ([r["spec"]["name"]] if r.get("spec") else [])}
                               for r in plan["requirements"]])
            self._announce_plan(plan, task_id, mode, emit)
            metrics["decision_seconds"] = round(time.time() - t0, 2)
            if plan["intent"] in ("task", "learn") and forced_intent is None:
                g = gate_rules.decide(plan, mode=mode, allow_build=allow, upgrade=upgrade, monster=monster, db=self.db, text=text,
                                      first_organs_free=self.first_organs_free)
                emit("CAPABILITY_GATE", **g.as_dict())
                metrics["decision"] = g.decision
                # вопрос про АКТУАЛЬНОЕ, а план хочет писать код («парсер рынка труда»): орган — минуты генерации и устареет
                # завтра; честнее и дешевле нанять агента Sokosumi (только по кнопке). Явное «научись/построй» — строим.
                rs = self.skills.researcher
                if (g.decision in ("build", "confirm") and plan["intent"] == "task" and not upgrade and rs.available()
                        and rs.should_offer(text) and not gate_rules.EXPLICIT_RE.search(text)):
                    emit("RESEARCH_INSTEAD_OF_BUILD", missing=g.missing_functionality)
                    result = _with_research({"summary": plan.get("direct_answer") or {"en": "", "cs": ""}, "files": [],
                                             "data": {"answered_from": "research offer (no organ was built)"}},
                                            rs, text, lang, monster, task_id, allow, from_memory=bool(plan.get("direct_answer")))
                    self._set_strategy(task_id, "research", {"decision": "research", "instead_of": g.missing_functionality})
                    metrics.update(tier="research", decision="research")
                    if monster:
                        self._evolve_monster(monster, task_id, before, [], result, emit)
                    raise _Done()
                if g.decision in ("build", "template"):
                    task_budget.set(config.MAX_TOKENS_PER_BUILD)     # сборка разрешена — бюджет сборки
                if g.user_confirmation_required:
                    result = _confirm_result(g.as_dict())
                    self._set_strategy(task_id, "confirm", {"decision": g.as_dict(), "params": plan.get("params") or {}})
                    metrics["tier"] = "gate"
                    if monster:
                        self._evolve_monster(monster, task_id, before, [], result, emit)
                    raise _Done()

            if plan["intent"] == "learn":
                # «Научись …»: строим/подключаем ОБОБЩЁННЫЕ навыки, сам запрос ничего не исполняет
                self._satisfy_requirements(plan, task_id, team)
                names = [r["spec"]["name"] if r["decision"] == "CREATE" else c for r in plan["requirements"]
                         for c in ([None] if r["decision"] == "CREATE" else r["capabilities"])]
                names = [n for n in dict.fromkeys(names) if n and self.registry.get(n)]
                for n in names:
                    # навык должен быть ОБОБЩЁН: на его же примерах разные параметры дают разные ответы (без LLM;
                    # ремонт — только если проверка провалилась)
                    self.skills.audit(n, task_id=task_id)
                result = _learn_report([self.registry.get(n) for n in names])
                if monster:
                    self._evolve_monster(monster, task_id, before, names, result, emit)
            elif plan["intent"] == "task" and not plan["requirements"] and plan.get("direct_answer"):
                # инструменты не нужны (совет, знание) — монстр отвечает сам, органы не строятся
                emit("DIRECT_ANSWER", needs_internet=bool(plan.get("needs_internet")))
                result = {"summary": plan["direct_answer"], "files": [],
                          "data": {"answered_from": "model knowledge (no organs were needed)",
                                   "needs_internet": bool(plan.get("needs_internet"))}}
                # вопрос про АКТУАЛЬНОЕ: честная пометка + (по желанию) агент Sokosumi, выбранный под вопрос автоматически
                rs = self.skills.researcher
                if rs.should_offer(text, needs_internet=bool(plan.get("needs_internet"))):
                    result = _with_research(result, rs, text, lang, monster, task_id, allow)
                if monster:
                    self._evolve_monster(monster, task_id, before, [], result, emit)
            elif plan["intent"] == "self_improve":
                audit = self.evolution.self_audit(task_id=task_id)
                result = {"summary": {"en": "Self-audit finished.", "cs": "Sebeaudit dokončen."},
                          "data": {"actions": audit["actions"], "before": _slim(audit.get("before")), "after": _slim(audit.get("after"))},
                          "files": []}
            else:
                mutations = self._satisfy_requirements(plan, task_id, team)
                if plan["requirements"] and all(r["decision"] == "MUTATE" for r in plan["requirements"]):
                    # задача была про сами органы («почини/улучши монстра»): отчёт об обслуживании, воркфлоу не нужен
                    result = _maintenance_report(mutations)
                    emit("MAINTENANCE_DONE", organs=len(mutations), improved=sum(1 for m in mutations if m.accepted))
                    if monster:
                        self._evolve_monster(monster, task_id, before, [], result, emit)
                    raise _Done()
                # 3) ВЫПОЛНИТЬ
                emit("EXECUTION_STARTED")
                # контекст ЭТОГО запроса: его текст и его параметры (не прошлые) — воркфлоу читает task["params"]
                task = {"text": text, "lang": lang, "params": plan.get("params") or {}}
                # чинить/строить органы после ошибки воркфлоу можно, только если сборка уже разрешена (барьер)
                may_evolve = bool(allow or upgrade or mode in ("new", "team") or metrics.get("decision") in ("build", "template")
                                  or not (monster or {}).get("capabilities"))
                try:
                    ex = self.executor.run(task, plan["understanding"].get("en", ""), files, task_id=task_id,
                                           expects_files=plan.get("expects_files", False), recipe_id=plan.get("recipe"),
                                           may_evolve=may_evolve)
                except NeedsConfirmation as nc:
                    cost, latency = gate_rules.build_estimate(self.db)
                    d = {**nc.decision, "estimated_cost": cost, "estimated_latency": latency}
                    result = _confirm_result(d)
                    self._set_strategy(task_id, "confirm", {"decision": d, "params": plan.get("params") or {}})
                    emit("CAPABILITY_GATE", **d)
                    metrics.update(tier="gate", decision="confirm")
                    if monster:
                        self._evolve_monster(monster, task_id, before, [], result, emit)
                    raise _Done()
                if not ex.ok:
                    raise RuntimeError("task could not be completed: " + (ex.error or "unknown error")[-400:])
                self.executor.record_success(ex.calls, task_id)
                used = [c["capability"] for c in ex.calls if c.get("depth", 0) == 0]
                for name in dict.fromkeys(used):
                    if name in preexisting:     # реально использовали уже существовавший орган
                        emit("CAPABILITY_REUSED", capability=name, calls=used.count(name))
                if ex.workflow_code:
                    # запоминаем удачный воркфлоу как РЕЦЕПТ: похожая задача потом пройдёт без вызова модели
                    rid = self.registry.save_recipe(plan["understanding"].get("en", text[:100]), list(dict.fromkeys(used)),
                                                    ex.workflow_code, params=plan.get("params") or {}, source_text=text)
                    emit("RECIPE_SAVED", recipe=rid, capabilities=list(dict.fromkeys(used)))
                result = {"summary": ex.output.get("summary"), "data": ex.output.get("data"),
                          "files": [f"/api/artifacts/{task_id}/{f}" for f in ex.files], "attempts": ex.attempts,
                          "trace": used, "recipe_used": ex.recipe_used}
                result = self._offline_honesty(result, used, text, lang, monster, task_id, allow)
                self.db.execute("UPDATE tasks SET trace=? WHERE id=?", (dumps(used), task_id))
                if team:
                    self._evolve_team(team, plan, task_id, before, result, emit)
                elif monster:
                    self._evolve_monster(monster, task_id, before, used, result, emit)
        except _Done:
            pass
        except cancel.TaskCancelled:
            error, cancelled = "cancelled by the user", True
        except (LLMError, RuntimeError, KeyError, ValueError) as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001  — задача не должна ронять сервер
            error = f"{type(exc).__name__}: {exc}"

        self._teams.pop(task_id, None)
        if error is None and isinstance(result, dict) and result.get("summary"):
            # ответ человеку, а не дамп: словари/ISO-время из кода органов -> читаемый текст (без модели)
            result["summary"] = answer_format.normalize(result["summary"])
        self._record_metrics(task_id, metrics, t0)
        for m in (team or ([monster] if monster else [])):
            summary = (result or {}).get("summary") or {}
            self.monsters.finish_task(m["id"], task_id, error is None, text,
                                      summary.get("en", "") if isinstance(summary, dict) else str(summary))
            if error is not None and mode != "reuse":
                emit("MONSTER_FAILED", monster=self.monsters.to_event(self.monsters.get(monster["id"])), error=error[:300])
        if error is None:
            emit("TASK_COMPLETED", duration=round(time.time() - t0, 1), summary=_short(result.get("summary")),
                 recipe_used=bool(result.get("recipe_used")), usage=self.ledger.summary(task_id)["task"])
        # 4) ПОСЛЕ задачи: повторяющиеся паттерны → новый «орган» (может сработать и после неудачной задачи)
        try:
            if error is None and not cancelled:
                pattern = self.evolution.detect_pattern()
                if pattern:
                    self.evolution.build_from_pattern(pattern, task_id=task_id)
        except Exception as exc:  # noqa: BLE001
            emit("REFLECTION_FAILED", error=str(exc)[:300])
        self._finish(task_id, before, error is None, result, error, t0, cancelled=cancelled)

    # ------------------------------------------------------------------ метрики исполнения
    def _record_metrics(self, task_id: int, metrics: dict, t0: float) -> None:
        """Запись исполнения: режим, навык, пакет знаний, был ли создан орган, вызовы/токены, время. Только измеренное."""
        built = self.db.one("SELECT COUNT(*) n FROM evolution_events WHERE task_id=? AND event_type IN "
                            "('CAPABILITY_BUILD_STARTED','CAPABILITY_MUTATION_STARTED')", (task_id,))["n"]
        templ = self.db.one("SELECT COUNT(*) n FROM evolution_events WHERE task_id=? AND event_type='TEMPLATE_INSTALL_STARTED'",
                            (task_id,))["n"]
        u = self.ledger.summary(task_id)["task"]
        row = self.db.one("SELECT mode FROM tasks WHERE id=?", (task_id,)) or {}
        tier = metrics.get("tier") or ("build" if built else "template" if templ else
                                       {"recipe": "light", "direct": "smart", "learn": "smart", "audit": "build"}.get(row.get("mode"), "smart"))
        metrics.update(tier=tier, organ_created=bool(built or templ), organ_generated_by_llm=bool(built), template_used=bool(templ),
                       llm_calls=u.get("llm_calls", 0), prompt_tokens=u.get("prompt_tokens", 0),
                       completion_tokens=u.get("completion_tokens", 0), credits=u.get("credits", 0),
                       seconds=round(time.time() - t0, 2))
        self.db.execute("UPDATE tasks SET metrics=? WHERE id=?", (dumps(metrics), task_id))

    # ------------------------------------------------------------------ стратегия
    def _offline_honesty(self, result: dict, used: list, text: str, lang: str, monster: dict | None, task_id: int,
                         allow: bool) -> dict:
        """Вопрос про актуальное, а ответили органы БЕЗ интернета: их данные зашиты в код (живой случай — «Job Market
        Catalog» с выдуманными зарплатами). Честная пометка + агент Sokosumi по кнопке («да» — нанять)."""
        rs = self.skills.researcher
        caps = [self.registry.get(n) for n in dict.fromkeys(used or [])]
        if not caps or any((c or {}).get("network") for c in caps) or not rs.should_offer(text):
            return result
        if not isinstance(result.get("data"), dict):
            result = {**result, "data": {"output": result.get("data")}}
        return _with_research(result, rs, text, lang, monster, task_id, allow, source="built-in data of the organ")

    def _set_strategy(self, task_id: int, mode: str, strategy: dict) -> None:
        self.db.execute("UPDATE tasks SET mode=?, strategy=? WHERE id=?", (mode, dumps(strategy), task_id))

    def _announce_plan(self, plan: dict, task_id: int, mode: str, emit) -> None:
        decisions = {r["decision"] for r in plan["requirements"]}
        if plan["intent"] == "self_improve":
            m = "audit"
        elif plan["intent"] == "learn":
            m = "learn"
        elif not plan["requirements"] and plan.get("direct_answer"):
            m = "direct"
        elif mode == "team":
            m = "team"
        elif plan["requirements"] and decisions == {"MUTATE"}:
            m = "maintenance"
        elif decisions & {"CREATE", "MUTATE"}:
            m = "full"
        elif plan.get("recipe"):
            m = "recipe"
        else:
            m = "reuse"
        self._set_strategy(task_id, m, {"decisions": sorted(decisions), "recipe": plan.get("recipe"),
                                        "params": plan.get("params") or {}})
        emit("TASK_STRATEGY", mode=m, decisions=sorted(decisions),
             new=[r["spec"]["name"] for r in plan["requirements"] if r["decision"] == "CREATE"],
             reused=[c for r in plan["requirements"] if r["decision"] in ("REUSE", "COMPOSE") for c in r["capabilities"]])

    def _evolve_team(self, team: list[dict], plan: dict, task_id: int, before: set, result: dict, emit) -> None:
        """Команда: новые органы получает тот участник, которому планировщик поручил подзадачу; чужие органы не дублируются."""
        by_name = {m["name"]: m for m in team}
        before_names = {name for name, _, _ in before}
        for r in plan["requirements"]:
            owner = by_name.get(r.get("assign_to") or "", team[0])
            names = [r["spec"]["name"]] if r["decision"] == "CREATE" else []
            for n in names:
                if n not in before_names and self.monsters.attach(owner["id"], n, source="built", task_id=task_id):
                    self.registry.set_owner(n, owner["id"])
                    cap = next(c for c in self.monsters.get(owner["id"])["capabilities"] if c["name"] == n)
                    emit("MONSTER_UPGRADED", capability=n, category=cap["category"], source="built", monster_id=owner["id"])
        lead = self.monsters.get(team[0]["id"])
        emit("MONSTER_READY", monster=self.monsters.to_event(lead), intro=self.monsters.intro(lead, []),
             summary=_short(result.get("summary")), learned=[], team=[self.monsters.to_event(self.monsters.get(m["id"])) for m in team])

    # ------------------------------------------------------------------ монстр
    def _monster_context(self, monster: dict | None, upgrade: bool = False) -> dict | None:
        if not monster:
            return None
        return {"name": monster["name"], "capabilities": [c["name"] for c in monster["capabilities"]],
                "memory": self.monsters.memory(monster["id"], 5), "upgrade": upgrade}

    def _evolve_monster(self, monster: dict, task_id: int, before: set, used: list[str], result: dict, emit) -> None:
        """Органы, выращенные в этой задаче или взятые из реестра, становятся частью монстра. Затем — MONSTER_READY."""
        before_names = {name for name, _, _ in before}
        built = [c["name"] for c in self.registry.active() if c["name"] not in before_names]
        learned = []
        for name in list(dict.fromkeys(built + used)):
            if name in built:
                self.registry.set_owner(name, monster["id"])
            if self.monsters.attach(monster["id"], name, source="built" if name in built else "library", task_id=task_id):
                cap = next(c for c in self.monsters.get(monster["id"])["capabilities"] if c["name"] == name)
                learned.append(cap)
                emit("MONSTER_UPGRADED", capability=name, category=cap["category"], source=cap["source"],
                     monster_id=monster["id"])
        fresh = self.monsters.get(monster["id"])
        emit("MONSTER_READY", monster=self.monsters.to_event(fresh), intro=self.monsters.intro(fresh, learned),
             summary=_short(result.get("summary")), learned=[c["name"] for c in learned])

    def _satisfy_requirements(self, plan: dict, task_id: int, team: list[dict] | None = None) -> list:
        """Для каждой потребности: REUSE / COMPOSE / MUTATE / CREATE.
        ЭКОНОМИЯ: все CREATE строятся одним пакетом (builder.build_many) — 1 запрос вместо N."""
        emit = lambda t, **kw: self.bus.emit(t, task_id=task_id, **kw)   # noqa: E731
        gaps = 0
        to_create: list[dict] = []
        mutations = []
        for r in plan["requirements"]:
            d = r["decision"]
            if team:
                owner = r.get("assign_to") if r.get("assign_to") in {m["name"] for m in team} else team[0]["name"]
                emit("DELEGATED", monster=owner, need=r["need"], decision=d,
                     capabilities=r["capabilities"] or ([r["spec"]["name"]] if r.get("spec") else []))
            if d == "REUSE":
                emit("CAPABILITY_SEARCH", requirement=r["id"], need=r["need"], found=r["capabilities"])
            elif d == "COMPOSE":
                emit("CAPABILITY_COMPOSITION", requirement=r["id"], need=r["need"], parts=r["capabilities"])
            elif d == "MUTATE":
                gaps += 1
                name = r["capabilities"][0]
                emit("CAPABILITY_GAP_DETECTED", capability=name, need=r["need"], source="plan", kind="insufficient")
                mutations.append(self.evolution.mutate(name, reason=r.get("mutation_reason") or "insufficient for this task", task_id=task_id))
            elif d == "CREATE":
                gaps += 1
                emit("CAPABILITY_GAP_DETECTED", capability=r["spec"]["name"], need=r["need"], source="plan", kind="missing")
                tid = templates.match_spec(r["spec"])
                if tid and templates.install(tid, builder=self.builder, registry=self.registry, bus=self.bus,
                                             name=r["spec"]["name"], task_id=task_id):
                    continue                 # проверенный шаблон: орган готов за секунды, код моделью не генерировался
                to_create.append(r["spec"])
        if to_create:
            parents = {sp["name"]: next(iter(sp.get("dependencies", [])), None) for sp in to_create}
            for outcome in self.builder.build_many(to_create, task_id=task_id, parents=parents):
                if not outcome.ok:
                    raise RuntimeError(f"capability '{outcome.name}' could not be built: {outcome.reason}")
        if gaps == 0:
            emit("NO_NEW_CAPABILITIES_REQUIRED")
        return mutations

    # ------------------------------------------------------------------ обучение и поколения
    def _fingerprint(self) -> set:
        """Снимок организма: какие версии в каком статусе. Изменился снимок — значит, была эволюция."""
        return {(c["name"], c["version"], c["status"]) for c in self.registry.list()}

    def _finish(self, task_id: int, before: set, ok: bool, result, error, t0: float, cancelled: bool = False) -> None:
        gen_before = self.registry.generation()
        if self._fingerprint() != before:
            gen = self.registry.bump_generation("evolution")
            self.bus.emit("GENERATION_UPDATED", task_id=task_id, generation=gen, previous=gen_before)
        self.db.execute(
            "UPDATE tasks SET status=?, result=?, error=?, duration=?, generation_after=?, finished_at=? WHERE id=?",
            ("completed" if ok else "cancelled" if cancelled else "failed", dumps(result) if result else None, error,
             round(time.time() - t0, 2), self.registry.generation(), now(), task_id))
        if not ok:
            forgotten = self.llm_cache.forget_task(task_id)     # неудачный план не должен повториться из кэша
            if forgotten:
                self.bus.emit("LLM_CACHE_FORGOTTEN", task_id=task_id, entries=forgotten)
        if cancelled:
            self.bus.emit("TASK_CANCELLED", task_id=task_id)
        elif not ok:
            self.bus.emit("TASK_FAILED", task_id=task_id, error=error)
        self.bus.emit("TASK_FINISHED", task_id=task_id, ok=ok)

    def task(self, task_id: int) -> dict | None:
        row = self.db.one("SELECT * FROM tasks WHERE id=?", (task_id,))
        if not row:
            return None
        return {"id": row["id"], "input": row["input"], "status": row["status"], "lang": row["lang"],
                "monster_id": row.get("monster_id"),
                "result": loads(row["result"]), "error": row["error"], "duration": row["duration"],
                "generation_before": row["generation_before"], "generation_after": row["generation_after"],
                "created_at": row["created_at"], "finished_at": row["finished_at"]}


def _learn_report(caps: list[dict]) -> dict:
    """Что монстр выучил и как этим пользоваться (примеры — из метаданных самих навыков)."""
    lines_en, lines_cs = [], []
    for c in caps:
        ex = (c.get("skill") or {}).get("examples") or []
        lines_en.append(f"{c['name']} v{c['version']} — {c['purpose_en'] or c['description']}" + (f". Try: \"{ex[0]}\"" if ex else ""))
        lines_cs.append(f"{c['name']} v{c['version']} — {c['purpose_cs'] or c['description']}" + (f". Zkus: \"{ex[0]}\"" if ex else ""))
    return {"summary": {"en": "I learned: " + "; ".join(lines_en), "cs": "Naučil jsem se: " + "; ".join(lines_cs)},
            "data": {"learned": [{"skill": c["name"], "version": c["version"], "examples": (c.get("skill") or {}).get("examples", []),
                                  "limits": (c.get("skill") or {}).get("limits", {}), "callable": (c.get("skill") or {}).get("callable", False)}
                                 for c in caps]}, "files": []}


def _saved_research(saved: dict) -> dict:
    """Готовый отчёт агента Sokosumi на этот же вопрос (похожая формулировка) — 0 вызовов, 0 кредитов."""
    return {"summary": {"en": saved["answer"], "cs": saved["answer"]}, "files": [],
            "data": {"research": {k: saved.get(k) for k in ("agent_name", "credits", "sources", "fetched_at")},
                     "mode": "cache", "tier": "instant"}}


def _with_research(result: dict, rs, text: str, lang: str, monster: dict | None, task_id: int, allow: bool,
                   from_memory: bool = True, source: str = "model knowledge") -> dict:
    """Прямой ответ из знаний модели на вопрос про актуальное: честная пометка + предложение/запуск исследования Sokosumi.
    from_memory=False — ответа из памяти нет (вопрос целиком про живые данные): только предложение агента.
    source — откуда ответ («model knowledge», «built-in data of the organ»): пометка говорит это честно."""
    cs = lang == "cs"
    if not from_memory and rs.available() and not allow:
        offer = rs.questions.offer(text, task_id)
        note = (("Tohle potřebuje průzkum aktuálních, skutečných zdrojů — kód kvůli tomu stavět nebudu. Udělá to najatý agent Sokosumi (níže)."
                 if cs else "This needs research in current, real sources, so I won't write code for it. A hired Sokosumi agent does that (below).")
                if offer else ("Tohle potřebuje aktuální data; živý průzkum teď není k dispozici (limit kreditů)." if cs else
                               "This needs current data; live research is not available now (credit limits)."))
        return {**result, "summary": {"en": note, "cs": note},
                "data": {**result["data"], "offer": offer, "research": {"available": True, "started": False}}}
    src = {"model knowledge": "ze znalostí modelu", "built-in data of the organ": "z vestavěných dat orgánu"}.get(source, source) if cs else source
    if not rs.available():
        note = (f"Odpověď je {src}, ne z živých dat (Sokosumi není nastaveno)." if cs else
                f"This answer is from {src}, not live data (Sokosumi is not configured).")
        offer, started = None, False
    elif allow:
        started = rs.questions.start(text, lang, (monster or {}).get("id"), task_id)
        offer = None
        note = (("Agent Sokosumi zkoumá aktuální zdroje; jeho zpráva přijde jako nová odpověď." if cs else
                 "A Sokosumi agent is researching current sources; its report will arrive as a new answer.") if started else
                ("Průzkum nelze spustit (běží nebo limit kreditů)." if cs else "Research could not start (already running or credit limits)."))
    else:
        offer, started = rs.questions.offer(text, task_id), False
        note = ((f"Odpověď je {src}. Pro aktuální data ze skutečných zdrojů můžeš najmout agenta Sokosumi (níže)." if cs else
                 f"This answer is from {src}. For current data from real sources you can hire a Sokosumi agent (below).")
                if offer else (f"Odpověď je {src}; živý průzkum teď není k dispozici (limit kreditů)." if cs else
                               f"This answer is from {src}; live research is not available now (credit limits)."))
    s = result.get("summary")
    s = {k: f"{v}\n\n> ℹ {note}" for k, v in s.items()} if isinstance(s, dict) else f"{s}\n\n> ℹ {note}"
    return {**result, "summary": s, "data": {**result["data"], "offer": offer,
                                             "research": {"available": rs.available(), "started": started}}}


TIER = {"cache": "instant", "deterministic": "instant", "light": "light", "partial": "smart", "smart": "smart"}


def _confirm_result(d: dict) -> dict:
    """Ответ «нужно подтверждение»: что нашлось, чего не хватает и сколько обычно стоит сборка (по замерам)."""
    cost = d.get("estimated_cost") or {}
    est = (f" A build usually costs ~{cost['tokens']} tokens, {cost['llm_calls']} AI calls and ~{d.get('estimated_latency')} s "
           f"({cost['basis']})." if cost else " There is no measured build yet to estimate the cost.")
    en = f"### Confirmation needed\n\n{d.get('reasoning_summary', '')}{est}\n\nNothing was built. Press **Build it** to confirm."
    cs = (f"### Je potřeba potvrzení\n\n{d.get('reasoning_summary', '')}{est}\n\nNic nebylo postaveno. "
          "Potvrďte tlačítkem **Postavit**.")
    return {"summary": {"en": en, "cs": cs}, "data": {"decision": d}, "files": []}


class _Done(Exception):
    """Задача завершена досрочно и успешно (например, отчёт об обслуживании органов)."""


def _maintenance_report(mutations: list) -> dict:
    rows = [{"organ": m.name, "new_version": m.version, "improved": m.accepted,
             "parent_score": m.parent_score, "child_score": m.child_score, "verdict": m.reason} for m in mutations]
    better = [m.name for m in mutations if m.accepted]
    kept = [m.name for m in mutations if not m.accepted]
    en = (f"I examined {len(mutations)} organ(s). " + (f"Improved: {', '.join(better)}. " if better else "") +
          (f"Kept unchanged (already passing all their tests or the change was not better): {', '.join(kept)}." if kept else ""))
    cs = (f"Prověřil jsem {len(mutations)} orgán(y). " + (f"Vylepšeno: {', '.join(better)}. " if better else "") +
          (f"Ponecháno beze změny (už procházejí všemi testy nebo změna nebyla lepší): {', '.join(kept)}." if kept else ""))
    return {"summary": {"en": en.strip(), "cs": cs.strip()}, "data": {"maintenance": rows}, "files": []}


def speakable(md: str) -> str:
    """Markdown -> текст для голоса: без #, **, `, ссылок и разделителей таблиц; строки таблицы — через запятую."""
    md = re.sub(r"```.*?```", " ", str(md), flags=re.S)
    lines = [ln.strip() for ln in md.splitlines()]
    sep = re.compile(r"\|?[\s:|-]+\|?")
    out, header = [], None
    for i, line in enumerate(lines):
        if not line or sep.fullmatch(line):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if i + 1 < len(lines) and sep.fullmatch(lines[i + 1] or "x"):
                header = cells                         # шапку не читаем — она станет подписями значений
                continue
            if header and len(header) == len(cells):   # «2026-10-10: overcast, max 18°C, min 6°C» вместо «Date, Conditions, Max…»
                head, *rest = [c for c in cells]
                labels = [h.lower().rstrip(".") for h in header[1:]]
                parts = [v if lab in ("conditions", "condition", "weather", "description") else f"{lab} {v}"
                         for lab, v in zip(labels, rest) if v]
                line = f"{head}: " + ", ".join(parts) if head else ", ".join(parts)
            else:
                line = ", ".join(c for c in cells if c)
        else:
            header = None
        line = re.sub(r"^#{1,6}\s*|^[-*+]\s+|^>\s*", "", line)
        line = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"[*_`]{1,3}", "", line).strip()
        if line:
            out.append(line if line[-1] in ".!?:;," else line + ".")
    return " ".join(out)


def _short(summary, limit: int = 420):
    """Резюме для озвучки и события: Markdown -> речь, обрезаем, чтобы не тратить символы ElevenLabs."""
    if isinstance(summary, dict):
        return {k: speakable(v)[:limit] for k, v in summary.items()}
    return speakable(summary)[:limit] if summary else None


def _slim(bench: dict | None) -> dict | None:
    return {k: v for k, v in bench.items() if k != "per_capability"} if bench else None
