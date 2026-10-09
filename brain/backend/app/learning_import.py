"""Import reusable learning from another Frankenstein data directory.

Only global, reusable data is copied.  Tasks, monsters, lab runs and usage stay
in the lab database, so connecting the UI cannot overwrite either project's
history.  The source SQLite database is always opened read-only and imports are
idempotent.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .db import Database
from .registry import Registry


def _source_db(source: Path) -> Path:
    return source / "frankenstein.db" if source.is_dir() else source


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _copy_rows(source: sqlite3.Connection, target: Database, table: str, *, where: str = "",
               exclude: set[str] | None = None) -> int:
    if table not in _tables(source):
        return 0
    excluded = exclude or set()
    source_cols = [r[1] for r in source.execute(f"PRAGMA table_info({table})")]
    target_cols = {r["name"] for r in target.query(f"PRAGMA table_info({table})")}
    cols = [c for c in source_cols if c in target_cols and c not in excluded]
    if not cols:
        return 0
    before = target.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
    names = ",".join(cols)
    marks = ",".join("?" for _ in cols)
    for row in source.execute(f"SELECT {names} FROM {table} {where}"):
        target.execute(f"INSERT OR IGNORE INTO {table}({names}) VALUES({marks})", tuple(row[c] for c in cols))
    after = target.one(f"SELECT COUNT(*) AS n FROM {table}")["n"]
    return after - before


def import_learning(target: Database, registry: Registry, source_dir: Path | None) -> dict[str, int | str]:
    """Copy missing reusable skills, knowledge and caches from ``source_dir``."""
    result: dict[str, int | str] = {"capabilities": 0, "knowledge": 0, "knowledge_packs": 0,
                                    "llm_cache": 0, "sokosumi_agents": 0}
    if not source_dir:
        return result
    source_path = _source_db(Path(source_dir)).resolve()
    if not source_path.exists() or source_path == target.path.resolve():
        return result

    source: sqlite3.Connection | None = None
    try:
        source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        available = _tables(source)

        if {"capabilities", "capability_versions"} <= available:
            caps = source.execute(
                "SELECT * FROM capabilities WHERE status='active' "
                "AND COALESCE(visibility,'global')='global' ORDER BY id"
            ).fetchall()
            for cap in caps:
                if registry.get(cap["name"]):
                    continue
                version = source.execute(
                    "SELECT * FROM capability_versions WHERE capability_id=? AND status='active' "
                    "ORDER BY id DESC LIMIT 1", (cap["id"],)
                ).fetchone()
                if not version:
                    continue
                spec = json.loads(version["metadata"] or cap["metadata"] or "{}")
                spec["name"] = cap["name"]
                registry.install_new(
                    spec,
                    code=version["implementation"],
                    tests=version["tests"],
                    readme=version["readme"] or "",
                    report={"total": version["test_total"] or 0, "passed": version["test_passed"] or 0},
                    repairs=version["repairs"] or 0,
                    origin="imported",
                )
                result["capabilities"] += 1

        result["knowledge"] = _copy_rows(source, target, "knowledge", where="WHERE scope='global'", exclude={"id"})
        result["knowledge_packs"] = _copy_rows(source, target, "knowledge_packs", exclude={"id"})
        result["llm_cache"] = _copy_rows(source, target, "llm_cache", exclude={"task_id"})
        result["sokosumi_agents"] = _copy_rows(source, target, "sokosumi_agents")
    except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
        # Import is an optimisation.  A stale/corrupt source must never prevent
        # the lab backend from starting with its own clean data.
        result["error"] = str(exc)[:300]
    finally:
        if source is not None:
            source.close()
    return result
