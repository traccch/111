"""Графики: /chart и переключение вида кнопками."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InputMediaPhoto, Message

from .. import charts
from ..db import Database, UserSettings
from ..keyboards import chart_kinds
from ..services import month_end, month_start

router = Router(name="charts")

NO_MATPLOTLIB = (
    "📉 Для картинок нужен matplotlib, а он не установлен.\n\n"
    "Поставить: <code>pip install matplotlib</code> — или просто запусти бота "
    "через <code>run.sh</code> / <code>run.bat</code>, они ставят всё сами.\n"
    "Без него всё остальное работает: сводка — /stats, выгрузка — /export."
)

EMPTY = "Пока нечего рисовать — запиши хотя бы пару трат."


async def render(
    db: Database, user: UserSettings, kind: str, today: dt.date
) -> tuple[bytes, str] | None:
    """PNG и имя файла, либо None, если данных нет."""
    if kind == "cats":
        start, end = month_start(today), month_end(today)
        totals = await db.totals_by_category(user.user_id, start, end)
        if not totals:
            return None
        items = [(item.name, item.total) for item in totals]
        title = f"Расходы по категориям · {charts.month_name(today.strftime('%Y-%m'))}"
        return charts.category_chart(items, user.currency, title), "categories.png"

    if kind == "months":
        items = await db.monthly_totals(user.user_id, months=12)
        if len(items) < 2:
            return None
        return charts.monthly_chart(items, user.currency, "Расходы по месяцам"), "months.png"

    start, end = month_start(today), today
    totals = await db.daily_totals(user.user_id, start, end)
    if not totals:
        return None
    days = [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]
    amounts = [totals.get(day, 0) for day in days]
    title = f"Расходы по дням · {charts.month_name(today.strftime('%Y-%m'))}"
    return charts.daily_chart(days, amounts, user.currency, title), "days.png"


@router.message(Command("chart", "graph"))
async def cmd_chart(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    if not charts.available():
        await message.answer(NO_MATPLOTLIB)
        return

    drawn = await render(db, user, "days", today)
    if drawn is None:
        await message.answer(EMPTY)
        return

    image, filename = drawn
    await message.answer_photo(
        BufferedInputFile(image, filename=filename), reply_markup=chart_kinds("days")
    )


@router.callback_query(F.data.startswith("chart:"))
async def cb_chart(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    kind = callback.data.split(":", 1)[1]
    if kind not in {"days", "cats", "months"}:
        await callback.answer()
        return

    drawn = await render(db, user, kind, today)
    if drawn is None:
        await callback.answer(
            "Для этого графика пока мало данных — нужны траты хотя бы за два месяца."
            if kind == "months"
            else EMPTY,
            show_alert=True,
        )
        return

    image, filename = drawn
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_media(
            InputMediaPhoto(media=BufferedInputFile(image, filename=filename)),
            reply_markup=chart_kinds(kind),
        )
    except TelegramBadRequest:
        pass  # то же изображение — Telegram ругается, для нас это не ошибка
