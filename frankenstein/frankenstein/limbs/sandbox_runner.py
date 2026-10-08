"""Child process for forged limbs: reads {code, args} JSON on stdin, writes {ok, result|error} on stdout."""
import builtins
import json
import sys

ALLOWED_MODULES = {
    "re", "json", "math", "datetime", "string", "collections", "itertools",
    "statistics", "html", "textwrap", "unicodedata", "functools", "operator",
}
SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter", "float", "frozenset",
    "int", "isinstance", "len", "list", "map", "max", "min", "next", "range", "reversed", "round",
    "set", "slice", "sorted", "str", "sum", "tuple", "zip", "chr", "ord", "repr", "hash", "iter",
    "ValueError", "KeyError", "IndexError", "TypeError", "Exception", "StopIteration", "True", "False", "None",
}


def _import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in ALLOWED_MODULES:
        raise ImportError(f"import of {name!r} is not allowed")
    return builtins.__import__(name, globals, locals, fromlist, level)


def main():
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    safe = {k: getattr(builtins, k) for k in SAFE_BUILTINS if hasattr(builtins, k)}
    safe["__import__"] = _import
    ns = {"__builtins__": safe, "__name__": "forged"}
    try:
        exec(compile(payload["code"], "<forged>", "exec"), ns)
        result = ns["run"](**payload["args"])
        if not isinstance(result, dict):
            raise TypeError("run() must return a dict")
        out = {"ok": True, "result": result}
        text = json.dumps(out, ensure_ascii=False, default=str)
    except Exception as e:
        text = json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"})
    sys.stdout.buffer.write(text.encode("utf-8"))


if __name__ == "__main__":
    main()
