"""ОТВЕТ ДЛЯ ЧЕЛОВЕКА, а не дамп данных — детерминированно, без вызова модели.

Код органов и воркфлоу пишет модель, и в текст ответа иногда попадают «сырые» значения:
  "Salary range: {'min': 80000, 'max': 180000, 'currency': 'USD'}"  ->  "Salary range: 80,000–180,000 USD"
  "as of 2026-10-09T03:10:19.429027+00:00"                          ->  "as of 2026-10-09 03:10 UTC"
  " Top Job in Current Market\\n\\n…" (заголовок потерял ###)        ->  "### Top Job in Current Market\\n\\n…"
Блоки кода и ссылки Markdown не трогаем.
"""
import ast
import json
import re

RAW_RE = re.compile(r"\{[^{}\n]*\}|\[[^\[\]\n]*['\",][^\[\]\n]*\]")
ISO_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]00:?00|[+-]\d{2}:?\d{2})?\b")
FENCE_RE = re.compile(r"(```.*?```)", re.S)
EMPTY = (None, "", [], {})


def _num(v) -> str:
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if isinstance(v, int) and abs(v) >= 10000:
        return f"{v:,}"
    return str(v)


def human(v) -> str:
    """Значение -> короткий читаемый текст: диапазон min–max, «ключ: значение; …», список через запятую."""
    if isinstance(v, dict):
        keys = {str(k).lower(): k for k in v}
        lo, hi = keys.get("min"), keys.get("max")
        if lo is not None and hi is not None:
            unit = v.get(keys.get("currency") or keys.get("unit") or "", "")
            return f"{_num(v[lo])}–{_num(v[hi])}" + (f" {unit}" if unit else "")
        return "; ".join(f"{str(k).replace('_', ' ')}: {human(x)}" for k, x in v.items() if x not in EMPTY)
    if isinstance(v, (list, tuple)):
        return ", ".join(human(x) for x in v if x not in EMPTY)
    return "—" if v is None else _num(v)


def _raw(m: re.Match) -> str:
    s = m.group(0)
    for parse in (ast.literal_eval, json.loads):
        try:
            v = parse(s)
        except Exception:  # noqa: BLE001 — не данные (ссылка, шаблон, текст в скобках) — оставляем как есть
            continue
        return human(v) if isinstance(v, (dict, list)) else s
    return s


def _iso(m: re.Match) -> str:
    day, hm, tz = m.group(1), m.group(2), m.group(3) or ""
    utc = tz in ("Z", "+00:00", "+0000", "-00:00")
    return f"{day} {hm}" + (" UTC" if utc else f" {tz}" if tz else "")


def _text(s: str) -> str:
    out = []
    for part in FENCE_RE.split(s):
        if not part.startswith("```"):
            part = ISO_RE.sub(_iso, RAW_RE.sub(_raw, part))
        out.append(part)
    s = "".join(out)
    # заголовок без ###: первая короткая строка без точки в конце, за ней пустая строка
    first, sep, rest = s.partition("\n\n")
    line = first.strip()
    if sep and line and "\n" not in line and len(line) <= 80 and not line.startswith(("#", "-", "*", "|", ">")) \
            and line[-1] not in ".!?:;," and first != line:
        s = f"### {line}\n\n{rest}"
    return s.strip()


def normalize(summary):
    """summary — строка или {"en": …, "cs": …}; возвращает то же, но читаемое."""
    if isinstance(summary, dict):
        return {k: _text(v) if isinstance(v, str) else v for k, v in summary.items()}
    return _text(summary) if isinstance(summary, str) else summary
