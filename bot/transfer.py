"""Обмен данными с внешним ИИ: выгрузка JSON и разбор правленого файла.

Смысл круга такой: бот отдаёт всё, что знает, одним файлом; человек скармливает
его любому ИИ («проверь, нет ли опечаток в суммах»); ИИ возвращает тот же файл
с правками; бот показывает, что именно изменится, и применяет только после
подтверждения. Ничего не удаляется молча.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Sequence

from .db import Category, Expense

FORMAT = "expenses-export"
VERSION = 1

#: Больше — это уже не дневник трат, а чей-то чужой файл.
MAX_ROWS = 5000
MAX_AMOUNT_MINOR = 10**13

INSTRUCTIONS = (
    "Это выгрузка трат из телеграм-бота. Проверь суммы, даты и категории: "
    "опечатки (60000 вместо 6000), дубли, явно не та категория. Верни ЭТОТ ЖЕ "
    "JSON целиком, изменив только ошибочные поля. У существующих записей "
    "обязательно сохрани id. Новую трату добавляй без поля id. Чтобы удалить "
    "запись, добавь ей \"delete\": true. Суммы — в основных единицах валюты "
    "(300.50), даты — ГГГГ-ММ-ДД, категория — строка ровно из списка categories."
)


class ImportError_(ValueError):
    """Файл не похож на нашу выгрузку."""


def _major(minor: int) -> float:
    return round(minor / 100, 2)


def _to_minor(value: Any) -> Optional[int]:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    minor = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return minor if 0 < minor < MAX_AMOUNT_MINOR else None


def dump(
    expenses: Sequence[Expense],
    categories: Sequence[Category],
    limits: Sequence[tuple[str, int]],
    currency: str,
    timezone: str,
    today: dt.date,
) -> bytes:
    """Выгрузка со всем контекстом: по ней ИИ поймёт и категории, и лимиты."""
    payload = {
        "format": FORMAT,
        "version": VERSION,
        "_instructions": INSTRUCTIONS,
        "generated_at": today.isoformat(),
        "currency": currency,
        "timezone": timezone,
        "categories": [
            {"name": category.name, "emoji": category.emoji,
             "keywords": list(category.keywords)}
            for category in categories
        ],
        "limits": {name: _major(amount) for name, amount in limits},
        "expenses": [
            {
                "id": expense.id,
                "date": expense.spent_on.isoformat(),
                "amount": _major(expense.amount),
                "category": expense.category_name,
                "note": expense.note,
            }
            for expense in expenses
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


@dataclass(frozen=True)
class Change:
    """Одна правка. Тип: update / create / delete."""

    kind: str
    expense_id: Optional[int]
    amount: Optional[int] = None
    note: Optional[str] = None
    spent_on: Optional[dt.date] = None
    category: str = ""
    before: Optional[Expense] = None


@dataclass(frozen=True)
class Plan:
    changes: tuple[Change, ...]
    skipped: int  # строки, которые не удалось разобрать
    unchanged: int

    def of(self, kind: str) -> list[Change]:
        return [change for change in self.changes if change.kind == kind]

    def __bool__(self) -> bool:
        return bool(self.changes)


def _same(change_value: Any, current: Any) -> bool:
    return change_value is None or change_value == current


def parse(
    raw: bytes, existing: Sequence[Expense], today: dt.date
) -> Plan:
    """Сравнивает присланный файл с тем, что в базе, и собирает список правок."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ImportError_("Это не JSON — пришли файл, который отдал /export") from exc

    if not isinstance(data, dict) or "expenses" not in data:
        raise ImportError_("В файле нет списка expenses — похоже, это другой файл")

    rows = data.get("expenses")
    if not isinstance(rows, list):
        raise ImportError_("Поле expenses должно быть списком")
    if len(rows) > MAX_ROWS:
        raise ImportError_(f"Слишком много записей: {len(rows)}, максимум {MAX_ROWS}")

    by_id = {expense.id: expense for expense in existing}
    changes: list[Change] = []
    skipped = 0
    unchanged = 0

    for row in rows:
        if not isinstance(row, dict):
            skipped += 1
            continue

        raw_id = row.get("id")
        expense_id = raw_id if isinstance(raw_id, int) else None
        current = by_id.get(expense_id) if expense_id is not None else None

        if row.get("delete"):
            if current is not None:
                changes.append(Change("delete", expense_id, before=current))
            else:
                skipped += 1
            continue

        amount = _to_minor(row.get("amount"))
        note = row.get("note")
        note = str(note).strip()[:200] if isinstance(note, str) else None
        category = str(row.get("category") or "").strip()

        spent_on: Optional[dt.date] = None
        if row.get("date"):
            try:
                spent_on = dt.date.fromisoformat(str(row["date"]))
            except ValueError:
                spent_on = None

        if current is None:
            # запись без известного id — это новая трата, для неё нужна сумма
            if amount is None:
                skipped += 1
                continue
            changes.append(
                Change("create", None, amount, note or "", spent_on or today, category)
            )
            continue

        if (
            _same(amount, current.amount)
            and _same(note, current.note)
            and _same(spent_on, current.spent_on)
            and (not category or category == current.category_name)
        ):
            unchanged += 1
            continue

        changes.append(
            Change(
                "update",
                expense_id,
                amount if amount != current.amount else None,
                note if note != current.note else None,
                spent_on if spent_on != current.spent_on else None,
                category if category and category != current.category_name else "",
                before=current,
            )
        )

    # Записи, которых в файле не оказалось, не трогаем: ИИ мог прислать кусок.
    return Plan(tuple(changes), skipped, unchanged)
