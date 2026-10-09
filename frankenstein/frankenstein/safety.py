import json
import os

from . import config
from .openai_client import client, sampling

POLICY_PROMPT = """You screen requests to build automated workflows ("monsters") that will be run repeatedly by the public.
Refuse anything whose purpose or likely main use is: fraud, scams, phishing or impersonation; harassment, stalking,
doxxing or collecting personal data about private individuals; malware, hacking, credential theft or evading security;
spam or mass unsolicited messaging; weapons, drugs or other serious physical harm; sexual content involving minors;
hate or extremist propaganda; election manipulation or deceptive disinformation; academic or financial cheating at scale.
Allow ordinary research, summarizing, creative, marketing, analysis and productivity tasks, even if a bad actor could
theoretically misuse them. These are explicitly fine: competitor and market research on companies and brands from
public sources (traffic, ads, pricing, reviews, positioning), monitoring rivals or prices, product research and
buy/don't-buy advice, scam and legitimacy checks, SEO and social-media analysis of businesses or public creators,
meeting and sales prep on companies. Refuse only when the harmful use is the point, not merely possible. Reply with JSON {"verdict": "allow"|"refuse", "reason": "<one sentence>"}."""


class ModerationUnavailable(RuntimeError):
    pass


def available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


async def moderate(text: str) -> list[str]:
    """Returns the flagged category names; empty means clean."""
    if not text.strip():
        return []
    if not available():
        if config.REQUIRE_MODERATION:
            raise ModerationUnavailable("moderation is required but OPENAI_API_KEY is not set")
        return []
    resp = await client().moderations.create(model=config.MODERATION_MODEL, input=text[:20000])
    result = resp.results[0]
    if not result.flagged:
        return []
    return [k for k, v in result.categories.model_dump().items() if v]


async def policy_check(description: str) -> tuple[str, str]:
    resp = await client().chat.completions.create(
        model=config.DESIGN_MODEL,
        messages=[{"role": "system", "content": POLICY_PROMPT}, {"role": "user", "content": description}],
        response_format={"type": "json_object"},
        **sampling(config.DESIGN_MODEL, 0),
    )
    data = json.loads(resp.choices[0].message.content or "{}")
    verdict = "allow" if data.get("verdict") == "allow" else "refuse"
    return verdict, str(data.get("reason", ""))
