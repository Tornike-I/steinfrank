from ..spec import MonsterSpec
from .base import BudgetExceeded, Cost, Limb, LimbError, NeedsInput, Pending, RunContext, TransientLimbError
from .forged import ForgedLimbRunner
from .http_fetch import HttpFetchLimb
from .llm import LlmLimb
from .media import ImageLimb, SfxLimb, TtsLimb, WebSearchLimb
from .mocks import MOCKED, Mock
from .notify import NotifyLimb
from .sokosumi_job import SokosumiLimb

REGISTRY: dict[str, Limb] = {
    limb.name: limb
    for limb in (LlmLimb(), HttpFetchLimb(), WebSearchLimb(), TtsLimb(), SfxLimb(), ImageLimb(), SokosumiLimb(), NotifyLimb())
}


def get_limb(name: str, spec: MonsterSpec) -> Limb | None:
    if name.startswith("forged:"):
        forged = spec.forged(name.split(":", 1)[1])
        return ForgedLimbRunner(forged) if forged else None
    return REGISTRY.get(name)


def dry_run_limb(name: str, spec: MonsterSpec) -> Limb | None:
    limb = get_limb(name, spec)
    return Mock(limb) if limb is not None and name in MOCKED else limb


__all__ = [
    "REGISTRY", "get_limb", "dry_run_limb", "Limb", "LimbError", "BudgetExceeded", "Cost", "NeedsInput", "Pending", "RunContext",
]
