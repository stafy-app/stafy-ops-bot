import pytest
from pydantic import ValidationError

from stafy_ops.render import render_body
from stafy_ops.schemas import LLMTurn
from tests.conftest import make_draft


def test_ask_requires_questions():
    with pytest.raises(ValidationError):
        LLMTurn(action="ask", questions=[])
    with pytest.raises(ValidationError):
        LLMTurn(action="ask", questions=["a", "b", "c", "d"])


def test_draft_requires_issue():
    with pytest.raises(ValidationError):
        LLMTurn(action="draft")


def test_mobile_repo_and_area_are_rejected():
    with pytest.raises(ValidationError):
        make_draft(repo="stafy-mobile")
    with pytest.raises(ValidationError):
        make_draft(area="mobile")


def test_labels_per_kind():
    assert make_draft(kind="feature").labels == ["enhancement", "area: webapp"]
    assert make_draft(kind="bug").labels == ["bug", "area: webapp"]
    assert make_draft(kind="chore").labels == ["area: webapp"]


def test_feature_body_sections():
    body = render_body(make_draft())
    for heading in ("Gap", "Context", "Why it matters", "Acceptance criteria", "Out of scope", "Impact", "Open questions"):
        assert f"## {heading}\n" in body
    assert "- [ ] Sessions listed per user" in body


def test_security_checkbox_when_money_or_roles():
    assert "security-auditor" not in render_body(make_draft())
    assert "security-auditor" in render_body(make_draft(money_data=True))
    assert "security-auditor" in render_body(make_draft(roles="manager"))


def test_bug_body_sections():
    body = render_body(make_draft(kind="bug", steps=["open settings"], expected="list shown"))
    for heading in ("What happens", "Expected", "Steps to reproduce", "Environment", "Impact", "Acceptance criteria"):
        assert f"## {heading}\n" in body
    assert "1. open settings" in body
