"""PNG-графики расходов. Требуют matplotlib; без него бот работает без картинок.

Все графики — одна серия данных, поэтому цвет один на все столбцы: подкрашивать
столбик темнее оттого, что он выше, значит второй раз кодировать ту же величину
и сжечь единственный свободный канал. Сравнение с фоном (среднее) — отдельная
пунктирная линия, чтобы её нельзя было спутать с данными.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import io
from typing import Sequence

from .formatting import MONTHS_NOMINATIVE, format_money

#: Палитра проверена валидатором на светлой подложке (контраст ≥ 3:1).
COLOR_SERIES = "#2a78d6"
COLOR_SURFACE = "#fcfcfb"
COLOR_INK = "#0b0b0b"
COLOR_MUTED = "#52514e"
COLOR_GRID = "#e6e5e1"

MONTHS_SHORT = (
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
)

FIGSIZE = (9.0, 5.0)
DPI = 150


class ChartsUnavailable(RuntimeError):
    """matplotlib не установлен."""


def available() -> bool:
    return importlib.util.find_spec("matplotlib") is not None


def _pyplot():
    if not available():
        raise ChartsUnavailable("matplotlib не установлен")
    import matplotlib

    matplotlib.use("Agg")  # без оконной подсистемы: рисуем в файл
    import matplotlib.pyplot as plt

    return plt


def _major(minor: int) -> float:
    return minor / 100


def _style(ax, currency: str, horizontal: bool = False) -> None:
    """Общий вид: тонкие волосяные линии, ненавязчивая сетка, без рамки."""
    from matplotlib.ticker import FuncFormatter

    for side in ("top", "right", "left" if not horizontal else "bottom"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(COLOR_GRID)
        ax.spines[side].set_linewidth(1)

    axis = "x" if horizontal else "y"
    ax.grid(axis=axis, color=COLOR_GRID, linewidth=1, zorder=0)
    ax.set_axisbelow(True)
    ax.tick_params(colors=COLOR_MUTED, labelsize=10, length=0)

    def money(value, _pos):
        if value >= 1000:
            return f"{value / 1000:,.0f} тыс".replace(",", " ")
        return f"{value:,.0f}".replace(",", " ")

    getattr(ax, f"{axis}axis").set_major_formatter(FuncFormatter(money))


def _finish(plt, fig, title: str, subtitle: str = "") -> bytes:
    fig.text(0.02, 0.955, title, color=COLOR_INK, fontsize=14, fontweight="bold", va="top")
    top = 0.93
    if subtitle:
        fig.text(0.02, 0.895, subtitle, color=COLOR_MUTED, fontsize=10, va="top")
        top = 0.88
    fig.tight_layout(rect=(0, 0, 1, top))
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=DPI, facecolor=COLOR_SURFACE)
    plt.close(fig)
    return buffer.getvalue()


def _average_line(ax, values: Sequence[float]) -> float:
    """Пунктир — заведомо не данные, а ориентир. Подпись уходит в подзаголовок:
    на самом графике она неизбежно налезает на столбцы."""
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    if average <= 0:
        return 0.0
    ax.axhline(average, color=COLOR_MUTED, linewidth=1.5, linestyle=(0, (4, 3)), zorder=3)
    return average


def _average_note(average: float, currency: str, unit: str) -> str:
    if average <= 0:
        return ""
    return f"пунктир — среднее {format_money(round(average) * 100, currency)} {unit}"


def _draw_daily(
    ax, days: Sequence[dt.date], amounts: Sequence[int], currency: str
) -> float:
    values = [_major(amount) for amount in amounts]
    # ширина < 1 оставляет зазор между столбцами — соседние заливки не слипаются
    ax.bar(range(len(days)), values, width=0.72, color=COLOR_SERIES, zorder=2)

    step = max(1, len(days) // 15)
    positions = list(range(0, len(days), step))
    ax.set_xticks(positions)
    ax.set_xticklabels([str(days[index].day) for index in positions])
    ax.set_xlim(-0.8, len(days) - 0.2)

    _style(ax, currency)
    return _average_line(ax, values)


def daily_chart(
    days: Sequence[dt.date], amounts: Sequence[int], currency: str, title: str
) -> bytes:
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)
    average = _draw_daily(ax, days, amounts, currency)
    return _finish(plt, fig, title, _average_note(average, currency, "в день"))


def _draw_categories(ax, items: Sequence[tuple[str, int]], currency: str) -> None:
    """Горизонтальные полосы: длинные названия категорий читаются, а не встают боком."""
    names = [name for name, _ in items][::-1]
    values = [_major(amount) for _, amount in items][::-1]

    ax.barh(range(len(names)), values, height=0.68, color=COLOR_SERIES, zorder=2)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(names, color=COLOR_INK, fontsize=11)

    span = max(values) if values else 1
    for index, value in enumerate(values):
        ax.annotate(
            format_money(round(value * 100), currency),
            xy=(value, index), xytext=(6, 0), textcoords="offset points",
            va="center", color=COLOR_MUTED, fontsize=10,
        )
    ax.set_xlim(0, span * 1.18)  # место для подписи справа от полосы

    _style(ax, currency, horizontal=True)
    # Каждая полоса подписана суммой — ось X и сетка стали бы повтором.
    ax.grid(visible=False)
    ax.set_xticks([])
    ax.spines["bottom"].set_visible(False)


def category_chart(items: Sequence[tuple[str, int]], currency: str, title: str) -> bytes:
    plt = _pyplot()
    height = max(3.0, 0.52 * len(items) + 1.6)
    fig, ax = plt.subplots(figsize=(FIGSIZE[0], height), facecolor=COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)
    _draw_categories(ax, items, currency)
    return _finish(plt, fig, title)


def monthly_chart(items: Sequence[tuple[str, int]], currency: str, title: str) -> bytes:
    plt = _pyplot()
    fig, ax = plt.subplots(figsize=FIGSIZE, facecolor=COLOR_SURFACE)
    ax.set_facecolor(COLOR_SURFACE)

    labels = []
    for month, _ in items:
        year, number = month.split("-")
        labels.append(f"{MONTHS_SHORT[int(number) - 1]}\n{year[2:]}")
    values = [_major(amount) for _, amount in items]

    ax.bar(range(len(items)), values, width=0.62, color=COLOR_SERIES, zorder=2)
    ax.set_xticks(range(len(items)))
    ax.set_xticklabels(labels)

    _style(ax, currency)
    average = _average_line(ax, values)
    return _finish(plt, fig, title, _average_note(average, currency, "в месяц"))


def month_name(month: str) -> str:
    """'2026-08' → 'август 2026'."""
    year, number = month.split("-")
    return f"{MONTHS_NOMINATIVE[int(number) - 1]} {year}"


# --------------------------------------------------------------------- PDF

#: Строк таблицы на страницу A4 выбранным кеглем.
TABLE_ROWS_PER_PAGE = 42
#: Сколько строк влезает под график на второй странице.
ROWS_UNDER_CHART = 22
A4 = (8.27, 11.69)


def _page(plt, title: str, subtitle: str = ""):
    page = plt.figure(figsize=A4, dpi=150, facecolor="white")
    page.text(0.07, 0.955, title, fontsize=16, fontweight="bold", va="top")
    if subtitle:
        page.text(0.07, 0.928, subtitle, fontsize=9, color=COLOR_MUTED, va="top")
    return page


def expense_pdf(
    title: str,
    subtitle: str,
    summary_lines: Sequence[str],
    categories: Sequence[tuple[str, int]],
    days: Sequence[dt.date],
    amounts: Sequence[int],
    table: Sequence[str],
    currency: str,
) -> bytes:
    """Отчёт A4: сводка с графиком по категориям, динамика по дням и таблица трат."""
    plt = _pyplot()
    from matplotlib.backends.backend_pdf import PdfPages

    buffer = io.BytesIO()
    with PdfPages(buffer) as pdf:
        page = _page(plt, title, subtitle)
        page.text(
            0.07, 0.90, "\n".join(summary_lines),
            fontsize=9, family="DejaVu Sans Mono", va="top", linespacing=1.5,
        )
        if categories:
            # график начинается там, где закончился текст: строк бывает от 6 до 20
            top = min(0.72, max(0.40, 0.88 - len(summary_lines) * 0.019))
            axes = page.add_axes((0.30, 0.09, 0.62, top - 0.09))
            axes.set_facecolor("white")
            _draw_categories(axes, categories, currency)
            page.text(
                0.07, top + 0.02, "По категориям",
                fontsize=11, fontweight="bold", va="bottom",
            )
        pdf.savefig(page)
        plt.close(page)

        rows = list(table)
        header, rest = rows[:2], rows[2:]
        done = 0

        # Страница с графиком по дням: под ним ещё остаётся место на часть таблицы,
        # иначе две трети листа ушли бы в пустоту.
        if len([amount for amount in amounts if amount]) >= 2:
            page = _page(plt, "Расходы по дням", subtitle)
            axes = page.add_axes((0.10, 0.60, 0.83, 0.28))
            axes.set_facecolor("white")
            average = _draw_daily(axes, days, amounts, currency)
            note = _average_note(average, currency, "в день")
            if note:
                page.text(0.07, 0.915, note, fontsize=9, color=COLOR_MUTED, va="top")

            chunk = rest[:ROWS_UNDER_CHART]
            if chunk:
                page.text(
                    0.06, 0.53, f"Траты (1–{len(chunk)} из {len(rest)})",
                    fontsize=11, fontweight="bold", va="top",
                )
                page.text(
                    0.06, 0.495, "\n".join(header + chunk),
                    fontsize=7.5, family="DejaVu Sans Mono", va="top", linespacing=1.6,
                )
                done = len(chunk)
            pdf.savefig(page)
            plt.close(page)

        while done < len(rest):
            chunk = rest[done : done + TABLE_ROWS_PER_PAGE]
            page = _page(plt, f"Траты ({done + 1}–{done + len(chunk)} из {len(rest)})")
            page.text(
                0.06, 0.915, "\n".join(header + chunk),
                fontsize=7.5, family="DejaVu Sans Mono", va="top", linespacing=1.6,
            )
            pdf.savefig(page)
            plt.close(page)
            done += len(chunk)

    return buffer.getvalue()
