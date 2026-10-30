"""Деньги снаружи: пополнение звёздами и вывод подарками Telegram.

Пополнение — счёт в XTR: мини-апп открывает его через openInvoice, звёзды
падают на баланс бота, а человеку — на внутренний баланс батла.

Вывод — подарки: бот покупает их со своего баланса звёзд и шлёт
человеку. Подарок можно оставить в профиле или обменять на звёзды.
Набираем сумму самыми дорогими подарками, какие влезают: 140⭐ — это
100 + 25 + 15, а остаток, на который подарка нет, остаётся на балансе.
"""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import LabeledPrice

import config
import db

log = logging.getLogger("profilerate.payouts")

TOPUP_PREFIX = "topup:"


class Refused(Exception):
    """Вывод сейчас невозможен — текст для человека."""


async def invoice_link(bot: Bot, user_id: int, amount: int) -> str:
    if amount not in config.TOPUP_AMOUNTS:
        raise Refused("Такой суммы пополнения нет.")
    return await bot.create_invoice_link(
        title=f"Пополнение на {amount}⭐",
        description="Звёзды на баланс батла: турнир, дуэли профилей и ников.",
        payload=f"{TOPUP_PREFIX}{user_id}:{amount}",
        currency="XTR",
        prices=[LabeledPrice(label="Пополнение", amount=amount)],
    )


async def credit_topup(user_id: int, amount: int, charge_id: str) -> bool:
    """Зачислить оплату. False — эта оплата уже зачислена."""
    async with db.money_lock:
        return await db.move(user_id, amount, "topup", f"topup:{charge_id}", "пополнение звёздами")


def plan(amount: int, prices: list[int]) -> list[int]:
    """Какими подарками набрать сумму: жадно, от дорогих к дешёвым."""
    out: list[int] = []
    left = amount
    for price in sorted(set(prices), reverse=True):
        while price <= left:
            out.append(price)
            left -= price
    return out


async def _catalogue(bot: Bot) -> dict[int, str]:
    """Цена → id обычного подарка, который ещё можно купить."""
    gifts = await bot.get_available_gifts()
    out: dict[int, str] = {}
    for gift in gifts.gifts:
        if gift.remaining_count == 0 or gift.publisher_chat is not None:
            continue
        # Из подарков одной цены берём любой: для вывода важна сумма.
        out.setdefault(int(gift.star_count), gift.id)
    return out


async def withdraw(bot: Bot, user_id: int, amount: int) -> tuple[int, list[int]]:
    """Вывести подарками. Возвращает (сколько ушло, цены подарков)."""
    if amount < config.WITHDRAW_MIN:
        raise Refused(f"Вывести можно от {config.WITHDRAW_MIN}⭐.")
    catalogue = await _catalogue(bot)
    gifts = plan(amount, list(catalogue))
    total = sum(gifts)
    if not gifts:
        raise Refused("На эту сумму подарка нет — добавь звёзд.")

    async with db.money_lock:
        if await db.balance(user_id) < total:
            raise Refused("На балансе меньше, чем хочешь вывести.")
        stars = (await bot.get_my_star_balance()).amount
        # Звёзды на балансе бота должны покрывать всё, что люди держат во
        # внутреннем балансе, кроме кассы. Если не покрывают — значит,
        # владелец вывел лишнее; людям об этом знать не надо, админу — да.
        if stars < total:
            log.error("на балансе бота %s⭐, вывод %s⭐ не покрыть", stars, total)
            raise Refused("Вывод временно недоступен — попробуй через час.")
        withdrawal = await db.new_withdrawal(user_id, total)
        await db.move(user_id, -total, "withdraw", f"withdraw:{withdrawal}",
                      f"вывод подарками ({len(gifts)} шт.)", commit=False)
        await db.conn().commit()

    sent = 0
    for price in gifts:
        try:
            await bot.send_gift(
                gift_id=catalogue[price], user_id=user_id, text="Выигрыш в батле профилей"
            )
            sent += price
        except Exception as error:
            log.warning("подарок %s⭐ для %s не ушёл: %s", price, user_id, error)
            break
    if sent < total:
        async with db.money_lock:
            await db.move(user_id, total - sent, "refund", f"withdraw-back:{withdrawal}",
                          "часть вывода не ушла — вернули на баланс")
    await db.finish_withdrawal(withdrawal, sent, "done" if sent == total else "partial")
    return sent, _sent_part(gifts, sent)


def _sent_part(gifts: list[int], sent: int) -> list[int]:
    out, acc = [], 0
    for price in gifts:
        if acc + price > sent:
            break
        out.append(price)
        acc += price
    return out
