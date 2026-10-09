import json
from pathlib import Path

from .. import config, store, voices
from ..openai_client import client, sampling

SYSTEM = (Path(__file__).with_name("designer_prompt.md")).read_text(encoding="utf-8")


def _catalog_text(limbs: list[dict], agents: list[dict]) -> str:
    lines = ["LIMBS:"]
    for limb in limbs:
        lines.append(
            f"- {limb['name']} ({'enabled' if limb['enabled'] else 'DISABLED'}): {limb['description']}\n"
            f"  args: {json.dumps(limb['args_schema']['properties'])} required: {limb['args_schema']['required']}"
        )
    if agents:
        lines.append("\nSOKOSUMI AGENTS (id | name | price credits | what it does | input fields):")
        for a in agents:
            fields = ", ".join(
                f"{f['id']}:{f['type']}{'?' if f['optional'] else ''}"
                + (f"[{'|'.join(map(str, f['values']))}]" if f.get("values") else "")
                for f in a["fields"]
            )
            lines.append(f"- {a['id']} | {a['name']} | {a['credits']} | {a['summary']} | {fields}")
    return "\n".join(lines)


# Known-good designs only: showing whatever happens to be published lets one bad monster get copied.
EXAMPLES = ("page-narrator",)


def _examples() -> str:
    specs = [s for s in (store.load_monster(i) for i in EXAMPLES) if s]
    if not specs:
        return ""
    shown = [s.model_dump(by_alias=True, exclude={"estimate", "safety", "voice", "examples"}) for s in specs]
    return "\n\nEXAMPLE MONSTERS:\n" + "\n".join(json.dumps(s, ensure_ascii=False) for s in shown)


class Designer:
    def __init__(self, limbs: list[dict], agents: list[dict]):
        system = SYSTEM + "\n" + voices.designer_text() + "\n\n" + _catalog_text(limbs, agents) + _examples()
        self.messages = [{"role": "system", "content": system}]
        self.usage = 0

    async def _ask(self) -> dict:
        resp = await client().chat.completions.create(
            model=config.DESIGN_MODEL,
            messages=self.messages,
            response_format={"type": "json_object"},
            **sampling(config.DESIGN_MODEL, 0.2),
        )
        self.usage += resp.usage.total_tokens if resp.usage else 0
        text = resp.choices[0].message.content or "{}"
        self.messages.append({"role": "assistant", "content": text})
        return json.loads(text)

    async def design(self, description: str) -> dict:
        self.messages.append({"role": "user", "content": f"Build a monster for this task:\n{description}"})
        return await self._ask()

    async def repair(self, errors: list[str], stage: str = "validation") -> dict:
        if any("expected/got" in e for e in errors):
            errors = errors + [
                "For 'expected/got' test mismatches the code ran fine; decide which side is wrong. If the code "
                "implements the rubric you intended, copy the actual value into the test's expect instead of "
                "rewriting the code."
            ]
        self.messages.append(
            {
                "role": "user",
                "content": f"The spec failed {stage}. Fix every problem and return the full corrected spec.\n- "
                + "\n- ".join(errors),
            }
        )
        return await self._ask()
