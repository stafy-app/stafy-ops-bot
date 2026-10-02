import json

import httpx

from stafy_ops.conversation import TTL_SECONDS, Conversation, MemoryStore, UpstashStore
from tests.conftest import make_draft


def test_ttl_is_72_hours():
    assert TTL_SECONDS == 72 * 3600


def test_conversation_json_roundtrip():
    conv = Conversation(messages=[{"role": "user", "content": "x"}], rounds=2, pending=make_draft(milestone="v0.2.0"))
    restored = Conversation.from_json(conv.to_json())
    assert restored.rounds == 2 and restored.messages == conv.messages and restored.pending == conv.pending


def upstash(handler) -> UpstashStore:
    return UpstashStore("https://redis.example", "tok", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def test_upstash_save_sets_72h_expiry_and_get_restores():
    db: dict[str, str] = {}
    commands: list[list] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        cmd = json.loads(request.content)
        commands.append(cmd)
        if cmd[0] == "SET":
            db[cmd[1]] = cmd[2]
            return httpx.Response(200, json={"result": "OK"})
        if cmd[0] == "GET":
            return httpx.Response(200, json={"result": db.get(cmd[1])})
        db.pop(cmd[1], None)
        return httpx.Response(200, json={"result": 1})

    store = upstash(handler)
    await store.save(7, Conversation(messages=[{"role": "user", "content": "hi"}], rounds=1))
    assert commands[0][3:] == ["EX", TTL_SECONDS]
    assert (await store.get(7)).rounds == 1
    await store.delete(7)
    assert (await store.get(7)).messages == []


async def test_upstash_seen_uses_set_nx():
    seen_keys: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        cmd = json.loads(request.content)
        assert cmd[0] == "SET" and "NX" in cmd
        first = cmd[1] not in seen_keys
        seen_keys.add(cmd[1])
        return httpx.Response(200, json={"result": "OK" if first else None})

    store = upstash(handler)
    assert await store.seen(100) is False
    assert await store.seen(100) is True


async def test_memory_store_seen():
    store = MemoryStore()
    assert await store.seen(1) is False
    assert await store.seen(1) is True
