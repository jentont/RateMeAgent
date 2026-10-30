"""Правила батла на призы: турнир недели, дуэли профилей, дуэли ников.

Деньги — внутренний баланс в звёздах (пополняется оплатой Stars). Со
всякого банка касса забирает HOUSE_PERCENT, остальное уходит победителям.
Вывод — подарками Telegram (payouts.py).

* **Турнир недели.** Взнос TOURNEY_FEE. Место — по баллу профиля из 10;
  балл можно обновлять не чаще раза в SCORE_TTL, если профиль подтянул.
  В конце недели банк делят верхние места по TOURNEY_SPLIT. Меньше двух
  участников — взносы возвращаются целиком.
* **Дуэль профилей.** Ставка из DUEL_STAKES. Заявка ждёт соперника с
  такой же ставкой; когда он находится, выше балл — забирает банк минус
  доля кассы. Ничья — обоим возврат. Свою заявку можно отозвать.
* **Дуэль ников.** То же, но меряются ники (оценка юзернейма из 10).
  Ставить можно только свой текущий @username — его подписывает Telegram
  в initData, чужой не подсунешь.

Балл замораживается в момент заявки: иначе можно было бы поставить с
одним профилем и до прихода соперника подменить аватарку.
"""

from __future__ import annotations

import calendar
import logging
import time
from dataclasses import dataclass

import config
import db

log = logging.getLogger("profilerate.battle")

KINDS = ("profile", "nick")


class Refused(Exception):
    """Так нельзя — текст для человека."""


def house_cut(bank: int) -> int:
    return bank * config.HOUSE_PERCENT // 100


def split(prize: int, places: int) -> list[int]:
    """Призы верхним местам. Доли перенормируются, если мест меньше."""
    shares = list(config.TOURNEY_SPLIT[:places])
    total = sum(shares) or 1
    parts = [prize * share // total for share in shares]
    if parts:
        # Остаток от деления — первому месту, чтобы банк расходился до звезды.
        parts[0] += prize - sum(parts)
    return parts


def next_end(at: float | None = None) -> int:
    """Ближайший конец недели: день TOURNEY_END_WEEKDAY, час TOURNEY_END_HOUR UTC."""
    at = at or time.time()
    day = time.gmtime(at)
    midnight = calendar.timegm((day.tm_year, day.tm_mon, day.tm_mday, 0, 0, 0))
    ahead = (config.TOURNEY_END_WEEKDAY - day.tm_wday) % 7
    end = midnight + ahead * 86400 + config.TOURNEY_END_HOUR * 3600
    if end <= at + 3600:
        end += 7 * 86400
    return end


# --------------------------------------------------------------------------
# Турнир
# --------------------------------------------------------------------------


async def current_tourney() -> db.Tourney:
    found = await db.open_tourney()
    if found is None:
        found = await db.new_tourney(next_end(), config.TOURNEY_FEE)
        log.info("открыт турнир #%s до %s", found.id, found.ends)
    return found


async def join_tourney(user_id: int, score: float) -> db.Tourney:
    async with db.money_lock:
        t = await current_tourney()
        if t.ends <= db.now():
            raise Refused("Турнир уже подводит итоги — подожди пару минут.")
        if await db.entry(t.id, user_id):
            raise Refused("Ты уже в турнире этой недели.")
        try:
            await db.move(user_id, -t.fee, "tourney", f"tourney:{t.id}:{user_id}",
                          f"взнос в турнир #{t.id}", commit=False)
        except db.NotEnough:
            await db.conn().rollback()
            raise Refused(f"Нужно {t.fee}⭐ на балансе — пополни кошелёк.") from None
        await db.put_entry(t.id, user_id, score, commit=False)
        await db.conn().commit()
    return t


async def refresh_entry(user_id: int, score: float) -> float:
    """Обновить балл в турнире. Возвращает тот, что теперь в таблице."""
    t = await current_tourney()
    row = await db.entry(t.id, user_id)
    if row is None:
        raise Refused("Сначала вступи в турнир.")
    left = config.SCORE_TTL - (db.now() - int(row["updated"]))
    if left > 0:
        raise Refused(f"Обновить балл можно через {left // 60 + 1} мин.")
    await db.put_entry(t.id, user_id, score)
    return score


async def tourney_view(user_id: int, offset: int = 0, limit: int = 8) -> dict:
    t = await current_tourney()
    count = await db.entrants(t.id)
    bank = count * t.fee
    prize = bank - house_cut(bank)
    table = await db.standings(t.id)
    place = next((i + 1 for i, row in enumerate(table) if row["user_id"] == user_id), None)
    mine = await db.entry(t.id, user_id)
    return {
        "id": t.id,
        "ends": t.ends,
        "fee": t.fee,
        "entrants": count,
        "bank": bank,
        "prize": prize,
        "prizes": split(prize, len(config.TOURNEY_SPLIT)),
        "joined": mine is not None,
        "place": place,
        "score": float(mine["score"]) if mine else None,
        "refresh_in": max(0, config.SCORE_TTL - (db.now() - int(mine["updated"]))) if mine else 0,
        "total_rows": len(table),
        "rows": [
            {
                "place": offset + i + 1,
                "name": row["name"] or "Игрок",
                "username": row["username"],
                "score": round(float(row["score"]), 2),
                "me": row["user_id"] == user_id,
            }
            for i, row in enumerate(table[offset:offset + limit])
        ],
    }


@dataclass(slots=True)
class Award:
    user_id: int
    place: int
    stars: int


async def settle_tourney(t: db.Tourney, rescore=None) -> list[Award]:
    """Подвести итоги: выплатить призы на баланс или вернуть взносы.

    Каждая выплата — запись с уникальным ref, поэтому перезапуск посреди
    подведения итогов ничего не выплатит дважды.

    `rescore(user_id) -> float | None` — живой балл. Им проверяются
    претенденты на призы: в таблице стоит балл на момент последнего
    обновления, и без проверки можно было бы набрать его с чужими
    подарками и вернуть их до конца недели. В зачёт идёт меньшее.
    """
    if rescore is not None:
        # Проверяем, пока все призовые места не заняты проверенными: если
        # чей-то балл упал, наверх поднимается следующий — проверяем и его.
        checked: set[int] = set()
        while True:
            top = await db.standings(t.id, limit=len(config.TOURNEY_SPLIT))
            fresh = [row for row in top if row["user_id"] not in checked]
            if not fresh:
                break
            for row in fresh:
                checked.add(row["user_id"])
                now = await rescore(row["user_id"])
                if now is not None and now < float(row["score"]):
                    await db.conn().execute(
                        "UPDATE entries SET score = ? WHERE tourney_id = ? AND user_id = ?",
                        (now, t.id, row["user_id"]),
                    )
            await db.conn().commit()
    async with db.money_lock:
        table = await db.standings(t.id)
        awards: list[Award] = []
        if len(table) < 2:
            for row in table:
                await db.move(row["user_id"], t.fee, "refund", f"tourney-refund:{t.id}:{row['user_id']}",
                              f"возврат: в турнире #{t.id} не набралось соперников", commit=False)
        else:
            bank = len(table) * t.fee
            cut = house_cut(bank)
            await db.move(db.HOUSE, cut, "house", f"tourney-house:{t.id}", f"турнир #{t.id}", commit=False)
            places = min(len(table) - 1, len(config.TOURNEY_SPLIT)) or 1
            for place, stars in enumerate(split(bank - cut, places), start=1):
                row = table[place - 1]
                await db.move(row["user_id"], stars, "prize", f"tourney-prize:{t.id}:{place}",
                              f"{place} место в турнире #{t.id}", commit=False)
                awards.append(Award(row["user_id"], place, stars))
        await db.conn().commit()
        await db.set_tourney_status(t.id, "done")
    return awards


async def due_tourney() -> db.Tourney | None:
    """Открытый турнир, у которого вышло время."""
    t = await db.open_tourney()
    return t if t and t.ends <= db.now() else None


# --------------------------------------------------------------------------
# Дуэли
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Outcome:
    duel_id: int
    matched: bool
    #: Когда соперник нашёлся: кто победил (None — ничья) и сколько забрал.
    winner: int | None = None
    prize: int = 0
    opponent_id: int | None = None
    my_score: float = 0.0
    their_score: float = 0.0
    their_label: str = ""


async def join_duel(kind: str, stake: int, user_id: int, score: float, label: str) -> Outcome:
    if kind not in KINDS:
        raise Refused("Нет такого батла.")
    stakes = config.DUEL_STAKES if kind == "profile" else config.NICK_STAKES
    if stake not in stakes:
        raise Refused("Такой ставки нет.")
    async with db.money_lock:
        if len(await db.my_waiting(user_id)) >= config.MAX_WAITING:
            raise Refused("У тебя уже висят заявки — дождись соперника или отзови одну.")
        rival = await db.waiting_duel(kind, stake, user_id)
        try:
            if rival is None:
                cur = await db.conn().execute(
                    """INSERT INTO duels (kind, stake, a_id, a_score, a_label, created)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (kind, stake, user_id, score, label, db.now()),
                )
                await db.move(user_id, -stake, "duel", f"duel:{cur.lastrowid}:a",
                              f"ставка в дуэли #{cur.lastrowid}", commit=False)
                await db.conn().commit()
                return Outcome(cur.lastrowid, matched=False)
            await db.move(user_id, -stake, "duel", f"duel:{rival['id']}:b",
                          f"ставка в дуэли #{rival['id']}", commit=False)
        except db.NotEnough:
            await db.conn().rollback()
            raise Refused(f"Нужно {stake}⭐ на балансе — пополни кошелёк.") from None

        duel_id, a_id, a_score = rival["id"], rival["a_id"], float(rival["a_score"])
        bank = stake * 2
        if abs(a_score - score) < 0.005:
            winner, prize = None, 0
            await db.move(a_id, stake, "refund", f"duel-refund:{duel_id}:a", f"ничья в дуэли #{duel_id}", commit=False)
            await db.move(user_id, stake, "refund", f"duel-refund:{duel_id}:b", f"ничья в дуэли #{duel_id}", commit=False)
        else:
            winner = a_id if a_score > score else user_id
            cut = house_cut(bank)
            prize = bank - cut
            await db.move(db.HOUSE, cut, "house", f"duel-house:{duel_id}", f"дуэль #{duel_id}", commit=False)
            await db.move(winner, prize, "prize", f"duel-prize:{duel_id}", f"победа в дуэли #{duel_id}", commit=False)
        await db.conn().execute(
            """UPDATE duels SET b_id = ?, b_score = ?, b_label = ?, winner = ?, status = 'done',
               finished = ? WHERE id = ?""",
            (user_id, score, label, winner, db.now(), duel_id),
        )
        await db.conn().commit()
    return Outcome(
        duel_id, matched=True, winner=winner, prize=prize, opponent_id=a_id,
        my_score=score, their_score=a_score, their_label=rival["a_label"],
    )


async def cancel_duel(duel_id: int, user_id: int) -> None:
    async with db.money_lock:
        row = await db.duel(duel_id)
        if row is None or row["a_id"] != user_id or row["status"] != "waiting":
            raise Refused("Эту заявку уже не отозвать — соперник нашёлся.")
        await db.conn().execute("UPDATE duels SET status = 'cancelled' WHERE id = ?", (duel_id,))
        await db.move(user_id, int(row["stake"]), "refund", f"duel-cancel:{duel_id}",
                      f"заявка #{duel_id} отозвана", commit=False)
        await db.conn().commit()


async def expire_duels() -> list:
    """Вернуть ставки по заявкам, которые ждали дольше DUEL_TTL."""
    edge = db.now() - config.DUEL_TTL
    async with db.conn().execute(
        "SELECT * FROM duels WHERE status = 'waiting' AND created < ?", (edge,)
    ) as cur:
        stale = list(await cur.fetchall())
    for row in stale:
        try:
            await cancel_duel(row["id"], row["a_id"])
        except Refused:
            continue
    return stale


def duel_view(row: dict, user_id: int) -> dict:
    mine_a = row["a_id"] == user_id
    return {
        "id": row["id"],
        "kind": row["kind"],
        "stake": row["stake"],
        "my_score": row["a_score"] if mine_a else row["b_score"],
        "their_score": row["b_score"] if mine_a else row["a_score"],
        "my_label": row["a_label"] if mine_a else row["b_label"],
        "their_label": row["b_label"] if mine_a else row["a_label"],
        "result": "draw" if row["winner"] is None else ("win" if row["winner"] == user_id else "lose"),
        "prize": (row["stake"] * 2 - house_cut(row["stake"] * 2)) if row["winner"] == user_id else 0,
        "at": row["finished"],
    }
