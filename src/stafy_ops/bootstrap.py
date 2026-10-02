from functools import partial

from stafy_ops.config import Settings, get_settings
from stafy_ops.conversation import ConversationStore, MemoryStore, UpstashStore
from stafy_ops.github import GitHubClient
from stafy_ops.handler import Bot
from stafy_ops.llm import OpenAICompatLLM
from stafy_ops.telegram import TelegramClient
from stafy_ops.usage import MemoryUsageStore, UpstashUsageStore, UsageStore, fetch_key_info


def build_store(settings: Settings, *, persistent: bool) -> ConversationStore:
    """Upstash when configured; in-memory otherwise. A serverless webhook (persistent=True) must have Upstash."""
    if creds := settings.redis_rest:
        return UpstashStore(*creds)
    if persistent:
        raise RuntimeError(
            "Webhook mode needs Upstash Redis: set UPSTASH_REDIS_REST_URL/TOKEN (or KV_REST_API_URL/TOKEN)"
        )
    return MemoryStore()


def build_usage_store(settings: Settings) -> UsageStore:
    creds = settings.redis_rest
    return UpstashUsageStore(*creds) if creds else MemoryUsageStore()


def build_bot(settings: Settings | None = None, *, persistent: bool = False) -> tuple[Bot, TelegramClient]:
    settings = settings or get_settings()
    tg = TelegramClient(settings.telegram_bot_token)
    store = build_store(settings, persistent=persistent)
    usage = build_usage_store(settings)
    bot = Bot(
        settings,
        tg,
        OpenAICompatLLM(settings, usage=usage),
        GitHubClient(settings),
        store,
        usage=usage,
        key_info=partial(fetch_key_info, settings),
    )
    return bot, tg
