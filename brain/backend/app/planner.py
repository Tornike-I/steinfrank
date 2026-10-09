"""Планировщик: понимает задачу, находит НЕДОСТАЮЩИЕ капабилити и решает REUSE / COMPOSE / MUTATE / CREATE.

Никаких правил вида «если в задаче слово PDF — создай pdf_reader». Модель сама сравнивает задачу с
реестром (по смыслу, входам/выходам, надёжности), а код здесь лишь ПРОВЕРЯЕТ её ответ:
выдуманные капабилити, неверные зависимости и некорректные имена отклоняются, модель переспрашивается.
"""
from . import config, prompts
from .llm import LLM
from .registry import Registry
from .safety import valid_name

DECISIONS = {"REUSE", "COMPOSE", "MUTATE", "CREATE"}


class Planner:
    def __init__(self, llm: LLM, registry: Registry):
        self.llm, self.registry = llm, registry

    def analyze(self, task_text: str, files: list[dict], monster: dict | None = None, team: list[dict] | None = None) -> dict:
        existing = self.registry.active_names()
        recipes = self.registry.recipes_for_llm()
        recipe_ids = {r["id"] for r in recipes}
        recipe_params = {r["id"]: r.get("params") for r in recipes}
        summary, notes = self.registry.summary_for_llm(task_text), self.registry.notes(8)
        # ключ кэша — СТАБИЛЬНАЯ часть запроса: без имени монстра и его памяти (они в промпте лишь как контекст), иначе
        # тот же вопрос другому/повзрослевшему монстру никогда не совпал бы с кэшем
        stable = {**monster, "name": "", "memory": []} if monster else None
        plan = self.llm.json(
            prompts.PLAN_SYSTEM,
            prompts.plan_user(task_text, files, summary, recipes, notes, monster,
                              network_allowed=config.ALLOW_NETWORK_CAPABILITIES, team=team),
            validate=lambda obj: self._validate(self._normalize(obj, existing), existing, recipe_ids, recipe_params),
            purpose="planning", retries=2,
            cache_text=prompts.plan_user(task_text, files, summary, recipes, notes, stable,
                                         network_allowed=config.ALLOW_NETWORK_CAPABILITIES, team=team))
        plan.setdefault("requirements", [])
        plan["expects_files"] = bool(plan.get("expects_files"))
        for r in plan["requirements"]:
            r["capabilities"] = r.get("capabilities") or []
            r["status"] = "pending"
        return plan

    @staticmethod
    def _normalize(plan: dict, existing: set[str]) -> dict:
        """Чиним КОДОМ очевидные огрехи плана, чтобы не платить за повторный вызов модели:
        REUSE/COMPOSE несуществующего органа при наличии spec -> CREATE; «REUSE ничего» без spec -> требование убирается."""
        fixed = []
        for r in plan.get("requirements") or []:
            if not isinstance(r, dict):
                continue
            d = str(r.get("decision", "")).strip().upper()
            caps = [c for c in (r.get("capabilities") or []) if isinstance(c, str)]
            known = [c for c in caps if c in existing]
            if d in ("REUSE", "COMPOSE", "MUTATE") and len(known) < len(caps or [None]):
                if isinstance(r.get("spec"), dict):
                    d, known = "CREATE", []
                elif d in ("REUSE", "COMPOSE") and not known:
                    continue                     # «использовать ничего» — это не требование
            r["decision"], r["capabilities"] = d, known if d != "CREATE" else caps
            fixed.append(r)
        plan["requirements"] = fixed
        if not isinstance(plan.get("params"), dict):
            plan["params"] = {}          # параметры ТЕКУЩЕГО запроса (города, роль, даты…) — воркфлоу читает их отсюда
        return plan

    @staticmethod
    def _validate(plan: dict, existing: set[str], recipe_ids: set[int] | None = None,
                  recipe_params: dict | None = None) -> list[str]:
        """Проверяем план. Возвращаем список ошибок — модель получит их и исправится."""
        errors: list[str] = []
        rid = plan.get("recipe")
        if rid is not None and (recipe_ids is None or rid not in recipe_ids):
            errors.append(f'"recipe" must be null or one of the listed recipe ids {sorted(recipe_ids or [])}')
        elif rid is not None and (recipe_params or {}).get(rid):
            want = sorted(recipe_params[rid])
            if sorted(plan.get("params") or {}) != want:
                errors.append(f'recipe {rid} reads params {want}: fill exactly these names in "params" with the values of THIS '
                              f'request (got {sorted(plan.get("params") or {})}), or set "recipe": null')
        if plan.get("intent") not in ("task", "self_improve", "learn"):
            errors.append('"intent" must be "task", "learn" or "self_improve"')
        if plan.get("intent") == "learn" and not plan.get("requirements"):
            errors.append('"learn" needs at least one requirement (CREATE the general skill, or REUSE an installed one)')
        if not isinstance(plan.get("understanding"), dict):
            errors.append('"understanding" must be {"en","cs"}')
        if plan.get("intent") == "task" and not plan.get("requirements"):
            da = plan.get("direct_answer")
            if not (isinstance(da, dict) and str(da.get("en", "")).strip()):
                errors.append('no requirements were given: either list the abilities the task needs (CREATE them - the registry '
                              f'has {len(existing)} organs), or, if no tool is needed at all, set "requirements": [] and give '
                              '"direct_answer": {"en": "...", "cs": "..."}')
        created: set[str] = set()
        if rid is not None and any(r.get("decision") in ("CREATE", "MUTATE") for r in plan.get("requirements") or []):
            errors.append('"recipe" may be used only when every requirement is REUSE or COMPOSE; set it to null otherwise')
        for i, r in enumerate(plan.get("requirements") or []):
            tag = f"requirement {r.get('id', i)}"
            d = r.get("decision")
            if d not in DECISIONS:
                errors.append(f"{tag}: decision must be one of {sorted(DECISIONS)}")
                continue
            if not isinstance(r.get("need"), dict):
                errors.append(f'{tag}: "need" must be {{"en","cs"}}')
            caps = r.get("capabilities") or []
            if d in ("REUSE", "COMPOSE", "MUTATE"):
                unknown = [c for c in caps if c not in existing]
                if not caps:
                    errors.append(f"{tag}: {d} must name installed capabilities, but none were listed "
                                  f"(installed: {sorted(existing) or 'NONE - the registry is empty'}). Use CREATE with a full spec.")
                elif unknown:
                    errors.append(f"{tag}: {unknown} are not installed (installed: {sorted(existing) or 'NONE'}). "
                                  f"Use CREATE with a full spec instead.")
                if d == "MUTATE" and len(caps) != 1:
                    errors.append(f"{tag}: MUTATE takes exactly one capability")
            if d == "CREATE":
                spec = r.get("spec")
                if not isinstance(spec, dict):
                    errors.append(f"{tag}: CREATE needs a full spec")
                    continue
                name = spec.get("name", "")
                if not valid_name(name):
                    errors.append(f"{tag}: invalid capability name '{name}' (snake_case, 3-40 chars, not a stdlib name)")
                if name in existing:
                    errors.append(f"{tag}: '{name}' already exists - use REUSE or MUTATE")
                for k in ("description", "purpose_en", "purpose_cs", "inputs", "outputs"):
                    if k not in spec:
                        errors.append(f"{tag}: spec is missing '{k}'")
                spec.setdefault("dependencies", [])
                spec.setdefault("network", False)
                if spec["network"] and not config.ALLOW_NETWORK_CAPABILITIES:
                    errors.append(f"{tag}: network access is disabled by the operator (ALLOW_NETWORK_CAPABILITIES=false); "
                                  f"design '{name}' to work offline with the provided files")
                bad = [x for x in spec["dependencies"] if x not in existing and x not in created]
                if bad:
                    errors.append(f"{tag}: dependencies {bad} are neither installed nor created earlier in the list")
                created.add(name)
        return errors
