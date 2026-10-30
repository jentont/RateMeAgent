"""Битвы в чате: два профиля или два ника — одна таблица, победитель сверху.

Тексты собираются здесь, а не в main.py: их удобно проверять без
Telegram, и вид таблицы правится в одном месте для обеих битв.
"""

from __future__ import annotations

import html
import re

import usernames
from scoring import METRICS, Score

#: «@a @b», «a vs b», «a против b», «a, b» — инлайн-запрос на битву ников.
#: Разделитель обязателен: иначе «storm» разбивалось бы на два ника.
VS_RE = re.compile(
    r"^@?([a-z0-9_]{1,32})(?:\s*(?:vs\.?|против|,|×)\s*|\s+)@?([a-z0-9_]{1,32})$", re.I
)

#: Ширина колонки имени в таблице: дальше моноширинный блок переносится.
NAME = 12


def _cut(text: str, size: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= size else text[: size - 1] + "…"


def _table(rows: list[tuple[str, float | None, float | None]], left: str, right: str) -> str:
    """Метрика | левый | правый, стрелка — у кого строка выше."""
    width = max(len(title) for title, _, _ in rows)
    head = f"{''.ljust(width)}  {_cut(left, 6).rjust(6)}   {_cut(right, 6).rjust(6)}"
    lines = [head]
    for title, a, b in rows:
        fa = "—" if a is None else f"{a:.1f}"
        fb = "—" if b is None else f"{b:.1f}"
        mark = " "
        if a is not None and b is not None and abs(a - b) >= 0.05:
            mark = "◀" if a > b else "▶"
        lines.append(f"{title.ljust(width)}  {fa.rjust(6)} {mark} {fb.rjust(6)}")
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


def _verdict(left: str, right: str, a: float, b: float) -> str:
    if abs(a - b) < 0.005:
        return f"🤝 <b>Ничья</b> — {html.escape(left)} {a:.2f} : {b:.2f} {html.escape(right)}"
    win, lose, hi, lo = (left, right, a, b) if a > b else (right, left, b, a)
    return f"🏆 <b>{html.escape(win)}</b> — {hi:.2f} против {lo:.2f} у {html.escape(lose)}"


def profiles(left_name: str, a: Score, right_name: str, b: Score) -> str:
    """Итог битвы профилей. Непрочитанное у одного — прочерк, в итог не идёт."""
    rows = [
        (
            title,
            None if key in a.profile.guessed else a.parts[key],
            None if key in b.profile.guessed else b.parts[key],
        )
        for key, title in METRICS
    ]
    return (
        "⚔️ <b>Битва профилей</b>\n"
        f"{_verdict(_cut(left_name, NAME), _cut(right_name, NAME), a.total, b.total)}\n"
        f"{_table(rows, left_name, right_name)}"
    )


def names(a: usernames.Rating, b: usernames.Rating) -> str:
    """Итог битвы ников: общий балл сверху, ниже шесть признаков."""
    rows = [(ra.title, ra.value, rb.value) for ra, rb in zip(a.rows, b.rows)]
    left, right = "@" + a.username, "@" + b.username
    return (
        "⚔️ <b>Битва ников</b>\n"
        f"{_verdict(left, right, a.total, b.total)}\n"
        f"{_table(rows, left, right)}"
    )


def profile_call(name: str) -> str:
    return (
        f"⚔️ <b>{html.escape(_cut(name, 24))}</b> вызывает на битву профилей!\n"
        "<blockquote>Чей профиль выше по 10-балльной шкале? Жми кнопку — "
        "сравню по семи метрикам.</blockquote>"
    )


def name_call(username: str) -> str:
    return (
        f"⚔️ <b>@{html.escape(username)}</b> вызывает на битву ников!\n"
        "<blockquote>Чей юзернейм круче? Жми кнопку — сравню твой @ник "
        "с этим по шести признакам.</blockquote>"
    )
