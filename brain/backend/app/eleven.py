"""ElevenLabs: «мозг» (LLM через Agents Platform), распознавание речи (Scribe) и баланс подписки.

КАК ElevenLabs СТАНОВИТСЯ «МОЗГОМ» (всё — документированные API):
  1. Один раз создаём текстового агента-оркестратора:  POST /v1/convai/agents/create
     (conversation.text_only = true, разрешены overrides для prompt, llm и first_message).
     Его id сохраняется в БД (meta.eleven_brain_agent_id) или задаётся в .env (ELEVENLABS_BRAIN_AGENT_ID).
  2. На каждый вызов мозга берём подписанный URL:  GET /v1/convai/conversation/get-signed-url?agent_id=...
     и открываем WebSocket-разговор в текстовом режиме.
  3. Первым сообщением (conversation_initiation_client_data) подменяем системный промпт и МОДЕЛЬ
     (claude / gpt / gemini / glm / qwen … — список в settings.py), затем отправляем user_message.
  4. Собираем agent_response — это и есть ответ модели.
Ключ API используется только на сервере.

ОГРАНИЧЕНИЯ: проверено на поддельном WebSocket-сервере (tests/test_eleven.py), а не на живом API.
Точная стоимость разговора берётся из GET /v1/convai/conversations/{id}, если ElevenLabs её отдаёт;
иначе в учёте остаётся только оценка токенов.
"""
import json
import threading
import time

import httpx
from websockets.exceptions import ConnectionClosed

from . import cancel, config


class ElevenError(RuntimeError):
    pass


def _headers() -> dict:
    return {"xi-api-key": config.ELEVENLABS_API_KEY}


# Какое право ключа ElevenLabs нужно для какого действия (ключи можно ограничивать по разделам).
PERMISSION_HINTS = {
    "convai_write": "ElevenLabs Agents (Conversational AI) — WRITE: needed once to create the brain agent "
                    "(or create an agent yourself and put its id into ELEVENLABS_BRAIN_AGENT_ID)",
    "convai_read": "ElevenLabs Agents (Conversational AI) — READ: needed for every brain call (signed conversation URL)",
    "text_to_speech": "Text to Speech: needed for the monster's voice",
    "speech_to_text": "Speech to Text: needed for microphone input (Scribe)",
    "user_read": "User — READ: needed to show the real credit balance",
    "sound_generation": "Sound Effects: needed for the monsters' arrive/working/done sounds (optional, FRANK_THEATRICS=0 turns them off)",
}


def api_error(r: httpx.Response, action: str) -> "ElevenError":
    """Понятное сообщение об ошибке ElevenLabs. Для нехватки прав — что именно включить у ключа."""
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = None
    if isinstance(detail, dict):
        msg = str(detail.get("message") or detail)
        if detail.get("status") == "missing_permissions" or "missing the permission" in msg:
            perm = next((p for p in PERMISSION_HINTS if p in msg), None)
            hint = PERMISSION_HINTS.get(perm, "the permission named above")
            return ElevenError(
                f"{action}: your ElevenLabs API key is missing the permission '{perm or '?'}'. "
                f"Fix: elevenlabs.io → Developers → API Keys → edit the key (or create a new one) and enable {hint}. "
                f"Then update ELEVENLABS_API_KEY in backend/.env if the key changed and restart the backend.")
        if r.status_code == 401:
            return ElevenError(f"{action}: ElevenLabs rejected the API key ({msg}). Check ELEVENLABS_API_KEY in backend/.env.")
        return ElevenError(f"{action}: ElevenLabs returned {r.status_code}: {msg[:300]}")
    return ElevenError(f"{action}: ElevenLabs returned {r.status_code}: {r.text[:300]}")


class ElevenBrain:
    """Текстовые «вызовы мозга» через агента ElevenLabs."""

    def __init__(self, get_agent_id, save_agent_id, transport=None, ws_connect=None):
        self._get_agent_id = get_agent_id        # функции чтения/записи id агента в БД
        self._save_agent_id = save_agent_id
        self._transport = transport              # для тестов: поддельный HTTP
        self._ws_connect = ws_connect            # для тестов: поддельный WebSocket
        self._lock = threading.Lock()

    def _http(self) -> httpx.Client:
        return httpx.Client(base_url=config.ELEVENLABS_API_URL, timeout=60, transport=self._transport, headers=_headers())

    # ------------------------------------------------------------------ агент-оркестратор
    def agent_id(self) -> str:
        if config.ELEVENLABS_BRAIN_AGENT_ID:
            return config.ELEVENLABS_BRAIN_AGENT_ID
        with self._lock:
            existing = self._get_agent_id()
            if existing:
                return existing
            body = {
                "name": "Frankenstein Orchestrator",
                "conversation_config": {
                    "agent": {"first_message": "", "language": "en",
                              "prompt": {"prompt": "You are the orchestration brain of Frankenstein. Follow the system "
                                                   "instructions given at the start of each conversation exactly.",
                                         "llm": config.ELEVENLABS_LLM, "temperature": 0, "max_tokens": -1}},
                    "conversation": {"text_only": True},
                },
                "platform_settings": {"overrides": {"conversation_config_override": {
                    "agent": {"prompt": {"prompt": True, "llm": True}, "first_message": True, "language": True},
                    "conversation": {"text_only": True}}}},
                "tags": ["frankenstein"],
            }
            with self._http() as http:
                r = http.post("/v1/convai/agents/create", json=body)
            if r.status_code >= 400:
                raise api_error(r, "could not create the ElevenLabs brain agent")
            agent = r.json()["agent_id"]
            self._save_agent_id(agent)
            return agent

    def _signed_url(self, agent: str) -> str:
        with self._http() as http:
            r = http.get("/v1/convai/conversation/get-signed-url", params={"agent_id": agent})
        if r.status_code >= 400:
            raise api_error(r, "could not open a brain conversation (get-signed-url)")
        return r.json()["signed_url"]

    # ------------------------------------------------------------------ один вызов
    def complete(self, system: str, user: str, model: str, timeout: int | None = None) -> tuple[str, dict]:
        """Возвращает (текст ответа, {"conversation_id": ...})."""
        url = self._signed_url(self.agent_id())
        connect = self._ws_connect
        if connect is None:
            from websockets.sync.client import connect as ws_connect
            connect = lambda u: ws_connect(u, open_timeout=30, max_size=None)   # noqa: E731
        deadline = time.time() + (timeout or config.ELEVENLABS_BRAIN_TIMEOUT_SEC)
        parts: list[str] = []
        conv_id = None
        sent = False
        last_at = 0.0
        with connect(url) as ws:
            ws.send(json.dumps({
                "type": "conversation_initiation_client_data",
                "conversation_config_override": {
                    "agent": {"prompt": {"prompt": system, "llm": model}, "first_message": ""},
                    "conversation": {"text_only": True}},
            }))
            while True:
                cancel.check()
                if time.time() > deadline:
                    raise ElevenError(f"no complete answer within {timeout or config.ELEVENLABS_BRAIN_TIMEOUT_SEC}s")
                try:
                    raw = ws.recv(timeout=1.0)
                except ConnectionClosed:
                    # ElevenLabs закрыл разговор: если ответ уже пришёл — он полный, иначе это ошибка
                    if parts:
                        break
                    raise ElevenError("the ElevenLabs conversation closed before an answer arrived")
                except TimeoutError:
                    # ответ пришёл и новых кусков нет 3 секунды — считаем его законченным
                    if parts and time.time() - last_at > 3.0:
                        break
                    continue
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "conversation_initiation_metadata":
                    conv_id = (msg.get("conversation_initiation_metadata_event") or {}).get("conversation_id")
                    if not sent:
                        ws.send(json.dumps({"type": "user_message", "text": user}))
                        sent = True
                elif kind == "ping":
                    ws.send(json.dumps({"type": "pong", "event_id": (msg.get("ping_event") or {}).get("event_id")}))
                elif kind == "agent_response":
                    parts.append((msg.get("agent_response_event") or {}).get("agent_response", ""))
                    last_at = time.time()
                elif kind == "agent_response_correction" and parts:
                    parts[-1] = (msg.get("agent_response_correction_event") or {}).get("corrected_agent_response", parts[-1])
                elif kind == "agent_response_complete" and parts:
                    break
                elif kind in ("client_error", "error"):
                    raise ElevenError(f"ElevenLabs agent error: {str(msg)[:300]}")
        text = "\n".join(p for p in parts if p).strip()
        if not text:
            raise ElevenError("the ElevenLabs agent returned an empty answer")
        return text, {"conversation_id": conv_id}

    # ------------------------------------------------------------------ стоимость разговора (если доступна)
    def conversation_cost(self, conversation_id: str) -> float | None:
        try:
            with self._http() as http:
                r = http.get(f"/v1/convai/conversations/{conversation_id}")
            if r.status_code >= 400:
                return None
            meta = r.json().get("metadata") or {}
            for k in ("cost", "credits", "total_cost"):
                if isinstance(meta.get(k), (int, float)):
                    return float(meta[k])
        except (httpx.HTTPError, ValueError):
            return None
        return None


# ----------------------------------------------------------------------------
# Распознавание речи — ElevenLabs Scribe:  POST /v1/speech-to-text (multipart)
# ----------------------------------------------------------------------------
def transcribe(audio: bytes, filename: str, mime: str, lang: str | None = None, transport=None) -> dict:
    if not config.ELEVENLABS_API_KEY:
        raise ElevenError("ELEVENLABS_API_KEY is not set (put it into backend/.env)")
    data = {"model_id": config.ELEVENLABS_STT_MODEL}
    if lang in ("en", "cs"):
        data["language_code"] = lang
    try:
        with httpx.Client(base_url=config.ELEVENLABS_API_URL, timeout=120, transport=transport, headers=_headers()) as http:
            r = http.post("/v1/speech-to-text", data=data, files={"file": (filename, audio, mime)})
    except httpx.HTTPError as exc:
        raise ElevenError(f"speech-to-text request failed: {exc}") from exc
    if r.status_code >= 400:
        raise api_error(r, "speech recognition failed")
    body = r.json()
    words = [w for w in body.get("words") or [] if w.get("type", "word") == "word"]
    seconds = max((w.get("end") or 0) for w in words) if words else 0.0
    return {"text": (body.get("text") or "").strip(), "language": body.get("language_code"), "seconds": seconds}


# ----------------------------------------------------------------------------
# Баланс: реальная подписка ElevenLabs (символы/кредиты), кэш на 60 секунд
# ----------------------------------------------------------------------------
_sub_cache: dict = {"at": 0.0, "value": None}


def subscription(transport=None) -> dict | None:
    if not config.ELEVENLABS_API_KEY:
        return None
    if time.time() - _sub_cache["at"] < 60 and transport is None:
        return _sub_cache["value"]
    try:
        with httpx.Client(base_url=config.ELEVENLABS_API_URL, timeout=20, transport=transport, headers=_headers()) as http:
            r = http.get("/v1/user/subscription")
        if r.status_code >= 400:
            value = {"error": str(api_error(r, "balance unavailable"))[:400]}
        else:
            b = r.json()
            value = {"used": b.get("character_count"), "limit": b.get("character_limit"), "tier": b.get("tier"),
                     "reset_unix": b.get("next_character_count_reset_unix")}
    except httpx.HTTPError as exc:
        value = {"error": str(exc)[:120]}
    _sub_cache.update(at=time.time(), value=value)
    return value
