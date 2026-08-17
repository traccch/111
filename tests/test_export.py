"""Тесты выгрузок: CSV, строки таблицы, подмена валюты и сборка PDF."""

from __future__ import annotations

import datetime as dt
import re
import unittest
from unittest import mock

from bot import charts, export
from bot.db import Expense

TODAY = dt.date(2026, 8, 16)


def expense(
    id: int, amount: int, note: str, category: str = "Кафе", day: int = 16
) -> Expense:
    return Expense(
        id=id,
        amount=amount,
        note=note,
        spent_on=dt.date(2026, 8, day),
        category_id=1,
        category_name=category,
        category_emoji="☕",
    )


EXPENSES = [
    expense(1, 30000, "кофе"),
    expense(2, 245050, "продукты на неделю", "Продукты", 14),
    expense(3, 4500000, "аренда за август; счёт №5", "Жильё", 1),
]


class CsvTest(unittest.TestCase):
    def test_has_bom_and_rows(self):
        payload = export.csv_bytes(EXPENSES, "₽")
        self.assertTrue(payload.startswith(b"\xef\xbb\xbf"))  # Excel и кириллица

        text = payload.decode("utf-8-sig")
        lines = text.strip().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("id;date;amount"))
        self.assertIn("300,00", lines[1])
        self.assertIn("кофе", lines[1])

    def test_semicolon_in_note_is_quoted(self):
        text = export.csv_bytes(EXPENSES, "₽").decode("utf-8-sig")
        self.assertIn('"аренда за август; счёт №5"', text)


class TableTest(unittest.TestCase):
    def test_columns_are_aligned(self):
        rows = export.table_rows(EXPENSES, "р.")
        self.assertEqual(len(rows), 5)  # шапка, разделитель и три траты

        body = rows[2:]
        starts = {row.index("р.") for row in body}
        self.assertEqual(len(starts), 1, "суммы должны стоять в одной колонке")
        self.assertTrue(all(row.startswith("01.08.2026") or row[2] == "." for row in body))

    def test_long_note_is_cut(self):
        rows = export.table_rows([expense(1, 100, "к" * 80)], "р.")
        self.assertLess(len(rows[-1]), 110)
        self.assertTrue(rows[-1].endswith("…"))


class MonoCurrencyTest(unittest.TestCase):
    @unittest.skipUnless(charts.available(), "нужен matplotlib со шрифтами")
    def test_ruble_is_replaced(self):
        # в DejaVu Sans Mono нет знака ₽ — иначе в PDF был бы пустой квадрат
        self.assertEqual(export.mono_currency("₽"), "р.")
        self.assertEqual(export.mono_currency("$"), "$")
        self.assertEqual(export.mono_currency("€"), "€")

    def test_without_matplotlib_keeps_currency(self):
        export._mono_charset.cache_clear()
        with mock.patch.object(export, "_mono_charset", return_value=None):
            self.assertEqual(export.mono_currency("₽"), "₽")


class SummaryTest(unittest.TestCase):
    def test_lines(self):
        lines = export.summary_lines(
            dt.date(2026, 8, 1), TODAY, 4775050, 3,
            [("Жильё", 4500000, 1), ("Продукты", 245050, 1), ("Кафе", 30000, 1)],
            "р.", ["", "На 12% больше, чем в июле."],
        )
        text = "\n".join(lines)
        self.assertIn("01.08.2026 — 16.08.2026 (16 дн.)", text)
        self.assertIn("47\u00a0750,50\u00a0р.", text)
        self.assertIn("94%", text)  # доля Жилья
        self.assertIn("На 12% больше", text)


@unittest.skipUnless(charts.available(), "matplotlib не установлен")
class PdfTest(unittest.TestCase):
    def build(self, expenses):
        days = [dt.date(2026, 8, day) for day in range(1, 17)]
        amounts = [0] * 16
        for item in expenses:
            amounts[item.spent_on.day - 1] += item.amount
        return charts.expense_pdf(
            title="Отчёт по расходам",
            subtitle="август 2026",
            summary_lines=export.summary_lines(
                dt.date(2026, 8, 1), TODAY,
                sum(item.amount for item in expenses), len(expenses),
                [("Кафе", 30000, 1)], "р.",
            ),
            categories=[("Кафе", 30000), ("Жильё", 4500000)],
            days=days,
            amounts=amounts,
            table=export.table_rows(expenses, "р."),
            currency="р.",
        )

    def test_pdf_is_produced(self):
        payload = self.build(EXPENSES)
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertGreater(len(payload), 5000)

    def test_many_rows_add_pages(self):
        # /Count в объекте Pages — число страниц документа
        def pages(payload: bytes) -> int:
            return int(re.search(rb"/Count (\d+)", payload).group(1))

        small = self.build(EXPENSES)
        many = self.build(
            [
                expense(index, 10000 + index, f"трата {index}", day=index % 16 + 1)
                for index in range(120)
            ]
        )
        self.assertGreater(pages(many), pages(small))


if __name__ == "__main__":
    unittest.main()
