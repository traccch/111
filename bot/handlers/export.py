"""Выгрузка трат: PDF-отчёт за месяц или CSV со всей историей."""

from __future__ import annotations

import datetime as dt

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from .. import charts, export
from ..db import Database, UserSettings
from ..formatting import format_money, month_title, records_word
from ..keyboards import export_kinds
from ..services import month_end, month_start, trend_line

router = Router(name="export")

NO_MATPLOTLIB = (
    "📄 PDF рисует matplotlib, а он не установлен.\n\n"
    "Поставить: <code>pip install matplotlib</code>. CSV работает и без него — "
    "кнопка ниже."
)


@router.message(Command("export"))
async def cmd_export(
    message: Message, db: Database, user: UserSettings, today: dt.date
) -> None:
    first = await db.first_expense_date(user.user_id)
    if first is None:
        await message.answer("Выгружать нечего — трат пока нет.")
        return

    await message.answer(
        "📤 <b>Что выгрузить?</b>\n\n"
        f"<b>PDF</b> — отчёт за {month_title(today)}: сводка, график по категориям, "
        "динамика по дням и таблица трат.\n"
        "<b>CSV</b> — все траты с самого начала, таблицей для Excel.",
        reply_markup=export_kinds(),
    )


@router.callback_query(F.data == "exp:csv")
async def cb_csv(callback: CallbackQuery, db: Database, user: UserSettings) -> None:
    first = await db.first_expense_date(user.user_id)
    if first is None:
        await callback.answer("Трат пока нет", show_alert=True)
        return

    expenses = await db.expenses_between(user.user_id, first, dt.date(2999, 12, 31))
    payload = export.csv_bytes(expenses, user.currency)
    filename = f"expenses-{dt.date.today().isoformat()}.csv"

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer_document(
            BufferedInputFile(payload, filename=filename),
            caption=f"Выгрузил {len(expenses)} {records_word(len(expenses))}.",
        )


@router.callback_query(F.data == "exp:pdf")
async def cb_pdf(
    callback: CallbackQuery, db: Database, user: UserSettings, today: dt.date
) -> None:
    if not charts.available():
        await callback.answer()
        if isinstance(callback.message, Message):
            await callback.message.answer(NO_MATPLOTLIB, reply_markup=export_kinds())
        return

    start, end = month_start(today), today
    # в таблицу PDF идёт моноширинный шрифт, а он знает не все символы валют
    mono = export.mono_currency(user.currency)
    expenses = await db.expenses_between(user.user_id, start, end)
    if not expenses:
        await callback.answer("В этом месяце трат ещё нет", show_alert=True)
        return

    total, count = await db.total_between(user.user_id, start, end)
    totals = await db.totals_by_category(user.user_id, start, end)

    extra: list[str] = []
    trend = await trend_line(db, user, "month", start, end, total)
    if trend:
        # в PDF идёт голый текст, разметка Telegram там ни к чему
        extra.append("")
        extra.append(_plain(trend))

    limit = await db.get_limit(user.user_id, 0)
    if limit:
        spent, _ = await db.total_between(user.user_id, start, month_end(today))
        extra.append(
            f"Лимит:         {format_money(spent, mono)} "
            f"из {format_money(limit, mono)}"
        )

    daily = await db.daily_totals(user.user_id, start, end)
    days = [start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)]

    payload = charts.expense_pdf(
        title="Отчёт по расходам",
        subtitle=f"{month_title(today)} · сформирован {today:%d.%m.%Y}",
        summary_lines=export.summary_lines(
            start, end, total, count,
            [(item.name, item.total, item.count) for item in totals],
            mono, extra,
        ),
        categories=[(item.name, item.total) for item in totals],
        days=days,
        amounts=[daily.get(day, 0) for day in days],
        table=export.table_rows(expenses, mono),
        currency=mono,
    )

    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer_document(
            BufferedInputFile(payload, filename=f"expenses-{today:%Y-%m}.pdf"),
            caption=f"Отчёт за {month_title(today)}.",
        )


def _plain(text: str) -> str:
    """Убирает HTML-разметку: в PDF попадает обычный текст."""
    import re

    return re.sub(r"<[^>]+>", "", text)
