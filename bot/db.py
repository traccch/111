"""Слой доступа к данным поверх SQLite (aiosqlite).

Суммы хранятся в минорных единицах (копейках) целым числом, чтобы не ловить
ошибки округления float.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass, field
from typing import Optional, Sequence

import aiosqlite

#: category_id, под которым хранится общий (не привязанный к категории) лимит
TOTAL_LIMIT_CATEGORY = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    currency   TEXT NOT NULL DEFAULT '₽',
    tz         TEXT NOT NULL DEFAULT 'Europe/Moscow',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    emoji       TEXT NOT NULL DEFAULT '📦',
    keywords    TEXT NOT NULL DEFAULT '',
    is_fallback INTEGER NOT NULL DEFAULT 0,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS expenses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    note        TEXT NOT NULL DEFAULT '',
    spent_on    TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_expenses_user_date ON expenses(user_id, spent_on);

CREATE TABLE IF NOT EXISTS limits (
    user_id     INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    amount      INTEGER NOT NULL,
    PRIMARY KEY (user_id, category_id)
);
"""

#: (эмодзи, название, ключевые слова)
DEFAULT_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("🍔", "Продукты", ("продукты", "еда", "магазин", "супермаркет", "пятерочка",
                        "пятёрочка", "перекресток", "перекрёсток", "магнит", "лента",
                        "ашан", "вкусвилл", "молоко", "хлеб")),
    ("☕", "Кафе", ("кофе", "кафе", "ресторан", "обед", "ужин", "завтрак", "бар",
                    "пиво", "доставка", "пицца", "суши", "бургер", "столовая")),
    ("🚕", "Транспорт", ("такси", "метро", "автобус", "трамвай", "маршрутка",
                         "бензин", "заправка", "транспорт", "поезд", "самолет",
                         "самолёт", "каршеринг", "самокат", "парковка")),
    ("🏠", "Жильё", ("аренда", "квартира", "жкх", "коммуналка", "интернет",
                     "электричество", "вода", "ипотека")),
    ("🛍", "Покупки", ("одежда", "обувь", "техника", "покупки", "маркетплейс",
                       "озон", "ozon", "вб", "wildberries", "днс", "икеа")),
    ("💊", "Здоровье", ("аптека", "врач", "лекарства", "стоматолог", "анализы",
                        "здоровье", "линзы", "спортзал", "фитнес")),
    ("🎬", "Развлечения", ("кино", "театр", "игры", "подписка", "концерт",
                           "развлечения", "музей", "книга", "steam")),
    ("🎓", "Образование", ("курсы", "обучение", "учеба", "учёба", "репетитор",
                           "школа", "университет")),
    ("🎁", "Подарки", ("подарок", "подарки", "цветы", "днюха")),
    ("📦", "Прочее", ()),
)


@dataclass(frozen=True)
class UserSettings:
    user_id: int
    currency: str
    tz: str


@dataclass(frozen=True)
class Category:
    id: int
    name: str
    emoji: str
    keywords: tuple[str, ...] = field(default=())
    is_fallback: bool = False

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True)
class Expense:
    id: int
    amount: int
    note: str
    spent_on: dt.date
    category_id: Optional[int]
    category_name: str
    category_emoji: str

    @property
    def category_title(self) -> str:
        return f"{self.category_emoji} {self.category_name}"


@dataclass(frozen=True)
class CategoryTotal:
    category_id: Optional[int]
    name: str
    emoji: str
    total: int
    count: int

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


def _split_keywords(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _join_keywords(keywords: Sequence[str]) -> str:
    seen: list[str] = []
    for kw in keywords:
        kw = kw.strip().lower()
        if kw and kw not in seen:
            seen.append(kw)
    return ",".join(seen)


def _row_to_category(row: aiosqlite.Row) -> Category:
    return Category(
        id=row["id"],
        name=row["name"],
        emoji=row["emoji"],
        keywords=_split_keywords(row["keywords"]),
        is_fallback=bool(row["is_fallback"]),
    )


def _row_to_expense(row: aiosqlite.Row) -> Expense:
    return Expense(
        id=row["id"],
        amount=row["amount"],
        note=row["note"],
        spent_on=dt.date.fromisoformat(row["spent_on"]),
        category_id=row["category_id"],
        category_name=row["name"] or "Без категории",
        category_emoji=row["emoji"] or "❔",
    )


_EXPENSE_SELECT = """
SELECT e.id, e.amount, e.note, e.spent_on, e.category_id, c.name, c.emoji
FROM expenses e
LEFT JOIN categories c ON c.id = e.category_id
"""


class Database:
    def __init__(self, path: str, default_tz: str, default_currency: str) -> None:
        self._path = path
        self._default_tz = default_tz
        self._default_currency = default_currency
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------ setup

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database.connect() не был вызван")
        return self._conn

    async def connect(self) -> None:
        directory = os.path.dirname(os.path.abspath(self._path))
        os.makedirs(directory, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------ users

    async def ensure_user(self, user_id: int) -> UserSettings:
        """Создаёт пользователя с набором категорий по умолчанию (идемпотентно)."""
        cur = await self.conn.execute(
            "SELECT user_id, currency, tz FROM users WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        if row is not None:
            return UserSettings(row["user_id"], row["currency"], row["tz"])

        await self.conn.execute(
            "INSERT INTO users (user_id, currency, tz) VALUES (?, ?, ?)",
            (user_id, self._default_currency, self._default_tz),
        )
        await self.conn.executemany(
            "INSERT OR IGNORE INTO categories (user_id, name, emoji, keywords, is_fallback)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (user_id, name, emoji, _join_keywords(keywords), int(not keywords))
                for emoji, name, keywords in DEFAULT_CATEGORIES
            ],
        )
        await self.conn.commit()
        return UserSettings(user_id, self._default_currency, self._default_tz)

    async def set_currency(self, user_id: int, currency: str) -> None:
        await self.conn.execute(
            "UPDATE users SET currency = ? WHERE user_id = ?", (currency, user_id)
        )
        await self.conn.commit()

    async def set_tz(self, user_id: int, tz: str) -> None:
        await self.conn.execute("UPDATE users SET tz = ? WHERE user_id = ?", (tz, user_id))
        await self.conn.commit()

    # ------------------------------------------------------------- categories

    async def list_categories(self, user_id: int) -> list[Category]:
        cur = await self.conn.execute(
            "SELECT id, name, emoji, keywords, is_fallback FROM categories"
            " WHERE user_id = ? ORDER BY is_fallback, name",
            (user_id,),
        )
        return [_row_to_category(row) for row in await cur.fetchall()]

    async def get_category(self, user_id: int, category_id: int) -> Optional[Category]:
        cur = await self.conn.execute(
            "SELECT id, name, emoji, keywords, is_fallback FROM categories"
            " WHERE user_id = ? AND id = ?",
            (user_id, category_id),
        )
        row = await cur.fetchone()
        return _row_to_category(row) if row else None

    async def find_category_by_name(self, user_id: int, name: str) -> Optional[Category]:
        cur = await self.conn.execute(
            "SELECT id, name, emoji, keywords, is_fallback FROM categories"
            " WHERE user_id = ? AND lower(name) = lower(?)",
            (user_id, name.strip()),
        )
        row = await cur.fetchone()
        return _row_to_category(row) if row else None

    async def get_fallback_category(self, user_id: int) -> Optional[Category]:
        cur = await self.conn.execute(
            "SELECT id, name, emoji, keywords, is_fallback FROM categories"
            " WHERE user_id = ? ORDER BY is_fallback DESC, id LIMIT 1",
            (user_id,),
        )
        row = await cur.fetchone()
        return _row_to_category(row) if row else None

    async def add_category(
        self, user_id: int, name: str, emoji: str, keywords: Sequence[str] = ()
    ) -> Optional[Category]:
        try:
            cur = await self.conn.execute(
                "INSERT INTO categories (user_id, name, emoji, keywords) VALUES (?, ?, ?, ?)",
                (user_id, name.strip(), emoji, _join_keywords(keywords)),
            )
        except aiosqlite.IntegrityError:
            return None
        await self.conn.commit()
        return await self.get_category(user_id, cur.lastrowid)

    async def delete_category(self, user_id: int, category_id: int) -> bool:
        """Удаляет категорию, перенося её траты в запасную категорию."""
        category = await self.get_category(user_id, category_id)
        if category is None or category.is_fallback:
            return False
        fallback = await self.get_fallback_category(user_id)
        fallback_id = fallback.id if fallback and fallback.id != category_id else None
        await self.conn.execute(
            "UPDATE expenses SET category_id = ? WHERE user_id = ? AND category_id = ?",
            (fallback_id, user_id, category_id),
        )
        await self.conn.execute(
            "DELETE FROM limits WHERE user_id = ? AND category_id = ?", (user_id, category_id)
        )
        await self.conn.execute(
            "DELETE FROM categories WHERE user_id = ? AND id = ?", (user_id, category_id)
        )
        await self.conn.commit()
        return True

    async def add_keyword(self, user_id: int, category_id: int, keyword: str) -> None:
        """Привязывает слово к категории и снимает его с остальных категорий."""
        keyword = keyword.strip().lower()
        if not keyword:
            return
        for category in await self.list_categories(user_id):
            keywords = list(category.keywords)
            if category.id == category_id:
                if keyword in keywords:
                    continue
                keywords.append(keyword)
            elif keyword in keywords:
                keywords.remove(keyword)
            else:
                continue
            await self.conn.execute(
                "UPDATE categories SET keywords = ? WHERE user_id = ? AND id = ?",
                (_join_keywords(keywords), user_id, category.id),
            )
        await self.conn.commit()

    # ---------------------------------------------------------------- expenses

    async def add_expense(
        self,
        user_id: int,
        amount: int,
        note: str,
        spent_on: dt.date,
        category_id: Optional[int],
    ) -> Expense:
        cur = await self.conn.execute(
            "INSERT INTO expenses (user_id, amount, note, spent_on, category_id)"
            " VALUES (?, ?, ?, ?, ?)",
            (user_id, amount, note.strip(), spent_on.isoformat(), category_id),
        )
        await self.conn.commit()
        expense = await self.get_expense(user_id, cur.lastrowid)
        assert expense is not None
        return expense

    async def get_expense(self, user_id: int, expense_id: int) -> Optional[Expense]:
        cur = await self.conn.execute(
            _EXPENSE_SELECT + " WHERE e.user_id = ? AND e.id = ?", (user_id, expense_id)
        )
        row = await cur.fetchone()
        return _row_to_expense(row) if row else None

    async def last_expenses(self, user_id: int, limit: int = 10) -> list[Expense]:
        cur = await self.conn.execute(
            _EXPENSE_SELECT + " WHERE e.user_id = ? ORDER BY e.id DESC LIMIT ?",
            (user_id, limit),
        )
        return [_row_to_expense(row) for row in await cur.fetchall()]

    async def delete_expense(self, user_id: int, expense_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM expenses WHERE user_id = ? AND id = ?", (user_id, expense_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def set_expense_category(
        self, user_id: int, expense_id: int, category_id: int
    ) -> Optional[Expense]:
        cur = await self.conn.execute(
            "UPDATE expenses SET category_id = ? WHERE user_id = ? AND id = ?",
            (category_id, user_id, expense_id),
        )
        await self.conn.commit()
        if cur.rowcount == 0:
            return None
        return await self.get_expense(user_id, expense_id)

    async def expenses_between(
        self, user_id: int, start: dt.date, end: dt.date
    ) -> list[Expense]:
        cur = await self.conn.execute(
            _EXPENSE_SELECT
            + " WHERE e.user_id = ? AND e.spent_on BETWEEN ? AND ?"
            " ORDER BY e.spent_on, e.id",
            (user_id, start.isoformat(), end.isoformat()),
        )
        return [_row_to_expense(row) for row in await cur.fetchall()]

    # ----------------------------------------------------------------- отчёты

    async def totals_by_category(
        self, user_id: int, start: dt.date, end: dt.date
    ) -> list[CategoryTotal]:
        cur = await self.conn.execute(
            """
            SELECT e.category_id AS category_id,
                   COALESCE(c.name, 'Без категории') AS name,
                   COALESCE(c.emoji, '❔') AS emoji,
                   SUM(e.amount) AS total,
                   COUNT(*) AS cnt
            FROM expenses e
            LEFT JOIN categories c ON c.id = e.category_id
            WHERE e.user_id = ? AND e.spent_on BETWEEN ? AND ?
            GROUP BY e.category_id
            ORDER BY total DESC
            """,
            (user_id, start.isoformat(), end.isoformat()),
        )
        return [
            CategoryTotal(
                category_id=row["category_id"],
                name=row["name"],
                emoji=row["emoji"],
                total=row["total"],
                count=row["cnt"],
            )
            for row in await cur.fetchall()
        ]

    async def total_between(self, user_id: int, start: dt.date, end: dt.date) -> tuple[int, int]:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS cnt FROM expenses"
            " WHERE user_id = ? AND spent_on BETWEEN ? AND ?",
            (user_id, start.isoformat(), end.isoformat()),
        )
        row = await cur.fetchone()
        return row["total"], row["cnt"]

    async def category_total_between(
        self, user_id: int, category_id: int, start: dt.date, end: dt.date
    ) -> int:
        cur = await self.conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses"
            " WHERE user_id = ? AND category_id = ? AND spent_on BETWEEN ? AND ?",
            (user_id, category_id, start.isoformat(), end.isoformat()),
        )
        row = await cur.fetchone()
        return row["total"]

    async def first_expense_date(self, user_id: int) -> Optional[dt.date]:
        cur = await self.conn.execute(
            "SELECT MIN(spent_on) AS first FROM expenses WHERE user_id = ?", (user_id,)
        )
        row = await cur.fetchone()
        return dt.date.fromisoformat(row["first"]) if row and row["first"] else None

    # ----------------------------------------------------------------- лимиты

    async def set_limit(self, user_id: int, category_id: int, amount: int) -> None:
        await self.conn.execute(
            "INSERT INTO limits (user_id, category_id, amount) VALUES (?, ?, ?)"
            " ON CONFLICT(user_id, category_id) DO UPDATE SET amount = excluded.amount",
            (user_id, category_id, amount),
        )
        await self.conn.commit()

    async def delete_limit(self, user_id: int, category_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM limits WHERE user_id = ? AND category_id = ?", (user_id, category_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def get_limit(self, user_id: int, category_id: int) -> Optional[int]:
        cur = await self.conn.execute(
            "SELECT amount FROM limits WHERE user_id = ? AND category_id = ?",
            (user_id, category_id),
        )
        row = await cur.fetchone()
        return row["amount"] if row else None

    async def list_limits(self, user_id: int) -> list[tuple[int, int]]:
        """Возвращает [(category_id, amount)], где 0 — общий лимит."""
        cur = await self.conn.execute(
            "SELECT category_id, amount FROM limits WHERE user_id = ? ORDER BY category_id",
            (user_id,),
        )
        return [(row["category_id"], row["amount"]) for row in await cur.fetchall()]
