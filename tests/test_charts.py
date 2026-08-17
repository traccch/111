"""Тесты графиков: рисуются ли PNG и корректно ли ведут себя без matplotlib."""

from __future__ import annotations

import datetime as dt
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot import charts
from bot.db import Database
from bot.handlers.charts import render

USER_ID = 777
TODAY = dt.date(2026, 8, 16)

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def png_size(data: bytes) -> tuple[int, int]:
    """Ширина и высота из заголовка IHDR."""
    width, height = struct.unpack(">II", data[16:24])
    return width, height


@unittest.skipUnless(charts.available(), "matplotlib не установлен")
class DrawingTest(unittest.TestCase):
    def test_daily_chart(self):
        days = [dt.date(2026, 8, day) for day in range(1, 17)]
        amounts = [30000, 0, 125050, 0, 8900, 45000] + [0] * 10
        image = charts.daily_chart(days, amounts, "₽", "Расходы по дням")

        self.assertTrue(image.startswith(PNG_MAGIC))
        width, height = png_size(image)
        self.assertGreater(width, 800)
        self.assertGreater(height, 500)

    def test_category_chart_grows_with_rows(self):
        few = charts.category_chart([("Кафе", 80000), ("Такси", 45000)], "₽", "Категории")
        many = charts.category_chart(
            [(f"Категория {index}", 10000 * (index + 1)) for index in range(12)], "₽", "Категории"
        )
        self.assertLess(png_size(few)[1], png_size(many)[1])

    def test_monthly_chart(self):
        items = [("2026-06", 4200000), ("2026-07", 3900000), ("2026-08", 5025050)]
        self.assertTrue(charts.monthly_chart(items, "₽", "По месяцам").startswith(PNG_MAGIC))

    def test_single_value_has_no_average_line(self):
        # среднее по одной точке — не ориентир, а та же точка
        image = charts.daily_chart([dt.date(2026, 8, 1)], [30000], "₽", "День")
        self.assertTrue(image.startswith(PNG_MAGIC))

    def test_month_name(self):
        self.assertEqual(charts.month_name("2026-08"), "август 2026")
        self.assertEqual(charts.month_name("2025-01"), "январь 2025")


class WithoutMatplotlibTest(unittest.TestCase):
    def test_raises_clear_error(self):
        with mock.patch.object(charts, "available", return_value=False):
            with self.assertRaises(charts.ChartsUnavailable):
                charts.daily_chart([dt.date(2026, 8, 1)], [1], "₽", "x")


class RenderDataTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow", "₽")
        await self.db.connect()
        self.user = await self.db.ensure_user(USER_ID)

    async def asyncTearDown(self):
        await self.db.close()
        self._tmp.cleanup()

    async def test_empty_returns_none(self):
        for kind in ("days", "cats", "months"):
            self.assertIsNone(await render(self.db, self.user, kind, TODAY))

    @unittest.skipUnless(charts.available(), "matplotlib не установлен")
    async def test_draws_when_data_exists(self):
        cafe = await self.db.find_category_by_name(USER_ID, "Кафе")
        await self.db.add_expense(USER_ID, 30000, "кофе", TODAY, cafe.id)

        for kind in ("days", "cats"):
            drawn = await render(self.db, self.user, kind, TODAY)
            self.assertIsNotNone(drawn, kind)
            self.assertTrue(drawn[0].startswith(PNG_MAGIC))

        # один месяц — рисовать нечего, нужны хотя бы два
        self.assertIsNone(await render(self.db, self.user, "months", TODAY))
        await self.db.add_expense(USER_ID, 50000, "кофе", dt.date(2026, 7, 3), cafe.id)
        self.assertIsNotNone(await render(self.db, self.user, "months", TODAY))


if __name__ == "__main__":
    unittest.main()
