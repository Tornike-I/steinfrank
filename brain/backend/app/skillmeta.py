"""Метаданные навыка, извлечённые из его кода (без выполнения кода — только AST + literal_eval).

Навык, который умеет сам отвечать на запросы пользователя («вызываемый»), объявляет в capability.py:

    SKILL = {"intents": [...], "keywords": [...], "examples": [...], "limits": {...},
             "freshness_seconds": 900 | None, "llm_slots": {...}}
    def parse_request(text, context) -> dict | None   # детерминированный разбор запроса в параметры run()
    def format_result(result, lang) -> str            # детерминированный ответ человеку

Именно это позволяет выполнять 2-й, 3-й, 100-й похожий запрос БЕЗ вызова модели.
"""
import ast
import re


def extract(code: str) -> dict:
    """{"callable": bool, "intents", "keywords", "examples", "limits", "freshness_seconds", "llm_slots", "has_format"}"""
    meta = {"callable": False, "has_format": False, "intents": [], "keywords": [], "examples": [], "limits": {},
            "freshness_seconds": None, "llm_slots": {}}
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return meta
    funcs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    meta["callable"] = "parse_request" in funcs and "run" in funcs
    meta["has_format"] = "format_result" in funcs
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SKILL" for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                continue
            if isinstance(value, dict):
                for k in ("intents", "keywords", "examples"):
                    if isinstance(value.get(k), list):
                        meta[k] = [str(x) for x in value[k]][:40]
                if isinstance(value.get("limits"), dict):
                    meta["limits"] = {str(k): str(v) for k, v in value["limits"].items()}
                f = value.get("freshness_seconds")
                meta["freshness_seconds"] = int(f) if isinstance(f, (int, float)) and f > 0 else None
                slots = value.get("llm_slots")
                if isinstance(slots, dict):
                    meta["llm_slots"] = {str(k): v for k, v in slots.items() if isinstance(v, dict) and isinstance(v.get("prompt"), str)}
                # навык на ПАКЕТАХ ЗНАНИЙ: {"kind", "param", "key_param"?, "describe"?, "schema"?} (см. knowledge_packs.py)
                kn = value.get("knowledge")
                if isinstance(kn, dict) and isinstance(kn.get("kind"), str) and isinstance(kn.get("param"), str):
                    meta["knowledge"] = {k: kn[k] for k in ("kind", "param", "key_param", "level_param", "count_param",
                                                             "detail_param", "describe", "schema") if k in kn}
    return meta


_WORD = re.compile(r"[a-zA-Zа-яА-ЯěščřžýáíéůúňťďĚŠČŘŽÝÁÍÉŮÚŇŤĎ0-9]{3,}")
STOP = {"the", "and", "for", "with", "what", "how", "show", "give", "please", "can", "you", "will", "this", "that", "from",
        "into", "about", "me", "my", "is", "are", "be", "jak", "jaké", "jaká", "co", "pro", "na", "mi", "prosím", "je", "bude"}


def tokens(text: str) -> set[str]:
    out = set()
    for w in _WORD.findall(text.lower()):
        if w in STOP:
            continue
        out.add(w)
        if len(w) > 5:
            out.add(w[:5])        # грубый «стемминг»: forecast/forecasts, počasí/počasím
    return out
