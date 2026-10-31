"""Залить свой пак премиум-эмодзи: python tools/upload_pack.py

Берёт иконки из assets/emoji (их рисует tools/make_icons.py) и по токену
бота создаёт набор «rateme_icons_by_<бот>». Владелец — первый из
ADMIN_IDS (или PACK_OWNER): он должен хоть раз написать боту, иначе
Telegram откажет.

Набор помечен needs_repainting: иконки белые, и Telegram перекрашивает
их в цвет текста — в тёмной теме светлые, в светлой тёмные.

Запускать можно сколько угодно раз: если набор уже есть, скрипт только
дольёт иконки, которых в нём ещё нет (сравнивает по обычному эмодзи).
Имя набора бот потом читает из PREMIUM_PACK — см. emoji.load.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from aiogram import Bot  # noqa: E402
from aiogram.client.session.aiohttp import AiohttpSession  # noqa: E402
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter  # noqa: E402
from aiogram.types import BufferedInputFile, InputSticker  # noqa: E402

import config  # noqa: E402

FOLDER = ROOT / "assets" / "emoji"
TITLE = "RateMeAgent · Solutions"


def _plain(emoji: str) -> str:
    # Вариационный селектор в emoji_list Telegram иногда не принимает.
    return emoji.replace("️", "")


def _sticker(item: dict) -> InputSticker:
    data = (FOLDER / f"{item['key']}.tgs").read_bytes()
    return InputSticker(
        sticker=BufferedInputFile(data, filename=f"{item['key']}.tgs"),
        format="animated",
        emoji_list=[_plain(item["emoji"])],
    )


def pack_name(bot_username: str) -> str:
    return f"rateme_icons_by_{bot_username}"


async def ensure(bot: Bot, log=print) -> str:
    """Создать набор или долить в него иконки. Возвращает имя набора.

    Зовётся и отсюда, и из бота при запуске (main.pack_keeper): создание
    набора у Telegram бывает под флуд-лимитом, и тогда бот повторяет
    попытку сам, не дожидаясь, пока кто-то запустит скрипт руками.
    """
    owner = config.PACK_OWNER or next(iter(config.ADMIN_IDS), 0)
    if not owner:
        raise SystemExit("Впиши id владельца набора в PACK_OWNER или ADMIN_IDS.")
    items = json.loads((FOLDER / "manifest.json").read_text(encoding="utf-8"))
    me = await bot.me()
    name = pack_name(me.username)
    try:
        current = await bot.get_sticker_set(name)
    except TelegramBadRequest:
        current = None

    if current is None:
        await bot.create_new_sticker_set(
            user_id=owner,
            name=name,
            title=TITLE,
            # Одна иконка, остальные доливаются ниже по одной: лёгкий запрос
            # на создание Telegram пропускает охотнее, чем пачку из 27 файлов.
            stickers=[_sticker(items[0])],
            sticker_type="custom_emoji",
            needs_repainting=True,
        )
        log("создан набор, доливаю остальные иконки")
        current = await bot.get_sticker_set(name)

    have = {_plain(s.emoji or "") for s in current.stickers}
    for item in items:
        if _plain(item["emoji"]) in have:
            continue
        while True:
            try:
                await bot.add_sticker_to_set(user_id=owner, name=name, sticker=_sticker(item))
                break
            except TelegramRetryAfter as error:
                # Доливку Telegram пускает порциями по десятку с паузой в
                # пять минут. Набор уже есть — просто ждём и продолжаем.
                log(f"пауза Telegram {error.retry_after} с, потом долью {item['key']}")
                await asyncio.sleep(error.retry_after + 5)
        log(f"долита иконка {item['key']}")
    return name


async def main() -> None:
    config.check()
    proxy = config.live_proxy()
    session = AiohttpSession(proxy=proxy) if proxy else None
    bot = Bot(config.BOT_TOKEN, session=session)
    try:
        name = await ensure(bot)
        current = await bot.get_sticker_set(name)
        print(f"готово: {len(current.stickers)} иконок — https://t.me/addemoji/{name}")
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
