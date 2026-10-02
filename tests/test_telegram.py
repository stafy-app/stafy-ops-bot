import httpx
import pytest

from stafy_ops.telegram import TelegramClient, TelegramError

TOKEN = "123456:SECRET-TOKEN"


def client(handler) -> TelegramClient:
    return TelegramClient(TOKEN, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_http_error_message_does_not_leak_the_bot_token():
    tg = client(lambda request: httpx.Response(400, text="Bad Request: chat not found"))
    with pytest.raises(TelegramError) as info:
        await tg.send_message(1, "hi")
    assert TOKEN not in str(info.value) and "400" in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


async def test_network_error_message_does_not_leak_the_bot_token():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(TelegramError) as info:
        await client(handler).send_message(1, "hi")
    assert TOKEN not in str(info.value)
