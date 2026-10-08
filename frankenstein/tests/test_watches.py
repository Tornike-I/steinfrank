import asyncio
import time

import pytest

from frankenstein import channels, store, watches
from frankenstein.runtime import Runner
from frankenstein.spec import MonsterSpec

WATCHER = {
    "id": "counter-watch",
    "name": "Counter",
    "purpose": "notify when the value changes",
    "inputs": {"type": "object", "properties": {"value": {"type": "integer", "description": "current value"}}},
    "policy": {"max_notifications": 1},
    "steps": [
        {"id": "diff", "limb": "forged:diff", "args": {"now": "{{inputs.value}}", "last": "{{memory.last}}"}},
        {"id": "tell", "limb": "notify", "when": "steps.diff.changed == true",
         "args": {"title": "Changed", "message": "{{steps.diff.text}}"}},
    ],
    "output": {"speech": "{{steps.diff.text}}", "report": "", "memory": {"last": "{{steps.diff.now}}"}},
    "forged_limbs": [{
        "name": "diff",
        "code": "def run(now, last):\n    changed = last is not None and last != now\n"
                "    return {'now': now, 'changed': changed, 'text': f'from {last} to {now}'}\n",
        "tests": [{"args": {"now": 2, "last": 1}, "expect": {"changed": True}}],
    }],
}


def test_destination_validation():
    for channel, target in (("slack", {"webhook_url": "http://evil.example"}), ("ntfy", {"topic": "a b"}),
                            ("email", {"to": "nope"}), ("webhook", {"url": "ftp://x"}), ("teleport", {})):
        with pytest.raises(channels.ChannelError):
            channels.create_destination(channel, target)
    dest = channels.create_destination("slack", {"webhook_url": "https://hooks.slack.com/services/T/B/x"})
    assert "target" not in channels.public_destination(dest)


def test_disabled_channel_reported_not_raised():
    dest = channels.create_destination("email", {"to": "a@b.co"})
    res = asyncio.run(channels.deliver([dest["id"], "missing"], channels.Message(title="t", body="b")))
    assert res[0] == {"channel": "ui", "ok": True}
    assert "disabled" in res[1]["error"] and res[2]["error"] == "unknown destination"


def test_watch_memory_and_change_notification():
    store.save_monster(MonsterSpec.model_validate(WATCHER))
    with pytest.raises(watches.WatchError):
        watches.create_watch("counter-watch", {"value": 1}, 5, [])
    w = watches.create_watch("counter-watch", {"value": 1}, 60, [])
    runner = Runner()
    inbox_before = len(store.inbox_since(0))

    asyncio.run(watches.tick(runner, now=time.time()))
    w = store.get_watch(w["id"])
    first = store.get_run(w["last_run_id"])
    assert first["status"] == "completed" and w["memory"] == {"last": 1}
    assert first["steps"]["tell"] is None

    w["inputs"]["value"] = 3
    store.save_watch(w)
    assert asyncio.run(watches.tick(runner, now=time.time() + 30)) == []
    asyncio.run(watches.tick(runner, now=time.time() + 61))
    w = store.get_watch(w["id"])
    second = store.get_run(w["last_run_id"])
    assert second["steps"]["tell"]["sent"][0]["ok"] and w["memory"] == {"last": 3}
    assert store.inbox_since(0)[inbox_before:][-1]["body"] == "from 1 to 3"
