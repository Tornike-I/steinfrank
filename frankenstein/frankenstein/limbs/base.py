import os
from dataclasses import asdict, dataclass, field
from typing import Any

from ..spec import MonsterSpec, Policy


class LimbError(RuntimeError):
    pass


class BudgetExceeded(LimbError):
    pass


class TransientLimbError(LimbError):
    """A failure worth retrying (e.g. HTTP 503 or 429 from an overloaded service)."""


@dataclass
class Cost:
    llm_tokens: int = 0
    credits: float = 0
    seconds: float = 0
    images: int = 0
    tts_chars: int = 0
    usd: float = 0

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(**{k: getattr(self, k) + getattr(other, k) for k in asdict(self)})

    def __mul__(self, n: int) -> "Cost":
        return Cost(**{k: v * n for k, v in asdict(self).items()})

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Pending:
    """Returned by an async-job limb; the runtime persists `state` and polls later."""
    state: dict


@dataclass
class NeedsInput:
    message: str
    input_schema: Any
    state: dict


@dataclass
class RunContext:
    run_id: str
    step_id: str
    spec: MonsterSpec
    usage: Cost = field(default_factory=Cost)
    deliver_to: list[str] = field(default_factory=list)
    notifications: int = 0

    @property
    def policy(self) -> Policy:
        return self.spec.policy

    def charge(self, cost: Cost):
        self.usage = self.usage + cost
        self.check()

    def check(self, extra: Cost | None = None):
        p, u = self.policy, self.usage + (extra or Cost())
        over = [
            name
            for name, used, cap in (
                ("llm_tokens", u.llm_tokens, p.max_llm_tokens),
                ("credits", u.credits, p.max_credits),
                ("images", u.images, p.max_images),
                ("tts_chars", u.tts_chars, p.max_tts_chars),
            )
            if used > cap
        ]
        if over:
            raise BudgetExceeded(f"budget exceeded: {', '.join(over)}")


class Limb:
    name: str = ""
    description: str = ""
    args_schema: dict = {"type": "object", "properties": {}, "required": []}
    requires: tuple[str, ...] = ()
    is_async_job = False
    retryable = True
    seconds = 1.0
    outputs: set[str] | None = None

    def enabled(self) -> bool:
        return all(os.environ.get(k) for k in self.requires)

    def estimate(self, args: dict) -> Cost:
        return Cost(seconds=self.seconds)

    def output_fields(self, args: dict) -> set[str] | None:
        """None means unknown, so references into this step's output are not checked."""
        return self.outputs

    def precheck(self, args: dict) -> Cost:
        return self.estimate(args)

    def validate_args(self, args: dict, spec: MonsterSpec) -> list[str]:
        return []

    async def run(self, args: dict, ctx: RunContext) -> dict | Pending:
        raise NotImplementedError

    async def poll(self, state: dict, ctx: RunContext) -> dict | Pending | NeedsInput:
        raise NotImplementedError

    async def answer(self, state: dict, data: dict, ctx: RunContext) -> Pending:
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "args_schema": self.args_schema,
            "enabled": self.enabled(),
            "async_job": self.is_async_job,
        }
