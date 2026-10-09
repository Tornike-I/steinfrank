"""ПРАВИЛА РЕЦЕПТОВ: рецепт — это переиспользуемая ПРОЦЕДУРА, а не прошлый ответ.

Найденная ошибка (задачи #14 -> #15 в живой базе): воркфлоу для «погода в Тель-Авиве и Дубае» был сохранён как рецепт
с городами, вписанными прямо в код (`get_weather_by_location.run({"location": "Kiev", ...})`). Следующий похожий запрос
(«Киев и Прага») запускал этот рецепт без вызова модели — и честно возвращал погоду для СТАРЫХ городов.

Теперь:
  * планировщик извлекает параметры ТЕКУЩЕГО запроса (task["params"]), воркфлоу обязан читать их оттуда;
  * bound_values() находит значения, которые код всё-таки зашил литералами;
  * guard() не даёт запустить рецепт, если зашитые значения не совпадают с текущим запросом;
  * contamination() ловит результат, в котором есть значения прошлого запроса, а текущих нет.
Всё — детерминированно, без вызова модели.
"""
import ast
import re

_WORD = re.compile(r"[\wÀ-ɏЀ-ӿ]+", re.UNICODE)


def _norm(v) -> str:
    return " ".join(_WORD.findall(str(v).lower()))


def _flat(params) -> list:
    """Все скалярные значения параметров (списки городов раскрываются)."""
    out = []
    if isinstance(params, dict):
        for v in params.values():
            out += _flat(v)
    elif isinstance(params, (list, tuple)):
        for v in params:
            out += _flat(v)
    elif isinstance(params, (str, int, float)) and not isinstance(params, bool):
        out.append(params)
    return out


def _call_literals(code: str) -> list:
    """Литералы (строки и числа), переданные в вызовы <орган>.run({...}) — то, что код «зашил» в аргументы."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "run":
            for arg in node.args:
                for sub in ast.walk(arg):
                    if isinstance(sub, ast.Dict):
                        for v in sub.values:
                            if isinstance(v, ast.Constant) and isinstance(v.value, (str, int, float)) and not isinstance(v.value, bool):
                                out.append(v.value)
                    elif isinstance(sub, ast.List):
                        for v in sub.elts:
                            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                                out.append(v.value)
    return out


def bound_values(code: str, params: dict, source_text: str = "") -> list:
    """Значения запроса, которые код вписал литералами (город, роль, горизонт…)."""
    wanted = {_norm(v) for v in _flat(params) if _norm(v)}
    text = _norm(source_text)
    out = []
    for lit in _call_literals(code):
        n = _norm(lit)
        if not n:
            continue
        if n in wanted or (isinstance(lit, str) and len(n) >= 3 and re.search(rf"\b{re.escape(n)}\b", text)):
            out.append(lit)
    return sorted(set(map(str, out)))


def guard(recipe: dict, params: dict | None, text: str) -> str | None:
    """None — рецепт можно запускать для ЭТОГО запроса; иначе — причина отказа."""
    from .db import loads
    rparams = loads(recipe.get("params"), None)
    if rparams is None:
        # старый рецепт (до этой версии): без описания параметров. Любой строковый аргумент с заглавной буквы
        # (город, имя, компания) обязан встречаться в текущем запросе.
        named = [v for v in _call_literals(recipe.get("code") or "") if isinstance(v, str) and v[:1].isupper()]
        missing = [v for v in named if not re.search(rf"\b{re.escape(_norm(v))}\b", _norm(text))]
        return f"legacy recipe hardcodes {missing} which this request does not mention" if missing else None
    if sorted(rparams) != sorted(params or {}):
        return f"recipe expects params {sorted(rparams)}, this request has {sorted(params or {})}"
    bound = loads(recipe.get("bound"), []) or []
    now_vals = {_norm(v) for v in _flat(params or {})}
    stale = [b for b in bound if _norm(b) not in now_vals and not re.search(rf"\b{re.escape(_norm(b))}\b", _norm(text))]
    return f"recipe code hardcodes {stale} from an earlier request" if stale else None


def contamination(recipe: dict, params: dict | None, output) -> list:
    """Значения ПРОШЛОГО запроса рецепта, которые попали в результат, хотя текущий запрос их не содержит."""
    from .db import loads
    old = loads(recipe.get("params"), None) or {}
    now_vals = {_norm(v) for v in _flat(params or {})}
    blob = _norm(output)
    out = []
    for v in _flat(old):
        n = _norm(v)
        if isinstance(v, str) and len(n) >= 3 and n not in now_vals and re.search(rf"\b{re.escape(n)}\b", blob):
            out.append(v)
    return out
