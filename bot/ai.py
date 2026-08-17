"""Разбор сложных фраз и голоса через Gemini.

Слой сугубо вспомогательный: без ключа `available()` вернёт False, и бот
работает ровно как раньше. Любая ошибка сети или невнятный ответ модели —
это `None`, а не исключение: пользователь не должен терять трату из-за того,
что чужой сервис прилёг.

Почему Gemini: у него бесплатный тариф без карты и приём аудио прямо в
запросе, поэтому голосовое сообщение не требует отдельного распознавания.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional, Sequence

import aiohttp

logger = logging.getLogger(__name__)

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: Больше — почти наверняка модель фантазирует, а не разобрала сообщение.
MAX_EXPENSES = 20
#: Разумный потолок суммы (в минорных единицах), выше — опечатка или бред.
MAX_AMOUNT_MINOR = 10**13

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "transcript": {"type": "STRING"},
        "expenses": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "amount": {"type": "NUMBER"},
                    "note": {"type": "STRING"},
                    "date": {"type": "STRING"},
                    "category": {"type": "STRING"},
                },
                "required": ["amount", "note", "date", "category"],
            },
        },
    },
    "required": ["transcript", "expenses"],
}

PROMPT = """Ты — разборщик трат в телеграм-боте учёта расходов.

Сегодня {today} ({weekday}). Валюта пользователя: {currency}.
Доступные категории: {categories}.

Из сообщения пользователя вытащи все траты. Правила:
- amount — сумма в основных единицах, число (300, 1250.5). Никаких знаков валюты.
- note — короткое описание тратой пользователя, 1–3 слова, без суммы и даты.
- date — дата траты в формате ГГГГ-ММ-ДД. «вчера», «в пятницу», «5 августа»
  переводи в дату. Если дата не названа — сегодняшняя.
- category — строго одна строка из списка категорий. Если ничего не подходит,
  напиши «{fallback}».
- В одном сообщении может быть несколько трат — верни их все.
- Если трат в сообщении нет вообще, верни пустой список.
- transcript — для текста пустая строка, для аудио: что было сказано.

Не придумывай траты, которых нет. Не объединяй разные покупки в одну."""

INSIGHT_PROMPT = """Ты — спокойный помощник в боте учёта личных расходов.

Вот сводка трат пользователя:

{summary}

Напиши 3–5 коротких предложений живым русским языком: что заметно в этих
цифрах — куда ушла основная часть денег, что изменилось по сравнению с прошлым
периодом, есть ли необычные всплески.

Правила: только то, что видно в цифрах, без выдумок. Никаких советов «начни
экономить» и морализаторства — пользователь взрослый человек. Без markdown и
списков, просто текст. Не здоровайся и не прощайся."""


@dataclass(frozen=True)
class AiExpense:
    amount: int  # минорные единицы
    note: str
    spent_on: dt.date
    category: str


@dataclass(frozen=True)
class AiResult:
    transcript: str
    expenses: tuple[AiExpense, ...]


def _to_minor(value: Any) -> Optional[int]:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    minor = int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not 0 < minor < MAX_AMOUNT_MINOR:
        return None
    return minor


def _to_date(value: Any, today: dt.date) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return today
    # Дата из будущего — почти всегда промах модели с годом.
    if parsed > today or parsed.year < today.year - 5:
        return today
    return parsed


class AiClient:
    def __init__(
        self,
        api_key: str = "",
        model: str = "gemini-2.5-flash",
        timeout: float = 20.0,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model
        self._timeout = timeout

    def available(self) -> bool:
        return bool(self._api_key)

    # ------------------------------------------------------------- транспорт

    async def _call(self, parts: list[dict], schema: Optional[dict] = None) -> Optional[str]:
        """Один запрос к модели. Возвращает текст ответа или None при любой беде."""
        if not self.available():
            return None

        payload: dict[str, Any] = {"contents": [{"parts": parts}]}
        if schema is not None:
            payload["generationConfig"] = {
                "responseMimeType": "application/json",
                "responseSchema": schema,
            }

        url = API_URL.format(model=self._model)
        try:
            timeout = aiohttp.ClientTimeout(total=self._timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url, params={"key": self._api_key}, json=payload
                ) as response:
                    if response.status != 200:
                        body = (await response.text())[:300]
                        logger.warning("Gemini ответил %s: %s", response.status, body)
                        return None
                    data = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.warning("Не достучался до Gemini: %s", exc)
            return None

        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            logger.warning("Неожиданный ответ Gemini: %s", str(data)[:300])
            return None

    # --------------------------------------------------------------- разбор

    def _prompt(
        self, categories: Sequence[str], fallback: str, currency: str, today: dt.date
    ) -> str:
        weekdays = (
            "понедельник", "вторник", "среда", "четверг",
            "пятница", "суббота", "воскресенье",
        )
        return PROMPT.format(
            today=today.isoformat(),
            weekday=weekdays[today.weekday()],
            currency=currency,
            categories=", ".join(categories) or fallback,
            fallback=fallback,
        )

    def _parse(self, raw: str, today: dt.date) -> Optional[AiResult]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("Gemini вернул не JSON: %s", raw[:200])
            return None
        if not isinstance(data, dict):
            return None

        expenses: list[AiExpense] = []
        for item in (data.get("expenses") or [])[:MAX_EXPENSES]:
            if not isinstance(item, dict):
                continue
            amount = _to_minor(item.get("amount"))
            if amount is None:
                continue
            expenses.append(
                AiExpense(
                    amount=amount,
                    note=str(item.get("note") or "").strip()[:100],
                    spent_on=_to_date(item.get("date"), today),
                    category=str(item.get("category") or "").strip(),
                )
            )

        return AiResult(
            transcript=str(data.get("transcript") or "").strip(),
            expenses=tuple(expenses),
        )

    async def extract_from_text(
        self,
        text: str,
        categories: Sequence[str],
        fallback: str,
        currency: str,
        today: dt.date,
    ) -> Optional[AiResult]:
        parts = [
            {"text": self._prompt(categories, fallback, currency, today)},
            {"text": f"Сообщение пользователя:\n{text}"},
        ]
        raw = await self._call(parts, EXTRACT_SCHEMA)
        return self._parse(raw, today) if raw else None

    async def extract_from_audio(
        self,
        audio: bytes,
        mime_type: str,
        categories: Sequence[str],
        fallback: str,
        currency: str,
        today: dt.date,
    ) -> Optional[AiResult]:
        parts = [
            {"text": self._prompt(categories, fallback, currency, today)},
            {
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(audio).decode("ascii"),
                }
            },
            {"text": "Разбери голосовое сообщение выше."},
        ]
        raw = await self._call(parts, EXTRACT_SCHEMA)
        return self._parse(raw, today) if raw else None

    async def insight(self, summary: str) -> Optional[str]:
        raw = await self._call([{"text": INSIGHT_PROMPT.format(summary=summary)}])
        if not raw:
            return None
        return raw.strip()[:2000] or None
