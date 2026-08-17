"""Тесты ИИ-слоя. Сеть не трогаем: транспорт подменяется заглушкой."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import unittest
from unittest import mock

import aiohttp

from bot import ai
from bot.ai import AiClient, _to_date, _to_minor

TODAY = dt.date(2026, 8, 16)


def gemini_answer(payload: dict | str) -> dict:
    """Ответ Gemini в том виде, в каком его отдаёт API."""
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


class FakeResponse:
    def __init__(self, status: int, payload: dict | None = None, text: str = "") -> None:
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def json(self):
        return self._payload

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Заглушка aiohttp.ClientSession: отдаёт заготовленный ответ или бросает."""

    def __init__(self, response=None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error
        self.requests: list[tuple[str, dict]] = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, params=None, json=None):
        self.requests.append((url, {"params": params, "json": json}))
        if self._error is not None:
            raise self._error
        return self._response


class ConversionTest(unittest.TestCase):
    def test_amount_to_minor(self):
        self.assertEqual(_to_minor(300), 30000)
        self.assertEqual(_to_minor(1250.5), 125050)
        self.assertEqual(_to_minor("450"), 45000)

    def test_bad_amounts_rejected(self):
        for value in (0, -5, None, "много", 10**12):
            with self.subTest(value=value):
                self.assertIsNone(_to_minor(value))

    def test_dates(self):
        self.assertEqual(_to_date("2026-08-14", TODAY), dt.date(2026, 8, 14))
        # будущее и мусор — сегодняшний день, модель промахнулась
        self.assertEqual(_to_date("2027-01-01", TODAY), TODAY)
        self.assertEqual(_to_date("вчера", TODAY), TODAY)
        self.assertEqual(_to_date(None, TODAY), TODAY)


class WithoutKeyTest(unittest.IsolatedAsyncioTestCase):
    async def test_client_is_inert(self):
        client = AiClient(api_key="")
        self.assertFalse(client.available())
        self.assertIsNone(
            await client.extract_from_text("кофе 300", ["Кафе"], "Прочее", "₽", TODAY)
        )
        self.assertIsNone(await client.insight("что-то"))


class ExtractionTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AiClient(api_key="test-key", model="gemini-2.5-flash")

    async def extract(self, payload, **kwargs):
        session = FakeSession(FakeResponse(200, gemini_answer(payload)))
        with mock.patch.object(ai.aiohttp, "ClientSession", session):
            result = await self.client.extract_from_text(
                "в пятёрочке 2340 и кофе 300", ["Кафе", "Продукты"], "Прочее", "₽", TODAY
            )
        self.session = session
        return result

    async def test_two_expenses(self):
        result = await self.extract(
            {
                "transcript": "",
                "expenses": [
                    {"amount": 2340, "note": "пятёрочка", "date": "2026-08-16",
                     "category": "Продукты"},
                    {"amount": 300, "note": "кофе", "date": "2026-08-16", "category": "Кафе"},
                ],
            }
        )
        self.assertIsNotNone(result)
        self.assertEqual(len(result.expenses), 2)
        self.assertEqual(result.expenses[0].amount, 234000)
        self.assertEqual(result.expenses[0].category, "Продукты")
        self.assertEqual(result.expenses[1].note, "кофе")

    async def test_request_shape(self):
        await self.extract({"transcript": "", "expenses": []})
        url, kwargs = self.session.requests[0]
        self.assertIn("gemini-2.5-flash:generateContent", url)
        self.assertEqual(kwargs["params"], {"key": "test-key"})
        self.assertEqual(
            kwargs["json"]["generationConfig"]["responseMimeType"], "application/json"
        )
        prompt = kwargs["json"]["contents"][0]["parts"][0]["text"]
        self.assertIn("2026-08-16", prompt)  # модель должна знать сегодняшнюю дату
        self.assertIn("Кафе, Продукты", prompt)  # и список категорий

    async def test_broken_items_are_skipped(self):
        result = await self.extract(
            {
                "transcript": "",
                "expenses": [
                    {"amount": "ерунда", "note": "x", "date": "2026-08-16", "category": "Кафе"},
                    {"amount": 0, "note": "y", "date": "2026-08-16", "category": "Кафе"},
                    {"amount": 300, "note": "кофе", "date": "плохая дата", "category": "Кафе"},
                ],
            }
        )
        self.assertEqual(len(result.expenses), 1)
        self.assertEqual(result.expenses[0].spent_on, TODAY)

    async def test_not_json_is_survived(self):
        self.assertIsNone(await self.extract("извините, я не понял"))

    async def test_too_many_expenses_are_capped(self):
        result = await self.extract(
            {
                "transcript": "",
                "expenses": [
                    {"amount": 100 + index, "note": "x", "date": "2026-08-16",
                     "category": "Кафе"}
                    for index in range(50)
                ],
            }
        )
        self.assertEqual(len(result.expenses), ai.MAX_EXPENSES)


class TransportTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.client = AiClient(api_key="test-key")

    async def call(self, session):
        with mock.patch.object(ai.aiohttp, "ClientSession", session):
            return await self.client.insight("сводка")

    async def test_http_error(self):
        session = FakeSession(FakeResponse(429, text="quota exceeded"))
        self.assertIsNone(await self.call(session))

    async def test_network_error(self):
        self.assertIsNone(await self.call(FakeSession(error=aiohttp.ClientError("нет сети"))))

    async def test_timeout(self):
        self.assertIsNone(await self.call(FakeSession(error=asyncio.TimeoutError())))

    async def test_unexpected_body(self):
        session = FakeSession(FakeResponse(200, {"candidates": []}))
        self.assertIsNone(await self.call(session))

    async def test_insight_returns_text(self):
        session = FakeSession(FakeResponse(200, gemini_answer("Больше всего ушло на жильё.")))
        self.assertEqual(await self.call(session), "Больше всего ушло на жильё.")


class AudioTest(unittest.IsolatedAsyncioTestCase):
    async def test_audio_goes_as_inline_data(self):
        client = AiClient(api_key="test-key")
        session = FakeSession(
            FakeResponse(
                200,
                gemini_answer(
                    {
                        "transcript": "кофе триста",
                        "expenses": [
                            {"amount": 300, "note": "кофе", "date": "2026-08-16",
                             "category": "Кафе"}
                        ],
                    }
                ),
            )
        )
        with mock.patch.object(ai.aiohttp, "ClientSession", session):
            result = await client.extract_from_audio(
                b"OggS-fake", "audio/ogg", ["Кафе"], "Прочее", "₽", TODAY
            )

        self.assertEqual(result.transcript, "кофе триста")
        self.assertEqual(result.expenses[0].amount, 30000)

        parts = session.requests[0][1]["json"]["contents"][0]["parts"]
        inline = next(part for part in parts if "inline_data" in part)["inline_data"]
        self.assertEqual(inline["mime_type"], "audio/ogg")
        self.assertTrue(inline["data"])  # base64, не сырые байты


if __name__ == "__main__":
    unittest.main()
