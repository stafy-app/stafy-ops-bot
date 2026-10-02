# Deployment (Vercel + Upstash Redis)

Webhook mode on Vercel Functions (Python/FastAPI, entrypoint = root `app.py`). Conversation state lives in Upstash Redis because serverless invocations share no memory.

## 1. Project

Vercel → Add New → Project → import `stafy-app/stafy-ops-bot` (the repo must be pushed first). Framework preset: the detected Python/FastAPI one; leave root directory and build settings at their defaults.

## 2. Redis

Project → Storage → Create Database → Upstash (Redis) → free plan → connect to this project, all environments. The integration injects `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` (or `KV_REST_API_URL` / `KV_REST_API_TOKEN`); the app reads either pair. Nothing to create inside Redis — keys are written on demand and expire after 72 h of inactivity.

## 3. Environment variables

Project → Settings → Environment Variables (Production; mark secrets as Sensitive):

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | BotFather token |
| `TELEGRAM_ALLOWED_USER_IDS` | your numeric Telegram user ID |
| `TELEGRAM_WEBHOOK_SECRET` | random, 16-256 chars of `A-Za-z0-9_-`: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `GITHUB_TOKEN` | PAT, see `CLAUDE.md` GitHub mechanics |
| `GITHUB_ORG`, `GITHUB_PROJECT_NUMBER` | `stafy-app`, `1` |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | OpenRouter (or other OpenAI-compatible) |

Redeploy after changing variables — they apply to new deployments only.

## 4. Verify the deployment

Open `https://<project>.vercel.app/health` → `{"ok": true}`. Use the **production domain**, not a per-deployment URL. If Vercel Authentication protects it, Telegram gets a 401 and `webhook-info` shows `last_error_message` — exclude the production domain from Deployment Protection.

## 5. Point Telegram at it

Put the same `TELEGRAM_WEBHOOK_SECRET` in your local `.env`, stop `just poll`, then:

```
just webhook-set https://<project>.vercel.app
just webhook-info      # url set, last_error_message absent
```

Send `/start` to the bot.

## Back to local polling

```
just webhook-delete
just poll
```

Telegram allows either a webhook or `getUpdates`, never both.
