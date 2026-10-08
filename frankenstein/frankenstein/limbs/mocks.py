"""Stand-ins for paid or outward-facing limbs during the forge-time dry run."""
from .base import Limb


class Mock(Limb):
    def __init__(self, real: Limb):
        self.real = real
        self.name = real.name
        self.outputs = real.outputs
        self.is_async_job = False

    def estimate(self, args):
        return self.real.estimate(args)

    def precheck(self, args):
        return self.real.precheck(args)

    def output_fields(self, args):
        return self.real.output_fields(args)

    async def run(self, args, ctx):
        if self.name == "sokosumi":
            return {"result": "# Dry-run report\nThis is placeholder output from a paid agent.", "job_id": "dry-run",
                    "credits": 0, "files": []}
        if self.name == "notify":
            return {"sent": [{"channel": "dry-run", "ok": True}]}
        return {"file": "", "url": ""}


MOCKED = {"sokosumi", "notify", "tts", "sfx", "image"}
