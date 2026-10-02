import pytest

from stafy_ops.config import Settings
from stafy_ops.conversation import MemoryStore
from stafy_ops.github import CreatedIssue
from stafy_ops.handler import Bot
from stafy_ops.schemas import IssueDraft, LLMTurn

USER_ID = 42
CHAT_ID = 7


def make_draft(**overrides) -> IssueDraft:
    data = dict(
        title="Add session list to settings",
        repo="stafy-web-app",
        kind="feature",
        area="webapp",
        priority="High",
        size="M",
        summary="Settings has no way to see active sessions.",
        acceptance_criteria=["Sessions listed per user"],
    )
    return IssueDraft(**{**data, **overrides})


def ask(*questions: str) -> LLMTurn:
    return LLMTurn(action="ask", questions=list(questions))


def draft_turn(**overrides) -> LLMTurn:
    return LLMTurn(action="draft", issue=make_draft(**overrides))


class FakeLLM:
    def __init__(self, turns: list[LLMTurn]) -> None:
        self.turns, self.force_flags = list(turns), []

    async def next_turn(self, messages, *, force_draft=False):
        self.force_flags.append(force_draft)
        return self.turns.pop(0)


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, dict | None]] = []
        self.actions: list[tuple[int, str]] = []

    async def send_message(self, chat_id, text, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    async def answer_callback(self, callback_id):
        pass

    async def send_chat_action(self, chat_id, action="typing"):
        self.actions.append((chat_id, action))


class FakeGitHub:
    def __init__(self) -> None:
        self.created: list[IssueDraft] = []

    async def resolve_milestone(self, draft):
        return draft.milestone or "v0.2.0"

    async def create_issue(self, draft):
        self.created.append(draft)
        return CreatedIssue(url="https://github.com/stafy-app/stafy-web-app/issues/1")


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_bot_token="t",
        telegram_allowed_user_ids=str(USER_ID),
        github_token="g",
        llm_base_url="https://llm.example/v1",
        llm_api_key="k",
        llm_model="m",
        max_question_rounds=2,
    )


@pytest.fixture
def make_bot(settings):
    def _make(turns: list[LLMTurn]):
        llm, tg, gh = FakeLLM(turns), FakeTelegram(), FakeGitHub()
        return Bot(settings, tg, llm, gh, MemoryStore()), llm, tg, gh

    return _make


def text_update(text: str, user_id: int = USER_ID) -> dict:
    return {"message": {"from": {"id": user_id}, "chat": {"id": CHAT_ID}, "text": text}}


def button_update(data: str, user_id: int = USER_ID) -> dict:
    return {"callback_query": {"id": "cb", "from": {"id": user_id}, "data": data, "message": {"chat": {"id": CHAT_ID}}}}
