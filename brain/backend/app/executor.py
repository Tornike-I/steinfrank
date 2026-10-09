"""Исполнитель: решает задачу, КОМПОНУЯ установленные капабилити.

COMPOSE реализован буквально: модель пишет короткий workflow.py, который импортирует нужные
капабилити как модули и вызывает их run(). Этот «временный орган» запускается в той же
песочнице. Трассировка внутри sandbox_boot.py фиксирует КАЖДЫЙ вызов капабилити — отсюда
берётся реальная статистика использования и поиск повторяющихся паттернов.

ЭКОНОМИЯ ВЫЗОВОВ «МОЗГА» (каждый вызов Sokosumi = кредиты + минуты):
  1. РЕЦЕПТ: удачный воркфлоу сохраняется. Если планировщик видит задачу того же рода — воркфлоу
     запускается снова с НОЛЕМ вызовов модели (только песочница).
  2. Сжатый контекст: модель получает описание органов (docstring + ключи результата, найденные по AST),
     а полный исходный код — только на повторной попытке.
  3. Проверка результата — КОДОМ (summary, данные, файлы), а не отдельным запросом-судьёй.
  Качество не страдает: рецепт проходит те же проверки, а при любой неудаче срабатывает обычный путь
  «диагноз -> починка -> повтор» (не более MAX_WORKFLOW_ATTEMPTS попыток).
"""
import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config, prompts
from . import recipes as recipe_rules
from .builder import Builder, parse_files
from .llm import save_debug
from .events import EventBus
from .llm import LLM
from .planner import Planner
from .registry import Registry
from .sandbox import Sandbox


@dataclass
class ExecutionResult:
    ok: bool
    output: dict = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    error: str | None = None
    attempts: int = 0
    evolved: bool = False        # менялся ли организм во время выполнения (новые органы / мутации)
    recipe_used: bool = False    # задача решена рецептом без вызовов модели для воркфлоу
    workflow_code: str = ""


def return_keys(code: str) -> list[str]:
    """Какие ключи возвращает run(): ищем `return {...}` с литеральными ключами (AST). Это точнее описания от планировщика."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    keys: list[str] = []
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "run"]:
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and k.value not in keys:
                        keys.append(k.value)
    return keys


def extract_workflow(raw: str) -> str | None:
    """workflow.py из ответа модели: по нашим маркерам, а если модель их потеряла — из блока ```python с def main(."""
    code = parse_files(raw).get("workflow.py")
    if code:
        return code
    for block in re.findall(r"```(?:python|py)?\s*\n(.*?)```", raw, re.S):
        if "def main(" in block:
            return block.strip() + "\n"
    return None


def check_output(output: dict, out_files: list[str], expects_files: bool) -> list[str]:
    """Детерминированная проверка результата (замена платного «судьи»). Пустой список = результат приемлем."""
    problems = []
    summary = output.get("summary")
    if not summary or (isinstance(summary, dict) and not any(str(v).strip() for v in summary.values())):
        problems.append("result has no summary")
    data = output.get("data")
    if data in (None, "", [], {}):
        problems.append("result has no data")
    elif isinstance(data, dict) and data.get("problems") and not any(v for k, v in data.items() if k != "problems"):
        problems.append(f"only problems were reported: {str(data['problems'])[:200]}")
    if expects_files and not out_files:
        problems.append("the task needs a file deliverable, but no file was created")
    for f in output.get("files") or []:
        if Path(f).name not in {Path(x).name for x in out_files}:
            problems.append(f"declared file '{f}' was not created")
    return problems


class NeedsConfirmation(Exception):
    """Починить/построить орган после ошибки воркфлоу можно только с подтверждения пользователя (барьер gate.py)."""

    def __init__(self, decision: dict):
        super().__init__(decision.get("reasoning_summary", "confirmation needed"))
        self.decision = decision


class Executor:
    def __init__(self, llm: LLM, sandbox: Sandbox, registry: Registry, bus: EventBus, builder: Builder, evolution):
        self.llm, self.sandbox, self.registry, self.bus = llm, sandbox, registry, bus
        self.builder, self.evolution = builder, evolution

    def run(self, task: dict, understanding: str, input_files: list[Path], *, task_id: int,
            expects_files: bool = False, recipe_id: int | None = None, may_evolve: bool = True) -> ExecutionResult:
        """may_evolve=False: после ошибки воркфлоу НЕ чинить и НЕ строить органы молча — вместо этого NeedsConfirmation."""
        self._may_evolve = may_evolve
        artifacts_dir = config.ARTIFACTS_DIR / str(task_id)
        error: str | None = None
        code: str | None = None
        evolved = False

        # ---- 0) РЕЦЕПТ: пробуем сохранённый воркфлоу (0 вызовов модели) ----
        # Рецепт — это ПРОЦЕДУРА. Если его код зашил значения прошлого запроса (город, роль…), он не годится для
        # запроса с другими значениями: тогда не запускаем его вовсе (иначе вернулся бы чужой результат).
        rec = self.registry.recipe(recipe_id) if recipe_id is not None else None
        why_not = recipe_rules.guard(rec, task.get("params"), task["text"]) if rec else None
        if rec and why_not:
            self.bus.emit("RECIPE_REJECTED", task_id=task_id, recipe=recipe_id, reason=why_not[:300])
            rec = None
        if rec:
            self.bus.emit("WORKFLOW_RECIPE_REUSED", task_id=task_id, recipe=recipe_id, description=rec["description"][:120])
            code = rec["code"]
            res, error = self._execute(code, task, input_files, artifacts_dir, task_id, 0, expects_files)
            leaked = recipe_rules.contamination(rec, task.get("params"), res.output) if res else []
            if leaked:
                # результат описывает ПРОШЛЫЙ запрос — это не ответ на текущий
                error = (f"the result mentions {leaked} from an earlier request instead of this request's values "
                         f"{task.get('params')}; read the values from task['params']")
                self.bus.emit("RESULT_MISMATCH", task_id=task_id, recipe=recipe_id, leaked=leaked[:10])
                res = None
            self.registry.recipe_used(recipe_id, res is not None)
            if res:
                res.recipe_used = True
                return res
            # рецепт не подошёл — ниже модель перепишет его с учётом ошибки

        for attempt in range(1, config.MAX_WORKFLOW_ATTEMPTS + 1):
            # ---- 1) модель собирает воркфлоу из ТЕКУЩИХ органов ----
            self.bus.emit("WORKFLOW_COMPOSING", task_id=task_id, attempt=attempt)
            raw = self.llm.text(prompts.WORKFLOW_SYSTEM,
                                prompts.workflow_user({**task, "files": [f"inputs/{p.name}" for p in input_files]},
                                                      self._caps_context(), understanding, error, code,
                                                      full_source=error is not None), purpose="composing")
            code = extract_workflow(raw)
            if not code:
                save_debug("composing", raw)
                said = " ".join(raw.split())[:240]
                error = (f"your previous answer contained no workflow.py. You wrote: \"{said}\". "
                         "Reply ONLY with the file in the required format, even if the workflow is small.")
                continue
            res, error = self._execute(code, task, input_files, artifacts_dir, task_id, attempt, expects_files)
            if res:
                res.evolved, res.workflow_code = evolved, code
                return res
            if attempt == config.MAX_WORKFLOW_ATTEMPTS:
                break
            # ---- 2) ДИАГНОЗ и реакция (вызов модели только при настоящей неудаче) ----
            evolved |= self._react_to_failure(task, error, code, task_id)

        if error and error.startswith("your previous answer contained no workflow.py"):
            error = ("the brain did not write workflow code (its answer is saved in backend/data/llm_last_failure.txt). "
                     "Last reply: " + error.split("You wrote: ", 1)[-1].split(". Reply ONLY")[0])
        return ExecutionResult(False, error=error, attempts=config.MAX_WORKFLOW_ATTEMPTS, evolved=evolved)

    # ------------------------------------------------------------------ один запуск воркфлоу
    def _execute(self, code: str, task: dict, input_files: list[Path], artifacts_dir: Path, task_id: int,
                 attempt: int, expects_files: bool) -> tuple[ExecutionResult | None, str | None]:
        """Запускает воркфлоу в песочнице и проверяет результат. Возвращает (успех, None) или (None, текст ошибки)."""
        self.bus.emit("WORKFLOW_RUNNING", task_id=task_id, attempt=attempt)
        active = self.registry.active()
        sources = self.registry.collect_dependencies([c["name"] for c in active])
        result, _ = self.sandbox.run_workflow(
            workflow_code=code, capabilities=sources, task=task, input_files=input_files,
            artifacts_dir=artifacts_dir, network=any(c["network"] for c in active))
        report = result.report or {}
        calls = report.get("calls", [])
        if report.get("ok"):
            problems = check_output(report["output"], result.out_files, expects_files)
            if not problems:
                return ExecutionResult(True, report["output"], calls, result.out_files, None, attempt, workflow_code=code), None
            error = "result failed quality checks: " + "; ".join(problems)
        else:
            error = report.get("traceback") or report.get("error") or \
                (f"sandbox killed: {result.killed_reason}" if result.killed_reason else result.stderr[-1500:])
        self._record_runs(calls, task_id)
        self.bus.emit("WORKFLOW_FAILED", task_id=task_id, attempt=attempt, error=error[-500:])
        return None, error

    def _caps_context(self) -> dict[str, dict]:
        out = {}
        for c in self.registry.active():
            info = self.registry.code_of(c["name"])
            doc = ast.get_docstring(ast.parse(info["code"])) or ""
            out[c["name"]] = {**c, "purpose": c["purpose_en"] or c["description"], "code": info["code"],
                              "doc": doc[:600], "return_keys": return_keys(info["code"])}
        return out

    # ------------------------------------------------------------------ реакция на провал
    def _react_to_failure(self, task: dict, error: str, code: str, task_id: int) -> bool:
        """Возвращает True, если организм изменился (построена капабилити / мутация)."""
        def validate(obj: dict) -> list[str]:
            errs = []
            if obj.get("kind") not in ("workflow_bug", "capability_bug", "missing_capability"):
                errs.append("kind must be workflow_bug | capability_bug | missing_capability")
            if obj.get("kind") == "capability_bug" and obj.get("capability") not in self.registry.active_names():
                errs.append(f"capability_bug must name an installed capability: {sorted(self.registry.active_names())}")
            if obj.get("kind") == "missing_capability":
                errs += [e for e in Planner._validate({"intent": "task", "understanding": {}, "requirements": [
                    {"id": "fix", "need": {}, "decision": "CREATE", "spec": obj.get("spec")}]},
                    self.registry.active_names()) if "understanding" not in e]
            return errs

        diag = self.llm.json(
            prompts.FAILURE_SYSTEM,
            f"TASK: {task['text']}\n\nERROR / PROBLEM:\n{error[-1800:]}\n\nWORKFLOW:\n{code}\n\n"
            f"INSTALLED: {prompts.compact(self.registry.summary_for_llm(task['text'])['detailed'])}",
            validate=validate, purpose="diagnosing")
        self.bus.emit("FAILURE_DIAGNOSED", task_id=task_id, kind=diag["kind"], capability=diag.get("capability"),
                      diagnosis=diag.get("diagnosis"))
        en = (diag.get("diagnosis") or {}).get("en")
        if en:
            self.registry.add_note(f"workflow: {en}"[:300])      # бесплатный урок в память

        if diag["kind"] in ("missing_capability", "capability_bug") and not getattr(self, "_may_evolve", True):
            # БАРЬЕР: это дорогая операция (минуты, тысячи токенов) — спрашиваем, а не делаем молча
            name = diag["spec"]["name"] if diag["kind"] == "missing_capability" else diag["capability"]
            self.bus.emit("FAILURE_NEEDS_CONFIRMATION", task_id=task_id, capability=name, kind=diag["kind"])
            raise NeedsConfirmation({
                "decision": "confirm", "action": "build" if diag["kind"] == "missing_capability" else "repair_skill",
                "selected_skill": [name], "missing_functionality": [name], "missing_knowledge": [],
                "reasoning_summary": (f"The workflow failed: {en or error[-200:]} Fixing it needs "
                                      + (f"a new organ '{name}'." if diag["kind"] == "missing_capability" else f"rewriting the organ '{name}'.")
                                      + " That is the expensive path, so it waits for your confirmation."),
                "user_confirmation_required": True})
        if diag["kind"] == "missing_capability":
            self.bus.emit("CAPABILITY_GAP_DETECTED", task_id=task_id, capability=diag["spec"]["name"],
                          need=diag.get("diagnosis"), source="failure", kind="missing")
            return self.builder.build(diag["spec"], task_id=task_id).ok
        if diag["kind"] == "capability_bug":
            return self.evolution.mutate(diag["capability"], reason=diag.get("reason") or error[-500:], task_id=task_id).accepted
        return False   # workflow_bug: следующая итерация перепишет воркфлоу с учётом ошибки

    # ------------------------------------------------------------------ статистика вызовов
    def _record_runs(self, calls: list[dict], task_id: int) -> None:
        for c in calls:
            self.registry.record_run(c["capability"], version=None, task_id=task_id, input_hash=c.get("input_hash", ""),
                                     success=bool(c.get("ok")), error=c.get("error"), duration=c.get("duration", 0))

    def record_success(self, calls: list[dict], task_id: int) -> None:
        self._record_runs(calls, task_id)
