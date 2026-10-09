"""Театр лаборатории: звуки по характеру (один раз на всех) и сцена рождения (сценарий без модели, один раз на монстра).
ElevenLabs подменён транспортом httpx — проверяем, ЧТО именно отправляется, и что повтор ничего не стоит."""
import json

import httpx

from app import config, theatrics
from app.db import Database
from app.usage import Ledger


class Fake:
    def __init__(self, status=200):
        self.calls, self.status = [], status

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append((request.url.path, json.loads(request.content)))
        if self.status != 200:
            return httpx.Response(self.status, json={"detail": {"status": "missing_permissions",
                                                                "message": "The API key you used is missing the permission sound_generation"}})
        return httpx.Response(200, content=b"ID3fake-mp3")


MONSTER = {"id": 7, "name": "Professor Volt", "emotion": "angry", "seed": 12345, "voice_id": "voice_monster"}


def make(tmp_path, monkeypatch, fake):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setattr(theatrics, "ENABLED", True)
    ledger = Ledger(Database(tmp_path / "t.db"))
    return theatrics.Theatrics(ledger, transport=httpx.MockTransport(fake), root=tmp_path / "th"), ledger


def test_sounds_once_per_character_and_birth_once_per_monster(tmp_path, monkeypatch):
    fake = Fake()
    th, ledger = make(tmp_path, monkeypatch, fake)
    th.prepare(MONSTER, task="Prepare me to SENIOR VOIP interview", lang="en", birth=True)
    paths = [p for p, _ in fake.calls]
    assert paths.count("/v1/sound-generation") == 3 and paths.count("/v1/text-to-dialogue") == 1
    sfx = {b["text"]: b for p, b in fake.calls if p == "/v1/sound-generation"}
    assert any(b.get("loop") for b in sfx.values()) and all("growl" in t or "hammer" in t or "slam" in t for t in sfx)
    dialogue = next(b for p, b in fake.calls if p == "/v1/text-to-dialogue")
    assert dialogue["model_id"] == "eleven_v3" and len(dialogue["inputs"]) == 3
    assert [i["voice_id"] for i in dialogue["inputs"]] == [theatrics.VOICE_FRANKENSTEIN, theatrics.VOICE_IGOR, "voice_monster"]
    assert "Professor Volt" in dialogue["inputs"][2]["text"] and "VOIP" in dialogue["inputs"][2]["text"].upper()
    assert sum(len(i["text"]) for i in dialogue["inputs"]) <= 220                     # короткая сцена — мало символов
    urls = th.urls(MONSTER)
    assert set(urls["sounds"]) == {"arrive", "working", "done"} and urls["birth"].endswith("/7/birth.mp3")
    # повтор и другой монстр того же характера — ни одного нового запроса за звуки
    th.prepare(MONSTER, task="again", birth=True)
    th.prepare({**MONSTER, "id": 8}, task="weather", birth=False)
    assert len(fake.calls) == 4
    kinds = [r["kind"] for r in ledger.db.query("SELECT kind FROM usage")]
    assert kinds.count("sfx") == 3 and kinds.count("tts") == 1


def test_czech_scene_and_script_needs_no_model():
    lines = theatrics.birth_script("Doktor Ohm", "sad", "Počasí v Praze", "cs", 3)
    assert [l["speaker"] for l in lines] == ["frankenstein", "igor", "monster"]
    assert lines[2]["text"].startswith("[sighs] Jsem Doktor Ohm") and "Počasí v Praze" in lines[2]["text"]


def test_missing_permission_is_reported_and_nothing_breaks(tmp_path, monkeypatch):
    th, _ = make(tmp_path, monkeypatch, Fake(status=401))
    events = []
    th.bus = type("Bus", (), {"emit": lambda self, t, **kw: events.append((t, kw))})()
    th.prepare(MONSTER, task="x", birth=True)
    assert events and events[0][0] == "THEATRICS_FAILED" and "sound_generation" in events[0][1]["error"]
    assert th.urls(MONSTER) == {"sounds": {}, "birth": None}


def test_files_outside_the_theatre_are_not_served(tmp_path, monkeypatch):
    th, _ = make(tmp_path, monkeypatch, Fake())
    th.prepare(MONSTER, task="x", birth=True)
    assert th.file("monsters/7/birth.mp3") is not None
    assert th.file("../t.db") is None and th.file("monsters/7/../../../t.db") is None
