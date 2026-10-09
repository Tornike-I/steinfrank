"""ТЕАТР ЛАБОРАТОРИИ (идея forge/theatrics.py из проекта коллеги): звуки монстров и сцена рождения от ElevenLabs.

Экономичнее, чем у коллеги:
  * звуки «пришёл / работает / готово» общие для ХАРАКТЕРА монстра (4 характера × 3 звука = 12 файлов на всю лабораторию),
    генерируются один раз (ElevenLabs Sound Effects) и потом только проигрываются;
  * сценарий рождения (Франкенштейн, Игорь, монстр) собирается по шаблону — БЕЗ вызова модели; ElevenLabs только озвучивает
    его диалогом (Text to Dialogue, eleven_v3) — один раз на монстра, затем файл хранится.
Всё делается в фоне и не задерживает задачу; без ключа или без прав — тихо выключается (событие THEATRICS_FAILED).
"""
import contextvars
import os
import threading
from pathlib import Path

import httpx

from . import config
from .usage import Ledger

API = "https://api.elevenlabs.io/v1"
ROOT = config.DATA_DIR / "theatrics"
ENABLED = os.getenv("FRANK_THEATRICS", "1").strip().lower() in ("1", "true", "yes")
# стандартные голоса ElevenLabs (доступны любому ключу); можно заменить своими
VOICE_FRANKENSTEIN = os.getenv("FRANK_VOICE_FRANKENSTEIN", "onwK4e9ZLuTAKqWW03F9")   # Daniel — глубокий, британский
VOICE_IGOR = os.getenv("FRANK_VOICE_IGOR", "N2lVS1w4EtoT3dr4eOWO")                   # Callum — хриплый
SOUND_SECONDS = {"arrive": 2.5, "working": 6.0, "done": 1.5}
SOUNDS = {
    "happy": {"arrive": "light bouncy footsteps and a cheerful little creature chirp",
              "working": "playful tinkering with springs and tiny bells in a laboratory, steady rhythm",
              "done": "cheerful two-note chime and a happy squeak"},
    "angry": {"arrive": "heavy stomping footsteps and a low angry growl",
              "working": "aggressive hammering on metal with crackling electric sparks, steady rhythm",
              "done": "loud metal slam followed by a satisfied snarl"},
    "sad": {"arrive": "slow dragging footsteps and a long tired sigh",
            "working": "slow gloomy creaking machinery and dripping water in a dungeon, steady rhythm",
            "done": "soft low bell toll and a quiet sigh"},
    "neutral": {"arrive": "mechanical footsteps with servo motor whirs",
                "working": "steady electrical humming and clicking relays in a mad scientist laboratory",
                "done": "short electric zap and a relay click"},
}
TAG = {"happy": "[laughs]", "angry": "[growls]", "sad": "[sighs]", "neutral": "[clears throat]"}
TWISTS = [
    ("[shouts] The lever is stuck! Igor, kick it!", "[shouts] Páka se zasekla! Igore, kopni do ní!"),
    ("[shouts] More lightning! The storm is too weak!", "[shouts] Víc blesků! Bouřka je moc slabá!"),
    ("[gasps] Igor, that is the wrong arm!", "[gasps] Igore, to je špatná ruka!"),
    ("[whispers] The power flickers... [shouts] now!", "[whispers] Proud bliká... [shouts] teď!"),
    ("[shouts] Igor, come out from behind the table!", "[shouts] Igore, vylez zpoza stolu!"),
]
IGOR = [("[gasps] Master... it moves!", "[gasps] Mistře... ono se hýbe!"), ("[laughs] It's alive! It's alive!", "[laughs] Žije! Žije!")]


def birth_script(name: str, emotion: str, task: str, lang: str, seed: int) -> list[dict]:
    """Три коротких реплики (до ~200 символов) — без вызова модели, разнообразие — от seed монстра."""
    cs = lang == "cs"
    twist = TWISTS[seed % len(TWISTS)][1 if cs else 0]
    igor = IGOR[(seed // 7) % len(IGOR)][1 if cs else 0]
    job = " ".join(str(task).split())[:60].rstrip(" ,.;:") or ("help you" if not cs else "pomáhat")
    monster = (f"{TAG.get(emotion, '')} Jsem {name}. Jsem tu kvůli: {job}." if cs
               else f"{TAG.get(emotion, '')} I am {name}. I was made for: {job}.").strip()
    return [{"speaker": "frankenstein", "text": twist}, {"speaker": "igor", "text": igor}, {"speaker": "monster", "text": monster}]


class Theatrics:
    def __init__(self, ledger: Ledger | None, bus=None, transport: httpx.BaseTransport | None = None, root: Path | None = None):
        self.ledger, self.bus, self.transport = ledger, bus, transport
        self.root = root or ROOT
        self._lock = threading.Lock()

    def enabled(self) -> bool:
        return ENABLED and bool(config.ELEVENLABS_API_KEY)

    # ------------------------------------------------------------------ пути и ссылки
    def _sound_path(self, emotion: str, name: str) -> Path:
        return self.root / "sounds" / emotion / f"{name}.mp3"

    def _birth_path(self, monster_id: int) -> Path:
        return self.root / "monsters" / str(monster_id) / "birth.mp3"

    def urls(self, monster: dict) -> dict:
        """Что уже готово для монстра (интерфейс проигрывает только существующие файлы)."""
        emo = monster.get("emotion", "neutral")
        out = {"sounds": {n: f"/api/theatrics/sounds/{emo}/{n}.mp3" for n in SOUND_SECONDS if self._sound_path(emo, n).exists()},
               "birth": f"/api/theatrics/monsters/{monster['id']}/birth.mp3" if self._birth_path(monster["id"]).exists() else None}
        return out

    def file(self, rel: str) -> Path | None:
        p = (self.root / rel).resolve()
        return p if p.is_file() and self.root.resolve() in p.parents and p.suffix == ".mp3" else None

    # ------------------------------------------------------------------ генерация
    def _post(self, path: str, body: dict) -> bytes:
        with httpx.Client(timeout=120, transport=self.transport) as c:
            r = c.post(API + path, params={"output_format": "mp3_44100_128"}, json=body,
                       headers={"xi-api-key": config.ELEVENLABS_API_KEY})
        if r.status_code >= 400:
            from .eleven import api_error
            raise api_error(r, f"ElevenLabs {path}")
        return r.content

    def ensure_sounds(self, emotion: str) -> list[str]:
        """Звуки характера — один раз на всю лабораторию."""
        made = []
        for name, seconds in SOUND_SECONDS.items():
            p = self._sound_path(emotion, name)
            if p.exists():
                continue
            body = {"text": SOUNDS.get(emotion, SOUNDS["neutral"])[name], "duration_seconds": seconds,
                    "model_id": "eleven_text_to_sound_v2"} | ({"loop": True} if name == "working" else {})
            audio = self._post("/sound-generation", body)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(audio)
            made.append(name)
            if self.ledger:
                self.ledger.record("sfx", purpose=f"sound:{emotion}:{name}", provider="elevenlabs", model="eleven_text_to_sound_v2",
                                   seconds=seconds)
        return made

    def ensure_birth(self, monster: dict, task: str, lang: str) -> bool:
        """Сцена рождения монстра — один раз (сценарий без модели, озвучка диалогом)."""
        p = self._birth_path(monster["id"])
        if p.exists():
            return False
        lines = birth_script(monster["name"], monster.get("emotion", "neutral"), task, lang, int(monster.get("seed") or 0))
        voices = {"frankenstein": VOICE_FRANKENSTEIN, "igor": VOICE_IGOR, "monster": monster.get("voice_id") or VOICE_IGOR}
        body = {"inputs": [{"text": l["text"], "voice_id": voices[l["speaker"]]} for l in lines], "model_id": "eleven_v3"}
        if lang == "cs":
            body["language_code"] = "cs"
        audio = self._post("/text-to-dialogue", body)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(audio)
        if self.ledger:
            self.ledger.record("tts", purpose="birth_scene", provider="elevenlabs", model="eleven_v3",
                               chars=sum(len(l["text"]) for l in lines))
        return True

    def prepare(self, monster: dict, *, task: str = "", lang: str = "en", birth: bool = False, task_id: int | None = None) -> None:
        """Синхронно (для тестов); в приложении — prepare_async."""
        if not self.enabled():
            return
        with self._lock:
            try:
                made = self.ensure_sounds(monster.get("emotion", "neutral"))
                born = self.ensure_birth(monster, task, lang) if birth else False
                if made or born:
                    self._emit("THEATRICS_READY", task_id, monster_id=monster["id"], sounds=made, birth=born, **self.urls(monster))
            except Exception as exc:  # noqa: BLE001 — театр не должен мешать работе
                self._emit("THEATRICS_FAILED", task_id, monster_id=monster["id"], error=str(exc)[:300])

    def prepare_async(self, monster: dict, **kw) -> None:
        if self.enabled():
            ctx = contextvars.copy_context()            # расход озвучки записывается на текущую задачу
            threading.Thread(target=ctx.run, args=(self.prepare, monster), kwargs=kw, daemon=True).start()

    def _emit(self, typ: str, task_id, **data) -> None:
        if self.bus:
            self.bus.emit(typ, task_id=task_id, **data)
