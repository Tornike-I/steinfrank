import os

from openai import AsyncOpenAI

_client: AsyncOpenAI | None = None


def client() -> AsyncOpenAI:
    global _client
    if _client is None:
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is not set")
        _client = AsyncOpenAI()
    return _client


def sampling(model: str, temperature: float) -> dict:
    """Reasoning models (gpt-5*, o*) reject a temperature argument."""
    return {} if model.startswith(("gpt-5", "o1", "o3", "o4")) else {"temperature": temperature}


def strictify(schema: dict) -> dict:
    """OpenAI strict structured output needs every property required and no extra keys."""
    s = dict(schema)
    if s.get("type") == "object":
        props = {k: strictify(v) for k, v in (s.get("properties") or {}).items()}
        s["properties"] = props
        s["required"] = list(props)
        s["additionalProperties"] = False
    if s.get("type") == "array" and isinstance(s.get("items"), dict):
        s["items"] = strictify(s["items"])
    return s
