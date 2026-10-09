"""Строитель капабилити — сердце цикла самостроительства.

    SPECS -> ОДИН запрос: код+тесты всех новых органов -> статическая проверка -> песочница -> тесты
                ^                                                                   |
                |                   падение: ОДИН запрос «диагноз + ремонт» на все упавшие органы
                +-------------------------------------------------------------------+   (до MAX_REPAIRS раз)
    все тесты зелёные  ->  УСТАНОВКА в реестр.   Не прошло -> ОТКАЗ, ничего не устанавливается.

Непротестированный код не устанавливается никогда.

ЭКОНОМИЯ ВЫЗОВОВ (каждый вызов «мозга» стоит кредитов и минут): раньше N органов = N запросов на генерацию
+ 2 запроса на каждый ремонт. Теперь: 1 запрос на генерацию ВСЕХ органов + 1 запрос на ремонт ВСЕХ упавших.
"""
import ast
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import config, prompts
from .db import loads
from .events import EventBus
from .llm import LLM, LLMError
from .registry import Registry
from .safety import check_source, valid_name
from .sandbox import Sandbox

SECTION_RE = re.compile(r"^=====(FILE|DIAGNOSIS):\s*(.+?)\s*=====\s*$", re.M)


# ----------------------------------------------------------------------------
# Разбор ответов модели
# ----------------------------------------------------------------------------
def parse_sections(text: str) -> list[tuple[str, str, str]]:
    """=====FILE: x/y.py===== ... -> [(kind, name, body)]; конец — маркер =====END=====."""
    parts = SECTION_RE.split(text)
    out = []
    for i in range(1, len(parts) - 2, 3):
        body = parts[i + 2].split("=====END=====")[0].strip("\n")
        m = re.match(r"^```[a-zA-Z]*\n(.*?)\n```\s*$", body, re.S)   # убираем ```python ... ```
        out.append((parts[i], parts[i + 1].strip(), (m.group(1) if m else body).rstrip() + "\n"))
    return out


def parse_files(text: str) -> dict[str, str]:
    """Только FILE-секции: путь -> содержимое."""
    return {name: body for kind, name, body in parse_sections(text) if kind == "FILE"}


def group_files(files: dict[str, str]) -> dict[str, dict[str, str]]:
    """{'pdf_reader/capability.py': ...} -> {'pdf_reader': {'capability.py': ...}}. Файлы без папки — ключ ''."""
    grouped: dict[str, dict[str, str]] = {}
    for path, body in files.items():
        folder, _, base = path.rpartition("/")
        grouped.setdefault(folder, {})[base] = body
    return grouped


def count_tests(test_source: str) -> int:
    """Сколько тестовых методов в сгенерированном файле (считаем по AST)."""
    try:
        tree = ast.parse(test_source)
    except SyntaxError:
        return 0
    return sum(1 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"))


@dataclass
class ValidationResult:
    ok: bool
    report: dict                 # {total, passed, tests[], duration, ...}
    problems: str                # текст для диагноза (пусто, если ok)
    failures: list[dict] = field(default_factory=list)


@dataclass
class BuildOutcome:
    ok: bool
    name: str
    version: str | None = None
    repairs: int = 0
    report: dict = field(default_factory=dict)
    reason: str | None = None


@dataclass
class Candidate:
    """Орган, который строится прямо сейчас (ещё не установлен)."""
    spec: dict
    code: str
    tests: str
    readme: str = ""
    deps: dict[str, str] = field(default_factory=dict)   # исходники зависимостей из реестра
    repairs: int = 0
    ok: bool = False
    dirty: bool = True            # нужна (пере)проверка
    report: dict = field(default_factory=lambda: {"total": 0, "passed": 0})
    problems: str = ""
    failures: list[dict] = field(default_factory=list)
    reason: str | None = None

    @property
    def name(self) -> str:
        return self.spec["name"]


class Builder:
    def __init__(self, llm: LLM, sandbox: Sandbox, registry: Registry, bus: EventBus):
        self.llm, self.sandbox, self.registry, self.bus = llm, sandbox, registry, bus

    def _emit(self, name: str, task_id: int | None, event: str, **data) -> None:
        self.bus.emit(event, capability=name, task_id=task_id, **data)

    # ------------------------------------------------------------------ публичный вход
    def build(self, spec: dict, *, task_id: int | None = None, origin: str = "created",
              parent: str | None = None) -> BuildOutcome:
        return self.build_many([spec], task_id=task_id, origin=origin, parents={spec["name"]: parent})[0]

    def build_many(self, specs: list[dict], *, task_id: int | None = None, origin: str = "created",
                   parents: dict[str, str | None] | None = None) -> list[BuildOutcome]:
        """Строит все specs за минимум вызовов модели. Результаты — в порядке specs."""
        parents = parents or {}
        outcomes: dict[str, BuildOutcome] = {}
        valid: list[dict] = []
        for s in specs:
            if not valid_name(s["name"]):
                self._emit(s["name"], task_id, "CAPABILITY_REJECTED", reason=f"invalid capability name '{s['name']}'")
                outcomes[s["name"]] = BuildOutcome(False, s["name"], reason="invalid name")
            else:
                valid.append(s)
                self._emit(s["name"], task_id, "CAPABILITY_BUILD_STARTED", spec=_public_spec(s), origin=origin)
                self._emit(s["name"], task_id, "CAPABILITY_BUILD_PROGRESS", stage="implementation", status="start")

        # 1) ГЕНЕРАЦИЯ: один запрос на все органы
        cands = self._generate(valid, task_id) if valid else []
        for c in cands:
            self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="implementation", status="done")
            self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="tests", status="start")
            self._emit(c.name, task_id, "CAPABILITY_TESTS_GENERATED", count=count_tests(c.tests))
            self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="tests", status="done")
        got = {c.name for c in cands}
        for s in valid:
            if s["name"] not in got:
                self._emit(s["name"], task_id, "CAPABILITY_REJECTED", reason="the model did not return files for this capability")
                outcomes[s["name"]] = BuildOutcome(False, s["name"], reason="no files returned")

        # 2) ПЕСОЧНИЦА + РЕМОНТ (раунды)
        self._run_rounds(cands, task_id, config.MAX_REPAIRS)

        # 3) УСТАНОВКА (в порядке зависимостей) или ОТКАЗ
        installed: set[str] = set()
        for c in cands:
            blocked = [d for d in c.spec.get("dependencies", []) if d in got and d not in installed]
            if not c.ok or blocked:
                reason = c.reason or (f"dependency {blocked} was not installed" if blocked else "validation failed")
                c.ok = False
                self._emit(c.name, task_id, "CAPABILITY_REJECTED", reason=reason, repairs=c.repairs)
                outcomes[c.name] = BuildOutcome(False, c.name, repairs=c.repairs, report=c.report, reason=reason)
                continue
            self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="install", status="start")
            info = self.registry.install_new(c.spec, code=c.code, tests=c.tests, readme=c.readme, report=c.report,
                                             repairs=c.repairs, origin=origin, parent=parents.get(c.name))
            installed.add(c.name)
            self._emit(c.name, task_id, "CAPABILITY_INSTALLED", version=info["version"], tests=c.report["total"],
                       passed=c.report["passed"], repairs=c.repairs, origin=origin, parent=parents.get(c.name),
                       dependencies=c.spec.get("dependencies", []))
            outcomes[c.name] = BuildOutcome(True, c.name, info["version"], c.repairs, c.report)
        return [outcomes[s["name"]] for s in specs]

    def validate_and_repair(self, spec: dict, code: str, tests: str, deps: dict[str, str], *,
                            task_id: int | None, max_repairs: int):
        """Для мутаций: тот же цикл, но для одного уже написанного органа. Возвращает (ok, code, tests, report, repairs, reason)."""
        c = Candidate(spec, code, tests, deps=deps)
        self._run_rounds([c], task_id, max_repairs)
        return c.ok, c.code, c.tests, c.report, c.repairs, c.reason

    # ------------------------------------------------------------------ раунды «проверить -> починить»
    def _run_rounds(self, cands: list[Candidate], task_id: int | None, max_repairs: int) -> None:
        by_name = {c.name: c for c in cands}
        repairs_done = 0
        while True:
            todo = [c for c in cands if c.dirty]
            for c in todo:
                self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="sandbox", status="start", attempt=c.repairs)
                self._emit(c.name, task_id, "CAPABILITY_TEST_STARTED", attempt=c.repairs)
            with ThreadPoolExecutor(max_workers=3) as pool:      # независимые прогоны — параллельно
                results = list(pool.map(lambda c: self.validate(c.spec, c.code, c.tests, self._deps_for(c, by_name)), todo))
            for c, res in zip(todo, results):
                c.dirty, c.ok, c.report, c.problems, c.failures = False, res.ok, res.report, res.problems, res.failures
                if res.ok:
                    self._emit(c.name, task_id, "CAPABILITY_TEST_PASSED", total=c.report["total"], passed=c.report["passed"],
                               duration=c.report.get("duration"), attempt=c.repairs)
                    self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="sandbox", status="done")
                else:
                    self._emit(c.name, task_id, "CAPABILITY_TEST_FAILED", total=c.report.get("total", 0),
                               passed=c.report.get("passed", 0), failed=max(c.report.get("total", 0) - c.report.get("passed", 0), 1),
                               attempt=c.repairs, failures=[{"name": f["name"], "message": f["message"][-300:]} for f in c.failures[:6]],
                               problems=c.problems[-400:])
                    self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="sandbox", status="failed")

            failing = [c for c in cands if not c.ok]
            if not failing:
                return
            if repairs_done >= max_repairs:
                for c in failing:
                    c.reason = "could not satisfy validation requirements: " + c.problems[-300:]
                return

            # ОДИН запрос «диагноз + ремонт» на все упавшие органы
            for c in failing:
                self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="repair", status="start")
            raw = self.llm.text(prompts.REPAIR_SYSTEM, prompts.repair_user(
                [{"spec": c.spec, "code": c.code, "tests": c.tests, "problems": c.problems} for c in failing]), purpose="repairing")
            repairs_done += 1
            sections = parse_sections(raw)
            diag = {n: loads(b) for k, n, b in sections if k == "DIAGNOSIS"}
            fixed = group_files({n: b for k, n, b in sections if k == "FILE"})
            changed: set[str] = set()
            for c in failing:
                d = diag.get(c.name) or {}
                files = fixed.get(c.name) or (fixed.get("") if len(failing) == 1 else None) or {}
                c.repairs += 1
                if "capability.py" in files or "test_capability.py" in files:
                    c.code = files.get("capability.py", c.code)
                    c.tests = files.get("test_capability.py", c.tests)
                    c.dirty = True
                    changed.add(c.name)
                else:
                    c.dirty = False          # модель не прислала исправление: этот орган останется красным
                diagnosis = d.get("diagnosis") or {"en": "see validation problems", "cs": "viz problémy validace"}
                self._emit(c.name, task_id, "CAPABILITY_DIAGNOSIS", diagnosis=diagnosis,
                           strategy=d.get("strategy") or {"en": "regenerate the failing part", "cs": "přegenerovat selhávající část"},
                           fix_target=d.get("fix_target", "code"))
                # урок в долговременную память — бесплатно, без отдельного вызова модели
                self.registry.add_note(f"{c.name}: {diagnosis.get('en', '')}"[:300])
                self._emit(c.name, task_id, "CAPABILITY_REPAIR", attempt=c.repairs, tests_changed="test_capability.py" in files)
                self._emit(c.name, task_id, "CAPABILITY_BUILD_PROGRESS", stage="repair", status="done")
            if not changed:                  # ремонт не дал нового кода — повторять бессмысленно
                for c in failing:
                    c.reason = "repair produced no new code: " + c.problems[-300:]
                return
            # органы пакета, зависящие от переписанных, нужно проверить заново
            for c in cands:
                if any(d in changed for d in self._batch_deps(c, by_name)):
                    c.dirty = True

    def _batch_deps(self, c: Candidate, by_name: dict[str, Candidate]) -> set[str]:
        """Какие органы ЭТОГО ЖЕ пакета нужны данному (транзитивно)."""
        seen: set[str] = set()
        stack = [d for d in c.spec.get("dependencies", []) if d in by_name]
        while stack:
            d = stack.pop()
            if d not in seen:
                seen.add(d)
                stack += [x for x in by_name[d].spec.get("dependencies", []) if x in by_name]
        return seen

    def _deps_for(self, c: Candidate, by_name: dict[str, Candidate]) -> dict[str, str]:
        """Исходники всего, что нужно для тестов: установленные зависимости + ещё не установленные органы пакета."""
        deps = dict(c.deps)
        installed = [d for d in c.spec.get("dependencies", []) if d not in by_name]
        deps.update(self.registry.collect_dependencies(installed))
        for d in self._batch_deps(c, by_name):
            deps[d] = by_name[d].code
            deps.update(self.registry.collect_dependencies([x for x in by_name[d].spec.get("dependencies", []) if x not in by_name]))
        return deps

    # ------------------------------------------------------------------ проверка одного органа
    def validate(self, spec: dict, code: str, tests: str, deps: dict[str, str]) -> ValidationResult:
        """Статическая проверка + запуск тестов в песочнице."""
        allowed = set(deps) | set(spec.get("dependencies", []))
        network = bool(spec.get("network"))
        problems = check_source(code, allowed_capabilities=allowed, network=network)
        problems += ["tests: " + p for p in check_source(tests, allowed_capabilities=allowed | {"capability"}, network=network)]
        if problems:
            return ValidationResult(False, {"total": 0, "passed": 0}, "static check failed:\n- " + "\n- ".join(problems))

        res = self.sandbox.run_tests(code=code, tests=tests, dependencies=deps, network=network)
        report = res.report or {}
        if res.killed_reason:
            return ValidationResult(False, {"total": report.get("total", 0), "passed": report.get("passed", 0)},
                                    f"sandbox killed the process: {res.killed_reason} "
                                    f"(limits: {config.SANDBOX_TIMEOUT_SEC}s, {config.SANDBOX_MEMORY_MB}MB). stderr: {res.stderr[-500:]}")
        if not report:
            return ValidationResult(False, {"total": 0, "passed": 0}, f"runner produced no report. stderr: {res.stderr[-800:]}")
        if report.get("load_error"):
            return ValidationResult(False, report, "tests failed to load:\n" + report["load_error"])
        failures = [t for t in report.get("tests", []) if t["status"] != "passed"]
        if failures:
            text = "\n\n".join(f"[{t['status'].upper()}] {t['name']}\n{t['message'][-700:]}" for t in failures[:8])
            return ValidationResult(False, report, f"{len(failures)} of {report['total']} tests did not pass:\n{text}", failures)
        if report["total"] < config.MIN_TESTS:
            return ValidationResult(False, report, f"only {report['total']} tests; at least {config.MIN_TESTS} are required "
                                                   f"(cover basic, edge, invalid, empty, unicode, large, malformed cases)")
        return ValidationResult(True, report, "")

    # ------------------------------------------------------------------ генерация
    def _generate(self, specs: list[dict], task_id: int | None) -> list[Candidate]:
        """Один запрос на все specs. Если модель что-то пропустила — один дозапрос только на недостающие."""
        # исходники уже установленных зависимостей (для всех specs сразу)
        installed_deps = sorted({d for s in specs for d in s.get("dependencies", [])} - {s["name"] for s in specs})
        dep_src = self.registry.collect_dependencies([d for d in installed_deps if d in self.registry.active_names()])
        cands: dict[str, Candidate] = {}
        remaining = list(specs)
        for _ in range(2):
            raw = self.llm.text(prompts.BUILD_SYSTEM, prompts.build_user(remaining, dep_src, self.registry.notes(8)), purpose="building")
            grouped = group_files(parse_files(raw))
            for s in remaining:
                f = grouped.get(s["name"]) or (grouped.get("") if len(remaining) == 1 else None) or {}
                if "capability.py" in f and "test_capability.py" in f:
                    cands[s["name"]] = Candidate(s, f["capability.py"], f["test_capability.py"], f.get("README.md", ""), deps=dep_src)
            remaining = [s for s in specs if s["name"] not in cands]
            if not remaining:
                break
        return [cands[s["name"]] for s in specs if s["name"] in cands]


def _public_spec(spec: dict) -> dict:
    return {k: spec.get(k) for k in ("name", "description", "purpose_en", "purpose_cs", "inputs", "outputs",
                                     "dependencies", "network")}
