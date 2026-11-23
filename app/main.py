from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .config import Settings
from .database import Database
from .handlers import build_router
from .transcription import TranscriptionService


LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def cleanup_loop(db: Database) -> None:
    while True:
        await asyncio.sleep(6 * 60 * 60)
        try:
            await db.cleanup()
        except Exception:
            LOGGER.exception("Периодическая очистка базы не выполнена")


async def configure_bot_menu(bot: Bot) -> None:
    await bot.set_my_commands(
        [
            BotCommand(command="new", description="Создать одноразовый код"),
            BotCommand(command="join", description="Подключиться по коду"),
            BotCommand(command="status", description="Проверить подключение"),
            BotCommand(command="leave", description="Отключиться"),
            BotCommand(command="del", description="Удалить сообщение у обоих"),
            BotCommand(command="help", description="Помощь"),
        ]
    )


async def main() -> None:
    configure_logging()
    settings = Settings.load()
    db = Database(settings.database_path)
    await db.connect()

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    transcription = TranscriptionService(bot, settings, db)
    await transcription.start()

    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(build_router(db, settings, transcription))
    cleanup_task = asyncio.create_task(cleanup_loop(db), name="database-cleanup")

    try:
        await bot.delete_webhook(drop_pending_updates=False)
        await configure_bot_menu(bot)
        allowed_updates = set(dispatcher.resolve_used_update_types())
        allowed_updates.add("message_reaction")
        LOGGER.info("Запуск long polling; updates=%s", sorted(allowed_updates))
        await dispatcher.start_polling(
            bot,
            allowed_updates=sorted(allowed_updates),
            polling_timeout=30,
            handle_as_tasks=True,
            tasks_concurrency_limit=settings.update_concurrency_limit,
            close_bot_session=False,
        )
    finally:
        cleanup_task.cancel()
        await asyncio.gather(cleanup_task, return_exceptions=True)
        await transcription.stop()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
