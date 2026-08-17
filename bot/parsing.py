"""Разбор свободного текста вида «кофе 300», «такси 450 вчера», «1,5к продукты»."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, Optional, Sequence

from .db import Category

MAX_AMOUNT_MINOR = 10**13  # 100 млрд в основной валюте — заведомо опечатка

RELATIVE_DAYS: dict[str, int] = {
    "сегодня": 0,
    "вчера": 1,
    "позавчера": 2,
}

MULTIPLIERS: dict[str, int] = {
    "к": 1_000,
    "k": 1_000,
    "тыс": 1_000,
    "тыс.": 1_000,
    "тысяча": 1_000,
    "тысячи": 1_000,
    "тысяч": 1_000,
    "кк": 1_000_000,
    "млн": 1_000_000,
    "m": 1_000_000,
}

_CURRENCY = r"(?:₽|\$|€|₸|₴|руб(?:л(?:ь|я|ей))?|rub|usd|eur|грн|тг|р)\.?"

# Валютные суффиксы/префиксы, приклеенные к числу: «300р», «₽300», «450 руб.»
_CURRENCY_AFTER = re.compile(r"(?<=\d)\s*" + _CURRENCY + r"(?![\w])", re.IGNORECASE)
_CURRENCY_BEFORE = re.compile(_CURRENCY + r"(?=\d)", re.IGNORECASE)

_NUMBER = re.compile(
    r"(?<![\w.,])"
    r"(?P<int>\d{1,3}(?:[  ]\d{3})+|\d+)"
    r"(?:[.,](?P<frac>\d{1,2}))?"
    r"\s*(?P<mult>кк|к|k|тыс\.?|тысяч[аи]?|млн|m)?"
    r"(?![\w])",
    re.IGNORECASE,
)

_DATE = re.compile(r"(?<![\d.,/])(\d{1,2})[./](\d{1,2})(?:[./](\d{2,4}))?(?![\d.,/])")

_RELATIVE = re.compile(r"\b(" + "|".join(RELATIVE_DAYS) + r")\b", re.IGNORECASE)

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


@dataclass(frozen=True)
class ParsedExpense:
    amount: int  # в минорных единицах
    note: str
    spent_on: dt.date


class ParseError(ValueError):
    """Текст похож на трату, но сумму разобрать не удалось."""


def _to_minor(int_part: str, frac_part: Optional[str], mult: Optional[str]) -> int:
    digits = int_part.replace(" ", "").replace(" ", "")
    value = Decimal(digits)
    if frac_part:
        value += Decimal(frac_part) / (10 ** len(frac_part))
    if mult:
        value *= MULTIPLIERS[mult.lower().rstrip(".")]
    minor = (value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(minor)


def _cleanup_note(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" \t-–—:;,. ")
    return text


def _extract_relative_date(text: str, today: dt.date) -> tuple[str, Optional[dt.date]]:
    match = _RELATIVE.search(text)
    if match is None:
        return text, None
    delta = RELATIVE_DAYS[match.group(1).lower()]
    return text[: match.start()] + " " + text[match.end() :], today - dt.timedelta(days=delta)


def _extract_explicit_date(text: str, today: dt.date) -> tuple[str, Optional[dt.date]]:
    """Ищет дату вида 05.08 / 5.8.2026 — но только если без неё остаётся сумма."""
    for match in _DATE.finditer(text):
        day, month = int(match.group(1)), int(match.group(2))
        year_raw = match.group(3)
        if year_raw is None:
            year = today.year
        else:
            year = int(year_raw)
            if year < 100:
                year += 2000
        try:
            parsed = dt.date(year, month, day)
        except ValueError:
            continue
        rest = text[: match.start()] + " " + text[match.end() :]
        if _NUMBER.search(rest) is None:
            continue  # без этого числа суммы не останется — значит это не дата
        if year_raw is None and parsed > today:
            parsed = dt.date(year - 1, month, day)
        return rest, parsed
    return text, None


def strip_currency(text: str) -> str:
    return _CURRENCY_BEFORE.sub(" ", _CURRENCY_AFTER.sub(" ", text))


def parse_amount(text: str) -> Optional[tuple[int, str]]:
    """Достаёт из строки сумму (в минорных единицах) и остаток текста.

    Если чисел несколько, берётся последнее: «2 кофе 300» → 300.
    """
    working = strip_currency(text)
    matches = list(_NUMBER.finditer(working))
    if not matches:
        return None
    match = matches[-1]
    amount = _to_minor(match.group("int"), match.group("frac"), match.group("mult"))
    rest = _cleanup_note(working[: match.start()] + " " + working[match.end() :])
    return amount, rest


def count_amounts(text: str) -> int:
    """Сколько чисел, похожих на суммы, в строке. Больше одного — повод
    показать сообщение модели: регулярка возьмёт только последнее."""
    return len(_NUMBER.findall(strip_currency(text)))


def parse_expense(text: str, today: Optional[dt.date] = None) -> Optional[ParsedExpense]:
    """Разбирает строку в трату. Возвращает None, если суммы в тексте нет."""
    today = today or dt.date.today()
    original = text.strip()
    if not original:
        return None

    working = strip_currency(original)

    working, spent_on = _extract_relative_date(working, today)
    if spent_on is None:
        working, spent_on = _extract_explicit_date(working, today)

    parsed = parse_amount(working)
    if parsed is None:
        return None

    amount, note = parsed
    if amount <= 0:
        raise ParseError("Сумма должна быть больше нуля")
    if amount >= MAX_AMOUNT_MINOR:
        raise ParseError("Слишком большая сумма — похоже на опечатку")

    return ParsedExpense(amount=amount, note=note, spent_on=spent_on or today)


def match_category(note: str, categories: Sequence[Category]) -> Optional[Category]:
    """Подбирает категорию по ключевым словам. Самое длинное совпадение выигрывает."""
    text = note.lower().replace("ё", "е")
    if not text:
        return None
    words = set(_WORD.findall(text))

    best: Optional[Category] = None
    best_len = 0
    for category in categories:
        if category.is_fallback:
            continue
        if category.name.lower().replace("ё", "е") in words and len(category.name) > best_len:
            best, best_len = category, len(category.name)
        for keyword in category.keywords:
            kw = keyword.lower().replace("ё", "е")
            if len(kw) <= best_len:
                continue
            if kw in words or (" " in kw and kw in text):
                best, best_len = category, len(kw)
    return best


def first_keyword(note: str) -> Optional[str]:
    """Первое значимое слово заметки — используем для обучения категорий."""
    for word in _WORD.findall(note.lower()):
        if len(word) >= 3:
            return word
    return None


def iter_words(note: str) -> Iterable[str]:
    return _WORD.findall(note.lower())
