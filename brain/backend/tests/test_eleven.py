"""ElevenLabs как «мозг», распознавание речи и баланс — на НАСТОЯЩЕМ локальном WebSocket-сервере и поддельном HTTP.
Протокол взят из документации ElevenLabs Agents Platform. С живым API это не проверка."""
import json
import threading

import httpx
import pytest
from websockets.sync.server import serve

from app import config, eleven
from app.eleven import ElevenBrain, ElevenError
from app.llm import LLM
from app.usage import Ledger
from helpers import make_parts


class FakeAgentServer:
    """Ведёт себя как wss://api.elevenlabs.io/v1/convai/conversation: метаданные, ping, ответ кусками."""

    def __init__(self, answer_parts, finish="complete"):
        self.answer_parts, self.finish = answer_parts, finish
        self.received: list[dict] = []
        self.server = serve(self.handler, "127.0.0.1", 0)
        self.port = self.server.socket.getsockname()[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def handler(self, ws):
        init = json.loads(ws.recv())
        self.received.append(init)
        ws.send(json.dumps({"type": "ping", "ping_event": {"event_id": 7, "ping_ms": 5}}))
        ws.send(json.dumps({"type": "conversation_initiation_metadata",
                            "conversation_initiation_metadata_event": {"conversation_id": "conv_42"}}))
        while True:
            msg = json.loads(ws.recv())
            self.received.append(msg)
            if msg.get("type") == "user_message":
                break
        for i, part in enumerate(self.answer_parts):
            ws.send(json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": part, "event_id": 10 + i}}))
        if self.finish == "complete":
            ws.send(json.dumps({"type": "agent_response_complete"}))
            ws.recv()            # ждём закрытия клиентом

    def close(self):
        self.server.shutdown()


def http_transport(calls, port):
    def handler(req: httpx.Request):
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.headers.get("xi-api-key") != "xi-test":
            return httpx.Response(401, json={"detail": "bad key"})
        if req.url.path == "/v1/convai/agents/create":
            return httpx.Response(200, json={"agent_id": "agent_brain"})
        if req.url.path == "/v1/convai/conversation/get-signed-url":
            assert req.url.params["agent_id"] == "agent_brain"
            return httpx.Response(200, json={"signed_url": f"ws://127.0.0.1:{port}/v1/convai/conversation?sig=1"})
        if req.url.path == "/v1/convai/conversations/conv_42":
            return httpx.Response(200, json={"metadata": {"cost": 123}})
        return httpx.Response(404)
    return httpx.MockTransport(handler)


@pytest.fixture()
def key(monkeypatch):
    monkeypatch.setattr(config, "ELEVENLABS_API_KEY", "xi-test")
    monkeypatch.setattr(config, "ELEVENLABS_BRAIN_AGENT_ID", "")


def test_brain_creates_agent_once_and_overrides_prompt_and_model(key):
    srv = FakeAgentServer(['{"a": ', '1}'])
    calls, saved = [], {}
    brain = ElevenBrain(lambda: saved.get("id"), lambda v: saved.update(id=v), transport=http_transport(calls, srv.port))
    text, meta = brain.complete("SYSTEM RULES", "USER TASK", "claude-sonnet-4-5")
    srv.close()
    assert text == '{"a": \n1}' or text.replace("\n", "") == '{"a": 1}'
    assert meta["conversation_id"] == "conv_42" and saved["id"] == "agent_brain"
    create = next(c for c in calls if c[1] == "/v1/convai/agents/create")[2]
    assert create["conversation_config"]["conversation"]["text_only"] is True
    assert create["platform_settings"]["overrides"]["conversation_config_override"]["agent"]["prompt"] == {"prompt": True, "llm": True}
    init = srv.received[0]
    assert init["type"] == "conversation_initiation_client_data"
    assert init["conversation_config_override"]["agent"]["prompt"] == {"prompt": "SYSTEM RULES", "llm": "claude-sonnet-4-5"}
    assert {"type": "pong", "event_id": 7} in srv.received                               # отвечаем на ping
    assert {"type": "user_message", "text": "USER TASK"} in srv.received
    # второй вызов — агент уже есть, повторно не создаётся
    srv2 = FakeAgentServer(["hello"])
    brain._transport = http_transport(calls, srv2.port)
    brain.complete("s", "u", "gemini-2.5-flash")
    srv2.close()
    assert sum(1 for c in calls if c[1] == "/v1/convai/agents/create") == 1


def test_answer_without_complete_event_ends_after_silence(key):
    srv = FakeAgentServer(["just text"], finish="silence")
    brain = ElevenBrain(lambda: "agent_brain", lambda v: None, transport=http_transport([], srv.port))
    text, _ = brain.complete("s", "u", "gpt-4.1")
    srv.close()
    assert text == "just text"


def test_llm_routes_models_by_purpose_and_records_usage(key, monkeypatch):
    srv = FakeAgentServer(['Sure:\n```json\n{"ok": true}\n```'])
    db, bus, reg, _, _ = make_parts([])
    llm = LLM(ledger=Ledger(db))
    llm.provider = "elevenlabs"
    llm.eleven = ElevenBrain(lambda: "agent_brain", lambda v: None, transport=http_transport([], srv.port))
    assert llm.model_for("building") == config.ELEVENLABS_LLM and llm.model_for("diagnosing") == config.ELEVENLABS_LLM_FAST
    llm._fetch_cost = lambda ref: None          # фоновая дозагрузка стоимости проверяется отдельным тестом
    assert llm.json("sys", "user", purpose="planning") == {"ok": True}
    srv.close()
    row = db.one("SELECT provider, model, ref FROM usage")
    assert row == {"provider": "elevenlabs", "model": config.ELEVENLABS_LLM, "ref": "conv_42"}
    assert srv.received[0]["conversation_config_override"]["agent"]["prompt"]["llm"] == config.ELEVENLABS_LLM


def test_cost_lookup_is_defensive(key):
    brain = ElevenBrain(lambda: "a", lambda v: None, transport=http_transport([], 1))
    assert brain.conversation_cost("conv_42") == 123.0
    assert brain.conversation_cost("missing") is None


def test_agent_creation_failure_is_reported(key):
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "invalid key"}))
    with pytest.raises(ElevenError):
        ElevenBrain(lambda: None, lambda v: None, transport=t).agent_id()


def test_speech_to_text_scribe(key):
    seen = {}

    def handler(req: httpx.Request):
        seen["path"], seen["body"] = req.url.path, req.content
        return httpx.Response(200, json={"text": " Analyze this PDF ", "language_code": "en", "words": [
            {"text": "Analyze", "type": "word", "start": 0.1, "end": 0.5}, {"text": " ", "type": "spacing"},
            {"text": "PDF", "type": "word", "start": 0.9, "end": 1.4}]})
    out = eleven.transcribe(b"RIFF....fakeaudio", "speech.webm", "audio/webm", "en", transport=httpx.MockTransport(handler))
    assert out == {"text": "Analyze this PDF", "language": "en", "seconds": 1.4}
    assert seen["path"] == "/v1/speech-to-text" and b"scribe_v2" in seen["body"] and b'name="language_code"' in seen["body"]


def test_subscription_balance(key):
    t = httpx.MockTransport(lambda r: httpx.Response(200, json={"character_count": 1200, "character_limit": 100000, "tier": "creator"}))
    assert eleven.subscription(transport=t) == {"used": 1200, "limit": 100000, "tier": "creator", "reset_unix": None}


def test_missing_permission_error_explains_the_fix(key):
    body = {"detail": {"type": "authentication_error", "code": "unauthorized", "status": "missing_permissions",
                       "message": "The API key you used is missing the permission convai_write to execute this operation."}}
    t = httpx.MockTransport(lambda r: httpx.Response(401, json=body))
    with pytest.raises(ElevenError) as e:
        ElevenBrain(lambda: None, lambda v: None, transport=t).agent_id()
    msg = str(e.value)
    assert "convai_write" in msg and "ELEVENLABS_BRAIN_AGENT_ID" in msg and "API Keys" in msg


def test_star_and_hash_survive_the_elevenlabs_channel(key):
    """ElevenLabs вырезает * и ## из ответа агента; модель пишет токены, мы декодируем — код снова компилируется."""
    from app.llm import STAR_TOKEN, HASH_TOKEN
    reply = (f"=====FILE: workflow.py=====\n{HASH_TOKEN} header {HASH_TOKEN}{HASH_TOKEN}\n"
             f"def main(task):\n    line = '=' {STAR_TOKEN} 60\n    return {{'summary': line, 'n': 2 {STAR_TOKEN}{STAR_TOKEN} 3}}\n=====END=====")
    srv = FakeAgentServer([reply])
    db, bus, reg, _, _ = make_parts([])
    llm = LLM(ledger=Ledger(db)); llm.provider = "elevenlabs"
    llm.eleven = ElevenBrain(lambda: "agent_brain", lambda v: None, transport=http_transport([], srv.port))
    llm._fetch_cost = lambda ref: None
    out = llm.text("system", "user", purpose="composing")
    srv.close()
    assert "'=' * 60" in out and "2 ** 3" in out and "# header ##" in out
    compile(out.split("=====FILE: workflow.py=====\n")[1].split("=====END=====")[0], "w", "exec")
    sent_system = srv.received[0]["conversation_config_override"]["agent"]["prompt"]["prompt"]
    assert "TRANSPORT RULE" in sent_system and STAR_TOKEN in sent_system
