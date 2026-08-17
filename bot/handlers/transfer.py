"""Круг «выгрузил → показал ИИ → залил правки»: /import и приём файла."""

from __future__ import annotations

import datetime as dt
from io import BytesIO

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from .. import transfer
from ..db import Database, UserSettings
from ..formatting import esc, format_date, format_money, records_word
from ..keyboards import confirm_import

router = Router(name="transfer")

#: Файл больше — это точно не наша выгрузка.
MAX_FILE_BYTES = 5 * 1024 * 1024
#: Сколько правок показываем в предпросмотре целиком.
PREVIEW_LIMIT = 12

HOW_TO = (
    "📥 <b>Как залить правки</b>\n\n"
    "1. Возьми файл из /export → «🤖 JSON для ИИ»\n"
    "2. Отправь его любому ИИ с просьбой проверить суммы и категории\n"
    "3. Полученный файл пришли сюда обычным вложением\n\n"
    "Я покажу, что изменится, и применю только после твоего подтверждения. "
    "Записи, которых в файле нет, не трогаю."
)


def _describe(change: transfer.Change, user: UserSettings, today: dt.date) -> str:
    before = change.before
    if change.kind == "create":
        return (
            f"➕ <b>{format_money(change.amount or 0, user.currency)}</b> "
            f"{esc(change.category or 'без категории')} · {esc(change.note or '')}"
        )
    if change.kind == "delete" and before is not None:
        return (
            f"🗑 <code>#{before.id}</code> {format_money(before.amount, user.currency)} "
            f"{esc(before.category_title)} · {esc(before.note)}"
        )

    assert before is not None
    parts: list[str] = []
    if change.amount is not None:
        parts.append(
            f"{format_money(before.amount, user.currency)} → "
            f"<b>{format_money(change.amount, user.currency)}</b>"
        )
    if change.category:
        parts.append(f"{esc(before.category_name)} → <b>{esc(change.category)}</b>")
    if change.spent_on is not None:
        parts.append(
            f"{format_date(before.spent_on, today)} → "
            f"<b>{format_date(change.spent_on, today)}</b>"
        )
    if change.note is not None:
        parts.append(f"«{esc(before.note)}» → «<b>{esc(change.note)}</b>»")
    return f"✏️ <code>#{before.id}</code> " + ", ".join(parts)


def _preview(plan: transfer.Plan, user: UserSettings, today: dt.date) -> str:
    updates, creates, deletes = plan.of("update"), plan.of("create"), plan.of("delete")
    head = ["📥 <b>Что изменится</b>", ""]
    counts = []
    if updates:
        counts.append(f"исправлю {len(updates)}")
    if creates:
        counts.append(f"добавлю {len(creates)}")
    if deletes:
        counts.append(f"удалю {len(deletes)}")
    head.append(", ".join(counts).capitalize() + ".")
    if plan.unchanged:
        head.append(f"<i>Без изменений: {plan.unchanged}.</i>")
    if plan.skipped:
        head.append(f"<i>Не разобрал строк: {plan.skipped}.</i>")
    head.append("")

    shown = list(plan.changes)[:PREVIEW_LIMIT]
    head.extend(_describe(change, user, today) for change in shown)
    if len(plan.changes) > PREVIEW_LIMIT:
        hidden = len(plan.changes) - PREVIEW_LIMIT
        head.append(f"<i>…и ещё {hidden} {records_word(hidden)}</i>")
    return "\n".join(head)


@router.message(Command("import"))
async def cmd_import(message: Message) -> None:
    await message.answer(HOW_TO)


@router.message(F.document)
async def handle_document(
    message: Message,
    bot: Bot,
    db: Database,
    user: UserSettings,
    today: dt.date,
    state: FSMContext,
) -> None:
    document = message.document
    if document is None:
        return

    name = (document.file_name or "").lower()
    if not name.endswith(".json"):
        await message.answer(
            "Жду файл <code>.json</code> — тот, что отдаёт /export → «🤖 JSON для ИИ».\n"
            "CSV и PDF я обратно не читаю."
        )
        return
    if (document.file_size or 0) > MAX_FILE_BYTES:
        await message.answer("Файл слишком большой — это точно моя выгрузка?")
        return

    buffer = BytesIO()
    await bot.download(document, destination=buffer)

    existing = await db.expenses_between(user.user_id, dt.date(1970, 1, 1), dt.date(2999, 1, 1))
    try:
        plan = transfer.parse(buffer.getvalue(), existing, today)
    except transfer.ImportError_ as exc:
        await message.answer(f"⚠️ {esc(str(exc))}")
        return

    if not plan:
        await message.answer(
            "Расхождений с моей базой нет — менять нечего."
            + (f"\n<i>Не разобрал строк: {plan.skipped}.</i>" if plan.skipped else "")
        )
        return

    await state.update_data(import_file_id=document.file_id)
    await message.answer(_preview(plan, user, today), reply_markup=confirm_import())


@router.callback_query(F.data == "import:cancel")
async def cb_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.update_data(import_file_id=None)
    await callback.answer("Отменено")
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Отменил. Ничего не изменилось.", reply_markup=None)


@router.callback_query(F.data == "import:apply")
async def cb_apply(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    user: UserSettings,
    today: dt.date,
    state: FSMContext,
) -> None:
    file_id = (await state.get_data()).get("import_file_id")
    if not file_id:
        await callback.answer("Файл потерялся, пришли его заново", show_alert=True)
        return

    # Перечитываем файл и пересобираем план: база могла измениться, пока
    # пользователь смотрел предпросмотр.
    buffer = BytesIO()
    await bot.download(file_id, destination=buffer)
    existing = await db.expenses_between(user.user_id, dt.date(1970, 1, 1), dt.date(2999, 1, 1))
    try:
        plan = transfer.parse(buffer.getvalue(), existing, today)
    except transfer.ImportError_ as exc:
        await callback.answer(str(exc)[:180], show_alert=True)
        return

    categories = await db.list_categories(user.user_id)
    by_name = {
        category.name.lower().replace("ё", "е"): category for category in categories
    }
    fallback = await db.get_fallback_category(user.user_id)

    def resolve(name: str):
        return by_name.get(name.lower().replace("ё", "е"))

    updated = created = deleted = 0
    for change in plan.changes:
        if change.kind == "delete" and change.expense_id is not None:
            deleted += int(await db.delete_expense(user.user_id, change.expense_id))
        elif change.kind == "create":
            category = resolve(change.category) or fallback
            await db.add_expense(
                user_id=user.user_id,
                amount=change.amount or 0,
                note=change.note or "",
                spent_on=change.spent_on or today,
                category_id=category.id if category else None,
            )
            created += 1
        elif change.expense_id is not None:
            category = resolve(change.category) if change.category else None
            result = await db.update_expense(
                user.user_id,
                change.expense_id,
                amount=change.amount,
                note=change.note,
                spent_on=change.spent_on,
                category_id=category.id if category else None,
            )
            updated += int(result is not None)

    await state.update_data(import_file_id=None)
    await callback.answer("Готово")
    if isinstance(callback.message, Message):
        summary = ", ".join(
            part
            for part in (
                f"исправлено {updated}" if updated else "",
                f"добавлено {created}" if created else "",
                f"удалено {deleted}" if deleted else "",
            )
            if part
        )
        await callback.message.edit_text(
            f"✅ Применил: {summary or 'ничего'}.\nПроверить — /last или /stats.",
            reply_markup=None,
        )
