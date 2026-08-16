"""Мидлвари: подстановка настроек пользователя и «сегодня» в его часовом поясе."""

from __future__ import annotations

import datetime as dt
from typing import Any, Awaitable, Callable, Dict
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User

from .db import Database


def today_for(tz: str) -> dt.date:
    try:
        zone = ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    return dt.datetime.now(zone).date()


class UserMiddleware(BaseMiddleware):
    """Гарантирует, что пользователь есть в БД, и кладёт его настройки в data."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")
        if user is None or user.is_bot:
            return await handler(event, data)

        db: Database = data["db"]
        settings = await db.ensure_user(user.id)
        data["user"] = settings
        data["today"] = today_for(settings.tz)
        return await handler(event, data)
