from typing import Protocol

import httpx

_MAX_LEN = 4000  # Telegram hard limit is 4096


class TelegramError(httpx.HTTPError):
    """Telegram API failure with the token-bearing URL stripped from the message."""


class Messenger(Protocol):
    async def send_message(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None: ...
    async def answer_callback(self, callback_id: str) -> None: ...
    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None: ...


class TelegramClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=40)

    async def _call(self, method: str, payload: dict) -> dict:
        # The bot token is part of the URL, and httpx puts the URL in its error messages, which end up in
        # logs. Re-raise without it (`from None` also drops the chained original).
        try:
            resp = await self._client.post(f"{self._base}/{method}", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise TelegramError(f"{method} failed: HTTP {exc.response.status_code} {exc.response.text[:200]}") from None
        except httpx.RequestError as exc:
            raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
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

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": action})
