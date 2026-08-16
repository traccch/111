"""Бизнес-логика: периоды отчётов, сборка отчёта, проверка лимитов."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from .db import TOTAL_LIMIT_CATEGORY, Database, UserSettings
from .formatting import (
    esc,
    format_date,
    format_money,
    limit_line,
    month_title,
    records_word,
    render_breakdown,
)


def month_start(date: dt.date) -> dt.date:
    return date.replace(day=1)


def month_end(date: dt.date) -> dt.date:
    if date.month == 12:
        return date.replace(day=31)
    return date.replace(month=date.month + 1, day=1) - dt.timedelta(days=1)


def period_range(period: str, today: dt.date) -> tuple[dt.date, dt.date, str]:
    """Возвращает (начало, конец, заголовок) для ключа периода."""
    if period == "day":
        return today, today, f"за {format_date(today, today)}"
    if period == "week":
        start = today - dt.timedelta(days=today.weekday())
        return start, today, "за неделю"
    if period == "all":
        return dt.date(1970, 1, 1), today, "за всё время"
    return month_start(today), today, f"за {month_title(today)}"


async def build_report(
    db: Database, user: UserSettings, period: str, today: dt.date
) -> str:
    start, end, title = period_range(period, today)
    if period == "all":
        first = await db.first_expense_date(user.user_id)
        start = first or today

    total, count = await db.total_between(user.user_id, start, end)
    header = f"📊 <b>Расходы {esc(title)}</b>"
    if total == 0:
        return f"{header}\n\nПока пусто. Напиши, например: <code>кофе 300</code>"

    lines = [
        header,
        f"Всего: <b>{format_money(total, user.currency)}</b> · "
        f"{count} {records_word(count)}",
        "",
    ]

    totals = await db.totals_by_category(user.user_id, start, end)
    lines.extend(render_breakdown(totals, total, user.currency))

    days = (end - start).days + 1
    if days > 1:
        lines.append("")
        lines.append(
            f"Средний день: <b>{format_money(round(total / days), user.currency)}</b>"
        )
        if period == "month":
            days_in_month = (month_end(today) - month_start(today)).days + 1
            forecast = round(total / days * days_in_month)
            lines.append(f"Прогноз на месяц: <b>{format_money(forecast, user.currency)}</b>")

    limit_block = await month_limit_summary(db, user, today)
    if limit_block:
        lines.append("")
        lines.append(limit_block)

    return "\n".join(lines)


async def month_limit_summary(
    db: Database, user: UserSettings, today: dt.date
) -> Optional[str]:
    limit = await db.get_limit(user.user_id, TOTAL_LIMIT_CATEGORY)
    if limit is None:
        return None
    spent, _ = await db.total_between(user.user_id, month_start(today), month_end(today))
    left = limit - spent
    tail = (
        f"осталось {format_money(left, user.currency)}"
        if left >= 0
        else f"перерасход {format_money(-left, user.currency)}"
    )
    return f"🎯 Лимит на месяц: {limit_line(spent, limit, user.currency)}\n{tail}"


async def check_limits(
    db: Database, user: UserSettings, category_id: Optional[int], today: dt.date
) -> list[str]:
    """Предупреждения по лимитам после добавления траты."""
    start, end = month_start(today), month_end(today)
    warnings: list[str] = []

    if category_id is not None:
        cat_limit = await db.get_limit(user.user_id, category_id)
        if cat_limit:
            spent = await db.category_total_between(user.user_id, category_id, start, end)
            category = await db.get_category(user.user_id, category_id)
            name = f"Лимит {category.title}" if category else "Лимит категории"
            warnings.extend(_limit_warning(name, spent, cat_limit, user.currency))

    total_limit = await db.get_limit(user.user_id, TOTAL_LIMIT_CATEGORY)
    if total_limit:
        spent, _ = await db.total_between(user.user_id, start, end)
        warnings.extend(_limit_warning("Лимит на месяц", spent, total_limit, user.currency))

    return warnings


def _limit_warning(name: str, spent: int, limit: int, currency: str) -> list[str]:
    share = spent / limit if limit else 0
    if share >= 1:
        return [
            f"🔴 <b>{esc(name)}</b>: лимит {format_money(limit, currency)} превышен на "
            f"{format_money(spent - limit, currency)}"
        ]
    if share >= 0.8:
        return [
            f"🟡 <b>{esc(name)}</b>: потрачено {round(share * 100)}% лимита, "
            f"осталось {format_money(limit - spent, currency)}"
        ]
    return []
