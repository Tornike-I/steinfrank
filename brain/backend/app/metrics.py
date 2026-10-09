"""Метрики. Все числа считаются запросами к БД — ничего не придумано и не захардкожено."""
from .db import Database
from .registry import Registry


def collect(db: Database, registry: Registry) -> dict:
    caps = registry.list()
    active = [c for c in caps if c["status"] == "active"]

    def n(sql: str, params: tuple = ()) -> int:
        row = db.one(sql, params)
        return int(list(row.values())[0] or 0) if row else 0

    tasks_done = n("SELECT COUNT(*) FROM tasks WHERE status='completed'")
    tasks_failed = n("SELECT COUNT(*) FROM tasks WHERE status='failed'")
    runs = n("SELECT COUNT(*) FROM capability_runs")
    runs_ok = n("SELECT COALESCE(SUM(success),0) FROM capability_runs")
    avg_row = db.one("SELECT AVG(duration) a FROM tasks WHERE status='completed'")
    return {
        "generation": registry.generation(),
        "capabilities_total": len(caps),
        "capabilities_active": len(active),
        "capabilities_retired": len(caps) - len(active),
        "task_success_rate": tasks_done / (tasks_done + tasks_failed) if (tasks_done + tasks_failed) else None,
        "tasks_completed": tasks_done, "tasks_failed": tasks_failed,
        "capability_success_rate": runs_ok / runs if runs else None,
        "capability_runs": runs,
        "avg_task_seconds": avg_row["a"] if avg_row and avg_row["a"] is not None else None,
        "tests_total": sum(c["tests"] for c in active),
        "tests_failed_total": n("SELECT COALESCE(SUM(json_extract(payload,'$.failed')),0) FROM evolution_events WHERE event_type='CAPABILITY_TEST_FAILED'"),
        "repairs": n("SELECT COUNT(*) FROM evolution_events WHERE event_type='CAPABILITY_REPAIR'"),
        "mutations_accepted": n("SELECT COUNT(*) FROM evolution_events WHERE event_type='CAPABILITY_MUTATED' AND json_extract(payload,'$.accepted')=1"),
        "mutations_rejected": n("SELECT COUNT(*) FROM evolution_events WHERE event_type='CAPABILITY_MUTATED' AND json_extract(payload,'$.accepted')=0"),
        "reused": n("SELECT COUNT(*) FROM evolution_events WHERE event_type='CAPABILITY_REUSED'"),
        "generated": n("SELECT COUNT(*) FROM evolution_events WHERE event_type='CAPABILITY_INSTALLED'"),
    }
