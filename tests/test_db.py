"""Тесты хранилища и бизнес-логики на временной базе."""

from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

import aiosqlite

from bot.db import TOTAL_LIMIT_CATEGORY, Database
from bot.services import build_report, check_limits, month_end, month_start, period_range

TODAY = dt.date(2026, 8, 16)
USER_ID = 777


class DatabaseTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow", "₽")
        await self.db.connect()
        self.user = await self.db.ensure_user(USER_ID)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def category(self, name: str):
        found = await self.db.find_category_by_name(USER_ID, name)
        self.assertIsNotNone(found, f"нет категории {name}")
        return found

    async def test_default_categories_seeded_once(self):
        categories = await self.db.list_categories(USER_ID)
        self.assertGreater(len(categories), 5)
        await self.db.ensure_user(USER_ID)
        self.assertEqual(len(await self.db.list_categories(USER_ID)), len(categories))

    async def test_fallback_category_is_last_resort(self):
        fallback = await self.db.get_fallback_category(USER_ID)
        self.assertEqual(fallback.name, "Прочее")
        self.assertFalse(await self.db.delete_category(USER_ID, fallback.id))

    async def test_add_and_read_expense(self):
        cafe = await self.category("Кафе")
        expense = await self.db.add_expense(USER_ID, 30000, "кофе", TODAY, cafe.id)
        self.assertEqual(expense.amount, 30000)
        self.assertEqual(expense.category_name, "Кафе")

        stored = await self.db.get_expense(USER_ID, expense.id)
        self.assertEqual(stored, expense)
        self.assertIsNone(await self.db.get_expense(USER_ID + 1, expense.id))

    async def test_totals_by_category(self):
        cafe = await self.category("Кафе")
        transport = await self.category("Транспорт")
        await self.db.add_expense(USER_ID, 30000, "кофе", TODAY, cafe.id)
        await self.db.add_expense(USER_ID, 20000, "обед", TODAY, cafe.id)
        await self.db.add_expense(USER_ID, 45000, "такси", TODAY, transport.id)

        totals = await self.db.totals_by_category(USER_ID, TODAY, TODAY)
        self.assertEqual([(item.name, item.total) for item in totals],
                         [("Транспорт", 45000), ("Кафе", 50000)][::-1])
        total, count = await self.db.total_between(USER_ID, TODAY, TODAY)
        self.assertEqual((total, count), (95000, 3))

    async def test_date_range_filters(self):
        cafe = await self.category("Кафе")
        await self.db.add_expense(USER_ID, 10000, "вчера", TODAY - dt.timedelta(days=1), cafe.id)
        await self.db.add_expense(USER_ID, 20000, "сегодня", TODAY, cafe.id)
        today_total, _ = await self.db.total_between(USER_ID, TODAY, TODAY)
        self.assertEqual(today_total, 20000)
        self.assertEqual(await self.db.first_expense_date(USER_ID), TODAY - dt.timedelta(days=1))

    async def test_delete_category_moves_expenses_to_fallback(self):
        cafe = await self.category("Кафе")
        expense = await self.db.add_expense(USER_ID, 30000, "кофе", TODAY, cafe.id)
        self.assertTrue(await self.db.delete_category(USER_ID, cafe.id))

        moved = await self.db.get_expense(USER_ID, expense.id)
        self.assertEqual(moved.category_name, "Прочее")
        self.assertIsNone(await self.db.find_category_by_name(USER_ID, "Кафе"))

    async def test_duplicate_category_rejected(self):
        self.assertIsNone(await self.db.add_category(USER_ID, "Кафе", "☕"))
        created = await self.db.add_category(USER_ID, "Пицца", "🍕", ["додо", "пицца"])
        self.assertEqual(created.keywords, ("додо", "пицца"))

    async def test_keyword_moves_between_categories(self):
        cafe = await self.category("Кафе")
        products = await self.category("Продукты")
        await self.db.add_keyword(USER_ID, cafe.id, "Шаурма")

        self.assertIn("шаурма", (await self.category("Кафе")).keywords)
        await self.db.add_keyword(USER_ID, products.id, "шаурма")
        self.assertNotIn("шаурма", (await self.category("Кафе")).keywords)
        self.assertIn("шаурма", (await self.category("Продукты")).keywords)

    async def test_limits_upsert_and_delete(self):
        await self.db.set_limit(USER_ID, TOTAL_LIMIT_CATEGORY, 3_000_000)
        await self.db.set_limit(USER_ID, TOTAL_LIMIT_CATEGORY, 4_000_000)
        self.assertEqual(await self.db.get_limit(USER_ID, TOTAL_LIMIT_CATEGORY), 4_000_000)
        self.assertEqual(len(await self.db.list_limits(USER_ID)), 1)

        self.assertTrue(await self.db.delete_limit(USER_ID, TOTAL_LIMIT_CATEGORY))
        self.assertFalse(await self.db.delete_limit(USER_ID, TOTAL_LIMIT_CATEGORY))

    async def test_limit_warnings(self):
        cafe = await self.category("Кафе")
        await self.db.set_limit(USER_ID, cafe.id, 100000)
        await self.db.add_expense(USER_ID, 85000, "кофе", TODAY, cafe.id)

        warnings = await check_limits(self.db, self.user, cafe.id, TODAY)
        self.assertEqual(len(warnings), 1)
        self.assertIn("🟡", warnings[0])

        await self.db.add_expense(USER_ID, 50000, "обед", TODAY, cafe.id)
        warnings = await check_limits(self.db, self.user, cafe.id, TODAY)
        self.assertIn("🔴", warnings[0])

    async def test_report_renders(self):
        cafe = await self.category("Кафе")
        empty = await build_report(self.db, self.user, "month", TODAY)
        self.assertIn("Пока пусто", empty)

        await self.db.add_expense(USER_ID, 30000, "кофе", TODAY, cafe.id)
        report = await build_report(self.db, self.user, "month", TODAY)
        self.assertIn("300", report)
        self.assertIn("Кафе", report)

        for period in ("day", "week", "month", "all"):
            self.assertTrue(await build_report(self.db, self.user, period, TODAY))


class MigrationTest(unittest.IsolatedAsyncioTestCase):
    """База, созданная до появления напоминаний, должна открываться и дополняться."""

    async def test_old_database_gets_new_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "old.db")

            legacy = await aiosqlite.connect(path)
            await legacy.executescript(
                """
                CREATE TABLE users (
                    user_id    INTEGER PRIMARY KEY,
                    currency   TEXT NOT NULL DEFAULT '₽',
                    tz         TEXT NOT NULL DEFAULT 'Europe/Moscow',
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO users (user_id, currency, tz) VALUES (777, '$', 'Asia/Almaty');
                """
            )
            await legacy.commit()
            await legacy.close()

            db = Database(path, "Europe/Moscow", "₽")
            await db.connect()
            try:
                user = await db.ensure_user(777)
                self.assertEqual((user.currency, user.tz), ("$", "Asia/Almaty"))
                self.assertTrue(user.skip_if_logged)
                self.assertIsNotNone(await db.add_reminder(777, dt.time(21, 0)))
            finally:
                await db.close()


class PeriodTest(unittest.TestCase):
    def test_month_bounds(self):
        self.assertEqual(month_start(TODAY), dt.date(2026, 8, 1))
        self.assertEqual(month_end(TODAY), dt.date(2026, 8, 31))
        self.assertEqual(month_end(dt.date(2026, 2, 10)), dt.date(2026, 2, 28))
        self.assertEqual(month_end(dt.date(2026, 12, 10)), dt.date(2026, 12, 31))

    def test_period_range(self):
        self.assertEqual(period_range("day", TODAY)[:2], (TODAY, TODAY))
        # 16 августа 2026 — воскресенье, неделя начинается 10-го
        self.assertEqual(period_range("week", TODAY)[0], dt.date(2026, 8, 10))
        self.assertEqual(period_range("month", TODAY)[0], dt.date(2026, 8, 1))


if __name__ == "__main__":
    unittest.main()
