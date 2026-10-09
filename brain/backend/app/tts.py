"""Голос головы: ElevenLabs text-to-speech С ТАЙМКОДАМИ.

Эндпоинт /v1/text-to-speech/{voice}/with-timestamps возвращает аудио (base64) и время начала/конца
каждого символа. Из них мы собираем слова — интерфейс подсвечивает их синхронно с голосом
(«караоке-транскрипция»). Настроение головы меняет настройки голоса (злой — резче, грустный — тише и медленнее).

ЭКОНОМИЯ: озвучивается только краткое резюме (SPEECH_MAX_CHARS), а не весь результат.
"""
import base64
import re

import httpx

from . import config
from .usage import Ledger


# stability ниже = эмоциональнее; style выше = выразительнее (параметры ElevenLabs voice_settings)
MOODS = {
    "happy":   {"stability": 0.35, "similarity_boost": 0.8, "style": 0.55, "speed": 1.05},
    "angry":   {"stability": 0.20, "similarity_boost": 0.8, "style": 0.85, "speed": 1.10},
    "sad":     {"stability": 0.75, "similarity_boost": 0.8, "style": 0.25, "speed": 0.92},
    "neutral": {"stability": 0.50, "similarity_boost": 0.75, "style": 0.0, "speed": 1.0},
}


class VoiceError(RuntimeError):
    pass


def shorten(text: str, limit: int) -> str:
    """Обрезаем до целого предложения (или слова), чтобы озвучка не обрывалась на полуслове."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    m = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return (cut[:m + 1] if m > limit * 0.4 else cut.rsplit(" ", 1)[0] + "…").strip()


def words_from_alignment(alignment: dict | None) -> list[dict]:
    """Символы с таймингами -> слова с таймингами."""
    if not alignment:
        return []
    chars, starts, ends = (alignment.get("characters") or [], alignment.get("character_start_times_seconds") or [],
                           alignment.get("character_end_times_seconds") or [])
    words, cur, t0, t1 = [], "", None, 0.0
    for ch, a, b in zip(chars, starts, ends):
        if ch.isspace():
            if cur:
                words.append({"w": cur, "start": t0, "end": t1})
            cur, t0 = "", None
            continue
        if t0 is None:
            t0 = a
        cur += ch
        t1 = b
    if cur:
        words.append({"w": cur, "start": t0, "end": t1})
    return words


class Voice:
    def __init__(self, ledger: Ledger | None = None, transport=None):
        self.ledger = ledger
        self._transport = transport

    def configured(self) -> bool:
        return bool(config.ELEVENLABS_API_KEY)

    def speak(self, text: str, mood: str = "neutral", voice_id: str | None = None) -> dict:
        if not self.configured():
            raise VoiceError("ELEVENLABS_API_KEY is not set (put it into backend/.env)")
        spoken = shorten(text, config.SPEECH_MAX_CHARS)
        if not spoken:
            raise VoiceError("nothing to say")
        body = {"text": spoken, "model_id": config.ELEVENLABS_MODEL,
                "voice_settings": {**MOODS.get(mood, MOODS["neutral"]), "use_speaker_boost": True}}
        voice = voice_id or config.ELEVENLABS_VOICE_ID
        r = self._post(voice, body)
        if r.status_code in (400, 404, 422) and voice != config.ELEVENLABS_VOICE_ID:
            r = self._post(config.ELEVENLABS_VOICE_ID, body)   # голоса монстра нет на аккаунте — говорим голосом по умолчанию
        if r.status_code >= 400:
            from .eleven import api_error
            raise VoiceError(str(api_error(r, "voice generation failed")))
        data = r.json()
        words = words_from_alignment(data.get("alignment") or data.get("normalized_alignment"))
        if self.ledger:
            self.ledger.record("tts", provider="elevenlabs", model=config.ELEVENLABS_MODEL, chars=len(spoken))
        return {"audio": data["audio_base64"], "mime": "audio/mpeg", "text": spoken, "words": words,
                "chars": len(spoken), "duration": words[-1]["end"] if words else None}

    def speak_lab(self, text: str, voice_id: str | None = None) -> dict:
        """Spoken verdict using the same endpoint/model as Friend/frankenstein.

        Friend sends only ``text`` and ``model_id`` to the regular MP3 endpoint;
        the selected archetype voice keeps its ElevenLabs defaults.  Keeping this
        separate preserves the old frontend's timed/emotional ``speak`` method.
        """
        if not self.configured():
            raise VoiceError("ELEVENLABS_API_KEY is not set (put it into backend/.env)")
        spoken = shorten(text, config.LAB_SPEECH_MAX_CHARS)
        if not spoken:
            raise VoiceError("nothing to say")
        body = {"text": spoken, "model_id": config.LAB_SPEECH_MODEL}
        voice = voice_id or config.ELEVENLABS_VOICE_ID
        r = self._post_audio(voice, body)
        if r.status_code in (400, 404, 422) and voice != config.ELEVENLABS_VOICE_ID:
            r = self._post_audio(config.ELEVENLABS_VOICE_ID, body)
        if r.status_code >= 400:
            from .eleven import api_error
            raise VoiceError(str(api_error(r, "voice generation failed")))
        if self.ledger:
            self.ledger.record("tts", provider="elevenlabs", model=config.LAB_SPEECH_MODEL, chars=len(spoken))
        return {"audio": base64.b64encode(r.content).decode("ascii"), "mime": "audio/mpeg", "text": spoken,
                "words": [], "chars": len(spoken), "duration": None}

    def _post(self, voice: str, body: dict) -> httpx.Response:
        try:
            with httpx.Client(timeout=60, transport=self._transport) as http:
                return http.post(f"{config.ELEVENLABS_API_URL}/v1/text-to-speech/{voice}/with-timestamps",
                                 headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json"}, json=body)
        except httpx.HTTPError as exc:
            raise VoiceError(f"ElevenLabs request failed: {exc}") from exc

    def _post_audio(self, voice: str, body: dict) -> httpx.Response:
        try:
            with httpx.Client(timeout=60, transport=self._transport) as http:
                return http.post(f"{config.ELEVENLABS_API_URL}/v1/text-to-speech/{voice}",
                                 headers={"xi-api-key": config.ELEVENLABS_API_KEY, "Content-Type": "application/json",
                                          "Accept": "audio/mpeg"}, json=body)
        except httpx.HTTPError as exc:
            raise VoiceError(f"ElevenLabs request failed: {exc}") from exc
