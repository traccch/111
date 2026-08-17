"""Инлайн-клавиатуры."""

from __future__ import annotations

from typing import Sequence

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .db import Category, Expense, Reminder

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


CHART_KINDS: tuple[tuple[str, str], ...] = (
    ("days", "По дням"),
    ("cats", "Категории"),
    ("months", "Месяцы"),
)


def chart_kinds(active: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for key, title in CHART_KINDS:
        mark = "· " if key == active else ""
        builder.button(text=f"{mark}{title}", callback_data=f"chart:{key}")
    builder.adjust(3)
    return builder.as_markup()


def cancel_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✖️ Отмена", callback_data="add:cancel")]
        ]
    )


def add_categories(categories: Sequence[Category]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for category in categories:
        builder.button(text=category.title, callback_data=f"add:cat:{category.id}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="✖️ Отмена", callback_data="add:cancel"))
    return builder.as_markup()


def skip_note() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Без комментария", callback_data="add:skip"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="add:cancel"),
            ]
        ]
    )


def export_kinds() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="📄 PDF за месяц", callback_data="exp:pdf"),
                InlineKeyboardButton(text="📊 CSV со всем", callback_data="exp:csv"),
            ]
        ]
    )


def reminder_actions() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⏰ Через 15 минут", callback_data="snooze"),
                InlineKeyboardButton(text="📊 Сводка", callback_data="rep:month"),
            ]
        ]
    )


def reminders_settings(
    reminders: Sequence[Reminder], skip_if_logged: bool
) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for reminder in reminders:
        builder.button(text=f"🗑 {reminder.title}", callback_data=f"delrem:{reminder.title}")
    builder.adjust(3)
    mark = "✅" if skip_if_logged else "⬜️"
    builder.row(
        InlineKeyboardButton(
            text=f"{mark} Молчать, если траты уже записаны", callback_data="togskip"
        )
    )
    return builder.as_markup()


def delete_many(expense_ids: Sequence[int]) -> InlineKeyboardMarkup:
    """Одна кнопка на всю пачку трат, записанных из одного сообщения."""
    payload = ",".join(str(expense_id) for expense_id in expense_ids)
    text = "🗑 Удалить всё" if len(expense_ids) > 1 else "🗑 Удалить"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=text, callback_data=f"delmany:{payload}")]
        ]
    )


def delete_buttons(expenses: Sequence[Expense]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for expense in expenses:
        builder.button(text=f"🗑 #{expense.id}", callback_data=f"del:{expense.id}")
    builder.adjust(3)
    return builder.as_markup()
