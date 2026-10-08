import asyncio
import copy
import json
from pathlib import Path

import pytest

from frankenstein.forge.estimator import estimate
from frankenstein.forge.validator import validate
from frankenstein.spec import MonsterSpec

BASE = json.loads((Path(__file__).resolve().parents[1] / "monsters" / "page-narrator.json").read_text(encoding="utf-8"))
AGENTS = {
    "agent1": {
        "id": "agent1", "name": "Researcher", "credits": 30,
        "fields": [{"id": "company_name", "type": "text", "optional": False}, {"id": "prompt", "type": "textarea", "optional": True}],
    }
}


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    for k in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SOKOSUMI_API_KEY"):
        monkeypatch.setenv(k, "test")


def errors(raw):
    return asyncio.run(validate(MonsterSpec.model_validate(raw), AGENTS))


def mutate(fn):
    raw = copy.deepcopy(BASE)
    fn(raw)
    return errors(raw)


def test_reference_monster_is_valid():
    assert errors(BASE) == []
    est = estimate(MonsterSpec.model_validate(BASE))
    assert 0 < est.max.llm_tokens <= BASE["policy"]["max_llm_tokens"] and est.max.credits == 0
    assert est.min.llm_tokens == 0 and est.max.tts_chars >= 900


@pytest.mark.parametrize(
    "fn,needle",
    [
        (lambda r: r["steps"][0]["args"].update(url="{{inputs.nope}}"), "not a declared input"),
        (lambda r: r["steps"][1]["args"].update(text="{{steps.say.file}}"), "earlier step"),
        (lambda r: r["steps"].append({"id": "x", "limb": "teleport"}), "unknown limb"),
        (lambda r: r["steps"][2].update(when="steps.clean.chars ** 2"), "when"),
        (lambda r: r["policy"].update(max_llm_tokens=100), "worst-case llm tokens"),
        (lambda r: r["policy"].update(max_credits=10**6), "global cap"),
        (lambda r: r["policy"].update(allowed_domains=[]), "allowed_domains"),
        (lambda r: r["steps"][2]["args"].update(temperature=1), "unknown arg"),
        (lambda r: r["forged_limbs"][0].update(code="import os\ndef run(text, limit): return {}"), "import os"),
        (lambda r: r["inputs"]["properties"]["url"].pop("description"), "needs a description"),
        (lambda r: r["steps"][3]["args"].update(summary="{{steps.sum.summery}}"), "has no field 'summery'"),
        (lambda r: r["steps"][3]["args"].update(title="{{steps.clean.nope}}"), "has no field 'nope'"),
        (lambda r: r["output"].pop("speech"), "output.speech is required"),
        (lambda r: r.update(examples=[{"link": "x"}]), "examples[0]"),
        (lambda r: r["steps"].append({"id": "n", "limb": "notify", "args": {"title": "t", "message": "m"}}), "max_notifications"),
        (lambda r: r["steps"].insert(0, {"id": "g", "parallel": [
            {"id": "p1", "limb": "http_fetch", "args": {"url": "{{inputs.url}}"}},
            {"id": "p2", "limb": "forged:trim_text", "args": {"text": "{{steps.p1.text}}", "limit": 5}}]}), "earlier step"),
        (lambda r: r["steps"].insert(0, {"id": "g", "parallel": [{"id": "p1", "limb": "http_fetch", "args": {"url": "x"}}]}), "at least 2"),
    ],
)
def test_rejections(fn, needle):
    errs = mutate(fn)
    assert any(needle in e for e in errs), errs


def test_sokosumi_checks():
    def add(r, args, for_each=None):
        r["policy"]["max_credits"] = 100
        step = {"id": "hire", "limb": "sokosumi", "args": args}
        if for_each:
            step["for_each"] = for_each
        r["steps"].append(step)

    ok = mutate(lambda r: add(r, {"agent_id": "agent1", "inputs": {"company_name": "{{inputs.url}}"}, "max_credits": 30}))
    assert ok == []
    assert any("missing" in e for e in mutate(lambda r: add(r, {"agent_id": "agent1", "inputs": {}, "max_credits": 30})))
    assert any("below the agent price" in e for e in mutate(lambda r: add(r, {"agent_id": "agent1", "inputs": {"company_name": "x"}, "max_credits": 5})))
    assert any("unknown Sokosumi" in e for e in mutate(lambda r: add(r, {"agent_id": "zzz", "inputs": {}, "max_credits": 30})))
    fe = {"over": "steps.page.text", "max": 2}
    assert any("for_each" in e for e in mutate(lambda r: add(r, {"agent_id": "agent1", "inputs": {"company_name": "x"}, "max_credits": 30}, fe)))
