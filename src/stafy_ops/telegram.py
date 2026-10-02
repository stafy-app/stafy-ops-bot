from typing import Protocol

import httpx

_MAX_LEN = 4000  # Telegram hard limit is 4096


class Messenger(Protocol):
    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None: ...
    async def answer_callback(self, callback_id: str) -> None: ...


class TelegramClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=40)

    async def _call(self, method: str, payload: dict) -> dict:
        resp = await self._client.post(f"{self._base}/{method}", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        payload: dict = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        return (await self._call("getUpdates", payload))["result"]

    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload: dict = {"chat_id": chat_id, "text": text[:_MAX_LEN]}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        await self._call("sendMessage", payload)

    async def answer_callback(self, callback_id: str) -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id})
