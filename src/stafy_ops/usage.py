"""LLM usage accounting: per-call capture, daily counters, and the `/usage` report.

Two clearly separate sources: what the bot itself counted from each response's `usage`, and (OpenRouter only)
what OpenRouter reports for the whole API key, which also includes any other use of that key.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from stafy_ops.config import Settings
from stafy_ops.upstash import UpstashClient

log = logging.getLogger(__name__)

TZ = ZoneInfo("Europe/Bucharest")  # "today" ends at the user's midnight, not UTC's
RETENTION_DAYS = 40
_FIELDS = ("calls", "prompt", "completion", "cached", "reasoning", "cost_micro", "costed_calls")
_MONTHS = ("ianuarie", "februarie", "martie", "aprilie", "mai", "iunie", "iulie", "august", "septembrie", "octombrie", "noiembrie", "decembrie")


@dataclass
class Usage:
    calls: int = 0
    prompt: int = 0
    completion: int = 0
    cached: int = 0
    reasoning: int = 0
    cost_micro: int = 0  # millionths of a dollar, so sums stay exact
    costed_calls: int = 0  # calls whose response carried a cost (OpenRouter does, most others don't)

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(*(getattr(self, f) + getattr(other, f) for f in _FIELDS))

    @property
    def cost(self) -> float:
        return self.cost_micro / 1_000_000


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_usage(raw: dict | None) -> Usage:
    """One call's usage from an OpenAI-compatible response; tolerant of provider differences."""
    raw = raw or {}
    cached = (
        raw.get("cached_tokens")
        or (raw.get("prompt_tokens_details") or {}).get("cached_tokens")
        or raw.get("prompt_cache_hit_tokens")  # DeepSeek
    )
    reasoning = raw.get("reasoning_tokens") or (raw.get("completion_tokens_details") or {}).get("reasoning_tokens")
    try:
        cost_micro = round(float(raw["cost"]) * 1_000_000) if raw.get("cost") is not None else None
    except (TypeError, ValueError):
        cost_micro = None
    return Usage(
        calls=1,
        prompt=_int(raw.get("prompt_tokens")),
        completion=_int(raw.get("completion_tokens")),
        cached=_int(cached),
        reasoning=_int(reasoning),
        cost_micro=cost_micro or 0,
        costed_calls=0 if cost_micro is None else 1,
    )


class UsageStore(Protocol):
    async def record(self, model: str, usage: Usage, day: date | None = None) -> None: ...
    async def load(self, days: list[date]) -> dict[date, dict[str, Usage]]: ...


class MemoryUsageStore:
    def __init__(self) -> None:
        self._data: dict[date, dict[str, Usage]] = {}

    async def record(self, model: str, usage: Usage, day: date | None = None) -> None:
        per_model = self._data.setdefault(day or datetime.now(TZ).date(), {})
        per_model[model] = per_model.get(model, Usage()) + usage

    async def load(self, days: list[date]) -> dict[date, dict[str, Usage]]:
        return {d: dict(self._data.get(d, {})) for d in days}


class UpstashUsageStore:
    """One Redis hash per day (`usage:YYYY-MM-DD`), fields `<model>|<counter>`, expiring after RETENTION_DAYS."""

    def __init__(self, url: str, token: str, client: httpx.AsyncClient | None = None) -> None:
        self._r = UpstashClient(url, token, client)

    @staticmethod
    def _key(day: date) -> str:
        return f"usage:{day.isoformat()}"

    async def record(self, model: str, usage: Usage, day: date | None = None) -> None:
        key = self._key(day or datetime.now(TZ).date())
        commands: list[list] = [["HINCRBY", key, f"{model}|{f}", getattr(usage, f)] for f in _FIELDS]
        commands.append(["EXPIRE", key, RETENTION_DAYS * 86400])
        await self._r.pipeline(commands)

    async def load(self, days: list[date]) -> dict[date, dict[str, Usage]]:
        results = await self._r.pipeline([["HGETALL", self._key(d)] for d in days])
        loaded: dict[date, dict[str, Usage]] = {}
        for day, flat in zip(days, results):
            per_model: dict[str, Usage] = {}
            for field_name, value in zip(flat[::2], flat[1::2]):
                model, _, counter = field_name.rpartition("|")
                if counter in _FIELDS:
                    setattr(per_model.setdefault(model, Usage()), counter, int(value))
            loaded[day] = per_model
        return loaded


async def fetch_key_info(settings: Settings, client: httpx.AsyncClient | None = None) -> dict | None:
    """OpenRouter's view of the whole API key (GET /key); None for other providers or on any failure."""
    if "openrouter.ai" not in settings.llm_base_url:
        return None
    try:
        http = client or httpx.AsyncClient(timeout=10)
        resp = await http.get(
            f"{settings.llm_base_url.rstrip('/')}/key", headers={"Authorization": f"Bearer {settings.llm_api_key}"}
        )
        resp.raise_for_status()
        body = resp.json()
        return body.get("data", body)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("OpenRouter /key failed: %s", type(exc).__name__)
        return None


def report_days(today: date) -> list[date]:
    """Every day the report needs: the calendar month so far, and at least the last 7 days."""
    return [today - timedelta(n) for n in range(max(today.day, 7))]


def _total(per_day: dict[date, dict[str, Usage]], days: Iterable[date]) -> Usage:
    total = Usage()
    for day in days:
        for usage in per_day.get(day, {}).values():
            total = total + usage
    return total


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _money(value: float) -> str:
    return f"${value:.2f}" if value >= 0.1 else f"${value:.4f}"


def _fmt_cost(u: Usage) -> str:
    if u.costed_calls == 0:
        return "cost n/a"
    text = _money(u.cost)
    return text if u.costed_calls == u.calls else f"{text} ({u.costed_calls}/{u.calls} apeluri cu cost)"


def _fmt_line(label: str, u: Usage) -> str:
    if u.calls == 0:
        return f"{label}: niciun apel"
    parts = [f"{u.calls} apeluri", f"in {fmt_tokens(u.prompt)}", f"out {fmt_tokens(u.completion)}", f"cache {fmt_tokens(u.cached)}"]
    if u.reasoning:
        parts.append(f"reasoning {fmt_tokens(u.reasoning)}")
    parts.append(_fmt_cost(u))
    return f"{label}: " + " · ".join(parts)


def _key_money(info: dict, field_name: str) -> str:
    value = info.get(field_name)
    return _money(float(value)) if isinstance(value, (int, float)) else "n/a"


def format_report(per_day: dict[date, dict[str, Usage]], today: date, model: str, key_info: dict | None) -> str:
    week = [today - timedelta(n) for n in range(7)]
    month = [today - timedelta(n) for n in range(today.day)]
    lines = [
        f"Consum LLM (model configurat: {model})",
        "",
        "Botul (numărat de bot, doar apelurile lui)",
        _fmt_line("Azi", _total(per_day, [today])),
        _fmt_line("Ultimele 7 zile", _total(per_day, week)),
        _fmt_line(f"Luna asta ({_MONTHS[today.month - 1]})", _total(per_day, month)),
    ]
    models: dict[str, Usage] = {}
    for day in month:
        for name, usage in per_day.get(day, {}).items():
            models[name] = models.get(name, Usage()) + usage
    if len(models) > 1:
        lines += [_fmt_line(f"  {name}", u) for name, u in sorted(models.items(), key=lambda kv: -kv[1].calls)]
    if key_info:
        lines += [
            "",
            "Cheia OpenRouter (toate folosirile cheii, nu doar botul; perioadele după calendarul OpenRouter)",
            f"Zilnic {_key_money(key_info, 'usage_daily')} · săptămânal {_key_money(key_info, 'usage_weekly')} · "
            f"lunar {_key_money(key_info, 'usage_monthly')} · total {_key_money(key_info, 'usage')}",
        ]
        if key_info.get("limit") is not None:
            lines.append(f"Limită {_key_money(key_info, 'limit')} · rămas {_key_money(key_info, 'limit_remaining')}")
    return "\n".join(lines)
