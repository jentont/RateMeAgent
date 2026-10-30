"""Мини-апп без Telegram: python preview_app.py → http://localhost:8091/?init=…

Поднимает сервер на временной базе с подставными участниками, подписывает
initData тестовым токеном и печатает готовую ссылку. Профили и ники не
ходят в Telegram — оценки подставные.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from types import SimpleNamespace as NS

import config

config.BOT_TOKEN = "123:PREVIEW"
config.DB_PATH = os.path.join(tempfile.mkdtemp(), "preview.db")
config.PORT = int(os.getenv("PORT", "8091"))
config.HOST = "127.0.0.1"

import battle  # noqa: E402
import db  # noqa: E402
import payouts  # noqa: E402
import usernames  # noqa: E402
import webapp  # noqa: E402
from scoring import Profile  # noqa: E402
from selfcheck_battle import GiftBot, signed_init  # noqa: E402

ME = {"id": 1001, "first_name": "Ваня", "username": "storm"}
PLAYERS = [
    (2001, "Лиза", "liza", 8.9), (2002, "Макс", "maxx", 8.1), (2003, "Артём", None, 7.7),
    (2004, "Соня", "sonya_tg", 7.2), (2005, "Даня", "danya", 6.8), (2006, "Кира", "kira", 6.1),
    (2007, "Олег", None, 5.6), (2008, "Ника", "nika", 5.2), (2009, "Тим", "timur", 4.9),
    (2010, "Ева", "eva", 4.1), (2011, "Гоша", None, 3.8),
]


async def fake_fetch(bot, uid, username, name):
    return Profile(user_id=uid, username=username, name=name, photos=3, avatar_quality=0.7,
                   bio="делаю ботов и аватарки", gifts=24, nft=2, stars=6000, level=18,
                   rare_name="Onyx Black (фон)", rarity=0.7)


async def fake_rate(name):
    rating = usernames.rate_offline(name)
    rating.status = usernames.Status.TAKEN
    return rating


async def main() -> None:
    webapp.collect.fetch = fake_fetch
    webapp.usernames.rate = fake_rate
    await db.init()
    webapp.use_bot(GiftBot())
    await db.touch(ME["id"], ME["username"], ME["first_name"])
    await payouts.credit_topup(ME["id"], 340, "seed-me")
    t = await battle.current_tourney()
    for uid, name, nick, score in PLAYERS:
        await db.touch(uid, nick, name)
        await payouts.credit_topup(uid, 200, f"seed-{uid}")
        await battle.join_tourney(uid, score)
    # Пара сыгранных дуэлей и одна заявка в очереди.
    await battle.join_duel("profile", 25, ME["id"], 7.9, "Ваня")
    await battle.join_duel("profile", 25, 2006, 6.1, "Кира")
    await battle.join_duel("profile", 10, 2001, 8.9, "Лиза")
    await battle.join_duel("profile", 10, ME["id"], 7.9, "Ваня")
    await battle.join_duel("profile", 50, 2005, 6.8, "Даня")
    await battle.join_duel("nick", 10, ME["id"], 8.1, "@storm")
    runner = await webapp.serve()
    init = signed_init(config.BOT_TOKEN, ME)
    print(f"http://localhost:{config.PORT}/?init={__import__('urllib.parse').parse.quote(init)}", flush=True)
    with open(os.path.join(os.path.dirname(config.DB_PATH), "url.txt"), "w") as f:
        f.write(init)
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
