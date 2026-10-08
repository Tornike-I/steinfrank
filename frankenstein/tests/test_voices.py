import asyncio

from frankenstein import voices
from frankenstein.forge import voice
from frankenstein.forge.validator import validate
from frankenstein.spec import MonsterSpec


def make(**voice_fields) -> MonsterSpec:
    return MonsterSpec.model_validate({
        "id": "test-monster", "name": "Test Monster", "purpose": "test things",
        "steps": [{"id": "say", "limb": "forged:say", "args": {}}],
        "output": {"speech": "{{steps.say.speech}}", "report": "{{steps.say.report}}"},
        "forged_limbs": [{"name": "say", "code": "def run():\n    return {'speech': 'hi', 'report': 'hi'}\n",
                          "tests": [{"args": {}, "expect": {"speech": "hi"}}]}],
        "voice": voice_fields,
    })


def test_pick_voice_is_stable_and_from_archetype():
    a = voices.pick_voice("hag", "scam-sniffer")
    assert a == voices.pick_voice("hag", "scam-sniffer")
    assert a in voices.archetype("hag")["voices"]


def test_agent_body_uses_archetype_voice_and_tags():
    spec = make(archetype="gremlin")
    tts = voice.agent_body(spec, ["t1"])["conversation_config"]["tts"]
    assert tts["voice_id"] in voices.archetype("gremlin")["voices"]
    assert {t["tag"] for t in tts["suggested_audio_tags"]} == {t["tag"] for t in voices.archetype("gremlin")["tags"]}
    assert "supported_voices" not in tts


def test_czech_monster_with_cast():
    spec = make(archetype="swarm", language="cs", cast=[{"label": "queen", "archetype": "lich", "role": "the hive queen"}])
    body = voice.agent_body(spec, [])
    assert body["conversation_config"]["agent"]["language"] == "cs"
    extra = body["conversation_config"]["tts"]["supported_voices"]
    assert extra[0]["label"] == "queen" and extra[0]["language"] == "cs"
    prompt = body["conversation_config"]["agent"]["prompt"]["prompt"]
    assert "Czech" in prompt and "<queen>" in prompt


def test_validator_rejects_unknown_archetype():
    errs = asyncio.run(validate(make(archetype="dragon")))
    assert any("voice.archetype" in e for e in errs)
