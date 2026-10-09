"""Шина событий.

Каждое событие: 1) записывается в таблицу evolution_events (это и есть история эволюции),
2) рассылается всем подписчикам SSE-потока, чтобы интерфейс видел происходящее в реальном времени.

ВАЖНО: тексты для интерфейса здесь НЕ формируются — бэкенд шлёт только код события и данные,
а фронтенд сам переводит их на английский/чешский (frontend/src/i18n.ts).
"""
import asyncio
import threading

from .db import Database, dumps, loads, now


class EventBus:
    def __init__(self, db: Database):
        self.db = db
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._lock = threading.Lock()

    # --- публикация (вызывается из потоков агента) ---
    def emit(self, event_type: str, *, capability: str | None = None,
             task_id: int | None = None, description: str = "", **payload) -> dict:
        gen_row = self.db.one("SELECT value FROM meta WHERE key='generation'")
        generation = int(gen_row["value"]) if gen_row else 0
        cap_id = None
        if capability:
            row = self.db.one("SELECT id FROM capabilities WHERE name=?", (capability,))
            cap_id = row["id"] if row else None
        event_id = self.db.execute(
            "INSERT INTO evolution_events(generation,event_type,capability_id,capability_name,description,payload,task_id,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (generation, event_type, cap_id, capability, description, dumps(payload), task_id, now()))
        event = {"id": event_id, "generation": generation, "type": event_type,
                 "capability": capability, "task_id": task_id,
                 "description": description, "data": payload, "created_at": now()}
        with self._lock:
            subs = list(self._subs)
        for loop, queue in subs:
            try:
                loop.call_soon_threadsafe(queue.put_nowait, event)
            except RuntimeError:  # цикл уже закрыт
                pass
        return event

    # --- подписка (вызывается из SSE-эндпоинта) ---
    def subscribe(self, loop: asyncio.AbstractEventLoop) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subs.append((loop, queue))
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        with self._lock:
            self._subs = [(l, q) for (l, q) in self._subs if q is not queue]

    # --- чтение истории ---
    def history(self, after: int = 0, limit: int = 400) -> list[dict]:
        rows = self.db.query(
            "SELECT * FROM (SELECT * FROM evolution_events WHERE id>? ORDER BY id DESC LIMIT ?) ORDER BY id",
            (after, limit))
        return [{"id": r["id"], "generation": r["generation"], "type": r["event_type"],
                 "capability": r["capability_name"], "task_id": r["task_id"],
                 "description": r["description"], "data": loads(r["payload"], {}),
                 "created_at": r["created_at"]} for r in rows]
