"""Монстры: создание, повторное использование без пересборки, эволюция, неудачный эксперимент, отмена, миграции.
Песочница, БД, реестр — настоящие; подменён только LLM (сценарий ответов)."""
import json
import sqlite3
import threading
import time

import pytest

from app import cancel, config
from app.brain import Brain
from app.db import Database
from app.monsters import Monsters, categorize
from helpers import ScriptedLLM, batch, many_tests, repair

SPEC_W = {"name": "word_counter", "description": "counts words", "purpose_en": "Count words in text", "purpose_cs": "Počítá slova",
          "inputs": {"text": "str"}, "outputs": {"words": "int"}, "dependencies": [], "network": False}
SPEC_C = {**SPEC_W, "name": "char_counter", "description": "counts characters", "purpose_en": "Count characters in text",
          "purpose_cs": "Počítá znaky", "outputs": {"chars": "int"}}
GOOD_W = "def run(inp):\n    if not isinstance(inp.get('text'), str):\n        raise ValueError('text')\n    return {'words': len(inp['text'].split())}\n"
BUGGY_W = "def run(inp):\n    return {'words': len(inp['text'].split(','))}\n"
GOOD_C = "def run(inp):\n    return {'chars': len(inp['text'])}\n"
TESTS_C = many_tests(body="self.assertEqual(capability.run({'text': 'abc'})['chars'], 3)")
WF_W = ("=====FILE: workflow.py=====\nimport word_counter\ndef main(task):\n    n = word_counter.run({'text': task['text']})['words']\n"
        "    return {'summary': {'en': f'{n} words', 'cs': f'{n} slov'}, 'data': {'words': n}, 'files': []}\n=====END=====")
WF_WC = ("=====FILE: workflow.py=====\nimport word_counter, char_counter\ndef main(task):\n"
         "    return {'summary': {'en': 'ok', 'cs': 'ok'}, 'data': {'w': word_counter.run({'text': task['text']})['words'],"
         " 'c': char_counter.run({'text': task['text']})['chars']}, 'files': []}\n=====END=====")


def plan(*reqs):
    return json.dumps({"intent": "task", "understanding": {"en": "u", "cs": "u"}, "requirements": list(reqs)})


def create(spec):
    return {"id": spec["name"], "need": {"en": "n", "cs": "n"}, "decision": "CREATE", "spec": spec}


def reuse(*names):
    return {"id": "r", "need": {"en": "n", "cs": "n"}, "decision": "REUSE", "capabilities": list(names)}


def wait(brain, timeout=120):
    t0 = time.time()
    while brain.is_busy() and time.time() - t0 < timeout:
        time.sleep(0.1)
    assert not brain.is_busy()


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "m.db")
    return Brain(llm=ScriptedLLM([]))


def types(brain, task_id=None):
    return [e["type"] for e in brain.bus.history(limit=2000) if task_id is None or e["task_id"] == task_id]


def test_new_monster_is_born_with_built_organs_and_persists(brain, tmp_path):
    brain.llm.replies += [plan(create(SPEC_W)), batch(("word_counter", GOOD_W, many_tests())), WF_W]
    out = brain.start_task("count words: a b c", [], "en", mode="new", emotion="angry")
    wait(brain)
    t = brain.task(out["task_id"])
    assert t["status"] == "completed" and t["monster_id"] == out["monster_id"]
    m = brain.monsters.get(out["monster_id"])
    assert m["status"] == "alive" and m["emotion"] == "angry" and m["version"] == 2       # v1 рождён, v2 выучил орган
    assert [c["name"] for c in m["capabilities"]] == ["word_counter"] and m["capabilities"][0]["source"] == "built"
    assert [h["kind"] for h in m["history"]] == ["born", "learned"]
    ev = types(brain, out["task_id"])
    # порядок для анимации: формирование -> апгрейд -> готов -> ответ
    assert ev.index("MONSTER_FORMING") < ev.index("CAPABILITY_INSTALLED") < ev.index("MONSTER_UPGRADED") \
        < ev.index("MONSTER_READY") < ev.index("TASK_COMPLETED")
    ready = next(e for e in brain.bus.history(limit=2000) if e["type"] == "MONSTER_READY")
    assert "Count words" in ready["data"]["intro"]["en"] and ready["data"]["monster"]["seed"] == m["seed"]
    # «перезапуск приложения»: новый Brain на той же БД видит того же монстра с той же внешностью (seed)
    again = Brain(llm=ScriptedLLM([]))
    m2 = again.monsters.get(out["monster_id"])
    assert m2["seed"] == m["seed"] and m2["name"] == m["name"] and [c["name"] for c in m2["capabilities"]] == ["word_counter"]


def test_reused_monster_does_not_rebuild_and_remembers(brain):
    brain.llm.replies += [plan(create(SPEC_W)), batch(("word_counter", GOOD_W, many_tests())), WF_W]
    mid = brain.start_task("count words: a b", [], "en")["monster_id"]; wait(brain)
    brain.llm.replies += [plan(reuse("word_counter")), WF_W]
    out = brain.start_task("count words again: x y z", [], "en", mode="reuse", monster_id=mid); wait(brain)
    ev = types(brain, out["task_id"])
    assert "MONSTER_ACTIVATED" in ev and "MONSTER_FORMING" not in ev
    assert "CAPABILITY_BUILD_STARTED" not in ev and "NO_NEW_CAPABILITIES_REQUIRED" in ev    # без регенерации
    assert brain.ledger.summary(out["task_id"])["task"]["llm_calls"] == 2                     # план + воркфлоу, без сборки
    m = brain.monsters.get(mid)
    assert m["tasks_ok"] == 2 and m["version"] == 2 and len(brain.monsters.memory(mid)) == 2
    assert "ACTIVE MONSTER" in brain.llm.prompts[-2]                                          # планировщик знал о монстре


def test_existing_monster_evolves_with_a_new_organ(brain):
    brain.llm.replies += [plan(create(SPEC_W)), batch(("word_counter", GOOD_W, many_tests())), WF_W]
    mid = brain.start_task("count words", [], "en")["monster_id"]; wait(brain)
    # БАРЬЕР: существующий монстр не строит орган молча — сначала подтверждение (1 вызов планировщика, 0 сборок)
    brain.llm.replies += [plan(reuse("word_counter"), create(SPEC_C))]
    ask = brain.start_task("count words and characters", [], "en", mode="reuse", monster_id=mid); wait(brain)
    t = brain.task(ask["task_id"])
    assert t["status"] == "completed" and t["result"]["data"]["decision"]["user_confirmation_required"] is True
    assert "char_counter" in t["result"]["data"]["decision"]["missing_functionality"]
    assert "CAPABILITY_BUILD_STARTED" not in types(brain, ask["task_id"]) and brain.registry.get("char_counter") is None
    # пользователь подтвердил — орган строится
    brain.llm.replies += [plan(reuse("word_counter"), create(SPEC_C)), batch(("char_counter", GOOD_C, TESTS_C)), WF_WC]
    out = brain.start_task("count words and characters", [], "en", mode="reuse", monster_id=mid, allow_build=True); wait(brain)
    assert brain.task(out["task_id"])["status"] == "completed"
    m = brain.monsters.get(mid)
    assert m["version"] == 3 and {c["name"] for c in m["capabilities"]} == {"word_counter", "char_counter"}
    up = [e for e in brain.bus.history(limit=2000) if e["type"] == "MONSTER_UPGRADED" and e["task_id"] == out["task_id"]]
    assert [u["capability"] for u in up] == ["char_counter"]


def test_organ_from_library_is_shared_without_rebuilding(brain):
    brain.llm.replies += [plan(create(SPEC_W)), batch(("word_counter", GOOD_W, many_tests())), WF_W]
    brain.start_task("count words", [], "en"); wait(brain)
    brain.llm.replies += [plan(reuse("word_counter")), WF_W]                                   # НОВЫЙ монстр, но орган уже есть
    out = brain.start_task("count words please", [], "en", mode="new", emotion="sad"); wait(brain)
    m = brain.monsters.get(out["monster_id"])
    assert m["capabilities"][0]["source"] == "library"
    assert "CAPABILITY_BUILD_STARTED" not in types(brain, out["task_id"])


def test_failed_experiment_installs_nothing_and_monster_never_wakes(brain):
    never = batch(("word_counter", BUGGY_W, many_tests()))
    brain.llm.replies += [plan(create(SPEC_W)), never] + [repair("word_counter", BUGGY_W, many_tests())] * config.MAX_REPAIRS
    out = brain.start_task("count words", [], "en"); wait(brain)
    assert brain.task(out["task_id"])["status"] == "failed"
    assert brain.registry.get("word_counter") is None                     # ничего непроверенного не установлено
    m = brain.monsters.get(out["monster_id"])
    assert m["status"] == "failed" and m["capabilities"] == []
    ev = types(brain, out["task_id"])
    assert "MONSTER_FAILED" in ev and "MONSTER_READY" not in ev and "TASK_COMPLETED" not in ev
    assert ev.count("CAPABILITY_REPAIR") == config.MAX_REPAIRS                # ограниченное число ремонтов
    assert brain.monsters.list() == []                                      # в библиотеке только живые


def test_cancel_stops_before_the_next_brain_call(brain):
    gate = threading.Event()
    original = brain.llm._chat

    def slow_chat(messages, json_mode):
        gate.wait(5)
        return original(messages, json_mode)
    brain.llm._chat = slow_chat
    brain.llm.replies += [plan(create(SPEC_W))]
    out = brain.start_task("count words", [], "en")
    time.sleep(0.2)
    assert brain.cancel_task(out["task_id"])
    gate.set()
    wait(brain)
    t = brain.task(out["task_id"])
    assert t["status"] == "cancelled" and "TASK_CANCELLED" in types(brain, out["task_id"])
    assert brain.llm.replies == []          # следующий (платный) вызов не делался
    assert not cancel.is_cancelled(out["task_id"])


def test_reuse_of_unknown_monster_is_rejected(brain):
    with pytest.raises(ValueError):
        brain.start_task("x", [], "en", mode="reuse", monster_id=999)
    assert not brain.is_busy()


def test_recommendation_and_categories(brain):
    brain.llm.replies += [plan(create({**SPEC_W, "name": "pdf_text_reader", "purpose_en": "Extract text from PDF documents"})),
                          batch(("pdf_text_reader", GOOD_W, many_tests())),
                          WF_W.replace("word_counter", "pdf_text_reader")]
    mid = brain.start_task("read pdf", [], "en")["monster_id"]; wait(brain)
    rec = brain.monsters.recommend("Please analyze these PDF documents for me")
    assert rec and rec[0]["monster"]["id"] == mid and rec[0]["matched"] == ["pdf_text_reader"]
    assert brain.monsters.recommend("bake a chocolate cake") == []
    assert categorize("pdf_text_reader") == "scanner" and categorize("excel_anomaly_finder") == "holo"
    assert categorize("web_search") == "eye" and categorize("mystery") == "core"


def test_names_are_unique_and_appearance_seed_is_stable(tmp_path):
    db = Database(tmp_path / "n.db")
    from app.registry import Registry
    ms = Monsters(db, Registry(db))
    a, b = ms.create("happy", seed=42), ms.create("happy", seed=42)
    assert a["name"] != b["name"] and a["seed"] == b["seed"] == 42 and a["voice_id"] == b["voice_id"]


def test_migration_keeps_data_of_an_old_database(tmp_path):
    """База из предыдущей версии (без таблиц монстров) открывается, данные на месте, новые колонки добавлены."""
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO meta VALUES ('generation','3');
        CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, input TEXT NOT NULL, files TEXT, lang TEXT, status TEXT NOT NULL,
            result TEXT, error TEXT, trace TEXT, duration REAL, generation_before INTEGER, generation_after INTEGER,
            created_at TEXT NOT NULL, finished_at TEXT);
        INSERT INTO tasks(input,status,created_at) VALUES ('old task','completed','2026-01-01');
        CREATE TABLE usage (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER, kind TEXT NOT NULL, purpose TEXT, provider TEXT,
            prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0, chars INTEGER DEFAULT 0, credits REAL DEFAULT 0,
            seconds REAL DEFAULT 0, created_at TEXT NOT NULL);
    """)
    con.commit(); con.close()
    db = Database(path)
    assert db.one("SELECT input FROM tasks")["input"] == "old task"
    assert db.one("SELECT value FROM meta WHERE key='generation'")["value"] == "3"
    cols = {r["name"] for r in db.query("PRAGMA table_info(tasks)")}
    assert "monster_id" in cols and db.one("SELECT value FROM meta WHERE key='schema_version'")["value"] == str(__import__("app.db", fromlist=["MIGRATIONS"]).MIGRATIONS[-1][0])
    Database(path)                                       # повторное открытие не ломается


def test_advice_task_gets_a_direct_answer_without_organs(brain):
    """«Купить BMW в Праге» — инструменты не нужны (или нужен выключенный интернет): монстр честно отвечает сам."""
    plan = json.dumps({"intent": "task", "understanding": {"en": "car advice", "cs": "rada"}, "needs_internet": True,
                       "requirements": [{"id": "r1", "need": {"en": "x", "cs": "x"}, "decision": "REUSE", "capabilities": []}],
                       "direct_answer": {"en": "I cannot browse live listings, but here is how to choose...", "cs": "Nemohu procházet inzeráty..."}})
    brain.llm.replies += [plan]                                   # «REUSE ничего» убирается кодом — без повторного вызова
    out = brain.start_task("I need a cheap BMW in Prague for 30-50k dollars", [], "en"); wait(brain)
    t = brain.task(out["task_id"])
    assert t["status"] == "completed", t["error"]
    assert t["result"]["summary"]["en"].startswith("I cannot browse") and t["result"]["data"]["needs_internet"] is True
    assert brain.ledger.summary(out["task_id"])["task"]["llm_calls"] == 1
    ev = types(brain, out["task_id"])
    assert "DIRECT_ANSWER" in ev and "MONSTER_READY" in ev and "CAPABILITY_BUILD_STARTED" not in ev
    assert brain.monsters.get(out["monster_id"])["status"] == "alive"


def test_planner_normalizes_obvious_mistakes(brain):
    from app.planner import Planner
    plan = {"intent": "task", "understanding": {"en": "u", "cs": "u"}, "requirements": [
        {"id": "r1", "need": {"en": "w", "cs": "w"}, "decision": "reuse", "capabilities": ["ghost"], "spec": SPEC_W}]}
    fixed = Planner._normalize(plan, set())
    assert fixed["requirements"][0]["decision"] == "CREATE"
    assert Planner._validate(fixed, set()) == []
    empty = Planner._normalize({"intent": "task", "understanding": {"en": "u", "cs": "u"}, "requirements": [
        {"id": "r1", "need": {}, "decision": "REUSE", "capabilities": []}]}, set())
    errors = Planner._validate(empty, set())
    assert errors and "direct_answer" in errors[0] and "CREATE" in errors[0]


def test_repair_my_monster_finishes_with_a_maintenance_report_not_a_workflow(brain):
    """«Почини монстра»: органы проверяются мутацией, воркфлоу НЕ требуется (раньше задача падала здесь)."""
    brain.llm.replies += [plan(create(SPEC_W)), batch(("word_counter", GOOD_W, many_tests())), WF_W]
    mid = brain.start_task("count words", [], "en")["monster_id"]; wait(brain)
    mutate_plan = json.dumps({"intent": "task", "understanding": {"en": "repair", "cs": "oprava"}, "requirements": [
        {"id": "r1", "need": {"en": "fix", "cs": "fix"}, "decision": "MUTATE", "capabilities": ["word_counter"], "mutation_reason": "check"}]})
    from helpers import files as one
    brain.llm.replies += [mutate_plan, one(GOOD_W, many_tests(), name="word_counter")]   # потомок не лучше -> родитель остаётся
    out = brain.start_task("repair my monster to work fully done", [], "en", mode="reuse", monster_id=mid); wait(brain)
    t = brain.task(out["task_id"])
    assert t["status"] == "completed", t["error"]
    assert "examined 1 organ" in t["result"]["summary"]["en"] and t["result"]["data"]["maintenance"][0]["improved"] is False
    assert "WORKFLOW_COMPOSING" not in types(brain, out["task_id"]) and brain.llm.replies == []
    assert "MONSTER_READY" in types(brain, out["task_id"])


def test_workflow_code_is_found_without_markers_and_prose_is_reported():
    from app.executor import extract_workflow
    assert "def main" in extract_workflow("Sure!\n```python\nimport x\ndef main(task):\n    return {}\n```\nDone.")
    assert extract_workflow("=====FILE: workflow.py=====\ndef main(task):\n    return {}\n=====END=====").startswith("def main")
    assert extract_workflow("I think your monster is already fine, what else would you like?") is None


def test_workflow_failure_does_not_rewrite_an_organ_without_confirmation(brain):
    """Найдено в живых данных (задача #43): воркфлоу упал -> диагноз «орган сломан» -> мутация началась МОЛЧА.
    Теперь у существующего монстра это решение барьера: задача останавливается с объяснением и кнопкой."""
    brain.llm.replies += [plan(create(SPEC_W)), batch(("word_counter", GOOD_W, many_tests())), WF_W]
    mid = brain.start_task("count words", [], "en")["monster_id"]; wait(brain)
    bad_wf = "=====FILE: workflow.py=====\nimport word_counter\ndef main(task):\n    return word_counter.run({'wrong': 1})\n=====END====="
    diag = json.dumps({"kind": "capability_bug", "capability": "word_counter", "diagnosis": {"en": "organ rejects input", "cs": "x"},
                       "reason": "accept other keys", "spec": None})
    brain.llm.replies += [plan(reuse("word_counter")), bad_wf, diag]
    out = brain.start_task("count the words in my essay please", [], "en", mode="reuse", monster_id=mid); wait(brain)
    t = brain.task(out["task_id"])
    ev = types(brain, out["task_id"])
    assert t["status"] == "completed" and t["result"]["data"]["decision"]["action"] == "repair_skill"
    assert "FAILURE_NEEDS_CONFIRMATION" in ev and "CAPABILITY_MUTATION_STARTED" not in ev
    assert brain.registry.get("word_counter")["version"] == "1.0.0"
