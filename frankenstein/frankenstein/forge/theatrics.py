import json
import logging
import os
import random

import httpx

from .. import config, voices
from ..openai_client import client, sampling
from ..spec import MonsterSpec, Sounds

log = logging.getLogger("frankenstein.theatrics")
ELEVEN = "https://api.elevenlabs.io/v1"
FORMAT = {"output_format": "mp3_44100_128"}
SOUND_SECONDS = {"arrive": 2.5, "working": 8.0, "done": 1.5}
BIRTH_MAX_CHARS = 170

# A random twist per birth keeps scenes from repeating when many monsters are forged.
TWISTS = [
    "the lever is stuck and Igor has to kick it",
    "the storm is too weak and Frankenstein begs the sky for more lightning",
    "Igor attached the wrong limb first and has to swap it",
    "the power flickers out right before the big moment",
    "Igor is terrified and hides behind the table",
    "the neighbours bang on the door to complain about the noise",
    "Frankenstein gives an over-the-top speech and Igor keeps interrupting",
    "the creature wakes up early, before Frankenstein is ready",
    "Igor spills a beaker and something starts smoking",
    "Frankenstein is exhausted after forging all night and nearly falls asleep",
    "the creature's first word comes out wrong and it has to try again",
    "Igor has secretly named the creature something silly",
]

BIRTH_PROMPT = """Write a very short comic radio scene: Doctor Frankenstein and his assistant Igor bring a new monster
to life in the lab, and the monster speaks its first lines. Language: {language}.
Monster: {name}. Its one job: {purpose}. Its personality: {persona}. Its voice: {sound}.
Twist for this scene: {twist}.
Rules: exactly 3 short lines, under 150 characters in total. The monster speaks last and says, in character, what it does.
You may use a few audio tags in square brackets such as [laughs], [shouts], [whispers], [gasps]. No stage directions
outside tags, no narrator. Reply with JSON {{"lines": [{{"speaker": "frankenstein|igor|monster", "text": "..."}}]}}."""


async def _eleven(path: str, body: dict) -> bytes:
    async with httpx.AsyncClient(timeout=180) as c:
        r = await c.post(ELEVEN + path, params=FORMAT, json=body, headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]})
    if r.status_code >= 400:
        raise RuntimeError(f"ElevenLabs {path} -> {r.status_code}: {r.text[:500]}")
    return r.content


def _sfx(text: str, seconds: float, loop: bool = False):
    body = {"text": text, "duration_seconds": seconds, "model_id": "eleven_text_to_sound_v2"}
    return _eleven("/sound-generation", body | ({"loop": True} if loop else {}))


def _save(spec: MonsterSpec, name: str, audio: bytes) -> str:
    d = config.ARTIFACTS_DIR / "monsters" / spec.id / f"v{spec.version}"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.mp3").write_bytes(audio)
    # Relative, so the URL survives a change of PUBLIC_BASE_URL.
    return f"/artifacts/monsters/{spec.id}/v{spec.version}/{name}.mp3"


async def make_sounds(spec: MonsterSpec) -> Sounds:
    prompts = voices.archetype(spec.voice.archetype)["sounds"]
    sounds = Sounds()
    for name, seconds in SOUND_SECONDS.items():
        try:
            audio = await _sfx(prompts[name], seconds, loop=name == "working")
            setattr(sounds, name, _save(spec, name, audio))
        except Exception as e:
            log.warning("sound %s for %s failed: %s", name, spec.id, e)
    return sounds


async def _birth_script(spec: MonsterSpec) -> list[dict]:
    prompt = BIRTH_PROMPT.format(
        language="Czech" if spec.voice.language == "cs" else "English", name=spec.name, purpose=spec.purpose,
        persona=spec.persona or "gruff but helpful", sound=voices.archetype(spec.voice.archetype)["sound"],
        twist=random.choice(TWISTS),
    )
    resp = await client().chat.completions.create(
        model=config.DESIGN_MODEL, messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"}, **sampling(config.DESIGN_MODEL, 1.0),
    )
    lines = json.loads(resp.choices[0].message.content or "{}").get("lines") or []
    return [l for l in lines if l.get("speaker") in ("frankenstein", "igor", "monster") and str(l.get("text", "")).strip()]


async def make_birth(spec: MonsterSpec) -> str | None:
    lines = await _birth_script(spec)
    if not lines:
        return None
    monster = spec.voice.voice_id or voices.pick_voice(spec.voice.archetype, spec.id)
    igor = next(v for v in [voices.cast_voice("igor"), *voices.archetype("igor")["voices"]] if v != monster)
    speakers = {"frankenstein": voices.cast_voice("frankenstein"), "igor": igor, "monster": monster}
    inputs, used = [], 0
    for l in lines:
        text = l["text"][:BIRTH_MAX_CHARS - used]
        if not text:
            break
        inputs.append({"text": text, "voice_id": speakers[l["speaker"]]})
        used += len(text)
    body = {"inputs": inputs, "model_id": "eleven_v3"}
    if spec.voice.language != "en":
        body["language_code"] = spec.voice.language
    return _save(spec, "birth", await _eleven("/text-to-dialogue", body))
