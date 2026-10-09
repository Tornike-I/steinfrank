"""Dollar prices per model/service, used to turn usage into USD. Checked October 2026; update when providers change them."""

# Per 1M tokens: (input, output).
LLM = {
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-5.4-mini": (0.75, 4.50),
}
# Per 1,000 characters.
TTS = {
    "eleven_flash_v2_5": 0.05,
    "eleven_turbo_v2_5": 0.05,
    "eleven_v3": 0.10,
    "eleven_multilingual_v2": 0.10,
}
# Per image at 1024x1024, by quality.
IMAGE = {"gpt-image-1": {"low": 0.011, "medium": 0.042, "high": 0.167}}
WEB_SEARCH = 0.008  # Tavily basic search, pay-as-you-go
# Sokosumi publishes no per-credit price; derived from the €25 / 1,500-credit plan.
SOKOSUMI_CREDIT = 0.018
# ElevenLabs agent conversation, per minute (the published starting rate).
VOICE_CALL_PER_MIN = 0.08


def llm(model: str, input_tokens: int, output_tokens: int) -> float:
    i, o = LLM.get(model, LLM["gpt-4.1-mini"])
    return (input_tokens * i + output_tokens * o) / 1_000_000


def tts(model: str, chars: int) -> float:
    return chars * TTS.get(model, 0.10) / 1000


def image(model: str, quality: str) -> float:
    return IMAGE.get(model, IMAGE["gpt-image-1"]).get(quality, 0.042)


def credits(n: float) -> float:
    return n * SOKOSUMI_CREDIT
