"""Демо-режим.

Здесь НЕТ никакой подделки: демо-задачи — это просто тексты запросов и пути к PDF-файлам.
Они проходят через тот же самый планировщик, строитель и песочницу, что и любая задача пользователя.
Кнопка Reset возвращает организм в поколение 0 (см. Brain.reset).
"""
from . import config

# Тексты написаны как обычные запросы человека. Ни в одном нет названий нужных капабилити —
# агент должен САМ понять, чего ему не хватает.
DEMO_TASKS = [
    {
        "id": "birth_task",
        "set": "set_a",
        "text": ("Analyze these university documents (PDF fact sheets). For every university extract the tuition, "
                 "ranking, acceptance rate, minimum English score and application deadline. Compare the universities, "
                 "rank them (cheaper, higher-ranked and easier to get into is better) and generate a final PDF report "
                 "with the ranking table."),
    },
    {
        "id": "memory_task",
        "set": "set_b",
        "text": ("Now analyze another set of universities in the same way: extract the key facts, compare them, "
                 "rank them and generate the PDF report."),
    },
    {
        "id": "evolution_task",
        "set": "set_c",
        "text": ("Now compare these universities. Tuition is presented in different currencies, so make it comparable "
                 "(convert everything to US dollars, state the exchange rates you used), rank the universities and "
                 "generate the PDF report."),
    },
]


def demo_files(set_name: str) -> list[str]:
    folder = config.DEMO_DIR / set_name
    return sorted(p.name for p in folder.glob("*.pdf")) if folder.exists() else []


def demo_definitions() -> list[dict]:
    return [{"id": t["id"], "text": t["text"], "set": t["set"], "files": demo_files(t["set"])} for t in DEMO_TASKS]
