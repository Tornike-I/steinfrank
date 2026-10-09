"""Защита API.

* Токен доступа: если в .env задан APP_ACCESS_TOKEN, все /api/* требуют заголовок X-Frank-Token
  (или ?token=... — только для SSE-потока, т.к. EventSource не умеет заголовки). Без токена — режим локального демо.
* Ограничение частоты: дорогие операции (задачи, голос, распознавание, загрузка) — не чаще N раз в минуту с одного IP.
* Проверка загружаемых файлов: расширение из белого списка, размер, «магические байты».
Ключи провайдеров (ElevenLabs, Sokosumi, OpenAI) никогда не покидают сервер.
"""
import hmac
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from . import config

PUBLIC_PATHS = {"/api/health"}

# лимиты: путь-префикс -> (запросов, за секунд)
LIMITS = {"/api/tasks": (12, 60), "/api/speak": (30, 60), "/api/transcribe": (20, 60), "/api/upload": (30, 60),
          "/api/self-audit": (6, 60), "/api/recommend": (60, 60)}

ALLOWED_UPLOADS = {".pdf": b"%PDF", ".xlsx": b"PK", ".csv": None, ".tsv": None, ".txt": None, ".json": None, ".md": None}


def check_token(request: Request) -> None:
    if not config.APP_ACCESS_TOKEN or not request.url.path.startswith("/api") or request.url.path in PUBLIC_PATHS:
        return
    given = request.headers.get("x-frank-token") or request.query_params.get("token") or ""
    if not hmac.compare_digest(given, config.APP_ACCESS_TOKEN):
        raise HTTPException(401, "access token required (X-Frank-Token)")


class RateLimiter:
    def __init__(self):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, request: Request) -> None:
        if request.method != "POST":
            return
        path = request.url.path
        rule = next(((p, lim) for p, lim in LIMITS.items() if path.startswith(p)), None)
        if not rule:
            return
        prefix, (limit, window) = rule
        key = (request.client.host if request.client else "?", prefix)
        now = time.time()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                raise HTTPException(429, f"too many requests: at most {limit} per {window}s for {prefix}")
            q.append(now)


def validate_upload(name: str, data: bytes) -> None:
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in ALLOWED_UPLOADS:
        raise HTTPException(415, f"file type '{ext or '?'}' is not allowed; allowed: {', '.join(sorted(ALLOWED_UPLOADS))}")
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"file too large (limit {config.MAX_UPLOAD_MB} MB)")
    if not data:
        raise HTTPException(400, "empty file")
    magic = ALLOWED_UPLOADS[ext]
    if magic and not data.startswith(magic):
        raise HTTPException(415, f"file content does not look like {ext}")
    if magic is None:
        try:
            data[:4096].decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(415, f"{ext} file must be UTF-8 text")
