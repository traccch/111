"""Напоминания: /remind, /reminders и кнопки под ними."""

from __future__ import annotations

import datetime as dt
import re

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..db import Database, UserSettings
from ..keyboards import reminders_settings
from ..reminders import SNOOZE_MINUTES

router = Router(name="reminders")

USAGE = (
    "Формат:\n"
    "<code>/remind 21:00</code> — напоминать каждый день в 21:00\n"
    "<code>/remind 21:00 off</code> — убрать это напоминание\n"
    "<code>/remind off</code> — выключить все\n"
    "Сколько нужно напоминаний, столько и добавляй."
)

_TIME = re.compile(r"^(\d{1,2})(?:[:.\s-](\d{2}))?$")


def parse_time(raw: str) -> dt.time | None:
    """«21:00», «21.00», «9», «09 30» → time. Иначе None."""
    match = _TIME.match(raw.strip())
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if hour > 23 or minute > 59:
        return None
    return dt.time(hour=hour, minute=minute)


async def settings_text(db: Database, user: UserSettings) -> str:
    reminders = await db.list_reminders(user.user_id)
    if not reminders:
        return "⏰ <b>Напоминаний нет</b>\n\n" + USAGE

    times = ", ".join(reminder.title for reminder in reminders)
    skip = (
        "не приходят, если траты за день уже записаны"
        if user.skip_if_logged
        else "приходят в любом случае"
    )
    return (
        f"⏰ <b>Напоминания</b>: {times}\n"
        f"Часовой пояс: {user.tz} · сменить — /tz\n\n"
        f"Сейчас напоминания {skip}.\n"
        "Кнопкой ниже можно переключить, крестиком — удалить время."
    )


@router.message(Command("remind"))
async def cmd_remind(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    args = (command.args or "").strip().lower()
    if not args:
        await message.answer(
            await settings_text(db, user),
            reply_markup=reminders_settings(
                await db.list_reminders(user.user_id), user.skip_if_logged
            ),
        )
        return

    if args in {"off", "выкл", "стоп"}:
        removed = await db.delete_all_reminders(user.user_id)
        await message.answer(
            f"Выключил напоминания ({removed} шт.)." if removed else "Напоминаний и не было."
        )
        return

    parts = args.split()
    disable = len(parts) > 1 and parts[-1] in {"off", "выкл", "убрать", "удалить"}
    at = parse_time(parts[0])
    if at is None:
        await message.answer("Не понял время.\n\n" + USAGE)
        return

    if disable:
        deleted = await db.delete_reminder(user.user_id, at)
        await message.answer(
            f"Убрал напоминание на {at.strftime('%H:%M')}."
            if deleted
            else f"Напоминания на {at.strftime('%H:%M')} не было."
        )
        return

    created = await db.add_reminder(user.user_id, at)
    if created is None:
        await message.answer(f"Напоминание на {at.strftime('%H:%M')} уже есть.")
        return

    await message.answer(
        f"⏰ Буду напоминать каждый день в <b>{at.strftime('%H:%M')}</b> "
        f"(часовой пояс {user.tz}, сменить — /tz).\n"
        "Если траты за день уже записаны, напоминание не придёт — "
        "это можно поменять в /reminders."
    )


@router.message(Command("reminders"))
async def cmd_reminders(message: Message, db: Database, user: UserSettings) -> None:
    await message.answer(
        await settings_text(db, user),
        reply_markup=reminders_settings(
            await db.list_reminders(user.user_id), user.skip_if_logged
        ),
    )


@router.callback_query(F.data == "snooze")
async def cb_snooze(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    fire_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(
        minutes=SNOOZE_MINUTES
    )
    await db.add_snooze(user.user_id, fire_at)
    await callback.answer(f"Напомню через {SNOOZE_MINUTES} минут")
    if isinstance(callback.message, Message):
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramBadRequest:
            pass


@router.callback_query(F.data.startswith("delrem:"))
async def cb_delete_reminder(
    callback: CallbackQuery, db: Database, user: UserSettings
) -> None:
    at = parse_time(callback.data.split(":", 1)[1])
    if at is None:
        await callback.answer()
        return

    await db.delete_reminder(user.user_id, at)
    await callback.answer(f"Убрал {at.strftime('%H:%M')}")
    await _refresh(callback, db, user)


@router.callback_query(F.data == "togskip")
async def cb_toggle_skip(
    callback: CallbackQuery, db: Database, user: UserSettings
) -> None:
    await db.set_skip_if_logged(user.user_id, not user.skip_if_logged)
    fresh = await db.ensure_user(user.user_id)
    await callback.answer(
        "Молчу, если траты записаны" if fresh.skip_if_logged else "Напоминаю всегда"
    )
    await _refresh(callback, db, fresh)


async def _refresh(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(
            await settings_text(db, user),
            reply_markup=reminders_settings(
                await db.list_reminders(user.user_id), user.skip_if_logged
            ),
        )
    except TelegramBadRequest:
        pass  # текст не изменился — для нас это не ошибка
