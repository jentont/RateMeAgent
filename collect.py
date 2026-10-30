"""Снимок профиля через обычный Bot API — без аккаунта-сборщика.

Мог Батлу для «о себе», подарков и уровня нужен был userbot. С Bot API
9.3 всё это отдаёт сам бот: getChat приносит описание и уровень
(`rating`), getUserGifts — выставленные подарки. Чего прочитать не
вышло, попадает в `guessed`: такая строка в таблице пишется «нет данных»
и в общий балл не идёт, вместо того чтобы тянуть его вниз.
"""

from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass

from aiogram import Bot

import config
import fragment
import photo
from scoring import Profile

log = logging.getLogger("profilerate.collect")

#: Аватарку берём не самую большую: на карточку она ложится в 200
#: пикселей, и качать ради этого исходник незачем.
AVATAR_TARGET = 640

#: Редкость признака NFT-подарка: всё, что выпадает чаще 20 %, — обычное.
COMMON_PERMILLE = 200.0
#: Как признак называется в подписи под строкой «Подарки».
KINDS = (("backdrop", "фон"), ("model", "модель"), ("symbol", "узор"))


@dataclass(slots=True)
class Haul:
    gifts: int = 0
    nft: int = 0
    stars: int = 0
    rare_name: str = ""
    rarity: float = 0.0


def rarity_score(permille: float) -> float:
    """Редкость 0..1 по логарифмической шкале — как в Мог Батле."""
    if permille <= 0:
        return 0.0
    scale = math.log10(COMMON_PERMILLE / permille) / math.log10(COMMON_PERMILLE)
    return max(0.0, min(1.0, scale))


def _rarest(unique) -> tuple[str, float]:
    """Самый редкий признак уникального подарка: «Чёрный (фон)» и 0..1."""
    best_name, best = "", 0.0
    for attr, kind in KINDS:
        item = getattr(unique, attr, None)
        permille = float(getattr(item, "rarity_per_mille", 0) or 0)
        score = rarity_score(permille)
        name = str(getattr(item, "name", "") or "").strip()
        if name and score > best:
            best_name, best = f"{name} ({kind})", score
    return best_name, best


async def gifts(bot: Bot, user_id: int) -> Haul | None:
    """Выставленные подарки. None — Telegram не отдал список."""
    haul = Haul()
    offset = ""
    for _ in range(config.GIFT_PAGES):
        try:
            page = await bot.get_user_gifts(user_id, offset=offset or None, limit=100)
        except Exception as error:
            log.info("подарки %s не прочитать: %s", user_id, error)
            return None if not haul.gifts else haul
        for owned in page.gifts:
            haul.gifts += 1
            if owned.type == "unique":
                haul.nft += 1
                name, rarity = _rarest(owned.gift)
                if rarity > haul.rarity:
                    haul.rare_name, haul.rarity = name, rarity
                haul.stars += int(config.NFT_STARS * (1 + config.RARITY_BOOST * rarity))
            else:
                haul.stars += int(
                    owned.gift.star_count or owned.convert_star_count or 0
                )
        offset = page.next_offset or ""
        if not offset:
            break
    return haul


async def _avatar(bot: Bot, user_id: int) -> tuple[int, bytes | None, bool]:
    """Сколько фото, байты одного и удалось ли вообще посмотреть."""
    try:
        photos = await bot.get_user_profile_photos(user_id, limit=1)
    except Exception as error:
        log.info("фото %s недоступны: %s", user_id, error)
        return 0, None, False
    total = int(photos.total_count or 0)
    if not total or not photos.photos:
        return 0, None, True
    sizes = sorted(photos.photos[0], key=lambda s: s.width)
    pick = next((s for s in sizes if s.width >= AVATAR_TARGET), sizes[-1])
    try:
        buffer = await bot.download(pick.file_id)
        return total, buffer.read() if buffer else None, True
    except Exception as error:
        log.info("аватарка %s не скачалась: %s", user_id, error)
        return total, None, True


async def _fragment(username: str | None) -> fragment.Name:
    try:
        return await asyncio.wait_for(
            fragment.look(username), timeout=config.FRAGMENT_TIMEOUT * 2 + 1
        )
    except asyncio.TimeoutError:
        return fragment.Name()


async def _chat(bot: Bot, user_id: int):
    try:
        return await bot.get_chat(user_id)
    except Exception as error:
        log.info("профиль %s не открыть: %s", user_id, error)
        return None


async def fetch(bot: Bot, user_id: int, username: str | None, name: str) -> Profile:
    """Собрать всё, что Telegram отдаёт боту, в один снимок."""
    chat, (photos, avatar, seen), haul, found = await asyncio.gather(
        _chat(bot, user_id),
        _avatar(bot, user_id),
        gifts(bot, user_id),
        _fragment(username),
    )
    guessed: list[str] = []
    profile = Profile(
        user_id=user_id,
        username=username,
        name=name or "Без имени",
        photos=photos,
        avatar=avatar,
        avatar_quality=photo.quality(avatar),
        nft_name=found.collectible,
        name_usd=found.usd if found.collectible else 0.0,
    )
    if not seen:
        guessed.append("avatar")

    if chat is None:
        guessed += ["bio", "level"]
    else:
        profile.bio = chat.bio or ""
        profile.level = int(getattr(chat.rating, "level", 0) or 0)

    if haul is None:
        guessed += ["gifts", "value"]
    else:
        profile.gifts, profile.nft, profile.stars = haul.gifts, haul.nft, haul.stars
        profile.rare_name, profile.rarity = haul.rare_name, round(haul.rarity, 3)

    profile.guessed = tuple(guessed)
    return profile
