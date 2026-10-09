"""Экономика токенов: ИЗМЕРЕННЫЕ числа, а не обещания.

Что считается фактом (из таблиц usage / tasks / evolution_events):
  * токены и вызовы модели по каждой задаче и по режиму исполнения (deterministic / cache / light / partial / full …);
  * сколько задач выполнено вообще без вызова модели;
  * сколько раз навык был переиспользован, сколько раз данные взяты из свежей памяти;
  * вызовы внешних API (не-LLM) — отдельно, без выдуманной цены.

Что считается ОЦЕНКОЙ (и так и подписывается):
  * «сэкономлено» = для каждого повторного использования навыка: (измеренные токены задачи, которая этот навык
    СОЗДАЛА) − (измеренные токены повторной задачи). Базовая линия — реальная, но предположение «без навыка повтор
    стоил бы столько же, сколько первая сборка» — это допущение. Если замера первой сборки нет — оценки нет.
"""
from . import pricing
from .db import Database, loads

REUSE_MODES = {"deterministic", "cache", "light", "partial"}


def report(db: Database) -> dict:
    tasks = db.query(
        "SELECT t.id, t.mode, t.status, t.duration, t.strategy, t.input,"
        " COALESCE((SELECT SUM(kind='llm') FROM usage u WHERE u.task_id=t.id),0) calls,"
        " COALESCE((SELECT SUM(prompt_tokens) FROM usage u WHERE u.task_id=t.id AND kind='llm'),0) pt,"
        " COALESCE((SELECT SUM(completion_tokens) FROM usage u WHERE u.task_id=t.id AND kind='llm'),0) ct,"
        " COALESCE((SELECT SUM(credits) FROM usage u WHERE u.task_id=t.id),0) credits,"
        " COALESCE((SELECT COUNT(*) FROM usage u WHERE u.task_id=t.id AND kind='tool'),0) tool_calls, t.metrics"
        " FROM tasks t ORDER BY t.id")
    # ≈ $ по каждой задаче: измеренные токены/символы × списочная цена модели (pricing.py)
    usd_of: dict[int, float] = {}
    for u in db.query("SELECT task_id, kind, model, prompt_tokens, completion_tokens, chars, credits FROM usage"
                                      " WHERE task_id IS NOT NULL"):
        usd_of[u["task_id"]] = usd_of.get(u["task_id"], 0.0) + pricing.usage_row(u)
    cache_hits_of = {r["task_id"]: r["n"] for r in db.query(
        "SELECT task_id, COUNT(*) n FROM usage WHERE kind='cache' AND task_id IS NOT NULL GROUP BY task_id")}
    by_mode: dict[str, dict] = {}
    for t in tasks:
        m = t["mode"] or "legacy"
        b = by_mode.setdefault(m, {"tasks": 0, "completed": 0, "llm_calls": 0, "tokens": 0, "seconds": 0.0, "tool_calls": 0})
        b["tasks"] += 1
        b["completed"] += t["status"] == "completed"
        b["llm_calls"] += t["calls"]
        b["tokens"] += t["pt"] + t["ct"]
        b["seconds"] += t["duration"] or 0
        b["tool_calls"] += t["tool_calls"]
    for b in by_mode.values():
        b["avg_tokens"] = round(b["tokens"] / b["tasks"]) if b["tasks"] else 0
        b["avg_seconds"] = round(b["seconds"] / b["tasks"], 1) if b["tasks"] else 0
        b["avg_llm_calls"] = round(b["llm_calls"] / b["tasks"], 2) if b["tasks"] else 0

    # режимы INSTANT / LIGHT / SMART / BUILD (+ template, gate) — из записи исполнения каждой задачи
    by_tier: dict[str, dict] = {}
    kn_loaded = kn_generated = organs_llm = organs_template = 0
    executions = []
    for t in tasks:
        m = loads(t["metrics"], None)
        if not m:
            continue
        b = by_tier.setdefault(m.get("tier") or "smart", {"tasks": 0, "llm_calls": 0, "tokens": 0, "seconds": 0.0, "usd": 0.0})
        b["usd"] += usd_of.get(t["id"], 0.0)
        b["tasks"] += 1
        b["llm_calls"] += t["calls"]
        b["tokens"] += t["pt"] + t["ct"]
        b["seconds"] += m.get("seconds") or 0
        k = m.get("knowledge") or {}
        kn_loaded += k.get("source") == "loaded"
        kn_generated += k.get("source") == "generated"
        organs_llm += bool(m.get("organ_generated_by_llm"))
        organs_template += bool(m.get("template_used"))
        executions.append({"task": t["id"], "input": (t["input"] or "")[:80], "tier": m.get("tier"), "skill": m.get("skill"),
                           "knowledge": k.get("title"), "knowledge_source": k.get("source"), "organ_created": m.get("organ_created"),
                           "llm_calls": t["calls"], "tokens": t["pt"] + t["ct"], "seconds": m.get("seconds"),
                           "usd": round(usd_of.get(t["id"], 0.0), 5), "llm_cache_hits": cache_hits_of.get(t["id"], 0),
                           "quality": "ok" if not m.get("quality") else "; ".join(m["quality"])[:120]})
    for b in by_tier.values():
        b["avg_tokens"] = round(b["tokens"] / b["tasks"]) if b["tasks"] else 0
        b["avg_seconds"] = round(b["seconds"] / b["tasks"], 1) if b["tasks"] else 0
        b["avg_llm_calls"] = round(b["llm_calls"] / b["tasks"], 2) if b["tasks"] else 0
        b["avg_usd"] = round(b["usd"] / b["tasks"], 5) if b["tasks"] else 0
        b["usd"] = round(b["usd"], 4)

    # кэш ответов модели: сколько раз тот же запрос обошёлся без вызова и сколько это сэкономило (по стоимости исходного вызова)
    c = db.one("SELECT COUNT(*) entries, COALESCE(SUM(hits),0) hits, COALESCE(SUM(hits*tokens),0) saved_tokens,"
               " COALESCE(SUM(hits*seconds),0) saved_seconds FROM llm_cache")
    cache_usd = sum(pricing.llm(r["model"], r["tokens"] // 2, r["tokens"] - r["tokens"] // 2) * r["hits"]
                    for r in db.query("SELECT model, tokens, hits FROM llm_cache WHERE hits > 0"))
    llm_cache = {"entries": c["entries"], "hits": c["hits"], "saved_tokens": c["saved_tokens"],
                 "saved_seconds": round(c["saved_seconds"], 1), "saved_usd": round(cache_usd, 4)}

    completed = [t for t in tasks if t["status"] == "completed"]
    no_llm = [t for t in completed if t["calls"] == 0]
    ev = {r["event_type"]: r["n"] for r in db.query(
        "SELECT event_type, COUNT(*) n FROM evolution_events WHERE event_type IN ('SKILL_EXECUTED','KNOWLEDGE_CACHE_HIT',"
        "'CAPABILITY_REUSED','WORKFLOW_RECIPE_REUSED','CAPABILITY_INSTALLED','SLOT_FILLING','SKILL_BROKEN','KNOWLEDGE_STALE')"
        " GROUP BY event_type")}

    # оценка экономии: базовая линия = измеренная стоимость задачи, которая СОЗДАЛА навык
    built_by = {}
    for r in db.query("SELECT capability_name, MIN(task_id) task_id FROM evolution_events WHERE event_type='CAPABILITY_INSTALLED'"
                      " AND task_id IS NOT NULL GROUP BY capability_name"):
        built_by[r["capability_name"]] = r["task_id"]
    tokens_of = {t["id"]: t["pt"] + t["ct"] for t in tasks}
    per_skill: dict[str, dict] = {}
    for t in completed:
        if (t["mode"] or "") not in REUSE_MODES:
            continue
        st = loads(t["strategy"], {}) or {}
        skill = st.get("skill")
        if not skill or st.get("limitation"):     # честный отказ («40 дней не поддерживается») экономией не считаем
            continue
        s = per_skill.setdefault(skill, {"skill": skill, "reuses": 0, "reuse_tokens": 0, "baseline_task": built_by.get(skill),
                                         "baseline_tokens": tokens_of.get(built_by.get(skill))})
        s["reuses"] += 1
        s["reuse_tokens"] += t["pt"] + t["ct"]
    estimated = 0
    for s in per_skill.values():
        if s["baseline_tokens"]:
            s["estimated_saved_tokens"] = max(0, s["baseline_tokens"] * s["reuses"] - s["reuse_tokens"])
            estimated += s["estimated_saved_tokens"]
        else:
            s["estimated_saved_tokens"] = None      # нет замера первой сборки — оценку не выдумываем

    return {
        "measured": {
            "tasks": len(tasks), "completed": len(completed), "tasks_without_llm": len(no_llm),
            "llm_tokens_in": sum(t["pt"] for t in tasks), "llm_tokens_out": sum(t["ct"] for t in tasks),
            "llm_calls": sum(t["calls"] for t in tasks), "tool_calls": sum(t["tool_calls"] for t in tasks),
            "skill_executions": ev.get("SKILL_EXECUTED", 0), "cache_hits": ev.get("KNOWLEDGE_CACHE_HIT", 0),
            "stale_refreshes": ev.get("KNOWLEDGE_STALE", 0), "slot_fills": ev.get("SLOT_FILLING", 0),
            "planner_reuses": ev.get("CAPABILITY_REUSED", 0), "recipe_reuses": ev.get("WORKFLOW_RECIPE_REUSED", 0),
            "skills_built": ev.get("CAPABILITY_INSTALLED", 0), "skill_repairs": ev.get("SKILL_BROKEN", 0),
            "avoided_rebuilds": ev.get("SKILL_EXECUTED", 0) + ev.get("KNOWLEDGE_CACHE_HIT", 0)
                                + ev.get("CAPABILITY_REUSED", 0) + ev.get("WORKFLOW_RECIPE_REUSED", 0),
            "by_mode": by_mode, "by_tier": by_tier, "llm_cache": llm_cache,
            "usd_total": round(sum(usd_of.values()), 4),
            "knowledge_loaded": kn_loaded, "knowledge_generated": kn_generated,
            "organs_generated_by_llm": organs_llm, "organs_from_templates": organs_template,
            "executions": executions[-20:][::-1],
        },
        "estimated": {
            "saved_tokens": estimated, "per_skill": sorted(per_skill.values(), key=lambda s: -s["reuses"]),
            "method": "for each reuse of a skill: measured tokens of the task that first BUILT that skill minus the measured "
                      "tokens of the reuse task. Answers that only reported a limitation are not counted. Assumes a reuse would otherwise have cost as much as the first build; "
                      "skills without a measured build task get no estimate.",
        },
        "note": "tool_calls are external non-LLM API calls (e.g. weather provider); their price is not known to the app. "
                "USD values are estimates from list prices of the underlying models (pricing.py); ElevenLabs bills its own credits.",
    }
