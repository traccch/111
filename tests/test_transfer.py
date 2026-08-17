"""Тесты обмена с внешним ИИ: выгрузка JSON и разбор правленого файла."""

from __future__ import annotations

import datetime as dt
import json
import unittest

from bot import transfer
from bot.db import Category, Expense

TODAY = dt.date(2026, 8, 16)

CATEGORIES = [
    Category(1, "Кафе", "☕", ("кофе", "обед")),
    Category(2, "Жильё", "🏠", ("аренда",)),
    Category(9, "Прочее", "📦", (), is_fallback=True),
]


def expense(id: int, amount: int, note: str, category: str = "Кафе", day: int = 16) -> Expense:
    return Expense(
        id=id,
        amount=amount,
        note=note,
        spent_on=dt.date(2026, 8, day),
        category_id=1,
        category_name=category,
        category_emoji="☕",
    )


EXISTING = [
    expense(1, 30000, "кофе"),
    expense(2, 6000000, "аренда за август", "Жильё", 1),
]


class DumpTest(unittest.TestCase):
    def payload(self) -> dict:
        raw = transfer.dump(
            EXISTING, CATEGORIES, [("Всего за месяц", 6000000)], "₽", "Europe/Moscow", TODAY
        )
        return json.loads(raw.decode("utf-8"))

    def test_shape(self):
        data = self.payload()
        self.assertEqual(data["format"], transfer.FORMAT)
        self.assertEqual(data["currency"], "₽")
        self.assertEqual(data["timezone"], "Europe/Moscow")
        self.assertIn("_instructions", data)  # чтобы ИИ понял файл без пояснений

    def test_amounts_are_human_readable(self):
        expenses = self.payload()["expenses"]
        self.assertEqual(expenses[0]["amount"], 300.0)
        self.assertEqual(expenses[1]["amount"], 60000.0)
        self.assertEqual(expenses[0]["id"], 1)

    def test_context_included(self):
        data = self.payload()
        names = [item["name"] for item in data["categories"]]
        self.assertIn("Кафе", names)
        self.assertIn("кофе", data["categories"][0]["keywords"])
        self.assertEqual(data["limits"]["Всего за месяц"], 60000.0)


def edited(**changes) -> bytes:
    """Выгрузка, в которой поправлено то, что просят изменить."""
    data = json.loads(
        transfer.dump(EXISTING, CATEGORIES, [], "₽", "Europe/Moscow", TODAY).decode("utf-8")
    )
    for index, patch in changes.items():
        data["expenses"][int(index)].update(patch)
    return json.dumps(data, ensure_ascii=False).encode("utf-8")


class ParseTest(unittest.TestCase):
    def test_typo_in_amount_becomes_update(self):
        # ИИ увидел 60 000 вместо 6 000 и поправил
        plan = transfer.parse(edited(**{"1": {"amount": 6000}}), EXISTING, TODAY)
        self.assertEqual(len(plan.changes), 1)
        self.assertEqual(plan.unchanged, 1)

        change = plan.changes[0]
        self.assertEqual((change.kind, change.expense_id, change.amount), ("update", 2, 600000))
        self.assertIsNone(change.note)  # остальное не трогаем
        self.assertEqual(change.before.amount, 6000000)

    def test_category_and_date_updates(self):
        plan = transfer.parse(
            edited(**{"0": {"category": "Жильё", "date": "2026-08-10"}}), EXISTING, TODAY
        )
        change = plan.changes[0]
        self.assertEqual(change.category, "Жильё")
        self.assertEqual(change.spent_on, dt.date(2026, 8, 10))

    def test_new_row_becomes_create(self):
        data = json.loads(edited().decode("utf-8"))
        data["expenses"].append(
            {"date": "2026-08-15", "amount": 450, "category": "Кафе", "note": "обед"}
        )
        plan = transfer.parse(json.dumps(data).encode("utf-8"), EXISTING, TODAY)

        creates = plan.of("create")
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0].amount, 45000)
        self.assertEqual(creates[0].spent_on, dt.date(2026, 8, 15))

    def test_delete_flag(self):
        plan = transfer.parse(edited(**{"0": {"delete": True}}), EXISTING, TODAY)
        deletes = plan.of("delete")
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0].expense_id, 1)

    def test_untouched_file_changes_nothing(self):
        plan = transfer.parse(edited(), EXISTING, TODAY)
        self.assertFalse(plan)
        self.assertEqual(plan.unchanged, 2)

    def test_missing_rows_are_not_deleted(self):
        """ИИ мог прислать только часть — остальное должно остаться на месте."""
        data = json.loads(edited().decode("utf-8"))
        data["expenses"] = data["expenses"][:1]
        plan = transfer.parse(json.dumps(data).encode("utf-8"), EXISTING, TODAY)
        self.assertEqual(plan.of("delete"), [])

    def test_garbage_rows_are_counted_not_fatal(self):
        data = json.loads(edited().decode("utf-8"))
        data["expenses"].extend([
            {"amount": "много", "note": "ерунда"},
            {"id": 999, "amount": 100},  # чужой id
            "строка вместо объекта",
        ])
        plan = transfer.parse(json.dumps(data).encode("utf-8"), EXISTING, TODAY)
        self.assertEqual(plan.skipped, 2)
        self.assertEqual(len(plan.of("create")), 1)  # id 999 не наш → это новая трата

    def test_broken_field_in_known_row_is_not_silently_ignored(self):
        """Мусор вместо суммы — это «не разобрал», а не «без изменений»."""
        plan = transfer.parse(edited(**{"0": {"amount": "много"}}), EXISTING, TODAY)
        self.assertEqual(plan.skipped, 1)
        self.assertEqual(plan.unchanged, 1)
        self.assertFalse(plan)

        plan = transfer.parse(edited(**{"0": {"date": "вчера"}}), EXISTING, TODAY)
        self.assertEqual(plan.skipped, 1)

    def test_bad_files_are_rejected_clearly(self):
        for payload, hint in (
            ("не json вовсе".encode("utf-8"), "не JSON"),
            (b'{"foo": 1}', "expenses"),
            ('{"expenses": "строка"}'.encode("utf-8"), "списком"),
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(transfer.ImportError_) as caught:
                    transfer.parse(payload, EXISTING, TODAY)
                self.assertIn(hint, str(caught.exception))

    def test_too_many_rows(self):
        data = {"expenses": [{"amount": 1} for _ in range(transfer.MAX_ROWS + 1)]}
        with self.assertRaises(transfer.ImportError_):
            transfer.parse(json.dumps(data).encode("utf-8"), EXISTING, TODAY)

    def test_bom_is_survived(self):
        plan = transfer.parse(b"\xef\xbb\xbf" + edited(), EXISTING, TODAY)
        self.assertEqual(plan.unchanged, 2)


if __name__ == "__main__":
    unittest.main()
