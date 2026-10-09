"""«Мозг» Frankenstein: единая обёртка над провайдерами (ElevenLabs Agents / Sokosumi / OpenAI).

Остальной код знает только два метода: text() и json(). Все остальные заботы — здесь:
  * выбор провайдера: ElevenLabs (LLM внутри Agents Platform, модель на каждый вызов — см. eleven.py),
    Sokosumi (маркетплейс агентов) или OpenAI-совместимый. Провайдер и модель меняются в интерфейсе (settings.py),
  * маршрутизация моделей: простые шаги (диагноз, чистка) — быстрая модель, код и планы — основная,
  * учёт расхода: каждый вызов пишется в таблицу usage (токены, кредиты, секунды),
  * события LLM_CALL_STARTED/DONE — по ним интерфейс показывает, что голова «думает»
    (с Sokosumi один вызов может идти минуты, пользователь должен это видеть).

В тестах подменяется метод _chat() (см. tests/helpers.py: ScriptedLLM).
"""
import json
import re
import time
from typing import Callable

from . import cancel, config
from .sokosumi import SokosumiClient, SokosumiError, job_credits
from .llm_cache import CACHEABLE
from .llm_cache import make_key as cache_key
from .usage import Ledger, current_task, estimate_tokens, task_budget


class LLMError(RuntimeError):
    pass


# Шаги, которым хватает быстрой (дешёвой) модели. Генерация кода, тестов и планов — на основной.
# быстрая (дешёвая) модель: короткие служебные задачи и ГЕНЕРАЦИЯ ЗНАНИЙ (проверяется кодом; при провале — основная модель)
FAST_PURPOSES = {"diagnosing", "pruning", "abstracting", "routing", "knowledge", "knowledge_extend"}


def resolve_provider() -> str:
    """elevenlabs | sokosumi | openai | none"""
    if config.LLM_PROVIDER in ("elevenlabs", "sokosumi", "openai"):
        return config.LLM_PROVIDER
    if config.ELEVENLABS_API_KEY:
        return "elevenlabs"
    if config.SOKOSUMI_API_KEY:
        return "sokosumi"
    return "openai" if config.OPENAI_API_KEY else "none"


class LLM:
    def __init__(self, ledger: Ledger | None = None, on_event: Callable | None = None) -> None:
        self.provider = resolve_provider()
        self.ledger = ledger
        self.on_event = on_event          # on_event("LLM_CALL_STARTED", purpose=..., ...)
        self._openai = None
        self._soko: SokosumiClient | None = None
        self._agent_price: float | None = None
        self._last_meta: dict = {}
        self._purpose = ""
        self.settings = None          # app.settings.Settings — подключает Brain (провайдер/модель из интерфейса)
        self.eleven = None            # app.eleven.ElevenBrain — подключает Brain
        self.cache = None             # app.llm_cache.LLMCache — подключает Brain (проверенные ответы: 0 токенов на повтор)
        self._last_tokens = 0
        self.extra_fast_purposes: set[str] = set()

    def _active(self) -> str:
        """Текущий провайдер: из настроек интерфейса, иначе из .env."""
        return self.settings.get()["llm_provider"] if self.settings else self.provider

    def model_for(self, purpose: str) -> str:
        p = self._active()
        if p == "elevenlabs":
            s = self.settings.get() if self.settings else {}
            fast = s.get("llm_model_fast", config.ELEVENLABS_LLM_FAST)
            main = s.get("llm_model", config.ELEVENLABS_LLM)
            return fast if purpose in FAST_PURPOSES or purpose in self.extra_fast_purposes else main
        if p == "sokosumi":
            return config.SOKOSUMI_AGENT_ID or "sokosumi-agent"
        return config.OPENAI_MODEL

    # ------------------------------------------------------------------ состояние
    def configured(self) -> bool:
        if self._active() == "elevenlabs":
            return bool(config.ELEVENLABS_API_KEY)
        if self._active() == "sokosumi":
            return bool(config.SOKOSUMI_API_KEY and config.SOKOSUMI_AGENT_ID)
        return self._active() == "openai" and bool(config.OPENAI_API_KEY)

    def describe(self) -> dict:
        return {"provider": self._active(), "configured": self.configured(),
                "model": self.model_for("building"), "model_fast": self.model_for("diagnosing"),
                "missing": self._missing()}

    def _missing(self) -> str | None:
        p = self._active()
        if p == "elevenlabs":
            return None if config.ELEVENLABS_API_KEY else "ELEVENLABS_API_KEY"
        if p == "sokosumi":
            if not config.SOKOSUMI_API_KEY:
                return "SOKOSUMI_API_KEY"
            if not config.SOKOSUMI_AGENT_ID:
                return "SOKOSUMI_AGENT_ID"
        if p == "none":
            return "ELEVENLABS_API_KEY"
        if p == "openai" and not config.OPENAI_API_KEY:
            return "OPENAI_API_KEY"
        return None

    # ------------------------------------------------------------------ публичный интерфейс
    def text(self, system: str, user: str, purpose: str = "") -> str:
        """Свободный текстовый ответ (генерация кода)."""
        return self._call([{"role": "system", "content": system}, {"role": "user", "content": user}], False, purpose)

    def json(self, system: str, user: str, validate: Callable[[dict], list[str]] | None = None,
             retries: int = 1, purpose: str = "", cache_text: str | None = None) -> dict:
        """JSON-ответ. validate(obj) -> список ошибок; при ошибках модель переспрашивается (каждый повтор = ещё один вызов,
        поэтому retries по умолчанию 1)."""
        messages = [{"role": "system", "content": system + "\n\nRespond with a single JSON object only."},
                    {"role": "user", "content": user}]
        # КЭШ: тот же запрос к той же модели уже получал ПРОВЕРЕННЫЙ ответ — берём его (проверку повторяем: состояние могло измениться)
        key = None
        if self.cache is not None and purpose in CACHEABLE:
            key = cache_key(self._active(), self.model_for(purpose), purpose, system, cache_text if cache_text is not None else user)
            hit = self.cache.get(key)
            if hit is not None and isinstance(hit["response"], dict) and not (validate(hit["response"]) if validate else []):
                self._emit("LLM_CACHE_HIT", purpose=purpose, saved_tokens=hit["tokens"], saved_seconds=hit["seconds"])
                if self.ledger:
                    self.ledger.record("cache", purpose=purpose, provider=self._active(), model=self.model_for(purpose))
                return hit["response"]
        spent, t_start = 0, time.time()
        last_problem = "unknown"
        for _ in range(retries + 1):
            raw = self._call(messages, True, purpose)
            spent += self._last_tokens
            try:
                obj = json.loads(_extract_json(raw))
                if not isinstance(obj, dict):
                    raise ValueError("top-level JSON value must be an object")
                errors = validate(obj) if validate else []
            except (json.JSONDecodeError, ValueError) as exc:
                obj, errors = None, [f"invalid JSON: {exc}"]
            if not errors:
                if key is not None:
                    self.cache.put(key, purpose, self.model_for(purpose), obj, spent, round(time.time() - t_start, 2), current_task.get())
                return obj
            last_problem = "; ".join(errors)
            messages += [{"role": "assistant", "content": raw[:6000]},
                         {"role": "user", "content": "Your JSON was rejected. Fix these problems and answer again with JSON only:\n- "
                          + "\n- ".join(errors)}]
        self._save_failure(messages, last_problem)
        raise LLMError(f"the model did not give a valid answer after {retries + 1} attempts: {last_problem[:300]} "
                       f"(the full exchange is saved to backend/data/llm_last_failure.txt)")

    @staticmethod
    def _save_failure(messages: list[dict], problem: str) -> None:
        """Для диагностики: вся «переписка» с моделью, которая так и не дала валидный ответ (только локальный файл)."""
        try:
            config.DATA_DIR.mkdir(parents=True, exist_ok=True)
            text = "\n\n".join(f"===== {m['role'].upper()} =====\n{m['content']}" for m in messages)
            (config.DATA_DIR / "llm_last_failure.txt").write_text(f"PROBLEM: {problem}\n\n{text}", encoding="utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ вызов с учётом расхода
    def _call(self, messages: list[dict], json_mode: bool, purpose: str) -> str:
        cancel.check()                       # задачу отменили — новых платных вызовов не делаем
        provider, model = self._active(), self.model_for(purpose)
        self._check_budget(messages, purpose)
        self._emit("LLM_CALL_STARTED", purpose=purpose, provider=provider, model=model)
        self._last_meta = {}
        self._purpose = purpose
        t0 = time.time()
        try:
            text = self._chat(messages, json_mode)
        except (LLMError, cancel.TaskCancelled) as exc:
            self._emit("LLM_CALL_FAILED", purpose=purpose, error=str(exc)[:200])
            raise
        seconds = round(time.time() - t0, 2)
        meta = self._last_meta
        pt = meta.get("prompt_tokens") or estimate_tokens("".join(m["content"] for m in messages))
        ct = meta.get("completion_tokens") or estimate_tokens(text)
        credits = float(meta.get("credits") or 0.0)
        ref = meta.get("conversation_id")
        self._last_tokens = pt + ct
        if self.ledger:
            self.ledger.record("llm", purpose=purpose, provider=provider, model=model, prompt_tokens=pt,
                               completion_tokens=ct, credits=credits, seconds=seconds, ref=ref)
            if ref and self.eleven:          # точную стоимость ElevenLabs дозапрашиваем в фоне (если API её отдаёт)
                import threading
                threading.Thread(target=self._fetch_cost, args=(ref,), daemon=True).start()
        self._emit("LLM_CALL_DONE", purpose=purpose, provider=provider, model=model, tokens=pt + ct, credits=credits,
                   seconds=seconds)
        return text

    def _check_budget(self, messages: list[dict], purpose: str) -> None:
        """Бюджет токенов задачи (идея Policy/BudgetExceeded из проекта коллеги): не даём задаче «съесть» больше лимита."""
        budget, task = task_budget.get(), current_task.get()
        if not budget or not task or not self.ledger:
            return
        t = self.ledger.summary(task)["task"]
        used = t.get("prompt_tokens", 0) + t.get("completion_tokens", 0)
        need = estimate_tokens("".join(m["content"] for m in messages))
        if used + need > budget:
            self._emit("LLM_BUDGET_EXCEEDED", purpose=purpose, used=used, budget=budget)
            raise LLMError(f"token budget for this task is exhausted ({used} of {budget} tokens used); "
                           "stopping instead of spending more. Raise FRANK_MAX_TOKENS_PER_TASK if this task really needs it.")

    def _emit(self, event: str, **data) -> None:
        if self.on_event:
            self.on_event(event, **data)

    def _fetch_cost(self, ref: str) -> None:
        time.sleep(4)
        cost = self.eleven.conversation_cost(ref)
        if cost is not None and self.ledger:
            self.ledger.set_credits(ref, cost)

    # ------------------------------------------------------------------ провайдеры
    def _chat(self, messages: list[dict], json_mode: bool) -> str:
        p = self._active()
        if p == "elevenlabs":
            return self._chat_elevenlabs(messages, json_mode)
        if p == "sokosumi":
            return self._chat_sokosumi(messages, json_mode)
        if p == "openai":
            return self._chat_openai(messages, json_mode)
        raise LLMError("No LLM configured: set ELEVENLABS_API_KEY in backend/.env")

    def _chat_elevenlabs(self, messages: list[dict], json_mode: bool) -> str:
        if not config.ELEVENLABS_API_KEY:
            raise LLMError("ELEVENLABS_API_KEY is not set (put it into backend/.env)")
        if self.eleven is None:
            raise LLMError("ElevenLabs brain is not initialised")
        system = messages[0]["content"] + ELEVEN_TRANSPORT_RULE
        # повторы после ошибок валидации идут одним сообщением пользователя (агент получает всю «переписку»)
        user = "\n\n".join(m["content"] if i == 0 else f"[{m['role'].upper()}]\n{m['content']}"
                            for i, m in enumerate(messages[1:]))
        if json_mode:
            user += "\n\nReply with ONLY the JSON object - no markdown fences, no commentary."
        try:
            text, meta = self.eleven.complete(system, user, self.model_for(self._purpose))
        except cancel.TaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"ElevenLabs brain request failed: {exc}") from exc
        self._last_meta = {"conversation_id": meta.get("conversation_id")}
        return decode_eleven(text)

    def _chat_sokosumi(self, messages: list[dict], json_mode: bool) -> str:
        if not self.configured():
            raise LLMError(f"{self._missing()} is not set (put it into backend/.env)")
        if self._soko is None:
            self._soko = SokosumiClient()
        # Job — это одно текстовое поле, поэтому вся «переписка» склеивается в один промпт.
        prompt = "\n\n".join(f"### {m['role'].upper()}\n{m['content']}" for m in messages)
        if json_mode:
            prompt += "\n\n### FORMAT\nReply with ONLY the JSON object — no markdown fences, no commentary."
        try:
            text, job = self._soko.run(config.SOKOSUMI_AGENT_ID, prompt)
        except (SokosumiError, Exception) as exc:  # noqa: BLE001 — сеть, таймауты, неверный ключ
            raise LLMError(f"Sokosumi request failed: {exc}") from exc
        price = job_credits(job, fallback=self._price())
        self._last_meta = {"credits": price}
        return text

    def _price(self) -> float:
        """Цена job у выбранного агента (если API её отдаёт); кэшируем."""
        if self._agent_price is None:
            try:
                a = self._soko.agent(config.SOKOSUMI_AGENT_ID)   # type: ignore[union-attr]
                self._agent_price = job_credits(a, 0.0)
            except Exception:  # noqa: BLE001
                self._agent_price = 0.0
        return self._agent_price

    def _chat_openai(self, messages: list[dict], json_mode: bool) -> str:
        if not config.OPENAI_API_KEY:
            raise LLMError("OPENAI_API_KEY is not set (put it into backend/.env)")
        if self._openai is None:
            from openai import OpenAI
            self._openai = OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL, timeout=180, max_retries=3)
        kwargs = {"model": config.OPENAI_MODEL, "messages": messages}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            resp = self._openai.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        u = resp.usage
        if u:
            self._last_meta = {"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens}
        return resp.choices[0].message.content or ""


# ElevenLabs Agents вырезает из ОТВЕТА агента символ «*» и последовательности «##» (markdown-разметка для озвучки;
# проверено реальным запросом: вход доходит целым, искажается только выход). Для кода это смертельно: '=' * 60 -> '=' 60.
# Поэтому на время передачи модель пишет эти символы словами-токенами, а мы превращаем их обратно.
STAR_TOKEN, HASH_TOKEN = "@@STAR@@", "@@HASH@@"
ELEVEN_TRANSPORT_RULE = (
    "\n\nTRANSPORT RULE (critical): the channel that carries your reply DELETES the characters asterisk and hash. "
    f"Everywhere in your reply - code, comments, strings, JSON, prose - write {STAR_TOKEN} instead of every asterisk "
    f"and {HASH_TOKEN} instead of every hash sign. Example: x = 2 {STAR_TOKEN} 3  {HASH_TOKEN} comment;  "
    f"f({STAR_TOKEN}args, {STAR_TOKEN}{STAR_TOKEN}kw). They are converted back automatically. Never write the raw characters.")


_TOKEN_RUN = re.compile(r"@{1,2}(?:STAR|HASH)(?:@{0,4}(?:STAR|HASH))*@{1,2}")


def decode_eleven(text: str) -> str:
    """Токены -> символы. Модель (или канал) иногда СКЛЕИВАЕТ соседние токены: «**» приходит как @@STAR@@STAR@@
    вместо @@STAR@@@@STAR@@ (найдено в живом органе: строки '*STAR@@ Technical Questions'). Поэтому декодируем целую
    «серию» токенов: каждое STAR -> '*', каждое HASH -> '#'. Обычное слово STAR (метод STAR) без @@ не трогается."""
    return _TOKEN_RUN.sub(lambda m: "".join("*" if t == "STAR" else "#" for t in re.findall(r"STAR|HASH", m.group(0))), text)


def save_debug(purpose: str, raw: str) -> None:
    """Сырой ответ модели, который не удалось разобрать, — в backend/data/llm_last_failure.txt (только локально)."""
    try:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        (config.DATA_DIR / "llm_last_failure.txt").write_text(f"PURPOSE: {purpose}\n\n{raw}", encoding="utf-8")
    except OSError:
        pass


def _extract_json(raw: str) -> str:
    """Агенты Sokosumi любят добавлять пояснения вокруг JSON: берём всё от первой { до последней }."""
    cleaned = _strip_fences(raw)
    try:
        json.loads(cleaned)
        return cleaned
    except json.JSONDecodeError:
        a, b = cleaned.find("{"), cleaned.rfind("}")
        return cleaned[a:b + 1] if 0 <= a < b else cleaned


def _strip_fences(raw: str) -> str:
    """Модель иногда оборачивает JSON в ```json ... ``` — убираем."""
    raw = raw.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.S)
    return m.group(1) if m else raw
