"""Delivery channels. Recipients are registered destinations, never chosen by a monster spec or run input."""
import os
import re
import uuid
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from . import config, store


class ChannelError(RuntimeError):
    pass


@dataclass
class Message:
    title: str
    body: str
    url: str | None = None
    report: str | None = None
    audio_url: str | None = None
    priority: str = "default"
    monster_id: str | None = None
    run_id: str | None = None


def _https_public(url: str, host_suffix: str | None = None) -> str | None:
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        return "must be an https URL"
    if host_suffix and not (u.hostname == host_suffix or u.hostname.endswith("." + host_suffix)):
        return f"host must be {host_suffix}"
    return None


async def _post(url: str, **kw) -> httpx.Response:
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.post(url, **kw)
    if r.status_code >= 400:
        raise ChannelError(f"{urlparse(url).hostname} -> {r.status_code}: {r.text[:300]}")
    return r


class Channel:
    name = ""
    description = ""
    requires: tuple[str, ...] = ()
    target_fields: dict[str, str] = {}

    def enabled(self) -> bool:
        return all(os.environ.get(k) for k in self.requires)

    def check_target(self, target: dict) -> str | None:
        missing = [k for k in self.target_fields if not target.get(k)]
        return f"missing {', '.join(missing)}" if missing else None

    async def send(self, target: dict, msg: Message):
        raise NotImplementedError

    def describe(self) -> dict:
        return {"name": self.name, "description": self.description, "enabled": self.enabled(),
                "requires": list(self.requires), "target_fields": self.target_fields}


class UiChannel(Channel):
    name = "ui"
    description = "The website inbox (always on)."

    async def send(self, target, msg):
        store.add_inbox(msg.__dict__)


class NtfyChannel(Channel):
    name = "ntfy"
    description = "Push notification via ntfy (phone/desktop app). No key needed on ntfy.sh."
    target_fields = {"topic": "ntfy topic name; pick something hard to guess"}

    def check_target(self, target):
        err = super().check_target(target)
        if err:
            return err
        return None if re.fullmatch(r"[A-Za-z0-9_-]{6,64}", target["topic"]) else "topic must be 6-64 letters, digits, - or _"

    async def send(self, target, msg):
        headers = {"Title": msg.title.encode("utf-8").decode("latin-1", "ignore"), "Priority": msg.priority}
        if msg.url or msg.audio_url:
            headers["Click"] = msg.url or msg.audio_url
        token = os.environ.get("NTFY_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        await _post(f"{config.NTFY_BASE_URL}/{target['topic']}", content=msg.body.encode("utf-8"), headers=headers)


class EmailChannel(Channel):
    name = "email"
    description = "Email via Resend, includes the full report."
    requires = ("RESEND_API_KEY",)
    target_fields = {"to": "email address"}

    def check_target(self, target):
        err = super().check_target(target)
        return err or (None if re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", target["to"]) else "invalid email address")

    async def send(self, target, msg):
        text = msg.body + (f"\n\n{msg.report}" if msg.report else "") + (f"\n\n{msg.url}" if msg.url else "")
        await _post(
            "https://api.resend.com/emails",
            json={"from": config.RESEND_FROM, "to": [target["to"]], "subject": msg.title, "text": text},
            headers={"Authorization": f"Bearer {os.environ['RESEND_API_KEY']}"},
        )


class SlackChannel(Channel):
    name = "slack"
    description = "Slack incoming webhook."
    target_fields = {"webhook_url": "https://hooks.slack.com/services/..."}

    def check_target(self, target):
        return super().check_target(target) or _https_public(target["webhook_url"], "hooks.slack.com")

    async def send(self, target, msg):
        text = f"*{msg.title}*\n{msg.body}" + (f"\n<{msg.url}|Open>" if msg.url else "")
        await _post(target["webhook_url"], json={"text": text})


class TelegramChannel(Channel):
    name = "telegram"
    description = "Telegram message from the Steinfrank bot."
    requires = ("TELEGRAM_BOT_TOKEN",)
    target_fields = {"chat_id": "numeric chat id (the user must /start the bot first)"}

    async def send(self, target, msg):
        text = f"{msg.title}\n\n{msg.body}" + (f"\n{msg.url}" if msg.url else "")
        await _post(
            f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN']}/sendMessage",
            json={"chat_id": target["chat_id"], "text": text[:4000]},
        )


class WebhookChannel(Channel):
    name = "webhook"
    description = "POST the message as JSON to any public https URL (Zapier, Make, n8n, your own service)."
    target_fields = {"url": "https endpoint"}

    def check_target(self, target):
        return super().check_target(target) or _https_public(target["url"])

    async def send(self, target, msg):
        from .limbs.http_fetch import assert_public

        await assert_public(urlparse(target["url"]).hostname)
        headers = {"X-Steinfrank-Secret": target["secret"]} if target.get("secret") else {}
        await _post(target["url"], json=msg.__dict__, headers=headers)


CHANNELS: dict[str, Channel] = {c.name: c for c in (
    UiChannel(), NtfyChannel(), EmailChannel(), SlackChannel(), TelegramChannel(), WebhookChannel()
)}


def create_destination(channel: str, target: dict, label: str = "") -> dict:
    ch = CHANNELS.get(channel)
    if ch is None:
        raise ChannelError(f"unknown channel {channel!r}")
    err = ch.check_target(target)
    if err:
        raise ChannelError(f"{channel}: {err}")
    dest = {"id": uuid.uuid4().hex[:10], "channel": channel, "target": target, "label": label or channel}
    store.save_destination(dest)
    return dest


def public_destination(dest: dict) -> dict:
    """Webhook URLs and chat ids act as credentials, so the API never echoes them back."""
    return {"id": dest["id"], "channel": dest["channel"], "label": dest["label"]}


async def deliver(dest_ids: list[str], msg: Message) -> list[dict]:
    """Always posts to the UI inbox; failures on other channels are reported, not raised."""
    results = []
    await CHANNELS["ui"].send({}, msg)
    results.append({"channel": "ui", "ok": True})
    for dest_id in dict.fromkeys(dest_ids):
        dest = store.get_destination(dest_id)
        if dest is None:
            results.append({"destination": dest_id, "ok": False, "error": "unknown destination"})
            continue
        ch = CHANNELS[dest["channel"]]
        if not ch.enabled():
            results.append({"destination": dest_id, "ok": False, "error": f"{ch.name} disabled (set {', '.join(ch.requires)})"})
            continue
        try:
            await ch.send(dest["target"], msg)
            results.append({"destination": dest_id, "channel": ch.name, "ok": True})
        except (ChannelError, httpx.HTTPError, RuntimeError) as e:
            results.append({"destination": dest_id, "channel": ch.name, "ok": False, "error": str(e)})
    return results
