"""Общее для ИИ-путей: сохранение разобранных трат и ответ пользователю."""

from __future__ import annotations

import datetime as dt
from typing import Optional, Sequence

from aiogram.types import InlineKeyboardMarkup

from ..ai import AiResult
from ..db import Category, Database, UserSettings
from ..formatting import esc, format_date, format_money, records_word
from ..keyboards import delete_many
from ..services import check_limits

NO_AI = (
    "🤖 Это умеет только версия с подключённым ИИ, а ключа нет.\n\n"
    "Бесплатный ключ берётся за минуту на "
    '<a href="https://aistudio.google.com/apikey">aistudio.google.com/apikey</a> '
    "(нужен только аккаунт Google, карта не нужна). Впиши его в файл "
    "<code>.env</code> строкой <code>AI_API_KEY=…</code> и перезапусти бота."
)

#: Столько трат из одного сообщения ещё разумно записать без подтверждения.
MAX_AT_ONCE = 10


def _resolve(
    name: str, categories: Sequence[Category], fallback: Optional[Category]
) -> Optional[Category]:
    """Название категории от модели — в нашу категорию."""
    cleaned = name.strip().lower().replace("ё", "е")
    for category in categories:
        if category.name.lower().replace("ё", "е") == cleaned:
            return category
    return fallback


async def save_ai_expenses(
    db: Database,
    user: UserSettings,
    today: dt.date,
    result: AiResult,
    categories: Sequence[Category],
    fallback: Optional[Category],
) -> tuple[str, Optional[InlineKeyboardMarkup]]:
    """Записывает траты из ответа модели и собирает ответ пользователю."""
    saved = []
    for item in result.expenses[:MAX_AT_ONCE]:
        category = _resolve(item.category, categories, fallback)
        saved.append(
            await db.add_expense(
                user_id=user.user_id,
                amount=item.amount,
                note=item.note,
                spent_on=item.spent_on,
                category_id=category.id if category else None,
            )
        )

    total = sum(expense.amount for expense in saved)
    lines = [
        f"✅ Записал {len(saved)} {records_word(len(saved))} "
        f"на <b>{format_money(total, user.currency)}</b>"
        if len(saved) > 1
        else f"✅ <b>{format_money(total, user.currency)}</b>",
        "",
    ]
    for expense in saved:
        note = f" · {esc(expense.note)}" if expense.note else ""
        lines.append(
            f"<code>#{expense.id}</code> {esc(expense.category_title)}{note} — "
            f"<b>{format_money(expense.amount, user.currency)}</b>"
            f" · {format_date(expense.spent_on, today)}"
        )

    if result.transcript:
        lines.append("")
        lines.append(f"<i>Услышал: {esc(result.transcript)}</i>")

    # Лимиты проверяем один раз, по самой крупной трате: иначе на десяти
    # тратах прилетело бы десять одинаковых предупреждений.
    biggest = max(saved, key=lambda expense: expense.amount)
    warnings = await check_limits(db, user, biggest.category_id, today)
    if warnings:
        lines.append("")
        lines.extend(warnings)

    return "\n".join(lines), delete_many([expense.id for expense in saved])
