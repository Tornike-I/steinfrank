from .. import config
from ..limbs import get_limb
from ..limbs.forged import run_tests
from ..refs import RefError, template_refs, when_refs
from ..spec import MonsterSpec
from .estimator import estimate

REQUIRED_OUTPUTS = ("speech", "report")


class _Scope:
    def __init__(self, inputs: set[str]):
        self.inputs = inputs
        self.fields: dict[str, set[str] | None] = {}
        self.groups: dict[str, set[str]] = {}

    def check(self, ref: str, where: str, loop_var: str | None = None) -> str | None:
        parts = ref.split(".")
        root = parts[0]
        if root == "memory" or (loop_var and root == loop_var):
            return None
        if root == "run":
            return None if parts[1:] in (["now"], ["today"], ["timestamp"]) else f"{where}: {{{{{ref}}}}}: run has now, today, timestamp"
        if root == "inputs":
            if len(parts) < 2 or parts[1] not in self.inputs:
                return f"{where}: {{{{{ref}}}}} is not a declared input"
            return None
        if root == "steps":
            if len(parts) < 2 or parts[1] not in self.fields:
                return f"{where}: {{{{{ref}}}}} does not refer to an earlier step"
            known = self.fields[parts[1]]
            if parts[1] in self.groups and len(parts) > 2:
                if parts[2] not in self.groups[parts[1]]:
                    return f"{where}: group {parts[1]} has no member {parts[2]!r}"
                return self.check(".".join(["steps", *parts[2:]]), where)
            if len(parts) > 2 and known is not None and parts[2] not in known:
                return f"{where}: step {parts[1]} has no field {parts[2]!r} (it returns {sorted(known)})"
            return None
        extra = f", {loop_var}." if loop_var else ""
        return f"{where}: {{{{{ref}}}}} must start with inputs., steps., memory., run.{extra}"


def _schema_errors(args: dict, schema: dict, where: str) -> list[str]:
    props = schema.get("properties", {})
    errs = [f"{where}: unknown arg {k!r}" for k in args if k not in props]
    errs += [f"{where}: missing arg {k!r}" for k in schema.get("required", []) if k not in args]
    return errs


def _sokosumi_errors(args: dict, agents: dict, where: str) -> list[str]:
    agent = agents.get(args.get("agent_id"))
    if agent is None:
        return [f"{where}: unknown Sokosumi agent_id {args.get('agent_id')!r}"]
    errs = []
    fields = {f["id"]: f for f in agent["fields"]}
    inputs = args.get("inputs") or {}
    if not isinstance(inputs, dict):
        return [f"{where}: inputs must be an object"]
    errs += [f"{where}: {k!r} is not an input field of {agent['name']}" for k in inputs if k not in fields]
    errs += [
        f"{where}: required field {fid!r} of {agent['name']} is missing"
        for fid, f in fields.items()
        if not f["optional"] and f["type"] != "hidden" and fid not in inputs
    ]
    errs += [f"{where}: file field {fid!r} is unsupported" for fid, f in fields.items() if f["type"] == "file" and fid in inputs]
    try:
        cap = float(args.get("max_credits", 0))
    except (TypeError, ValueError):
        return errs + [f"{where}: max_credits must be a number"]
    if cap < agent["credits"]:
        errs.append(f"{where}: max_credits {cap} is below the agent price {agent['credits']}")
    return errs


def _policy_errors(spec: MonsterSpec) -> list[str]:
    caps, p = config.CAPS, spec.policy
    errs = []
    for key in ("max_credits", "max_llm_tokens", "max_steps", "max_images", "max_tts_chars", "max_notifications"):
        if getattr(p, key) > caps[key]:
            errs.append(f"policy.{key} {getattr(p, key)} exceeds the global cap {caps[key]}")
    return errs


def _io_errors(spec: MonsterSpec) -> list[str]:
    from ..runtime import InputError, check_inputs

    errs = []
    if spec.inputs.get("type") != "object":
        errs.append("inputs must be a JSON schema of type object")
    for name, prop in (spec.inputs.get("properties") or {}).items():
        if not prop.get("description"):
            errs.append(f"input {name!r} needs a description (the voice agent asks for it)")
    for key in REQUIRED_OUTPUTS:
        if not isinstance(spec.output.get(key), str):
            errs.append(f"output.{key} is required and must be a string template")
    for i, ex in enumerate(spec.examples):
        try:
            check_inputs(spec.inputs, ex)
        except InputError as e:
            errs.append(f"examples[{i}]: {e}")
    return errs


async def validate(spec: MonsterSpec, agents: dict[str, dict] | None = None) -> list[str]:
    errs = _policy_errors(spec) + _io_errors(spec)
    caps = config.CAPS
    scope = _Scope(set((spec.inputs.get("properties") or {}).keys()))
    if len(spec.leaves()) > min(spec.policy.max_steps, caps["max_steps"]):
        errs.append("more steps than policy.max_steps")
    seen: set[str] = set()

    for group in spec.steps:
        if group.parallel is not None:
            if len(group.parallel) < 2:
                errs.append(f"group {group.id}: parallel needs at least 2 steps")
            if group.when or group.for_each or group.args:
                errs.append(f"group {group.id}: put when/for_each/args on the parallel members, not the group")
            if any(m.parallel is not None for m in group.parallel):
                errs.append(f"group {group.id}: parallel groups cannot be nested")
        new_fields = {}
        for step in group.leaves():
            if step.parallel is not None:
                continue
            where = f"step {step.id}"
            if step.id in seen:
                errs.append(f"{where}: duplicate step id")
            seen.add(step.id)
            limb = get_limb(step.limb, spec)
            if limb is None:
                errs.append(f"{where}: unknown limb {step.limb!r}")
            elif not limb.enabled():
                errs.append(f"{where}: limb {step.limb!r} is disabled (missing {', '.join(limb.requires)})")
            loop_var = step.for_each.as_ if step.for_each else None
            if step.for_each:
                if step.for_each.max > caps["max_fanout"]:
                    errs.append(f"{where}: for_each.max above {caps['max_fanout']}")
                if limb is not None and limb.is_async_job:
                    errs.append(f"{where}: async-job limbs cannot be used in for_each")
                e = scope.check(step.for_each.over, f"{where} for_each.over")
                errs += [e] if e else []
            for ref in template_refs(step.args):
                e = scope.check(ref, where, loop_var)
                errs += [e] if e else []
            if step.when:
                try:
                    for ref in when_refs(step.when):
                        e = scope.check(ref, f"{where} when")
                        errs += [e] if e else []
                except RefError as e:
                    errs.append(f"{where} when: {e}")
            if limb is not None and not step.limb.startswith("forged:"):
                errs += _schema_errors(step.args, limb.args_schema, where)
                errs += [f"{where}: {e}" for e in limb.validate_args(step.args, spec)]
            if step.limb == "sokosumi" and agents is not None:
                errs += _sokosumi_errors(step.args, agents, where)
            if limb is None:
                new_fields[step.id] = None
            else:
                new_fields[step.id] = {"items"} if step.for_each else limb.output_fields(step.args)
        scope.fields.update(new_fields)
        if group.parallel is not None:
            scope.fields[group.id] = set(new_fields)
            scope.groups[group.id] = set(new_fields)

    for ref in template_refs(spec.output):
        e = scope.check(ref, "output")
        errs += [e] if e else []

    notifies = sum(s.for_each.max if s.for_each else 1 for s in spec.leaves() if s.limb == "notify")
    if notifies > spec.policy.max_notifications:
        errs.append(f"{notifies} possible notifications exceed policy.max_notifications {spec.policy.max_notifications}")

    used = {s.limb.split(":", 1)[1] for s in spec.leaves() if s.limb.startswith("forged:")}
    for f in spec.forged_limbs:
        if f.name in used:
            errs += await run_tests(f)

    est = estimate(spec).max
    p = spec.policy
    for name, used_, cap in (("llm tokens", est.llm_tokens, p.max_llm_tokens), ("credits", est.credits, p.max_credits),
                             ("images", est.images, p.max_images), ("tts chars", est.tts_chars, p.max_tts_chars)):
        if used_ > cap:
            errs.append(f"worst-case {name} {used_} exceed the policy limit {cap}")
    return errs
