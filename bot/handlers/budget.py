"""Месячные лимиты: /limit, /limits."""

from __future__ import annotations

import datetime as dt

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..db import TOTAL_LIMIT_CATEGORY, Database, UserSettings
from ..formatting import esc, format_money, limit_line
from ..parsing import MAX_AMOUNT_MINOR, parse_amount
from ..services import month_end, month_start

router = Router(name="budget")

USAGE = (
    "Формат:\n"
    "<code>/limit 30000</code> — общий лимит на месяц\n"
    "<code>/limit Кафе 8000</code> — лимит по категории\n"
    "<code>/limit Кафе 0</code> — снять лимит"
)


@router.message(Command("limit"))
async def cmd_limit(
    message: Message,
    command: CommandObject,
    db: Database,
    user: UserSettings,
    today: dt.date,
) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(USAGE)
        return

    parsed = parse_amount(args)
    if parsed is None:
        await message.answer("Не нашёл сумму лимита.\n\n" + USAGE)
        return

    amount, rest = parsed
    if amount >= MAX_AMOUNT_MINOR:
        await message.answer("Слишком большая сумма — похоже на опечатку.")
        return

    category_id = TOTAL_LIMIT_CATEGORY
    label = "общий лимит на месяц"
    if rest:
        category = await db.find_category_by_name(user.user_id, rest)
        if category is None:
            await message.answer(
                f"Категории «{esc(rest)}» нет. Список — /cats\n\n" + USAGE
            )
            return
        category_id = category.id
        label = f"лимит для {category.title}"

    if amount == 0:
        removed = await db.delete_limit(user.user_id, category_id)
        await message.answer(
            f"Снял {esc(label)}." if removed else f"А {esc(label)} и не был установлен."
        )
        return

    await db.set_limit(user.user_id, category_id, amount)

    start, end = month_start(today), month_end(today)
    if category_id == TOTAL_LIMIT_CATEGORY:
        spent, _ = await db.total_between(user.user_id, start, end)
    else:
        spent = await db.category_total_between(user.user_id, category_id, start, end)

    await message.answer(
        f"🎯 Установил {esc(label)}: <b>{format_money(amount, user.currency)}</b>\n"
        f"{limit_line(spent, amount, user.currency)}"
    )


@router.message(Command("limits"))
async def cmd_limits(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    limits = await db.list_limits(user.user_id)
    if not limits:
        await message.answer("Лимитов пока нет.\n\n" + USAGE)
        return

    start, end = month_start(today), month_end(today)
    lines = ["🎯 <b>Лимиты на месяц</b>", ""]
    for category_id, amount in limits:
        if category_id == TOTAL_LIMIT_CATEGORY:
            spent, _ = await db.total_between(user.user_id, start, end)
            title = "Всего за месяц"
        else:
            category = await db.get_category(user.user_id, category_id)
            if category is None:
                continue
            spent = await db.category_total_between(user.user_id, category_id, start, end)
            title = category.title
        lines.append(f"<b>{esc(title)}</b>\n{limit_line(spent, amount, user.currency)}")
        lines.append("")

    await message.answer("\n".join(lines).strip())
