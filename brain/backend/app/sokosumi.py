"""Клиент Sokosumi (маркетплейс агентов Masumi).

Sokosumi — не чат-API, а биржа агентов: вы создаёте JOB у агента, затем опрашиваете его статус.
Поэтому один «запрос к мозгу» = один job: он стоит кредитов и занимает от секунд до минут.
Из этого следует главная стратегия экономии проекта: МЕНЬШЕ вызовов, но крупнее (см. builder.py, executor.py).

ВАЖНО: формат ответа job (где лежит текст результата) в открытой документации не описан.
Клиент ищет его в нескольких типичных полях, а при неудаче сохраняет сырой JSON в
backend/data/sokosumi_last_job.json — по нему можно поправить _extract_text().
Проверить всё на вашем ключе: python tools/sokosumi_probe.py --test <agent_id>
"""
import json
import time
from pathlib import Path

import httpx

from . import config

TERMINAL_OK = {"completed"}
TERMINAL_BAD = {"failed", "refund_resolved", "dispute_resolved"}
# поля job, где обычно лежит текст результата (порядок = приоритет)
RESULT_KEYS = ("result", "output", "resultText", "response", "text", "content", "message", "answer")
SKIP_KEYS = {"id", "status", "agentId", "userId", "createdAt", "updatedAt", "name", "credits", "price", "input",
             "inputData", "inputSchema", "error", "errorMessage"}


class SokosumiError(RuntimeError):
    pass


class SokosumiClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None, transport=None):
        self.api_key = (api_key or config.SOKOSUMI_API_KEY or "").strip()
        bad = next((i + 1 for i, ch in enumerate(self.api_key) if not ch.isascii()), None)
        if bad:
            # частая ошибка: часть ключа набрана в русской раскладке (латинская M -> «Ь») или к ключу прилипло слово
            raise SokosumiError(f"SOKOSUMI_API_KEY contains a non-Latin character at position {bad} "
                                f"('{self.api_key[bad - 1]}'); copy the key again from sokosumi.com and paste it into backend/.env")
        self.base = (base_url or config.SOKOSUMI_API_URL).rstrip("/")
        self._http = httpx.Client(base_url=self.base, timeout=60, transport=transport,
                                  headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})

    # ------------------------------------------------------------------ простые запросы
    def _get(self, path: str, **params) -> dict:
        r = self._http.get(path, params=params or None)
        if r.status_code >= 400:
            raise SokosumiError(f"GET {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()

    def list_agents(self, limit: int = 100, pages: int = 5) -> list[dict]:
        """Список агентов. Sokosumi принимает limit <= 100, поэтому больше — постранично (offset), не больше `pages` страниц."""
        limit, out = min(limit, 100), []
        for page in range(pages):
            data = self._get("/agents", limit=limit, offset=page * limit) if page else self._get("/agents", limit=limit)
            batch = data.get("data", data) if isinstance(data, dict) else data
            batch = batch if isinstance(batch, list) else []
            out += batch
            if len(batch) < limit:
                break
        return out

    def agent(self, agent_id: str) -> dict:
        d = self._get(f"/agents/{agent_id}")
        return d.get("data", d)

    def input_schema(self, agent_id: str) -> dict:
        d = self._get(f"/agents/{agent_id}/input-schema")
        return d.get("data", d)

    def credits(self) -> dict | None:
        try:
            me = self._get("/users/registered")
            uid = (me.get("data") or me).get("id")
            d = self._get(f"/users/{uid}/credits")
            return d.get("data", d)
        except (SokosumiError, httpx.HTTPError):
            return None

    # ------------------------------------------------------------------ job = один «вызов мозга»
    def run(self, agent_id: str, prompt: str, *, timeout: int | None = None, on_poll=None,
            max_credits: float | None = None, schema: dict | None = None) -> tuple[str, dict]:
        """Создать job, дождаться результата. Возвращает (текст, сырой job). max_credits — потолок цены задания;
        schema — заранее подгруженная схема полей (экономит запрос и время)."""
        schema = schema or self.input_schema(agent_id)
        fields = _schema_fields(schema)
        input_data, schema_body = _fill_inputs(fields, prompt)
        body = {"inputSchema": schema_body, "inputData": input_data}
        if max_credits:
            body["maxCredits"] = max_credits          # Sokosumi не спишет больше этого
        r = self._http.post(f"/agents/{agent_id}/jobs", json=body)
        if r.status_code >= 400:
            raise SokosumiError(f"create job -> {r.status_code}: {r.text[:300]}")
        body = r.json()
        job_id = (body.get("data") or body).get("id")
        if not job_id:
            raise SokosumiError(f"no job id in response: {str(body)[:200]}")

        deadline = time.time() + (timeout or config.SOKOSUMI_JOB_TIMEOUT_SEC)
        delay = 2.0                                  # опрашиваем часто в начале, потом реже (уменьшаем простой)
        while time.time() < deadline:
            time.sleep(delay)
            delay = min(delay * 1.4, 10.0)
            job = self._get(f"/jobs/{job_id}")
            job = job.get("data", job)
            status = str(job.get("status", "")).lower()
            if on_poll:
                on_poll(status)
            if status in TERMINAL_OK:
                text = _extract_text(job)
                if not text:
                    self._dump(job)
                    raise SokosumiError("job completed but no result text was found; raw job saved to "
                                        "backend/data/sokosumi_last_job.json (adjust _extract_text)")
                return text, job
            if status in TERMINAL_BAD:
                self._dump(job)
                raise SokosumiError(f"job {job_id} ended with status '{status}'")
        raise SokosumiError(f"job {job_id} did not finish within {timeout or config.SOKOSUMI_JOB_TIMEOUT_SEC}s")

    @staticmethod
    def _dump(job: dict) -> None:
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            Path(config.DATA_DIR / "sokosumi_last_job.json").write_text(json.dumps(job, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass


# ----------------------------------------------------------------------------
# Вспомогательные функции (чистые — удобно тестировать)
# ----------------------------------------------------------------------------
def _schema_fields(schema: dict) -> list[dict]:
    """В схеме агента поля лежат в input_data (или в inputSchema.input_data)."""
    s = schema.get("inputSchema", schema)
    return list(s.get("input_data") or s.get("inputData") or [])


TEXT_TYPES = ("string", "text", "textarea", "")
HINTS = ("question", "query", "prompt", "topic", "research", "message", "task", "request", "instruction", "input", "text")


def _optional(f: dict) -> bool:
    return any(str(v.get("validation")) == "optional" for v in (f.get("validations") or []) if isinstance(v, dict))


def _has_default(f: dict) -> bool:
    meta = f.get("data") or {}
    return meta.get("default") is not None or bool(meta.get("values") or meta.get("options"))


def fill_plan(fields: list[dict]) -> tuple[dict | None, list[str]]:
    """Куда положить вопрос и какие ОБЯЗАТЕЛЬНЫЕ поля мы честно заполнить не можем.
    Вопрос идёт в обязательное текстовое поле (по подсказке в id/названии), иначе — в лучшее необязательное."""
    real = [f for f in fields if str(f.get("type", "")).lower() != "none"]
    textual = [f for f in real if str(f.get("type", "")).lower() in TEXT_TYPES]

    def rank(f):
        name = f"{f.get('id', '')} {f.get('name', '')}".lower()
        return (_optional(f), -sum(h in name for h in HINTS))
    main = min(textual, key=rank) if textual else None
    missing = [str(f.get("name") or f.get("id")) for f in real
               if f is not main and not _optional(f) and not _has_default(f) and str(f.get("type", "")).lower() in TEXT_TYPES]
    return main, missing


def _fill_inputs(fields: list[dict], prompt: str) -> tuple[dict, dict]:
    """Кладём наш вопрос в подходящее текстовое поле, остальные — значениями по умолчанию. Если у агента есть обязательные
    поля, которые нечем честно заполнить (например, «Company Name»), задание НЕ создаём — кредиты не тратятся впустую."""
    main, missing = fill_plan(fields)
    if main is None:
        raise SokosumiError("agent has no free-text input field, so it cannot be used as an LLM")
    if missing:
        raise SokosumiError(f"agent needs fields we cannot fill automatically: {', '.join(missing)}")
    data: dict = {}
    for f in fields:
        fid = f.get("id")
        if str(f.get("type", "")).lower() == "none":
            continue                                  # информационный блок, не поле ввода
        if f is main:
            data[fid] = prompt
            continue
        meta = f.get("data") or {}
        validations = f.get("validations") or []
        optional = any(str(v.get("validation")) == "optional" for v in validations if isinstance(v, dict))
        default = meta.get("default")
        options = meta.get("values") or meta.get("options")
        if default is not None:
            data[fid] = default
        elif options:
            data[fid] = options[0] if not isinstance(options[0], dict) else options[0].get("value", options[0])
        elif not optional:
            t = str(f.get("type", "")).lower()
            data[fid] = False if t == "boolean" else 0 if t == "number" else ""
    return data, {"input_data": fields}


def _extract_text(job: dict) -> str:
    """Достаём текст результата из job: известные ключи, затем вложенные объекты, затем самая длинная строка."""
    for k in RESULT_KEYS:
        v = job.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            inner = _extract_text(v)
            if inner:
                return inner
    best = ""
    for k, v in job.items():
        if k in SKIP_KEYS:
            continue
        if isinstance(v, str) and len(v) > len(best) and len(v) > 20:
            best = v
        elif isinstance(v, (dict, list)):
            inner = _extract_text(v) if isinstance(v, dict) else ""
            if len(inner) > len(best):
                best = inner
    return best.strip()


def job_credits(job: dict, fallback: float = 0.0) -> float:
    """Сколько кредитов списал job (поле может называться по-разному)."""
    for k in ("credits", "price", "cost", "creditsUsed"):
        v = job.get(k)
        if isinstance(v, (int, float)):
            return float(v)
        if isinstance(v, dict):
            for kk in ("credits", "amount", "total"):
                if isinstance(v.get(kk), (int, float)):
                    return float(v[kk])
    return fallback
