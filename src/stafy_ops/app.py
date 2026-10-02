import hmac
import logging
from functools import lru_cache

from fastapi import FastAPI, Header, HTTPException, Request

from stafy_ops.bootstrap import build_bot
from stafy_ops.config import get_settings

log = logging.getLogger(__name__)
app = FastAPI(title="Stafy Ops Bot")


@lru_cache
def _bot():
    return build_bot(persistent=True)[0]


@app.get("/health")
async def health() -> dict:
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request, x_telegram_bot_api_secret_token: str | None = Header(default=None)
) -> dict:
    secret = get_settings().telegram_webhook_secret
    if not secret or not hmac.compare_digest(x_telegram_bot_api_secret_token or "", secret):
        raise HTTPException(status_code=401)
    try:
        await _bot().handle_update(await request.json())
    except Exception:  # noqa: BLE001 — always 200, otherwise Telegram retries the same update forever
        log.exception("handle_update failed")
    return {"ok": True}
