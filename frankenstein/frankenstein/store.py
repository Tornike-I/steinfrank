import json
import sqlite3
import threading
import time
from pathlib import Path

from . import config
from .spec import MonsterSpec

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, monster_id TEXT, status TEXT, data TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT, created REAL);
CREATE TABLE IF NOT EXISTS drafts (id TEXT PRIMARY KEY, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS destinations (id TEXT PRIMARY KEY, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS inbox (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, created REAL);
CREATE TABLE IF NOT EXISTS watches (id TEXT PRIMARY KEY, data TEXT, next_at REAL);
CREATE TABLE IF NOT EXISTS spend (run_id TEXT, step TEXT, credits REAL, created REAL);
"""


def db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(config.DATA_DIR / "frankenstein.db", check_same_thread=False)
        _conn.executescript(SCHEMA)
    return _conn


def _exec(sql, params=()):
    with _lock:
        cur = db().execute(sql, params)
        db().commit()
        return cur.fetchall()


def save_run(run: dict):
    now = time.time()
    run.setdefault("created", now)
    run["updated"] = now
    _exec(
        "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
        (run["id"], run["monster_id"], run["status"], json.dumps(run), run["created"], now),
    )


def get_run(run_id: str) -> dict | None:
    rows = _exec("SELECT data FROM runs WHERE id=?", (run_id,))
    return json.loads(rows[0][0]) if rows else None


def runs_with_status(*statuses: str) -> list[dict]:
    q = ",".join("?" * len(statuses))
    return [json.loads(r[0]) for r in _exec(f"SELECT data FROM runs WHERE status IN ({q})", statuses)]


def runs_for_monster(monster_id: str) -> list[dict]:
    rows = _exec("SELECT data FROM runs WHERE monster_id=? ORDER BY created DESC", (monster_id,))
    return [json.loads(r[0]) for r in rows]


def cache_get(key: str, max_age: float | None = None):
    rows = _exec("SELECT value, created FROM cache WHERE key=?", (key,))
    if not rows or (max_age is not None and time.time() - rows[0][1] > max_age):
        return None
    return json.loads(rows[0][0])


def cache_put(key: str, value):
    _exec("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (key, json.dumps(value), time.time()))


def save_draft(spec: dict):
    _exec("INSERT OR REPLACE INTO drafts VALUES (?,?,?)", (spec["id"], json.dumps(spec), time.time()))


def get_draft(monster_id: str) -> dict | None:
    rows = _exec("SELECT data FROM drafts WHERE id=?", (monster_id,))
    return json.loads(rows[0][0]) if rows else None


def monster_path(monster_id: str) -> Path:
    return config.MONSTERS_DIR / f"{monster_id}.json"


def save_monster(spec: MonsterSpec):
    config.MONSTERS_DIR.mkdir(parents=True, exist_ok=True)
    monster_path(spec.id).write_text(json.dumps(spec.dump(), indent=2, ensure_ascii=False), encoding="utf-8")


def load_monster(monster_id: str) -> MonsterSpec | None:
    p = monster_path(monster_id)
    if not p.exists():
        return None
    return MonsterSpec.model_validate_json(p.read_text(encoding="utf-8"))


def list_monsters() -> list[MonsterSpec]:
    if not config.MONSTERS_DIR.exists():
        return []
    return [MonsterSpec.model_validate_json(p.read_text(encoding="utf-8")) for p in sorted(config.MONSTERS_DIR.glob("*.json"))]


def _put(table: str, item_id: str, data: dict):
    _exec(f"INSERT OR REPLACE INTO {table} (id, data, created) VALUES (?,?,?)", (item_id, json.dumps(data), time.time()))


def _get(table: str, item_id: str) -> dict | None:
    rows = _exec(f"SELECT data FROM {table} WHERE id=?", (item_id,))
    return json.loads(rows[0][0]) if rows else None


def save_destination(dest: dict):
    _put("destinations", dest["id"], dest)


def get_destination(dest_id: str) -> dict | None:
    return _get("destinations", dest_id)


def list_destinations() -> list[dict]:
    return [json.loads(r[0]) for r in _exec("SELECT data FROM destinations ORDER BY created")]


def delete_destination(dest_id: str):
    _exec("DELETE FROM destinations WHERE id=?", (dest_id,))


def add_inbox(msg: dict) -> int:
    with _lock:
        cur = db().execute("INSERT INTO inbox (data, created) VALUES (?,?)", (json.dumps(msg), time.time()))
        db().commit()
        return cur.lastrowid


def inbox_since(after_id: int = 0, limit: int = 100) -> list[dict]:
    rows = _exec("SELECT id, data, created FROM inbox WHERE id>? ORDER BY id LIMIT ?", (after_id, limit))
    return [{"id": r[0], "created": r[2], **json.loads(r[1])} for r in rows]


def save_watch(watch: dict):
    _exec("INSERT OR REPLACE INTO watches VALUES (?,?,?)", (watch["id"], json.dumps(watch), watch["next_at"]))


def get_watch(watch_id: str) -> dict | None:
    rows = _exec("SELECT data FROM watches WHERE id=?", (watch_id,))
    return json.loads(rows[0][0]) if rows else None


def list_watches() -> list[dict]:
    return [json.loads(r[0]) for r in _exec("SELECT data FROM watches ORDER BY next_at")]


def due_watches(now: float) -> list[dict]:
    return [w for w in (json.loads(r[0]) for r in _exec("SELECT data FROM watches WHERE next_at<=?", (now,))) if w.get("enabled", True)]


def delete_watch(watch_id: str):
    _exec("DELETE FROM watches WHERE id=?", (watch_id,))


def reserve_credits(run_id: str, step: str, credits: float):
    _exec("INSERT INTO spend VALUES (?,?,?,?)", (run_id, step, credits, time.time()))


def credits_reserved_since(since: float) -> float:
    return _exec("SELECT COALESCE(SUM(credits), 0) FROM spend WHERE created>=?", (since,))[0][0]
