"""Живой голосовой звонок с монстром в интерфейсе monster-lab (по образцу frankenstein/forge/voice.py коллег).

Для каждого монстра — свой агент ElevenLabs с «клиентскими инструментами», которые выполняет БРАУЗЕР:
  start_run(question) -> запуск задачи через наш движок, check_run(run_id), answer_input(run_id, answer),
  confirm_spend(run_id, approve). Сам агент задачу не решает — он собирает вопрос голосом и озвучивает результат.
Агент создаётся один раз (при публикации) и обновляется на месте; браузер получает короткоживущую подписанную ссылку.
"""
import os

import httpx

from . import config

API = "https://api.elevenlabs.io/v1/convai"
TOOLS_KEY = "lab_voice_tools:v1"
# модель озвучки агента как у коллег; если ключ её не принимает — запасная (поддерживает чешский)
TTS_MODEL = os.getenv("FRANK_VOICE_AGENT_TTS", "eleven_v4_turbo")
TTS_FALLBACK = "eleven_flash_v2_5"


class VoiceAgentError(RuntimeError):
    pass


def _tool(name: str, description: str, props: dict, required: list[str]) -> dict:
    return {"type": "client", "name": name, "description": description,
            "parameters": {"type": "object", "required": required, "properties": props},
            "expects_response": True, "response_timeout_secs": 30}


START_RUN = _tool("start_run", "Start the task with the user's request. Call once you know what the user wants.",
                  {"question": {"type": "string", "description": "The user's request, in their own words"}}, ["question"])
CHECK_RUN = _tool("check_run", "Check a running task. Returns its status and, when completed, the result to tell the user.",
                  {"run_id": {"type": "string", "description": "The run_id returned by start_run"}}, ["run_id"])
ANSWER_INPUT = _tool("answer_input", "Send the user's answer when check_run reports status needs_input.",
                     {"run_id": {"type": "string", "description": "The run_id"},
                      "answer": {"type": "string", "description": "The user's answer to the question"}}, ["run_id", "answer"])
CONFIRM_SPEND = _tool("confirm_spend", "Send the user's yes/no when check_run reports status needs_confirmation.",
                      {"run_id": {"type": "string", "description": "The run_id"},
                       "approve": {"type": "boolean", "description": "true only if the user clearly said yes"}}, ["run_id", "approve"])


def agent_prompt(name: str, topic: str, purpose: str, language: str) -> str:
    cz = ("\n- Always speak Czech. Results and questions from the tools arrive in English: say them in natural Czech."
          if language == "cs" else "")
    return f"""You are {name}, a monster built by Doctor Frankenstein. You are the assistant for everything about {topic}.
{purpose}

You do not do the task yourself. When the user asks something, call start_run with their request, then call check_run:
- queued/running/waiting: say you are working on it; hired research agents can take minutes.
- needs_input: ask the user the question and send their reply with answer_input.
- needs_confirmation: read the question (it may mention a cost) and call confirm_spend with the user's clear yes or no.
- completed: say the "speech" text from the result, close to word for word; the full report is on screen.
- failed: say so plainly in one sentence.
Politely decline anything outside {topic}. Keep every reply short; you are speaking, not writing.{cz}"""


class LabVoice:
    def __init__(self, db, transport: httpx.BaseTransport | None = None):
        self.db, self.transport = db, transport

    def configured(self) -> bool:
        return bool(config.ELEVENLABS_API_KEY)

    def _call(self, method: str, path: str, body: dict | None = None, params: dict | None = None) -> dict:
        if not self.configured():
            raise VoiceAgentError("ELEVENLABS_API_KEY is not set")
        with httpx.Client(timeout=60, transport=self.transport) as c:
            r = c.request(method, API + path, json=body, params=params, headers={"xi-api-key": config.ELEVENLABS_API_KEY})
        if r.status_code >= 400:
            from .eleven import api_error
            raise VoiceAgentError(str(api_error(r, f"ElevenLabs {method} {path}")))
        return r.json() if r.content else {}

    def _tool_ids(self) -> list[str]:
        row = self.db.one("SELECT value FROM meta WHERE key=?", (TOOLS_KEY,))
        if row and row["value"]:
            return row["value"].split(",")
        ids = [self._call("POST", "/tools", {"tool_config": t})["id"] for t in (START_RUN, CHECK_RUN, ANSWER_INPUT, CONFIRM_SPEND)]
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (TOOLS_KEY, ",".join(ids)))
        return ids

    def _body(self, *, sid: str, name: str, topic: str, purpose: str, language: str, voice_id: str, model: str) -> dict:
        return {"name": f"Monster: {name}", "tags": ["frankenstein-brain", f"monster:{sid}"],
                "conversation_config": {
                    "agent": {"first_message": (f"Grr. Jsem {name}. Co potřebuješ ohledně tématu {topic}?" if language == "cs"
                                                else f"Grr. I am {name}, your {topic} monster. What do you need?"),
                              "language": language,
                              "prompt": {"prompt": agent_prompt(name, topic, purpose, language), "llm": config.ELEVENLABS_LLM_FAST,
                                         "tool_ids": self._tool_ids(), "temperature": 0}},
                    "tts": {"model_id": model, "voice_id": voice_id}},
                "platform_settings": {"auth": {"enable_auth": True}}}

    def ensure_agent(self, *, sid: str, name: str, topic: str, purpose: str, language: str, voice_id: str,
                     agent_id: str | None = None) -> str:
        """Создать агента (или обновить существующего на месте). Возвращает agent_id."""
        last = None
        for model in (TTS_MODEL, TTS_FALLBACK):
            body = self._body(sid=sid, name=name, topic=topic, purpose=purpose, language=language, voice_id=voice_id, model=model)
            try:
                if agent_id:
                    self._call("PATCH", f"/agents/{agent_id}", body)
                    return agent_id
                return self._call("POST", "/agents/create", body)["agent_id"]
            except VoiceAgentError as exc:
                last = exc
                if "missing the permission" in str(exc):
                    raise
        raise last or VoiceAgentError("could not create the voice agent")

    def signed_url(self, agent_id: str) -> str:
        return self._call("GET", "/conversation/get-signed-url", params={"agent_id": agent_id})["signed_url"]
