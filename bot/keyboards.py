"""Инлайн-клавиатуры."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Category, Expense

PERIODS: tuple[tuple[str, str], ...] = (
    ("day", "Сегодня"),
    ("week", "Неделя"),
    ("month", "Месяц"),
    ("all", "Всё время"),
)


def expense_actions(expense: Expense) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Категория", callback_data=f"pickcat:{expense.id}"
                ),
                InlineKeyboardButton(text="🗑 Удалить", callback_data=f"del:{expense.id}"),
            ]
        ]
    )


def category_picker(expense_id: int, categories: Sequence[Category]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.title, callback_data=f"setcat:{expense_id}:{category.id}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"back:{expense_id}"))
    return builder.as_markup()


def report_periods(active: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, title in PERIODS:
        mark = "· " if key == active else ""
        builder.button(text=f"{mark}{title}", callback_data=f"rep:{key}")
    builder.adjust(4)
    return builder.as_markup()


def delete_buttons(expenses: Sequence[Expense]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for expense in expenses:
        builder.button(text=f"🗑 #{expense.id}", callback_data=f"del:{expense.id}")
    builder.adjust(3)
    return builder.as_markup()
