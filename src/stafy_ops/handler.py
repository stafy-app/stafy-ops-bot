import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

import httpx

from stafy_ops.config import Settings
from stafy_ops.conversation import Conversation, ConversationStore
from stafy_ops.github import IssueCreator
from stafy_ops.llm import LLM, LLMError, history_message
from stafy_ops.render import render_body
from stafy_ops.schemas import MILESTONE_REPOS, IssueDraft
from stafy_ops.telegram import Messenger

log = logging.getLogger(__name__)

_KEYBOARD = {
    "inline_keyboard": [
        [
            {"text": "Create", "callback_data": "create"},
            {"text": "Edit", "callback_data": "edit"},
            {"text": "Cancel", "callback_data": "cancel"},
        ]
    ]
}
_HELP = (
    "Trimite o idee, un bug sau o sarcină. Pun întrebări dacă ceva nu e clar, apoi îți arăt un draft.\n"
    "/draft — forțează draftul acum\n/cancel — renunță la conversația curentă"
)


def format_preview(d: IssueDraft) -> str:
    milestone = ""
    if d.repo in MILESTONE_REPOS:
        milestone = f"Milestone: {d.milestone or 'none (no open milestone or release tag found in the repo)'}\n"
    header = (
        f"{d.repo.value} · {' · '.join(d.labels)} · Priority {d.priority.value} · Size {d.size.value}\n"
        f"{milestone}Assignee: you\n\n"
        f"{d.title}\n\n"
    )
    return header + render_body(d)


class Bot:
    def __init__(self, settings: Settings, tg: Messenger, llm: LLM, gh: IssueCreator, store: ConversationStore) -> None:
        self._s, self._tg, self._llm, self._gh, self._store = settings, tg, llm, gh, store

    @contextlib.asynccontextmanager
    async def _typing(self, chat_id: int) -> AsyncIterator[None]:
        """Shows Telegram's "typing..." indicator while work is in flight (it lasts ~5 s, so refresh every 4 s)."""

        async def ping() -> None:
            try:
                await self._tg.send_chat_action(chat_id)
            except httpx.HTTPError as exc:  # cosmetic only — never break the real work
                log.debug("chat action failed: %s", exc)

        async def refresh() -> None:
            while True:
                await asyncio.sleep(4)
                await ping()

        await ping()
        task = asyncio.create_task(refresh())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def handle_update(self, update: dict) -> None:
        if (update_id := update.get("update_id")) is not None and await self._store.seen(update_id):
            return
        if message := update.get("message"):
            if message.get("from", {}).get("id") in self._s.allowed_user_ids and message.get("text"):
                await self._on_text(message["chat"]["id"], message["text"].strip())
        elif callback := update.get("callback_query"):
            if callback.get("from", {}).get("id") in self._s.allowed_user_ids:
                await self._tg.answer_callback(callback["id"])
                await self._on_button(callback["message"]["chat"]["id"], callback.get("data", ""))

    async def _on_text(self, chat_id: int, text: str) -> None:
        command = text.split()[0].lower() if text.startswith("/") else None
        conv = await self._store.get(chat_id)
        if command == "/start":
            await self._tg.send_message(chat_id, _HELP)
        elif command == "/cancel":
            await self._store.delete(chat_id)
            await self._tg.send_message(chat_id, "Anulat.")
        elif command == "/draft":
            if not conv.messages:
                await self._tg.send_message(chat_id, "Nu am nimic de rezumat încă.")
                return
            conv.messages.append({"role": "user", "content": "Draft now with what we have."})
            await self._advance(chat_id, conv, force=True)
        elif command:
            await self._tg.send_message(chat_id, _HELP)
        else:
            conv.pending = None  # a reply after a draft means "edit"
            conv.messages.append({"role": "user", "content": text})
            await self._advance(chat_id, conv)

    async def _advance(self, chat_id: int, conv: Conversation, force: bool = False) -> None:
        limit = self._s.max_question_rounds
        try:
            async with self._typing(chat_id):
                turn = await self._llm.next_turn(conv.messages, force_draft=force or conv.rounds >= limit)
        except (LLMError, httpx.HTTPError) as exc:
            log.warning("LLM failed: %s", exc)
            await self._tg.send_message(chat_id, "Nu am putut procesa mesajul (eroare LLM). Încearcă din nou.")
            return
        conv.messages.append(history_message(turn))
        if turn.action == "ask":
            conv.rounds += 1
            lines = "\n".join(f"{n}. {q}" for n, q in enumerate(turn.questions, 1))
            await self._tg.send_message(chat_id, f"Întrebări (runda {conv.rounds}/{limit}):\n{lines}")
        else:
            issue = turn.issue
            try:
                issue = issue.model_copy(update={"milestone": await self._gh.resolve_milestone(issue)})
            except httpx.HTTPError as exc:
                log.warning("Milestone resolve failed: %s", exc)
            conv.pending = issue
            await self._tg.send_message(chat_id, format_preview(issue), reply_markup=_KEYBOARD)
        await self._store.save(chat_id, conv)

    async def _on_button(self, chat_id: int, action: str) -> None:
        conv = await self._store.get(chat_id)
        if action == "cancel":
            await self._store.delete(chat_id)
            await self._tg.send_message(chat_id, "Anulat.")
        elif action == "edit":
            conv.pending = None
            await self._store.save(chat_id, conv)
            await self._tg.send_message(chat_id, "Ce vrei să schimbi?")
        elif action == "create":
            if conv.pending is None:
                await self._tg.send_message(chat_id, "Nu am un draft activ.")
                return
            try:
                async with self._typing(chat_id):
                    created = await self._gh.create_issue(conv.pending)
            except httpx.HTTPError as exc:
                log.warning("GitHub create failed: %s", exc)
                await self._tg.send_message(chat_id, f"GitHub a refuzat crearea: {exc}. Draftul rămâne, apasă Create din nou.")
                return
            await self._store.delete(chat_id)
            note = "\n".join(f"⚠ {w}" for w in created.warnings)
            await self._tg.send_message(chat_id, f"Creat: {created.url}" + (f"\n{note}" if note else ""))
