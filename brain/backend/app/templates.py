"""ПРОВЕРЕННЫЕ ШАБЛОНЫ ОРГАНОВ — сборка за секунды без генерации кода моделью.

Шаблон — это обобщённый МЕТОД (подготовка к собеседованию по ЛЮБОЙ профессии, погода для ЛЮБОГО города), чьи предметные
знания приходят из пакетов знаний или от провайдера. Если план требует орган, который покрывает шаблон, орган ставится
из шаблона: те же тесты в песочнице, тот же реестр и версии, но 0 токенов и без 4-минутной генерации.

adopt() — безопасная миграция уже выученных органов: «узкий» орган пользователя получает НОВУЮ версию из шаблона
(старые версии остаются, откат — POST /api/capabilities/{name}/rollback). Каждый орган мигрируется один раз
(meta template_adopted:<name>), поэтому ручной откат пользователем не перезаписывается.
"""
import re
from pathlib import Path

from . import config

ROOT = Path(__file__).parent / "organ_templates"

TEMPLATES = {
    "interview": {
        "name": "prepare_interview",
        "version": 4,        # 2: дозаказ знаний; 3: спрашивает профессию; 4: раздел актуальных данных (Sokosumi)
        "match": re.compile(r"interview|pohovor|собеседован|интервью", re.I),
        "spec": {"description": "Universal interview preparation for any profession and seniority (knowledge from packs)",
                 "purpose_en": "Prepare users for job interviews in any profession and seniority, using reusable knowledge packs",
                 "purpose_cs": "Příprava na pracovní pohovor pro libovolnou profesi a úroveň (znalosti z balíčků)",
                 "inputs": {"role": "str", "role_key": "str|None", "seniority": "junior|mid|senior|lead", "question_count": "int 3-40",
                            "include_answers": "bool", "language": "en|cs", "knowledge": "dict (supplied by the runtime)"},
                 "outputs": {"role": "str", "sections": "list", "competencies": "list", "exercises": "list", "rubric": "list"},
                 "dependencies": [], "network": False},
    },
    "weather": {
        "name": "get_weather",
        "version": 3,        # 2: крупнейший город вместо одноимённой деревни, «Kiev» -> Kyiv; 3: спрашивает город, если его нет
        "match": re.compile(r"weather|forecast|počasí|předpověď|погод|прогноз", re.I),
        "spec": {"description": "Daily weather forecast for any location (Open-Meteo), several locations in parallel",
                 "purpose_en": "Retrieve weather forecasts for any locations, dates and horizons up to 16 days",
                 "purpose_cs": "Předpověď počasí pro libovolná místa a data (až 16 dní)",
                 "inputs": {"location": "str", "start_date": "ISO date", "forecast_days": "int 1-16", "units": "celsius|fahrenheit"},
                 "outputs": {"location": "str", "days": "list", "source": "str"}, "dependencies": [], "network": True},
    },
}


def source(tid: str) -> tuple[str, str, str]:
    d = ROOT / tid
    code = (d / "capability.py").read_text(encoding="utf-8")
    if tid == "weather":
        # адреса провайдера можно подменить (тесты, корпоративный прокси) переменными окружения
        import os
        geo, fc = os.getenv("FRANK_WEATHER_GEO_URL"), os.getenv("FRANK_WEATHER_FORECAST_URL")
        if geo:
            code = re.sub(r'^GEO_URL = ".*?"', f'GEO_URL = "{geo}"', code, count=1, flags=re.M)
        if fc:
            code = re.sub(r'^FORECAST_URL = ".*?"', f'FORECAST_URL = "{fc}"', code, count=1, flags=re.M)
    return code, (d / "test_capability.py").read_text(encoding="utf-8"), f"Verified template `{tid}` (Frankenstein organ template)."


def match_spec(spec: dict) -> str | None:
    """Покрывает ли шаблон орган, который хочет построить планировщик (по смыслу его назначения)."""
    if not config.USE_TEMPLATES:
        return None
    text = " ".join(str(spec.get(k, "")) for k in ("name", "description", "purpose_en"))
    for tid, t in TEMPLATES.items():
        if t["match"].search(text) and (not t["spec"]["network"] or config.ALLOW_NETWORK_CAPABILITIES):
            return tid
    return None


def install(tid: str, *, builder, registry, bus=None, name: str | None = None, task_id: int | None = None) -> str | None:
    """Проверить шаблон в песочнице и установить (новой линией или новой версией существующего органа)."""
    t = TEMPLATES[tid]
    name = name or t["name"]
    code, tests, readme = source(tid)
    spec = {**t["spec"], "name": name, "template": tid, "template_version": t["version"]}
    emit = (lambda typ, **kw: bus.emit(typ, task_id=task_id, capability=name, **kw)) if bus else (lambda *a, **k: None)
    emit("TEMPLATE_INSTALL_STARTED", template=tid)
    v = builder.validate(spec, code, tests, {})
    if not v.ok:
        emit("TEMPLATE_FAILED", template=tid, problems=v.problems[:400])
        return None
    if registry.get(name):
        version = registry.add_version(name, code=code, tests=tests, readme=readme, spec=spec, report=v.report, repairs=0,
                                       origin="template", score=1.0)
        registry.promote_version(name, version, old_cause="superseded", old_note=f"replaced by verified template '{tid}'")
    else:
        version = registry.install_new(spec, code=code, tests=tests, readme=readme, report=v.report, repairs=0, origin="template")["version"]
    emit("CAPABILITY_INSTALLED", version=version, template=tid, tests=v.report.get("total"))
    return name


def adopt(*, registry, builder, monsters, bus, db) -> list[str]:
    """Миграция при старте: узкие органы -> обобщённые методы из шаблонов. Идемпотентно, с возможностью отката."""
    if not config.USE_TEMPLATES:
        return []
    done = []

    def once(key: str) -> bool:
        if db.one("SELECT 1 x FROM meta WHERE key=?", (key,)):
            return False
        db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", (key, "1"))
        return True

    active = registry.active()

    def stale_template(name: str, tid: str) -> bool:
        """Орган поставлен из шаблона, но более старой версии шаблона -> обновляем тем же методом (с откатом)."""
        spec = (registry.code_of(name) or {}).get("spec") or {}
        return spec.get("template") == tid and int(spec.get("template_version") or 1) < TEMPLATES[tid]["version"]

    # 1) собеседования: орган без пакетов знаний (или со старой версией шаблона) получает новую версию-метод
    #    (то же имя -> монстры продолжают им пользоваться)
    v_int = TEMPLATES["interview"]["version"]
    for c in active:
        sk = c.get("skill") or {}
        text = f"{c['name']} {c.get('purpose_en', '')}"
        legacy = TEMPLATES["interview"]["match"].search(text) and not sk.get("knowledge")
        if (legacy or stale_template(c["name"], "interview")) and once(f"template_adopted:{c['name']}:v{v_int}"):
            if install("interview", builder=builder, registry=registry, bus=bus, name=c["name"]):
                bus.emit("CAPABILITY_UPGRADED", capability=c["name"], template="interview", template_version=v_int,
                         reason="profession knowledge lives in reusable knowledge packs; only missing parts are generated")
                done.append(c["name"])
    v_w = TEMPLATES["weather"]["version"]
    for c in active:
        if stale_template(c["name"], "weather") and once(f"template_adopted:{c['name']}:v{v_w}"):
            if install("weather", builder=builder, registry=registry, bus=bus, name=c["name"]):
                bus.emit("CAPABILITY_UPGRADED", capability=c["name"], template="weather", template_version=v_w,
                         reason="improved city recognition and place selection")
                done.append(c["name"])
    # 2) погода: монстры со «старыми» погодными органами (без разбора запроса) получают обобщённый get_weather
    has_weather_skill = any(TEMPLATES["weather"]["match"].search(" ".join((c.get("skill") or {}).get("keywords", [])))
                            for c in active if (c.get("skill") or {}).get("callable"))
    legacy = [c["name"] for c in active if c.get("network") and TEMPLATES["weather"]["match"].search(f"{c['name']} {c.get('purpose_en', '')}")
              and not (c.get("skill") or {}).get("callable")]
    if legacy and not has_weather_skill and config.ALLOW_NETWORK_CAPABILITIES and once(f"template_adopted:weather:v{v_w}"):
        name = "get_weather" if not registry.get("get_weather") else "get_weather_forecast"
        if install("weather", builder=builder, registry=registry, bus=bus, name=name):
            for m in monsters.list(include_failed=True):
                if any(cap["name"] in legacy for cap in m["capabilities"]):
                    monsters.attach(m["id"], name, source="library", task_id=None)
            bus.emit("CAPABILITY_UPGRADED", capability=name, template="weather", replaces=legacy,
                     reason="one generalized weather skill for any city instead of per-task workflows")
            done.append(name)
    return done
