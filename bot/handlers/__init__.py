"""Роутеры бота. Порядок важен: expenses ловит любой свободный текст, он последний."""

from aiogram import Router

from . import (
    add,
    budget,
    categories,
    charts,
    common,
    expenses,
    export,
    reminders,
    reports,
    transfer,
    voice,
)


def build_router() -> Router:
    router = Router(name="root")
    router.include_router(common.router)
    router.include_router(add.router)
    router.include_router(categories.router)
    router.include_router(budget.router)
    router.include_router(reminders.router)
    router.include_router(reports.router)
    router.include_router(charts.router)
    router.include_router(export.router)
    router.include_router(transfer.router)
    router.include_router(voice.router)
    router.include_router(expenses.router)
    return router


__all__ = ["build_router"]
