import os

import httpx

from .. import config, store
from ..spec import MonsterSpec

API = "https://api.elevenlabs.io/v1/convai"
SHARED_TOOLS_KEY = "elevenlabs:shared_tools:v2"

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


async def _post(path: str, body: dict) -> dict:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise VoiceError("ELEVENLABS_API_KEY is not set")
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(API + path, json=body, headers={"xi-api-key": key})
    if r.status_code >= 400:
        raise VoiceError(f"ElevenLabs {path} -> {r.status_code}: {r.text[:800]}")
    return r.json()


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
Politely decline anything outside your one job. Keep every reply short; you are speaking, not writing."""


async def _shared_tool_ids() -> list[str]:
    cached = store.cache_get(SHARED_TOOLS_KEY)
    if cached:
        return cached
    ids = [(await _post("/tools", {"tool_config": t}))["id"] for t in (CHECK_RUN, ANSWER_INPUT, CONFIRM_SPEND)]
    store.cache_put(SHARED_TOOLS_KEY, ids)
    return ids


async def create_agent(spec: MonsterSpec) -> str:
    start_id = (await _post("/tools", {"tool_config": start_run_tool(spec)}))["id"]
    tool_ids = [start_id, *await _shared_tool_ids()]
    body = {
        "name": f"Monster: {spec.name}",
        "tags": ["steinfrank", f"monster:{spec.id}"],
        "conversation_config": {
            "agent": {
                "first_message": spec.voice.first_message or f"Grr. I am {spec.name}. {spec.purpose}. What do you need?",
                "language": "en",
                "prompt": {"prompt": agent_prompt(spec), "llm": config.VOICE_LLM, "tool_ids": tool_ids, "temperature": 0},
            },
            "tts": {"voice_id": config.VOICE_ID},
        },
        "platform_settings": {"auth": {"enable_auth": True}},
    }
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
