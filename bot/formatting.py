"""Форматирование сумм, дат и текстовых отчётов."""

from __future__ import annotations

import datetime as dt
import html
from typing import Sequence

from .db import CategoryTotal

NBSP = " "

MONTHS_GENITIVE = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)

MONTHS_NOMINATIVE = (
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
)

WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def format_money(minor: int, currency: str = "₽") -> str:
    """125050 → «1 250,50 ₽», 30000 → «300 ₽»."""
    sign = "−" if minor < 0 else ""
    major, cents = divmod(abs(int(minor)), 100)
    grouped = f"{major:,}".replace(",", NBSP)
    tail = f",{cents:02d}" if cents else ""
    return f"{sign}{grouped}{tail}{NBSP}{currency}"


def plural(number: int, one: str, few: str, many: str) -> str:
    n = abs(number) % 100
    if 11 <= n <= 14:
        return many
    n %= 10
    if n == 1:
        return one
    if 2 <= n <= 4:
        return few
    return many


def records_word(count: int) -> str:
    return plural(count, "запись", "записи", "записей")


def format_date(date: dt.date, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    if date == today:
        return "сегодня"
    if date == today - dt.timedelta(days=1):
        return "вчера"
    if date == today - dt.timedelta(days=2):
        return "позавчера"
    if date.year == today.year:
        return f"{date.day} {MONTHS_GENITIVE[date.month - 1]}"
    return f"{date.day} {MONTHS_GENITIVE[date.month - 1]} {date.year}"


def month_title(date: dt.date) -> str:
    return f"{MONTHS_NOMINATIVE[date.month - 1]} {date.year}"


def bar(value: int, maximum: int, width: int = 10) -> str:
    if maximum <= 0 or value <= 0:
        return "░" * width
    filled = max(1, min(width, round(value / maximum * width)))
    return "█" * filled + "░" * (width - filled)


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def render_breakdown(
    totals: Sequence[CategoryTotal], grand_total: int, currency: str
) -> list[str]:
    """Строки разбивки по категориям с бар-чартом и долями."""
    if not totals:
        return []
    top = max(item.total for item in totals)
    name_width = min(14, max(len(item.name) for item in totals))
    lines: list[str] = []
    for item in totals:
        share = round(item.total / grand_total * 100) if grand_total else 0
        name = item.name if len(item.name) <= name_width else item.name[: name_width - 1] + "…"
        lines.append(
            f"{item.emoji} <code>{esc(name.ljust(name_width))}</code> "
            f"{bar(item.total, top, 8)} <b>{format_money(item.total, currency)}</b>"
            f" · {share}%"
        )
    return lines


def limit_line(spent: int, limit: int, currency: str) -> str:
    share = spent / limit if limit else 0
    if share >= 1:
        icon = "🔴"
    elif share >= 0.8:
        icon = "🟡"
    else:
        icon = "🟢"
    return (
        f"{icon} {format_money(spent, currency)} из {format_money(limit, currency)}"
        f" ({round(share * 100)}%) {bar(spent, limit, 10)}"
    )
