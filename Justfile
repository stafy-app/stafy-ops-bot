set windows-shell := ["powershell.exe", "-NoLogo", "-Command"]

# List available recipes
default:
    @just --list

# Install dependencies
install:
    uv sync

# Run the bot locally with Telegram long-polling (no public URL needed)
poll:
    uv run python -m stafy_ops.polling

# Run the webhook server on http://localhost:8001 (/health, POST /telegram/webhook; no /docs)
dev:
    uv run uvicorn stafy_ops.app:app --reload --port 8001

# Point Telegram at the deployed webhook: just webhook-set https://<project>.vercel.app
webhook-set url:
    uv run python -m stafy_ops.webhook set {{ url }}

# Show Telegram's current webhook state (pending updates, last error)
webhook-info:
    uv run python -m stafy_ops.webhook info

# Remove the webhook (required before long-polling with `just poll` again)
webhook-delete:
    uv run python -m stafy_ops.webhook delete

# Run tests
test:
    uv run pytest
