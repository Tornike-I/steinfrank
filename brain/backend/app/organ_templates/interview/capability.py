"""prepare_interview: UNIVERSAL interview-preparation METHOD for any profession.

The method (role recognition, seniority rules, question selection, exercises, rubric, formatting, quality check) is code.
Everything profession-specific comes from a KNOWLEDGE PACK passed by the runtime as inp["knowledge"]
(see SKILL["knowledge"]): a new profession needs a new pack, never a new organ.

inputs:  role (str, the user's wording is preserved), role_key (str|None, recognised pack), seniority (junior|mid|senior|lead),
         question_count (int 3-40), include_answers (bool), language (en|cs), knowledge (dict, supplied by the runtime)
outputs: role, requested_role, seniority, sections[{topic, questions[{q, level, kind, answer_hint?}]}], competencies,
         exercises, rubric, recommendations, question_count, shortfall, knowledge{key, version, source}
"""
import re

SKILL = {
    "intents": ["interview preparation", "mock interview", "interview questions"],
    "keywords": ["interview", "interviews", "pohovor", "pohovoru", "собеседование", "собеседованию", "собеседования", "интервью"],
    "examples": ["Prepare me for a cybersecurity interview", "Prepare me for a UX/UI designer interview",
                 "Prepare me to SENIOR VOIP interview", "15 questions for a junior frontend developer interview",
                 "Připrav mě na pohovor na pozici tester", "Подготовь меня к собеседованию на DevOps инженера"],
    "limits": {"question_count": "3-40", "seniority": "junior, mid, senior, lead"},
    "freshness_seconds": None,
    "llm_slots": {},
    # пакет знаний выбирается по роли; уровень/число/подробность говорят рантайму, ЧТО дозаказать, если в пакете этого нет
    "knowledge": {"kind": "interview_role", "param": "role", "key_param": "role_key", "level_param": "seniority",
                  "count_param": "question_count", "detail_param": "include_answers"},
}

LEVELS = ["junior", "mid", "senior", "lead"]
LEVEL_WORDS = {
    "lead": ["lead", "principal", "staff", "head of", "тимлид", "лид", "ведущ"],
    "senior": ["senior", "sr", "experienced", "сеньор", "синьор", "старш", "zkušen"],
    "junior": ["junior", "jr", "entry", "intern", "trainee", "graduate", "джун", "младш", "začáteč", "junior"],
    "mid": ["mid", "middle", "intermediate", "мидл", "средн", "medior"],
}
DEFAULT_COUNT = {"junior": 10, "mid": 12, "senior": 15, "lead": 15}
# порядок предпочтения уровней при выборе вопросов
PREFERENCE = {"junior": ["junior", "mid"], "mid": ["mid", "junior", "senior"], "senior": ["senior", "mid", "lead"],
              "lead": ["lead", "senior", "mid"]}
NOISE = r"\b(?:a|an|the|my|me|for|to|on|as|position|role|job|interview|interviews|level|developer interview|questions?|" \
        r"\d+|na|pro|pozici|на|для|должность|позицию|роль|к|ко)\b"
GENERIC = re.compile(r"process(es)? and (a )?thread|version control|\bgit\b|osi model", re.I)


def _norm(t):
    t = str(t).lower()
    t = re.sub(r"\s*([/\-])\s*", r"\1", t)
    return " ".join(t.split())


def _word(w):
    """Короткие слова (sr, jr, mid) — только целиком; длинные — как основа (старш -> старший, старшего)."""
    return r"(?<![\w])" + re.escape(w) + (r"\.?(?![\w])" if len(w) <= 3 else r"[\w]*")


def _level(t):
    for lv in ("lead", "senior", "junior", "mid"):
        for w in LEVEL_WORDS[lv]:
            if re.search(_word(w), t):
                return lv
    return None


def _strip_levels(t):
    for words in LEVEL_WORDS.values():
        for w in words:
            t = re.sub(_word(w), " ", t)
    return t


def _extract_role(t):
    """Роль словами пользователя (если её нет в индексе знаний) — НИКОГДА не подменяется общей заглушкой."""
    patterns = [r"(?:for|to|on)\s+(?:an?\s+|the\s+|my\s+)?(.+?)\s+(?:job\s+)?interview",
                r"interview\s+(?:for|as)\s+(?:an?\s+|the\s+)?(.+?)(?:\s+(?:position|role|job))?\s*(?:[.?!,]|$)",
                r"pohovor\w*\s+(?:na|pro)\s+(?:pozici\s+)?(.+?)\s*(?:[.?!,]|$)",
                r"собеседовани\w*\s+(?:на|для|по)\s+(?:должность\s+|позицию\s+|роль\s+)?(.+?)\s*(?:[.?!,]|$)",
                r"^(.+?)\s+interview"]
    for p in patterns:
        m = re.search(p, t)
        if m:
            role = _strip_levels(m.group(1))
            role = re.sub(NOISE, " ", role)
            role = " ".join(role.replace("prepare", " ").split()).strip(" ,.-")
            if role and len(role) >= 2:
                return role
    return None


def parse_request(text, context):
    t = _norm(text)
    if not any(k in t for k in SKILL["keywords"]):
        return None
    level = _level(t) or "mid"
    m = re.search(r"(\d{1,2})\s*(?:questions?|otáz\w*|вопрос\w*)", t)
    count = int(m.group(1)) if m else None          # не указано -> метод возьмёт обычное число из того, что уже знает
    answers = bool(re.search(r"with (?:example |sample )?answers|example answers|s odpověď|с ответ", t))
    best = None
    for entry in context.get("knowledge_index", []):          # таксономия из пакетов знаний: длиннейший синоним побеждает
        for alias in entry.get("aliases", []):
            a = _norm(alias)
            if a and re.search(r"(?<![\w])" + re.escape(a) + r"(?![\w])", t) and (best is None or len(a) > best[1]):
                best = (entry, len(a))
    if best:
        role, key = best[0]["title"], best[0]["key"]
    else:
        role, key = _extract_role(t), None
        if not role:
            # собеседование, но профессия не названа — спросить (ответ пользователя станет role)
            q = {"cs": "Na jakou pozici se připravuješ?", "en": "Which profession is the interview for?"}.get(context.get("lang"),
                                                                                                          "Which profession is the interview for?")
            if re.search(r"[а-яё]", t):
                q = "На какую должность собеседование?"
            return {"_missing": "role", "_question": q, "role_key": None, "seniority": level, "question_count": count,
                    "include_answers": answers, "language": context.get("lang", "en")}
    return {"role": role, "role_key": key, "seniority": level, "question_count": count, "include_answers": answers,
            "language": context.get("lang", "en")}


def run(inp):
    role = inp.get("role")
    if not isinstance(role, str) or not role.strip():
        raise ValueError("tell me which profession or specialization the interview is for")
    level = inp.get("seniority", "mid")
    if level not in LEVELS:
        raise ValueError("seniority must be junior, mid, senior or lead")
    count = int(inp.get("question_count") or DEFAULT_COUNT[level])
    if count < 3 or count > 40:
        raise ValueError("question_count must be between 3 and 40")
    kn = inp.get("knowledge")
    if not isinstance(kn, dict) or not kn.get("topics"):
        raise ValueError(f"no knowledge pack is available for '{role}' yet")
    topics = [t for t in kn["topics"] if isinstance(t, dict) and t.get("questions")]
    order = PREFERENCE[level]
    picked = {i: [] for i in range(len(topics))}
    total = 0
    for lv in order:                                   # уровень за уровнем, по кругу через темы — покрытие тем
        pools = [[q for q in t["questions"] if q.get("level") == lv] for t in topics]
        while total < count and any(pools):
            for i, pool in enumerate(pools):
                if pool and total < count:
                    picked[i].append(pool.pop(0))
                    total += 1
    sections = []
    for i, t in enumerate(topics):
        if picked[i]:
            qs = [{"q": q["q"], "level": q.get("level"), "kind": q.get("kind", "concept"),
                   **({"answer_hint": q.get("answer_hint", "")} if inp.get("include_answers") else {})} for q in picked[i]]
            sections.append({"topic": t.get("name", "Topic"), "questions": qs})
    exercises = [e for e in kn.get("exercises", []) if isinstance(e, dict) and e.get("level") in order[:2]] or \
        [e for e in kn.get("exercises", []) if isinstance(e, dict)]
    return {"role": kn.get("title") or role, "requested_role": role, "seniority": level, "sections": sections,
            "competencies": list(kn.get("competencies", []))[:10], "exercises": exercises[:4],
            "rubric": list(kn.get("rubric", []))[:6], "recommendations": list(kn.get("recommendations", []))[:6],
            "question_count": total, "requested_count": count, "shortfall": max(0, count - total),
            "knowledge": {k: kn.get(k) for k in ("key", "version", "source")},
            "research": kn.get("research") if isinstance(kn.get("research"), dict) else None}


LABELS = {
    "en": {"title": "{role} — {level} interview preparation", "comp": "Core competencies", "q": "Interview questions",
           "ex": "Practical exercises", "rub": "How you will be evaluated", "rec": "Preparation recommendations",
           "hint": "Key points", "pack": "Knowledge pack", "now": "Current market notes (researched {day})", "src": "Sources", "short": "Only {n} questions at this level are in the knowledge pack (you asked for {m})."},
    "cs": {"title": "{role} — příprava na pohovor ({level})", "comp": "Klíčové kompetence", "q": "Otázky na pohovor",
           "ex": "Praktická cvičení", "rub": "Jak budete hodnoceni", "rec": "Doporučení k přípravě",
           "hint": "Klíčové body", "pack": "Balíček znalostí", "now": "Aktuální stav trhu (průzkum {day})", "src": "Zdroje", "short": "V balíčku znalostí je na této úrovni jen {n} otázek (chtěli jste {m})."},
}


def format_result(result, lang):
    L = LABELS.get(lang, LABELS["en"])
    out = [f"### {L['title'].format(role=result['role'], level=result['seniority'])}", ""]
    if result.get("competencies"):
        out += [f"#### 1. {L['comp']}"] + [f"- {c}" for c in result["competencies"]] + [""]
    out += [f"#### 2. {L['q']}"]
    n = 0
    for s in result["sections"]:
        out += ["", f"**{s['topic']}**", ""]
        for q in s["questions"]:
            n += 1
            tag = f" _({q['level']})_" if q.get("level") and q["level"] != result["seniority"] else ""
            out.append(f"{n}. {q['q']}{tag}")
            if q.get("answer_hint"):
                out.append(f"   - *{L['hint']}:* {q['answer_hint']}")
    if result.get("shortfall"):
        out += ["", "> " + L["short"].format(n=result["question_count"], m=result["requested_count"])]
    if result.get("exercises"):
        out += ["", f"#### 3. {L['ex']}"] + [f"- {e.get('title')}" for e in result["exercises"]]
    if result.get("rubric"):
        out += ["", f"#### 4. {L['rub']}"] + [f"- {r}" for r in result["rubric"]]
    if result.get("recommendations"):
        out += ["", f"#### 5. {L['rec']}"] + [f"- {r}" for r in result["recommendations"]]
    rs = result.get("research") or {}
    if rs.get("points"):
        out += ["", f"#### {L['now'].format(day=str(rs.get('fetched_at', ''))[:10])}"] + [f"- {p}" for p in rs["points"]]
        if rs.get("sources"):
            out += ["", f"_{L['src']}: " + ", ".join(str(x) for x in rs["sources"][:6]) + "_"]
    k = result.get("knowledge") or {}
    if k.get("key"):
        out += ["", f"_{L['pack']}: {k['key']} v{k.get('version')} ({k.get('source')})_"]
    return "\n".join(out)


def check_result(result, params):
    """Проверка качества без LLM: роль сохранена, уровень верный, вопросы есть, нет «воды», нет повторов."""
    problems = []
    if params.get("seniority") and result.get("seniority") != params["seniority"]:
        problems.append("seniority changed")
    qs = [q["q"] for s in result.get("sections", []) for q in s["questions"]]
    if not qs:
        problems.append("no questions selected")
    if len(set(qs)) != len(qs):
        problems.append("duplicate questions")
    filler = [q for q in qs if GENERIC.search(q)]
    if qs and len(filler) > max(1, len(qs) // 5):
        problems.append(f"generic filler: {len(filler)} of {len(qs)} questions")
    if str(result.get("role", "")).lower() in ("general it", "it", ""):
        problems.append("role was replaced by a generic fallback")
    return problems
