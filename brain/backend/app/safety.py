"""Статическая проверка сгенерированного кода — ДО запуска в песочнице.

Это первая линия защиты и быстрая обратная связь для LLM («этот модуль недоступен»).
Настоящий барьер — сама песочница (sandbox_boot.py: audit-hook, пустое окружение, таймаут)
и, если запущен, Docker с отключённой сетью.
"""
import ast
import re
import sys

# Сторонние библиотеки, установленные в песочнице
THIRD_PARTY_ALLOWED = {"pypdf", "reportlab", "openpyxl", "et_xmlfile"}

# Никогда не разрешено (запуск процессов, нативный код, обход через многопроцессность)
ALWAYS_BLOCKED_MODULES = {"subprocess", "ctypes", "multiprocessing", "pty", "pexpect", "winreg",
                          "_winapi", "importlib.machinery"}
# Разрешено только если капабилити явно объявила network=true
NETWORK_MODULES = {"socket", "ssl", "urllib", "http", "ftplib", "smtplib", "requests", "httpx", "aiohttp",
                   "xmlrpc", "telnetlib", "socketserver", "asyncio"}
BLOCKED_BUILTINS = {"eval", "exec", "compile", "__import__"}
BLOCKED_ATTRS = {("os", "system"), ("os", "popen"), ("os", "execv"), ("os", "execvp"), ("os", "spawnl"),
                 ("os", "fork"), ("os", "startfile")}

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{2,40}$")


def valid_name(name: str) -> bool:
    return bool(NAME_RE.match(name or "")) and name not in sys.stdlib_module_names \
        and name not in THIRD_PARTY_ALLOWED and name not in {"capability", "workflow", "sandbox_boot"}


def check_source(code: str, *, allowed_capabilities: set[str], network: bool) -> list[str]:
    """Возвращает список нарушений (пустой список = код допустим)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError: {exc.msg} (line {exc.lineno})"]

    problems: list[str] = []
    for node in ast.walk(tree):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules = [node.module]
        for mod in modules:
            top = mod.split(".")[0]
            if top in ALWAYS_BLOCKED_MODULES or mod in ALWAYS_BLOCKED_MODULES:
                problems.append(f"import of '{mod}' is forbidden in the sandbox")
            elif top in NETWORK_MODULES and not network:
                problems.append(f"import of '{mod}' needs network access, but this capability is declared with network=false")
            elif top in sys.stdlib_module_names or top in THIRD_PARTY_ALLOWED or top in allowed_capabilities:
                pass
            elif top in {"capability", "workflow"}:
                pass
            else:
                problems.append(f"module '{top}' is not available in the sandbox "
                                f"(allowed: Python stdlib, pypdf, reportlab, openpyxl, declared dependencies)")
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in BLOCKED_BUILTINS:
                problems.append(f"call to '{node.func.id}()' is forbidden")
            if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                if (node.func.value.id, node.func.attr) in BLOCKED_ATTRS:
                    problems.append(f"call to '{node.func.value.id}.{node.func.attr}()' is forbidden")
    return problems
