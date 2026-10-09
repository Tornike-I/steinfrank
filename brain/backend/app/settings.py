"""Настройки провайдеров, которые можно менять из интерфейса без перезапуска (хранятся в БД, таблица meta).

Значения по умолчанию берутся из .env (config.py). Ключи API здесь НЕ хранятся и наружу не отдаются.
"""
from . import config
from .db import Database, dumps, loads

# Модели, которые ElevenLabs Agents принимает в поле llm (из официального API reference, сокращённый список).
ELEVENLABS_MODELS = [
    "claude-sonnet-4-5", "claude-sonnet-4", "claude-haiku-4-5", "claude-3-7-sonnet",
    "gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini",
    "gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash",
    "glm-45-air-fp8", "qwen3-30b-a3b", "gpt-oss-120b",
]
PROVIDERS = {
    "llm": ["elevenlabs", "sokosumi", "openai"],
    "stt": ["elevenlabs", "browser"],
    "tts": ["elevenlabs", "off"],
}


def defaults() -> dict:
    llm = config.LLM_PROVIDER
    if llm not in PROVIDERS["llm"]:
        llm = "elevenlabs" if config.ELEVENLABS_API_KEY else "sokosumi" if config.SOKOSUMI_API_KEY else \
              "openai" if config.OPENAI_API_KEY else "elevenlabs"
    return {"llm_provider": llm, "llm_model": config.ELEVENLABS_LLM, "llm_model_fast": config.ELEVENLABS_LLM_FAST,
            "stt_provider": "elevenlabs" if config.ELEVENLABS_API_KEY else "browser",
            "tts_provider": "elevenlabs", "conversation_provider": "elevenlabs"}


class Settings:
    def __init__(self, db: Database):
        self.db = db

    def get(self) -> dict:
        row = self.db.one("SELECT value FROM meta WHERE key='settings'")
        return {**defaults(), **(loads(row["value"], {}) if row else {})}

    def update(self, changes: dict) -> dict:
        """Меняем только известные ключи и только на допустимые значения."""
        cur = self.get()
        allowed = {"llm_provider": PROVIDERS["llm"], "stt_provider": PROVIDERS["stt"], "tts_provider": PROVIDERS["tts"],
                   "llm_model": ELEVENLABS_MODELS, "llm_model_fast": ELEVENLABS_MODELS}
        for k, v in changes.items():
            if k not in allowed:
                raise ValueError(f"unknown setting '{k}'")
            if v not in allowed[k]:
                raise ValueError(f"'{v}' is not allowed for {k}; options: {allowed[k]}")
            cur[k] = v
        stored = {k: cur[k] for k in allowed}
        self.db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('settings',?)", (dumps(stored),))
        return cur

    @staticmethod
    def options() -> dict:
        return {**PROVIDERS, "models": ELEVENLABS_MODELS}
