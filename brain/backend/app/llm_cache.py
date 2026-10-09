"""Кэш ответов модели (идея из проекта коллеги: limbs/llm.py — «identical prompts are cached and cost nothing»).

Кэшируются только ПРОВЕРЕННЫЕ JSON-ответы для назначений, где тот же запрос означает тот же ответ: план, знания, маршрутизация. Код органов (сборка/ремонт/воркфлоу) НЕ кэшируется: неудачный вариант мог бы вернуться снова.
Ключ — хэш всего промпта и модели: изменилось состояние (органы, рецепты, память) — изменился промпт — новый ключ.
Если задача провалилась, её записи удаляются (forget_task), чтобы неудачный план не повторился.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from . import config
from .db import Database, now

# "slot" НЕ кэшируется: слоты — это свежие данные со своим сроком годности (память знаний); при обновлении модель обязана
# ответить заново, а не повторить старое.
CACHEABLE = {"planning", "knowledge", "knowledge_extend", "knowledge_main", "routing"}


def make_key(provider: str, model: str, purpose: str, system: str, user: str) -> str:
    raw = json.dumps([provider, model, purpose, system, user], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class LLMCache:
    def __init__(self, db: Database):
        self.db = db

    def get(self, key: str) -> dict | None:
        row = self.db.one("SELECT * FROM llm_cache WHERE key=?", (key,))
        if not row:
            return None
        if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc).isoformat(timespec="seconds"):
            self.db.execute("DELETE FROM llm_cache WHERE key=?", (key,))
            return None
        self.db.execute("UPDATE llm_cache SET hits=hits+1 WHERE key=?", (key,))
        return {"response": json.loads(row["response"]), "tokens": row["tokens"], "seconds": row["seconds"]}

    def put(self, key: str, purpose: str, model: str, response: dict, tokens: int, seconds: float, task_id: int | None) -> None:
        ttl = config.LLM_CACHE_TTL_HOURS
        exp = (datetime.now(timezone.utc) + timedelta(hours=ttl)).isoformat(timespec="seconds") if ttl else None
        self.db.execute("INSERT OR REPLACE INTO llm_cache(key,purpose,model,response,tokens,seconds,task_id,hits,created_at,expires_at)"
                        " VALUES(?,?,?,?,?,?,?,0,?,?)",
                        (key, purpose, model, json.dumps(response, ensure_ascii=False), tokens, seconds, task_id, now(), exp))

    def forget_task(self, task_id: int) -> int:
        n = self.db.one("SELECT COUNT(*) n FROM llm_cache WHERE task_id=?", (task_id,))["n"]
        self.db.execute("DELETE FROM llm_cache WHERE task_id=?", (task_id,))
        return n

    def stats(self) -> dict:
        r = self.db.one("SELECT COUNT(*) entries, COALESCE(SUM(hits),0) hits, COALESCE(SUM(hits*tokens),0) saved_tokens,"
                        " COALESCE(SUM(hits*seconds),0) saved_seconds FROM llm_cache")
        return {"entries": r["entries"], "hits": r["hits"], "saved_tokens": r["saved_tokens"], "saved_seconds": round(r["saved_seconds"], 1)}
