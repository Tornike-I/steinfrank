"""Сквозная проверка мозга: задача -> создание органа -> повторное использование -> рецепт -> мутация -> самоаудит.
Песочница, БД, реестр, события, учёт расхода — настоящие. Подменён только LLM (сценарием ответов)."""
import json
import time

import pytest

from app import config
from app.brain import Brain
from helpers import ScriptedLLM, batch, files, many_tests

SPEC = {"name": "word_counter", "description": "counts words", "purpose_en": "Count words", "purpose_cs": "Počítá slova",
        "inputs": {"text": "str"}, "outputs": {"words": "int"}, "dependencies": [], "network": False}
GOOD = "def run(inp):\n    if not isinstance(inp.get('text'), str):\n        raise ValueError('text required')\n    return {'words': len(inp['text'].split())}\n"
BETTER = ("import re\ndef run(inp):\n    if not isinstance(inp.get('text'), str):\n        raise ValueError('text required')\n"
          "    return {'words': len(re.findall(r'\\w+', inp['text']))}\n")
WORKFLOW = ("=====FILE: workflow.py=====\nimport word_counter\ndef main(task):\n    n = word_counter.run({'text': task['text']})['words']\n"
            "    return {'summary': {'en': f'{n} words', 'cs': f'{n} slov'}, 'data': {'words': n}, 'files': []}\n=====END=====")
BAD_WORKFLOW = ("=====FILE: workflow.py=====\nimport word_counter\ndef main(task):\n    return {'summary': {'en': 'x', 'cs': 'x'}, 'data': {}, 'files': []}\n=====END=====")
PLAN_CREATE = json.dumps({"intent": "task", "understanding": {"en": "count words", "cs": "spočítat slova"},
                          "requirements": [{"id": "r1", "need": {"en": "count words", "cs": "počítat slova"},
                                            "decision": "CREATE", "capabilities": [], "spec": SPEC}]})
PLAN_REUSE = json.dumps({"intent": "task", "understanding": {"en": "count words", "cs": "x"},
                         "requirements": [{"id": "r1", "need": {"en": "count words", "cs": "x"}, "decision": "REUSE",
                                           "capabilities": ["word_counter"]}]})
PLAN_RECIPE = json.dumps({"intent": "task", "understanding": {"en": "count words", "cs": "x"}, "recipe": 1,
                          "requirements": [{"id": "r1", "need": {"en": "count words", "cs": "x"}, "decision": "REUSE",
                                            "capabilities": ["word_counter"]}]})
PLAN_SELF = json.dumps({"intent": "self_improve", "understanding": {"en": "audit", "cs": "audit"}, "requirements": []})


def wait(brain, timeout=120):
    t0 = time.time()
    while brain.is_busy() and time.time() - t0 < timeout:
        time.sleep(0.2)
    assert not brain.is_busy(), "task did not finish"


@pytest.fixture()
def brain(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "f.db")
    return Brain(llm=ScriptedLLM([]))


def types(brain):
    return [e["type"] for e in brain.bus.history(limit=1000)]


def calls_of(brain, task_id):
    return brain.ledger.summary(task_id)["task"]["llm_calls"]


def test_birth_state_is_empty(brain):
    assert brain.registry.generation() == 0 and brain.registry.list() == []
    assert brain.ledger.summary()["total"]["llm_calls"] == 0


def test_full_lifecycle(brain):
    # ---- задача 1: орган отсутствует -> создаётся, тестируется, устанавливается, используется (3 вызова модели)
    brain.llm.replies += [PLAN_CREATE, batch(("word_counter", GOOD, many_tests())), WORKFLOW]
    tid = brain.start_task("count the words in: one two three", [], "en")["task_id"]; wait(brain)
    t = brain.task(tid)
    assert t["status"] == "completed", t["error"]
    assert t["result"]["data"] == {"words": 7}   # реальный результат, посчитанный органом в песочнице
    assert brain.registry.generation() == 1 and t["generation_after"] == 1
    assert "CAPABILITY_GAP_DETECTED" in types(brain) and "CAPABILITY_INSTALLED" in types(brain)
    assert "CAPABILITY_REUSED" not in types(brain)
    assert calls_of(brain, tid) == 3            # план + генерация органа + воркфлоу — и всё
    cap = brain.registry.get("word_counter")
    assert cap["usage_count"] == 1 and cap["tests"] == 9 and cap["generation_created"] == 1

    # ---- задача 2: орган уже есть -> REUSE, новых капабилити нет, поколение не растёт (2 вызова)
    brain.llm.replies += [PLAN_REUSE, WORKFLOW]
    tid2 = brain.start_task("count again", [], "en")["task_id"]; wait(brain)
    assert brain.task(tid2)["status"] == "completed"
    assert "NO_NEW_CAPABILITIES_REQUIRED" in types(brain) and "CAPABILITY_REUSED" in types(brain)
    assert brain.registry.generation() == 1 and brain.registry.get("word_counter")["usage_count"] == 2
    assert calls_of(brain, tid2) == 2

    # ---- мутация принята: потомок проходит тест, который родитель проваливает
    extra = "    def test_punctuation(self):\n        self.assertEqual(capability.run({'text': 'a,b;c'})['words'], 3)\n"
    better_tests = many_tests() + extra
    brain.llm.replies += [files(BETTER, better_tests, name="word_counter")]
    out = brain.evolution.mutate("word_counter", reason="punctuation not handled")
    assert out.accepted and out.parent_score < 1.0 and out.child_score == 1.0, out
    cap = brain.registry.get("word_counter")
    assert cap["version"] == "1.1.0"
    assert [v["status"] for v in cap["versions"]] == ["retired", "active"]
    assert any(g["kind"] == "version" and g["version"] == "1.0.0" and g["cause"] == "superseded" for g in brain.registry.graveyard())

    # ---- мутация отклонена: ничего не улучшилось -> родитель остаётся
    brain.llm.replies += [files(BETTER, better_tests, name="word_counter")]
    out2 = brain.evolution.mutate("word_counter", reason="try again")
    assert not out2.accepted and "no measurable improvement" in out2.reason
    assert brain.registry.get("word_counter")["version"] == "1.1.0"

    # ---- самоаудит кнопкой: БЕЗ вызова планировщика; реальные замеры до/после
    brain.llm.replies += [files(BETTER, better_tests, name="word_counter")]
    tid3 = brain.start_self_audit("en"); wait(brain)
    assert brain.task(tid3)["status"] == "completed", brain.task(tid3)["error"]
    done = next(e for e in brain.bus.history(limit=1000) if e["type"] == "SELF_AUDIT_COMPLETED")
    assert done["data"]["before"]["tests"] > 0 and done["data"]["after"]["pass_rate"] == 1.0
    assert calls_of(brain, tid3) == 1           # только мутация


def test_recipe_runs_a_repeat_task_with_zero_workflow_calls(brain):
    brain.llm.replies += [PLAN_CREATE, batch(("word_counter", GOOD, many_tests())), WORKFLOW]
    brain.start_task("count the words in: one two three", [], "en")["task_id"]; wait(brain)
    assert "RECIPE_SAVED" in types(brain)
    # планировщик видит рецепт в памяти и выбирает его: остаётся ОДИН вызов модели (план), воркфлоу не пишется
    brain.llm.replies += [PLAN_RECIPE]
    tid = brain.start_task("count the words in: a b", [], "en")["task_id"]; wait(brain)
    t = brain.task(tid)
    assert t["status"] == "completed", t["error"]
    assert t["result"]["recipe_used"] is True and t["result"]["data"] == {"words": 6}   # результат пересчитан под новый текст
    assert "WORKFLOW_RECIPE_REUSED" in types(brain)
    assert calls_of(brain, tid) == 1 and brain.llm.replies == []
    assert brain.ledger.per_task()[-1]["llm_calls"] < brain.ledger.per_task()[0]["llm_calls"]   # повторная задача дешевле


def test_bad_recipe_falls_back_to_regenerating_the_workflow(brain):
    brain.llm.replies += [PLAN_CREATE, batch(("word_counter", GOOD, many_tests())), WORKFLOW]
    brain.start_task("count the words in: one two three", [], "en")["task_id"]; wait(brain)
    brain.db.execute("UPDATE recipes SET code=?", (BAD_WORKFLOW.split("=====")[2].split("\n", 1)[1],))   # «испортили» рецепт
    brain.llm.replies += [PLAN_RECIPE, WORKFLOW]
    tid = brain.start_task("count the words in: a b", [], "en")["task_id"]; wait(brain)
    t = brain.task(tid)
    assert t["status"] == "completed", t["error"]
    assert t["result"]["recipe_used"] is False and t["result"]["data"] == {"words": 6}   # проверка кодом поймала пустой результат
    assert "WORKFLOW_FAILED" in types(brain)


def test_dependent_blocks_retirement_and_graveyard_keeps_data(brain):
    brain.llm.replies += [PLAN_CREATE, batch(("word_counter", GOOD, many_tests())), WORKFLOW]
    brain.start_task("t1", [], "en")["task_id"]; wait(brain)
    assert brain.evolution.retire_manually("word_counter")["ok"]       # зависимых нет -> можно
    g = brain.registry.graveyard()
    assert g[0]["name"] == "word_counter" and g[0]["cause"] == "manual"   # данные сохранены, а не удалены
    assert brain.registry.get("word_counter")["status"] == "retired"


def test_planner_rejects_hallucinated_capability_and_bad_recipe(brain):
    bad = json.dumps({"intent": "task", "understanding": {"en": "x", "cs": "x"}, "requirements": [
        {"id": "r1", "need": {"en": "x", "cs": "x"}, "decision": "REUSE", "capabilities": ["does_not_exist"]}]})
    brain.llm.replies += [bad, PLAN_CREATE]    # первый ответ отклонят, второй (CREATE) пройдёт
    plan = brain.planner.analyze("anything", [])
    assert plan["requirements"][0]["decision"] == "CREATE"
    assert "CREATE" in brain.llm.prompts[1] and "direct_answer" in brain.llm.prompts[1]   # модели объяснили, как исправиться
    bad_recipe = json.dumps({"intent": "task", "understanding": {"en": "x", "cs": "x"}, "recipe": 99, "requirements": []})
    brain.llm.replies += [bad_recipe, PLAN_CREATE]
    brain.planner.analyze("anything else", [])
    assert "recipe" in brain.llm.prompts[-1]
    # тот же самый запрос ещё раз — проверенный план из кэша: ни одного нового вызова модели
    asked = len(brain.llm.prompts)
    again = brain.planner.analyze("anything else", [])
    assert len(brain.llm.prompts) == asked and again["requirements"][0]["decision"] == "CREATE"


def test_repeated_pattern_becomes_a_new_composite_organ(brain):
    """Две одинаковые цепочки вызовов -> агент сам предлагает и строит составной орган (без просьбы пользователя)."""
    spec2 = {**SPEC, "name": "char_counter", "description": "counts chars", "purpose_en": "Count chars",
             "purpose_cs": "Počítá znaky", "outputs": {"chars": "int"}}
    char_code = "def run(inp):\n    return {'chars': len(inp['text'])}\n"
    char_tests = many_tests(body="self.assertEqual(capability.run({'text': 'abc'})['chars'], 3)")
    plan = json.dumps({"intent": "task", "understanding": {"en": "u", "cs": "u"}, "requirements": [
        {"id": "r1", "need": {"en": "w", "cs": "w"}, "decision": "CREATE", "spec": SPEC},
        {"id": "r2", "need": {"en": "c", "cs": "c"}, "decision": "CREATE", "spec": spec2}]})
    workflow = ("=====FILE: workflow.py=====\nimport word_counter, char_counter\ndef main(task):\n"
                "    w = word_counter.run({'text': task['text']})['words']\n    c = char_counter.run({'text': task['text']})['chars']\n"
                "    return {'summary': {'en': 'ok', 'cs': 'ok'}, 'data': {'words': w, 'chars': c}, 'files': []}\n=====END=====")
    plan_reuse = json.dumps({"intent": "task", "understanding": {"en": "u", "cs": "u"}, "requirements": [
        {"id": "r1", "need": {"en": "w", "cs": "w"}, "decision": "COMPOSE", "capabilities": ["word_counter", "char_counter"]}]})
    # оба органа — ОДНИМ запросом генерации
    brain.llm.replies += [plan, batch(("word_counter", GOOD, many_tests()), ("char_counter", char_code, char_tests)), workflow]
    tid1 = brain.start_task("one two", [], "en")["task_id"]; wait(brain)
    assert calls_of(brain, tid1) == 3
    assert brain.registry.get("text_profile") is None            # после одной задачи паттерна ещё нет

    composite_spec = json.dumps({"name": "text_profile", "description": "words and chars together", "purpose_en": "Profile text",
                                 "purpose_cs": "Profil textu", "inputs": {"text": "str"}, "outputs": {"words": "int", "chars": "int"},
                                 "dependencies": [], "network": False, "edge_cases": []})
    composite_code = ("import word_counter, char_counter\ndef run(inp):\n"
                      "    return {'words': word_counter.run(inp)['words'], 'chars': char_counter.run(inp)['chars']}\n")
    composite_tests = many_tests(body="self.assertEqual(capability.run({'text': 'ab cd'}), {'words': 2, 'chars': 5})")
    brain.llm.replies += [plan_reuse, workflow, composite_spec, files(composite_code, composite_tests, name="text_profile")]
    brain.start_task("one two", [], "en")["task_id"]; wait(brain)
    comp = brain.registry.get("text_profile")
    assert comp and comp["status"] == "active"
    assert sorted(comp["dependencies"]) == ["char_counter", "word_counter"]
    assert comp["parent"] == "word_counter"                      # родословная для эволюционного дерева
    assert "REPEATED_PATTERN_DETECTED" in types(brain)
    assert brain.registry.generation() == 2                       # задача 1 -> gen 1, составной орган -> gen 2
    assert brain.registry.get("word_counter")["generation_created"] == 1
    assert brain.evolution.retire_manually("word_counter") == {"ok": False, "affected": ["text_profile"]}
