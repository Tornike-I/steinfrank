"""Монстры — постоянные агенты, которых собирает Frankenstein.

У каждого монстра:
  * стабильный seed  -> одна и та же внешность после перезагрузки (внешность рисует фронтенд по seed: lab/monsterGen.ts);
  * эмоция (happy/angry/sad/neutral) -> поза, мимика, свет и настройки голоса;
  * свой голос ElevenLabs (из пула, по seed);
  * набор органов — ссылки на капабилити ОБЩЕГО реестра. Орган, однажды выращенный любым монстром,
    другому монстру достаётся без повторной генерации (source = library);
  * история версий (Version 1 — born, Version 2 — learned pdf_reader …) и память о задачах.

Визуальный апгрейд органа (сканер, цифровой глаз, мехрука…) определяется категорией (categorize) —
только для РЕАЛЬНО установленных органов.
"""
import random
import re

from . import config, skillmeta
from .db import Database, now
from .registry import Registry

EMOTIONS = ("happy", "angry", "sad", "neutral")

TITLES = ["Professor", "Doctor", "Baron", "Countess", "Sir", "Madame", "Captain", "Igor the", "Lady", "Herr"]
NAMES = ["Volt", "Ampere", "Galvani", "Tesla", "Ohm", "Sparks", "Rivet", "Coil", "Fuse", "Cog", "Bolt", "Dynamo",
         "Faraday", "Watt", "Gauss", "Kelvin", "Joule", "Morse", "Crookes", "Leyden", "Static", "Arc", "Flux", "Henry"]

# Классические готовые голоса ElevenLabs. Если голос недоступен на аккаунте, tts.py откатится на ELEVENLABS_VOICE_ID.
DEFAULT_VOICES = ["pNInz6obpgDQGcFmaJgB", "ErXwobaYiN019PkySvjV", "VR6AewLTigWG4xSOukaG", "21m00Tcm4TlvDq8ikWAM",
                  "AZnzlk1XvdvUeBnXmlld", "EXAVITQu4vr4xnSDxMaL", "TxGEqnHWrfWFTfGW9XjX", "yoZ06aMxZJJ28mfd3POQ"]

# категория апгрейда -> ключевые слова в имени/назначении органа
CATEGORIES = [
    ("scanner", ("pdf", "document", "ocr", "scan", "page", "parse", "extract")),
    ("holo", ("excel", "xlsx", "spreadsheet", "csv", "table", "data", "statistic", "analy", "chart", "anomal", "transaction")),
    ("eye", ("web", "search", "http", "url", "research", "crawl", "fetch", "browse")),
    ("arm", ("code", "python", "program", "script", "compile", "build")),
    ("voice", ("voice", "audio", "speech", "sound", "speak", "transcri")),
    ("calc", ("currency", "convert", "rate", "money", "price", "tuition", "math", "calc", "rank", "score", "compar")),
    ("quill", ("report", "write", "summar", "generate", "document_writer", "render", "format")),
]


def categorize(name: str, purpose: str = "") -> str:
    hay = f"{name} {purpose}".lower()
    for cat, words in CATEGORIES:
        if any(w in hay for w in words):
            return cat
    return "core"


def _tokens(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-zа-яěščřžýáíéůú0-9]{4,}", text.lower())}


class Monsters:
    def __init__(self, db: Database, registry: Registry):
        self.db, self.registry = db, registry

    # ------------------------------------------------------------------ создание
    def create(self, emotion: str, *, seed: int | None = None, task_id: int | None = None) -> dict:
        emotion = emotion if emotion in EMOTIONS else "neutral"
        seed = seed if seed is not None else random.SystemRandom().randrange(1, 2**31 - 1)
        rnd = random.Random(seed)
        base = f"{rnd.choice(TITLES)} {rnd.choice(NAMES)}"
        taken = {r["name"] for r in self.db.query("SELECT name FROM monsters")}
        name, n = base, 2
        while name in taken:                       # уникальные имена: Professor Volt II, III …
            name = f"{base} {['II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX'][min(n - 2, 7)]}" if n <= 9 else f"{base} {n}"
            n += 1
        pool = config.ELEVENLABS_VOICE_POOL or DEFAULT_VOICES
        voice = pool[rnd.randrange(len(pool))]
        personality = {"happy": "cheerful and energetic", "angry": "intense and blunt", "sad": "melancholic and gentle",
                       "neutral": "calm and precise"}[emotion]
        mid = self.db.execute(
            "INSERT INTO monsters(name,seed,emotion,personality,voice_id,status,version,created_at,last_used_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)", (name, seed, emotion, personality, voice, "forming", 1, now(), now()))
        if task_id:
            self.db.execute("UPDATE tasks SET monster_id=? WHERE id=?", (mid, task_id))
        self._history(mid, 1, "born", None, "Assembled on the operating table")
        return self.get(mid)

    # ------------------------------------------------------------------ чтение
    def get(self, monster_id: int) -> dict | None:
        m = self.db.one("SELECT * FROM monsters WHERE id=?", (monster_id,))
        if not m:
            return None
        caps = []
        for r in self.db.query("SELECT * FROM monster_capabilities WHERE monster_id=? ORDER BY installed_at", (monster_id,)):
            c = self.registry.get(r["capability"]) or {}
            caps.append({"name": r["capability"], "version": c.get("version", r["version"]), "category": r["category"],
                         "source": r["source"], "installed_at": r["installed_at"],
                         "purpose_en": c.get("purpose_en", ""), "purpose_cs": c.get("purpose_cs", ""),
                         "tests": c.get("tests", 0), "tests_passed": c.get("tests_passed", 0),
                         "status": c.get("status", "missing"), "usage_count": c.get("usage_count", 0),
                         # для библиотеки: закреплённая версия, сеть, права, «умеет ли навык сам понимать запросы»
                         "pinned_version": r["pinned_version"], "network": bool(c.get("network")),
                         "visibility": c.get("visibility", "global"), "owner_monster_id": c.get("owner_monster_id"),
                         "callable": bool((c.get("skill") or {}).get("callable")),
                         "versions": [v["version"] for v in c.get("versions", []) if v["status"] in ("active", "retired")]})
        history = self.db.query("SELECT version, kind, capability, description, created_at FROM monster_history"
                                " WHERE monster_id=? ORDER BY id", (monster_id,))
        mem = self.db.one("SELECT COUNT(*) n FROM monster_memory WHERE monster_id=?", (monster_id,))["n"]
        return {**m, "capabilities": caps, "history": history, "memories": mem}

    def list(self, include_failed: bool = False) -> list[dict]:
        rows = self.db.query("SELECT id FROM monsters WHERE status IN ('alive','forming')" +
                             ("" if not include_failed else " OR status='failed'") + " ORDER BY last_used_at DESC")
        return [self.get(r["id"]) for r in rows]

    def memory(self, monster_id: int, limit: int = 5) -> list[str]:
        return [r["text"] for r in self.db.query(
            "SELECT text FROM monster_memory WHERE monster_id=? ORDER BY id DESC LIMIT ?", (monster_id, limit))]

    # ------------------------------------------------------------------ обучение
    def attach(self, monster_id: int, capability: str, *, source: str, task_id: int | None) -> bool:
        """Добавить орган монстру. True — если это новое знание (версия монстра растёт)."""
        if self.db.one("SELECT 1 x FROM monster_capabilities WHERE monster_id=? AND capability=?", (monster_id, capability)):
            return False
        cap = self.registry.get(capability)
        if not cap or cap["status"] != "active":
            return False
        cat = categorize(capability, cap.get("purpose_en", ""))
        self.db.execute("INSERT INTO monster_capabilities(monster_id,capability,version,category,source,task_id,installed_at)"
                        " VALUES(?,?,?,?,?,?,?)", (monster_id, capability, cap["version"], cat, source, task_id, now()))
        self.db.execute("UPDATE monsters SET version=version+1 WHERE id=?", (monster_id,))
        v = self.db.one("SELECT version FROM monsters WHERE id=?", (monster_id,))["version"]
        self._history(monster_id, v, "learned", capability, cap.get("purpose_en") or cap.get("description", ""))
        return True

    def finish_task(self, monster_id: int, task_id: int, ok: bool, text: str, summary_en: str = "") -> None:
        m = self.db.one("SELECT status FROM monsters WHERE id=?", (monster_id,))
        if not m:
            return
        if ok:
            self.db.execute("UPDATE monsters SET tasks_ok=tasks_ok+1, last_used_at=?, status='alive' WHERE id=?", (now(), monster_id))
            self.db.execute("INSERT INTO monster_memory(monster_id,task_id,text,created_at) VALUES(?,?,?,?)",
                            (monster_id, task_id, f"Task: {text[:160]} -> {summary_en[:220]}", now()))
        else:
            # эксперимент не удался: только что собранный монстр не оживает (существующий просто записывает неудачу)
            status = "failed" if m["status"] == "forming" else m["status"]
            self.db.execute("UPDATE monsters SET tasks_failed=tasks_failed+1, last_used_at=?, status=? WHERE id=?",
                            (now(), status, monster_id))
            if status == "failed":
                self._history(monster_id, 1, "failed", None, "The experiment failed; the creature never woke up")

    def _history(self, mid: int, version: int, kind: str, capability: str | None, description: str) -> None:
        self.db.execute("INSERT INTO monster_history(monster_id,version,kind,capability,description,created_at)"
                        " VALUES(?,?,?,?,?,?)", (mid, version, kind, capability, description[:240], now()))

    # ------------------------------------------------------------------ права и совместное использование навыков
    def can_use(self, monster_id: int, cap: dict) -> tuple[bool, str]:
        """Монстр не получает навык автоматически только потому, что он есть у другого: проверяем права."""
        if cap.get("visibility") == "private" and cap.get("owner_monster_id") not in (None, monster_id):
            return False, "this skill is private to another monster"
        if cap.get("network") and not config.ALLOW_NETWORK_CAPABILITIES:
            return False, "this skill needs internet access, which the operator has disabled"
        return True, ""

    def assign(self, monster_id: int, capability: str) -> tuple[bool, str]:
        """Выдать монстру СУЩЕСТВУЮЩИЙ навык из общего реестра (без копирования реализации)."""
        cap = self.registry.get(capability)
        if not cap or cap["status"] != "active":
            return False, "unknown or retired skill"
        ok, why = self.can_use(monster_id, cap)
        if not ok:
            return False, why
        return (True, "") if self.attach(monster_id, capability, source="library", task_id=None) else (False, "already installed")

    def revoke(self, monster_id: int, capability: str) -> bool:
        row = self.db.one("SELECT 1 x FROM monster_capabilities WHERE monster_id=? AND capability=?", (monster_id, capability))
        if not row:
            return False
        self.db.execute("DELETE FROM monster_capabilities WHERE monster_id=? AND capability=?", (monster_id, capability))
        v = self.db.one("SELECT version FROM monsters WHERE id=?", (monster_id,))["version"]
        self._history(monster_id, v, "revoked", capability, "Skill access revoked by the operator")
        return True

    # ------------------------------------------------------------------ рекомендация: один монстр или команда
    @staticmethod
    def _skill_words(c: dict, cap: dict | None) -> set[str]:
        sk = (cap or {}).get("skill") or {}
        return skillmeta.tokens(" ".join([c["name"].replace("_", " "), c.get("purpose_en", "")] + sk.get("keywords", []) + sk.get("intents", [])))

    def recommend(self, text: str, limit: int = 3) -> list[dict]:
        """Эвристика БЕЗ вызова модели: совпадение слов задачи с РЕАЛЬНЫМИ навыками монстра (ключевые слова, намерения,
        назначение) и его памятью. Окончательно решает планировщик — здесь только подсказка пользователю."""
        words = skillmeta.tokens(text)
        out = []
        for m in self.list():
            matched, score = [], 0.0
            for c in m["capabilities"]:
                hit = words & self._skill_words(c, self.registry.get(c["name"]))
                if hit:
                    matched.append(c["name"])
                    score += len(hit)
            score += len(words & skillmeta.tokens(" ".join(self.memory(m["id"], 10)))) * 0.5
            if matched or score >= 1:
                out.append({"monster": {k: m[k] for k in ("id", "name", "seed", "emotion", "version", "tasks_ok")},
                            "matched": matched, "score": round(score, 2),
                            "coverage": round(len(matched) / max(1, len(m["capabilities"])), 2)})
        return sorted(out, key=lambda r: -r["score"])[:limit]

    def recommend_team(self, text: str) -> dict | None:
        """Команда, если РАЗНЫЕ части запроса покрываются навыками РАЗНЫХ монстров (жадное покрытие слов запроса)."""
        words = skillmeta.tokens(text)
        shown = set(re.findall(r"\w+", text.lower()))      # людям показываем целые слова запроса, а не служебные основы
        offers = []
        for m in self.list():
            if m["status"] != "alive":
                continue
            skills, covered = [], set()
            for c in m["capabilities"]:
                hit = words & self._skill_words(c, self.registry.get(c["name"]))
                if hit:
                    skills.append(c["name"])
                    covered |= hit
            if skills:
                offers.append((m, skills, covered))
        team, covered_all = [], set()
        while offers:
            m, skills, covered = max(offers, key=lambda o: len(o[2] - covered_all))
            if not covered - covered_all:
                break
            team.append({"monster": {k: m[k] for k in ("id", "name", "seed", "emotion", "version")}, "skills": skills,
                         "covers": sorted(shown & (covered - covered_all))})
            covered_all |= covered
            offers = [o for o in offers if o[0]["id"] != m["id"]]
            if len(team) == 4:
                break
        if len(team) < 2:
            return None
        return {"members": team, "uncovered": sorted(w for w in (words - covered_all) & shown if len(w) > 3)[:12],
                "note": "based on the monsters' registered skills; the planner decides the final split and builds only missing parts"}

    @staticmethod
    def intro(monster: dict, learned: list[dict]) -> dict:
        """Первая фраза монстра после пробуждения — из реальных органов, без вызова модели."""
        name = monster["name"]
        if learned:
            en = "; ".join(c.get("purpose_en") or c["name"].replace("_", " ") for c in learned[:3]).rstrip(".")
            cs = "; ".join(c.get("purpose_cs") or c["name"].replace("_", " ") for c in learned[:3]).rstrip(".")
            return {"en": f"I am {name}. I am ready. I have learned: {en}.", "cs": f"Jsem {name}. Jsem připraven. Naučil jsem se: {cs}."}
        return {"en": f"{name}, at your service.", "cs": f"{name} k vašim službám."}

    def to_event(self, m: dict) -> dict:
        return {"id": m["id"], "name": m["name"], "seed": m["seed"], "emotion": m["emotion"], "version": m["version"],
                "voice_id": m["voice_id"], "upgrades": [c["category"] for c in m["capabilities"]]}
