"""Этот файл запускается ВНУТРИ песочницы (отдельный процесс Python или контейнер Docker).

Он НЕ импортируется основным приложением — sandbox.py копирует его в рабочую папку
и запускает командой:   python -I -B sandbox_boot.py tests|workflow

Что он делает:
  1. Ставит audit-hook — Python вызывает его при открытии файлов, запуске процессов,
     сетевых соединениях. Запрещённое действие -> PermissionError. Хук нельзя снять
     из Python-кода, поэтому сгенерированная капабилити не может его обойти.
  2. Запускает тесты (режим tests) или воркфлоу задачи (режим workflow).
  3. Записывает результат в файл __result__.json (stdout сгенерированного кода с ним не смешивается).

Используется только стандартная библиотека.
"""
import json
import os
import sys
import time
import traceback

MODE = sys.argv[1] if len(sys.argv) > 1 else "tests"
WORKDIR = os.path.realpath(os.environ.get("FRANK_WORKDIR", os.getcwd()))
NETWORK = os.environ.get("FRANK_NETWORK") == "1"

# Результат пишем в файл, открытый ДО установки хука, — сгенерированный код его не подменит.
_result_file = open(os.path.join(WORKDIR, "__result__.json"), "w", encoding="utf-8")


def _norm(p: str) -> str:
    return os.path.normcase(os.path.realpath(p))


# ----------------------------------------------------------------------------
# 1. AUDIT-HOOK: что разрешено сгенерированному коду
# ----------------------------------------------------------------------------
def _read_roots() -> list[str]:
    """Читать можно: рабочую папку и установку Python (стандартная библиотека, site-packages)."""
    import sysconfig
    roots = {WORKDIR, sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}
    roots.update(sysconfig.get_paths().values())
    roots.update(["/usr/share/zoneinfo", "/usr/lib/python3", "/etc/localtime"])
    if os.environ.get("FRANK_NETWORK") == "1":    # HTTPS: сертификаты центров сертификации и DNS (Linux / Docker)
        roots.update(["/etc/ssl", "/usr/lib/ssl", "/usr/share/ca-certificates", "/etc/pki", "/etc/hosts", "/etc/resolv.conf",
                      "/etc/nsswitch.conf", "/etc/gai.conf"])
    return [_norm(r) for r in roots if r]


READ_ROOTS = _read_roots()
WRITE_ROOTS = [_norm(WORKDIR)]

WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC

# События, которые запрещены всегда (запуск процессов, нативный код)
DENY_ALWAYS = {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.fork",
               "os.forkpty", "ctypes.dlopen", "os.startfile"}
# События сети — запрещены, если капабилити не объявила network=true
NETWORK_EVENTS = {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname",
                  "socket.sendto", "socket.gethostbyaddr"}
# События, меняющие файловую систему: все аргументы-пути должны лежать в рабочей папке
FS_WRITE_EVENTS = {"os.remove", "os.rename", "os.mkdir", "os.rmdir", "os.truncate", "os.chmod",
                   "shutil.rmtree", "shutil.copyfile", "shutil.move", "os.symlink", "os.link"}


def _inside(path, roots) -> bool:
    try:
        return any((lambda p: p == r or p.startswith(r + os.sep))(_norm(os.fsdecode(path))) for r in roots)
    except Exception:
        return False


def _audit(event: str, args: tuple) -> None:
    if event == "open":
        path, mode, flags = args[0], args[1], args[2]
        if isinstance(path, int):
            return
        is_write = bool(flags & WRITE_FLAGS) if isinstance(flags, int) else any(c in str(mode) for c in "wax+")
        if not _inside(path, WRITE_ROOTS if is_write else READ_ROOTS):
            raise PermissionError(f"sandbox: {'write' if is_write else 'read'} access to '{path}' is not allowed")
    elif event in DENY_ALWAYS:
        raise PermissionError(f"sandbox: '{event}' is not allowed")
    elif event in NETWORK_EVENTS:
        if not NETWORK:
            raise PermissionError("sandbox: network access is disabled for this capability")
    elif event in FS_WRITE_EVENTS:
        for a in args:
            if isinstance(a, (str, bytes, os.PathLike)) and not _inside(a, WRITE_ROOTS):
                raise PermissionError(f"sandbox: '{event}' outside the working directory is not allowed")
    elif event == "os.listdir":
        if args and isinstance(args[0], (str, bytes, os.PathLike)) and not _inside(args[0], READ_ROOTS):
            raise PermissionError("sandbox: listing directories outside the working directory is not allowed")


# ----------------------------------------------------------------------------
# 2. Подготовка окружения. Всё нужное импортируем ДО хука, чтобы он не мешал самому раннеру.
# ----------------------------------------------------------------------------
import hashlib  # noqa: E402
import importlib  # noqa: E402
import unittest  # noqa: E402

os.chdir(WORKDIR)
sys.path.insert(0, WORKDIR)
sys.addaudithook(_audit)

CALLS: list[dict] = []     # трассировка вызовов капабилити (для статистики и поиска паттернов)
_depth = [0]


def _write_result(obj: dict) -> None:
    _result_file.seek(0)
    _result_file.truncate()
    _result_file.write(json.dumps(obj, ensure_ascii=False, default=str))
    _result_file.flush()


# ----------------------------------------------------------------------------
# РЕЖИМ tests: запустить unittest из test_capability.py и собрать подробный отчёт
# ----------------------------------------------------------------------------
class _Collector(unittest.TestResult):
    def __init__(self):
        super().__init__()
        self.items: list[dict] = []
        self._t0 = 0.0

    def startTest(self, test):
        super().startTest(test)
        self._t0 = time.perf_counter()

    def _add(self, test, status, message=""):
        self.items.append({"name": test.id().split(".", 1)[-1], "status": status,
                           "message": message[-1500:], "duration": round(time.perf_counter() - self._t0, 4)})

    def addSuccess(self, test):
        super().addSuccess(test)
        self._add(test, "passed")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._add(test, "failed", self._exc_info_to_string(err, test))

    def addError(self, test, err):
        super().addError(test, err)
        self._add(test, "error", self._exc_info_to_string(err, test))

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        self._add(test, "skipped", reason)


def run_tests() -> dict:
    t0 = time.perf_counter()
    try:
        suite = unittest.defaultTestLoader.loadTestsFromName("test_capability")
    except Exception:
        return {"ok": False, "total": 0, "passed": 0, "tests": [], "duration": 0,
                "load_error": traceback.format_exc()[-3000:]}
    collector = _Collector()
    suite.run(collector)
    # Тесты, которые не смогли загрузиться (ImportError и т.п.), unittest превращает в «ошибочные тесты»
    passed = sum(1 for t in collector.items if t["status"] == "passed")
    return {"ok": passed == len(collector.items) and len(collector.items) > 0,
            "total": len(collector.items), "passed": passed, "tests": collector.items,
            "duration": round(time.perf_counter() - t0, 3), "load_error": None}


# ----------------------------------------------------------------------------
# РЕЖИМ workflow: выполнить workflow.main(task) и записать, какие капабилити вызывались
# ----------------------------------------------------------------------------
def _wrap_capability(name: str, module) -> None:
    original = module.run

    def traced(inp, *args, **kwargs):
        rec = {"capability": name, "depth": _depth[0], "ts": time.time()}
        try:
            rec["input_hash"] = hashlib.md5(json.dumps(inp, sort_keys=True, default=str).encode()).hexdigest()[:12]
        except Exception:
            rec["input_hash"] = "?"
        _depth[0] += 1
        t0 = time.perf_counter()
        try:
            out = original(inp, *args, **kwargs)
            rec["ok"] = True
            return out
        except BaseException as exc:
            rec["ok"] = False
            rec["error"] = f"{type(exc).__name__}: {exc}"[:500]
            raise
        finally:
            _depth[0] -= 1
            rec["duration"] = round(time.perf_counter() - t0, 4)
            CALLS.append(rec)

    module.run = traced


def run_workflow() -> dict:
    result = {"ok": False, "output": None, "error": None, "traceback": None, "calls": CALLS}
    try:
        with open("task.json", encoding="utf-8") as fh:
            task = json.load(fh)
        for name in [n for n in os.environ.get("FRANK_CAPS", "").split(",") if n]:
            _wrap_capability(name, importlib.import_module(name))
        workflow = importlib.import_module("workflow")
        output = workflow.main(task)
        if not isinstance(output, dict):
            raise TypeError(f"workflow.main must return a dict, got {type(output).__name__}")
        json.dumps(output, default=str)  # проверка, что результат сериализуется
        result.update(ok=True, output=output)
    except BaseException as exc:
        result.update(error=f"{type(exc).__name__}: {exc}"[:1500], traceback=traceback.format_exc()[-3500:])
    return result


def run_invoke() -> dict:
    """Вызов навыка: parse_request(text, context) -> run(params) -> format_result(output, lang). Без LLM."""
    result = {"ok": False, "stage": None, "params": None, "output": None, "answer": None, "error": None,
              "error_type": None, "http_status": None, "traceback": None, "calls": CALLS}
    try:
        with open("invoke.json", encoding="utf-8") as fh:
            job = json.load(fh)
        for name in [n for n in os.environ.get("FRANK_CAPS", "").split(",") if n]:
            _wrap_capability(name, importlib.import_module(name))
        mod = importlib.import_module(job["skill"])
        ctx = {**job.get("context", {}), "files": job.get("files", [])}
        params = job.get("params")
        steps = job.get("steps", ["parse", "run", "format"])
        if "parse" in steps:
            result["stage"] = "parse"
            params = mod.parse_request(job.get("text", ""), ctx) if hasattr(mod, "parse_request") else None
            # dict — один запрос; list[dict] — несколько однотипных сущностей («Дубай и Тель-Авив»), рантайм исполнит каждую
            if isinstance(params, list) and params and all(isinstance(p, dict) for p in params):
                if len(params) == 1:
                    params = params[0]
            elif params is not None and not isinstance(params, dict):
                raise TypeError(f"parse_request must return a dict, a list of dicts or None, got {type(params).__name__}")
            result["params"] = params
            if params is None:
                result["ok"] = True
                return result
        if "run" in steps:
            result["stage"] = "run"
            if isinstance(params, list):
                result["ok"] = True              # несколько сущностей: каждую рантайм запустит отдельным вызовом
                return result
            inp = {k: v for k, v in (params or {}).items() if not str(k).startswith("_")}
            inp["out_dir"] = "out"
            if job.get("slots") is not None:
                inp["slots"] = job["slots"]
            if job.get("knowledge") is not None:
                inp["knowledge"] = job["knowledge"]      # пакет знаний (данные), подобранный рантаймом под параметры
            output = mod.run(inp)
            json.dumps(output, default=str)
            result["output"] = output
        if "format" in steps:
            result["stage"] = "format"
            if hasattr(mod, "format_result"):
                result["answer"] = str(mod.format_result(result["output"], ctx.get("lang", "en")))
        if "check" in steps and hasattr(mod, "check_result"):
            # проверка качества самим навыком (без LLM): тот ли город/роль/уровень, нет ли «воды»
            result["problems"] = [str(p) for p in (mod.check_result(result["output"], params or {}) or [])][:10]
        result["ok"] = True
    except BaseException as exc:
        result.update(error=f"{type(exc).__name__}: {exc}"[:1500], error_type=type(exc).__name__,
                      http_status=getattr(exc, "code", None) if isinstance(getattr(exc, "code", None), int) else None,
                      traceback=traceback.format_exc()[-3500:])
    return result


if __name__ == "__main__":
    try:
        payload = run_tests() if MODE == "tests" else run_invoke() if MODE == "invoke" else run_workflow()
    except BaseException as exc:  # на случай ошибки самого раннера
        payload = {"ok": False, "error": f"runner crashed: {exc}", "traceback": traceback.format_exc()[-3000:]}
    _write_result(payload)
