import os
from pathlib import Path

PKG_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PKG_ROOT.parent
DATA_DIR = Path(os.environ.get("FRANK_DATA_DIR", PKG_ROOT / "data"))
MONSTERS_DIR = Path(os.environ.get("FRANK_MONSTERS_DIR", PKG_ROOT / "monsters"))
ARTIFACTS_DIR = DATA_DIR / "artifacts"


def load_dotenv():
    for env in (PKG_ROOT / ".env", REPO_ROOT / ".env"):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"'))


load_dotenv()


def env(name, default=None):
    return os.environ.get(name) or default


DESIGN_MODEL = env("FRANK_DESIGN_MODEL", "gpt-5.4-mini")
RUNTIME_MODEL = env("MONSTER_LLM_MODEL", "gpt-4.1-nano")
IMAGE_MODEL = env("MONSTER_IMAGE_MODEL", "gpt-image-1")
MODERATION_MODEL = env("MODERATION_MODEL", "omni-moderation-latest")
VOICE_LLM = env("MONSTER_VOICE_LLM", "gemini-2.5-flash")
VOICE_ID = env("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
TTS_MODEL = env("ELEVENLABS_TTS_MODEL", "eleven_flash_v2_5")
SPEECH_TTS_MODEL = env("ELEVENLABS_SPEECH_MODEL", "eleven_v3")
USER_AGENT = env("FRANK_USER_AGENT", "SteinfrankMonster/1.0 (hackathon workflow bot)")
PUBLIC_BASE_URL = env("FRANK_PUBLIC_BASE_URL", "http://localhost:8000")

# Without this, run-time moderation is skipped when no OpenAI key is configured.
REQUIRE_MODERATION = env("FRANK_REQUIRE_MODERATION", "0") == "1"

CAPS = {
    "max_credits": int(env("FRANK_MAX_CREDITS_PER_RUN", "500")),
    "max_llm_tokens": int(env("FRANK_MAX_LLM_TOKENS_PER_RUN", "20000")),
    "max_steps": 30,
    "max_fanout": 20,
    "max_images": 2,
    "max_tts_chars": 5000,
    "llm_max_tokens": 1500,
    "max_notifications": 5,
    "daily_credits": int(env("FRANK_DAILY_CREDIT_CAP", "1000")),
}

# About 60 seconds of speech; the spoken verdict must fit in this, the full report goes on screen.
SPEECH_MAX_CHARS = 900
CONFIRM_ABOVE_CREDITS = float(env("FRANK_CONFIRM_ABOVE_CREDITS", "100"))
MIN_WATCH_SECONDS = int(env("FRANK_MIN_WATCH_SECONDS", "300"))
NTFY_BASE_URL = env("NTFY_BASE_URL", "https://ntfy.sh")
RESEND_FROM = env("RESEND_FROM", "Steinfrank <onboarding@resend.dev>")

SOKOSUMI_POLL_SECONDS = 10
