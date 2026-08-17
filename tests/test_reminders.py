"""Тесты планировщика напоминаний: время, часовые пояса, защита от повторов."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from bot.db import Database
from bot.handlers.reminders import parse_time
from bot.reminders import ReminderScheduler, is_due, local_midnight_utc, local_now

USER_ID = 777


class FakeBot:
    """Вместо отправки в Telegram складывает сообщения в список."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.sent.append((chat_id, text))


class ParseTimeTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(parse_time("21:00"), dt.time(21, 0))
        self.assertEqual(parse_time("21.30"), dt.time(21, 30))
        self.assertEqual(parse_time("9"), dt.time(9, 0))
        self.assertEqual(parse_time(" 08:05 "), dt.time(8, 5))

    def test_garbage(self):
        for raw in ("вечером", "25:00", "10:75", "", "8:5"):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_time(raw))


class IsDueTest(unittest.TestCase):
    def test_fires_at_the_appointed_minute(self):
        now = dt.datetime(2026, 8, 16, 21, 0)
        self.assertTrue(is_due(now, dt.time(21, 0), None))

    def test_does_not_fire_early(self):
        now = dt.datetime(2026, 8, 16, 20, 59)
        self.assertFalse(is_due(now, dt.time(21, 0), None))

    def test_catches_up_within_grace_period(self):
        # бот был выключен и включился через 10 минут после срока
        self.assertTrue(is_due(dt.datetime(2026, 8, 16, 21, 10), dt.time(21, 0), None))
        # а через час напоминать уже поздно
        self.assertFalse(is_due(dt.datetime(2026, 8, 16, 22, 0), dt.time(21, 0), None))

    def test_does_not_repeat_the_same_day(self):
        now = dt.datetime(2026, 8, 16, 21, 5)
        self.assertFalse(is_due(now, dt.time(21, 0), dt.date(2026, 8, 16)))
        self.assertTrue(is_due(now, dt.time(21, 0), dt.date(2026, 8, 15)))


class TimezoneTest(unittest.TestCase):
    def test_local_now(self):
        utc = dt.datetime(2026, 8, 16, 18, 0, tzinfo=dt.timezone.utc)
        self.assertEqual(local_now("Europe/Moscow", utc), dt.datetime(2026, 8, 16, 21, 0))
        self.assertEqual(local_now("Asia/Almaty", utc), dt.datetime(2026, 8, 16, 23, 0))
        self.assertEqual(local_now("Мордор", utc), dt.datetime(2026, 8, 16, 18, 0))

    def test_local_midnight_in_utc(self):
        local = dt.datetime(2026, 8, 16, 21, 0)
        self.assertEqual(
            local_midnight_utc("Europe/Moscow", local), dt.datetime(2026, 8, 15, 21, 0)
        )


class SchedulerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow", "₽")
        await self.db.connect()
        self.user = await self.db.ensure_user(USER_ID)
        self.bot = FakeBot()
        self.scheduler = ReminderScheduler(self.bot, self.db)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    @staticmethod
    def utc(*args: int) -> dt.datetime:
        return dt.datetime(*args, tzinfo=dt.timezone.utc)

    async def test_fires_once_per_day(self):
        await self.db.add_reminder(USER_ID, dt.time(21, 0))

        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 16, 17, 59)), 0)
        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 16, 18, 0)), 1)
        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 16, 18, 1)), 0)
        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 17, 18, 0)), 1)
        self.assertEqual(len(self.bot.sent), 2)
        self.assertIn("записать траты", self.bot.sent[0][1])

    async def test_respects_user_timezone(self):
        await self.db.set_tz(USER_ID, "Asia/Almaty")  # UTC+5
        await self.db.add_reminder(USER_ID, dt.time(21, 0))

        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 16, 18, 0)), 0)
        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 16, 16, 0)), 1)

    async def test_skipped_when_expense_already_logged_today(self):
        await self.db.add_reminder(USER_ID, dt.time(21, 0))
        await self.db.add_expense(USER_ID, 30000, "кофе", dt.date(2026, 8, 16), None)

        # created_at ставит SQLite «сейчас», поэтому проверяем на сегодняшнем дне
        today = dt.datetime.now(dt.timezone.utc)
        moment = today.replace(hour=18, minute=0, second=0, microsecond=0)
        self.assertEqual(await self.scheduler.tick(moment), 0)
        self.assertEqual(self.bot.sent, [])

    async def test_not_skipped_when_setting_is_off(self):
        await self.db.add_reminder(USER_ID, dt.time(21, 0))
        await self.db.add_expense(USER_ID, 30000, "кофе", dt.date(2026, 8, 16), None)
        await self.db.set_skip_if_logged(USER_ID, False)

        moment = dt.datetime.now(dt.timezone.utc).replace(hour=18, minute=0, second=0)
        self.assertEqual(await self.scheduler.tick(moment), 1)

    async def test_reminder_marked_fired_even_when_skipped(self):
        """Пропуск не должен превращаться в отложенный залп вечером."""
        await self.db.add_reminder(USER_ID, dt.time(21, 0))
        await self.db.add_expense(USER_ID, 30000, "кофе", dt.date(2026, 8, 16), None)

        moment = dt.datetime.now(dt.timezone.utc).replace(hour=18, minute=0, second=0)
        await self.scheduler.tick(moment)
        reminders = await self.db.list_reminders(USER_ID)
        self.assertIsNotNone(reminders[0].last_fired_on)

    async def test_snooze(self):
        base = self.utc(2026, 8, 16, 18, 0)
        await self.db.add_snooze(USER_ID, (base + dt.timedelta(minutes=15)).replace(tzinfo=None))

        self.assertEqual(await self.scheduler.tick(base + dt.timedelta(minutes=10)), 0)
        self.assertEqual(await self.scheduler.tick(base + dt.timedelta(minutes=15)), 1)
        # повторно не выстрелит — запись удалена
        self.assertEqual(await self.scheduler.tick(base + dt.timedelta(minutes=20)), 0)

    async def test_month_summary_on_the_first(self):
        await self.db.set_tz(USER_ID, "UTC")
        await self.db.add_reminder(USER_ID, dt.time(9, 0))
        cafe = await self.db.find_category_by_name(USER_ID, "Кафе")
        await self.db.add_expense(USER_ID, 120000, "кофе", dt.date(2026, 7, 15), cafe.id)
        await self.db.set_skip_if_logged(USER_ID, False)

        await self.scheduler.tick(self.utc(2026, 8, 1, 9, 0))
        self.assertIn("Итоги за июль 2026", self.bot.sent[0][1])
        self.assertIn("1 200", self.bot.sent[0][1])  # неразрывный пробел в сумме

        # второго числа итогов уже нет
        self.bot.sent.clear()
        await self.scheduler.tick(self.utc(2026, 8, 2, 9, 0))
        self.assertNotIn("Итоги", self.bot.sent[0][1])

    async def test_two_reminders_a_day(self):
        await self.db.add_reminder(USER_ID, dt.time(9, 0))
        await self.db.add_reminder(USER_ID, dt.time(21, 0))
        await self.db.set_skip_if_logged(USER_ID, False)

        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 16, 6, 0)), 1)
        self.assertEqual(await self.scheduler.tick(self.utc(2026, 8, 16, 18, 0)), 1)

    async def test_duplicate_time_rejected(self):
        self.assertIsNotNone(await self.db.add_reminder(USER_ID, dt.time(9, 0)))
        self.assertIsNone(await self.db.add_reminder(USER_ID, dt.time(9, 0)))
        self.assertEqual(len(await self.db.list_reminders(USER_ID)), 1)


if __name__ == "__main__":
    unittest.main()
