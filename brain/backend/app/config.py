"""Настройки Frankenstein.

Всё, что можно менять без правки кода, берётся из переменных окружения
(файл backend/.env, см. .env.example). Здесь же — все пути к данным.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent   # папка backend/
ROOT_DIR = BASE_DIR.parent                          # корень проекта
load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


# --- Пути (всё, что создаёт сам агент, лежит в backend/data) ---
DATA_DIR = Path(os.getenv("FRANK_DATA_DIR") or BASE_DIR / "data")
DB_PATH = DATA_DIR / "frankenstein.db"
# Optional read-only source with the original brain's learned skills/cache.  The
# lab keeps its own monsters and runs, but does not have to learn everything again.
_import_data = os.getenv("FRANK_IMPORT_DATA_DIR", "").strip()
IMPORT_DATA_DIR = Path(_import_data) if _import_data else None
CAPS_DIR = DATA_DIR / "capabilities"      # исходники установленных капабилити (для людей)
RUNS_DIR = DATA_DIR / "runs"              # временные рабочие папки песочницы
ARTIFACTS_DIR = DATA_DIR / "artifacts"    # файлы-результаты задач (PDF-отчёты и т.п.)
UPLOADS_DIR = DATA_DIR / "uploads"        # файлы, загруженные пользователем
DEMO_DIR = ROOT_DIR / "demo_data"         # образцы PDF для демо
SANDBOX_DIR = BASE_DIR / "sandbox"        # Dockerfile песочницы

# --- LLM: какой «мозг» используется ---
# sokosumi — агент с маркетплейса Sokosumi (хакатонный кредит); openai — запасной вариант для разработки.
SOKOSUMI_API_KEY = os.getenv("SOKOSUMI_API_KEY", "").strip()
SOKOSUMI_API_URL = (os.getenv("SOKOSUMI_API_URL", "") or "https://api.sokosumi.com/v1").rstrip("/")
SOKOSUMI_AGENT_ID = os.getenv("SOKOSUMI_AGENT_ID", "").strip()
SOKOSUMI_JOB_TIMEOUT_SEC = _int("SOKOSUMI_JOB_TIMEOUT_SEC", 900)
BUDGET_CREDITS = _int("SOKOSUMI_BUDGET_CREDITS", 5000)
CREDIT_USD = float(os.getenv("CREDIT_USD", "0.01") or 0.01)      # 5000 кредитов ≈ $50
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "").strip().lower()      # elevenlabs | sokosumi | openai | "" (авто)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1").strip()
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip() or None

# --- Голос (ElevenLabs) ---
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "").strip() or "21m00Tcm4TlvDq8ikWAM"
ELEVENLABS_MODEL = os.getenv("ELEVENLABS_MODEL", "").strip() or "eleven_multilingual_v2"
SPEECH_MAX_CHARS = _int("SPEECH_MAX_CHARS", 280)   # голос стоит денег: озвучиваем только краткое резюме
ELEVENLABS_API_URL = (os.getenv("ELEVENLABS_API_URL", "") or "https://api.elevenlabs.io").rstrip("/")
# Пул голосов для монстров (у каждого монстра свой голос, выбирается по seed). Пусто — все говорят ELEVENLABS_VOICE_ID.
ELEVENLABS_VOICE_POOL = [v.strip() for v in os.getenv("ELEVENLABS_VOICE_POOL", "").split(",") if v.strip()]
# «Мозг» на ElevenLabs Agents: текстовый агент, модель задаётся на каждый вызов (overrides).
ELEVENLABS_BRAIN_AGENT_ID = os.getenv("ELEVENLABS_BRAIN_AGENT_ID", "").strip()   # пусто — создаётся автоматически
ELEVENLABS_LLM = os.getenv("ELEVENLABS_LLM", "").strip() or "claude-sonnet-4-5"      # для кода, тестов, планов
ELEVENLABS_LLM_FAST = os.getenv("ELEVENLABS_LLM_FAST", "").strip() or "gemini-2.5-flash"   # для простых шагов
ELEVENLABS_BRAIN_TIMEOUT_SEC = _int("ELEVENLABS_BRAIN_TIMEOUT_SEC", 300)
# Распознавание речи (Scribe)
ELEVENLABS_STT_MODEL = os.getenv("ELEVENLABS_STT_MODEL", "").strip() or "scribe_v2"

# --- Безопасность ---
APP_ACCESS_TOKEN = os.getenv("APP_ACCESS_TOKEN", "").strip()      # если задан — API требует X-Frank-Token
ALLOW_NETWORK_CAPABILITIES = os.getenv("ALLOW_NETWORK_CAPABILITIES", "").strip().lower() in ("1", "true", "yes")
# Проверенные шаблоны органов (организованные методы: подготовка к собеседованию, погода). Если план требует орган,
# который покрывает шаблон, он ставится из шаблона за секунды и без генерации кода моделью (0 токенов на сборку).
USE_TEMPLATES = os.getenv("FRANK_USE_TEMPLATES", "1").strip().lower() in ("1", "true", "yes")
# Монстр без органов строит первые органы без подтверждения. В monster-lab монстр создаётся мгновенно и пустым,
# поэтому там это выключено (lab_api.attach): любая сборка кода — только после «да» человека.
FIRST_ORGANS_FREE = os.getenv("FRANK_FIRST_ORGANS_FREE", "1").strip().lower() in ("1", "true", "yes")
# The browser already plays the original instant WebAudio effects.  Extra generated
# audio is opt-in so forge/result endpoints never wait on ElevenLabs by default.
LAB_TYPED_TTS = os.getenv("FRANK_LAB_TYPED_TTS", "1").strip().lower() in ("1", "true", "yes")
LAB_GENERATED_AUDIO = os.getenv("FRANK_LAB_GENERATED_AUDIO", "0").strip().lower() in ("1", "true", "yes")
# Match Friend/frankenstein's spoken-verdict pipeline exactly. This affects only
# the final answer; live voice calls keep their own low-latency agent model.
LAB_SPEECH_MODEL = os.getenv("FRANK_LAB_SPEECH_MODEL", "").strip() or "eleven_v3"
LAB_SPEECH_MAX_CHARS = _int("FRANK_LAB_SPEECH_MAX_CHARS", 900)
# automatic = offer paid research for vague freshness hints; explicit = only
# when the user actually asks to search/research online. Friend uses explicit.
LAB_RESEARCH_MODE = os.getenv("FRANK_LAB_RESEARCH_MODE", "explicit").strip().lower()
LAB_FAST_RESPONSES = os.getenv("FRANK_LAB_FAST_RESPONSES", "1").strip().lower() in ("1", "true", "yes")

# Кэш проверенных ответов модели (тот же запрос -> 0 токенов). 0 — без срока годности.
LLM_CACHE_TTL_HOURS = int(os.getenv("FRANK_LLM_CACHE_TTL_HOURS", "168"))
# Бюджет токенов на ОДНУ задачу (защита от «застреваний»): обычная задача / задача, где строится или чинится орган.
# Превышение — задача честно останавливается вместо бесконечных повторов. 0 — без ограничения.
MAX_TOKENS_PER_TASK = int(os.getenv("FRANK_MAX_TOKENS_PER_TASK", "40000"))
MAX_TOKENS_PER_BUILD = int(os.getenv("FRANK_MAX_TOKENS_PER_BUILD", "150000"))

# СВЕЖИЕ ЗНАНИЯ через Sokosumi: агент-исследователь собирает актуальные данные из реальных источников ОДИН раз, результат
# становится пакетом знаний для всех монстров. Платно -> только после подтверждения пользователя, с потолками кредитов.
SOKOSUMI_RESEARCH_AGENT_ID = os.getenv("SOKOSUMI_RESEARCH_AGENT_ID", "").strip()
SOKOSUMI_MAX_CREDITS_PER_JOB = float(os.getenv("SOKOSUMI_MAX_CREDITS_PER_JOB", "300"))
SOKOSUMI_DAILY_CREDIT_CAP = float(os.getenv("SOKOSUMI_DAILY_CREDIT_CAP", "1000"))
RESEARCH_FRESH_DAYS = int(os.getenv("FRANK_RESEARCH_FRESH_DAYS", "30"))   # исследование свежее N дней — повторно не предлагаем
MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 25)

# --- Песочница ---
SANDBOX_MODE = os.getenv("SANDBOX_MODE", "auto").strip().lower()   # auto | docker | subprocess
SANDBOX_TIMEOUT_SEC = _int("SANDBOX_TIMEOUT_SEC", 60)
SANDBOX_MEMORY_MB = _int("SANDBOX_MEMORY_MB", 512)
DOCKER_IMAGE = "frankenstein-sandbox:1"

# --- Правила цикла самостроительства ---
MAX_REPAIRS = _int("MAX_REPAIRS", 3)                    # сколько раз чиним капабилити
MAX_WORKFLOW_ATTEMPTS = _int("MAX_WORKFLOW_ATTEMPTS", 3)  # сколько раз пробуем выполнить задачу
MIN_TESTS = _int("MIN_TESTS", 8)                        # меньше тестов — не устанавливаем
AUDIT_MAX_MUTATIONS = _int("AUDIT_MAX_MUTATIONS", 2)    # сколько капабилити мутирует self-audit
