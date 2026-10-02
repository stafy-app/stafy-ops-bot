import json
from datetime import date, datetime

import httpx

from stafy_ops.handler import Bot
from stafy_ops.llm import OpenAICompatLLM
from stafy_ops.conversation import MemoryStore
from stafy_ops.usage import (
    TZ,
    MemoryUsageStore,
    Usage,
    UpstashUsageStore,
    fetch_key_info,
    fmt_tokens,
    format_report,
    parse_usage,
    report_days,
)
from tests.conftest import CHAT_ID, FakeGitHub, FakeLLM, FakeTelegram, text_update

TODAY = date(2026, 10, 2)


def test_parse_openrouter_usage():
    u = parse_usage(
        {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "cost": 0.0123,
            "prompt_tokens_details": {"cached_tokens": 600},
            "completion_tokens_details": {"reasoning_tokens": 50},
        }
    )
    assert (u.calls, u.prompt, u.completion, u.cached, u.reasoning) == (1, 1000, 200, 600, 50)
    assert u.cost_micro == 12300 and u.costed_calls == 1


def test_parse_flat_and_deepseek_cache_fields_and_missing_cost():
    assert parse_usage({"prompt_tokens": 5, "cached_tokens": 3}).cached == 3
    u = parse_usage({"prompt_tokens": 5, "completion_tokens": 1, "prompt_cache_hit_tokens": 4})
    assert u.cached == 4 and u.costed_calls == 0 and u.cost_micro == 0


def test_parse_without_usage_still_counts_the_call():
    assert parse_usage(None) == Usage(calls=1)
    assert parse_usage({"cost": "garbage"}).costed_calls == 0


def test_usage_addition_keeps_cost_exact():
    total = parse_usage({"cost": 0.1}) + parse_usage({"cost": 0.2})
    assert total.cost_micro == 300000 and total.calls == 2


def test_fmt_tokens():
    assert [fmt_tokens(n) for n in (7, 1500, 2_300_000)] == ["7", "1.5k", "2.3M"]


def test_report_days_cover_month_and_a_week():
    assert len(report_days(date(2026, 10, 2))) == 7
    assert len(report_days(date(2026, 10, 20))) == 20


async def test_memory_store_roundtrip():
    store = MemoryUsageStore()
    await store.record("m1", parse_usage({"prompt_tokens": 10}), TODAY)
    await store.record("m1", parse_usage({"prompt_tokens": 5}), TODAY)
    assert (await store.load([TODAY]))[TODAY]["m1"].prompt == 15


async def test_upstash_store_writes_one_pipeline_and_reads_it_back():
    sent: list[list] = []
    db: dict[str, int] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/pipeline"
        commands = json.loads(request.content)
        sent.extend(commands)
        out = []
        for cmd in commands:
            if cmd[0] == "HINCRBY":
                db[cmd[2]] = db.get(cmd[2], 0) + cmd[3]
                out.append({"result": db[cmd[2]]})
            elif cmd[0] == "HGETALL":
                flat = [x for k, v in db.items() for x in (k, str(v))]
                out.append({"result": flat})
            else:
                out.append({"result": 1})
        return httpx.Response(200, json=out)

    store = UpstashUsageStore("https://redis.example", "tok", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await store.record("google/gemini-2.5-flash", parse_usage({"prompt_tokens": 40, "completion_tokens": 8, "cost": 0.5}), TODAY)
    assert sent[-1] == ["EXPIRE", "usage:2026-10-02", 40 * 86400]
    loaded = (await store.load([TODAY]))[TODAY]["google/gemini-2.5-flash"]
    assert (loaded.calls, loaded.prompt, loaded.completion, loaded.cost_micro) == (1, 40, 8, 500000)


def test_report_labels_bot_and_key_sections_separately():
    per_day = {TODAY: {"m1": parse_usage({"prompt_tokens": 18400, "completion_tokens": 3100, "cached_tokens": 9200, "cost": 0.04})}}
    key = {"usage_daily": 0.05, "usage_weekly": 0.21, "usage_monthly": 0.6, "usage": 1.5, "limit": 5, "limit_remaining": 4.2}
    text = format_report(per_day, TODAY, "m1", key)
    assert "Botul (numărat de bot, doar apelurile lui)" in text
    assert "Cheia OpenRouter (toate folosirile cheii, nu doar botul" in text
    assert "Azi: 1 apeluri · in 18.4k · out 3.1k · cache 9.2k · $0.0400" in text
    assert "Limită $5.00 · rămas $4.20" in text and "octombrie" in text


def test_report_without_key_info_or_cost():
    per_day = {TODAY: {"m1": parse_usage({"prompt_tokens": 10})}}
    text = format_report(per_day, TODAY, "m1", None)
    assert "cost n/a" in text and "Cheia OpenRouter" not in text
    assert "Azi: niciun apel" in format_report({}, TODAY, "m1", None)


async def test_fetch_key_info_only_for_openrouter(settings):
    assert await fetch_key_info(settings) is None  # llm.example, no request made

    openrouter = settings.model_copy(update={"llm_base_url": "https://openrouter.ai/api/v1"})

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/key" and request.headers["authorization"] == "Bearer k"
        return httpx.Response(200, json={"data": {"usage_daily": 0.1, "limit": None}})

    info = await fetch_key_info(openrouter, httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert info == {"usage_daily": 0.1, "limit": None}


async def test_fetch_key_info_failure_returns_none(settings):
    openrouter = settings.model_copy(update={"llm_base_url": "https://openrouter.ai/api/v1"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401)))
    assert await fetch_key_info(openrouter, client) is None


async def test_llm_records_every_call_including_the_repair_retry(settings):
    answers = iter(["not json", json.dumps({"action": "chat", "reply": "Salut!"})])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "routed/model",
                "choices": [{"message": {"content": next(answers)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.01},
            },
        )

    usage = MemoryUsageStore()
    llm = OpenAICompatLLM(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)), usage)
    await llm.next_turn([{"role": "user", "content": "salut"}])
    recorded = (await usage.load([datetime.now(TZ).date()]))[datetime.now(TZ).date()]["routed/model"]
    assert recorded.calls == 2 and recorded.prompt == 200 and recorded.cost_micro == 20000


async def test_usage_recording_failure_never_breaks_the_reply(settings):
    class Broken:
        async def record(self, *args, **kwargs):
            raise RuntimeError("redis down")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"action": "chat", "reply": "Salut!"}'}}]})

    llm = OpenAICompatLLM(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)), Broken())
    assert (await llm.next_turn([{"role": "user", "content": "salut"}])).reply == "Salut!"


async def test_usage_command_replies_with_the_report(settings):
    usage = MemoryUsageStore()
    await usage.record("m1", parse_usage({"prompt_tokens": 1200, "completion_tokens": 300, "cost": 0.02}), TODAY)

    async def key_info():
        return {"usage_daily": 0.02, "usage_weekly": 0.02, "usage_monthly": 0.02, "usage": 0.02}

    tg = FakeTelegram()
    bot = Bot(settings, tg, FakeLLM([]), FakeGitHub(), MemoryStore(), usage=usage, key_info=key_info,
              clock=lambda: datetime(2026, 10, 2, 12, 0, tzinfo=TZ))
    await bot.handle_update(text_update("/usage"))
    text = tg.sent[-1][1]
    assert tg.sent[-1][0] == CHAT_ID and "Botul (numărat" in text and "Cheia OpenRouter" in text and "in 1.2k" in text


async def test_usage_command_without_a_store(make_bot):
    bot, llm, tg, gh = make_bot([])
    await bot.handle_update(text_update("/usage"))
    assert tg.sent[-1][1] == "Raportul de consum nu e configurat."
