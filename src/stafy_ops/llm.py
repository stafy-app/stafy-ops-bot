import json
import logging
from typing import Protocol

import httpx
from pydantic import ValidationError

from stafy_ops.config import Settings
from stafy_ops.prompts import build_system_prompt
from stafy_ops.schemas import LLMTurn
from stafy_ops.usage import UsageStore, parse_usage

log = logging.getLogger(__name__)


class LLMError(Exception):
    pass


class LLM(Protocol):
    async def next_turn(self, messages: list[dict], *, force_draft: bool = False) -> LLMTurn: ...


def _extract_json(raw: str) -> str:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in response")
    return raw[start : end + 1]


class OpenAICompatLLM:
    """Any OpenAI-compatible /chat/completions endpoint with JSON mode (Gemini, DeepSeek, ...).

    Only `response_format: json_object` is used (lowest common denominator); the schema lives in
    the prompt and is enforced by Pydantic, with one repair retry.
    """

    def __init__(
        self, settings: Settings, client: httpx.AsyncClient | None = None, usage: UsageStore | None = None
    ) -> None:
        self._s = settings
        self._client = client or httpx.AsyncClient(timeout=60)
        self._usage = usage

    async def _record(self, body: dict) -> None:
        """Counts one call (retries included). Accounting must never break a reply."""
        if self._usage is None:
            return
        try:
            await self._usage.record(body.get("model") or self._s.llm_model, parse_usage(body.get("usage")))
        except Exception:  # noqa: BLE001
            log.warning("Usage recording failed", exc_info=False)

    async def _complete(self, messages: list[dict]) -> str:
        resp = await self._client.post(
            f"{self._s.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {self._s.llm_api_key}"},
            json={
                "model": self._s.llm_model,
                "messages": messages,
                "response_format": {"type": "json_object"},
            },
        )
        resp.raise_for_status()
        body = resp.json()
        await self._record(body)
        return body["choices"][0]["message"]["content"] or ""

    async def next_turn(self, messages: list[dict], *, force_draft: bool = False) -> LLMTurn:
        convo = [{"role": "system", "content": build_system_prompt(force_draft)}, *messages]
        last_error = "unknown"
        for _ in range(2):
            raw = await self._complete(convo)
            try:
                turn = LLMTurn.model_validate_json(_extract_json(raw))
                if force_draft and turn.action != "draft":
                    raise ValueError('action must be "draft"')
                return turn
            except (ValidationError, ValueError) as exc:
                last_error = str(exc)
                convo = [
                    *convo,
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"Invalid output: {last_error}. Return only the corrected JSON."},
                ]
        raise LLMError(f"LLM returned invalid output twice: {last_error}")


def history_message(turn: LLMTurn) -> dict:
    return {"role": "assistant", "content": json.dumps(turn.model_dump(mode="json"))}
