import difflib
import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field

from pydantic import ValidationError

from .. import config, safety, store, voices
from ..openai_client import client, sampling
from ..spec import MonsterSpec, Safety
from . import theatrics, voice
from .catalog import limb_catalog, sokosumi_agents
from .designer import Designer
from .estimator import estimate
from .validator import validate

log = logging.getLogger("frankenstein.forge")

REVIEW_PROMPT = """You review what a voice assistant will say aloud at the end of an automated task, from test runs.
Paid steps were mocked: their placeholder output stands in for a real result, so never flag the placeholder itself.
'declined' means the user refused the paid step. Flag only real problems:
- the speech claims something the run did not do (e.g. says it escalated or researched deeper when that step was
  skipped or declined), or contradicts the report;
- raw technical text spoken aloud: error messages, exception text, JSON, URLs, markdown, code;
- numbers without sensible units or rounding (e.g. "9947 days" instead of "about 27 years"), or awkward grammar;
- more than about 5 sentences, or the verdict is not first;
- the core data the job needs is missing or empty in the report (n/a, "no data returned", status 0, fetch errors),
  yet the speech presents a normal or confident result instead of saying the data could not be obtained;
- the speech does not actually answer (no concrete facts, numbers or verdict; only "found information", "data is
  available" or "see the report");
- a failed fetch or missing data is treated as evidence for the verdict (e.g. "site unreachable" as a scam signal
  when the URL itself was malformed).
Reply with JSON {"problems": ["<concrete problem and which case>", ...]} and an empty list if it is fine."""


@dataclass
class ForgeResult:
    status: str
    spec: dict | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    reason: str = ""
    design_tokens: int = 0

    def as_dict(self):
        return asdict(self)


def _pydantic_errors(e: ValidationError) -> list[str]:
    return [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]


async def _agents_by_id() -> dict[str, dict]:
    if not os.environ.get("SOKOSUMI_API_KEY"):
        return {}
    return {a["id"]: a for a in await sokosumi_agents()}


def fit_policy(spec: MonsterSpec):
    """Budgets follow from the steps, so they are computed rather than guessed; global caps still apply."""
    worst = estimate(spec).max
    p = spec.policy
    p.max_llm_tokens = max(p.max_llm_tokens, math.ceil(worst.llm_tokens * 1.3 / 100) * 100) if worst.llm_tokens else 0
    p.max_credits = max(p.max_credits, math.ceil(worst.credits))
    p.max_images = max(p.max_images, worst.images)
    p.max_tts_chars = max(p.max_tts_chars, worst.tts_chars)
    notifies = sum(s.for_each.max if s.for_each else 1 for s in spec.leaves() if s.limb == "notify")
    p.max_notifications = max(p.max_notifications, notifies)
    p.max_steps = max(p.max_steps, len(spec.leaves()))


def _fix_agent_ids(raw: dict, agents: dict[str, dict]):
    """The designer sometimes mistypes a letter of a long agent id; take the only near-identical real one."""
    if not agents:
        return
    for group in raw.get("steps") or []:
        for step in group.get("parallel") or [group]:
            args = step.get("args") or {}
            aid = args.get("agent_id")
            if step.get("limb") == "sokosumi" and isinstance(aid, str) and aid not in agents:
                close = difflib.get_close_matches(aid, list(agents), n=2, cutoff=0.9)
                if len(close) == 1:
                    args["agent_id"] = close[0]


async def check(raw: dict, agents: dict[str, dict]) -> tuple[MonsterSpec | None, list[str]]:
    _fix_agent_ids(raw, agents)
    try:
        spec = MonsterSpec.model_validate(raw)
    except ValidationError as e:
        return None, _pydantic_errors(e)
    fit_policy(spec)
    return spec, await validate(spec, agents)


async def dry_run(spec: MonsterSpec) -> list[str]:
    """Runs the free limbs for real on the designer's examples, with paid and outward-facing limbs mocked."""
    from ..runtime import Runner

    if not spec.examples:
        return ["add 1-2 realistic examples so the monster can be test-run"]
    errs, runs = [], []
    has_paid = any(s.limb == "sokosumi" for s in spec.leaves())
    for i, example in enumerate(spec.examples[:2]):
        for decline in (False, True) if has_paid else (False,):
            run = await Runner(dry_run=True, decline_paid=decline).start(spec, example)
            where = f"test run {i} with inputs {example}" + (" (user declined the paid step)" if decline else "")
            if run["status"] != "completed":
                errs.append(f"{where}: {run['status']}: {run['error']}")
                continue
            for key in ("speech", "report"):
                if not str((run["output"] or {}).get(key) or "").strip():
                    errs.append(f"{where}: output.{key} came out empty")
            runs.append((where, run))
    if not errs and runs:
        errs = await speech_review(spec, runs)
    return errs


async def speech_review(spec: MonsterSpec, runs: list[tuple[str, dict]]) -> list[str]:
    cases = []
    for where, run in runs:
        skipped = [e["step"] for e in run["log"] if e["event"] in ("skipped", "declined")]
        cases.append({"case": where, "skipped_or_declined_steps": skipped, "speech": run["output"]["speech"],
                      "report_start": str(run["output"]["report"])[:1500]})
    resp = await client().chat.completions.create(
        model=config.DESIGN_MODEL,
        messages=[{"role": "system", "content": REVIEW_PROMPT},
                  {"role": "user", "content": json.dumps({"purpose": spec.purpose, "runs": cases}, ensure_ascii=False)}],
        response_format={"type": "json_object"},
        **sampling(config.DESIGN_MODEL, 0),
    )
    problems = json.loads(resp.choices[0].message.content or "{}").get("problems") or []
    return [f"speech review: {p}" for p in problems]


async def forge(description: str, max_repairs: int = 4) -> ForgeResult:
    if not safety.available():
        raise RuntimeError("forging needs OPENAI_API_KEY")
    flagged = await safety.moderate(description)
    if flagged:
        return ForgeResult("refused", reason=f"request flagged: {', '.join(flagged)}")
    verdict, reason = await safety.policy_check(description)
    if verdict != "allow":
        return ForgeResult("refused", reason=reason)

    agents = await _agents_by_id()
    designer = Designer(limb_catalog(), list(agents.values()))
    raw = await designer.design(description)
    errors: list[str] = []
    warnings: list[str] = []
    best: tuple[MonsterSpec, list[str]] | None = None
    pushed_back = False
    for attempt in range(max_repairs + 1):
        if "refuse" in raw:
            # The safety screen already allowed this; the designer gets one push back before a refusal stands.
            if pushed_back:
                return ForgeResult("refused", reason=str(raw["refuse"]), design_tokens=designer.usage)
            pushed_back = True
            raw = await designer.repair(
                [f"You refused ({raw['refuse']}), but this request passed the dedicated safety review as an ordinary "
                 "task. Build the spec; refuse again only if it is clearly in a harmful category."], "the safety review")
            continue
        spec, errors = await check(raw, agents)
        stage = "validation"
        if not errors:
            errors, stage = await dry_run(spec), "the test run"
            if not errors:
                break
        log.info("design attempt %d failed %s: %s", attempt, stage, errors)
        if all(e.startswith("speech review:") for e in errors) and (best is None or len(errors) <= len(best[1])):
            best = (spec, errors)
        if attempt == max_repairs:
            if best is None:
                return ForgeResult("invalid", spec=raw, errors=errors, design_tokens=designer.usage)
            spec, warnings = best
            break
        raw = await designer.repair(errors, stage)

    spec.estimate = estimate(spec)
    spec.safety = Safety(verdict="allow", notes=reason)
    existing = store.load_monster(spec.id)
    if existing:
        spec.version = existing.version + 1
    store.save_draft(spec.dump())
    return ForgeResult("draft", spec=spec.dump(), warnings=warnings, design_tokens=designer.usage)


async def publish(raw: dict, with_voice: bool = True) -> MonsterSpec:
    spec, errors = await check(raw, await _agents_by_id())
    if errors:
        raise ValueError("; ".join(errors))
    spec.estimate = estimate(spec)
    if spec.safety.verdict == "unchecked":
        spec.safety = Safety(verdict="allow", notes="hand-written monster")
    if spec.safety.verdict != "allow":
        raise ValueError("monster did not pass the safety check")
    if with_voice:
        await dress(spec, store.load_monster(spec.id))
    store.save_monster(spec)
    return spec


async def dress(spec: MonsterSpec, previous: MonsterSpec | None):
    """A monster keeps its sounds and birth scene across re-publishes unless its archetype changed."""
    v = spec.voice
    v.voice_id = voices.pick_voice(v.archetype, spec.id)
    same = previous is not None and previous.voice.archetype == v.archetype
    if same:
        v.sounds, v.birth_url = previous.voice.sounds, previous.voice.birth_url
    if not v.sounds.arrive:
        v.sounds = await theatrics.make_sounds(spec)
    if not v.birth_url:
        try:
            v.birth_url = await theatrics.make_birth(spec)
        except Exception as e:
            log.warning("birth scene for %s failed: %s", spec.id, e)
    v.elevenlabs_agent_id = await voice.create_agent(spec, previous.voice.elevenlabs_agent_id if previous else None)
