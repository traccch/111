"""Точка входа: настройка бота и запуск long polling."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand

from .ai import AiClient
from .config import load_config
from .db import Database
from .handlers import build_router
from .middlewares import UserMiddleware
from .reminders import ReminderScheduler

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="add", description="Записать трату по шагам"),
    BotCommand(command="stats", description="Сводка расходов"),
    BotCommand(command="chart", description="Графики"),
    BotCommand(command="insight", description="Разбор месяца словами"),
    BotCommand(command="last", description="Последние траты"),
    BotCommand(command="undo", description="Удалить последнюю трату"),
    BotCommand(command="limit", description="Лимит на месяц"),
    BotCommand(command="limits", description="Список лимитов"),
    BotCommand(command="remind", description="Напоминать записывать траты"),
    BotCommand(command="cats", description="Категории"),
    BotCommand(command="export", description="Выгрузка: PDF, CSV, JSON"),
    BotCommand(command="import", description="Залить правки от ИИ"),
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
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher["db"] = db
    dispatcher["ai"] = AiClient(config.ai_api_key, config.ai_model, config.ai_timeout)

    middleware = UserMiddleware()
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)
    dispatcher.include_router(build_router())

    scheduler = ReminderScheduler(bot, db)

    try:
        try:
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            print(
                "\n✗ Telegram не принял токен.\n"
                "  Проверь строку BOT_TOKEN в файле .env — она должна быть ровно такой,\n"
                "  какую прислал @BotFather. Если бот удалён или токен отозван,\n"
                "  получи новый через /newbot или /token у @BotFather.\n"
            )
            return
        except TelegramNetworkError:
            print(
                "\n✗ Не получается достучаться до Telegram.\n"
                "  Проверь интернет. Если Telegram блокируется провайдером,\n"
                "  запусти бота через VPN.\n"
            )
            return

        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        scheduler.start()
        logger.info("Бот @%s запущен. Открой его в Telegram и напиши /start", me.username)
        print(f"\n✓ Бот работает: https://t.me/{me.username}\n")
        await dispatcher.start_polling(bot)
    finally:
        await scheduler.stop()
        await db.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Остановлен")


if __name__ == "__main__":
    main()
