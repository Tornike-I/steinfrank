"""Вспомогательные вещи для тестов: «сценарный» LLM и сборка компонентов агента."""
import json
import tempfile
from pathlib import Path

from app.builder import Builder
from app.db import Database
from app.events import EventBus
from app.llm import LLM
from app.registry import Registry
from app.sandbox import Sandbox
from app.usage import Ledger


class ScriptedLLM(LLM):
    """Вместо настоящей модели отдаёт заранее заданные ответы по порядку.
    Нужен ТОЛЬКО для проверки инфраструктуры (песочница, ремонт, реестр). В приложении не используется."""

    def __init__(self, replies):
        super().__init__()
        self.replies = list(replies)
        self.prompts = []

    def configured(self):
        return True

    def _chat(self, messages, json_mode):
        self.prompts.append(messages[-1]["content"])
        if not self.replies:
            raise AssertionError("ScriptedLLM: unexpected extra LLM call")
        reply = self.replies.pop(0)
        return reply(messages[-1]["content"]) if callable(reply) else reply     # ответ может зависеть от запроса


def make_parts(replies):
    db = Database(Path(tempfile.mkdtemp()) / "t.db")
    bus = EventBus(db)
    reg = Registry(db)
    llm = ScriptedLLM(replies)
    llm.ledger = Ledger(db)
    return db, bus, reg, llm, Builder(llm, Sandbox(), reg, bus)


def files(code, tests, readme="doc", name=None):
    """Ответ модели с файлами одного органа (name=None — без папки, для одиночной сборки)."""
    d = f"{name}/" if name else ""
    return (f"=====FILE: {d}capability.py=====\n{code}\n=====FILE: {d}test_capability.py=====\n{tests}\n"
            f"=====FILE: {d}README.md=====\n{readme}\n=====END=====")


def batch(*items):
    """Ответ модели с несколькими органами: batch((name, code, tests), ...)."""
    body = "".join(f"=====FILE: {n}/capability.py=====\n{c}\n=====FILE: {n}/test_capability.py=====\n{t}\n" for n, c, t in items)
    return body + "=====END====="


def repair(name, code, tests, diagnosis="splits on commas", with_tests=True):
    """Ответ модели на ремонт: диагноз + новые файлы в одном сообщении."""
    d = json.dumps({"diagnosis": {"en": diagnosis, "cs": "dělí podle čárek"},
                    "strategy": {"en": "split on whitespace", "cs": "dělit podle mezer"}, "fix_target": "code"})
    out = f"=====DIAGNOSIS: {name}=====\n{d}\n=====FILE: {name}/capability.py=====\n{code}\n"
    if with_tests:
        out += f"=====FILE: {name}/test_capability.py=====\n{tests}\n"
    return out + "=====END====="


def many_tests(n=9, body="self.assertEqual(capability.run({'text': 'a b c'})['words'], 3)"):
    methods = "\n".join(f"    def test_case_{i}(self):\n        {body}" for i in range(n))
    return f"import unittest\nimport capability\n\nclass T(unittest.TestCase):\n{methods}\n"
