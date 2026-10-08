import ast
import asyncio
import json
import sys
from pathlib import Path

from ..spec import ForgedLimb
from .base import Limb, LimbError
from .sandbox_runner import ALLOWED_MODULES

RUNNER = Path(__file__).with_name("sandbox_runner.py")
TIMEOUT = 2.0
MAX_OUTPUT = 1_000_000
BANNED_NAMES = {
    "eval", "exec", "open", "compile", "getattr", "setattr", "delattr", "globals", "locals", "vars",
    "__import__", "input", "breakpoint", "memoryview", "type", "object", "super", "help", "dir", "id",
}
# str.format can reach attributes through "{0.__class__}"-style fields, which the AST check can't see.
BANNED_ATTRS = {"format", "format_map", "mro", "gi_frame", "f_globals", "f_back", "tb_frame"}


def check_code(code: str) -> list[str]:
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"syntax error: {e}"]
    errs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            errs += [f"import {a.name} not allowed" for a in node.names if a.name.split(".")[0] not in ALLOWED_MODULES]
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                errs.append(f"from {node.module} import not allowed")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in BANNED_ATTRS:
                errs.append(f"attribute .{node.attr} not allowed")
        elif isinstance(node, ast.Name):
            if node.id in BANNED_NAMES or node.id.startswith("__"):
                errs.append(f"name {node.id} not allowed")
        elif isinstance(node, (ast.Global, ast.Nonlocal, ast.AsyncFunctionDef, ast.Await)):
            errs.append(f"{type(node).__name__} not allowed")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        errs.append("must define a top-level function run(...)")
    return errs


def returned_keys(code: str) -> set[str] | None:
    """Keys of the dict literals that run() returns; None if any return is not a literal dict."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"), None)
    if fn is None:
        return None
    keys: set[str] = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Return):
            if not isinstance(node.value, ast.Dict) or not all(isinstance(k, ast.Constant) for k in node.value.keys):
                return None
            keys |= {k.value for k in node.value.keys}
    return keys


async def execute(code: str, args: dict, timeout: float = TIMEOUT) -> dict:
    errs = check_code(code)
    if errs:
        raise LimbError("forged code rejected: " + "; ".join(errs))
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-S", str(RUNNER),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(json.dumps({"code": code, "args": args}).encode()), timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise LimbError(f"forged code timed out after {timeout}s")
    if len(stdout) > MAX_OUTPUT:
        raise LimbError("forged code output too large")
    try:
        res = json.loads(stdout.decode("utf-8"))
    except ValueError:
        raise LimbError(f"forged code crashed: {stderr.decode(errors='replace')[-500:]}")
    if not res["ok"]:
        raise LimbError(res["error"])
    return res["result"]


async def run_tests(forged: ForgedLimb) -> list[str]:
    errs = check_code(forged.code)
    if errs:
        return [f"forged:{forged.name}: {e}" for e in errs]
    for i, t in enumerate(forged.tests):
        try:
            got = await execute(forged.code, t.args)
        except LimbError as e:
            errs.append(f"forged:{forged.name} test {i}: {e}")
            continue
        mismatched = {k: (v, got.get(k)) for k, v in t.expect.items() if got.get(k) != v}
        if mismatched:
            errs.append(f"forged:{forged.name} test {i}: expected/got {mismatched}")
        for k, needles in t.contains.items():
            text = str(got.get(k) or "")
            missing = [n for n in ([needles] if isinstance(needles, str) else needles) if n not in text]
            if missing:
                errs.append(f"forged:{forged.name} test {i}: {k} should contain {missing}, got {text[:300]!r}")
    return errs


class ForgedLimbRunner(Limb):
    seconds = 0.2

    def __init__(self, forged: ForgedLimb):
        self.forged = forged
        self.name = f"forged:{forged.name}"
        self.description = forged.description

    def output_fields(self, args):
        return returned_keys(self.forged.code)

    async def run(self, args, ctx):
        return await execute(self.forged.code, args)

