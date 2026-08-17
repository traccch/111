"""Отчёты: /stats с переключением периода."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..ai import AiClient
from ..db import Database, UserSettings
from ..formatting import esc
from ..keyboards import PERIODS, report_periods
from ..services import build_report, insight_summary, month_end, month_start
from .ai_common import NO_AI

router = Router(name="reports")

VALID_PERIODS = {key for key, _ in PERIODS}


@router.message(Command("stats", "report"))
async def cmd_stats(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    text = await build_report(db, user, "month", today)
    await message.answer(text, reply_markup=report_periods("month"))


@router.message(Command("insight", "разбор"))
async def cmd_insight(
    message: Message, db: Database, user: UserSettings, today: dt.date, ai: AiClient
) -> None:
    if not ai.available():
        await message.answer(NO_AI)
        return

    total, count = await db.total_between(
        user.user_id, month_start(today), month_end(today)
    )
    if count < 3:
        await message.answer(
            "Пока мало данных для разбора — запиши хотя бы несколько трат."
        )
        return

    note = await message.answer("🧠 Смотрю на цифры…")
    summary = await insight_summary(db, user, today)
    text = await ai.insight(summary)
    if text is None:
        await note.edit_text("Сервис ИИ не ответил. Попробуй позже — /stats работает всегда.")
        return

    await note.edit_text(f"🧠 <b>Разбор месяца</b>\n\n{esc(text)}")


@router.callback_query(F.data.startswith("rep:"))
async def cb_report(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    period = callback.data.split(":", 1)[1]
    if period not in VALID_PERIODS:
        await callback.answer()
        return

    text = await build_report(db, user, period, today)
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=report_periods(period))
    except TelegramBadRequest:
        pass  # текст не изменился — Telegram ругается, для нас это не ошибка
