from helpers import batch, files, make_parts, many_tests, repair

SPEC = {"name": "word_counter", "description": "counts words", "purpose_en": "Count words", "purpose_cs": "Počítá slova",
        "inputs": {"text": "str"}, "outputs": {"words": "int"}, "dependencies": [], "network": False}

GOOD = "def run(inp):\n    if not isinstance(inp.get('text'), str):\n        raise ValueError('text required')\n    return {'words': len(inp['text'].split())}\n"
BUGGY = "def run(inp):\n    return {'words': len(inp['text'].split(','))}\n"   # считает по запятым — тесты упадут


def test_build_fail_then_diagnose_repair_install():
    # ровно ДВА вызова модели: генерация + «диагноз и ремонт» одним запросом
    db, bus, reg, llm, builder = make_parts([files(BUGGY, many_tests()), repair("word_counter", GOOD, many_tests())])
    out = builder.build(SPEC)
    assert out.ok and out.repairs == 1 and out.report["passed"] == out.report["total"] == 9
    assert llm.replies == []
    cap = reg.get("word_counter")
    assert cap["status"] == "active" and cap["version"] == "1.0.0" and cap["tests"] == 9
    order = [e["type"] for e in bus.history() if e["type"] in (
        "CAPABILITY_TEST_FAILED", "CAPABILITY_DIAGNOSIS", "CAPABILITY_REPAIR", "CAPABILITY_TEST_PASSED", "CAPABILITY_INSTALLED")]
    assert order == ["CAPABILITY_TEST_FAILED", "CAPABILITY_DIAGNOSIS", "CAPABILITY_REPAIR",
                     "CAPABILITY_TEST_PASSED", "CAPABILITY_INSTALLED"]
    failed = next(e for e in bus.history() if e["type"] == "CAPABILITY_TEST_FAILED")
    assert failed["data"]["failed"] == 9 and failed["data"]["passed"] == 0
    assert "splits on commas" in reg.notes()[0]            # диагноз стал уроком в памяти — бесплатно


def test_untested_code_is_never_installed():
    never_good = files(BUGGY, many_tests())
    db, bus, reg, llm, builder = make_parts([never_good] + [repair("word_counter", BUGGY, many_tests())] * 3)
    out = builder.build(SPEC)
    assert not out.ok
    assert reg.get("word_counter") is None
    assert "CAPABILITY_REJECTED" in [e["type"] for e in bus.history()]


def test_too_few_tests_is_rejected():
    few = files(GOOD, many_tests(3))
    db, bus, reg, llm, builder = make_parts([few] + [repair("word_counter", GOOD, many_tests(3))] * 3)
    assert not builder.build(SPEC).ok


def test_forbidden_import_is_caught_before_sandbox():
    evil = "import subprocess\ndef run(inp):\n    return {'words': 1}\n"
    db, bus, reg, llm, builder = make_parts([files(evil, many_tests())] + [repair("word_counter", evil, many_tests())] * 3)
    out = builder.build(SPEC)
    assert not out.ok and "forbidden" in out.reason


def test_batch_builds_dependent_capabilities_with_one_generation_call():
    """Два органа (второй зависит от первого, оба ещё не установлены) = ОДИН запрос на генерацию."""
    top_spec = {**SPEC, "name": "text_stats", "dependencies": ["word_counter"]}
    top_code = "import word_counter\ndef run(inp):\n    return {'words': word_counter.run(inp)['words']}\n"
    db, bus, reg, llm, builder = make_parts([batch(("word_counter", GOOD, many_tests()), ("text_stats", top_code, many_tests()))])
    a, b = builder.build_many([SPEC, top_spec], parents={"text_stats": "word_counter"})
    assert a.ok and b.ok and llm.replies == []
    assert len(llm.prompts) == 1                            # единственный вызов модели
    assert reg.dependents("word_counter") == ["text_stats"]
    assert reg.get("text_stats")["parent"] == "word_counter"
    assert reg.get("word_counter")["children"] == ["text_stats"]


def test_batch_repairs_all_failures_in_one_call():
    spec_b = {**SPEC, "name": "word_counter_b"}
    two_repairs = repair("word_counter", GOOD, many_tests()) + "\n" + repair("word_counter_b", GOOD, many_tests())
    db, bus, reg, llm, builder = make_parts([
        batch(("word_counter", BUGGY, many_tests()), ("word_counter_b", BUGGY, many_tests())), two_repairs])
    res = builder.build_many([SPEC, spec_b])
    assert all(r.ok for r in res) and len(llm.prompts) == 2   # 1 генерация + 1 ремонт на ОБА органа
    assert all(r.repairs == 1 for r in res)
