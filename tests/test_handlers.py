"""Смоук-тесты хендлеров: прогоняем апдейты через диспетчер с фейковым ботом.

Проверяем, что роутинг, внедрение зависимостей и ответы работают, не ходя в сеть.
"""

from __future__ import annotations

import datetime as dt
import re
import tempfile
import unittest
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import (
    EditMessageMedia,
    EditMessageText,
    SendDocument,
    SendMessage,
    SendPhoto,
    TelegramMethod,
)
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, Chat, Message, Update, User, Voice

from bot import charts
from bot.ai import AiExpense, AiResult
from bot.db import Database
from bot.handlers import build_router
from bot.middlewares import UserMiddleware

CHAT_ID = 555
USER_ID = 777

ALLOWED_TAGS = {"b", "i", "u", "s", "code", "pre", "a"}
TAG_RE = re.compile(r"</?([a-zA-Z]+)[^>]*>")


class RecordingBot(Bot):
    """Бот, который вместо HTTP-запроса складывает вызовы в список."""

    def __init__(self) -> None:
        super().__init__(
            token="42:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN",
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.calls: list[TelegramMethod[Any]] = []

    async def __call__(self, method: TelegramMethod[TelegramType], request_timeout=None):
        self.calls.append(method)
        if isinstance(method, (SendMessage, SendDocument, SendPhoto)):
            # as_(self) — иначе у ответа нет бота и .edit_text() на нём падает,
            # хотя в бою aiogram привязывает его сам
            return Message(
                message_id=len(self.calls),
                date=dt.datetime.now(dt.timezone.utc),
                chat=Chat(id=CHAT_ID, type="private"),
            ).as_(self)
        return True

    async def download(self, file, destination=None, **kwargs):
        """Вместо похода в Telegram отдаём заранее известные байты."""
        if destination is not None:
            destination.write(b"OggS-fake-voice")
            return destination
        return None

    @property
    def texts(self) -> list[str]:
        return [
            call.text
            for call in self.calls
            if isinstance(call, SendMessage) and call.text is not None
        ]

    @property
    def last_shown(self) -> str:
        """Последний текст, который увидел пользователь: отправленный или
        вписанный поверх прежнего (хендлеры с «Слушаю…» правят своё сообщение)."""
        for call in reversed(self.calls):
            if isinstance(call, (SendMessage, EditMessageText)) and call.text:
                return call.text
        return ""


class FakeAi:
    """ИИ-клиент без сети: отдаёт заготовленный ответ и запоминает вызовы."""

    def __init__(self, result: AiResult | None = None, insight_text: str | None = None,
                 available: bool = False) -> None:
        self._result = result
        self._insight = insight_text
        self._available = available
        self.text_calls: list[str] = []
        self.audio_calls: list[bytes] = []

    def available(self) -> bool:
        return self._available

    async def extract_from_text(self, text, categories, fallback, currency, today):
        self.text_calls.append(text)
        return self._result

    async def extract_from_audio(self, audio, mime, categories, fallback, currency, today):
        self.audio_calls.append(audio)
        return self._result

    async def insight(self, summary):
        return self._insight


def ai_result(*expenses: tuple[int, str, str], transcript: str = "") -> AiResult:
    today = dt.date.today()
    return AiResult(
        transcript=transcript,
        expenses=tuple(
            AiExpense(amount=amount, note=note, spent_on=today, category=category)
            for amount, note, category in expenses
        ),
    )


_dispatcher: Dispatcher | None = None


def get_dispatcher() -> Dispatcher:
    """Роутеры aiogram — объекты уровня модуля, подключить их можно лишь однажды,
    поэтому диспетчер собирается один раз на весь прогон, а база подменяется в setUp."""
    global _dispatcher
    if _dispatcher is None:
        _dispatcher = Dispatcher()
        middleware = UserMiddleware()
        _dispatcher.message.middleware(middleware)
        _dispatcher.callback_query.middleware(middleware)
        _dispatcher.include_router(build_router())
    return _dispatcher


def make_message(text: str, message_id: int = 1) -> Message:
    return Message(
        message_id=message_id,
        date=dt.datetime.now(dt.timezone.utc),
        chat=Chat(id=CHAT_ID, type="private"),
        from_user=User(id=USER_ID, is_bot=False, first_name="Тест"),
        text=text,
    )


class HandlersTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(str(Path(self._tmp.name) / "test.db"), "Europe/Moscow", "₽")
        await self.db.connect()

        self.dp = get_dispatcher()
        self.dp["db"] = self.db
        self.ai = FakeAi()
        self.dp["ai"] = self.ai
        # диспетчер общий на весь прогон, поэтому состояние FSM из прошлого
        # теста иначе утекло бы в следующий
        self.dp.fsm.storage = MemoryStorage()

        self.bot = RecordingBot()
        self._update_id = 0

    async def asyncTearDown(self):
        await self.bot.session.close()
        await self.db.close()
        self._tmp.cleanup()

    async def send(self, text: str) -> str:
        self._update_id += 1
        await self.dp.feed_update(
            self.bot, Update(update_id=self._update_id, message=make_message(text))
        )
        return self.bot.texts[-1]

    async def click(self, data: str) -> None:
        self._update_id += 1
        await self.dp.feed_update(
            self.bot,
            Update(
                update_id=self._update_id,
                callback_query=CallbackQuery(
                    id=str(self._update_id),
                    from_user=User(id=USER_ID, is_bot=False, first_name="Тест"),
                    chat_instance="test",
                    data=data,
                    message=make_message("предыдущий ответ", message_id=100),
                ),
            ),
        )

    # --------------------------------------------------------------- сценарии

    async def test_start_and_help(self):
        self.assertIn("расходы", (await self.send("/start")).lower())
        self.assertIn("/stats", await self.send("/help"))

    async def test_add_expense_flow(self):
        answer = await self.send("кофе 300")
        self.assertIn("300", answer)
        self.assertIn("Кафе", answer)

        expenses = await self.db.last_expenses(USER_ID)
        self.assertEqual(len(expenses), 1)
        self.assertEqual(expenses[0].amount, 30000)

    async def test_unparsable_text_gets_hint(self):
        self.assertIn("Не нашёл сумму", await self.send("привет"))
        self.assertEqual(await self.db.last_expenses(USER_ID), [])

    async def test_change_category_teaches_bot(self):
        await self.send("шаурма 250")
        expense = (await self.db.last_expenses(USER_ID))[0]
        self.assertEqual(expense.category_name, "Прочее")

        cafe = await self.db.find_category_by_name(USER_ID, "Кафе")
        await self.click(f"setcat:{expense.id}:{cafe.id}")
        self.assertEqual((await self.db.get_expense(USER_ID, expense.id)).category_name, "Кафе")

        # в следующий раз «шаурма» уходит в Кафе сама
        await self.send("шаурма 300")
        self.assertEqual((await self.db.last_expenses(USER_ID))[0].category_name, "Кафе")

    async def test_delete_via_button_and_undo(self):
        await self.send("такси 450")
        expense = (await self.db.last_expenses(USER_ID))[0]
        await self.click(f"del:{expense.id}")
        self.assertEqual(await self.db.last_expenses(USER_ID), [])

        await self.send("метро 60")
        self.assertIn("Удалил", await self.send("/undo"))
        self.assertEqual(await self.db.last_expenses(USER_ID), [])

    async def test_stats_and_period_switch(self):
        await self.send("кофе 300")
        await self.send("продукты 1 200")
        report = await self.send("/stats")
        self.assertIn("Всего", report)
        for period in ("day", "week", "month", "all"):
            await self.click(f"rep:{period}")

    async def test_limit_lifecycle(self):
        self.assertIn("Установил", await self.send("/limit 1000"))
        await self.send("кофе 900")
        self.assertIn("🔴", await self.send("кофе 300"))
        self.assertIn("Всего за месяц", await self.send("/limits"))
        self.assertIn("Снял", await self.send("/limit 0"))

    async def test_category_commands(self):
        self.assertIn("Пицца", await self.send("/addcat 🍕 Пицца, додо, пиццерия"))
        await self.send("додо 700")
        self.assertEqual((await self.db.last_expenses(USER_ID))[0].category_name, "Пицца")

        self.assertIn("Запомнил", await self.send("/kw Пицца, пападжонс"))
        self.assertIn("Категории", await self.send("/cats"))
        self.assertIn("Удалил", await self.send("/delcat Пицца"))
        self.assertEqual((await self.db.last_expenses(USER_ID))[0].category_name, "Прочее")

    async def test_add_step_by_step(self):
        self.assertIn("Шаг 1", await self.send("/add"))
        self.assertIn("Не понял сумму", await self.send("абвгд"))

        self.assertIn("Шаг 2", await self.send("450"))
        cafe = await self.db.find_category_by_name(USER_ID, "Кафе")
        await self.click(f"add:cat:{cafe.id}")

        answer = await self.send("обед с коллегами")
        self.assertIn("450", answer)

        expense = (await self.db.last_expenses(USER_ID))[0]
        self.assertEqual(expense.amount, 45000)
        self.assertEqual(expense.category_name, "Кафе")
        self.assertEqual(expense.note, "обед с коллегами")

        # после записи состояние сброшено: обычный текст снова разбирается сам
        await self.send("кофе 300")
        self.assertEqual((await self.db.last_expenses(USER_ID))[0].note, "кофе")

    async def test_add_skip_note_and_cancel(self):
        await self.send("/add")
        await self.send("1 250,50")
        products = await self.db.find_category_by_name(USER_ID, "Продукты")
        await self.click(f"add:cat:{products.id}")
        await self.click("add:skip")

        expense = (await self.db.last_expenses(USER_ID))[0]
        self.assertEqual(expense.amount, 125050)
        self.assertEqual(expense.note, "")

        await self.send("/add")
        self.assertIn("Отменил", await self.send("/cancel"))
        self.assertIn("Нечего отменять", await self.send("/cancel"))
        self.assertEqual(len(await self.db.last_expenses(USER_ID)), 1)

    async def test_command_during_add_is_not_swallowed(self):
        await self.send("/add")
        self.assertIn("Расходы", await self.send("/stats"))

    async def test_reminder_commands(self):
        self.assertIn("21:00", await self.send("/remind 21:00"))
        self.assertIn("21:00", await self.send("/reminders"))
        self.assertEqual(len(await self.db.list_reminders(USER_ID)), 1)

        self.assertIn("Не понял время", await self.send("/remind вечером"))
        await self.send("/remind 9:00")
        self.assertEqual(len(await self.db.list_reminders(USER_ID)), 2)

        await self.click("delrem:09:00")
        self.assertEqual(len(await self.db.list_reminders(USER_ID)), 1)

        await self.click("togskip")
        self.assertFalse((await self.db.ensure_user(USER_ID)).skip_if_logged)

        self.assertIn("Выключил", await self.send("/remind off"))
        self.assertEqual(await self.db.list_reminders(USER_ID), [])

    async def test_snooze_button(self):
        await self.click("snooze")
        soon = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) + dt.timedelta(minutes=20)
        self.assertEqual(await self.db.pop_due_snoozes(soon), [USER_ID])

    async def test_export_csv(self):
        self.assertIn("Выгружать нечего", await self.send("/export"))

        await self.send("кофе 300")
        self.assertIn("Что выгрузить", await self.send("/export"))
        await self.click("exp:csv")

        documents = [call for call in self.bot.calls if isinstance(call, SendDocument)]
        self.assertEqual(len(documents), 1)
        payload = documents[0].document.data.decode("utf-8-sig")
        self.assertIn("кофе", payload)
        self.assertIn("300,00", payload)

    @unittest.skipUnless(charts.available(), "matplotlib не установлен")
    async def test_export_pdf(self):
        await self.send("кофе 300")
        await self.send("аренда 45000")
        await self.send("/export")
        await self.click("exp:pdf")

        documents = [call for call in self.bot.calls if isinstance(call, SendDocument)]
        self.assertEqual(len(documents), 1)
        self.assertTrue(documents[0].document.data.startswith(b"%PDF"))
        self.assertTrue(documents[0].document.filename.endswith(".pdf"))

    @unittest.skipUnless(charts.available(), "matplotlib не установлен")
    async def test_chart_command(self):
        self.assertIn("нечего рисовать", await self.send("/chart"))

        await self.send("кофе 300")
        await self.send("/chart")
        photos = [call for call in self.bot.calls if isinstance(call, SendPhoto)]
        self.assertEqual(len(photos), 1)

        await self.click("chart:cats")
        edits = [call for call in self.bot.calls if isinstance(call, EditMessageMedia)]
        self.assertEqual(len(edits), 1)

    # ------------------------------------------------------------------- ИИ

    async def send_voice(self) -> str:
        self._update_id += 1
        message = make_message("", message_id=self._update_id)
        message = message.model_copy(
            update={
                "text": None,
                "voice": Voice(
                    file_id="voice-1",
                    file_unique_id="u1",
                    duration=4,
                    mime_type="audio/ogg",
                    file_size=2048,
                ),
            }
        )
        await self.dp.feed_update(self.bot, Update(update_id=self._update_id, message=message))
        return self.bot.last_shown

    async def test_ai_splits_message_into_two_expenses(self):
        self.dp["ai"] = FakeAi(
            ai_result((234000, "пятёрочка", "Продукты"), (30000, "кофе", "Кафе")),
            available=True,
        )
        answer = await self.send("в пятёрочке 2340 и кофе 300")

        self.assertIn("Записал 2", answer)
        expenses = await self.db.last_expenses(USER_ID)
        self.assertEqual(len(expenses), 2)
        self.assertEqual({expense.amount for expense in expenses}, {234000, 30000})
        self.assertEqual(
            {expense.category_name for expense in expenses}, {"Продукты", "Кафе"}
        )

    async def test_ai_is_not_asked_for_simple_text(self):
        ai = FakeAi(ai_result((999999, "чушь", "Кафе")), available=True)
        self.dp["ai"] = ai

        await self.send("кофе 300")
        self.assertEqual(ai.text_calls, [], "обычный разбор справился сам")
        self.assertEqual((await self.db.last_expenses(USER_ID))[0].amount, 30000)

    async def test_ai_rescues_unparsable_text(self):
        self.dp["ai"] = FakeAi(ai_result((45000, "шаурма", "Кафе")), available=True)
        answer = await self.send("отдал за шаурму четыреста пятьдесят рублей")

        self.assertIn("450", answer)
        self.assertEqual(len(await self.db.last_expenses(USER_ID)), 1)

    async def test_ai_silence_falls_back_to_hint(self):
        self.dp["ai"] = FakeAi(None, available=True)
        self.assertIn("Не нашёл сумму", await self.send("совсем непонятное сообщение"))
        self.assertEqual(await self.db.last_expenses(USER_ID), [])

    async def test_delete_many_button(self):
        self.dp["ai"] = FakeAi(
            ai_result((234000, "пятёрочка", "Продукты"), (30000, "кофе", "Кафе")),
            available=True,
        )
        await self.send("в пятёрочке 2340 и кофе 300")
        expenses = await self.db.last_expenses(USER_ID)

        await self.click("delmany:" + ",".join(str(item.id) for item in expenses))
        self.assertEqual(await self.db.last_expenses(USER_ID), [])

    async def test_voice_without_key(self):
        self.assertIn("ключа нет", await self.send_voice())
        self.assertEqual(await self.db.last_expenses(USER_ID), [])

    async def test_voice_records_expenses(self):
        ai = FakeAi(
            ai_result((30000, "кофе", "Кафе"), transcript="кофе триста рублей"),
            available=True,
        )
        self.dp["ai"] = ai

        answer = await self.send_voice()
        self.assertEqual(ai.audio_calls, [b"OggS-fake-voice"])
        self.assertIn("Услышал", answer)
        self.assertEqual((await self.db.last_expenses(USER_ID))[0].amount, 30000)

    async def test_voice_with_nothing_recognised(self):
        self.dp["ai"] = FakeAi(ai_result(), available=True)
        self.assertIn("не разобрал", (await self.send_voice()).lower())

    async def test_insight(self):
        self.dp["ai"] = FakeAi(insight_text="Больше всего ушло на жильё.", available=True)
        for text in ("кофе 300", "продукты 1200", "такси 450"):
            await self.send(text)

        await self.send("/insight")
        self.assertIn("Больше всего ушло на жильё", self.bot.last_shown)

    async def test_insight_needs_data_and_key(self):
        self.assertIn("ключа нет", await self.send("/insight"))

        self.dp["ai"] = FakeAi(insight_text="текст", available=True)
        self.assertIn("мало данных", await self.send("/insight"))

    async def test_settings(self):
        self.assertIn("$", await self.send("/currency $"))
        self.assertIn("$", await self.send("кофе 300"))
        self.assertIn("Asia/Almaty", await self.send("/tz Asia/Almaty"))
        self.assertIn("Не знаю", await self.send("/tz Мордор"))

    async def test_all_answers_use_valid_html_tags(self):
        for text in ("/start", "/help", "кофе 300", "/stats", "/cats", "/limits", "/last"):
            await self.send(text)
        for text in self.bot.texts:
            for tag in TAG_RE.findall(text):
                self.assertIn(tag.lower(), ALLOWED_TAGS, f"недопустимый тег <{tag}> в: {text}")


if __name__ == "__main__":
    unittest.main()
