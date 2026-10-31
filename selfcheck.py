"""Проверка без Telegram: python selfcheck.py → в конце «Всё в порядке»."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace as NS

from PIL import Image

import card
import collect
import fragment
import main
import usernames
import versus
from scoring import METRICS, Profile, evaluate

failed: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok  " if cond else "  FAIL ") + what)
    if not cond:
        failed.append(what)


def _png() -> bytes:
    buf = io.BytesIO()
    Image.effect_noise((640, 640), 60).convert("RGB").save(buf, "PNG")
    return buf.getvalue()


def unique(permille: int):
    attr = lambda name: NS(name=name, rarity_per_mille=permille)  # noqa: E731
    return NS(type="unique", gift=NS(model=attr("Duck"), backdrop=attr("Onyx Black"), symbol=attr("Star")))


class FakeBot:
    def __init__(self, closed: bool = False, pages: int = 1):
        self.closed, self.pages, self.calls = closed, pages, 0

    async def get_chat(self, user_id):
        if self.closed:
            raise RuntimeError("chat not found")
        return NS(bio="пишу код и рисую", rating=NS(level=12))

    async def get_user_profile_photos(self, user_id, limit=1):
        if self.closed:
            raise RuntimeError("forbidden")
        return NS(total_count=3, photos=[[NS(width=160, file_id="s"), NS(width=640, file_id="b")]])

    async def download(self, file_id):
        return io.BytesIO(_png())

    async def get_user_gifts(self, user_id, offset=None, limit=100):
        if self.closed:
            raise RuntimeError("forbidden")
        self.calls += 1
        regular = NS(type="regular", gift=NS(star_count=50), convert_star_count=40)
        gifts = [regular, regular, unique(3)]
        nxt = str(self.calls) if self.calls < self.pages else ""
        return NS(gifts=gifts, next_offset=nxt)


async def checks() -> None:
    async def no_fragment(name):
        return fragment.Name()

    fragment.look = no_fragment

    print("сбор профиля")
    p = await collect.fetch(FakeBot(pages=2), 300_000_000, "storm", "Ваня")
    ok(p.guessed == (), "всё прочитано — нет «нет данных»")
    ok(p.bio == "пишу код и рисую" and p.level == 12, "био и уровень из getChat")
    ok(p.photos == 3 and p.avatar and p.avatar_quality > 0, "аватарка скачана и измерена")
    ok(p.gifts == 6 and p.nft == 2, "подарки со всех страниц")
    ok("Onyx Black" in p.rare_name or p.rare_name, "редкий признак найден")
    ok(p.stars > 100 + 1200, "NFT подняли стоимость")

    closed = await collect.fetch(FakeBot(closed=True), 8_000_000_000, None, "")
    ok(set(closed.guessed) == {"avatar", "bio", "level", "gifts", "value"}, "закрытое — «нет данных»")

    print("балл")
    for prof in (p, closed, Profile(user_id=1, username=None, name="x")):
        s = evaluate(prof)
        ok(1.0 <= s.total <= 10.0, f"общий балл в 1..10: {s.total}")
        ok(all(1.0 <= v <= 10.0 for v in s.parts.values()), "каждая метрика в 1..10")
    hidden = Profile(user_id=300_000_000, username="storm", name="x", guessed=("gifts", "value"))
    shown = Profile(user_id=300_000_000, username="storm", name="x")
    ok(evaluate(hidden).total > evaluate(shown).total, "скрытые подарки не топят балл как пустые")

    print("карточка")
    for prof in (p, closed, Profile(user_id=5, username="a" * 32, name="Очень длинное имя 🙂" * 5)):
        blob = card.render(evaluate(prof), "bot")
        img = Image.open(io.BytesIO(blob))
        ok(img.size == card.SIZE, f"карточка {img.size}")
    ok(len(METRICS) == 7, "семь строк")

    print("подпись")
    text = main.caption(evaluate(p), own=True)
    ok("/10" in text and "Что подтянуть" in text, "балл и советы")
    text = main.caption(evaluate(closed), own=False)
    ok("нет данных" in text and "Возраст" not in text, "пометка про скрытое")

    print("обработчики")
    sent: list = []

    class Msg:
        def __init__(self, **kw):
            self.from_user = NS(id=10, username="me", full_name="Я", first_name="Я", is_bot=False)
            self.chat = NS(id=10, type="private")
            self.photo = None
            self.caption = None
            self.text = None
            self.__dict__.update(kw)

        async def reply_photo(self, photo, caption, reply_markup=None):
            sent.append(("photo", caption))

        async def reply(self, text, **kw):
            sent.append(("reply", text))

        async def answer(self, text, **kw):
            sent.append(("answer", text))

    class HBot(FakeBot):
        async def send_chat_action(self, *a):
            pass

        async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, **kw):
            if caption is None:
                # Заливка картинки для инлайна: без подписи, ради file_id.
                self.uploads = getattr(self, "uploads", 0) + 1
                return NS(photo=[NS(file_id=f"up-{self.uploads}")], message_id=self.uploads)
            sent.append(("banner", caption, photo))
            return NS(photo=[NS(file_id="banner-fid")], message_id=1)

        async def delete_message(self, *a):
            pass

        async def edit_message_caption(self, inline_message_id=None, caption=None, reply_markup=None):
            sent.append(("battle", caption, inline_message_id))

        async def edit_message_text(self, inline_message_id=None, text=None, link_preview_options=None, reply_markup=None):
            sent.append(("battle-text", text, link_preview_options.url))

        async def edit_message_media(self, inline_message_id=None, media=None, reply_markup=None):
            sent.append(("battle", media.caption, inline_message_id))
            self.fight_media = media

        async def me(self):
            return NS(username="ocenka_bot")

    bot = HBot()
    main._last.clear()
    main._banner_id = None
    await main.start(Msg(), bot)
    ok(sent[-1][0] == "banner" and "из 10" in sent[-1][1], "/start — баннер с меню, а не карточка")
    ok(main.BANNER.exists() and main._banner_id == "banner-fid", "баннер есть, file_id запомнен")
    await main.start(Msg(), bot)
    ok(sent[-1][2] == "banner-fid", "второй раз баннер идёт по file_id, без загрузки")
    markup = main.menu()
    first = markup.inline_keyboard[0][0]
    ok(first.style == "primary" and first.switch_inline_query == "", "главная кнопка — цветная, в инлайн")
    ok(all(b.text[0] in "🚀👤👥🏷🔍🏆❓" for row in markup.inline_keyboard for b in row), "у каждой кнопки меню свой эмодзи")
    ok("<blockquote expandable>" in main.start_text("Ваня <script>", "bot") and "&lt;script&gt;" not in main.start_text("Ваня <b>", "bot").split("Привет")[0], "приветствие по имени, команды свёрнуты")
    ok("&lt;b&gt;" in main.start_text("<b>", "bot"), "имя экранируется")

    class Call:
        def __init__(self, data, text="меню"):
            self.data = data
            self.from_user = NS(id=10, username="me", full_name="Я", first_name="Я", is_bot=False)
            self.message = Msg(text=text)
            self.message.from_user = NS(id=999, username="bot", full_name="Бот", is_bot=True)

            async def edit_text(text, **kw):
                sent.append(("edit", text))

            self.message.edit_text = edit_text

        async def answer(self, *a, **kw):
            pass

    main._last.clear()
    await main.menu_button(Call("menu:me"), bot)
    ok(sent[-1][0] == "photo" and "Твой профиль" in sent[-1][1], "кнопка «Мой профиль» — карточка нажавшего")
    await main.rate_me(Msg(), bot)
    ok(sent[-1][0] == "reply" and "Подожди" in sent[-1][1], "кулдаун держит")
    await main.menu_button(Call("menu:how"), bot)
    ok(sent[-1][0] == "edit" and "Как считается" in sent[-1][1], "«Как считается» правит то же сообщение")
    await main.menu_button(Call("menu:home"), bot)
    ok(sent[-1][0] == "edit" and "из 10" in sent[-1][1], "«В меню» возвращает меню на место")

    under_banner = Call("menu:how", text=None)
    under_banner.message.photo = [NS(file_id="banner-fid")]
    under_banner.message.caption = "Привет, Я! 👤"

    async def edit_caption(caption=None, **kw):
        sent.append(("caption", caption))

    under_banner.message.edit_caption = edit_caption
    await main.menu_button(under_banner, bot)
    ok(sent[-1][0] == "caption" and "Как считается" in sent[-1][1], "под баннером «Как считается» правит подпись")
    under_banner.data = "menu:home"
    under_banner.message.caption = "❓ Как считается"
    await main.menu_button(under_banner, bot)
    ok(sent[-1][0] == "caption" and "из 10" in sent[-1][1], "и «В меню» возвращает подпись")
    under_card = Call("menu:home", text=None)
    under_card.message.photo = [NS(file_id="card")]
    under_card.message.caption = "📊 Твой профиль: 7.1/10"
    await main.menu_button(under_card, bot)
    ok(sent[-1][0] == "banner", "под карточкой «В меню» — новым сообщением, карточка цела")
    await main.menu_button(Call("menu:friend"), bot)
    ok(sent[-1][0] == "answer" and "Выбрать друга" in sent[-1][1], "«Оценить друга» — выбор человека")
    await main.menu_button(Call("menu:search"), bot)
    ok(main._waiting.get(10) == "search", "«Найти ник» ждёт слово")
    main._waiting.clear()

    main._last.clear()
    shared = NS(users=[NS(user_id=77, first_name="Петя", last_name=None, username="petya")])
    await main.rate_shared(Msg(users_shared=shared), bot)
    ok(sent[-1][0] == "photo" and "@petya" in sent[-1][1], "друг из кнопки")

    main._last.clear()
    from aiogram.types import MessageOriginHiddenUser, MessageOriginUser, User
    from datetime import datetime
    origin = MessageOriginUser(date=datetime.now(), sender_user=User(id=88, is_bot=False, first_name="Оля", username="olya"))
    await main.rate_forward(Msg(forward_origin=origin), bot)
    ok(sent[-1][0] == "photo" and "@olya" in sent[-1][1], "пересланное сообщение")
    hidden_origin = MessageOriginHiddenUser(date=datetime.now(), sender_user_name="Аноним")
    await main.rate_forward(Msg(forward_origin=hidden_origin), bot)
    ok(sent[-1][0] == "reply" and "скрыл" in sent[-1][1], "скрытая пересылка — подсказка")

    main._last.clear()
    group = Msg(reply_to_message=NS(from_user=NS(id=99, username="vasya", full_name="Вася", is_bot=False)))
    group.chat = NS(id=-100, type="supergroup")
    await main.rate_reply(group, bot)
    ok(sent[-1][0] == "photo" and "@vasya" in sent[-1][1], "/rate ответом в группе")


    print("прокси")
    import config as cfg_proxy

    was = cfg_proxy.PROXY
    cfg_proxy.PROXY = "http://127.0.0.1:1"
    ok(cfg_proxy.live_proxy() == "", "мёртвый прокси (хостинг без VPN) — бот идёт напрямую, не падает")
    cfg_proxy.PROXY = ""
    ok(cfg_proxy.live_proxy() == "", "без прокси — напрямую")
    cfg_proxy.PROXY = was

    print("премиум-эмодзи")
    import emoji
    from aiogram.methods import SendMessage

    saved = dict(emoji.PACK)
    emoji.PACK.update({"⏳": ("111",), "⭐": ("222",)})
    plain = SendMessage(chat_id=1, text="⏳ ждём", parse_mode=None)
    emoji._rewrite(plain, lambda t: emoji.dress(t) or t)
    ok(plain.text == "⏳ ждём", "в текст без разметки теги не лезут")
    rich = SendMessage(chat_id=1, text="⭐ баланс")
    emoji._rewrite(rich, lambda t: emoji.dress(t) or t)
    ok('<tg-emoji emoji-id="222">' in rich.text, "в HTML-текст премиум-эмодзи встают")
    emoji.PACK.clear()
    emoji.PACK.update(saved)

    print("юзернеймы")
    ok(usernames.rate_offline("storm").total > usernames.rate_offline("kwjpz_7").total, "слово выше мусора")
    ok(len(usernames.rate_offline("storm").rows) == 6, "шесть строк разбора")
    ok(usernames.normalize("https://t.me/Storm?x") == "storm", "ссылка → имя")
    ok(usernames.ratable("qq") and not usernames.valid("qq"), "короткое оценить можно, занять нельзя")

    # Подменяем сеть: t.me и Fragment отвечают по таблице.
    tme = {"durov": "profile", "pavel": "contact", "qzxvb": "contact", "freeone": "contact"}
    frg = {"durov": ("taken", None), "pavel": ("unavail", None), "qzxvb": ("avail", "500 TON")}

    async def fake_tg(name):
        if name in tme:
            return tme[name]
        return "contact" if name.endswith(("x", "o", "y")) else "profile"

    async def fake_frag(name):
        return frg.get(name, ("absent", None))

    usernames._telegram, usernames._fragment = fake_tg, fake_frag
    usernames._checked.clear()
    S = usernames.Status
    for name, want in (("durov", S.TAKEN), ("pavel", S.RESERVED), ("qzxvb", S.AUCTION), ("freeone", S.FREE)):
        got, _ = await usernames.check(name)
        ok(got is want, f"@{name} → {got}")
    tme["broken"] = "unknown"
    ok((await usernames.check("broken"))[0] is S.UNKNOWN, "нет ответа — не «свободно»")
    rating = await usernames.rate("qzxvb")
    ok("500 TON" in rating.headline and "Fragment" in rating.headline, f"лот в подписи: {rating.headline}")

    found = await usernames.search("storm", want=5, budget=3)
    ok(0 < len(found) <= 5 and all(r.status is S.FREE for r in found), f"подбор: {[r.username for r in found]}")
    ok([r.total for r in found] == sorted((r.total for r in found), reverse=True), "лучшие сверху")
    found = await usernames.search("", want=4, budget=3)
    ok(all(usernames.valid(r.username) for r in found), "случайные имена валидны")

    async def slow_tg(name):
        await asyncio.sleep(10)
        return "contact"

    usernames._telegram = slow_tg
    usernames._checked.clear()
    t0 = asyncio.get_running_loop().time()
    await usernames.search("zzslow", budget=0.5)
    ok(asyncio.get_running_loop().time() - t0 < 2, "подбор укладывается во время")
    usernames._telegram = fake_tg

    blob = card.render_username(usernames.rate_offline("storm"), "свободен", "bot")
    ok(Image.open(io.BytesIO(blob)).size[0] == 1200, "карточка ника")

    print("личка: юзернеймы")
    main._last.clear()
    await main.free_text(Msg(text="@storm"), bot)
    ok(sent[-1][0] == "photo" and "@storm" in sent[-1][1], "@имя → карточка ника")
    main._last.clear()
    await main.free_text(Msg(text="поиск storm"), bot)
    ok(sent[-1][0] == "answer" and "Свободные" in sent[-1][1], "поиск слово → список")
    main._last.clear()
    await main.ask_seed(Msg(text=main.BTN_SEARCH))
    await main.free_text(Msg(text="рандом"), bot)
    ok(sent[-1][0] == "answer", "кнопка поиска + «рандом»")
    main._last.clear()
    await main.free_text(Msg(text="привет как дела"), bot)
    ok(sent[-1][0] == "answer" and "@ник" in sent[-1][1], "непонятный текст → подсказка с меню")
    main._last.clear()
    await main.ask_name(Msg(text=main.BTN_NAME))
    await main.free_text(Msg(text="не ник"), bot)
    ok(sent[-1][0] == "reply" and "не юзернейм" in sent[-1][1], "кнопка ника + мусор → объяснение")

    print("инлайн")

    class Query:
        def __init__(self, text):
            self.query = text
            self.from_user = NS(id=10, username="me", full_name="Я")
            self.got = None

        async def answer(self, results, **kw):
            self.got = (results, kw)

    async def ask(text):
        q = Query(text)
        await main.on_inline(q, bot)
        return q.got

    results, kw = await ask("")
    titles = [r.title for r in results]
    ok(kw["is_personal"] and "Мой профиль" in titles[0], "пусто → меню, первым свой профиль")
    ok(all(type(r).__name__ == "InlineQueryResultCachedPhoto" for r in results[:-1]), "все режимы — картинками")
    ok(results[-1].title.startswith("ℹ️"), "последним текстовый пункт — выдача списком, с подписями")
    ok(any("Битва профилей" in t for t in titles) and any("Битва ников" in t for t in titles), "обе битвы в меню")
    ok(any("Мой ник" in t for t in titles) and any("Поиск" in t for t in titles), "ник и поиск в меню")
    call_pv = next(r for r in results if "Битва профилей" in r.title).reply_markup.inline_keyboard[0][0]
    ok(call_pv.callback_data == "pv:10", "вызов на битву с кнопкой «Принять»")
    find = next(r for r in results if "Поиск" in r.title).reply_markup.inline_keyboard[0][0]
    ok(find.switch_inline_query_current_chat == "поиск ", "кнопка поиска вписывает запрос")
    ok(any(r.photo_file_id == main._banner_id for r in results), "в инлайне баннер")
    results, _ = await ask("@durov")
    ok("@durov" in results[0].title and results[0].reply_markup, "@ник → карточка и кнопка поиска")
    results, _ = await ask("@durov @pavel")
    ok("vs" in results[0].title and "Битва ников" in results[0].caption, "@ник1 @ник2 → битва ников")
    results, _ = await ask("поиск storm")
    ok(len(results) >= 2 and "Все найденные" in results[0].title, "поиск → список + по одному")
    ok(len({r.id for r in results}) == len(results), "id результатов разные")
    results, _ = await ask("поиск ab")
    ok("короче" in results[0].title.lower() or "покороче" in results[0].title, "слишком короткое слово")
    results, _ = await ask("просто текст")
    ok(results[0].title == "Что я умею", "непонятный запрос → подсказка")
    ok(all(len(r.id) <= 64 for r in results), "id не длиннее 64")

    print("битвы из инлайна")

    class Press:
        def __init__(self, data, user_id, username="rival"):
            self.data = data
            self.inline_message_id = "im-1"
            self.message = None
            self.from_user = NS(id=user_id, username=username, full_name="Соперник")
            self.said = None

        async def answer(self, text=None, show_alert=False):
            self.said = text

    main._last.clear()
    main._fought.clear()
    own = Press("pv:10", 10)
    await main.accept_battle(own, bot)
    ok("твой вызов" in own.said, "свой вызов не принять")
    await main.accept_battle(Press("pv:10", 20), bot)
    ok(sent[-1][0] == "battle" and "Битва профилей" in sent[-1][1] and sent[-1][2] == "im-1", "битва профилей — таблица на месте вызова")
    ok(bot.fight_media.media.startswith("up-"), "итог битвы — новой картинкой на месте вызова")
    again = Press("pv:10", 30)
    await main.accept_battle(again, bot)
    ok("уже" in again.said, "второй раз ту же битву не запустить")
    main._fought.clear()
    main._last.clear()
    noname = Press("nv:10:storm", 40, username=None)
    await main.accept_battle(noname, bot)
    ok("username" in noname.said, "без ника в битву ников нельзя")
    await main.accept_battle(Press("nv:10:storm", 50, username="luna"), bot)
    ok(sent[-1][0] == "battle" and "@storm" in sent[-1][1] and "@luna" in sent[-1][1], "битва ников")
    print("режим @send: текст с превью картинки")
    import config as cfg
    import webapp as web_mod

    saved_url = cfg.WEBAPP_URL
    cfg.WEBAPP_URL = "https://rate.test"
    try:
        results, _ = await ask("")
        ok(all(type(r).__name__ == "InlineQueryResultArticle" for r in results), "все пункты — текстом, выдача списком")
        first = results[0].input_message_content
        url = first.link_preview_options.url
        ok(url.startswith("https://rate.test/img/") and first.link_preview_options.show_above_text, "превью картинки над текстом")
        ok(url.rsplit("/", 1)[1] in web_mod.IMAGES and results[0].thumbnail_url == url, "картинка лежит на нашем сервере, она же миниатюра")
        main._fought.clear()
        main._last.clear()
        await main.accept_battle(Press("pv:10", 60), bot)
        ok(sent[-1][0] == "battle-text" and sent[-1][2].startswith("https://rate.test/img/"), "итог битвы — новый текст и новое превью")
    finally:
        cfg.WEBAPP_URL = saved_url

    ok(len(versus.profiles("А" * 40, evaluate(Profile(user_id=1, username="a", name="x")), "Б", evaluate(Profile(user_id=2, username="b", name="y")))) < 1024, "таблица битвы влезает в подпись")
    ok(all(len(r.id) <= 64 for r in results), "id не длиннее 64")


asyncio.run(checks())

import selfcheck_battle  # noqa: E402

failed += asyncio.run(selfcheck_battle.run())
print("\nВсё в порядке" if not failed else f"\nПровалено: {len(failed)}")
raise SystemExit(1 if failed else 0)
