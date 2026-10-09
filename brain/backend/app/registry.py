"""Реестр капабилити — «память органов» Frankenstein.

Модель данных:
  capabilities          одна строка на ЛИНИЮ (имя). Хранит статус, поколение рождения, предка.
  capability_versions   версии линии (код + тесты + результаты). Активна ровно одна.
  capability_runs       каждый реальный вызов (для usage / success rate).

Ничего не удаляется навсегда: «умершие» капабилити и версии остаются в БД и показываются
на кладбище (Graveyard).
"""
import re
from pathlib import Path

from . import config, skillmeta
from . import recipes
from .db import Database, dumps, loads, now


def _next_version(existing: list[str]) -> str:
    """1.0.0 -> 1.1.0 -> 1.2.0 ... (минорная версия растёт при каждой мутации)."""
    if not existing:
        return "1.0.0"
    minors = [int(v.split(".")[1]) for v in existing if re.match(r"^\d+\.\d+\.\d+$", v)]
    return f"1.{max(minors, default=0) + 1}.0"


class Registry:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ поколения
    def generation(self) -> int:
        row = self.db.one("SELECT value FROM meta WHERE key='generation'")
        return int(row["value"]) if row else 0

    def bump_generation(self, note: str) -> int:
        """Новое поколение: фиксируем снимок (сколько капабилити живо/мертво)."""
        gen = self.generation() + 1
        total = self.db.one("SELECT COUNT(*) n FROM capabilities")["n"]
        active = self.db.one("SELECT COUNT(*) n FROM capabilities WHERE status='active'")["n"]
        self.db.execute("UPDATE meta SET value=? WHERE key='generation'", (str(gen),))
        self.db.execute(
            "INSERT OR REPLACE INTO generations(generation,capabilities_total,active,retired,note,created_at) VALUES(?,?,?,?,?,?)",
            (gen, total, active, total - active, note, now()))
        return gen

    def generations(self) -> list[dict]:
        return self.db.query("SELECT * FROM generations ORDER BY generation")

    # ------------------------------------------------------------------ чтение
    def _run_stats(self) -> dict[int, dict]:
        rows = self.db.query(
            "SELECT capability_id, COUNT(*) n, SUM(success) ok, AVG(duration) avg FROM capability_runs GROUP BY capability_id")
        return {r["capability_id"]: r for r in rows}

    def _build(self, row: dict, versions: list[dict], stats: dict, names: dict[int, str],
               children: dict[int, list[str]]) -> dict:
        meta = loads(row["metadata"], {})
        active_v = next((v for v in versions if v["status"] == "active"), None)
        st = stats.get(row["id"], {"n": 0, "ok": 0, "avg": None})
        runs, ok = st["n"], st["ok"] or 0
        if runs:
            success = ok / runs
        elif active_v and active_v["test_total"]:
            success = active_v["test_passed"] / active_v["test_total"]
        else:
            success = None
        return {
            "id": row["id"], "name": row["name"], "version": row["current_version"],
            "description": row["description"], "purpose_en": meta.get("purpose_en", ""),
            "purpose_cs": meta.get("purpose_cs", ""), "inputs": meta.get("inputs", {}),
            "outputs": meta.get("outputs", {}), "dependencies": meta.get("dependencies", []),
            "network": bool(meta.get("network")), "status": row["status"],
            "skill": meta.get("skill") or {}, "visibility": row.get("visibility") or "global",
            "owner_monster_id": row.get("owner_monster_id"),
            "generation_created": row["generation"],
            "parent": names.get(row["parent_id"]), "children": children.get(row["id"], []),
            "replacement": names.get(row["replacement_id"]),
            "death_cause": row["death_cause"], "death_note": row["death_note"],
            "retired_generation": row["retired_generation"],
            "tests": active_v["test_total"] if active_v else 0,
            "tests_passed": active_v["test_passed"] if active_v else 0,
            "success_rate": success, "usage_count": runs, "avg_duration": st["avg"],
            "created_at": row["created_at"], "retired_at": row["retired_at"],
            "implementation_path": row["implementation_path"],
            "versions": [{"version": v["version"], "status": v["status"], "origin": v["origin"],
                          "score": v["score"], "tests": v["test_total"], "tests_passed": v["test_passed"],
                          "repairs": v["repairs"], "created_at": v["created_at"],
                          "death_cause": v["death_cause"], "death_note": v["death_note"]} for v in versions],
        }

    def list(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM capabilities ORDER BY id")
        names = {r["id"]: r["name"] for r in rows}
        children: dict[int, list[str]] = {}
        for r in rows:
            if r["parent_id"]:
                children.setdefault(r["parent_id"], []).append(r["name"])
        stats = self._run_stats()
        all_versions = self.db.query("SELECT * FROM capability_versions ORDER BY id")
        by_cap: dict[int, list[dict]] = {}
        for v in all_versions:
            by_cap.setdefault(v["capability_id"], []).append(v)
        return [self._build(r, by_cap.get(r["id"], []), stats, names, children) for r in rows]

    def get(self, name: str) -> dict | None:
        return next((c for c in self.list() if c["name"] == name), None)

    def active(self) -> list[dict]:
        return [c for c in self.list() if c["status"] == "active"]

    def active_names(self) -> set[str]:
        return {r["name"] for r in self.db.query("SELECT name FROM capabilities WHERE status='active'")}

    def summary_for_llm(self, task_text: str = "", detailed: int = 8) -> dict:
        """Сводка реестра для планировщика. ЭКОНОМИЯ ТОКЕНОВ: подробно (входы/выходы/зависимости/статистика)
        описываем только органы, лексически близкие к задаче, остальные — одной короткой строкой.
        Если органов не больше `detailed`, подробно описываются все."""
        caps = self.active()
        words = set(re.findall(r"[a-zа-я0-9]{4,}", task_text.lower()))

        def score(c: dict) -> int:
            hay = set(re.findall(r"[a-zа-я0-9]{4,}", f"{c['name'].replace('_', ' ')} {c['purpose_en']} {c['description']}".lower()))
            return len(words & hay) + c["usage_count"] * 0.01
        ranked = sorted(caps, key=score, reverse=True)

        def full(c: dict) -> dict:
            return {"name": c["name"], "v": c["version"], "purpose": c["purpose_en"] or c["description"],
                    "in": list(c["inputs"]), "out": list(c["outputs"]), "deps": c["dependencies"],
                    "ok": None if c["success_rate"] is None else round(c["success_rate"], 2), "uses": c["usage_count"]}
        return {"total": len(caps), "detailed": [full(c) for c in ranked[:detailed]],
                "others": [{"name": c["name"], "purpose": (c["purpose_en"] or c["description"])[:70]} for c in ranked[detailed:]]}

    # ------------------------------------------------------------------ рецепты (повторное исполнение без модели)
    def recipes_for_llm(self) -> list[dict]:
        """Рецепты, все капабилити которых ещё живы (иначе рецепт устарел). Модель видит ИМЕНА параметров рецепта,
        а не значения прошлого запроса — чтобы заполнить их значениями текущего запроса."""
        active = self.active_names()
        out = []
        for r in self.db.query("SELECT * FROM recipes ORDER BY id DESC LIMIT 10"):
            caps = loads(r["capabilities"], [])
            if caps and all(c in active for c in caps):
                params = loads(r["params"], None)
                item = {"id": r["id"], "does": r["description"][:140], "capabilities": caps, "runs": r["uses"],
                        "params": sorted(params) if isinstance(params, dict) else None}
                if params is None or loads(r["bound"], []):
                    item["hardcoded"] = True     # код рецепта содержит значения прошлого запроса — годится только для тех же значений
                out.append(item)
        return out

    def recipe(self, recipe_id: int) -> dict | None:
        return self.db.one("SELECT * FROM recipes WHERE id=?", (recipe_id,))

    def save_recipe(self, description: str, capabilities: list[str], code: str, params: dict | None = None,
                    source_text: str = "") -> int:
        """Запоминаем удачный воркфлоу как ПРОЦЕДУРУ. bound — значения запроса, которые код зашил литералами
        (тогда рецепт безопасен только для запроса с теми же значениями — это проверяет recipe_guard).
        Для того же набора органов и тех же имён параметров обновляем существующий рецепт."""
        caps = sorted(set(capabilities))
        params = params if isinstance(params, dict) else {}
        bound = recipes.bound_values(code, params, source_text)
        keys = sorted(params)
        row = next((r for r in self.db.query("SELECT id, capabilities, params FROM recipes")
                    if loads(r["capabilities"]) == caps and sorted(loads(r["params"], {}) or {}) == keys), None)
        if row:
            self.db.execute("UPDATE recipes SET code=?, description=?, params=?, source_text=?, bound=?, updated_at=? WHERE id=?",
                            (code, description, dumps(params), source_text[:500], dumps(bound), now(), row["id"]))
            return row["id"]
        return self.db.execute("INSERT INTO recipes(description,capabilities,code,params,source_text,bound,created_at,updated_at)"
                               " VALUES(?,?,?,?,?,?,?,?)",
                               (description, dumps(caps), code, dumps(params), source_text[:500], dumps(bound), now(), now()))

    def recipe_used(self, recipe_id: int, ok: bool) -> None:
        self.db.execute("UPDATE recipes SET uses=uses+1, successes=successes+? WHERE id=?", (1 if ok else 0, recipe_id))

    def active_version_row(self, name: str) -> dict | None:
        return self.db.one(
            "SELECT v.*, c.name, c.id cid FROM capability_versions v JOIN capabilities c ON c.id=v.capability_id"
            " WHERE c.name=? AND v.status='active'", (name,))

    def code_of(self, name: str, version: str | None = None) -> dict | None:
        """Код/тесты капабилити: активной версии или указанной (закреплённой у монстра)."""
        v = self.active_version_row(name) if not version else self.db.one(
            "SELECT v.* FROM capability_versions v JOIN capabilities c ON c.id=v.capability_id"
            " WHERE c.name=? AND v.version=? AND v.status IN ('active','retired')", (name, version))
        if not v:
            return None
        return {"version": v["version"], "code": v["implementation"], "tests": v["tests"],
                "readme": v["readme"], "spec": loads(v["metadata"], {})}

    def collect_dependencies(self, names: list[str]) -> dict[str, str]:
        """Исходники указанных капабилити + всех их зависимостей (рекурсивно)."""
        result: dict[str, str] = {}
        stack = list(names)
        while stack:
            n = stack.pop()
            if n in result:
                continue
            info = self.code_of(n)
            if not info:
                raise KeyError(f"capability '{n}' is not installed")
            result[n] = info["code"]
            stack.extend(info["spec"].get("dependencies", []))
        return result

    def dependents(self, name: str) -> list[str]:
        """Какие АКТИВНЫЕ капабилити зависят от данной (нужно при удалении)."""
        return [c["name"] for c in self.active() if name in c["dependencies"]]

    def next_version(self, name: str) -> str:
        row = self.db.one("SELECT id FROM capabilities WHERE name=?", (name,))
        versions = [v["version"] for v in self.db.query(
            "SELECT version FROM capability_versions WHERE capability_id=?", (row["id"],))] if row else []
        return _next_version(versions)

    # ------------------------------------------------------------------ запись
    def _write_files(self, name: str, version: str, code: str, tests: str, readme: str, spec: dict) -> str:
        """Копия на диск — чтобы человек мог открыть и прочитать установленный «орган»."""
        folder = config.CAPS_DIR / name / f"v{version}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "capability.py").write_text(code, encoding="utf-8")
        (folder / "test_capability.py").write_text(tests, encoding="utf-8")
        (folder / "README.md").write_text(readme or "", encoding="utf-8")
        (folder / "meta.json").write_text(dumps(spec), encoding="utf-8")
        return str(folder)

    def install_new(self, spec: dict, *, code: str, tests: str, readme: str, report: dict,
                    repairs: int, origin: str = "created", parent: str | None = None) -> dict:
        """Первая установка капабилити (новая линия). Вызывать ТОЛЬКО после успешных тестов.

        Орган принадлежит СЛЕДУЮЩЕМУ поколению (generation()+1): номер растёт, когда задача завершится
        (см. Brain._finish), поэтому всё, что выросло в одной задаче, получает один и тот же номер."""
        name = spec["name"]
        spec = {**spec, "skill": skillmeta.extract(code)}
        parent_row = self.db.one("SELECT id FROM capabilities WHERE name=?", (parent,)) if parent else None
        version = "1.0.0"
        path = self._write_files(name, version, code, tests, readme, spec)
        cap_id = self.db.execute(
            "INSERT INTO capabilities(name,description,implementation_path,metadata,status,generation,parent_id,current_version,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (name, spec.get("description", ""), path, dumps(spec), "active", self.generation() + 1,
             parent_row["id"] if parent_row else None, version, now()))
        self._insert_version(cap_id, version, code, tests, readme, spec, origin, report, repairs, "active")
        return {"name": name, "version": version}

    def add_version(self, name: str, *, code: str, tests: str, readme: str, spec: dict, report: dict,
                    repairs: int, origin: str = "mutation", score: float | None = None) -> str:
        """Добавить версию-кандидата (status=candidate), пока она не победила в сравнении."""
        cap = self.db.one("SELECT id FROM capabilities WHERE name=?", (name,))
        version = self.next_version(name)
        spec = {**spec, "skill": skillmeta.extract(code)}
        self._insert_version(cap["id"], version, code, tests, readme, spec, origin, report, repairs, "candidate", score)
        return version

    def _insert_version(self, cap_id, version, code, tests, readme, spec, origin, report, repairs, status, score=None):
        total, passed = report.get("total", 0), report.get("passed", 0)
        self.db.execute(
            "INSERT INTO capability_versions(capability_id,version,implementation,tests,readme,metadata,origin,"
            "test_total,test_passed,score,repairs,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (cap_id, version, code, tests, readme, dumps(spec), origin, total, passed,
             score if score is not None else (passed / total if total else None), repairs, status, now()))

    def promote_version(self, name: str, version: str, *, old_cause: str = "superseded", old_note: str = "") -> None:
        """Версия-кандидат побеждает: становится активной, прежняя уходит на кладбище."""
        cap = self.db.one("SELECT * FROM capabilities WHERE name=?", (name,))
        new = self.db.one("SELECT * FROM capability_versions WHERE capability_id=? AND version=?", (cap["id"], version))
        self.db.execute(
            "UPDATE capability_versions SET status='retired', death_cause=?, death_note=?, retired_at=?"
            " WHERE capability_id=? AND status='active'", (old_cause, old_note, now(), cap["id"]))
        self.db.execute("UPDATE capability_versions SET status='active' WHERE id=?", (new["id"],))
        spec = loads(new["metadata"], {})
        path = self._write_files(name, version, new["implementation"], new["tests"], new["readme"], spec)
        self.db.execute("UPDATE capabilities SET current_version=?, metadata=?, description=?, implementation_path=? WHERE id=?",
                        (version, new["metadata"], spec.get("description", cap["description"]), path, cap["id"]))

    def rollback(self, name: str) -> str | None:
        """Откат: предыдущая (вытесненная) версия снова становится активной. Ничего не удаляется."""
        cap = self.db.one("SELECT id, current_version FROM capabilities WHERE name=? AND status='active'", (name,))
        if not cap:
            return None
        prev = self.db.one("SELECT version FROM capability_versions WHERE capability_id=? AND status='retired'"
                           " ORDER BY id DESC LIMIT 1", (cap["id"],))
        if not prev:
            return None
        self.promote_version(name, prev["version"], old_cause="rolled_back", old_note=f"rolled back to v{prev['version']}")
        return prev["version"]

    def set_owner(self, name: str, monster_id: int | None, visibility: str | None = None) -> None:
        cap = self.db.one("SELECT id, owner_monster_id FROM capabilities WHERE name=?", (name,))
        if not cap:
            return
        if monster_id is not None and cap["owner_monster_id"] is None:
            self.db.execute("UPDATE capabilities SET owner_monster_id=? WHERE id=?", (monster_id, cap["id"]))
        if visibility in ("global", "private"):
            self.db.execute("UPDATE capabilities SET visibility=? WHERE id=?", (visibility, cap["id"]))

    def callable_skills(self) -> list[dict]:
        """Навыки, которые умеют сами разбирать запрос (parse_request) — кандидаты на исполнение без LLM."""
        return [c for c in self.active() if (c.get("skill") or {}).get("callable")]

    def reject_version(self, name: str, version: str, reason: str) -> None:
        """Проигравшая мутация: хранится в истории, но родитель остаётся активным."""
        cap = self.db.one("SELECT id FROM capabilities WHERE name=?", (name,))
        self.db.execute(
            "UPDATE capability_versions SET status='rejected', death_cause='rejected', death_note=?, retired_at=?"
            " WHERE capability_id=? AND version=?", (reason, now(), cap["id"], version))

    def set_version_score(self, name: str, version: str, score: float) -> None:
        cap = self.db.one("SELECT id FROM capabilities WHERE name=?", (name,))
        self.db.execute("UPDATE capability_versions SET score=? WHERE capability_id=? AND version=?",
                        (score, cap["id"], version))

    def retire(self, name: str, cause: str, *, note: str = "", replacement: str | None = None) -> None:
        """Отправить капабилити на кладбище целиком (данные сохраняются)."""
        cap = self.db.one("SELECT id FROM capabilities WHERE name=?", (name,))
        rep = self.db.one("SELECT id FROM capabilities WHERE name=?", (replacement,)) if replacement else None
        self.db.execute(
            "UPDATE capabilities SET status='retired', death_cause=?, death_note=?, replacement_id=?,"
            " retired_generation=?, retired_at=? WHERE id=?",
            (cause, note, rep["id"] if rep else None, self.generation() + 1, now(), cap["id"]))
        self.db.execute(
            "UPDATE capability_versions SET status='retired', death_cause=?, death_note=?, retired_at=?"
            " WHERE capability_id=? AND status='active'", (cause, note, now(), cap["id"]))

    def record_run(self, name: str, *, version: str | None, task_id: int | None, input_hash: str,
                   success: bool, error: str | None, duration: float) -> None:
        cap = self.db.one("SELECT id, current_version FROM capabilities WHERE name=?", (name,))
        if not cap:
            return
        self.db.execute(
            "INSERT INTO capability_runs(capability_id,version,task_id,input_hash,input,output,success,error,duration,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cap["id"], version or cap["current_version"], task_id, input_hash, "", "",
             1 if success else 0, error, duration, now()))

    # ------------------------------------------------------------------ кладбище и история
    def graveyard(self) -> list[dict]:
        """Мёртвые линии + мёртвые версии живых капабилити."""
        by_name = {c["name"]: c for c in self.list()}
        out: list[dict] = []
        for c in by_name.values():
            if c["status"] == "retired":
                out.append({"kind": "capability", "name": c["name"], "version": c["version"],
                            "created_at": c["created_at"], "retired_at": c["retired_at"],
                            "usage": c["usage_count"], "success_rate": c["success_rate"],
                            "failures": self._failures(c["name"]), "replaced_by": c["replacement"],
                            "cause": c["death_cause"], "note": c["death_note"],
                            "generation_created": c["generation_created"], "generation_retired": c["retired_generation"]})
            else:
                for v in c["versions"]:
                    if v["status"] in ("retired", "rejected"):
                        usage = self.db.one(
                            "SELECT COUNT(*) n, SUM(1-success) f FROM capability_runs r JOIN capabilities k ON k.id=r.capability_id"
                            " WHERE k.name=? AND r.version=?", (c["name"], v["version"]))
                        out.append({"kind": "version" if v["status"] == "retired" else "rejected_mutation",
                                    "name": c["name"], "version": v["version"], "created_at": v["created_at"],
                                    "retired_at": None, "usage": usage["n"], "failures": usage["f"] or 0,
                                    "success_rate": v["score"], "replaced_by": f"{c['name']} v{c['version']}",
                                    "cause": v["death_cause"], "note": v["death_note"],
                                    "generation_created": c["generation_created"], "generation_retired": None})
        return out

    def _failures(self, name: str) -> int:
        row = self.db.one("SELECT COUNT(*) n FROM capability_runs r JOIN capabilities c ON c.id=r.capability_id"
                          " WHERE c.name=? AND r.success=0", (name,))
        return row["n"]

    def failure_history(self, name: str, limit: int = 8) -> list[dict]:
        """Последние ошибки: реальные падения при запуске + провалы тестов при сборке."""
        runs = self.db.query(
            "SELECT r.version, r.error, r.created_at FROM capability_runs r JOIN capabilities c ON c.id=r.capability_id"
            " WHERE c.name=? AND r.success=0 ORDER BY r.id DESC LIMIT ?", (name, limit))
        tests = self.db.query(
            "SELECT payload, created_at FROM evolution_events WHERE capability_name=? AND event_type='CAPABILITY_TEST_FAILED'"
            " ORDER BY id DESC LIMIT ?", (name, limit))
        out = [{"source": "run", "version": r["version"], "error": r["error"], "at": r["created_at"]} for r in runs]
        for t in tests:
            p = loads(t["payload"], {})
            out.append({"source": "tests", "error": f"{p.get('failed', '?')}/{p.get('total', '?')} tests failed", "at": t["created_at"]})
        return sorted(out, key=lambda x: x["at"], reverse=True)[:limit]

    # ------------------------------------------------------------------ задачи и память
    def duplicate_calls(self) -> list[dict]:
        """Одинаковые вызовы (капабилити + хеш входа) в рамках одной задачи — реальный признак лишней работы."""
        return self.db.query(
            "SELECT c.name, r.task_id, r.input_hash, COUNT(*) n FROM capability_runs r JOIN capabilities c ON c.id=r.capability_id"
            " WHERE r.task_id IS NOT NULL GROUP BY c.name, r.task_id, r.input_hash HAVING n>1")

    def add_note(self, text: str) -> None:
        self.db.execute("INSERT INTO notes(text,created_at) VALUES(?,?)", (text, now()))

    def notes(self, limit: int = 12) -> list[str]:
        return [r["text"] for r in self.db.query("SELECT text FROM notes ORDER BY id DESC LIMIT ?", (limit,))]
