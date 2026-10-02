import json
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from stafy_ops.schemas import IssueDraft

# Sliding expiry: every save refreshes it, so a conversation lives 72 h after its last activity.
TTL_SECONDS = 72 * 3600


@dataclass
class Conversation:
    messages: list[dict] = field(default_factory=list)
    rounds: int = 0
    pending: IssueDraft | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "messages": self.messages,
                "rounds": self.rounds,
                "pending": self.pending.model_dump(mode="json") if self.pending else None,
            }
        )

    @classmethod
    def from_json(cls, raw: str) -> "Conversation":
        data = json.loads(raw)
        pending = IssueDraft.model_validate(data["pending"]) if data.get("pending") else None
        return cls(messages=data["messages"], rounds=data["rounds"], pending=pending)


class ConversationStore(Protocol):
    async def get(self, chat_id: int) -> Conversation: ...
    async def save(self, chat_id: int, conv: Conversation) -> None: ...
    async def delete(self, chat_id: int) -> None: ...
    async def seen(self, update_id: int) -> bool:
        """True if this Telegram update was already processed (webhook retries); marks it as seen otherwise."""
        ...


class MemoryStore:
    """In-process state, for long-polling and tests. A serverless webhook needs UpstashStore."""

    def __init__(self) -> None:
        self._data: dict[int, Conversation] = {}
        self._seen: set[int] = set()

    async def get(self, chat_id: int) -> Conversation:
        return self._data.setdefault(chat_id, Conversation())

    async def save(self, chat_id: int, conv: Conversation) -> None:
        self._data[chat_id] = conv

    async def delete(self, chat_id: int) -> None:
        self._data.pop(chat_id, None)

    async def seen(self, update_id: int) -> bool:
        if update_id in self._seen:
            return True
        self._seen.add(update_id)
        return False


class UpstashStore:
    """Upstash Redis over its REST API (plain httpx, no extra dependency)."""

    def __init__(self, url: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._client = client or httpx.AsyncClient(timeout=10)

    async def _cmd(self, *args: str | int):
        resp = await self._client.post(self._url, headers=self._headers, json=list(args))
        resp.raise_for_status()
        body = resp.json()
        if "error" in body:
            raise RuntimeError(f"Upstash error: {body['error']}")
        return body["result"]

    async def get(self, chat_id: int) -> Conversation:
        raw = await self._cmd("GET", f"conv:{chat_id}")
        return Conversation.from_json(raw) if raw else Conversation()

    async def save(self, chat_id: int, conv: Conversation) -> None:
        await self._cmd("SET", f"conv:{chat_id}", conv.to_json(), "EX", TTL_SECONDS)

    async def delete(self, chat_id: int) -> None:
        await self._cmd("DEL", f"conv:{chat_id}")

    async def seen(self, update_id: int) -> bool:
        # SET NX returns "OK" only for the first writer; null means the key already existed.
        return await self._cmd("SET", f"upd:{update_id}", 1, "NX", "EX", TTL_SECONDS) is None
