"""Отчёты: /stats с переключением периода."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ..db import Database, UserSettings
from ..keyboards import PERIODS, report_periods
from ..services import build_report

router = Router(name="reports")

VALID_PERIODS = {key for key, _ in PERIODS}


@router.message(Command("stats", "report"))
async def cmd_stats(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    text = await build_report(db, user, "month", today)
    await message.answer(text, reply_markup=report_periods("month"))


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
