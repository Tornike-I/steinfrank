"""Учёт расхода («$ cost» на экране). Каждый вызов «мозга» и каждая озвучка записываются в таблицу usage.

Задача контекста: чтобы LLM и TTS знали, к какой задаче относится вызов, Brain выставляет
текущий task_id через ContextVar (он свой в каждом потоке, поэтому ничего не путается).
"""
import contextvars

from . import config
from .db import Database, now

current_task: contextvars.ContextVar[int | None] = contextvars.ContextVar("current_task", default=None)
# бюджет токенов текущей задачи (0/None — без ограничения); выставляет Brain, проверяет LLM перед каждым вызовом
task_budget: contextvars.ContextVar[int | None] = contextvars.ContextVar("task_budget", default=None)


def estimate_tokens(text: str) -> int:
    """Грубая оценка (≈4 символа на токен) — для провайдеров, которые не сообщают токены (Sokosumi)."""
    return max(1, len(text) // 4)


class Ledger:
    def __init__(self, db: Database):
        self.db = db

    def record(self, kind: str, *, purpose: str = "", provider: str = "", prompt_tokens: int = 0,
               completion_tokens: int = 0, chars: int = 0, credits: float = 0.0, seconds: float = 0.0,
               model: str | None = None, ref: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO usage(task_id,kind,purpose,provider,model,prompt_tokens,completion_tokens,chars,credits,seconds,ref,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (current_task.get(), kind, purpose, provider, model, prompt_tokens, completion_tokens, chars, credits,
             seconds, ref, now()))

    def set_credits(self, ref: str, credits: float) -> None:
        """Уточнённая стоимость вызова (пришла от провайдера позже)."""
        self.db.execute("UPDATE usage SET credits=? WHERE ref=?", (credits, ref))

    def summary(self, task_id: int | None = None) -> dict:
        """Итоги: всего и (если указана) по задаче."""
        def agg(where: str, params: tuple) -> dict:
            r = self.db.one(
                "SELECT COALESCE(SUM(kind='llm'),0) calls, COALESCE(SUM(prompt_tokens),0) pt, COALESCE(SUM(completion_tokens),0) ct,"
                " COALESCE(SUM(credits),0) credits, COALESCE(SUM(CASE WHEN kind='llm' THEN seconds END),0) llm_seconds,"
                " COALESCE(SUM(chars),0) tts_chars FROM usage" + where, params)
            return {"llm_calls": int(r["calls"]), "prompt_tokens": int(r["pt"]), "completion_tokens": int(r["ct"]),
                    "tokens": int(r["pt"] + r["ct"]), "credits": round(r["credits"], 3),
                    "usd": round(r["credits"] * config.CREDIT_USD, 4), "llm_seconds": round(r["llm_seconds"], 1),
                    "tts_chars": int(r["tts_chars"])}
        out = {"total": agg("", ())}
        if task_id is not None:
            out["task"] = agg(" WHERE task_id=?", (task_id,))
        return out

    def breakdown(self) -> dict:
        """Для панели баланса: расход по моделям, по монстрам, по задачам, по видам (LLM / голос / распознавание) и история."""
        by_model = self.db.query(
            "SELECT provider, COALESCE(model,'') model, SUM(kind='llm') calls, SUM(prompt_tokens) pt, SUM(completion_tokens) ct,"
            " SUM(credits) credits FROM usage WHERE kind='llm' GROUP BY provider, model ORDER BY calls DESC")
        by_monster = self.db.query(
            "SELECT t.monster_id, m.name, SUM(u.kind='llm') calls, SUM(u.prompt_tokens+u.completion_tokens) tokens,"
            " SUM(u.credits) credits, SUM(u.chars) tts_chars FROM usage u JOIN tasks t ON t.id=u.task_id"
            " LEFT JOIN monsters m ON m.id=t.monster_id WHERE t.monster_id IS NOT NULL GROUP BY t.monster_id ORDER BY calls DESC")
        by_kind = self.db.query(
            "SELECT kind, COUNT(*) n, SUM(prompt_tokens) pt, SUM(completion_tokens) ct, SUM(chars) chars,"
            " SUM(seconds) seconds, SUM(credits) credits FROM usage GROUP BY kind")
        recent = self.db.query(
            "SELECT id, task_id, kind, purpose, provider, model, prompt_tokens, completion_tokens, chars, credits, seconds,"
            " created_at FROM usage ORDER BY id DESC LIMIT 25")
        return {"by_model": by_model, "by_monster": by_monster, "by_kind": {r["kind"]: r for r in by_kind},
                "by_task": self.per_task(12), "recent": recent}

    def per_task(self, limit: int = 8) -> list[dict]:
        """Сколько вызовов/токенов/кредитов потратила каждая из последних задач — видно, как падает цена повторных задач."""
        rows = self.db.query(
            "SELECT task_id, SUM(kind='llm') calls, SUM(prompt_tokens+completion_tokens) tokens, SUM(credits) credits"
            " FROM usage WHERE task_id IS NOT NULL GROUP BY task_id ORDER BY task_id DESC LIMIT ?", (limit,))
        return [{"task_id": r["task_id"], "llm_calls": int(r["calls"] or 0), "tokens": int(r["tokens"] or 0),
                 "credits": round(r["credits"] or 0, 3)} for r in reversed(rows)]
