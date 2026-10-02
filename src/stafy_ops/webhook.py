"""One-off Telegram webhook management: `python -m stafy_ops.webhook set <https-url> | info | delete`."""

import re
import sys

import httpx

from stafy_ops.config import get_settings


def main(argv: list[str]) -> int:
    settings = get_settings()
    base = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
    command = argv[0] if argv else ""

    if command == "set" and len(argv) == 2:
        url = argv[1].rstrip("/")
        if not url.startswith("https://"):
            print("Webhook URL must be https://")
            return 1
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", settings.telegram_webhook_secret):
            print("TELEGRAM_WEBHOOK_SECRET must be 16-256 chars of A-Z a-z 0-9 _ -")
            return 1
        payload = {
            "url": f"{url}/telegram/webhook",
            "secret_token": settings.telegram_webhook_secret,
            "allowed_updates": ["message", "callback_query"],
        }
        result = httpx.post(f"{base}/setWebhook", json=payload, timeout=20).json()
    elif command == "info":
        result = httpx.get(f"{base}/getWebhookInfo", timeout=20).json()
    elif command == "delete":
        result = httpx.post(f"{base}/deleteWebhook", timeout=20).json()
    else:
        print("usage: python -m stafy_ops.webhook set <https-url> | info | delete")
        return 1

    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
