import asyncio
import logging

from stafy_ops.bootstrap import build_bot

log = logging.getLogger(__name__)


async def run() -> None:
    bot, tg = build_bot()
    offset: int | None = None
    log.info("Polling Telegram...")
    while True:
        try:
            updates = await tg.get_updates(offset)
        except Exception:  # noqa: BLE001 — keep polling through network blips
            log.exception("getUpdates failed")
            await asyncio.sleep(5)
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                await bot.handle_update(update)
            except Exception:  # noqa: BLE001 — one bad update must not kill the loop
                log.exception("handle_update failed")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
