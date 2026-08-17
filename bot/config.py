"""Загрузка конфигурации из переменных окружения / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    db_path: str
    default_tz: str
    default_currency: str
    log_level: str
    ai_api_key: str
    ai_model: str
    ai_timeout: float


def load_config() -> Config:
    load_dotenv()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен от @BotFather."
        )

    tz = os.getenv("DEFAULT_TZ", "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RuntimeError(f"DEFAULT_TZ={tz!r} — неизвестный часовой пояс") from exc

    try:
        ai_timeout = float(os.getenv("AI_TIMEOUT", "20") or 20)
    except ValueError:
        ai_timeout = 20.0

    return Config(
        token=token,
        db_path=os.getenv("DB_PATH", "data/expenses.db").strip() or "data/expenses.db",
        default_tz=tz,
        default_currency=os.getenv("DEFAULT_CURRENCY", "₽").strip() or "₽",
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        ai_api_key=os.getenv("AI_API_KEY", "").strip(),
        ai_model=os.getenv("AI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        ai_timeout=ai_timeout,
    )
