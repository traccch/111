"""Добавление трат свободным текстом, редактирование, удаление, экспорт."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message

from ..ai import AiClient
from ..db import Database, Expense, UserSettings
from ..formatting import esc, format_date, format_money, records_word
from ..keyboards import category_picker, delete_buttons, expense_actions
from ..parsing import (
    ParseError,
    count_amounts,
    first_keyword,
    match_category,
    parse_expense,
)
from ..services import check_limits
from .ai_common import save_ai_expenses

router = Router(name="expenses")


def render_expense(expense: Expense, user: UserSettings, today: dt.date) -> str:
    note = f" · {esc(expense.note)}" if expense.note else ""
    when = format_date(expense.spent_on, today)
    return (
        f"✅ <b>{format_money(expense.amount, user.currency)}</b> — "
        f"{esc(expense.category_title)}{note}\n"
        f"<i>{when} · #{expense.id}</i>"
    )


@router.message(Command("del"))
async def cmd_delete(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Укажи номер траты: <code>/del 42</code> (номер есть в /last).")
        return
    if await db.delete_expense(user.user_id, int(raw)):
        await message.answer(f"🗑 Трата #{int(raw)} удалена.")
    else:
        await message.answer("Такой траты нет.")


@router.message(Command("undo"))
async def cmd_undo(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    recent = await db.last_expenses(user.user_id, limit=1)
    if not recent:
        await message.answer("Удалять нечего — трат пока нет.")
        return
    expense = recent[0]
    await db.delete_expense(user.user_id, expense.id)
    await message.answer(
        f"🗑 Удалил: {format_money(expense.amount, user.currency)} — "
        f"{esc(expense.category_title)}"
    )


@router.message(Command("last"))
async def cmd_last(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    expenses = await db.last_expenses(user.user_id, limit=10)
    if not expenses:
        await message.answer("Трат пока нет. Напиши, например: <code>кофе 300</code>")
        return

    lines = [f"🧾 <b>Последние {len(expenses)} {records_word(len(expenses))}</b>", ""]
    for expense in expenses:
        note = f" · {esc(expense.note)}" if expense.note else ""
        lines.append(
            f"<code>#{expense.id}</code> {format_date(expense.spent_on, today)} — "
            f"<b>{format_money(expense.amount, user.currency)}</b> "
            f"{esc(expense.category_title)}{note}"
        )
    await message.answer("\n".join(lines), reply_markup=delete_buttons(expenses))


@router.message(F.text, ~F.text.startswith("/"))
async def add_expense(
    message: Message, db: Database, user: UserSettings, today: dt.date, ai: AiClient
) -> None:
    text = (message.text or "").strip()
    try:
        parsed = parse_expense(text, today)
    except ParseError as exc:
        await message.answer(f"⚠️ {exc}")
        return

    # Регулярка берёт из строки одну сумму. Если сумм несколько или не нашлось
    # ни одной — зовём ИИ; он же разберёт «в пятёрочке 2340 и кофе 300» в две
    # траты. Без ключа всё работает как раньше.
    if ai.available() and (parsed is None or count_amounts(text) > 1):
        if await _try_ai(message, db, user, today, ai, text, fallback_used=parsed is None):
            return

    if parsed is None:
        await message.answer(
            "Не нашёл сумму. Напиши трату так: <code>кофе 300</code> или "
            "<code>450 такси вчера</code>.\nВсе команды — /help"
        )
        return

    categories = await db.list_categories(user.user_id)
    category = match_category(parsed.note, categories)
    if category is None:
        category = await db.get_fallback_category(user.user_id)

    expense = await db.add_expense(
        user_id=user.user_id,
        amount=parsed.amount,
        note=parsed.note,
        spent_on=parsed.spent_on,
        category_id=category.id if category else None,
    )

    lines = [render_expense(expense, user, today)]
    warnings = await check_limits(db, user, expense.category_id, today)
    if warnings:
        lines.append("")
        lines.extend(warnings)

    await message.answer("\n".join(lines), reply_markup=expense_actions(expense))


async def _try_ai(
    message: Message,
    db: Database,
    user: UserSettings,
    today: dt.date,
    ai: AiClient,
    text: str,
    fallback_used: bool,
) -> bool:
    """Пробует разобрать сообщение моделью. True — трата записана."""
    categories = await db.list_categories(user.user_id)
    fallback = await db.get_fallback_category(user.user_id)
    result = await ai.extract_from_text(
        text,
        [category.name for category in categories],
        fallback.name if fallback else "Прочее",
        user.currency,
        today,
    )

    if result is None or not result.expenses:
        return False
    # Одна трата — обычный разбор надёжнее и уже справился, не мешаем ему.
    if len(result.expenses) < 2 and not fallback_used:
        return False

    answer, markup = await save_ai_expenses(db, user, today, result, categories, fallback)
    await message.answer(answer, reply_markup=markup)
    return True


@router.callback_query(F.data.startswith("delmany:"))
async def cb_delete_many(
    callback: CallbackQuery, db: Database, user: UserSettings
) -> None:
    raw = callback.data.split(":", 1)[1]
    deleted = 0
    for chunk in raw.split(","):
        if chunk.isdigit() and await db.delete_expense(user.user_id, int(chunk)):
            deleted += 1

    await callback.answer("Удалено" if deleted else "Уже удалено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"🗑 Удалил {deleted} {records_word(deleted)}." if deleted else "🗑 Уже удалено.",
            reply_markup=None,
        )


@router.callback_query(F.data.startswith("del:"))
async def cb_delete(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    expense_id = int(callback.data.split(":", 1)[1])
    deleted = await db.delete_expense(user.user_id, expense_id)
    await callback.answer("Удалено" if deleted else "Уже удалено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"🗑 Трата #{expense_id} удалена.", reply_markup=None
        )


@router.callback_query(F.data.startswith("pickcat:"))
async def cb_pick_category(
    callback: CallbackQuery, db: Database, user: UserSettings
) -> None:
    expense_id = int(callback.data.split(":", 1)[1])
    expense = await db.get_expense(user.user_id, expense_id)
    if expense is None:
        await callback.answer("Трата уже удалена", show_alert=True)
        return
    categories = await db.list_categories(user.user_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=category_picker(expense_id, categories)
        )


@router.callback_query(F.data.startswith("setcat:"))
async def cb_set_category(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    _, raw_expense, raw_category = callback.data.split(":")
    expense_id, category_id = int(raw_expense), int(raw_category)

    expense = await db.set_expense_category(user.user_id, expense_id, category_id)
    if expense is None:
        await callback.answer("Трата уже удалена", show_alert=True)
        return

    # Запоминаем выбор: слово из заметки закрепляем за выбранной категорией.
    learned = first_keyword(expense.note)
    if learned:
        await db.add_keyword(user.user_id, category_id, learned)

    await callback.answer("Категория обновлена")
    if isinstance(callback.message, Message):
        text = render_expense(expense, user, today)
        if learned:
            text += f"\n<i>Запомнил: «{esc(learned)}» → {esc(expense.category_title)}</i>"
        await callback.message.edit_text(text, reply_markup=expense_actions(expense))


@router.callback_query(F.data.startswith("back:"))
async def cb_back(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    expense_id = int(callback.data.split(":", 1)[1])
    expense = await db.get_expense(user.user_id, expense_id)
    await callback.answer()
    if expense is None or not isinstance(callback.message, Message):
        return
    await callback.message.edit_text(
        render_expense(expense, user, today), reply_markup=expense_actions(expense)
    )
