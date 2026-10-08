import logging
import os

import httpx

from .. import config, store, voices
from ..spec import MonsterSpec

API = "https://api.elevenlabs.io/v1/convai"
SHARED_TOOLS_KEY = "elevenlabs:shared_tools:v2"
log = logging.getLogger("frankenstein.voice")

CHECK_RUN = {
    "type": "client",
    "name": "check_run",
    "description": "Check a running task. Returns its status and, when completed, the result to tell the user.",
    "parameters": {
        "type": "object",
        "required": ["run_id"],
        "properties": {"run_id": {"type": "string", "description": "The run_id returned by start_run"}},
    },
    "expects_response": True,
    "response_timeout_secs": 30,
}
ANSWER_INPUT = {
    "type": "client",
    "name": "answer_input",
    "description": "Send the user's answer when check_run reports status needs_input.",
    "parameters": {
        "type": "object",
        "required": ["run_id", "answer"],
        "properties": {
            "run_id": {"type": "string", "description": "The run_id"},
            "answer": {"type": "string", "description": "The user's answer to the question"},
        },
    },
    "expects_response": True,
    "response_timeout_secs": 30,
}


CONFIRM_SPEND = {
    "type": "client",
    "name": "confirm_spend",
    "description": "Send the user's yes/no when check_run reports status needs_confirmation (a paid hire).",
    "parameters": {
        "type": "object",
        "required": ["run_id", "approve"],
        "properties": {
            "run_id": {"type": "string", "description": "The run_id"},
            "approve": {"type": "boolean", "description": "true only if the user clearly said yes"},
        },
    },
    "expects_response": True,
    "response_timeout_secs": 30,
}


class VoiceError(RuntimeError):
    pass


async def _call(method: str, path: str, body: dict | None = None) -> dict:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.request(method, API + path, json=body, headers={"xi-api-key": key})
    if r.status_code >= 400:
        raise VoiceError(f"ElevenLabs {method} {path} -> {r.status_code}: {r.text[:800]}")
    return r.json() if r.content else {}


async def _post(path: str, body: dict) -> dict:
    return await _call("POST", path, body)


def _param(prop: dict) -> dict:
    t = prop.get("type", "string")
    desc = prop.get("description") or ""
    if prop.get("enum"):
        desc += f" One of: {', '.join(map(str, prop['enum']))}."
    out = {"type": t if t in ("string", "number", "integer", "boolean", "array") else "string", "description": desc}
    if out["type"] == "array":
        out["items"] = {"type": (prop.get("items") or {}).get("type", "string"), "description": "item"}
    return out


def start_run_tool(spec: MonsterSpec) -> dict:
    props = spec.inputs.get("properties") or {}
    return {
        "type": "client",
        "name": "start_run",
        "description": f"Start the task: {spec.purpose}. Call once you have all required inputs from the user.",
        "parameters": {
            "type": "object",
            "required": list(spec.inputs.get("required") or []),
            "properties": {k: _param(v) for k, v in props.items()},
        },
        "expects_response": True,
        "response_timeout_secs": 30,
    }


def agent_prompt(spec: MonsterSpec) -> str:
    props = spec.inputs.get("properties") or {}
    inputs = "\n".join(f"- {k}: {v.get('description', '')}" for k, v in props.items()) or "- (none)"
    return f"""You are {spec.name}, a monster built by Frankenstein. Your one job: {spec.purpose}
Persona: {spec.persona or 'brief, friendly, a little monstrous'}.

You do not do the task yourself. Collect these inputs from the user, then call start_run:
{inputs}

Then call check_run with the run_id:
- queued/running/waiting: say you are working on it. Paid agents can take minutes; offer that the user can hang up
  and you will notify them, otherwise check again when they ask or after a short pause.
- needs_confirmation: read the question and the credit cost to the user, then call confirm_spend with their answer.
- needs_input: ask the user the question and send their reply with answer_input.
- completed: say the "speech" text from the result, close to word for word. Mention that the full report is on screen.
- failed or blocked: say so plainly in one sentence.
Politely decline anything outside your one job. Keep every reply short; you are speaking, not writing.
{_voice_rules(spec)}"""


def _voice_rules(spec: MonsterSpec) -> str:
    v = spec.voice
    tags = ", ".join(f"[{t['tag']}]" for t in voices.archetype(v.archetype)["tags"])
    rules = [
        f"Sound effects: you may put at most ONE of {tags} at the very start or end of a reply, and most replies should "
        "have none. Never put one inside the result you read out; the result must stay clear and easy to follow.",
        "Read the mood: if the user sounds stressed or upset, be gentler and calmer; if they interrupt you, a short "
        "grumble is fine, then help.",
    ]
    if v.language == "cs":
        rules.append("Always speak Czech. Results and questions from the tools arrive in English: say them in natural "
                     "Czech. Tool arguments stay as the user gave them.")
    if v.cast:
        others = "\n".join(f"  <{c.label}>…</{c.label}>: {c.role}" for c in v.cast)
        rules.append("You share this body with other voices. Wrap their lines in their tag; use them rarely, one short "
                     f"line at a time, never for the result:\n{others}")
    return "\n".join(f"- {r}" for r in rules)


async def _shared_tool_ids() -> list[str]:
    cached = store.cache_get(SHARED_TOOLS_KEY)
    if cached:
        return cached
    ids = [(await _post("/tools", {"tool_config": t}))["id"] for t in (CHECK_RUN, ANSWER_INPUT, CONFIRM_SPEND)]
    store.cache_put(SHARED_TOOLS_KEY, ids)
    return ids


def agent_body(spec: MonsterSpec, tool_ids: list[str]) -> dict:
    v = spec.voice
    arch = voices.archetype(v.archetype)
    tts = {
        "model_id": "eleven_v4_turbo",
        "voice_id": v.voice_id or voices.pick_voice(v.archetype, spec.id),
        "expressive_mode": True,
        "stability": arch["stability"],
        "speed": arch["speed"],
        "suggested_audio_tags": arch["tags"],
    }
    if v.cast:
        tts["supported_voices"] = [
            {"label": c.label, "voice_id": voices.pick_voice(c.archetype, f"{spec.id}:{c.label}"), "description": c.role,
             **({"language": v.language} if v.language != "en" else {})}
            for c in v.cast
        ]
    return {
        "name": f"Monster: {spec.name}",
        "tags": ["steinfrank", f"monster:{spec.id}"],
        "conversation_config": {
            "agent": {
                "first_message": v.first_message or f"Grr. I am {spec.name}. {spec.purpose}. What do you need?",
                "language": v.language,
                "prompt": {"prompt": agent_prompt(spec), "llm": config.VOICE_LLM, "tool_ids": tool_ids, "temperature": 0},
            },
            "tts": tts,
        },
        "platform_settings": {"auth": {"enable_auth": True}},
    }


async def create_agent(spec: MonsterSpec, previous_agent_id: str | None = None) -> str:
    """Updates the previous agent in place when there is one, so re-publishing doesn't pile up agents."""
    start_id = (await _post("/tools", {"tool_config": start_run_tool(spec)}))["id"]
    shared = await _shared_tool_ids()
    body = agent_body(spec, [start_id, *shared])
    if previous_agent_id:
        try:
            old = await _call("GET", f"/agents/{previous_agent_id}")
            await _call("PATCH", f"/agents/{previous_agent_id}", body)
        except VoiceError as e:
            log.warning("could not update agent %s, creating a new one: %s", previous_agent_id, e)
        else:
            stale = set(old["conversation_config"]["agent"]["prompt"].get("tool_ids") or []) - {start_id, *shared}
            for tool_id in stale:
                try:
                    await _call("DELETE", f"/tools/{tool_id}")
                except VoiceError as e:
                    log.warning("could not delete stale tool %s: %s", tool_id, e)
            return previous_agent_id
    return (await _post("/agents/create", body))["agent_id"]


async def signed_url(agent_id: str) -> str:
    """Agents are private; the browser gets a short-lived URL from us instead of the bare agent id."""
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(f"{API}/conversation/get-signed-url", params={"agent_id": agent_id}, headers={"xi-api-key": key})
    if r.status_code >= 400:
        raise VoiceError(f"ElevenLabs signed url -> {r.status_code}: {r.text[:500]}")
    return r.json()["signed_url"]
