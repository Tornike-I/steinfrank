"""SQLite: схема базы и тонкая обёртка с блокировкой (агент работает в потоках)."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now() -> str:
    """Текущее время в UTC, ISO-формат."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);

-- Одна строка = одна «линия» капабилити (имя). Версии лежат в capability_versions.
CREATE TABLE IF NOT EXISTS capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    implementation_path TEXT,
    metadata TEXT,                 -- JSON: inputs, outputs, dependencies, network, purpose_en/cs
    status TEXT NOT NULL,          -- active | retired
    generation INTEGER NOT NULL,   -- в каком поколении родилась
    parent_id INTEGER,             -- эволюционный предок
    current_version TEXT,
    replacement_id INTEGER,        -- кто заменил (если retired)
    death_cause TEXT,              -- код причины: superseded|redundant|low_reliability|obsolete|harmful|inefficient|manual
    death_note TEXT,
    retired_generation INTEGER,
    created_at TEXT NOT NULL,
    retired_at TEXT
);

CREATE TABLE IF NOT EXISTS capability_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id INTEGER NOT NULL,
    version TEXT NOT NULL,
    implementation TEXT NOT NULL,
    tests TEXT NOT NULL,
    readme TEXT,
    metadata TEXT,                 -- JSON: spec этой версии
    origin TEXT,                   -- created | mutation | composite
    test_total INTEGER DEFAULT 0,
    test_passed INTEGER DEFAULT 0,
    score REAL,                    -- доля пройденных тестов на общем наборе
    repairs INTEGER DEFAULT 0,     -- сколько раз пришлось чинить
    status TEXT NOT NULL,          -- active | retired | rejected
    death_cause TEXT,
    death_note TEXT,
    created_at TEXT NOT NULL,
    retired_at TEXT
);

CREATE TABLE IF NOT EXISTS capability_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    capability_id INTEGER NOT NULL,
    version TEXT,
    task_id INTEGER,
    input_hash TEXT,
    input TEXT,
    output TEXT,
    success INTEGER NOT NULL,
    error TEXT,
    duration REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evolution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    generation INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    capability_id INTEGER,
    capability_name TEXT,
    description TEXT,
    payload TEXT,
    task_id INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    input TEXT NOT NULL,
    files TEXT,
    lang TEXT,
    status TEXT NOT NULL,          -- running | completed | failed
    result TEXT,
    error TEXT,
    trace TEXT,                    -- JSON: последовательность вызванных капабилити
    duration REAL,
    generation_before INTEGER,
    generation_after INTEGER,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS generations (
    generation INTEGER PRIMARY KEY,
    capabilities_total INTEGER,
    active INTEGER,
    retired INTEGER,
    note TEXT,
    created_at TEXT NOT NULL
);

-- Учёт расхода: каждый вызов «мозга» (llm) и каждая озвучка (tts).
CREATE TABLE IF NOT EXISTS usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER,
    kind TEXT NOT NULL,            -- llm | tts
    purpose TEXT,                  -- planning | building | repairing | composing | mutating | ...
    provider TEXT,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    chars INTEGER DEFAULT 0,       -- для tts: сколько символов озвучено
    credits REAL DEFAULT 0,
    seconds REAL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- Рецепты: успешные воркфлоу, которые можно запустить повторно без единого вызова модели.
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    capabilities TEXT NOT NULL,    -- JSON: отсортированный список имён
    code TEXT NOT NULL,
    uses INTEGER DEFAULT 0,        -- сколько раз запускали повторно
    successes INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Долговременная память: короткие уроки, которые агент вынес из задач.
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


# ---------------------------------------------------------------------------
# МИГРАЦИИ: (номер, [SQL]). Добавлять только В КОНЕЦ, старые не менять.
# ---------------------------------------------------------------------------
MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [
        # Монстры — отдельные агенты со своей внешностью, эмоцией, набором органов и памятью.
        """CREATE TABLE IF NOT EXISTS monsters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            seed INTEGER NOT NULL,             -- стабильный seed внешности (одинаковая внешность после перезапуска)
            emotion TEXT NOT NULL,             -- happy | angry | sad | neutral
            personality TEXT,
            voice_id TEXT,
            appearance TEXT,                   -- JSON (необязательно; внешность выводится из seed)
            status TEXT NOT NULL,              -- forming | alive | failed | retired
            version INTEGER DEFAULT 1,
            tasks_ok INTEGER DEFAULT 0,
            tasks_failed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            last_used_at TEXT
        )""",
        """CREATE TABLE IF NOT EXISTS monster_capabilities (
            monster_id INTEGER NOT NULL,
            capability TEXT NOT NULL,
            version TEXT,
            category TEXT,                     -- визуальный апгрейд: scanner | eye | arm | voice | holo | calc | quill | core
            source TEXT,                       -- built (вырастил сам) | library (взял готовый из реестра)
            task_id INTEGER,
            installed_at TEXT NOT NULL,
            PRIMARY KEY (monster_id, capability)
        )""",
        """CREATE TABLE IF NOT EXISTS monster_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monster_id INTEGER NOT NULL,
            version INTEGER NOT NULL,
            kind TEXT NOT NULL,                -- born | learned | failed
            capability TEXT,
            description TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS monster_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            monster_id INTEGER NOT NULL,
            task_id INTEGER,
            text TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        "ALTER TABLE tasks ADD COLUMN monster_id INTEGER",
        "ALTER TABLE usage ADD COLUMN model TEXT",
        "ALTER TABLE usage ADD COLUMN ref TEXT",          # id разговора у провайдера (для уточнения стоимости)
    ]),
    (2, [
        # Память со сроком годности: «я знаю КАК» (навыки) хранится в реестре, а «у меня есть СВЕЖИЕ данные» — здесь.
        """CREATE TABLE IF NOT EXISTS knowledge (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,              -- global | monster:<id>
            category TEXT NOT NULL,           -- dynamic | stable | procedural | task | preference
            skill TEXT,                       -- какой навык/слот породил запись
            key TEXT NOT NULL,                -- хеш (навык + слот + параметры)
            params TEXT,                      -- JSON параметров запроса
            content TEXT NOT NULL,            -- JSON результата
            source TEXT,                      -- откуда данные (навык vX / модель)
            created_at TEXT NOT NULL,
            expires_at TEXT,                  -- NULL — не устаревает
            last_verified_at TEXT,
            hits INTEGER DEFAULT 0,
            UNIQUE(scope, key)
        )""",
        # Режим исполнения задачи (deterministic / light / partial / full / team / ...) и выбранная стратегия
        "ALTER TABLE tasks ADD COLUMN mode TEXT",
        "ALTER TABLE tasks ADD COLUMN strategy TEXT",
        "ALTER TABLE tasks ADD COLUMN monster_ids TEXT",
        # Права и видимость навыков, закрепление версии у монстра
        "ALTER TABLE capabilities ADD COLUMN visibility TEXT DEFAULT 'global'",
        "ALTER TABLE capabilities ADD COLUMN owner_monster_id INTEGER",
        "ALTER TABLE monster_capabilities ADD COLUMN pinned_version TEXT",
        "ALTER TABLE monster_capabilities ADD COLUMN enabled INTEGER DEFAULT 1",
    ]),
    (3, [
        # Рецепт = ПРОЦЕДУРА, а не ответ: параметры запроса, на котором он родился, и значения, которые его код
        # ошибочно «зашил» (bound) — такой рецепт нельзя запускать для запроса с другими значениями.
        "ALTER TABLE recipes ADD COLUMN params TEXT",
        "ALTER TABLE recipes ADD COLUMN source_text TEXT",
        "ALTER TABLE recipes ADD COLUMN bound TEXT",
    ]),
    (4, [
        # ПАКЕТЫ ЗНАНИЙ: предметные знания (роль VoIP, кибербезопасность, UX/UI…) отдельно от органов (кода).
        # Один орган (метод) работает с сотнями пакетов; новая профессия = новый пакет, а НЕ новый орган.
        """CREATE TABLE IF NOT EXISTS knowledge_packs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,               -- для какого метода: interview_role, ...
            key TEXT NOT NULL,                -- нормализованный идентификатор: voip-engineer
            title TEXT NOT NULL,              -- VoIP Engineer
            domain TEXT, specialization TEXT,
            aliases TEXT,                     -- JSON: синонимы/аббревиатуры для распознавания (voip, voice over ip, sip engineer…)
            content TEXT NOT NULL,            -- JSON: competencies, topics[{name, questions[{q, level, kind, answer_hint}]}], exercises, rubric…
            source TEXT,                      -- curated | llm:<model> | user
            validation_status TEXT,           -- curated | validated | needs_review
            version INTEGER NOT NULL DEFAULT 1,
            uses INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            expires_at TEXT,                  -- когда стоит освежить (требования рынка меняются); NULL — не устаревает
            UNIQUE(kind, key)
        )""",
        # Запись исполнения: режим (instant/light/smart/build), навык, пакет знаний, создан ли орган, качество, задержки
        "ALTER TABLE tasks ADD COLUMN metrics TEXT",
    ]),
    (5, [
        # КЭШ ОТВЕТОВ МОДЕЛИ: тот же запрос (тот же промпт и модель) — проверенный ответ из памяти, 0 токенов.
        # Записи привязаны к задаче: если задача провалилась, её ответы удаляются (неудачный план не повторится).
        """CREATE TABLE IF NOT EXISTS llm_cache (
            key TEXT PRIMARY KEY,             -- sha256(провайдер, модель, назначение, системный промпт, запрос)
            purpose TEXT, model TEXT,
            response TEXT NOT NULL,           -- проверенный JSON-ответ
            tokens INTEGER DEFAULT 0,         -- сколько токенов стоил исходный вызов (столько экономит каждый повтор)
            seconds REAL DEFAULT 0,
            task_id INTEGER,
            hits INTEGER DEFAULT 0,
            created_at TEXT NOT NULL, expires_at TEXT
        )""",
    ]),
    (6, [
        # КАТАЛОГ АГЕНТОВ SOKOSUMI (локальная копия): подбор агента под потребность — в памяти, за миллисекунды, без ИИ
        """CREATE TABLE IF NOT EXISTS sokosumi_agents (
            id TEXT PRIMARY KEY,
            name TEXT, description TEXT,
            tags TEXT,                        -- JSON-список тегов
            price REAL,                       -- кредиты за задание (если Sokosumi отдаёт)
            status TEXT,
            input_fields TEXT,                -- JSON-схема полей (кэш; подгружается заранее для выбранного агента)
            raw TEXT,
            fetched_at TEXT NOT NULL
        )""",
    ]),
    (7, [
        # СВЯЗКА С ИНТЕРФЕЙСОМ monster-lab (lab_api.py): их «монстр темы» = наш монстр; их «запуск» = цепочка наших задач
        """CREATE TABLE IF NOT EXISTS lab_monsters (
            sid TEXT PRIMARY KEY,             -- id монстра в их интерфейсе: weather-monster
            monster_id INTEGER NOT NULL,      -- наш монстр (monsters.id)
            topic TEXT, purpose TEXT, archetype TEXT, language TEXT DEFAULT 'en',
            jobs TEXT,                        -- JSON: задачи, которым монстр «обучен» (из брифа кузницы)
            published INTEGER DEFAULT 0,
            voice_agent_id TEXT,              -- голосовой агент ElevenLabs для живого звонка
            version INTEGER DEFAULT 1,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS lab_runs (
            rid TEXT PRIMARY KEY,             -- id запуска в их интерфейсе
            sid TEXT NOT NULL,
            text TEXT NOT NULL,               -- исходный запрос
            tasks TEXT NOT NULL,              -- JSON: наши задачи по порядку (ответ на вопрос/подтверждение — новая задача)
            state TEXT NOT NULL,              -- JSON: phase, speech, решения пользователя
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
    ]),
]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.conn: sqlite3.Connection | None = None
        self.connect()

    def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._seed()
        self._migrate()

    def _migrate(self) -> None:
        """Миграции схемы. Существующие данные сохраняются: применяются только недостающие шаги по порядку."""
        with self.lock:
            row = self.one("SELECT value FROM meta WHERE key='schema_version'")
            current = int(row["value"]) if row else 0
            for version, statements in MIGRATIONS:
                if version <= current:
                    continue
                for sql in statements:
                    try:
                        self.conn.execute(sql)
                    except sqlite3.OperationalError as exc:      # колонка уже есть (ручная правка) — не страшно
                        if "duplicate column" not in str(exc):
                            raise
                self.conn.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('schema_version',?)", (str(version),))
                self.conn.commit()

    def _seed(self) -> None:
        """Поколение 0: ноль капабилити."""
        with self.lock:
            if not self.one("SELECT 1 AS x FROM meta WHERE key='generation'"):
                self.execute("INSERT INTO meta(key,value) VALUES('generation','0')")
                self.execute(
                    "INSERT INTO generations(generation,capabilities_total,active,retired,note,created_at)"
                    " VALUES(0,0,0,0,'birth',?)", (now(),))

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self.lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur.lastrowid

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def reset(self) -> None:
        """Полный сброс до поколения 0 (кнопка Demo → Reset)."""
        with self.lock:
            self.conn.close()
            for suffix in ("", "-wal", "-shm"):
                p = Path(str(self.path) + suffix)
                if p.exists():
                    p.unlink()
            self.connect()


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default
