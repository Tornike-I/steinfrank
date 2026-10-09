"""БАРЬЕР СОЗДАНИЯ ОРГАНОВ — решаем БЕЗ вызова модели, нужно ли строить/переписывать код.

Новый орган (или мутация) = минуты ожидания и тысячи токенов. Поэтому по умолчанию обычный запрос к СУЩЕСТВУЮЩЕМУ
монстру не уходит в генерацию кода. Строим без вопросов только когда:
  * пользователь явно просит научиться («learn …», кнопка «Upgrade», подтверждение в интерфейсе);
  * пользователь создаёт НОВОГО монстра (это и есть просьба вырастить ему органы);
  * орган покрывает проверенный шаблон (сборка за секунды, 0 токенов на код);
  * работает команда монстров — планировщик строит только то, чего нет ни у кого.
Иначе — решение "confirm": объясняем, чего не хватает, сколько это обычно стоит (по ИЗМЕРЕННЫМ прошлым сборкам)
и ждём подтверждения. Новая профессия, город, дата, уровень, формат — это параметры или знания, не повод для органа.
"""
import re
from dataclasses import asdict, dataclass

from . import templates
from .db import Database, loads


# явная просьба поработать над самим монстром/органами («почини», «улучши», «научись»)
EXPLICIT_RE = re.compile(r"\b(learn|teach|repair|fix|improve|upgrade|heal|build|naučit|nauč|oprav|vylepš|postav|"
                         r"научи|почин|исправ|улучш|построй)\w*", re.I)


@dataclass
class GateDecision:
    decision: str                          # reuse | template | build | confirm
    selected_skill: list
    missing_functionality: list
    missing_knowledge: list
    reasoning_summary: str
    estimated_cost: dict | None            # {"tokens", "llm_calls", "basis"} — по прошлым сборкам; None — замеров нет
    estimated_latency: float | None        # секунды, среднее прошлых сборок
    user_confirmation_required: bool

    def as_dict(self) -> dict:
        return asdict(self)


def build_estimate(db: Database) -> tuple[dict | None, float | None]:
    """Средняя ИЗМЕРЕННАЯ стоимость задач, в которых строились органы (не выдумываем, если замеров нет)."""
    rows = db.query(
        "SELECT t.duration d, COALESCE((SELECT SUM(prompt_tokens+completion_tokens) FROM usage u WHERE u.task_id=t.id AND kind='llm'),0) tok,"
        " COALESCE((SELECT COUNT(*) FROM usage u WHERE u.task_id=t.id AND kind='llm'),0) n FROM tasks t"
        " WHERE t.status='completed' AND EXISTS (SELECT 1 FROM evolution_events e WHERE e.task_id=t.id AND"
        " e.event_type IN ('CAPABILITY_BUILD_STARTED','CAPABILITY_MUTATION_STARTED')) ORDER BY t.id DESC LIMIT 20")
    if not rows:
        return None, None
    k = len(rows)
    return ({"tokens": round(sum(r["tok"] for r in rows) / k), "llm_calls": round(sum(r["n"] for r in rows) / k, 1),
             "basis": f"average of {k} measured build task(s)"}, round(sum(r["d"] or 0 for r in rows) / k, 1))


def decide(plan: dict, *, mode: str, allow_build: bool, upgrade: bool, monster: dict | None, db: Database,
           text: str = "", first_organs_free: bool = True) -> GateDecision:
    reqs = plan.get("requirements") or []
    reuse = sorted({c for r in reqs if r["decision"] in ("REUSE", "COMPOSE") for c in r.get("capabilities") or []})
    creates = [r for r in reqs if r["decision"] == "CREATE"]
    mutates = [r for r in reqs if r["decision"] == "MUTATE"]
    if not creates and not mutates:
        return GateDecision("reuse", reuse, [], [], "existing organs cover the task", None, None, False)
    by_template = [r for r in creates if templates.match_spec(r.get("spec") or {})]
    expensive = [r for r in creates if r not in by_template] + mutates
    missing = [(r.get("spec") or {}).get("name") or (r.get("capabilities") or ["?"])[0] for r in expensive]
    if not expensive:
        return GateDecision("template", reuse, [r["spec"]["name"] for r in by_template], [],
                            "verified templates cover the missing organs (no code generation)", None, None, False)
    explicit = allow_build or upgrade or plan.get("intent") == "learn" or bool(EXPLICIT_RE.search(text))
    owned = [c["name"] for c in (monster or {}).get("capabilities", [])]
    if explicit or mode in ("new", "team") or (not owned and first_organs_free):
        why = ("you asked the monster to learn" if explicit else "a new monster grows its first organs" if mode == "new" or not owned
               else "the team builds only what no member can do")
        return GateDecision("build", reuse, missing, [], why, None, None, False)
    cost, latency = build_estimate(db)
    needs = "; ".join(f"{(r.get('need') or {}).get('en', '')}" for r in expensive)[:300]
    has = (f"{monster['name']} already has {', '.join(owned[:6])}. " if owned else
           f"{(monster or {}).get('name', 'The monster')} has no organ for this yet. ")
    return GateDecision(
        "confirm", owned, missing, [],
        f"{has}The plan wants to build or rewrite code ({', '.join(missing)}) for: "
        f"{needs}. Building an organ is the expensive path; confirm only if this is genuinely NEW functionality, not just a new "
        "city, profession, date or wording.", cost, latency, True)
