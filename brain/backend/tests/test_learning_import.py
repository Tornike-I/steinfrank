from app import config
from app.db import Database, dumps, now
from app.learning_import import import_learning
from app.registry import Registry


CODE = '''
SKILL = {"keywords": ["weather"], "intents": ["weather forecast"], "examples": ["weather in Prague"]}
def parse_request(text, context):
    return {"city": "Prague"} if "weather" in text.lower() else None
def run(city):
    return {"city": city}
def format_result(result, lang):
    return result["city"]
'''


def test_import_reuses_learning_without_copying_user_history(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CAPS_DIR", tmp_path / "capability-files")
    source_dir = tmp_path / "original-data"
    source = Database(source_dir / "frankenstein.db")
    Registry(source).install_new(
        {"name": "weather_fast", "description": "weather", "purpose_en": "Weather", "purpose_cs": "Počasí",
         "inputs": {"city": "str"}, "outputs": {"city": "str"}, "dependencies": [], "network": False},
        code=CODE, tests="def test_ok(): assert True", readme="fast", report={"total": 8, "passed": 8}, repairs=0,
    )
    source.execute(
        "INSERT INTO knowledge(scope,category,skill,key,params,content,source,created_at) VALUES(?,?,?,?,?,?,?,?)",
        ("global", "dynamic", "weather_fast", "weather:prague", dumps({"city": "Prague"}),
         dumps({"temperature": 20}), "test", now()),
    )
    source.execute(
        "INSERT INTO knowledge_packs(kind,key,title,aliases,content,source,validation_status,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("topic", "weather", "Weather", dumps(["forecast"]), dumps({"facts": []}), "curated", "validated", now(), now()),
    )
    source.execute("INSERT INTO llm_cache(key,response,created_at) VALUES(?,?,?)", ("cached-plan", "{}", now()))
    source.execute("INSERT INTO tasks(input,status,created_at) VALUES(?,?,?)", ("private task", "completed", now()))
    source.execute("INSERT INTO monsters(name,seed,emotion,status,created_at) VALUES(?,?,?,?,?)",
                   ("Private monster", 1, "happy", "alive", now()))

    target = Database(tmp_path / "lab-data" / "frankenstein.db")
    registry = Registry(target)
    result = import_learning(target, registry, source_dir)

    assert result["capabilities"] == 1 and registry.get("weather_fast")
    assert result["knowledge"] == 1 and result["knowledge_packs"] == 1 and result["llm_cache"] == 1
    assert target.one("SELECT COUNT(*) AS n FROM tasks")["n"] == 0
    assert target.one("SELECT COUNT(*) AS n FROM monsters")["n"] == 0

    again = import_learning(target, registry, source_dir)
    assert all(value == 0 for value in again.values())
