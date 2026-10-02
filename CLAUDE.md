# Stafy Ops Bot

Internal dev tooling, not part of the product. A Telegram bot: the developer sends a short message, an LLM asks clarifying questions until the request is clear (max `MAX_QUESTION_ROUNDS`, default 6), then proposes a draft issue; on **Create** the bot opens it on GitHub with labels, Priority, Status and Size and adds it to the `Stafy - Kanban` project (`stafy-app` org project #1). Python 3.13, FastAPI, httpx, pydantic-settings, `uv`. It talks to Telegram, the LLM and GitHub only — never to `stafy-backend`.

Layout: package `src/stafy_ops/` (all module paths below are relative to it), tests in `tests/`.

## Output Rules (Zero Fluff)

- No preamble, pleasantries, or closing remarks.
- Output only the requested change or direct answer.
- Never rewrite an entire file for a partial change — make targeted, surgical edits.

## Deliberate Decisions — don't silently reverse

- **LLM provider is pluggable, not Claude-specific.** `llm.py` speaks the OpenAI-compatible `/chat/completions` protocol with `response_format: json_object` only (lowest common denominator across Gemini, DeepSeek, etc.). The JSON schema is embedded in the prompt (`prompts.py`) and enforced by Pydantic (`schemas.LLMTurn`) with one repair retry. Don't add provider-specific SDKs or features (forced tool use, native schema mode) without discussing.
- **Language split.** Questions to the user are Romanian; issue content is English (matches the repos and templates).
- **One structured turn per LLM call**: `LLMTurn.action` is `ask` (1-3 questions) or `draft` (full `IssueDraft`). After `MAX_QUESTION_ROUNDS` ask-rounds, or on `/draft`, the LLM is called with `force_draft=True` and an `ask` reply is rejected and retried.
- **The LLM never sees code.** `context` holds only what the user stated (else `Not stated`); the prompt forbids inventing paths/symbols. A read-only `CLAUDE.md`/`docs/**` fetch tool is a possible later step, not built.
- **Enums are fixed** in `schemas.py`: repos `stafy-backend|stafy-web-app|stafy-landing|workspace` (**`stafy-mobile` excluded**, no `area: mobile`), areas `backend|webapp|landing|infra` (chosen by what must be done, not by repo), kinds `bug|feature|docs|chore`, Priority `Urgent|High|Medium|Low`, Size `XS-XL`. Labels: `bug`/`enhancement`/`documentation` (chore gets none) + `area: <area>`.
- **Issue body is rendered in code** (`render.py`) and mirrors the section headings of the workspace repo's `.github/ISSUE_TEMPLATE/{feature,bug,chore}.md`. Change both together.
- **Never commits, pushes or pulls on the user's behalf** — same workspace rule as every repo.

## GitHub mechanics (`github.py`)

| Step | API |
|---|---|
| Assignee | Always the token owner (`GET /user`, cached) — no login in config. Shown as `Assignee: you` in the preview; the Project's Assignees column follows the issue |
| Milestone | **Only `stafy-backend` and `stafy-web-app`** (`schemas.MILESTONE_REPOS`; landing and workspace are not versioned and never get one, even if requested). Chosen by code, never guessed by the LLM (`GitHubClient.resolve_milestone`): the user's explicit `vX.Y.Z` (`IssueDraft.milestone`) wins; else the lowest open `vX.Y.Z` milestone of the target repo; else the repo's latest `vX.Y.Z` tag with minor + 1; else none. GitHub is the only source — no milestone value lives in `.env` or code. Shown in the preview; the user changes it via Edit ("pune-l în v0.3.0"). Created in the repo if missing. Per repo, since repos version independently |
| Create issue (title, body, labels, milestone, assignee) | REST `POST /repos/{org}/{repo}/issues` |
| Priority | **Org issue field**, set on the issue: REST `POST .../issues/{n}/issue-field-values` with `field_id` looked up by name from `GET /orgs/{org}/issue-fields`. Not a Project field — `updateProjectV2Field` rejects it |
| Add to project | GraphQL `addProjectV2ItemById` (called explicitly; the project's auto-add workflow is async and can't be relied on for the item id) |
| Status = Backlog, Size | GraphQL `updateProjectV2ItemFieldValue` (single-select options resolved by name from the project's fields) |

Steps after issue creation return warnings instead of raising, so the created issue is never lost. Token: fine-grained PAT / GitHub App with Issues (write) on the repos and Projects (read/write) for the org — separate from the dev `gh` login.

## Runtime

- **Polling** (`just poll`): `polling.py` long-polls `getUpdates`; state in memory (`conversation.MemoryStore`). Runs only while the process is up.
- **Webhook** (`just dev` locally, Vercel in production): `src/stafy_ops/app.py` `POST /telegram/webhook`, verifies `X-Telegram-Bot-Api-Secret-Token` (`TELEGRAM_WEBHOOK_SECRET`, required — empty rejects everything). Always returns 200 so Telegram doesn't retry. Vercel's entrypoint is the root `app.py` (adds `src/` to `sys.path`, re-exports `app`). Webhook mode **requires Upstash Redis** (`build_bot(persistent=True)` raises otherwise) because serverless invocations share no memory. Not Render: the free 750 h/month is consumed by the always-on demo API. Steps: [`docs/deployment.md`](docs/deployment.md).
- **State** (`conversation.py`): `UpstashStore` (Upstash REST over httpx; keys `conv:<chat_id>` and `upd:<update_id>`) when `UPSTASH_REDIS_REST_URL/TOKEN` or `KV_REST_API_URL/TOKEN` are set, else `MemoryStore`. Conversations expire 72 h after their last activity (`TTL_SECONDS`, sliding). `upd:` keys dedupe Telegram's webhook retries. Webhook and long-polling are mutually exclusive on Telegram's side — `just webhook-delete` before `just poll`.
- Only user IDs in `TELEGRAM_ALLOWED_USER_IDS` are served; everyone else is silently ignored.

## CLI

```bash
just install   # uv sync
just poll      # run bot via long-polling (needs .env)
just dev       # webhook server on :8001
just test      # pytest
```

Config: copy `.env.example` to `.env`; `config.Settings` validates it at startup (non-empty tokens, integer allowlist, `LLM_BASE_URL` scheme, `MAX_QUESTION_ROUNDS` 1-10).

## Module Status

| Module | Status | Description |
|---|---|---|
| `config.py` | Live | `Settings` + startup validation |
| `schemas.py` | Live | Enums, `IssueDraft`, `LLMTurn` |
| `prompts.py` | Live | System prompt: repos/areas, when to ask, grounding rules, embedded JSON schema |
| `llm.py` | Live | `OpenAICompatLLM` (JSON mode + one repair retry), `LLM` protocol |
| `conversation.py` | Live | `Conversation`, async `ConversationStore` protocol, `MemoryStore`, `UpstashStore` (72 h sliding TTL, update-id dedupe) |
| `webhook.py` | Live | `just webhook-set <url>` / `webhook-info` / `webhook-delete` |
| root `app.py` | Live | Vercel entrypoint |
| `render.py` | Live | `render_body(IssueDraft)` per kind |
| `github.py` | Live, untested against the live API | `GitHubClient.create_issue` |
| `telegram.py` | Live, untested against the live API | `TelegramClient` over httpx |
| `handler.py` | Live | `Bot.handle_update`: allowlist, `/start /draft /cancel`, ask/draft loop, Create/Edit/Cancel buttons |
| `polling.py`, `app.py`, `bootstrap.py` | Live | Entry points + wiring |

## Where to Look

| Task | Start here |
|---|---|
| Change what the bot asks / how it decides | `prompts.py` |
| Add a field to issues | `schemas.IssueDraft` → `render.py` → `github.py` |
| Swap LLM provider | `.env` (`LLM_BASE_URL`, `LLM_MODEL`); code only if the provider isn't OpenAI-compatible |
| Issue templates (human-facing) | `../.github/ISSUE_TEMPLATE/` (workspace repo) |
