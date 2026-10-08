import pytest

from frankenstein.refs import RefError, eval_when, render, template_refs, when_refs

CTX = {"inputs": {"n": 3, "name": "Ada"}, "steps": {"a": {"items": [1, 2], "label": "x"}, "skip": None}}


def test_whole_ref_keeps_type():
    assert render("{{steps.a.items}}", CTX) == [1, 2]
    assert render({"k": ["{{inputs.n}}"]}, CTX) == {"k": [3]}


def test_inline_ref_is_text():
    assert render("hi {{inputs.name}}, {{steps.a.items}}", CTX) == "hi Ada, [1, 2]"
    assert render("{{steps.skip.label}}!", CTX) == "!"


def test_list_index_and_length():
    assert render("{{steps.a.items.1}}", CTX) == 2
    assert render("{{steps.a.items.length}}", CTX) == 2


def test_template_refs():
    assert template_refs({"p": "a {{inputs.x}} b {{ steps.y.z }}"}) == ["inputs.x", "steps.y.z"]


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("inputs.n > 2", True),
        ("inputs.n == 3 and steps.a.label == 'x'", True),
        ("not (inputs.n < 1 or steps.skip != null)", True),
        ("len(steps.a.items) >= 2", True),
        ("'d' in inputs.name", True),
        ("2 not in steps.a.items", False),
        ("steps.skip.label == 'x'", False),
        ("inputs.name > 5", False),
    ],
)
def test_when(expr, expected):
    assert eval_when(expr, CTX) is expected


def test_when_refs_and_errors():
    assert when_refs("len(steps.a.items) > 0 and inputs.n") == ["steps.a.items", "inputs.n"]
    for bad in ("__import__('os')", "inputs.n +", "a = 1", "inputs.n ** 2"):
        with pytest.raises(RefError):
            eval_when(bad, CTX)
