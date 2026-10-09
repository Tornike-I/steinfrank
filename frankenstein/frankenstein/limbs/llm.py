import hashlib
import json

from .. import config, pricing, store
from ..openai_client import client, strictify
from ..refs import literal_chars, template_refs
from .base import Cost, Limb, LimbError

# Rough guess for how much text one `{{ref}}` pulls into a prompt; only used for pre-run estimates.
REF_TOKENS = 400
OVERHEAD_TOKENS = 30


def count_tokens(text: str) -> int:
    return len(text) // 4 + 1


class LlmLimb(Limb):
    name = "llm"
    description = (
        "One call to a small, fast LLM at temperature 0. Use only for work that genuinely needs language "
        "understanding (summarize, classify free text, extract from messy prose, write prose). Always give "
        "output_schema so later steps can reference fields. Identical prompts are cached and cost nothing."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "User prompt template with {{refs}}"},
            "system": {"type": "string", "description": "Optional fixed system prompt"},
            "max_tokens": {"type": "integer", "description": "Output cap, keep small (<= 1500)"},
            "output_schema": {"type": "object", "description": "JSON schema (type object) for the reply"},
        },
        "required": ["prompt", "max_tokens"],
    }
    requires = ("OPENAI_API_KEY",)
    seconds = 3.0

    def estimate(self, args: dict) -> Cost:
        chars = literal_chars(args.get("prompt", "")) + literal_chars(args.get("system", ""))
        refs = len(template_refs(args.get("prompt", ""))) + len(template_refs(args.get("system", "")))
        out = int(args.get("max_tokens", 500))
        inp = chars // 4 + refs * REF_TOKENS + OVERHEAD_TOKENS
        return Cost(llm_tokens=inp + out, usd=pricing.llm(config.RUNTIME_MODEL, inp, out), seconds=self.seconds)

    def validate_args(self, args, spec):
        errs = []
        if int(args.get("max_tokens", 0)) > config.CAPS["llm_max_tokens"]:
            errs.append(f"max_tokens above {config.CAPS['llm_max_tokens']}")
        schema = args.get("output_schema")
        if schema is not None and schema.get("type") != "object":
            errs.append("output_schema must have type object")
        return errs

    def output_fields(self, args):
        schema = args.get("output_schema")
        return set((schema.get("properties") or {}).keys()) if isinstance(schema, dict) else {"text"}

    def precheck(self, args: dict) -> Cost:
        """Cost of the rendered call, used for the budget check right before it runs."""
        text = (args.get("system") or "") + str(args.get("prompt", ""))
        return Cost(llm_tokens=count_tokens(text) + OVERHEAD_TOKENS + int(args.get("max_tokens", 500)))

    async def run(self, args, ctx):
        prompt = str(args["prompt"])
        system = args.get("system") or "You are a precise worker inside an automated workflow. Follow the instructions exactly."
        schema = args.get("output_schema")
        max_tokens = int(args.get("max_tokens", 500))
        key = "llm:" + hashlib.sha256(
            json.dumps([config.RUNTIME_MODEL, system, prompt, schema, max_tokens], sort_keys=True).encode()
        ).hexdigest()
        cached = store.cache_get(key)
        if cached is not None:
            return cached

        kwargs = {}
        if schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "step_output", "strict": True, "schema": strictify(schema)},
            }
        resp = await client().chat.completions.create(
            model=config.RUNTIME_MODEL,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=max_tokens,
            seed=7,
            **kwargs,
        )
        if resp.usage:
            used = Cost(llm_tokens=resp.usage.total_tokens,
                        usd=pricing.llm(config.RUNTIME_MODEL, resp.usage.prompt_tokens, resp.usage.completion_tokens))
        else:
            n = self.precheck(args).llm_tokens
            used = Cost(llm_tokens=n, usd=pricing.llm(config.RUNTIME_MODEL, n, 0))
        ctx.charge(used)
        text = resp.choices[0].message.content or ""
        if schema:
            try:
                out = json.loads(text)
            except json.JSONDecodeError as e:
                raise LimbError(f"llm returned invalid JSON: {e}") from e
        else:
            out = {"text": text}
        store.cache_put(key, out)
        return out
