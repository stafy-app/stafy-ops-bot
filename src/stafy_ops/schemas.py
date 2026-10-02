from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Repo(StrEnum):
    BACKEND = "stafy-backend"
    WEB_APP = "stafy-web-app"
    LANDING = "stafy-landing"
    WORKSPACE = "workspace"


class Kind(StrEnum):
    BUG = "bug"
    FEATURE = "feature"
    DOCS = "docs"
    CHORE = "chore"


class Area(StrEnum):
    BACKEND = "backend"
    WEBAPP = "webapp"
    LANDING = "landing"
    INFRA = "infra"


class Priority(StrEnum):
    URGENT = "Urgent"
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class Size(StrEnum):
    XS = "XS"
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


# Only these repos are versioned with milestones; landing and workspace never get one.
MILESTONE_REPOS = {Repo.BACKEND, Repo.WEB_APP}

# chore has no type label; every issue also gets one `area: <area>` label.
TYPE_LABELS = {Kind.BUG: "bug", Kind.FEATURE: "enhancement", Kind.DOCS: "documentation"}


class IssueDraft(BaseModel):
    title: str = Field(min_length=5, max_length=100, description="Short, imperative, no `feat:` prefix")
    repo: Repo
    kind: Kind
    area: Area
    priority: Priority
    size: Size
    summary: str = Field(description="Feature/chore/docs: the Gap. Bug: what happens.")
    context: str = "Not stated"
    why: str = ""
    expected: str = ""
    steps: list[str] = []
    environment: str = ""
    acceptance_criteria: list[str] = []
    out_of_scope: list[str] = []
    open_questions: list[str] = []
    api_contract: str = "none"
    roles: str = "none"
    money_data: bool = False
    milestone: str | None = Field(
        default=None,
        pattern=r"^v\d+\.\d+\.\d+$",
        description="Only when the user explicitly named a version (e.g. v0.3.0); otherwise null — the app picks the default",
    )

    @property
    def labels(self) -> list[str]:
        labels = [f"area: {self.area.value}"]
        if type_label := TYPE_LABELS.get(self.kind):
            labels.insert(0, type_label)
        return labels


MAX_ISSUES = 5


class LLMTurn(BaseModel):
    action: Literal["ask", "draft", "chat"]
    questions: list[str] = Field(default=[], description="1-3 questions in Romanian when action is `ask`")
    reply: str = Field(default="", description="1-2 short friendly Romanian sentences when action is `chat`")
    issues: list[IssueDraft] = Field(
        default=[], description=f"1-{MAX_ISSUES} issues when action is `draft`: one per repo that needs work"
    )

    @model_validator(mode="after")
    def _check(self) -> "LLMTurn":
        if self.action == "ask" and not 1 <= len(self.questions) <= 3:
            raise ValueError("`ask` requires 1-3 questions")
        if self.action == "chat" and not 1 <= len(self.reply.strip()) <= 500:
            raise ValueError("`chat` requires a `reply` of 1-500 characters")
        if self.action == "draft" and not 1 <= len(self.issues) <= MAX_ISSUES:
            raise ValueError(f"`draft` requires 1-{MAX_ISSUES} `issues`")
        return self
