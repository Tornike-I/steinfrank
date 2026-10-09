"""Отмена задачи пользователем.

Флаг отмены проверяется в «безопасных точках»: перед каждым вызовом мозга, между раундами сборки,
между попытками воркфлоу и во время ожидания ответа провайдера. Уже запущенный тест в песочнице
доигрывается до конца (это секунды), затем задача останавливается.
"""
import threading

from .usage import current_task

_flags: dict[int, threading.Event] = {}
_lock = threading.Lock()


class TaskCancelled(Exception):
    pass


def request(task_id: int) -> None:
    with _lock:
        _flags.setdefault(task_id, threading.Event()).set()


def clear(task_id: int) -> None:
    with _lock:
        _flags.pop(task_id, None)


def is_cancelled(task_id: int | None = None) -> bool:
    tid = task_id if task_id is not None else current_task.get()
    if tid is None:
        return False
    with _lock:
        ev = _flags.get(tid)
    return bool(ev and ev.is_set())


def check(task_id: int | None = None) -> None:
    """Бросает TaskCancelled, если задачу отменили."""
    if is_cancelled(task_id):
        raise TaskCancelled("cancelled by the user")
