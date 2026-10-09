import hashlib
import json

from . import config, store
from .openai_client import client, sampling

# Worst-case tokens for one rewrite; the estimator adds this to every speaking monster.
REWRITE_TOKENS = 600

PROMPT = """Rewrite a result so it sounds natural when a voice assistant reads it aloud.
Keep every fact, name and number; do not add anything. Verdict first, 2-5 short plain sentences.
No URLs, file paths, markdown, code, symbols, emoji or raw error text: say a web address as a person
would ("example dot com"), round long numbers and say units in words. Reply in the language of the result.
Reply with JSON {"speech": "..."}."""


async def speakable(speech: str, report: str = "") -> tuple[str, int]:
    """The speech rewritten for listening, and the tokens it cost (0 when cached)."""
    user = json.dumps({"result": speech, "report_excerpt": report[:1500]}, ensure_ascii=False)
    key = "speech:" + hashlib.sha256(f"{config.RUNTIME_MODEL}\n{PROMPT}\n{user}".encode()).hexdigest()
    cached = store.cache_get(key)
    if cached is not None:
        return cached, 0
    resp = await client().chat.completions.create(
        model=config.RUNTIME_MODEL,
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        max_tokens=350,
        **sampling(config.RUNTIME_MODEL, 0),
    )
    text = str(json.loads(resp.choices[0].message.content or "{}").get("speech") or "").strip() or speech
    store.cache_put(key, text)
    return text, resp.usage.total_tokens if resp.usage else 0
