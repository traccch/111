"""Выгрузки: CSV для таблиц и текстовые строки для PDF-отчёта."""

from __future__ import annotations

import csv
import datetime as dt
import io
from functools import lru_cache
from typing import Optional, Sequence

from .db import Expense
from .formatting import format_money

#: Ширины колонок таблицы в PDF (моноширинный шрифт).
COL_DATE = 10
COL_CATEGORY = 16
COL_AMOUNT = 14
COL_NOTE = 34

#: Чем заменить символ валюты, которого нет в моноширинном шрифте PDF.
MONO_FALLBACK = {"₽": "р.", "₴": "грн", "₸": "тг"}


@lru_cache(maxsize=1)
def _mono_charset() -> Optional[frozenset[int]]:
    """Какие символы умеет шрифт таблицы. None — если matplotlib не установлен."""
    try:
        from fontTools.ttLib import TTFont
        from matplotlib import font_manager
    except ImportError:
        return None

    path = font_manager.findfont(font_manager.FontProperties(family="DejaVu Sans Mono"))
    font = TTFont(path)
    codes: set[int] = set()
    for table in font["cmap"].tables:
        codes |= set(table.cmap.keys())
    return frozenset(codes)


def mono_currency(currency: str) -> str:
    """Таблица в PDF набрана DejaVu Sans Mono, а там нет, например, знака ₽ —
    без замены в отчёте появился бы пустой квадрат."""
    charset = _mono_charset()
    if charset is None or all(ord(char) in charset for char in currency):
        return currency
    return MONO_FALLBACK.get(currency, "у.е.")


def csv_bytes(expenses: Sequence[Expense], currency: str) -> bytes:
    """CSV с BOM — иначе Excel ломает кириллицу."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow(["id", "date", "amount", "currency", "category", "note"])
    for expense in expenses:
        writer.writerow(
            [
                expense.id,
                expense.spent_on.isoformat(),
                f"{expense.amount / 100:.2f}".replace(".", ","),
                currency,
                expense.category_name,
                expense.note,
            ]
        )
    return buffer.getvalue().encode("utf-8-sig")


def _cut(text: str, width: int) -> str:
    text = text.strip()
    if len(text) <= width:
        return text.ljust(width)
    return text[: width - 1] + "…"


def table_rows(expenses: Sequence[Expense], currency: str) -> list[str]:
    """Шапка, разделитель и строки таблицы для PDF."""
    header = (
        f"{'Дата'.ljust(COL_DATE)} {'Категория'.ljust(COL_CATEGORY)} "
        f"{'Сумма'.rjust(COL_AMOUNT)}  Комментарий"
    )
    rows = [header, "─" * (COL_DATE + COL_CATEGORY + COL_AMOUNT + COL_NOTE + 4)]
    for expense in expenses:
        rows.append(
            f"{expense.spent_on.strftime('%d.%m.%Y').ljust(COL_DATE)} "
            f"{_cut(expense.category_name, COL_CATEGORY)} "
            f"{format_money(expense.amount, currency).rjust(COL_AMOUNT)}  "
            f"{_cut(expense.note, COL_NOTE).rstrip()}"
        )
    return rows


def summary_lines(
    start: dt.date,
    end: dt.date,
    total: int,
    count: int,
    categories: Sequence[tuple[str, int, int]],
    currency: str,
    extra: Sequence[str] = (),
) -> list[str]:
    """Текстовая сводка для первой страницы PDF. categories: (имя, сумма, штук)."""
    days = (end - start).days + 1
    lines = [
        f"Период:        {start:%d.%m.%Y} — {end:%d.%m.%Y} ({days} дн.)",
        f"Всего:         {format_money(total, currency)} за {count} записей",
        f"Средний день:  {format_money(round(total / days), currency)}",
    ]
    lines.extend(extra)

    if categories:
        lines.append("")
        lines.append("Категории:")
        width = max(len(name) for name, _, _ in categories)
        for name, amount, number in categories:
            share = round(amount / total * 100) if total else 0
            lines.append(
                f"    {name.ljust(width)}  "
                f"{format_money(amount, currency).rjust(14)}  "
                f"{str(share).rjust(3)}%  ({number})"
            )
    return lines
