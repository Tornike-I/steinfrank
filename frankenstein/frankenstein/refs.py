"""`{{path}}` templates and the `when` condition language. Path lookup only, never eval."""
import json
import re
from typing import Any

TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][\w.\-]*)\s*\}\}")
WHOLE = re.compile(r"^\{\{\s*([A-Za-z_][\w.\-]*)\s*\}\}$")


class RefError(ValueError):
    pass


def lookup(path: str, ctx: dict) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and part.lstrip("-").isdigit():
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        elif isinstance(cur, list) and part == "length":
            cur = len(cur)
        else:
            return None
    return cur


def _to_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def render(value: Any, ctx: dict) -> Any:
    """A string that is exactly one `{{ref}}` keeps the referenced value's type."""
    if isinstance(value, str):
        m = WHOLE.match(value)
        if m:
            return lookup(m.group(1), ctx)
        return TEMPLATE.sub(lambda m: _to_text(lookup(m.group(1), ctx)), value)
    if isinstance(value, dict):
        return {k: render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, ctx) for v in value]
    return value


def template_refs(value: Any) -> list[str]:
    if isinstance(value, str):
        return TEMPLATE.findall(value)
    if isinstance(value, dict):
        return [r for v in value.values() for r in template_refs(v)]
    if isinstance(value, list):
        return [r for v in value for r in template_refs(v)]
    return []


def literal_chars(value: Any) -> int:
    if isinstance(value, str):
        return len(TEMPLATE.sub("", value))
    if isinstance(value, dict):
        return sum(literal_chars(v) for v in value.values())
    if isinstance(value, list):
        return sum(literal_chars(v) for v in value)
    return 0


TOKEN = re.compile(
    r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<str>'[^']*'|\"[^\"]*\")|(?P<op>==|!=|<=|>=|<|>|\(|\)|,)"
    r"|(?P<word>[A-Za-z_][\w.\-]*))"
)
KEYWORDS = {"and", "or", "not", "in", "true", "false", "null", "len"}


def _tokenize(expr: str) -> list[tuple[str, str]]:
    out, pos = [], 0
    expr = expr.strip()
    while pos < len(expr):
        m = TOKEN.match(expr, pos)
        if not m or m.end() == pos:
            raise RefError(f"bad token at {pos} in {expr!r}")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
        while pos < len(expr) and expr[pos].isspace():
            pos += 1
    return out


class _Parser:
    def __init__(self, expr: str):
        self.toks = _tokenize(expr)
        self.i = 0

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, value=None):
        tok = self.peek()
        if tok[0] is None or (value is not None and tok[1] != value):
            raise RefError(f"expected {value or 'token'}, got {tok[1]!r}")
        self.i += 1
        return tok

    def parse(self):
        node = self.or_()
        if self.i != len(self.toks):
            raise RefError(f"unexpected {self.peek()[1]!r}")
        return node

    def or_(self):
        node = self.and_()
        while self.peek() == ("word", "or"):
            self.take()
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.peek() == ("word", "and"):
            self.take()
            node = ("and", node, self.not_())
        return node

    def not_(self):
        if self.peek() == ("word", "not"):
            self.take()
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        left = self.atom()
        kind, val = self.peek()
        if kind == "op" and val in ("==", "!=", "<", ">", "<=", ">="):
            self.take()
            return ("cmp", val, left, self.atom())
        if (kind, val) == ("word", "in"):
            self.take()
            return ("cmp", "in", left, self.atom())
        if (kind, val) == ("word", "not") and self.toks[self.i + 1 : self.i + 2] == [("word", "in")]:
            self.take()
            self.take()
            return ("cmp", "not in", left, self.atom())
        return left

    def atom(self):
        kind, val = self.take()
        if kind == "num":
            return ("lit", float(val) if "." in val else int(val))
        if kind == "str":
            return ("lit", val[1:-1])
        if kind == "op" and val == "(":
            node = self.or_()
            self.take(")")
            return node
        if kind == "word":
            if val == "len":
                self.take("(")
                node = self.or_()
                self.take(")")
                return ("len", node)
            if val in ("true", "false", "null"):
                return ("lit", {"true": True, "false": False, "null": None}[val])
            if val in KEYWORDS:
                raise RefError(f"unexpected keyword {val!r}")
            return ("ref", val)
        raise RefError(f"unexpected {val!r}")


def parse_when(expr: str):
    return _Parser(expr).parse()


def when_refs(expr: str) -> list[str]:
    out = []

    def walk(n):
        if n[0] == "ref":
            out.append(n[1])
        for c in n[1:]:
            if isinstance(c, tuple):
                walk(c)

    walk(parse_when(expr))
    return out


def _eval(n, ctx):
    op = n[0]
    if op == "lit":
        return n[1]
    if op == "ref":
        return lookup(n[1], ctx)
    if op == "len":
        v = _eval(n[1], ctx)
        return len(v) if isinstance(v, (str, list, dict)) else 0
    if op == "not":
        return not _eval(n[1], ctx)
    if op == "and":
        return bool(_eval(n[1], ctx)) and bool(_eval(n[2], ctx))
    if op == "or":
        return bool(_eval(n[1], ctx)) or bool(_eval(n[2], ctx))
    _, cmp, a, b = n
    a, b = _eval(a, ctx), _eval(b, ctx)
    try:
        if cmp == "==":
            return a == b
        if cmp == "!=":
            return a != b
        if cmp in ("in", "not in"):
            hit = a in b if isinstance(b, (str, list, dict)) else False
            return hit if cmp == "in" else not hit
        return {"<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[cmp]
    except TypeError:
        return False


def eval_when(expr: str, ctx: dict) -> bool:
    return bool(_eval(parse_when(expr), ctx))
