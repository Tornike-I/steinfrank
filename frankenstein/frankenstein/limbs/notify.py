from .. import channels
from .base import Limb, LimbError


class NotifyLimb(Limb):
    name = "notify"
    description = (
        "Send a short message to the people who started this run or watch (UI inbox plus their chosen channels: "
        "push, email, Slack, Telegram, webhook). You cannot choose recipients. Use with 'when' so watchers only "
        "notify on a real change. Needs policy.max_notifications. Returns {sent}."
    )
    args_schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Short headline, <= 80 chars"},
            "message": {"type": "string", "description": "Body, <= 1000 chars"},
            "url": {"type": "string", "description": "Optional link"},
            "priority": {"type": "string", "enum": ["low", "default", "high"]},
        },
        "required": ["title", "message"],
    }
    seconds = 1.0
    outputs = {"sent"}
    retryable = False

    def validate_args(self, args, spec):
        return [] if spec.policy.max_notifications >= 1 else ["notify needs policy.max_notifications >= 1"]

    async def run(self, args, ctx):
        if ctx.notifications >= ctx.policy.max_notifications:
            raise LimbError("policy.max_notifications reached")
        ctx.notifications += 1
        msg = channels.Message(
            title=str(args["title"])[:80],
            body=str(args["message"])[:1000],
            url=args.get("url") or None,
            priority=args.get("priority") or "default",
            monster_id=ctx.spec.id,
            run_id=ctx.run_id,
        )
        return {"sent": await channels.deliver(ctx.deliver_to, msg)}
