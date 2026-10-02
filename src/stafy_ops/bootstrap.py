from stafy_ops.config import Settings, get_settings
from stafy_ops.conversation import ConversationStore, MemoryStore, UpstashStore
from stafy_ops.github import GitHubClient
from stafy_ops.handler import Bot
from stafy_ops.llm import OpenAICompatLLM
from stafy_ops.telegram import TelegramClient


def build_store(settings: Settings, *, persistent: bool) -> ConversationStore:
    """Upstash when configured; in-memory otherwise. A serverless webhook (persistent=True) must have Upstash."""
    if creds := settings.redis_rest:
        return UpstashStore(*creds)
    if persistent:
        raise RuntimeError(
            "Webhook mode needs Upstash Redis: set UPSTASH_REDIS_REST_URL/TOKEN (or KV_REST_API_URL/TOKEN)"
        )
    return MemoryStore()


def build_bot(settings: Settings | None = None, *, persistent: bool = False) -> tuple[Bot, TelegramClient]:
    settings = settings or get_settings()
    tg = TelegramClient(settings.telegram_bot_token)
    store = build_store(settings, persistent=persistent)
    return Bot(settings, tg, OpenAICompatLLM(settings), GitHubClient(settings), store), tg
