"""Тесты разбора свободного текста. Запуск: python -m unittest (или pytest)."""

from __future__ import annotations

import datetime as dt
import unittest

from bot.db import Category
from bot.parsing import (
    ParseError,
    first_keyword,
    match_category,
    parse_amount,
    parse_expense,
)

TODAY = dt.date(2026, 8, 16)


class ParseExpenseTest(unittest.TestCase):
    def parse(self, text: str):
        parsed = parse_expense(text, TODAY)
        self.assertIsNotNone(parsed, f"не разобрал: {text!r}")
        return parsed

    def test_note_then_amount(self):
        parsed = self.parse("кофе 300")
        self.assertEqual(parsed.amount, 30000)
        self.assertEqual(parsed.note, "кофе")
        self.assertEqual(parsed.spent_on, TODAY)

    def test_amount_then_note(self):
        parsed = self.parse("450 такси до дома")
        self.assertEqual(parsed.amount, 45000)
        self.assertEqual(parsed.note, "такси до дома")

    def test_decimal_and_thousands_separator(self):
        self.assertEqual(self.parse("продукты 1 250,50").amount, 125050)
        self.assertEqual(self.parse("продукты 1250.5").amount, 125050)

    def test_currency_suffix_is_ignored(self):
        for text in ("кофе 300р", "кофе 300 руб.", "кофе 300₽", "кофе 300 rub"):
            with self.subTest(text=text):
                parsed = self.parse(text)
                self.assertEqual(parsed.amount, 30000)
                self.assertEqual(parsed.note, "кофе")

    def test_multipliers(self):
        self.assertEqual(self.parse("аренда 25к").amount, 2_500_000)
        self.assertEqual(self.parse("аренда 1,5к").amount, 150_000)
        self.assertEqual(self.parse("машина 1.2млн").amount, 120_000_000)

    def test_relative_dates(self):
        self.assertEqual(self.parse("такси 450 вчера").spent_on, dt.date(2026, 8, 15))
        self.assertEqual(self.parse("позавчера кино 800").spent_on, dt.date(2026, 8, 14))
        self.assertEqual(self.parse("кофе 300 сегодня").spent_on, TODAY)
        self.assertEqual(self.parse("такси 450 вчера").note, "такси")

    def test_explicit_date(self):
        parsed = self.parse("подарок 3000 05.08")
        self.assertEqual(parsed.spent_on, dt.date(2026, 8, 5))
        self.assertEqual(parsed.amount, 300000)
        self.assertEqual(parsed.note, "подарок")

    def test_explicit_date_with_year(self):
        self.assertEqual(self.parse("аренда 30000 01.07.2025").spent_on, dt.date(2025, 7, 1))

    def test_future_date_without_year_falls_back_to_last_year(self):
        self.assertEqual(self.parse("страховка 5000 20.12").spent_on, dt.date(2025, 12, 20))

    def test_lonely_decimal_is_amount_not_date(self):
        parsed = self.parse("кофе 1.05")
        self.assertEqual(parsed.amount, 105)
        self.assertEqual(parsed.spent_on, TODAY)

    def test_last_number_wins(self):
        self.assertEqual(self.parse("2 кофе 300").amount, 30000)

    def test_text_without_amount(self):
        self.assertIsNone(parse_expense("привет как дела", TODAY))
        self.assertIsNone(parse_expense("", TODAY))

    def test_zero_and_huge_amounts_rejected(self):
        with self.assertRaises(ParseError):
            parse_expense("кофе 0", TODAY)
        with self.assertRaises(ParseError):
            parse_expense("кофе 999999999999", TODAY)

    def test_note_may_be_empty(self):
        parsed = self.parse("500")
        self.assertEqual(parsed.amount, 50000)
        self.assertEqual(parsed.note, "")


class ParseAmountTest(unittest.TestCase):
    def test_returns_amount_and_rest(self):
        self.assertEqual(parse_amount("Кафе 8000"), (800000, "Кафе"))
        self.assertEqual(parse_amount("30000"), (3000000, ""))
        self.assertEqual(parse_amount("Кафе 0"), (0, "Кафе"))

    def test_no_number(self):
        self.assertIsNone(parse_amount("Кафе"))


class MatchCategoryTest(unittest.TestCase):
    def setUp(self):
        self.categories = [
            Category(1, "Кафе", "☕", ("кофе", "кафе", "обед")),
            Category(2, "Транспорт", "🚕", ("такси", "метро")),
            Category(3, "Продукты", "🍔", ("продукты", "магазин")),
            Category(4, "Доставка еды", "🍜", ("доставка еды",)),
            Category(5, "Прочее", "📦", (), is_fallback=True),
        ]

    def test_matches_keyword(self):
        self.assertEqual(match_category("кофе с собой", self.categories).name, "Кафе")
        self.assertEqual(match_category("такси домой", self.categories).name, "Транспорт")

    def test_matches_category_name(self):
        self.assertEqual(match_category("продукты", self.categories).name, "Продукты")

    def test_multiword_keyword_wins(self):
        self.assertEqual(
            match_category("доставка еды вечером", self.categories).name, "Доставка еды"
        )

    def test_no_partial_word_match(self):
        # «кофейня» не должно матчиться по односложному ключу «кофе»
        self.assertIsNone(match_category("кофейня рядом", self.categories))

    def test_unknown_note(self):
        self.assertIsNone(match_category("что-то новое", self.categories))
        self.assertIsNone(match_category("", self.categories))


class FirstKeywordTest(unittest.TestCase):
    def test_skips_short_words(self):
        self.assertEqual(first_keyword("на шаурму"), "шаурму")
        self.assertEqual(first_keyword("Кофе с собой"), "кофе")
        self.assertIsNone(first_keyword("42"))


if __name__ == "__main__":
    unittest.main()
