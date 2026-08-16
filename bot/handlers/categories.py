"""Управление категориями: /cats, /addcat, /delcat, /kw."""

from __future__ import annotations

import re

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from ..db import Database, UserSettings
from ..formatting import esc

router = Router(name="categories")

#: любой символ вне букв/цифр/пунктуации ASCII считаем эмодзи
_EMOJI = re.compile(r"^[^\w\s,]{1,4}", re.UNICODE)


def _split_emoji(raw: str) -> tuple[str, str]:
    """«🍕 Пицца» → ('🍕', 'Пицца'); без эмодзи вернёт дефолтное."""
    raw = raw.strip()
    match = _EMOJI.match(raw)
    if match:
        return match.group(0), raw[match.end() :].strip()
    return "📦", raw


@router.message(Command("cats", "categories"))
async def cmd_cats(message: Message, db: Database, user: UserSettings) -> None:
    categories = await db.list_categories(user.user_id)
    lines = ["🗂 <b>Категории</b>", ""]
    for category in categories:
        keywords = ", ".join(category.keywords[:8])
        if len(category.keywords) > 8:
            keywords += f" … (+{len(category.keywords) - 8})"
        suffix = f"\n    <i>{esc(keywords)}</i>" if keywords else ""
        lines.append(f"{esc(category.title)}{suffix}")
    lines.append("")
    lines.append(
        "Добавить: <code>/addcat 🍕 Пицца, пицца, додо</code>\n"
        "Удалить: <code>/delcat Пицца</code>\n"
        "Привязать слово: <code>/kw Кафе, шаурма</code>"
    )
    await message.answer("\n".join(lines))


@router.message(Command("addcat"))
async def cmd_addcat(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "Формат: <code>/addcat 🍕 Пицца, пицца, додо</code>\n"
            "После названия через запятую — ключевые слова (необязательно)."
        )
        return

    head, *keywords = [part.strip() for part in args.split(",")]
    emoji, name = _split_emoji(head)
    if not name:
        await message.answer("Не хватает названия категории.")
        return
    if len(name) > 32:
        await message.answer("Название длинновато, максимум 32 символа.")
        return

    category = await db.add_category(user.user_id, name, emoji, keywords)
    if category is None:
        await message.answer(f"Категория «{esc(name)}» уже есть.")
        return

    hint = f"\nКлючевые слова: <i>{esc(', '.join(category.keywords))}</i>" if category.keywords else ""
    await message.answer(f"Добавил категорию {esc(category.title)}.{hint}")


@router.message(Command("delcat"))
async def cmd_delcat(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    name = (command.args or "").strip()
    if not name:
        await message.answer("Формат: <code>/delcat Пицца</code>")
        return

    _, clean_name = _split_emoji(name)
    category = await db.find_category_by_name(user.user_id, clean_name or name)
    if category is None:
        await message.answer("Такой категории нет. Список — /cats")
        return
    if category.is_fallback:
        await message.answer("Эту категорию удалить нельзя — в неё уезжают все остальные.")
        return

    await db.delete_category(user.user_id, category.id)
    await message.answer(
        f"Удалил {esc(category.title)}. Прошлые траты переехали в «Прочее»."
    )


@router.message(Command("kw"))
async def cmd_keyword(
    message: Message, command: CommandObject, db: Database, user: UserSettings
) -> None:
    args = (command.args or "").strip()
    if "," not in args:
        await message.answer(
            "Формат: <code>/kw Кафе, шаурма</code> — привязать слово «шаурма» к категории «Кафе»."
        )
        return

    raw_name, *words = [part.strip() for part in args.split(",")]
    _, clean_name = _split_emoji(raw_name)
    category = await db.find_category_by_name(user.user_id, clean_name or raw_name)
    if category is None:
        await message.answer("Такой категории нет. Список — /cats")
        return

    added = [word for word in words if word]
    if not added:
        await message.answer("Не указаны слова для привязки.")
        return
    for word in added:
        await db.add_keyword(user.user_id, category.id, word)

    await message.answer(
        f"Запомнил для {esc(category.title)}: <i>{esc(', '.join(added))}</i>"
    )
