"""Менеджер песочницы: готовит рабочую папку и запускает в ней сгенерированный код.

Два режима (SANDBOX_MODE в .env):
  * docker     — контейнер без сети, с лимитами памяти/CPU/процессов (если Docker запущен)
  * subprocess — отдельный процесс Python с пустым окружением, таймаутом, лимитом памяти
                 и audit-hook'ом внутри (sandbox_boot.py)
  * auto       — Docker, если доступен, иначе subprocess.

Что получает сгенерированный код:
  - рабочую папку (только в неё можно писать)
  - копии входных файлов в inputs/
  - исходники капабилити-зависимостей как модули рядом
Чего НЕ получает: переменные окружения (ключи API), домашнюю папку, базу данных, сеть
(если капабилити явно не объявила network=true).
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from . import config

BOOT_FILE = Path(__file__).with_name("sandbox_boot.py")
MAX_OUTPUT_CHARS = 12000


class SandboxError(RuntimeError):
    pass


@dataclass
class SandboxResult:
    mode: str                          # docker | subprocess
    finished: bool                     # процесс отработал сам (не убит по таймауту/памяти)
    returncode: int | None
    duration: float
    stdout: str
    stderr: str
    killed_reason: str | None = None   # timeout | memory
    report: dict = field(default_factory=dict)   # содержимое __result__.json
    out_files: list[str] = field(default_factory=list)


class Sandbox:
    def __init__(self) -> None:
        self._docker_checked_at = 0.0
        self._docker_ok = False
        self._image_ready = False

    # ------------------------------------------------------------------ режим
    def docker_available(self) -> bool:
        """Docker считается доступным, если демон отвечает. Результат кэшируем на 30 секунд."""
        if time.time() - self._docker_checked_at < 30:
            return self._docker_ok
        self._docker_checked_at = time.time()
        try:
            r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"],
                               capture_output=True, timeout=5)
            self._docker_ok = r.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            self._docker_ok = False
        return self._docker_ok

    def mode(self) -> str:
        wanted = config.SANDBOX_MODE
        if wanted == "subprocess":
            return "subprocess"
        if self.docker_available():
            return "docker"
        if wanted == "docker":
            raise SandboxError("SANDBOX_MODE=docker, but the Docker daemon is not running")
        return "subprocess"

    def _ensure_image(self) -> None:
        if self._image_ready:
            return
        have = subprocess.run(["docker", "image", "inspect", config.DOCKER_IMAGE], capture_output=True)
        if have.returncode != 0:
            build = subprocess.run(["docker", "build", "-t", config.DOCKER_IMAGE, str(config.SANDBOX_DIR)],
                                   capture_output=True, text=True, timeout=600)
            if build.returncode != 0:
                raise SandboxError("docker build failed: " + build.stderr[-500:])
        self._image_ready = True

    # ------------------------------------------------------------------ публичные операции
    def run_tests(self, *, code: str, tests: str, dependencies: dict[str, str],
                  network: bool = False, timeout: int | None = None) -> SandboxResult:
        """Прогнать unittest-набор капабилити. dependencies = {имя_модуля: исходный код}."""
        files = {"capability.py": code, "test_capability.py": tests, **{f"{n}.py": c for n, c in dependencies.items()}}
        return self._run("tests", files, network=network, timeout=timeout or config.SANDBOX_TIMEOUT_SEC)

    def run_invoke(self, *, capabilities: dict[str, str], job: dict, input_files: list[Path], artifacts_dir: Path | None,
                   network: bool = False, timeout: int | None = None) -> SandboxResult:
        """Вызвать установленный навык: parse_request -> run -> format_result (шаги задаёт job["steps"])."""
        return self._run("invoke", {f"{n}.py": c for n, c in capabilities.items()}, network=network,
                         timeout=timeout or config.SANDBOX_TIMEOUT_SEC, task=job, input_files=input_files,
                         artifacts_dir=artifacts_dir, caps=list(capabilities), payload_name="invoke.json")[0]

    def run_workflow(self, *, workflow_code: str, capabilities: dict[str, str], task: dict,
                     input_files: list[Path], artifacts_dir: Path, network: bool = False,
                     timeout: int | None = None) -> tuple[SandboxResult, dict]:
        """Выполнить workflow.py. Возвращает результат и task с путями внутри песочницы."""
        files = {"workflow.py": workflow_code, **{f"{n}.py": c for n, c in capabilities.items()}}
        return self._run("workflow", files, network=network, timeout=timeout or config.SANDBOX_TIMEOUT_SEC * 2,
                         task=task, input_files=input_files, artifacts_dir=artifacts_dir,
                         caps=list(capabilities))

    # ------------------------------------------------------------------ внутренности
    def _run(self, mode_name: str, files: dict[str, str], *, network: bool, timeout: int,
             task: dict | None = None, input_files: list[Path] | None = None,
             artifacts_dir: Path | None = None, caps: list[str] | None = None, payload_name: str = "task.json"):
        workdir = config.RUNS_DIR / uuid.uuid4().hex[:12]
        (workdir / "out").mkdir(parents=True)
        (workdir / "tmp").mkdir()
        (workdir / "inputs").mkdir()
        try:
            shutil.copyfile(BOOT_FILE, workdir / "sandbox_boot.py")
            for name, content in files.items():
                (workdir / name).write_text(content, encoding="utf-8")

            sandbox_task = None
            if task is not None:
                # Входные файлы копируем внутрь песочницы; задача видит только относительные пути.
                inside = []
                for src in input_files or []:
                    dst = workdir / "inputs" / Path(src).name
                    shutil.copyfile(src, dst)
                    inside.append(f"inputs/{dst.name}")
                sandbox_task = {**task, "files": inside, "out_dir": "out"}
                (workdir / payload_name).write_text(json.dumps(sandbox_task, ensure_ascii=False), encoding="utf-8")

            mode = self.mode()
            result = (self._exec_docker if mode == "docker" else self._exec_subprocess)(
                workdir, mode_name, network, timeout, caps or [])

            report_path = workdir / "__result__.json"
            if report_path.exists():
                try:
                    result.report = json.loads(report_path.read_text(encoding="utf-8") or "{}")
                except json.JSONDecodeError:
                    result.report = {}
            # Забираем файлы, которые капабилити положила в out/
            if artifacts_dir is not None:
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                for f in (workdir / "out").rglob("*"):
                    if f.is_file():
                        rel = f.relative_to(workdir / "out")
                        target = artifacts_dir / rel
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(f, target)
                        result.out_files.append(rel.as_posix())
            return (result, sandbox_task) if task is not None else result
        finally:
            shutil.rmtree(workdir, ignore_errors=True)   # «уборка» песочницы

    def _exec_subprocess(self, workdir: Path, mode_name: str, network: bool, timeout: int,
                         caps: list[str]) -> SandboxResult:
        # Окружение ПУСТОЕ: никаких ключей, токенов и путей хозяина.
        env = {"FRANK_WORKDIR": str(workdir), "FRANK_NETWORK": "1" if network else "0",
               "FRANK_CAPS": ",".join(caps), "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
               "TEMP": str(workdir / "tmp"), "TMP": str(workdir / "tmp"), "TMPDIR": str(workdir / "tmp"),
               "HOME": str(workdir), "USERPROFILE": str(workdir)}
        if os.name == "nt":
            env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", r"C:\Windows")
        cmd = [sys.executable, "-I", "-B", "sandbox_boot.py", mode_name]
        t0 = time.perf_counter()
        proc = subprocess.Popen(cmd, cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        killed: dict[str, str] = {}
        stop = threading.Event()

        def kill_tree(reason: str) -> None:
            killed.setdefault("reason", reason)
            try:
                parent = psutil.Process(proc.pid)
                for child in parent.children(recursive=True):
                    child.kill()
                parent.kill()
            except psutil.Error:
                pass

        def watchdog() -> None:
            """Следим за памятью процесса (на Windows нет resource.setrlimit)."""
            limit = config.SANDBOX_MEMORY_MB * 1024 * 1024
            while not stop.wait(0.15):
                try:
                    p = psutil.Process(proc.pid)
                    rss = p.memory_info().rss + sum(c.memory_info().rss for c in p.children(recursive=True))
                except psutil.Error:
                    return
                if rss > limit:
                    kill_tree("memory")
                    return

        threading.Thread(target=watchdog, daemon=True).start()
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree("timeout")
            out, err = proc.communicate()
        finally:
            stop.set()
        return SandboxResult(mode="subprocess", finished="reason" not in killed, returncode=proc.returncode,
                             duration=round(time.perf_counter() - t0, 3), stdout=_clip(out), stderr=_clip(err),
                             killed_reason=killed.get("reason"))

    def _exec_docker(self, workdir: Path, mode_name: str, network: bool, timeout: int,
                     caps: list[str]) -> SandboxResult:
        self._ensure_image()
        name = "frank-" + uuid.uuid4().hex[:10]
        cmd = ["docker", "run", "--rm", "--name", name,
               "--network", "bridge" if network else "none",
               "--memory", f"{config.SANDBOX_MEMORY_MB}m", "--cpus", "1", "--pids-limit", "128",
               "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
               "-v", f"{workdir}:/work", "-w", "/work",
               "-e", "FRANK_WORKDIR=/work", "-e", f"FRANK_NETWORK={'1' if network else '0'}",
               "-e", f"FRANK_CAPS={','.join(caps)}", "-e", "HOME=/work", "-e", "TMPDIR=/work/tmp",
               config.DOCKER_IMAGE, "python", "-I", "-B", "sandbox_boot.py", mode_name]
        t0 = time.perf_counter()
        killed = None
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
            out, err, code = r.stdout, r.stderr, r.returncode
        except subprocess.TimeoutExpired as exc:
            subprocess.run(["docker", "kill", name], capture_output=True)
            out, err, code, killed = exc.stdout or b"", exc.stderr or b"", None, "timeout"
        if code == 137 and not killed:     # 137 = убит OOM-killer'ом
            killed = "memory"
        return SandboxResult(mode="docker", finished=killed is None, returncode=code,
                             duration=round(time.perf_counter() - t0, 3), stdout=_clip(out), stderr=_clip(err),
                             killed_reason=killed)


def _clip(data: bytes | None) -> str:
    text = (data or b"").decode("utf-8", errors="replace")
    return text if len(text) <= MAX_OUTPUT_CHARS else text[:MAX_OUTPUT_CHARS // 2] + "\n...[cut]...\n" + text[-MAX_OUTPUT_CHARS // 2:]
