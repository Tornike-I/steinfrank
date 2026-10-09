"""Память со сроком годности.

Монстр различает:
  «я знаю, КАК это делать»      — навык в реестре (процедурная память, живёт, пока проходит тесты);
  «у меня есть СВЕЖИЕ данные»   — записи здесь (dynamic), у каждой есть источник, время и срок годности.

Устаревшие данные приводят к ОБНОВЛЕНИЮ данных (повторный вызов навыка / LLM-слота), а не к пересборке навыка.
Сроки годности задаёт сам навык (SKILL["freshness_seconds"], слоты — свои), а не жёсткая таблица в коде.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from .db import Database, dumps, loads, now

CATEGORIES = ("dynamic", "stable", "procedural", "task", "preference")


def make_key(*parts) -> str:
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


class Knowledge:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str, scope: str = "global") -> dict | None:
        """Запись + признаки свежести: fresh (не истекла), age_seconds."""
        r = self.db.one("SELECT * FROM knowledge WHERE scope=? AND key=?", (scope, key))
        if not r:
            return None
        exp = _parse(r["expires_at"])
        created = _parse(r["last_verified_at"] or r["created_at"])
        nowdt = datetime.now(timezone.utc)
        return {**r, "content": loads(r["content"]), "params": loads(r["params"], {}),
                "fresh": exp is None or exp > nowdt, "age_seconds": int((nowdt - created).total_seconds()) if created else None}

    def put(self, key: str, *, category: str, skill: str, params: dict, content, source: str,
            ttl_seconds: int | None, scope: str = "global") -> None:
        exp = (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds") if ttl_seconds else None
        self.db.execute(
            "INSERT INTO knowledge(scope,category,skill,key,params,content,source,created_at,expires_at,last_verified_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(scope,key) DO UPDATE SET content=excluded.content,"
            " source=excluded.source, expires_at=excluded.expires_at, last_verified_at=excluded.last_verified_at,"
            " category=excluded.category", (scope, category, skill, key, dumps(params), dumps(content), source, now(), exp, now()))

    def hit(self, key: str, scope: str = "global") -> None:
        self.db.execute("UPDATE knowledge SET hits=hits+1 WHERE scope=? AND key=?", (scope, key))

    def expire_skill(self, skill: str) -> int:
        """Пометить устаревшими все данные навыка (например, после смены версии/провайдера)."""
        rows = self.db.query("SELECT id FROM knowledge WHERE skill=? AND (expires_at IS NULL OR expires_at > ?)", (skill, now()))
        self.db.execute("UPDATE knowledge SET expires_at=? WHERE skill=?", (now(), skill))
        return len(rows)

    def summary(self) -> dict:
        rows = self.db.query("SELECT category, COUNT(*) n, SUM(hits) hits, SUM(CASE WHEN expires_at IS NOT NULL AND expires_at <= ? THEN 1 ELSE 0 END) stale"
                             " FROM knowledge GROUP BY category", (now(),))
        return {r["category"]: {"records": r["n"], "hits": r["hits"] or 0, "stale": r["stale"] or 0} for r in rows}

    def list(self, limit: int = 30) -> list[dict]:
        out = []
        for r in self.db.query("SELECT * FROM knowledge ORDER BY id DESC LIMIT ?", (limit,)):
            exp = _parse(r["expires_at"])
            out.append({"id": r["id"], "category": r["category"], "skill": r["skill"], "params": loads(r["params"], {}),
                        "source": r["source"], "created_at": r["created_at"], "expires_at": r["expires_at"], "hits": r["hits"],
                        "fresh": exp is None or exp > datetime.now(timezone.utc)})
        return out
