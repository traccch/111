"""Пошаговый ввод траты: /add — сумма, категория, комментарий.

Свободный текст быстрее, но помнить формат нужно не всем: этот путь ведёт
за руку и не требует ничего, кроме нажатий и одного числа.
"""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from ..db import Database, UserSettings
from ..formatting import format_money
from ..keyboards import add_categories, cancel_button, skip_note
from ..parsing import MAX_AMOUNT_MINOR, parse_amount
from ..services import check_limits
from .expenses import render_expense

router = Router(name="add")

CANCELLED = "Отменил. Трата не записана."


class AddExpense(StatesGroup):
    amount = State()
    category = State()
    note = State()


@router.message(Command("add"))
async def cmd_add(message: Message, state: FSMContext) -> None:
    await state.set_state(AddExpense.amount)
    await message.answer(
        "💰 <b>Шаг 1 из 3.</b> Сколько потратил?\n"
        "<i>Просто число, например</i> <code>300</code> <i>или</i> <code>1250,50</code>",
        reply_markup=cancel_button(),
    )


@router.callback_query(F.data == "add:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(CANCELLED, reply_markup=None)


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer(CANCELLED)


# Команды пропускаем дальше по роутерам: /stats посреди ввода должен
# показать сводку, а не превратиться в «не понял сумму».
@router.message(AddExpense.amount, F.text, ~F.text.startswith("/"))
async def step_amount(
    message: Message, state: FSMContext, db: Database, user: UserSettings
) -> None:
    parsed = parse_amount(message.text or "")
    if parsed is None or not 0 < parsed[0] < MAX_AMOUNT_MINOR:
        await message.answer(
            "Не понял сумму. Нужно число, например <code>300</code>.",
            reply_markup=cancel_button(),
        )
        return

    amount, note = parsed
    await state.update_data(amount=amount, note=note)
    await state.set_state(AddExpense.category)
    await message.answer(
        f"<b>Шаг 2 из 3.</b> {format_money(amount, user.currency)} — на что?",
        reply_markup=add_categories(await db.list_categories(user.user_id)),
    )


@router.callback_query(AddExpense.category, F.data.startswith("add:cat:"))
async def step_category(
    callback: CallbackQuery, state: FSMContext, db: Database, user: UserSettings
) -> None:
    category_id = int(callback.data.rsplit(":", 1)[1])
    await state.update_data(category_id=category_id)
    await state.set_state(AddExpense.note)

    category = await db.get_category(user.user_id, category_id)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            f"<b>Шаг 3 из 3.</b> {category.title if category else ''} — "
            "добавить комментарий?\n<i>Напиши его или нажми «Без комментария».</i>",
            reply_markup=skip_note(),
        )


@router.callback_query(AddExpense.note, F.data == "add:skip")
async def cb_skip_note(
    callback: CallbackQuery, state: FSMContext, db: Database, user: UserSettings,
    today: dt.date,
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        await _save(callback.message, state, db, user, today, note=None)


@router.message(AddExpense.note, F.text, ~F.text.startswith("/"))
async def step_note(
    message: Message, state: FSMContext, db: Database, user: UserSettings, today: dt.date
) -> None:
    await _save(message, state, db, user, today, note=message.text)


async def _save(
    message: Message,
    state: FSMContext,
    db: Database,
    user: UserSettings,
    today: dt.date,
    note: str | None,
) -> None:
    data = await state.get_data()
    await state.clear()

    expense = await db.add_expense(
        user_id=user.user_id,
        amount=data["amount"],
        note=(note or data.get("note") or "").strip(),
        spent_on=today,
        category_id=data.get("category_id"),
    )

    lines = [render_expense(expense, user, today)]
    warnings = await check_limits(db, user, expense.category_id, today)
    if warnings:
        lines.append("")
        lines.extend(warnings)
    await message.answer("\n".join(lines))
