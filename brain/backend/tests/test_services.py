"""Внешние сервисы (Sokosumi, ElevenLabs) проверяем на ПОДДЕЛЬНОМ HTTP-транспорте: настоящий клиентский код,
но вместо сети — заготовленные ответы. С живыми API это НЕ проверка (см. tools/sokosumi_probe.py)."""
import json

import httpx
import pytest

from app import config
from app.executor import check_output, return_keys
from app.llm import LLM, LLMError
from app.sokosumi import SokosumiClient, SokosumiError, _extract_text, _fill_inputs
from app.tts import Voice, VoiceError, shorten, words_from_alignment
from app.usage import Ledger
from helpers import make_parts


def _soko_transport(job_states, captured):
    schema = {"data": {"input_data": [
        {"id": "prompt", "type": "string", "name": "Prompt"},
        {"id": "mode", "type": "option", "name": "Mode", "data": {"values": ["fast", "deep"]}},
        {"id": "notes", "type": "string", "name": "Notes", "validations": [{"validation": "optional"}]}]}}
    states = list(job_states)

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer sk-test"
        if req.url.path.endswith("/input-schema"):
            return httpx.Response(200, json=schema)
        if req.method == "POST" and req.url.path.endswith("/jobs"):
            captured["body"] = json.loads(req.content)
            return httpx.Response(200, json={"data": {"id": "job1"}})
        if req.url.path == "/v1/jobs/job1":
            return httpx.Response(200, json={"data": states.pop(0) if len(states) > 1 else states[0]})
        if req.url.path == "/v1/agents/agent1":
            return httpx.Response(200, json={"data": {"id": "agent1", "credits": 3}})
        return httpx.Response(404, json={})
    return httpx.MockTransport(handler)


@pytest.fixture()
def no_sleep(monkeypatch):
    monkeypatch.setattr("app.sokosumi.time.sleep", lambda s: None)


def test_sokosumi_job_flow(no_sleep):
    cap = {}
    c = SokosumiClient("sk-test", "https://x.test/v1", transport=_soko_transport(
        [{"id": "job1", "status": "running"}, {"id": "job1", "status": "completed", "result": "hello"}], cap))
    text, job = c.run("agent1", "my prompt")
    assert text == "hello"
    data = cap["body"]["inputData"]
    assert data["prompt"] == "my prompt" and data["mode"] == "fast" and "notes" not in data   # промпт в нужное поле, дефолты, optional пропущен


def test_sokosumi_failed_job_raises(no_sleep):
    c = SokosumiClient("sk-test", "https://x.test/v1", transport=_soko_transport([{"id": "job1", "status": "failed"}], {}))
    with pytest.raises(SokosumiError):
        c.run("agent1", "x")


def test_agent_without_text_field_is_rejected():
    with pytest.raises(SokosumiError):
        _fill_inputs([{"id": "file", "type": "file"}], "x")


def test_result_text_extraction_is_defensive():
    assert _extract_text({"id": "1", "status": "completed", "output": {"text": "nested answer"}}) == "nested answer"
    assert _extract_text({"id": "1", "status": "completed", "weird": "x" * 30}) == "x" * 30
    assert _extract_text({"id": "1", "status": "completed"}) == ""


def test_llm_over_sokosumi_records_credits_and_survives_prose_around_json(no_sleep, monkeypatch):
    monkeypatch.setattr(config, "SOKOSUMI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "SOKOSUMI_AGENT_ID", "agent1")
    db, bus, reg, _, _ = make_parts([])
    llm = LLM(ledger=Ledger(db))
    llm.provider = "sokosumi"
    events = []
    llm.on_event = lambda e, **d: events.append((e, d))
    llm._soko = SokosumiClient("sk-test", "https://x.test/v1", transport=_soko_transport(
        [{"id": "job1", "status": "completed", "result": 'Sure! Here you go:\n```json\n{"a": 1}\n```\nHope it helps.', "credits": 4}], {}))
    assert llm.json("sys", "user", purpose="planning") == {"a": 1}
    s = llm.ledger.summary()["total"]
    assert s["llm_calls"] == 1 and s["credits"] == 4 and s["usd"] == pytest.approx(4 * config.CREDIT_USD)
    assert [e for e, _ in events] == ["LLM_CALL_STARTED", "LLM_CALL_DONE"]


def test_llm_reports_what_is_missing(monkeypatch):
    monkeypatch.setattr(config, "SOKOSUMI_API_KEY", "")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "")
    llm = LLM()
    llm.provider = "sokosumi"
    assert not llm.configured() and llm.describe()["missing"] == "SOKOSUMI_API_KEY"
    with pytest.raises(LLMError):
        llm.text("s", "u")


# ---------------------------------------------------------------- ElevenLabs
def _tts_transport(captured):
    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"], captured["key"], captured["body"] = str(req.url), req.headers["xi-api-key"], json.loads(req.content)
        text = captured["body"]["text"]
        chars = list(text)
        return httpx.Response(200, json={"audio_base64": "QUJD", "alignment": {
            "characters": chars, "character_start_times_seconds": [i * 0.1 for i in range(len(chars))],
            "character_end_times_seconds": [(i + 1) * 0.1 for i in range(len(chars))]}})
    return httpx.MockTransport(handler)


def test_voice_returns_word_timings_and_records_usage(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "xi-test")
    db, *_ = make_parts([])
    cap = {}
    v = Voice(Ledger(db), transport=_tts_transport(cap))
    out = v.speak("Hello brave world", mood="angry")
    assert [w["w"] for w in out["words"]] == ["Hello", "brave", "world"]
    assert out["words"][1]["start"] == pytest.approx(0.6) and out["words"][2]["end"] == pytest.approx(1.7)
    assert cap["key"] == "xi-test" and cap["url"].endswith("/with-timestamps")
    assert cap["body"]["voice_settings"]["style"] > 0.5          # злое настроение -> выразительнее
    assert Ledger(db).summary()["total"]["tts_chars"] == len("Hello brave world")


def test_lab_verdict_uses_the_same_plain_mp3_pipeline_as_friend(monkeypatch):
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["body"] = json.loads(req.content)
        return httpx.Response(200, content=b"ID3-friend-audio", headers={"content-type": "audio/mpeg"})

    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "xi-test")
    monkeypatch.setattr(config, "LAB_SPEECH_MODEL", "eleven_v3")
    db, *_ = make_parts([])
    out = Voice(Ledger(db), transport=httpx.MockTransport(handler)).speak_lab("The answer", voice_id="friend-voice")

    assert captured["url"].endswith("/text-to-speech/friend-voice")
    assert captured["body"] == {"text": "The answer", "model_id": "eleven_v3"}
    assert out["mime"] == "audio/mpeg" and out["audio"] == "SUQzLWZyaWVuZC1hdWRpbw=="
    assert Ledger(db).summary()["total"]["tts_chars"] == len("The answer")


def test_voice_without_key_and_text_shortening(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "")
    with pytest.raises(VoiceError):
        Voice().speak("hi")
    long = "First sentence here. " + "Second one goes on and on. " * 30
    cut = shorten(long, 60)
    assert len(cut) <= 61 and cut.endswith((".", "…"))
    assert words_from_alignment(None) == []


# ---------------------------------------------------------------- проверки результата
def test_return_keys_found_by_ast():
    code = "def run(inp):\n    if not inp:\n        raise ValueError('x')\n    return {'title': 1, 'amount': 2, **{}}\n"
    assert return_keys(code) == ["title", "amount"]


def test_check_output_catches_empty_and_missing_files():
    ok = {"summary": {"en": "done", "cs": "hotovo"}, "data": {"rows": [1]}, "files": ["out/r.pdf"]}
    assert check_output(ok, ["r.pdf"], True) == []
    assert any("no summary" in p for p in check_output({"summary": "", "data": {"a": 1}}, [], False))
    assert any("no data" in p for p in check_output({"summary": "x", "data": {}}, [], False))
    assert any("file deliverable" in p for p in check_output({"summary": "x", "data": {"a": 1}}, [], True))
    assert any("was not created" in p for p in check_output(ok, ["other.pdf"], True))
    assert any("only problems" in p for p in check_output({"summary": "x", "data": {"problems": ["bad"]}}, [], False))
