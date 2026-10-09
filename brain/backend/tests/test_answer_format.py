"""Ответ человеку, а не дамп данных: живой случай «Salary range: {'min': 80000, …}» и потерянный заголовок."""
from app.answer_format import human, normalize


def test_live_job_market_answer_becomes_readable():
    raw = (" Top Job in Current Market\n\nThe current #1 ranked position is **Software Engineer**. Key metrics: salary range "
           "{'min': 80000, 'max': 180000, 'currency': 'USD'}, demand level very high. Data as of 2026-10-09T03:10:19.429027+00:00.")
    out = normalize({"en": raw, "cs": raw})["en"]
    assert out.startswith("### Top Job in Current Market\n\n")
    assert "salary range 80,000–180,000 USD" in out and "2026-10-09 03:10 UTC" in out and "{" not in out


def test_markdown_code_and_plain_text_stay_untouched():
    s = "See [docs](http://x), note [1] and {name}.\n\n```py\nx = {'a': 1}\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |"
    assert normalize(s) == s
    assert normalize("Short answer here\n\nMore text.") == "Short answer here\n\nMore text."
    assert normalize("### Kyiv\n\nok") == "### Kyiv\n\nok"


def test_human_values():
    assert human({"k": [1, 2], "z": None, "growth_rate": 22.0}) == "k: 1, 2; growth rate: 22"
    assert human(["a", "b"]) == "a, b" and human(True) == "yes"
