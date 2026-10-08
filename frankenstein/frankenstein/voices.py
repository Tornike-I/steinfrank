import hashlib
import json
from functools import cache
from pathlib import Path


@cache
def table() -> dict:
    return json.loads(Path(__file__).with_name("voices.json").read_text(encoding="utf-8"))


def archetypes() -> dict[str, dict]:
    return table()["archetypes"]


def cast_voice(name: str) -> str:
    return table()["cast"][name]


def archetype(name: str) -> dict:
    return archetypes().get(name) or archetypes()["brute"]


def pick_voice(archetype_name: str, seed: str) -> str:
    """Stable per monster, so re-publishing keeps the voice users already know."""
    voices = archetype(archetype_name)["voices"]
    return voices[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(voices)]


def designer_text() -> str:
    lines = ["VOICE ARCHETYPES (voice.archetype; cast[].archetype):"]
    for name, a in archetypes().items():
        lines.append(f"- {name}: {a['sound']}. Fits: {a['fits']}.")
    return "\n".join(lines)
