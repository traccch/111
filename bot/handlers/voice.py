"""Голосовые сообщения: наговорил траты — бот их записал."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.types import Message

from ..ai import AiClient
from ..db import Database, UserSettings
from .ai_common import NO_AI, save_ai_expenses

router = Router(name="voice")

#: Больше 10 МБ — это не «сказал пару трат», а что-то не то.
MAX_AUDIO_BYTES = 10 * 1024 * 1024

NOTHING_HEARD = (
    "🎧 Не разобрал в записи ни одной траты.\n"
    "Попробуй сказать проще: «кофе триста рублей и такси четыреста пятьдесят»."
)


@router.message(F.voice | F.audio)
async def handle_voice(
    message: Message,
    bot: Bot,
    ai: AiClient,
    db: Database,
    user: UserSettings,
    today: dt.date,
) -> None:
    if not ai.available():
        await message.answer(NO_AI)
        return

    source = message.voice or message.audio
    if source is None:
        return
    if (source.file_size or 0) > MAX_AUDIO_BYTES:
        await message.answer("Запись слишком длинная — уложись в пару минут.")
        return

    note = await message.answer("🎧 Слушаю…")

    buffer = BytesIO()
    await bot.download(source, destination=buffer)
    mime = getattr(source, "mime_type", None) or "audio/ogg"

    categories = await db.list_categories(user.user_id)
    fallback = await db.get_fallback_category(user.user_id)
    result = await ai.extract_from_audio(
        buffer.getvalue(),
        mime,
        [category.name for category in categories],
        fallback.name if fallback else "Прочее",
        user.currency,
        today,
    )

    if result is None:
        await note.edit_text(
            "🎧 Не получилось разобрать запись — сервис ИИ не ответил.\n"
            "Попробуй ещё раз или напиши тратой текстом."
        )
        return

    if not result.expenses:
        heard = f"\n\n<i>Услышал: {result.transcript}</i>" if result.transcript else ""
        await note.edit_text(NOTHING_HEARD + heard)
        return

    text, markup = await save_ai_expenses(db, user, today, result, categories, fallback)
    await note.edit_text(text, reply_markup=markup)
