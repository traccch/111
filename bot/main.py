"""Точка входа: настройка бота и запуск long polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .config import load_config
from .db import Database
from .handlers import build_router
from .middlewares import UserMiddleware

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="stats", description="Сводка расходов"),
    BotCommand(command="last", description="Последние траты"),
    BotCommand(command="undo", description="Удалить последнюю трату"),
    BotCommand(command="limit", description="Лимит на месяц"),
    BotCommand(command="limits", description="Список лимитов"),
    BotCommand(command="cats", description="Категории"),
    BotCommand(command="export", description="Выгрузка в CSV"),
    BotCommand(command="help", description="Как пользоваться"),
]


async def run() -> None:
    config = load_config()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    db = Database(config.db_path, config.default_tz, config.default_currency)
    await db.connect()

    bot = Bot(
        token=config.token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher["db"] = db

    middleware = UserMiddleware()
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)
    dispatcher.include_router(build_router())

    try:
        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logger.info("Запускаюсь как @%s", me.username)
        await dispatcher.start_polling(bot)
    finally:
        await db.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлен")


if __name__ == "__main__":
    main()
