"""Эволюция: мутации, сравнение версий, самоочистка, поиск паттернов, самоаудит.

Принцип честности: решение «новая версия лучше» принимает ЦИФРА, а не LLM.
  - родитель и потомок гоняются на ОДНОМ И ТОМ ЖЕ наборе тестов в песочнице;
  - потомок не имеет права ломать старые тесты (проверка на регрессию);
  - если измеримого улучшения нет — мутация отклоняется, родитель остаётся.
"""
import time
from dataclasses import dataclass

from . import config, prompts
from .builder import Builder, count_tests, group_files, parse_files
from .db import loads
from .events import EventBus
from .llm import LLM
from .planner import Planner
from .registry import Registry
from .sandbox import Sandbox


@dataclass
class MutationOutcome:
    accepted: bool
    name: str
    version: str | None = None
    parent_score: float | None = None
    child_score: float | None = None
    reason: str = ""


class Evolution:
    def __init__(self, llm: LLM, sandbox: Sandbox, registry: Registry, bus: EventBus, builder: Builder):
        self.llm, self.sandbox, self.registry, self.bus, self.builder = llm, sandbox, registry, bus, builder

    # ======================================================================
    # МУТАЦИЯ: родитель -> потомок -> сравнение -> оставить лучшего
    # ======================================================================
    def mutate(self, name: str, *, reason: str, task_id: int | None = None, probe=None) -> MutationOutcome:
        """probe(code) -> (ok, detail): проверка ПОВЕДЕНИЯ (например, «ответ зависит от параметров»). Если она задана,
        потомок принимается по ней: старые тесты родителя могли закреплять сам дефект (общий ответ для любой роли),
        поэтому их провал здесь не считается регрессией."""
        emit = lambda t, **kw: self.bus.emit(t, capability=name, task_id=task_id, **kw)   # noqa: E731
        info = self.registry.code_of(name)
        if not info:
            return MutationOutcome(False, name, reason="capability is not installed")
        spec = info["spec"]
        parent_row = self.registry.active_version_row(name)
        deps = self.registry.collect_dependencies(spec.get("dependencies", []))

        emit("CAPABILITY_MUTATION_STARTED", reason=reason[:300], parent_version=info["version"])
        raw = self.llm.text(prompts.MUTATE_SYSTEM, prompts.mutate_user(
            name, spec, info["code"], info["tests"], reason, self.registry.failure_history(name), self.registry.notes(8)),
            purpose="mutating")
        grouped = group_files(parse_files(raw))
        files = grouped.get(name) or grouped.get("") or {}
        if "capability.py" not in files or "test_capability.py" not in files:
            emit("CAPABILITY_MUTATED", accepted=False, reason="model returned malformed files")
            return MutationOutcome(False, name, reason="malformed model output")
        emit("CAPABILITY_TESTS_GENERATED", count=count_tests(files["test_capability.py"]), mutation=True)

        # Потомок проходит тот же путь, что и новорождённый: песочница + ремонт (но не более 2 раз)
        ok, code, tests, report, repairs, why = self.builder.validate_and_repair(
            spec, files["capability.py"], files["test_capability.py"], deps, task_id=task_id, max_repairs=2)

        # --- СРАВНЕНИЕ на одном и том же (финальном) наборе тестов ---
        emit("CAPABILITY_BUILD_PROGRESS", stage="compare", status="start")
        parent_run = self.sandbox.run_tests(code=info["code"], tests=tests, dependencies=deps, network=bool(spec.get("network")))
        pr = parent_run.report or {}
        parent_score = (pr.get("passed", 0) / pr["total"]) if pr.get("total") else 0.0
        child_score = (report["passed"] / report["total"]) if report.get("total") else 0.0
        # Регрессия: потомок обязан проходить все ОРИГИНАЛЬНЫЕ тесты родителя
        old = self.sandbox.run_tests(code=code, tests=info["tests"], dependencies=deps, network=bool(spec.get("network")))
        regressed = (old.report or {}).get("passed", 0) < parent_row["test_passed"]

        probe_ok, probe_why = probe(code) if (probe and ok) else (None, "")
        if probe is not None:
            accepted = bool(ok and probe_ok)
        else:
            accepted = ok and not regressed and parent_score < 1.0
        if not ok:
            verdict = f"mutant failed its own validation: {why}"
        elif probe is not None:
            verdict = f"behaviour check {'passed' if probe_ok else 'failed'}: {probe_why}"
        elif regressed:
            verdict = "mutant broke tests the parent passed (regression)"
        elif parent_score >= 1.0:
            verdict = "no measurable improvement: the parent already passes every test"
        else:
            verdict = "mutant passes tests the parent fails"

        version = self.registry.add_version(name, code=code, tests=tests, readme=files.get("README.md", ""),
                                            spec=spec, report=report, repairs=repairs, origin="mutation", score=child_score)
        self.registry.set_version_score(name, info["version"], parent_score)
        if accepted:
            self.registry.promote_version(name, version, old_cause="superseded",
                                          old_note=f"replaced by v{version}: {parent_score:.0%} -> {child_score:.0%}")
        else:
            self.registry.reject_version(name, version, verdict)
        emit("CAPABILITY_MUTATED", accepted=accepted, parent_version=info["version"], child_version=version,
             parent_score=parent_score, child_score=child_score, regressed=regressed, verdict=verdict,
             tests=report.get("total", 0), repairs=repairs, probe=probe_why[:300] if probe else None,
             versions=self.registry.get(name)["versions"])
        return MutationOutcome(accepted, name, version, parent_score, child_score, verdict)

    # ======================================================================
    # САМООЧИСТКА: избыточные и ненадёжные капабилити -> кладбище
    # ======================================================================
    def prune(self, *, task_id: int | None = None) -> list[dict]:
        active = self.registry.active()
        candidates: list[dict] = []

        # Правило 1 (чистая арифметика): много запусков и низкая надёжность
        for c in active:
            if c["usage_count"] >= 3 and c["success_rate"] is not None and c["success_rate"] < 0.5:
                candidates.append({"retire": c["name"], "replaced_by": None, "cause": "low_reliability",
                                   "explanation": {"en": f"success rate {c['success_rate']:.0%} over {c['usage_count']} runs",
                                                   "cs": f"úspěšnost {c['success_rate']:.0%} z {c['usage_count']} spuštění"}})
        # Правило 2 (семантика): просим LLM найти дубликаты, но проверяем его ответ кодом
        if len(active) >= 2:
            names = {c["name"] for c in active}

            def validate(obj: dict) -> list[str]:
                errs = []
                for d in obj.get("decisions", []):
                    if d.get("retire") not in names:
                        errs.append(f"'{d.get('retire')}' is not an active capability")
                    if d.get("replaced_by") not in names or d.get("replaced_by") == d.get("retire"):
                        errs.append(f"replaced_by '{d.get('replaced_by')}' must be a different active capability")
                return errs
            judged = self.llm.json(prompts.REDUNDANCY_SYSTEM, str(self.registry.summary_for_llm(detailed=999)['detailed']), validate=validate, purpose="pruning")
            candidates += judged.get("decisions", [])

        retired = []
        seen = set()
        for d in candidates:
            name = d["retire"]
            if name in seen:
                continue
            seen.add(name)
            affected = [x for x in self.registry.dependents(name) if x not in seen]
            if affected:   # нельзя убивать орган, от которого зависят другие
                self.bus.emit("DEPENDENCY_WARNING", capability=name, task_id=task_id, affected=affected, blocked=True)
                continue
            self.registry.retire(name, d.get("cause", "redundant"), note=(d.get("explanation") or {}).get("en", ""),
                                 replacement=d.get("replaced_by"))
            self.bus.emit("CAPABILITY_RETIRED", capability=name, task_id=task_id, cause=d.get("cause", "redundant"),
                          replaced_by=d.get("replaced_by"), explanation=d.get("explanation"))
            retired.append(d)
        return retired

    def retire_manually(self, name: str) -> dict:
        """Ручное удаление из интерфейса. Зависимые органы блокируют удаление."""
        affected = self.registry.dependents(name)
        if affected:
            self.bus.emit("DEPENDENCY_WARNING", capability=name, affected=affected, blocked=True)
            return {"ok": False, "affected": affected}
        self.registry.retire(name, "manual", note="retired by the operator")
        self.bus.emit("CAPABILITY_RETIRED", capability=name, cause="manual", replaced_by=None, explanation=None)
        return {"ok": True}

    # ======================================================================
    # ПОИСК ПОВТОРЯЮЩИХСЯ ПОСЛЕДОВАТЕЛЬНОСТЕЙ ВЫЗОВОВ
    # ======================================================================
    def detect_pattern(self) -> dict | None:
        """Ищет цепочку капабилити (≥2 шагов), которая повторялась минимум в двух разных задачах."""
        # trace пишется только при успешном выполнении, причём ДО смены статуса задачи на completed —
        # поэтому смотрим на наличие trace, а не на статус (иначе паттерн нашёлся бы на задачу позже)
        rows = self.registry.db.query("SELECT id, trace FROM tasks WHERE trace IS NOT NULL ORDER BY id")
        sequences = []
        for r in rows:
            seq = [n for n in (loads(r["trace"], []) or [])]
            collapsed = [n for i, n in enumerate(seq) if i == 0 or n != seq[i - 1]]   # A,A,A -> A
            if len(collapsed) >= 2:
                sequences.append(collapsed)
        if len(sequences) < 2:
            return None
        counts: dict[tuple, int] = {}
        for seq in sequences:
            subs = {tuple(seq[i:j]) for i in range(len(seq)) for j in range(i + 2, len(seq) + 1)}
            for s in subs:
                counts[s] = counts.get(s, 0) + 1
        active = {c["name"]: c for c in self.registry.active()}
        best = None
        for seq, n in counts.items():
            if n < 2 or not all(x in active for x in seq):
                continue
            if any(set(seq) <= set(c["dependencies"]) for c in active.values()):   # уже обёрнуто в составной орган
                continue
            if best is None or (len(seq), n) > (len(best[0]), best[1]):
                best = (seq, n)
        return {"sequence": list(best[0]), "count": best[1]} if best else None

    def build_from_pattern(self, pattern: dict, *, task_id: int | None = None) -> bool:
        self.bus.emit("REPEATED_PATTERN_DETECTED", task_id=task_id, sequence=pattern["sequence"], count=pattern["count"])
        seq = pattern["sequence"]
        details = {n: self.registry.get(n) for n in seq}
        existing = self.registry.active_names()
        spec = self.llm.json(
            prompts.PATTERN_SYSTEM,
            f"Repeated sequence (in order): {seq}\nMember capabilities:\n"
            + "\n".join(f"- {n}: {d['purpose_en']} | in={d['inputs']} | out={d['outputs']}" for n, d in details.items()),
            validate=lambda s: Planner._validate({"intent": "task", "understanding": {}, "requirements": [
                {"id": "p", "need": {}, "decision": "CREATE", "spec": s}]}, existing), purpose="abstracting")
        spec["dependencies"] = list(dict.fromkeys(list(spec.get("dependencies", [])) + seq))
        return self.builder.build(spec, task_id=task_id, origin="composite", parent=seq[0]).ok

    # ======================================================================
    # БЕНЧМАРК: реальный прогон тестов всех живых капабилити
    # ======================================================================
    def benchmark(self) -> dict:
        t0 = time.perf_counter()
        total = passed = 0
        per_cap = []
        for c in self.registry.active():
            info = self.registry.code_of(c["name"])
            deps = self.registry.collect_dependencies(info["spec"].get("dependencies", []))
            res = self.sandbox.run_tests(code=info["code"], tests=info["tests"], dependencies=deps,
                                         network=bool(info["spec"].get("network")))
            rep = res.report or {}
            total += rep.get("total", 0)
            passed += rep.get("passed", 0)
            per_cap.append({"name": c["name"], "total": rep.get("total", 0), "passed": rep.get("passed", 0),
                            "duration": rep.get("duration", res.duration)})
        return {"capabilities": len(per_cap), "tests": total, "passed": passed,
                "pass_rate": (passed / total) if total else None,
                "duration": round(time.perf_counter() - t0, 2), "per_capability": per_cap}

    # ======================================================================
    # САМОАУДИТ  («Improve yourself»)
    # ======================================================================
    def self_audit(self, *, task_id: int | None = None) -> dict:
        self.bus.emit("SELF_AUDIT_STARTED", task_id=task_id, capabilities=len(self.registry.active()))
        if not self.registry.active():
            self.bus.emit("SELF_AUDIT_COMPLETED", task_id=task_id, before=None, after=None, actions=[], empty=True)
            return {"evolved": False, "actions": []}

        before = self.benchmark()
        self.bus.emit("SELF_AUDIT_BENCHMARK", task_id=task_id, phase="before", **{k: v for k, v in before.items() if k != "per_capability"})

        # --- находки: только то, что можно измерить ---
        caps = self.registry.active()
        findings: list[dict] = []
        for c in caps:
            fails = len([f for f in self.registry.failure_history(c["name"]) if f["source"] == "run"])
            repairs = max((v["repairs"] for v in c["versions"] if v["status"] == "active"), default=0)
            if fails:
                findings.append({"kind": "failed_runs", "capability": c["name"], "detail": f"{fails} failed runs"})
            if repairs:
                findings.append({"kind": "needed_repairs", "capability": c["name"], "detail": f"{repairs} repairs during construction"})
            if c["avg_duration"] and c["avg_duration"] > 5:
                findings.append({"kind": "slow", "capability": c["name"], "detail": f"{c['avg_duration']:.1f}s per call"})
            if c["usage_count"] == 0:
                findings.append({"kind": "unused", "capability": c["name"], "detail": "never used"})
        dups = self.registry.duplicate_calls()
        for d in dups:
            findings.append({"kind": "duplicate_calls", "capability": d["name"], "detail": f"{d['n']}x identical call in task {d['task_id']}"})
        self.bus.emit("SELF_AUDIT_FINDINGS", task_id=task_id, findings=findings)

        actions: list[dict] = []
        evolved = False

        # --- улучшение 1: мутируем самые слабые (больше всего падений/ремонтов, меньше всего тестов) ---
        def weakness(c: dict):
            fails = len([f for f in self.registry.failure_history(c["name"]) if f["source"] == "run"])
            repairs = max((v["repairs"] for v in c["versions"] if v["status"] == "active"), default=0)
            return (-fails, -repairs, c["tests"])
        for c in sorted(caps, key=weakness)[:config.AUDIT_MAX_MUTATIONS]:
            reasons = [f["detail"] for f in findings if f["capability"] == c["name"] and f["kind"] in ("failed_runs", "needed_repairs", "slow")]
            reason = ("self-audit: " + "; ".join(reasons)) if reasons else \
                f"self-audit: harden against edge cases and malformed input (only {c['tests']} tests so far)"
            out = self.mutate(c["name"], reason=reason, task_id=task_id)
            actions.append({"action": "mutate", "capability": c["name"], "accepted": out.accepted, "detail": out.reason})
            evolved |= out.accepted

        # --- улучшение 2: самоочистка ---
        retired = self.prune(task_id=task_id)
        for d in retired:
            actions.append({"action": "retire", "capability": d["retire"], "accepted": True, "detail": d.get("cause")})
        evolved |= bool(retired)

        # --- улучшение 3: повторяющийся паттерн -> новый составной орган ---
        pattern = self.detect_pattern()
        if pattern:
            built = self.build_from_pattern(pattern, task_id=task_id)
            actions.append({"action": "compose", "capability": " + ".join(pattern["sequence"]), "accepted": built, "detail": ""})
            evolved |= built

        after = self.benchmark()
        self.bus.emit("SELF_AUDIT_BENCHMARK", task_id=task_id, phase="after", **{k: v for k, v in after.items() if k != "per_capability"})
        self.bus.emit("SELF_AUDIT_COMPLETED", task_id=task_id, actions=actions,
                      before={k: v for k, v in before.items() if k != "per_capability"},
                      after={k: v for k, v in after.items() if k != "per_capability"})
        return {"evolved": evolved, "actions": actions, "before": before, "after": after}
