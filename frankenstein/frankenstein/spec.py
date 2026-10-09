from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STEP_ID = r"^[a-z][a-z0-9_]*$"


class Policy(BaseModel):
    allowed_domains: list[str] = []
    max_credits: int = 0
    max_llm_tokens: int = 4000
    max_steps: int = 20
    max_images: int = 0
    max_tts_chars: int = 1200
    max_notifications: int = 0


class ForEach(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    over: str
    max: int = Field(ge=1)
    as_: str = Field("item", alias="as", pattern=STEP_ID)


class Step(BaseModel):
    id: str = Field(pattern=STEP_ID)
    limb: str | None = None
    args: dict[str, Any] = {}
    when: str | None = None
    for_each: ForEach | None = None
    parallel: list["Step"] | None = None
    note: str = ""

    @model_validator(mode="after")
    def _limb_xor_parallel(self):
        if (self.limb is None) == (self.parallel is None):
            raise ValueError("a step needs exactly one of 'limb' or 'parallel'")
        return self

    def leaves(self) -> list["Step"]:
        return list(self.parallel) if self.parallel is not None else [self]


class ForgedTest(BaseModel):
    args: dict[str, Any]
    expect: dict[str, Any] = {}
    contains: dict[str, str | list[str]] = {}


class ForgedLimb(BaseModel):
    name: str = Field(pattern=STEP_ID)
    description: str = ""
    code: str
    tests: list[ForgedTest] = Field(min_length=1)


class Usage(BaseModel):
    llm_tokens: int = 0
    credits: float = 0
    seconds: float = 0
    images: int = 0
    tts_chars: int = 0
    usd: float = 0


class Estimate(BaseModel):
    min: Usage = Usage()
    max: Usage = Usage()


class Safety(BaseModel):
    verdict: Literal["allow", "refuse", "unchecked"] = "unchecked"
    notes: str = ""


class CastMember(BaseModel):
    label: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    archetype: str
    role: str


class Sounds(BaseModel):
    arrive: str | None = None
    working: str | None = None
    done: str | None = None


class Voice(BaseModel):
    elevenlabs_agent_id: str | None = None
    first_message: str = ""
    archetype: str = "brute"
    language: Literal["en", "cs"] = "en"
    cast: list[CastMember] = Field([], max_length=2)
    voice_id: str | None = None
    sounds: Sounds = Sounds()
    birth_url: str | None = None


class MonsterSpec(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    version: int = 1
    purpose: str
    persona: str = ""
    inputs: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
    policy: Policy = Policy()
    steps: list[Step] = Field(min_length=1)
    output: dict[str, Any] = {}
    speak: bool = True
    forged_limbs: list[ForgedLimb] = []
    examples: list[dict[str, Any]] = []
    estimate: Estimate = Estimate()
    safety: Safety = Safety()
    voice: Voice = Voice()

    def forged(self, name: str) -> ForgedLimb | None:
        return next((f for f in self.forged_limbs if f.name == name), None)

    def leaves(self) -> list[Step]:
        return [leaf for step in self.steps for leaf in step.leaves()]

    def dump(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)
