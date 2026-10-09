"""Общие настройки тестов.

Тесты НИКОГДА не трогают ваши настоящие данные и ключи:
  * данные пишутся во временную папку (FRANK_DATA_DIR), а не в backend/data;
  * все ключи провайдеров обнуляются ДО загрузки backend/.env (python-dotenv не перезаписывает уже заданные
    переменные), поэтому ни один тест не обращается к настоящим ElevenLabs / Sokosumi / OpenAI.
"""
import os
import sys
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp(prefix="frank-test-")
os.environ["FRANK_DATA_DIR"] = _tmp
for key in ("ELEVENLABS_API_KEY", "ELEVENLABS_BRAIN_AGENT_ID", "ELEVENLABS_VOICE_ID", "ELEVENLABS_VOICE_POOL",
            "SOKOSUMI_API_KEY", "SOKOSUMI_AGENT_ID", "OPENAI_API_KEY", "LLM_PROVIDER", "APP_ACCESS_TOKEN",
            "ELEVENLABS_LLM", "ELEVENLABS_LLM_FAST", "ALLOW_NETWORK_CAPABILITIES", "FRANK_IMPORT_DATA_DIR",
            "FRANK_LAB_TYPED_TTS", "FRANK_LAB_GENERATED_AUDIO", "FRANK_LAB_RESEARCH_MODE", "FRANK_LAB_FAST_RESPONSES"):
    os.environ[key] = ""
os.environ["FRANK_USE_TEMPLATES"] = "0"       # старые сценарии проверяют генерацию моделью; шаблоны включаются в своих тестах
os.environ["SANDBOX_MODE"] = "subprocess"   # тесты не должны зависеть от Docker
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
