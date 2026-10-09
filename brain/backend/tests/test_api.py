from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_initial_state_is_generation_zero():
    s = client.get("/api/state").json()
    assert s["generation"] == 0 and s["capabilities"] == [] and s["graveyard"] == []
    assert s["metrics"]["capabilities_total"] == 0 and s["metrics"]["task_success_rate"] is None
    assert s["llm"]["configured"] is False


def test_task_without_api_key_gives_clear_error():
    r = client.post("/api/tasks", json={"text": "hello"})
    assert r.status_code == 503 and "is not set" in r.json()["detail"]


def test_demo_tasks_list_real_files():
    tasks = client.get("/api/demo/tasks").json()
    assert [t["id"] for t in tasks] == ["birth_task", "memory_task", "evolution_task"]
    assert all(len(t["files"]) >= 3 for t in tasks)


def test_upload_and_path_traversal_protection():
    assert client.post("/api/upload", files={"file": ("a b.pdf", b"%PDF-1.4")}).json()["name"] == "a_b.pdf"
    assert client.get("/api/artifacts/1/../../frankenstein.db").status_code == 404
    from app import main
    import pytest
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        main._resolve_files(["demo:../backend/app/config.py"])
    with pytest.raises(HTTPException):
        main._resolve_files(["../../etc/passwd"])


def test_reset_returns_to_generation_zero():
    assert client.post("/api/demo/reset").json() == {"ok": True}
    assert client.get("/api/state").json()["generation"] == 0


def test_state_has_usage_voice_and_llm_info():
    s = client.get("/api/state?events=0").json()
    assert s["events"] == [] and s["usage"]["total"]["llm_calls"] == 0 and s["usage"]["credit_usd"] > 0
    assert s["voice"]["configured"] is False and "missing" in s["llm"]


def test_speak_without_key_is_a_clear_503():
    r = client.post("/api/speak", json={"text": "hello", "mood": "happy"})
    assert r.status_code == 503 and "ELEVENLABS_API_KEY" in r.json()["detail"]


def test_skill_economy_and_team_endpoints():
    eco = client.get("/api/economy").json()
    assert set(eco) >= {"measured", "estimated", "note", "knowledge"}
    assert eco["estimated"]["method"] and "saved_tokens" in eco["estimated"]
    assert client.post("/api/recommend/team", json={"text": "build a weather website"}).json() is None   # монстров нет — команды нет
    assert client.get("/api/knowledge").json() == []
    assert client.post("/api/monsters/999/skills", json={"name": "x"}).status_code == 404
    assert client.delete("/api/monsters/999/skills/x").status_code == 404
    assert client.post("/api/capabilities/nope/rollback").status_code == 409
    assert client.put("/api/capabilities/nope/visibility", json={"visibility": "secret"}).status_code == 400
    assert client.post("/api/tasks", json={"text": "hi", "mode": "squad"}).status_code in (400, 503)
