import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["FRANK_DATA_DIR"] = tempfile.mkdtemp(prefix="frank-test-")
os.environ["FRANK_MONSTERS_DIR"] = tempfile.mkdtemp(prefix="frank-monsters-")
os.environ["FRANK_MIN_WATCH_SECONDS"] = "60"
# Empty rather than unset, so config's load_dotenv (setdefault) can't pull the real keys back in.
for key in ("OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SOKOSUMI_API_KEY", "TAVILY_API_KEY", "RESEND_API_KEY", "TELEGRAM_BOT_TOKEN"):
    os.environ[key] = ""
