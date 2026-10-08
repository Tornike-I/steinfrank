import asyncio

import pytest

from frankenstein.limbs.forged import check_code, execute, run_tests
from frankenstein.limbs.base import LimbError
from frankenstein.spec import ForgedLimb

GOOD = """
import re
def run(text):
    words = re.findall(r"[a-z']+", text.lower())
    return {"count": len(words), "first": words[0] if words else None}
"""


def test_good_code_runs():
    assert asyncio.run(execute(GOOD, {"text": "Hello big World"})) == {"count": 3, "first": "hello"}


@pytest.mark.parametrize(
    "code",
    [
        "import os\ndef run(): return {}",
        "from subprocess import run as r\ndef run(): return {}",
        "def run(): return {'x': open('f').read()}",
        "def run(): return {'x': ().__class__.__subclasses__()}",
        "def run(): return {'x': '{0.__class__}'.format(1)}",
        "def run(): return {'x': getattr(1, 'real')}",
        "def run(): return {'x': eval('1')}",
        "def go(): return {}",
    ],
)
def test_static_rejections(code):
    assert check_code(code)


def test_non_ascii_roundtrip():
    code = "def run(s):\n    return {'s': s + ' → ok'}"
    assert asyncio.run(execute(code, {"s": "Ünïcødé"})) == {"s": "Ünïcødé → ok"}


def test_allowed_import():
    code = "import json\ndef run():\n    return {'m': str(json.loads('1'))}"
    assert asyncio.run(execute(code, {})) == {"m": "1"}


def test_timeout():
    with pytest.raises(LimbError, match="timed out"):
        asyncio.run(execute("def run():\n    while True:\n        pass", {}, timeout=1))


def test_non_dict_and_exception():
    with pytest.raises(LimbError, match="must return a dict"):
        asyncio.run(execute("def run(): return 5", {}))
    with pytest.raises(LimbError, match="ZeroDivisionError"):
        asyncio.run(execute("def run(): return {'x': 1/0}", {}))


def test_run_tests_reports_mismatch():
    f = ForgedLimb(name="wc", code=GOOD, tests=[{"args": {"text": "a b"}, "expect": {"count": 3}}])
    errs = asyncio.run(run_tests(f))
    assert errs and "expected/got" in errs[0]
