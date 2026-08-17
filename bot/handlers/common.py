"""/start, /help и настройки (валюта, часовой пояс)."""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message

from ..db import Database, UserSettings
from ..formatting import esc

router = Router(name="common")

HELP = """💰 <b>Бот учёта расходов</b>

<b>Записать трату</b> — просто напиши сообщение:
· <code>кофе 300</code>
· <code>450 такси</code>
· <code>продукты 1 250,50</code>
· <code>аренда 25к</code>
· <code>такси 450 вчера</code>
· <code>подарок 3000 05.08</code>

Категория подставляется сама по ключевым словам. Если промахнулась — нажми
«✏️ Категория», и бот запомнит слово на будущее.

<b>Отчёты</b>
/stats — сводка с переключением периода
/last — последние траты (с кнопками удаления)
/undo — удалить последнюю трату
/del <code>id</code> — удалить трату по номеру
/export — выгрузка в CSV

<b>Категории</b>
/cats — список категорий и ключевых слов
/addcat <code>🍕 Пицца, пицца, додо</code> — добавить
/delcat <code>Пицца</code> — удалить (траты уедут в «Прочее»)
/kw <code>Кафе, шаурма</code> — привязать слово к категории

<b>Напоминания</b>
/remind <code>21:00</code> — напоминать записывать траты
/remind <code>off</code> — выключить все
/reminders — список и настройки

<b>Лимиты на месяц</b>
/limit <code>30000</code> — общий лимит
/limit <code>Кафе 8000</code> — лимит по категории
/limit <code>Кафе 0</code> — снять лимит
/limits — список лимитов

<b>Настройки</b>
/currency <code>$</code> — валюта
/tz <code>Europe/Moscow</code> — часовой пояс
"""


@router.message(CommandStart())
async def cmd_start(message: Message, user: UserSettings) -> None:
    await message.answer(
        "Привет! Я считаю твои расходы.\n\n"
        "Просто пиши тратами: <code>кофе 300</code>, <code>такси 450 вчера</code>, "
        "<code>продукты 1 250,50</code>.\n"
        "Я сам разберу сумму, дату и категорию.\n\n"
        "Что дальше: /stats — сводка, /limit — лимит на месяц, /help — всё остальное."
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP)


@router.message(Command("currency"))
async def cmd_currency(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    value = (command.args or "").strip()
    if not value:
        await message.answer(
            f"Текущая валюта: <b>{esc(user.currency)}</b>\n"
            "Сменить: <code>/currency $</code>"
        )
        return
    if len(value) > 8:
        await message.answer("Слишком длинное обозначение валюты, максимум 8 символов.")
        return
    await db.set_currency(user.user_id, value)
    await message.answer(f"Готово, теперь считаю в <b>{esc(value)}</b>.")


@router.message(Command("tz"))
async def cmd_tz(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    value = (command.args or "").strip()
    if not value:
        await message.answer(
            f"Текущий часовой пояс: <b>{esc(user.tz)}</b>\n"
            "Сменить: <code>/tz Europe/Berlin</code>"
        )
        return
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        await message.answer(
            "Не знаю такой часовой пояс. Нужен формат IANA, например "
            "<code>Europe/Moscow</code> или <code>Asia/Almaty</code>."
        )
        return
    await db.set_tz(user.user_id, value)
    await message.answer(f"Часовой пояс: <b>{esc(value)}</b>.")
