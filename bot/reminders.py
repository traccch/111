"""Планировщик напоминаний: раз в полминуты смотрит, кому пора записать траты.

Ничего не держит в памяти — состояние живёт в БД, поэтому перезапуск бота
не приводит ни к потерянным, ни к задвоенным напоминаниям.

Устройство планировщика перенесено из дневника давления (репозиторий 222).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .db import Database
from .formatting import format_money, month_title
from .keyboards import reminder_actions

logger = logging.getLogger(__name__)

#: Насколько поздно ещё уместно напомнить (бот мог быть выключен).
GRACE_MINUTES = 20
#: Пауза кнопки «отложить».
SNOOZE_MINUTES = 15

TICK_SECONDS = 30

REMINDER_TEXT = (
    "⏰ <b>Не забудь записать траты за сегодня</b>\n\n"
    "Просто пришли их сообщением, например <code>кофе 300</code> или "
    "<code>продукты 1 250</code>."
)

SNOOZE_TEXT = (
    "⏰ <b>Напоминаю ещё раз</b>\n\nПришли траты, например <code>кофе 300</code>."
)


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_now(tz: str, now_utc: dt.datetime) -> dt.datetime:
    """Местное время пользователя без таймзоны."""
    return now_utc.astimezone(_zone(tz)).replace(tzinfo=None)


def local_midnight_utc(tz: str, local: dt.datetime) -> dt.datetime:
    """Начало местных суток, переведённое в UTC без таймзоны."""
    midnight = dt.datetime.combine(local.date(), dt.time.min, tzinfo=_zone(tz))
    return midnight.astimezone(dt.timezone.utc).replace(tzinfo=None)


def is_due(
    now: dt.datetime,
    at: dt.time,
    last_fired_on: Optional[dt.date],
    grace_minutes: int = GRACE_MINUTES,
) -> bool:
    """Пора ли отправлять напоминание, назначенное на `at` по местному времени."""
    if last_fired_on == now.date():
        return False
    scheduled = dt.datetime.combine(now.date(), at)
    late = (now - scheduled).total_seconds() / 60
    return 0 <= late <= grace_minutes


class ReminderScheduler:
    def __init__(self, bot: Bot, db: Database, tick_seconds: int = TICK_SECONDS) -> None:
        self._bot = bot
        self._db = db
        self._tick_seconds = tick_seconds
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="reminders")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — планировщик не должен падать целиком
                logger.exception("Сбой при рассылке напоминаний")
            await asyncio.sleep(self._tick_seconds)

    async def tick(self, now_utc: Optional[dt.datetime] = None) -> int:
        """Один проход планировщика. Возвращает число отправленных сообщений."""
        now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
        if now_utc.tzinfo is None:
            now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
        sent = 0

        for user_id in await self._db.pop_due_snoozes(now_utc.replace(tzinfo=None)):
            sent += await self._send(user_id, SNOOZE_TEXT)

        for candidate in await self._db.due_candidates():
            now = local_now(candidate.tz, now_utc)
            if not is_due(now, candidate.at, candidate.last_fired_on):
                continue

            await self._db.mark_reminder_fired(candidate.reminder_id, now.date())
            if candidate.skip_if_logged:
                since = local_midnight_utc(candidate.tz, now)
                if await self._db.has_expense_since(candidate.user_id, since):
                    logger.debug("Напоминание %s пропущено: траты уже записаны", candidate.at)
                    continue

            text = REMINDER_TEXT
            summary = await self._month_summary(candidate.user_id, now.date())
            if summary:
                text = f"{summary}\n\n{text}"
            sent += await self._send(candidate.user_id, text)

        return sent

    async def _month_summary(self, user_id: int, today: dt.date) -> Optional[str]:
        """Первого числа добавляем к напоминанию итоги прошлого месяца."""
        if today.day != 1:
            return None

        end = today - dt.timedelta(days=1)
        start = end.replace(day=1)
        total, count = await self._db.total_between(user_id, start, end)
        if not count:
            return None

        user = await self._db.ensure_user(user_id)
        lines = [
            f"📊 <b>Итоги за {month_title(start)}</b>",
            f"Всего: <b>{format_money(total, user.currency)}</b>",
        ]
        top = await self._db.totals_by_category(user_id, start, end)
        for item in top[:3]:
            lines.append(f"{item.title} — {format_money(item.total, user.currency)}")
        return "\n".join(lines)

    async def _send(self, user_id: int, text: str) -> int:
        try:
            await self._bot.send_message(user_id, text, reply_markup=reminder_actions())
        except TelegramAPIError as exc:
            logger.warning("Не отправил напоминание %s: %s", user_id, exc)
            return 0
        return 1
