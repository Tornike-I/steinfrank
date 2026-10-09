from .. import config, speech
from ..limbs import REGISTRY, Cost, get_limb
from ..spec import Estimate, MonsterSpec, Usage

MIN_SPEECH_CHARS = 100


def _usage(c: Cost) -> Usage:
    return Usage(**{k: (round(v, 6 if k == "usd" else 1) if isinstance(v, float) else v) for k, v in c.as_dict().items()})


def _group(costs: list[Cost], parallel: bool) -> Cost:
    total = Cost()
    for c in costs:
        total = total + c
    if parallel and costs:
        total.seconds = max(c.seconds for c in costs)
    return total


def estimate(spec: MonsterSpec, voice: bool = True) -> Estimate:
    """max is the worst case; min assumes every `when` step is skipped and every for_each list is empty.
    voice=False leaves out the spoken verdict (rewrite + TTS), which is shown separately."""
    lo, hi = Cost(), Cost()
    for group in spec.steps:
        lows, highs = [], []
        for leaf in group.leaves():
            limb = get_limb(leaf.limb, spec)
            if limb is None:
                continue
            one = limb.estimate(leaf.args)
            highs.append(one * (leaf.for_each.max if leaf.for_each else 1))
            lows.append(Cost() if leaf.when or leaf.for_each else one)
        parallel = group.parallel is not None
        lo, hi = lo + _group(lows, parallel), hi + _group(highs, parallel)
    if spec.speak and voice:
        tts = REGISTRY["tts"]
        lo = lo + tts.estimate({"text": "x" * MIN_SPEECH_CHARS, "model_id": config.SPEECH_TTS_MODEL})
        hi = hi + tts.estimate({"text": "x" * config.SPEECH_MAX_CHARS, "model_id": config.SPEECH_TTS_MODEL})
        rewrite = speech.estimate()
        lo, hi = lo + rewrite, hi + rewrite
    return Estimate(min=_usage(lo), max=_usage(hi))


def step_estimates(spec: MonsterSpec) -> dict[str, dict]:
    """Worst case per leaf step, for showing next to each workflow box."""
    out = {}
    for leaf in spec.leaves():
        limb = get_limb(leaf.limb, spec)
        if limb is not None:
            out[leaf.id] = _usage(limb.estimate(leaf.args) * (leaf.for_each.max if leaf.for_each else 1)).model_dump()
    if spec.speak:
        out["speech"] = _usage(speech.estimate() + REGISTRY["tts"].estimate({"text": "x" * config.SPEECH_MAX_CHARS, "model_id": config.SPEECH_TTS_MODEL})).model_dump()
    return out
