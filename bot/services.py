"""Бизнес-логика: периоды отчётов, сборка отчёта, проверка лимитов."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from .db import TOTAL_LIMIT_CATEGORY, Database, UserSettings
from .formatting import (
    MONTHS_GENITIVE,
    WEEKDAYS,
    days_word,
    esc,
    format_date,
    format_money,
    limit_line,
    month_title,
    records_word,
    render_breakdown,
    sparkline,
)

#: Сколько последних дней показываем спарклайном, чтобы строка не расползалась.
SPARK_DAYS = 30


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


def previous_range(
    period: str, start: dt.date, end: dt.date
) -> Optional[tuple[dt.date, dt.date, str]]:
    """Тот же по длине отрезок периодом раньше — и как его назвать в тексте."""
    if period == "day":
        day = start - dt.timedelta(days=1)
        return day, day, "вчера"
    if period == "week":
        return start - dt.timedelta(days=7), end - dt.timedelta(days=7), "неделей раньше"
    if period == "month":
        prev_start = month_start(start - dt.timedelta(days=1))
        length = (end - start).days
        prev_end = min(prev_start + dt.timedelta(days=length), month_end(prev_start))
        label = f"за те же дни в {MONTHS_GENITIVE[prev_start.month - 1]}"
        return prev_start, prev_end, label
    return None


async def trend_line(
    db: Database, user: UserSettings, period: str, start: dt.date, end: dt.date, total: int
) -> Optional[str]:
    """Сравнение с предыдущим таким же отрезком."""
    previous = previous_range(period, start, end)
    if previous is None:
        return None

    prev_start, prev_end, label = previous
    prev_total, prev_count = await db.total_between(user.user_id, prev_start, prev_end)
    if not prev_count:
        return None

    delta = total - prev_total
    share = round(abs(delta) / prev_total * 100)
    if share < 3:
        return f"≈ Столько же, сколько {label} ({format_money(prev_total, user.currency)})."

    icon = "🔺" if delta > 0 else "🔻"
    word = "больше" if delta > 0 else "меньше"
    return (
        f"{icon} На <b>{share}%</b> {word}, чем {label}"
        f" ({format_money(prev_total, user.currency)})."
    )


async def dynamics_lines(
    db: Database, user: UserSettings, start: dt.date, end: dt.date
) -> list[str]:
    """Спарклайн по дням и самый дорогой день периода."""
    window_start = max(start, end - dt.timedelta(days=SPARK_DAYS - 1))
    totals = await db.daily_totals(user.user_id, window_start, end)
    span = (end - window_start).days + 1
    if span < 3 or len(totals) < 2:
        return []

    days = [window_start + dt.timedelta(days=offset) for offset in range(span)]
    values = [totals.get(day, 0) for day in days]

    peak_day = max(totals, key=lambda day: totals[day])
    with_records = len(totals)
    lines = [
        "",
        f"<b>По дням</b> <i>(последние {span} {days_word(span)})</i>",
        f"<code>{sparkline(values)}</code>",
        f"<i>Дороже всего {format_date(peak_day, end)} — "
        f"{format_money(totals[peak_day], user.currency)} · "
        f"{with_records} {days_word(with_records)} с тратами</i>",
    ]
    return lines


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
    ]

    trend = await trend_line(db, user, period, start, end, total)
    if trend:
        lines.append(trend)
    lines.append("")

    totals = await db.totals_by_category(user.user_id, start, end)
    lines.extend(render_breakdown(totals, total, user.currency))

    lines.extend(await dynamics_lines(db, user, start, end))

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


async def insight_summary(db: Database, user: UserSettings, today: dt.date) -> str:
    """Голые цифры для модели: текущий месяц, прошлый и разбивка по категориям."""
    start, end = month_start(today), today
    total, count = await db.total_between(user.user_id, start, end)

    lines = [
        f"Валюта: {user.currency}",
        f"Текущий период: {start:%d.%m.%Y} — {end:%d.%m.%Y}",
        f"Всего потрачено: {total / 100:.2f} за {count} записей",
        f"В среднем за день: {total / 100 / ((end - start).days + 1):.2f}",
        "",
        "По категориям в этом месяце:",
    ]
    for item in await db.totals_by_category(user.user_id, start, end):
        share = round(item.total / total * 100) if total else 0
        lines.append(f"- {item.name}: {item.total / 100:.2f} ({share}%, {item.count} записей)")

    previous = previous_range("month", start, end)
    if previous:
        prev_start, prev_end, _ = previous
        prev_total, prev_count = await db.total_between(user.user_id, prev_start, prev_end)
        if prev_count:
            lines.append("")
            lines.append(
                f"Тот же отрезок прошлого месяца ({prev_start:%d.%m} — {prev_end:%d.%m}): "
                f"{prev_total / 100:.2f} за {prev_count} записей"
            )
            for item in await db.totals_by_category(user.user_id, prev_start, prev_end):
                lines.append(f"- {item.name}: {item.total / 100:.2f}")

    daily = await db.daily_totals(user.user_id, start, end)
    if daily:
        lines.append("")
        lines.append("По дням:")
        lines.extend(f"- {day:%d.%m} ({WEEKDAYS[day.weekday()]}): {amount / 100:.2f}"
                     for day, amount in sorted(daily.items()))

    limit = await db.get_limit(user.user_id, TOTAL_LIMIT_CATEGORY)
    if limit:
        lines.append("")
        lines.append(f"Лимит на месяц: {limit / 100:.2f}")

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
