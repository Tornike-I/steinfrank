import time

from .. import config, sokosumi, store
from .base import Cost, Limb, LimbError, NeedsInput, Pending

FAILED = {"failed", "payment_failed", "refund_resolved", "dispute_resolved"}


class SokosumiLimb(Limb):
    name = "sokosumi"
    description = (
        "Hire a paid agent from the Sokosumi marketplace (the monster's extra hands). Runs async for minutes "
        "and costs credits. args.inputs keys must be the agent's input field ids. Returns {result, job_id, credits}; "
        "result is usually markdown text. Use only when no cheaper limb can do the job."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "agent_id": {"type": "string"},
            "inputs": {"type": "object", "description": "Field id -> value, per the agent's input schema"},
            "max_credits": {"type": "number", "description": "Spend cap for this job, >= the agent's price"},
        },
        "required": ["agent_id", "inputs", "max_credits"],
    }
    requires = ("SOKOSUMI_API_KEY",)
    is_async_job = True
    retryable = False
    seconds = 180.0
    outputs = {"result", "job_id", "credits", "files"}

    def estimate(self, args):
        return Cost(credits=float(args.get("max_credits") or 0), seconds=self.seconds)

    async def run(self, args, ctx):
        cap = float(args["max_credits"])
        if cap + ctx.usage.credits > ctx.policy.max_credits:
            raise LimbError("job max_credits would exceed policy.max_credits")
        if store.credits_reserved_since(time.time() - 86400) + cap > config.CAPS["daily_credits"]:
            raise LimbError(f"daily credit cap of {config.CAPS['daily_credits']} reached")
        job = await sokosumi.create_job(
            args["agent_id"], args.get("inputs") or {}, float(args["max_credits"]), name=f"{ctx.spec.id}/{ctx.run_id}"
        )
        store.reserve_credits(ctx.run_id, ctx.step_id, cap)
        return Pending({"job_id": job["id"], "agent_id": args["agent_id"]})

    async def poll(self, state, ctx):
        job = await sokosumi.get_job(state["job_id"])
        status = job["status"]
        if status == "completed":
            ctx.charge(Cost(credits=float(job.get("credits") or 0)))
            try:
                files = [{"name": f.get("name"), "url": f.get("fileUrl"), "mime": f.get("mimeType")}
                         for f in await sokosumi.get_files(job["id"])]
            except sokosumi.SokosumiError:
                files = []
            return {"result": job.get("result") or "", "job_id": job["id"], "credits": job.get("credits"), "files": files}
        if status == "input_required":
            req = await sokosumi.get_input_request(state["job_id"])
            return NeedsInput(req.get("message") or "", req.get("inputSchema"), {**state, "event_id": req["id"]})
        if status in FAILED:
            raise LimbError(f"sokosumi job {job['id']} ended {status}")
        return Pending(state)

    async def answer(self, state, data, ctx):
        await sokosumi.provide_input(state["job_id"], state["event_id"], data)
        return Pending({k: v for k, v in state.items() if k != "event_id"})
