"""Цены в долларах (идея pricing.py из проекта коллеги): переводим ИЗМЕРЕННЫЕ токены и символы в ≈ $.

ВАЖНО: это ОЦЕНКА по списочным ценам провайдеров моделей. Мозг работает через ElevenLabs Agents, а ElevenLabs списывает
свои кредиты (точная цена разговора дозапрашивается отдельно и хранится в usage.credits). Доллары здесь — для сравнения
путей между собой («сборка органа ≈ $0.12, ответ навыком ≈ $0»), а не счёт. Обновляйте таблицу, когда меняются цены.
"""

# $ за 1 млн токенов: (вход, выход). Приблизительно, по публичным прайсам провайдеров.
LLM = {
    "claude-sonnet-4-5": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-3-7-sonnet": (3.00, 15.00),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-2.0-flash-lite": (0.075, 0.30),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}
DEFAULT_LLM = (3.00, 15.00)          # неизвестная модель — считаем по «дорогой», чтобы не занижать
# $ за 1000 символов озвучки (по таблице коллеги)
TTS = {"eleven_flash_v2_5": 0.05, "eleven_turbo_v2_5": 0.05, "eleven_v3": 0.10, "eleven_multilingual_v2": 0.10}


# Sokosumi не публикует цену кредита; оценка по тарифу €25 / 1500 кредитов (как в проекте коллеги)
SOKOSUMI_CREDIT = 0.018


def llm(model: str | None, input_tokens: int, output_tokens: int) -> float:
    i, o = LLM.get((model or "").lower(), DEFAULT_LLM)
    return (input_tokens * i + output_tokens * o) / 1_000_000


def tts(model: str | None, chars: int) -> float:
    return chars * TTS.get(model or "", 0.10) / 1000


def usage_row(row: dict) -> float:
    """Стоимость одной записи учёта (таблица usage)."""
    if row.get("kind") == "llm":
        return llm(row.get("model"), int(row.get("prompt_tokens") or 0), int(row.get("completion_tokens") or 0))
    if row.get("kind") == "tts":
        return tts(row.get("model"), int(row.get("chars") or 0))
    if row.get("kind") == "sokosumi":
        return float(row.get("credits") or 0) * SOKOSUMI_CREDIT
    return 0.0
