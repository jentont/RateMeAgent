"""База батла: баланс, журнал денег, турниры, дуэли, выводы.

Деньги меняются только через `move()`: каждая запись журнала несёт
уникальный `ref`, и повторный вызов с тем же ref ничего не делает. Так
одна оплата не зачислится дважды, если Telegram пришлёт апдейт ещё раз,
а один турнир не выплатится дважды после перезапуска посреди выплаты.

Пользователь 0 — касса: туда идёт доля бота с каждого банка.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import aiosqlite

import config

HOUSE = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT,
    name TEXT NOT NULL DEFAULT '',
    balance INTEGER NOT NULL DEFAULT 0,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL UNIQUE,
    note TEXT NOT NULL DEFAULT '',
    at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id, id);
CREATE TABLE IF NOT EXISTS tourneys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    starts INTEGER NOT NULL,
    ends INTEGER NOT NULL,
    fee INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open'
);
CREATE TABLE IF NOT EXISTS entries (
    tourney_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    score REAL NOT NULL,
    updated INTEGER NOT NULL,
    PRIMARY KEY (tourney_id, user_id)
);
CREATE TABLE IF NOT EXISTS duels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    stake INTEGER NOT NULL,
    a_id INTEGER NOT NULL,
    a_score REAL NOT NULL,
    a_label TEXT NOT NULL,
    b_id INTEGER,
    b_score REAL,
    b_label TEXT,
    winner INTEGER,
    status TEXT NOT NULL DEFAULT 'waiting',
    created INTEGER NOT NULL,
    finished INTEGER
);
CREATE INDEX IF NOT EXISTS duels_wait ON duels(status, kind, stake);
CREATE TABLE IF NOT EXISTS withdrawals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    sent INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'pending',
    at INTEGER NOT NULL
);
"""

_db: aiosqlite.Connection | None = None
#: Все денежные операции идут по очереди: проверка баланса и списание
#: должны быть одним шагом, иначе два быстрых нажатия спишут дважды.
money_lock = asyncio.Lock()


def now() -> int:
    return int(time.time())


async def init(path=None) -> None:
    global _db
    _db = await aiosqlite.connect(path or config.DB_PATH)
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _db.execute(
        "INSERT OR IGNORE INTO users (id, name, created) VALUES (?, 'касса', ?)", (HOUSE, now())
    )
    await _db.commit()


async def close() -> None:
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def conn() -> aiosqlite.Connection:
    assert _db is not None, "db.init() не вызван"
    return _db


# --------------------------------------------------------------------------
# Люди и деньги
# --------------------------------------------------------------------------


async def touch(user_id: int, username: str | None, name: str) -> None:
    await conn().execute(
        """INSERT INTO users (id, username, name, created) VALUES (?, ?, ?, ?)
           ON CONFLICT(id) DO UPDATE SET username = excluded.username, name = excluded.name""",
        (user_id, username, name or "", now()),
    )
    await conn().commit()


async def balance(user_id: int) -> int:
    async with conn().execute("SELECT balance FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return int(row["balance"]) if row else 0


async def user(user_id: int) -> aiosqlite.Row | None:
    async with conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
        return await cur.fetchone()


class NotEnough(Exception):
    """Списание больше баланса."""


async def move(user_id: int, delta: int, kind: str, ref: str, note: str = "",
               commit: bool = True) -> bool:
    """Изменить баланс с записью в журнал. False — этот ref уже был.

    Зовётся под `money_lock`. Уйти в минус нельзя никому, кроме кассы
    тоже нельзя: если касса не покрывает выплату, это ошибка в расчёте,
    и лучше упасть, чем раздать звёзды, которых нет.
    """
    async with conn().execute("SELECT 1 FROM ledger WHERE ref = ?", (ref,)) as cur:
        if await cur.fetchone():
            return False
    if delta < 0 and await balance(user_id) + delta < 0:
        raise NotEnough(ref)
    await conn().execute(
        "INSERT OR IGNORE INTO users (id, name, created) VALUES (?, '', ?)", (user_id, now())
    )
    await conn().execute(
        "INSERT INTO ledger (user_id, delta, kind, ref, note, at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, delta, kind, ref, note, now()),
    )
    await conn().execute("UPDATE users SET balance = balance + ? WHERE id = ?", (delta, user_id))
    if commit:
        await conn().commit()
    return True


async def history(user_id: int, offset: int = 0, limit: int = 6) -> tuple[list[dict], int]:
    async with conn().execute(
        "SELECT COUNT(*) AS n FROM ledger WHERE user_id = ?", (user_id,)
    ) as cur:
        total = int((await cur.fetchone())["n"])
    async with conn().execute(
        "SELECT delta, kind, note, at FROM ledger WHERE user_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
        (user_id, limit, offset),
    ) as cur:
        rows = [dict(row) for row in await cur.fetchall()]
    return rows, total


# --------------------------------------------------------------------------
# Турнир
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Tourney:
    id: int
    starts: int
    ends: int
    fee: int
    status: str


def _tourney(row) -> Tourney:
    return Tourney(row["id"], row["starts"], row["ends"], row["fee"], row["status"])


async def open_tourney() -> Tourney | None:
    async with conn().execute(
        "SELECT * FROM tourneys WHERE status = 'open' ORDER BY id DESC LIMIT 1"
    ) as cur:
        row = await cur.fetchone()
    return _tourney(row) if row else None


async def new_tourney(ends: int, fee: int) -> Tourney:
    cur = await conn().execute(
        "INSERT INTO tourneys (starts, ends, fee) VALUES (?, ?, ?)", (now(), ends, fee)
    )
    await conn().commit()
    return Tourney(cur.lastrowid, now(), ends, fee, "open")


async def set_tourney_status(tourney_id: int, status: str) -> None:
    await conn().execute("UPDATE tourneys SET status = ? WHERE id = ?", (status, tourney_id))
    await conn().commit()


async def entry(tourney_id: int, user_id: int) -> aiosqlite.Row | None:
    async with conn().execute(
        "SELECT * FROM entries WHERE tourney_id = ? AND user_id = ?", (tourney_id, user_id)
    ) as cur:
        return await cur.fetchone()


async def put_entry(tourney_id: int, user_id: int, score: float, commit: bool = True) -> None:
    await conn().execute(
        """INSERT INTO entries (tourney_id, user_id, score, updated) VALUES (?, ?, ?, ?)
           ON CONFLICT(tourney_id, user_id) DO UPDATE SET score = excluded.score,
           updated = excluded.updated""",
        (tourney_id, user_id, score, now()),
    )
    if commit:
        await conn().commit()


async def standings(tourney_id: int, offset: int = 0, limit: int = 1000) -> list[dict]:
    """Таблица: балл сверху вниз, при равенстве — кто раньше набрал."""
    async with conn().execute(
        """SELECT e.user_id, e.score, e.updated, u.username, u.name FROM entries e
           JOIN users u ON u.id = e.user_id WHERE e.tourney_id = ?
           ORDER BY e.score DESC, e.updated ASC LIMIT ? OFFSET ?""",
        (tourney_id, limit, offset),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def entrants(tourney_id: int) -> int:
    async with conn().execute(
        "SELECT COUNT(*) AS n FROM entries WHERE tourney_id = ?", (tourney_id,)
    ) as cur:
        return int((await cur.fetchone())["n"])


# --------------------------------------------------------------------------
# Дуэли
# --------------------------------------------------------------------------


async def waiting_duel(kind: str, stake: int, not_user: int) -> aiosqlite.Row | None:
    """Самая старая заявка с такой ставкой — не своя."""
    async with conn().execute(
        """SELECT * FROM duels WHERE status = 'waiting' AND kind = ? AND stake = ?
           AND a_id != ? ORDER BY id LIMIT 1""",
        (kind, stake, not_user),
    ) as cur:
        return await cur.fetchone()


async def my_waiting(user_id: int) -> list[aiosqlite.Row]:
    async with conn().execute(
        "SELECT * FROM duels WHERE status = 'waiting' AND a_id = ? ORDER BY id", (user_id,)
    ) as cur:
        return list(await cur.fetchall())


async def duel(duel_id: int) -> aiosqlite.Row | None:
    async with conn().execute("SELECT * FROM duels WHERE id = ?", (duel_id,)) as cur:
        return await cur.fetchone()


async def queue_sizes(kind: str, not_user: int = 0) -> dict[int, int]:
    """Сколько чужих заявок ждёт по каждой ставке — свои не считаются:
    с ними бой не начнётся."""
    async with conn().execute(
        """SELECT stake, COUNT(*) AS n FROM duels WHERE status = 'waiting' AND kind = ?
           AND a_id != ? GROUP BY stake""",
        (kind, not_user),
    ) as cur:
        return {int(row["stake"]): int(row["n"]) for row in await cur.fetchall()}


async def recent_duels(user_id: int, kind: str, offset: int = 0,
                       limit: int = 5) -> tuple[list[dict], int]:
    where = "status = 'done' AND kind = ? AND (a_id = ? OR b_id = ?)"
    args = (kind, user_id, user_id)
    async with conn().execute(f"SELECT COUNT(*) AS n FROM duels WHERE {where}", args) as cur:
        total = int((await cur.fetchone())["n"])
    async with conn().execute(
        f"SELECT * FROM duels WHERE {where} ORDER BY finished DESC, id DESC LIMIT ? OFFSET ?",
        (*args, limit, offset),
    ) as cur:
        return [dict(row) for row in await cur.fetchall()], total


# --------------------------------------------------------------------------
# Выводы
# --------------------------------------------------------------------------


async def new_withdrawal(user_id: int, amount: int) -> int:
    cur = await conn().execute(
        "INSERT INTO withdrawals (user_id, amount, at) VALUES (?, ?, ?)", (user_id, amount, now())
    )
    return cur.lastrowid


async def finish_withdrawal(withdrawal_id: int, sent: int, status: str) -> None:
    await conn().execute(
        "UPDATE withdrawals SET sent = ?, status = ? WHERE id = ?", (sent, status, withdrawal_id)
    )
    await conn().commit()


async def stats() -> dict:
    """Для /bank: сколько людей, сколько в кассе, сколько на руках."""
    out = {}
    for key, sql in (
        ("users", "SELECT COUNT(*) AS n FROM users WHERE id != 0"),
        ("house", "SELECT balance AS n FROM users WHERE id = 0"),
        ("held", "SELECT COALESCE(SUM(balance), 0) AS n FROM users WHERE id != 0"),
        ("paid_in", "SELECT COALESCE(SUM(delta), 0) AS n FROM ledger WHERE kind = 'topup'"),
        ("paid_out", "SELECT COALESCE(-SUM(delta), 0) AS n FROM ledger WHERE kind = 'withdraw'"),
        ("duels", "SELECT COUNT(*) AS n FROM duels WHERE status = 'done'"),
    ):
        async with conn().execute(sql) as cur:
            row = await cur.fetchone()
            out[key] = int(row["n"]) if row else 0
    return out
