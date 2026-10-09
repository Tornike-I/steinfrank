import asyncio

import pytest

from frankenstein import store
from frankenstein.limbs import Cost, Limb, NeedsInput, Pending
from frankenstein.limbs import get_limb as real_get_limb
from frankenstein.runtime import InputError, Runner, check_inputs, clip_speech
from frankenstein.spec import MonsterSpec


class Echo(Limb):
    name = "echo"

    async def run(self, args, ctx):
        return dict(args)


class Slow(Limb):
    name = "slow"
    active = 0
    peak = 0

    async def run(self, args, ctx):
        Slow.active += 1
        Slow.peak = max(Slow.peak, Slow.active)
        await asyncio.sleep(0.05)
        Slow.active -= 1
        return {"v": args.get("v")}


class Spendy(Limb):
    name = "spendy"

    def precheck(self, args):
        return Cost(llm_tokens=int(args.get("tokens", 0)))

    async def run(self, args, ctx):
        ctx.charge(Cost(llm_tokens=int(args.get("tokens", 0))))
        return {"ok": True}


class FakeJob(Limb):
    name = "job"
    is_async_job = True

    def precheck(self, args):
        return Cost(credits=float(args.get("credits", 0)))

    async def run(self, args, ctx):
        return Pending({"n": 0, "tag": args.get("tag")})

    async def poll(self, state, ctx):
        n = state["n"] + 1
        if state.get("ask") and n == 2 and "answered" not in state:
            return NeedsInput("which one?", None, {**state, "n": n})
        if n >= 3:
            return {"result": f"{state['tag']} done after {n}", "answer": state.get("answered")}
        return Pending({**state, "n": n})

    async def answer(self, state, data, ctx):
        return Pending({**state, "answered": data["pick"]})


class AskingJob(FakeJob):
    async def run(self, args, ctx):
        return Pending({"n": 0, "tag": args.get("tag"), "ask": True})


FAKES = {"echo": Echo(), "slow": Slow(), "spendy": Spendy(), "job": FakeJob(), "ask": AskingJob()}


def limb_for(name, spec):
    return FAKES.get(name) or real_get_limb(name, spec)


def make(steps, **kw):
    return MonsterSpec.model_validate(
        {
            "id": "test",
            "name": "Test",
            "purpose": "test",
            "inputs": {"type": "object", "properties": {"xs": {"type": "array"}, "q": {"type": "string"}}},
            "steps": steps,
            **kw,
        }
    )


def run(spec, inputs, **kw):
    return asyncio.run(Runner(limb_for).start(spec, inputs, **kw))


async def drain(runner, r):
    while r["status"] == "waiting":
        r = await runner.poll(r)
    return r


def test_branching_and_fanout():
    spec = make(
        [
            {"id": "a", "limb": "echo", "args": {"xs": "{{inputs.xs}}"}},
            {"id": "b", "limb": "echo", "args": {"v": "{{x}}!"}, "for_each": {"over": "steps.a.xs", "max": 2, "as": "x"}},
            {"id": "c", "limb": "echo", "args": {"v": 1}, "when": "len(steps.a.xs) > 5"},
        ],
        output={"vals": "{{steps.b.items}}", "c": "{{steps.c}}"},
    )
    r = run(spec, {"xs": ["p", "q", "r"]})
    assert r["status"] == "completed", r["error"]
    assert r["output"]["vals"] == [{"v": "p!"}, {"v": "q!"}] and r["output"]["c"] is None


def test_parallel_group_runs_concurrently():
    Slow.peak = 0
    spec = make(
        [
            {"id": "gather", "parallel": [
                {"id": "a", "limb": "slow", "args": {"v": 1}},
                {"id": "b", "limb": "slow", "args": {"v": 2}},
                {"id": "c", "limb": "slow", "args": {"v": 3}, "when": "inputs.q == 'all'"},
            ]},
            {"id": "sum", "limb": "echo", "args": {"a": "{{steps.a.v}}", "b": "{{steps.b.v}}", "c": "{{steps.c}}"}},
        ],
        output={"sum": "{{steps.sum}}"},
    )
    r = run(spec, {"q": "some"})
    assert r["status"] == "completed", r["error"]
    assert r["output"]["sum"] == {"a": 1, "b": 2, "c": None}
    assert Slow.peak == 2


def test_parallel_async_jobs_and_input():
    spec = make(
        [{"id": "jobs", "parallel": [{"id": "j1", "limb": "job", "args": {"tag": "one"}},
                                     {"id": "j2", "limb": "ask", "args": {"tag": "two"}}]}],
        output={"r1": "{{steps.j1.result}}", "r2": "{{steps.j2.result}}", "a": "{{steps.j2.answer}}"},
    )
    runner = Runner(limb_for)

    async def go():
        r = await runner.start(spec, {})
        assert r["status"] == "waiting" and set(r["pending"]) == {"j1", "j2"}
        r = await runner.poll(r)
        r = await runner.poll(r)
        assert r["status"] == "needs_input" and r["needs_input"]["step"] == "j2"
        r = await runner.answer(r["id"], {"pick": "B"})
        return await drain(runner, r)

    r = asyncio.run(go())
    assert r["status"] == "completed", r["error"]
    assert {k: r["output"][k] for k in ("r1", "r2", "a")} == {"r1": "one done after 3", "r2": "two done after 3", "a": "B"}
    assert store.get_run(r["id"])["status"] == "completed"


def test_spend_gate_threshold():
    steps = [{"id": "hire", "limb": "job", "args": {"tag": "x", "credits": "{{inputs.q}}"}, "note": "expert"}]
    out = {"r": "{{steps.hire.result}}"}
    policy = {"max_credits": 500}
    runner = Runner(limb_for)

    async def go(credits, approve):
        r = await runner.start(make(steps, output=out, policy=policy), {"q": credits})
        if approve is None:
            return r
        assert r["status"] == "needs_confirmation" and r["confirm"]["credits"] == float(credits)
        r = await runner.confirm(r["id"], approve)
        return await drain(runner, r)

    assert asyncio.run(go("30", None))["status"] == "waiting"
    approved = asyncio.run(go("300", True))
    assert approved["status"] == "completed" and approved["output"]["r"] == "x done after 3"
    declined = asyncio.run(go("300", False))
    assert declined["status"] == "completed" and declined["output"]["r"] is None


def test_budget_stops_before_overspend():
    spec = make([{"id": "s", "limb": "spendy", "args": {"tokens": 5000}}], policy={"max_llm_tokens": 1000})
    r = run(spec, {})
    assert r["status"] == "failed" and "budget" in r["error"]


def test_memory_and_speech():
    spec = make(
        [{"id": "a", "limb": "echo", "args": {"prev": "{{memory.last}}", "now": "{{inputs.q}}"}}],
        output={"speech": "Was {{steps.a.prev}}, now {{steps.a.now}}.", "report": "r", "memory": {"last": "{{steps.a.now}}"}},
    )
    r = run(spec, {"q": "7"}, memory={"last": "5"})
    assert r["output"]["speech"] == "Was 5, now 7." and r["output"]["memory"] == {"last": "7"}
    assert "speech_audio_url" not in r["output"]


def test_clip_speech():
    text = "First sentence here. " * 80
    clipped = clip_speech(text, 100)
    assert len(clipped) <= 100 and clipped.endswith(".")
    assert clip_speech("  a   b ") == "a b"


def test_notify_goes_to_inbox():
    spec = make(
        [{"id": "n", "limb": "notify", "args": {"title": "Hi", "message": "{{inputs.q}}"}}],
        policy={"max_notifications": 1},
        output={"sent": "{{steps.n.sent}}"},
    )
    before = len(store.inbox_since(0))
    r = run(spec, {"q": "ping"})
    assert r["status"] == "completed", r["error"]
    assert r["output"]["sent"][0] == {"channel": "ui", "ok": True}
    assert store.inbox_since(0)[before:][0]["body"] == "ping"


def test_forged_limb_in_run():
    spec = make(
        [{"id": "f", "limb": "forged:upper", "args": {"s": "{{inputs.q}}"}}],
        forged_limbs=[{"name": "upper", "code": "def run(s):\n    return {'s': s.upper()}", "tests": [{"args": {"s": "a"}, "expect": {"s": "A"}}]}],
        output={"s": "{{steps.f.s}}"},
    )
    assert run(spec, {"q": "grr"})["output"]["s"] == "GRR"


def test_input_checks():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}, "s": {"type": "string", "enum": ["a"]}}, "required": ["n"]}
    assert check_inputs(schema, {"n": "4"}) == {"n": 4}
    for bad in ({}, {"n": 1, "zzz": 1}, {"n": "x"}, {"n": 1, "s": "b"}, {"n": True}):
        with pytest.raises(InputError):
            check_inputs(schema, bad)


def test_done_events_carry_per_step_usage():
    spec = make(
        [{"id": "g", "parallel": [{"id": "a", "limb": "spendy", "args": {"tokens": 300}},
                                  {"id": "b", "limb": "spendy", "args": {"tokens": 50}}]},
         {"id": "c", "limb": "echo", "args": {"x": 1}}],
        policy={"max_llm_tokens": 1000},
    )
    r = run(spec, {})
    used = {e["step"]: e["usage"]["llm_tokens"] for e in r["log"] if e["event"] == "done"}
    assert used == {"a": 300, "b": 50, "c": 0}


def test_speech_rewrite_never_blocks_tts(monkeypatch):
    from frankenstein import safety, speech
    from frankenstein.limbs import REGISTRY

    async def fake_speakable(text, report=""):
        return "Rewritten.", Cost(llm_tokens=300, usd=0.0001)

    class FakeTts(Limb):
        name = "tts"

        async def run(self, args, ctx):
            ctx.charge(Cost(tts_chars=len(args["text"])))
            return {"url": "http://x/speech.mp3"}

    monkeypatch.setattr(speech, "speakable", fake_speakable)
    monkeypatch.setattr(safety, "available", lambda: True)

    async def no_flags(text):
        return []

    monkeypatch.setattr(safety, "moderate", no_flags)
    monkeypatch.setitem(REGISTRY, "tts", FakeTts())
    spec = make([{"id": "a", "limb": "echo", "args": {"x": 1}}], output={"speech": "Raw https://x.com", "report": "r"},
                policy={"max_llm_tokens": 0, "max_tts_chars": 2000})
    r = run(spec, {})
    assert r["output"]["speech"] == "Rewritten." and r["output"]["speech_audio_url"] == "http://x/speech.mp3"
    assert r["usage"]["llm_tokens"] == 300


def test_sokosumi_payment_pending_with_result_counts_as_done(monkeypatch):
    from frankenstein import sokosumi
    from frankenstein.limbs.sokosumi_job import SokosumiLimb

    async def fake_get_job(job_id):
        return {"id": job_id, "status": "payment_pending", "credits": 60, "result": "**Done**", "completedAt": "2026-10-09T01:57:41Z"}

    monkeypatch.setattr(sokosumi, "get_job", fake_get_job)
    spec = make([{"id": "a", "limb": "echo", "args": {}}], policy={"max_credits": 100})
    from frankenstein.limbs import RunContext
    out = asyncio.run(SokosumiLimb().poll({"job_id": "j1", "agent_id": "x"}, RunContext("r", "a", spec)))
    assert isinstance(out, dict) and out["result"] == "**Done**"
