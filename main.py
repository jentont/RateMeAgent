"""Оценщик профилей Telegram: таблица метрик, у каждой балл из десяти.

Что умеет:

* профиль — себя (/start, кнопка), друга (кнопка «выбрать человека» или
  пересланное от него сообщение), в группе — /rate ответом на сообщение;
* юзернейм — прислать @имя: разбор по шести признакам, свободен ли,
  цена с Fragment;
* поиск — прислать слово: свободные имена вокруг него, лучшие сверху;
* инлайн — `@бот` в любом чате: пустой запрос — свой профиль, `@имя` —
  оценка ника, `поиск слово` — свободные имена;
* батл на призы — мини-апп (webapp.py, battle.py): турнир недели,
  дуэли профилей и ников на звёзды, вывод подарками.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
import sys
import time

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ChatAction, ParseMode
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    FSInputFile,
    MenuButtonWebApp,
    PreCheckoutQuery,
    WebAppInfo,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultCachedPhoto,
    InputMediaPhoto,
    InlineQueryResultsButton,
    InputTextMessageContent,
    KeyboardButton,
    KeyboardButtonRequestUsers,
    LinkPreviewOptions,
    Message,
    MessageOriginUser,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    User,
)

import banner
import battle
import card
import cards_glass
import collect
import config
import db
import emoji
import fragment
import payouts
import usernames
import versus
import webapp
from scoring import METRICS, Score, evaluate, subtitle, title_of, weakest

log = logging.getLogger("profilerate")
router = Router()

#: Подписи старой нижней клавиатуры. Её больше нет, но у тех, кто
#: открывал бота раньше, она может висеть до первого /start — нажатия по
#: ней продолжают работать.
BTN_ME = "Оценить мой профиль"
BTN_NAME = "Оценить юзернейм"
BTN_SEARCH = "Поиск юзернейма"
FRIEND_REQUEST = 1
#: Цвет главной кнопки — как у «Бросить вызов в чат» в Мог Батле.
PRIMARY = "primary"

#: Что сказать про слабую строку. Совет — то, что можно сделать за вечер.
TIPS = {
    "avatar": "поставь чёткое фото: резкое, светлое, без пережатия",
    "username": "займи короткий читаемый @username — без цифр и подчёркиваний",
    "bio": "напиши пару живых строк в «о себе», без рекламы и ссылок пачкой",
    "gifts": "выставь подарки в профиль — скрытые бот не видит",
    "value": "стоимость растёт от NFT-подарков и редких фонов",
    "registered": "возраст аккаунта не накрутить — тут только ждать",
    "level": "уровень растёт от подарков и звёзд, полученных от других",
}

#: Как в инлайне и в личке просят подбор: «поиск storm», «search storm».
SEARCH_RE = re.compile(r"^(?:поиск|найди|найти|search|find|\?)\s*(.*)$", re.I)

_last: dict[int, float] = {}
#: Кто нажал «Оценить ник» / «Найти ник» и теперь шлёт имя или слово.
_waiting: dict[int, str] = {}
#: Подборы: слово → (найденное, когда). Инлайн шлёт запрос на каждую
#: паузу в наборе, и одно и то же слово проверять заново незачем.
_searches: dict[str, tuple[list[usernames.Rating], float]] = {}
SEARCH_TTL = 600


def _button(text: str, **kw) -> InlineKeyboardButton:
    """Кнопка. Эмодзи в начале подписи прослойка emoji сама унесёт в
    премиум-иконку — если Telegram её пустит, а нет — останется как есть."""
    return InlineKeyboardButton(text=text, **kw)


def menu() -> InlineKeyboardMarkup:
    """Главное меню: одна главная кнопка, потом пары по смыслу."""
    rows = [
        [_button("🚀 Отправить оценку в чат", switch_inline_query="", style=PRIMARY)],
        [
            _button("👤 Мой профиль", callback_data="menu:me"),
            _button("👥 Оценить друга", callback_data="menu:friend"),
        ],
        [
            _button("🏷 Оценить ник", callback_data="menu:name"),
            _button("🔍 Найти ник", callback_data="menu:search"),
        ],
    ]
    if config.WEBAPP_URL.startswith("https://"):
        rows.append([_button("🏆 Батл на призы", web_app=WebAppInfo(url=config.WEBAPP_URL))])
    rows.append([_button("❓ Как считается", callback_data="menu:how")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _is_menu(call) -> bool:
    """Нажато под баннером меню, а не под карточкой: у баннера своя подпись."""
    caption = call.message.caption or ""
    return caption.startswith("❓") or caption.startswith("Привет")


def back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_button("⬅️ В меню", callback_data="menu:home")]])


def card_menu(share: str = "") -> InlineKeyboardMarkup:
    """Под карточкой: отправить её в чат и вернуться в меню."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [_button("🚀 Отправить в чат", switch_inline_query=share, style=PRIMARY)],
        [_button("⬅️ В меню", callback_data="menu:home")],
    ])


def friend_picker() -> ReplyKeyboardMarkup:
    """Выбор друга. Такая кнопка бывает только у нижней клавиатуры, поэтому
    она одноразовая и исчезает сразу после выбора."""
    return ReplyKeyboardMarkup(
        keyboard=[[
            KeyboardButton(
                text="👥 Выбрать друга",
                request_users=KeyboardButtonRequestUsers(
                    request_id=FRIEND_REQUEST,
                    user_is_bot=False,
                    max_quantity=1,
                    request_name=True,
                    request_username=True,
                ),
            )
        ]],
        resize_keyboard=True,
        one_time_keyboard=True,
        input_field_placeholder="или перешли сообщение друга",
    )


def _wait_left(user_id: int) -> int:
    """Сколько секунд ещё ждать до следующей оценки. 0 — можно."""
    left = config.RATE_COOLDOWN - (time.monotonic() - _last.get(user_id, 0.0))
    if left > 0:
        return int(left) + 1
    _last[user_id] = time.monotonic()
    return 0


def _bar(value: float | None) -> str:
    if value is None:
        return "▱" * 10
    full = max(0, min(10, round(value)))
    return "▰" * full + "▱" * (10 - full)


def text_table(rows: list[tuple[str, float | None]]) -> str:
    """Таблица моноширинным блоком — для инлайна, где картинки нет."""
    width = max(len(title) for title, _ in rows)
    lines = [
        f"{title.ljust(width)}  {_bar(value)}  {'—' if value is None else f'{value:.1f}'}"
        for title, value in rows
    ]
    return "<pre>" + html.escape("\n".join(lines)) + "</pre>"


# --------------------------------------------------------------------------
# Профиль
# --------------------------------------------------------------------------


def caption(score: Score, own: bool) -> str:
    p = score.profile
    who = "Твой профиль" if own else f"Профиль {html.escape(p.handle)}"
    lines = [f"📊 <b>{who}: {score.total:.1f}/10</b>", ""]
    tips = [key for key in weakest(score, 3) if key not in p.guessed][:2]
    if tips:
        lines.append("💡 Что подтянуть:" if own else "💡 Слабые места:")
        lines += [f"• {title_of(key)} — {TIPS[key]}" for key in tips]
    if p.guessed:
        lines += ["", "«нет данных» — Telegram это скрыл, в общий балл не идёт."]
    return "\n".join(lines)


def profile_text(score: Score) -> str:
    p = score.profile
    rows = [(title, None if key in p.guessed else score.parts[key]) for key, title in METRICS]
    head = f"<b>Профиль {html.escape(p.handle)}: {score.total:.1f}/10</b>"
    notes = ", ".join(
        f"{title.lower()}: {html.escape(subtitle(key, p))}"
        for key, title in METRICS
        if key in ("gifts", "registered") and key not in p.guessed
    )
    return f"{head}\n{text_table(rows)}" + (f"\n{notes}" if notes else "")


async def rate(message: Message, bot: Bot, target: User | None, own: bool,
               user_id: int | None = None, username: str | None = None,
               name: str = "", actor: int | None = None, markup=None) -> None:
    """Прислать карточку профиля. `actor` — кто просил (у нажатия кнопки
    автор сообщения — сам бот), `markup` — своя клавиатура вместо обычной."""
    left = _wait_left(actor or message.from_user.id)
    if left:
        await message.reply(f"Подожди {left} с — и оценю ещё.")
        return
    if target is not None:
        user_id, username, name = target.id, target.username, target.full_name
    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    profile = await collect.fetch(bot, user_id, username, name)
    score = evaluate(profile)
    me = await bot.me()
    blob = await asyncio.to_thread(card.render, score, me.username or "")
    await message.reply_photo(
        BufferedInputFile(blob, filename="profile.png"),
        caption=caption(score, own),
        reply_markup=markup or (card_menu() if message.chat.type == "private" else None),
    )


# --------------------------------------------------------------------------
# Юзернеймы
# --------------------------------------------------------------------------


def username_caption(rating: usernames.Rating) -> str:
    lines = [
        f"🏷 <b>@{rating.username}: {rating.total:.1f}/10</b>",
        html.escape(rating.headline[:1].upper() + rating.headline[1:]),
    ]
    if rating.pros:
        lines += ["", "Плюсы: " + html.escape("; ".join(rating.pros).lower())]
    if rating.cons:
        lines += ["Минусы: " + html.escape("; ".join(rating.cons).lower())]
    if rating.status is usernames.Status.FREE:
        lines += ["", "Свободен — займи в настройках, пока не забрали."]
    return "\n".join(lines)


def username_text(rating: usernames.Rating) -> str:
    rows = [(row.title, row.value) for row in rating.rows]
    return (
        f"<b>@{rating.username}: {rating.total:.1f}/10</b> · {html.escape(rating.headline)}\n"
        f"{text_table(rows)}"
    )


async def rate_name(message: Message, bot: Bot, raw: str, actor: int | None = None) -> None:
    name = usernames.normalize(raw)
    if not usernames.ratable(name):
        await message.reply(
            "Это не юзернейм: латиница, цифры и «_», 5–32 символа, начинается с буквы."
        )
        return
    left = _wait_left(actor or message.from_user.id)
    if left:
        await message.reply(f"Подожди {left} с — и оценю ещё.")
        return
    await bot.send_chat_action(message.chat.id, ChatAction.UPLOAD_PHOTO)
    rating = await usernames.rate(name)
    me = await bot.me()
    blob = await asyncio.to_thread(
        card.render_username, rating, rating.headline, me.username or ""
    )
    rows = [[_button("🚀 Отправить в чат", switch_inline_query=f"@{name}", style=PRIMARY)]]
    if rating.status is not usernames.Status.FREE and usernames.valid(name):
        rows.append([_button("🔍 Похожие свободные", callback_data=f"s:{name}")])
    rows.append([_button("⬅️ В меню", callback_data="menu:home")])
    markup = InlineKeyboardMarkup(inline_keyboard=rows)
    await message.reply_photo(
        BufferedInputFile(blob, filename="username.png"),
        caption=username_caption(rating),
        reply_markup=markup,
    )


async def find(seed: str, budget: float | None = None) -> list[usernames.Rating]:
    key = usernames.normalize(seed)
    cached = _searches.get(key)
    if cached and time.time() - cached[1] < SEARCH_TTL and key:
        return cached[0]
    found = await usernames.search(key, budget=budget)
    if len(_searches) > 500:
        _searches.clear()
    if key:
        _searches[key] = (found, time.time())
    return found


def search_text(seed: str, found: list[usernames.Rating]) -> str:
    about = f" по «{html.escape(seed)}»" if seed else ""
    if not found:
        return (
            f"Свободных{about} сейчас не нашлось — попробуй другое слово "
            "или ещё раз: каждый подбор новый."
        )
    lines = [f"🔍 <b>Свободные юзернеймы{about}</b>", ""]
    lines += [f"<code>@{r.username}</code> — {r.total:.1f}/10" for r in found]
    lines += ["", "Нажми на имя, чтобы скопировать, и займи в настройках."]
    return "\n".join(lines)


async def run_search(message: Message, bot: Bot, seed: str, user_id: int) -> None:
    left = _wait_left(user_id)
    if left:
        await message.answer(f"Подожди {left} с — и поищу ещё.")
        return
    await bot.send_chat_action(message.chat.id, ChatAction.TYPING)
    key = usernames.normalize(seed)
    # «Ещё варианты» должны быть новыми, а не тем же списком из кэша.
    _searches.pop(key, None)
    found = await find(seed)
    await message.answer(
        search_text(key, found),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [_button("🔄 Ещё варианты", callback_data=f"s:{key}")],
            [_button("⬅️ В меню", callback_data="menu:home")],
        ]),
    )


# --------------------------------------------------------------------------
# Личка и группы
# --------------------------------------------------------------------------

def start_text(name: str, bot: str) -> str:
    """Главное меню, как в Мог Батле: по имени, хук в цитате, команды
    моноширинным, подробности — в раскрывающейся цитате."""
    who = html.escape((name or "").strip().split()[0] if (name or "").strip() else "")
    hello = f"Привет, <b>{who}</b>! 👤" if who else "Привет! 👤"
    return (
        f"{hello}\n"
        "<blockquote>Сколько твой профиль из 10? 💯</blockquote>\n"
        "Оцениваю профили и юзернеймы Telegram по 10-балльной шкале — "
        "таблицей, по каждой метрике.\n\n"
        "<u>В любом чате</u>\n"
        f"• <code>@{bot}</code> — твоя таблица\n"
        f"• <code>@{bot} @ник</code> — оценка ника\n"
        f"• <code>@{bot} поиск слово</code> — свободные ники\n\n"
        "<blockquote expandable><u>Ещё способы</u>\n"
        "/me — свой профиль\n"
        "/u @ник — оценка юзернейма\n"
        "/search слово — свободные ники рядом со словом\n"
        "/rate ответом на сообщение в группе — профиль автора\n"
        "Перешли мне сообщение друга — оценю его профиль</blockquote>"
    )


HOW = (
    "❓ <b>Как считается</b>\n\n"
    "<b>Профиль</b> — семь метрик, у каждой балл из 10:\n"
    "<blockquote>Аватар — качество самого кадра: резкость, детали, контраст\n"
    "@username — словарный ли, читается ли, цена на Fragment\n"
    "О себе — живое описание лучше пустого и лучше рекламы\n"
    "Подарки — сколько, NFT и редкость фонов\n"
    "Стоимость — во сколько звёзд всё это стоит\n"
    "Регистрация — возраст аккаунта, его не накрутить\n"
    "Уровень TG — растёт от подарков и звёзд</blockquote>\n"
    "Общий балл — взвешенный: подарки, ник и аватар весят больше, «о себе» — "
    "меньше. Что Telegram скрыл, пишется «нет данных» и в итог не идёт.\n\n"
    "<b>Юзернейм</b> — шесть признаков: слово, произносимость, длина, "
    "символы, набор, повторы. Свободен ли — сверяю t.me и Fragment."
)


#: Баннер меню и его file_id после первой отправки: дальше Telegram отдаёт
#: картинку из своего кэша, и меню открывается без загрузки файла.
BANNER = banner.OUT
_banner_id: str | None = None


async def send_menu(bot: Bot, chat_id: int, name: str) -> None:
    """Главное меню: баннер, приветствие в подписи и кнопки под ним."""
    global _banner_id
    me = await bot.me()
    text = start_text(name, me.username)
    if not BANNER.exists():
        await bot.send_message(chat_id, text, reply_markup=menu())
        return
    sent = await bot.send_photo(
        chat_id, _banner_id or FSInputFile(BANNER), caption=text, reply_markup=menu()
    )
    if sent.photo:
        _banner_id = sent.photo[-1].file_id


async def _edit(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Поменять текст меню на месте: у баннера это подпись, у текста — сам текст."""
    if call.message.photo:
        await call.message.edit_caption(caption=text, reply_markup=markup)
    else:
        await call.message.edit_text(text, reply_markup=markup)


async def _clear_old_keyboard(bot: Bot, chat_id: int) -> None:
    """Убрать нижнюю клавиатуру старой версии бота.

    Убрать её можно только сообщением с ReplyKeyboardRemove, а меню у нас
    под сообщением — в одно их не совместить. Поэтому служебное сообщение
    шлём и сразу удаляем: клавиатура при этом остаётся убранной.
    """
    try:
        sent = await bot.send_message(chat_id, "·", reply_markup=ReplyKeyboardRemove(), parse_mode=None)
        await bot.delete_message(chat_id, sent.message_id)
    except Exception as error:
        log.debug("старую клавиатуру не убрать: %s", error)


@router.message(CommandStart(), F.chat.type == "private")
async def start(message: Message, bot: Bot, command: CommandObject | None = None) -> None:
    arg = (command.args if command else "") or ""
    if arg.startswith("u_"):
        await rate_name(message, bot, arg[2:])
        return
    if arg.startswith("s_"):
        await run_search(message, bot, arg[2:], message.from_user.id)
        return
    if arg == "me":
        await rate(message, bot, message.from_user, own=True)
        return
    await _clear_old_keyboard(bot, message.chat.id)
    await send_menu(bot, message.chat.id, message.from_user.first_name)


@router.callback_query(F.data.startswith("menu:"))
async def menu_button(call: CallbackQuery, bot: Bot) -> None:
    what = call.data.split(":", 1)[1]
    user = call.from_user
    if what == "home":
        await call.answer()
        # С баннера или из «Как считается» — меню возвращается на место.
        # Под карточкой профиля или ника меню приходит новым сообщением:
        # карточку затирать нельзя, её только что получили.
        if call.message.text or (call.message.photo and _is_menu(call)):
            me = await bot.me()
            await _edit(call, start_text(user.first_name, me.username), menu())
        else:
            await send_menu(bot, call.message.chat.id, user.first_name)
        return
    if what == "how":
        await call.answer()
        await _edit(call, HOW, back())
        return
    if what == "me":
        await call.answer("Смотрю профиль…")
        await rate(call.message, bot, user, own=True, actor=user.id)
        return
    if what == "friend":
        await call.answer()
        await call.message.answer(
            "👥 Нажми «Выбрать друга» внизу — или перешли мне любое его сообщение.",
            reply_markup=friend_picker(),
        )
        return
    if what == "name":
        _waiting[user.id] = "name"
        await call.answer()
        await call.message.answer("🏷 Пришли юзернейм — например, <code>@storm</code>.", reply_markup=back())
        return
    if what == "search":
        _waiting[user.id] = "search"
        await call.answer()
        await call.message.answer(
            "🔍 Пришли слово — подберу свободные ники вокруг него.\n"
            "Или <code>рандом</code> — короткие случайные.",
            reply_markup=back(),
        )
        return
    await call.answer()


@router.message(F.text == BTN_ME)
@router.message(Command("me", "rate"), ~F.reply_to_message)
async def rate_me(message: Message, bot: Bot) -> None:
    await rate(message, bot, message.from_user, own=True)


@router.message(Command("rate"), F.reply_to_message)
async def rate_reply(message: Message, bot: Bot) -> None:
    target = message.reply_to_message.from_user
    if target is None or target.is_bot:
        await message.reply("Ответь командой на сообщение человека.")
        return
    await rate(message, bot, target, own=target.id == message.from_user.id)


@router.message(Command("id"))
async def my_id(message: Message) -> None:
    """Свой id — нужен, например, чтобы вписать аккаунт в PACK_OWNER."""
    text = f"Твой id: <code>{message.from_user.id}</code>"
    if message.chat.type != "private":
        # Так узнают id группы-склада для STORAGE_CHAT_ID.
        text += f"\nid этого чата: <code>{message.chat.id}</code>"
    await message.reply(text)


@router.message(Command("u", "username"))
async def rate_name_cmd(message: Message, bot: Bot, command: CommandObject) -> None:
    if not command.args:
        await message.reply("Напиши так: /u @имя")
        return
    await rate_name(message, bot, command.args)


@router.message(Command("search", "find"))
async def search_cmd(message: Message, bot: Bot, command: CommandObject) -> None:
    await run_search(message, bot, command.args or "", message.from_user.id)


@router.message(F.text == BTN_NAME)
async def ask_name(message: Message) -> None:
    _waiting[message.from_user.id] = "name"
    await message.answer("Пришли юзернейм — например, @storm.")


@router.message(F.text == BTN_SEARCH)
async def ask_seed(message: Message) -> None:
    _waiting[message.from_user.id] = "search"
    await message.answer(
        "Пришли слово — подберу свободные имена вокруг него. "
        "Или «рандом» — короткие случайные."
    )


@router.callback_query(F.data.startswith("s:"))
async def search_button(call: CallbackQuery, bot: Bot) -> None:
    await call.answer("Ищу свободные…")
    await run_search(call.message, bot, call.data[2:], call.from_user.id)


@router.message(F.users_shared)
async def rate_shared(message: Message, bot: Bot) -> None:
    shared = message.users_shared.users[0]
    name = " ".join(part for part in (shared.first_name, shared.last_name) if part)
    await rate(
        message, bot, None, own=shared.user_id == message.from_user.id,
        user_id=shared.user_id, username=shared.username, name=name,
    )
    await _clear_old_keyboard(bot, message.chat.id)


@router.message(F.forward_origin, F.chat.type == "private")
async def rate_forward(message: Message, bot: Bot) -> None:
    origin = message.forward_origin
    if not isinstance(origin, MessageOriginUser):
        await message.reply(
            "Автор скрыл аккаунт в пересылке — нажми «Оценить друга» и выбери его."
        )
        return
    target = origin.sender_user
    await rate(message, bot, target, own=target.id == message.from_user.id)


@router.message(F.text, F.chat.type == "private")
async def free_text(message: Message, bot: Bot) -> None:
    text = message.text.strip()
    mode = _waiting.pop(message.from_user.id, None)
    asked = SEARCH_RE.match(text)
    if mode == "search" or asked:
        seed = asked.group(1) if asked else text
        if seed.lower() in ("рандом", "random", "любой", "любые"):
            seed = ""
        await run_search(message, bot, seed, message.from_user.id)
        return
    if mode == "name" or text.startswith("@") or "t.me/" in text or usernames.valid(text.lower()):
        await rate_name(message, bot, text)
        return
    await message.answer(
        "Пришли <code>@ник</code> — оценю юзернейм, <code>поиск слово</code> — "
        "найду свободные. Или выбери в меню 👇",
        reply_markup=menu(),
    )


# --------------------------------------------------------------------------
# Батл: оплата, итоги, касса
# --------------------------------------------------------------------------


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    ok = query.invoice_payload.startswith(payouts.TOPUP_PREFIX) and query.currency == "XTR"
    await query.answer(ok=ok, error_message=None if ok else "Счёт устарел — открой батл заново.")


@router.message(F.successful_payment)
async def paid(message: Message) -> None:
    pay = message.successful_payment
    if not pay.invoice_payload.startswith(payouts.TOPUP_PREFIX):
        return
    user_id = int(pay.invoice_payload[len(payouts.TOPUP_PREFIX):].split(":")[0])
    fresh = await payouts.credit_topup(user_id, pay.total_amount, pay.telegram_payment_charge_id)
    if fresh:
        await message.answer(
            f"+{pay.total_amount}⭐ на балансе батла. Баланс: {await db.balance(user_id)}⭐",
            parse_mode=None,
        )


@router.message(Command("bank"))
async def bank(message: Message, bot: Bot) -> None:
    if message.from_user.id not in config.ADMIN_IDS:
        return
    s = await db.stats()
    try:
        stars = (await bot.get_my_star_balance()).amount
    except Exception:
        stars = "?"
    await message.answer(
        "<b>Касса батла</b>\n"
        f"Звёзд у бота: {stars}⭐\n"
        f"На руках у игроков: {s['held']}⭐ — столько бот должен держать для выводов\n"
        f"Доход кассы: {s['house']}⭐\n"
        f"Пополнили: {s['paid_in']}⭐ · вывели: {s['paid_out']}⭐\n"
        f"Игроков: {s['users']} · дуэлей: {s['duels']}"
    )


async def pack_keeper(bot: Bot) -> None:
    """Создать свой пак эмодзи и перейти на него.

    Создание набора Telegram держит под флуд-лимитом на владельце, и
    отвечает «повтори через 5 минут» снова и снова. Поэтому пробуем в
    фоне раз в PACK_RETRY, а до успеха бот живёт на наборе Solutions.
    """
    from aiogram.exceptions import TelegramRetryAfter

    sys.path.insert(0, str(config.BASE_DIR / "tools"))
    import upload_pack

    while True:
        try:
            name = await upload_pack.ensure(bot, log=log.info)
        except TelegramRetryAfter as error:
            # Сюда доходит только запрет на создание самого набора: паузы
            # при доливке иконок upload_pack.ensure пережидает сам.
            wait = max(config.PACK_RETRY, error.retry_after + 30)
            log.info("свой пак эмодзи: флуд-лимит Telegram, пробую через %s мин", wait // 60)
            await asyncio.sleep(wait)
            continue
        except Exception as error:
            log.warning("свой пак эмодзи не собрался: %s — пробую через час", error)
            await asyncio.sleep(3600)
            continue
        log.info("свой пак: %s", (await emoji.load(bot, name)).replace("\n", "; "))
        return


async def ticker(bot: Bot) -> None:
    """Раз в минуту: подвести итоги турнира и вернуть просроченные заявки."""
    while True:
        try:
            due = await battle.due_tourney()
            if due is not None:
                awards = await battle.settle_tourney(due, rescore=webapp.live_score)
                await battle.current_tourney()
                for award in awards:
                    try:
                        await bot.send_message(
                            award.user_id,
                            f"Турнир недели: {award.place} место! +{award.stars}⭐ на балансе батла — "
                            "выводи подарками в кошельке.",
                            parse_mode=None,
                        )
                    except Exception as error:
                        log.info("не поздравить %s: %s", award.user_id, error)
            for row in await battle.expire_duels():
                try:
                    await bot.send_message(
                        row["a_id"],
                        f"Соперник на {row['stake']}⭐ так и не нашёлся — ставка вернулась.",
                        parse_mode=None,
                    )
                except Exception:
                    pass
        except Exception:
            log.exception("тикер батла упал — попробую через минуту")
        await asyncio.sleep(60)


@router.message(F.chat.type == "private")
async def fallback(message: Message) -> None:
    await message.answer("Выбери в меню 👇", reply_markup=menu())


# --------------------------------------------------------------------------
# Инлайн
# --------------------------------------------------------------------------


def _article(key: str, title: str, description: str, text: str,
             markup: InlineKeyboardMarkup | None = None) -> InlineQueryResultArticle:
    return InlineQueryResultArticle(
        id=hashlib.md5(key.encode()).hexdigest(),
        title=title,
        description=description,
        input_message_content=InputTextMessageContent(
            message_text=text, parse_mode=ParseMode.HTML
        ),
        reply_markup=markup,
    )


def _preview(url: str) -> LinkPreviewOptions:
    return LinkPreviewOptions(url=url, prefer_large_media=True, show_above_text=True)


def _photo(key: str, file_id: str, title: str, description: str, caption: str,
           markup: InlineKeyboardMarkup | None = None):
    """Картинка с подписью.

    file_id-адрес (https://…) — режим @send: текст с большим превью
    картинки над ним, в рамке. Иначе — обычное фото из склада.
    """
    if file_id.startswith("https://"):
        return InlineQueryResultArticle(
            id=hashlib.md5(key.encode()).hexdigest(),
            title=title,
            description=description,
            thumbnail_url=file_id,
            input_message_content=InputTextMessageContent(
                message_text=caption, parse_mode=ParseMode.HTML,
                link_preview_options=_preview(file_id),
            ),
            reply_markup=markup,
        )
    return InlineQueryResultCachedPhoto(
        id=hashlib.md5(key.encode()).hexdigest(),
        photo_file_id=file_id,
        title=title,
        description=description,
        caption=caption,
        parse_mode=ParseMode.HTML,
        reply_markup=markup,
    )


def _open(bot_username: str, start: str, text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, url=f"https://t.me/{bot_username}?start={start}")
    ]])


def _type(text: str, query: str) -> InlineKeyboardMarkup:
    """Кнопка, которая сама впишет запрос в поле: «поиск », «@»."""
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, switch_inline_query_current_chat=query)
    ]])


# --- картинки для инлайна -----------------------------------------------------
#
# Инлайн-фото Telegram отдаёт только по file_id — то есть картинку надо
# сначала куда-то залить. Склад — STORAGE_CHAT_ID (приватный канал, бот
# админ). Без него — личка владельца: сообщение тут же удаляется, а
# file_id остаётся рабочим.

_uploaded: dict[str, str] = {}


async def upload(bot: Bot, key: str, blob: bytes | None = None) -> str | None:
    """file_id картинки. blob=None — баннер меню. None — залить некуда.

    С WEBAPP_URL возвращается не file_id, а публичный адрес картинки на
    нашем сервере — для режима @send (см. _photo).
    """
    global _banner_id
    if config.WEBAPP_URL.startswith("https://"):
        return webapp.publish(blob if blob is not None else BANNER.read_bytes())
    if key == "banner" and _banner_id:
        return _banner_id
    if key in _uploaded:
        return _uploaded[key]
    target = config.STORAGE_CHAT_ID or next(iter(config.ADMIN_IDS), 0)
    if not target:
        return None
    photo = FSInputFile(BANNER) if blob is None else BufferedInputFile(blob, filename=f"{key}.png")
    try:
        sent = await bot.send_photo(target, photo, disable_notification=True)
    except Exception as error:
        log.warning("картинку для инлайна не залить: %s", error)
        return None
    file_id = sent.photo[-1].file_id
    if not config.STORAGE_CHAT_ID:
        try:
            await bot.delete_message(target, sent.message_id)
        except Exception:
            pass
    if key == "banner":
        _banner_id = file_id
    else:
        if len(_uploaded) > 500:
            _uploaded.clear()
        _uploaded[key] = file_id
    return file_id


def _accept(data: str, text: str = "⚔️ Принять вызов") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=text, callback_data=data, style=PRIMARY)
    ]])


def _profile_rows(score: Score, other: Score | None = None) -> list:
    """Строки сетки битвы: метрика, у левого, у правого (None — нет данных)."""
    def value(s: Score | None, key: str) -> float | None:
        return None if s is None or key in s.profile.guessed else s.parts[key]
    return [(title, value(score, key), value(other, key)) for key, title in METRICS]


def _nick_rows(a: usernames.Rating, b: usernames.Rating | None = None) -> list:
    if b is None:
        return [(row.title, row.value, None) for row in a.rows]
    return [(ra.title, ra.value, rb.value) for ra, rb in zip(a.rows, b.rows)]


async def _render(fn, *args) -> bytes:
    return await asyncio.to_thread(fn, *args)


async def _menu_results(query: InlineQuery, bot: Bot, me) -> list:
    """Пустой запрос: все режимы разом — профиль, битвы, ник, поиск.

    Всё, что можно, идёт параллельно: профиль и ник считаются вместе,
    картинки рисуются и заливаются вместе. Иначе инлайн отвечал по
    пять-восемь секунд и Telegram крутил загрузку.
    """
    user = query.from_user
    handle = f"@{user.username}" if user.username else "без юзернейма"
    profile, nick = await asyncio.gather(
        collect.fetch(bot, user.id, user.username, user.full_name),
        _quick_rate(user.username) if user.username else asyncio.sleep(0),
    )
    score = evaluate(profile)
    bot_name = me.username or ""

    jobs = {
        "card": _render(card.render, score, bot_name),
        "pv": _render(cards_glass.versus, "Битва профилей",
                      (user.full_name, handle, profile.avatar, score.total), None,
                      _profile_rows(score), bot_name),
    }
    if nick:
        jobs["nick"] = _render(card.render_username, nick, nick.headline, bot_name)
        jobs["nv"] = _render(cards_glass.versus, "Битва ников",
                             (f"@{nick.username}", f"@{nick.username}", None, nick.total), None,
                             _nick_rows(nick), bot_name, True)
    blobs = dict(zip(jobs, await asyncio.gather(*jobs.values())))
    keys = {
        "card": f"pf:{user.id}:{score.total}",
        "pv": f"pvc:{user.id}:{score.total}",
        "nick": f"nk:{nick.username}:{nick.total}" if nick else "",
        "nv": f"nvc:{nick.username}:{nick.total}" if nick else "",
    }
    names = list(blobs)
    ids = await asyncio.gather(upload(bot, "banner"), *(upload(bot, keys[k], blobs[k]) for k in names))
    banner_id, files = ids[0], dict(zip(names, ids[1:]))
    if not banner_id and not files.get("card"):
        return [_article(
            f"me:{user.id}:{score.total}", f"📊 Мой профиль — {score.total:.1f}/10",
            "таблица оценки своего профиля", profile_text(score),
        )]
    banner_id = banner_id or files.get("card")

    results = [
        _photo(
            f"me:{user.id}:{score.total}", files.get("card") or banner_id,
            f"📊 Мой профиль — {score.total:.1f}/10", "отправить таблицу своего профиля",
            caption(score, own=False) if files.get("card") else profile_text(score),
            _open(me.username, "me", "👤 Оценить свой профиль"),
        ),
        _photo(
            f"pv:{user.id}:{score.total}", files.get("pv") or banner_id, "⚔️ Битва профилей",
            "вызов в чат: кто нажмёт — с тем и сравню",
            versus.profile_call(user.full_name), _accept(f"pv:{user.id}"),
        ),
    ]
    if nick:
        results += [
            _photo(
                f"nick:{nick.username}:{nick.total}", files.get("nick") or banner_id,
                f"🏷 Мой ник @{nick.username} — {nick.total:.1f}/10", nick.headline,
                username_caption(nick) if files.get("nick") else username_text(nick),
            ),
            _photo(
                f"nv:{user.id}:{nick.username}:{nick.total}", files.get("nv") or banner_id,
                "⚔️ Битва ников", f"вызвать ником @{nick.username}",
                versus.name_call(nick.username), _accept(f"nv:{user.id}:{nick.username}"),
            ),
        ]
    results += [
        _photo(
            "find", banner_id, "🔍 Поиск свободных ников", "напиши: поиск слово",
            "🔍 <b>Ищешь свободный ник?</b>\nЖми кнопку и допиши слово — подберу свободные.",
            _type("🔍 Найти ник", "поиск "),
        ),
        _photo(
            "rate", banner_id, "🏷 Оценить любой ник", "напиши: @ник · или @ник1 @ник2 — битва",
            "🏷 <b>Сколько стоит твой ник?</b>\nЖми кнопку и впиши @ник — оценю из 10.",
            _type("🏷 Оценить ник", "@"),
        ),
    ]
    return results


async def _quick_rate(name: str) -> usernames.Rating:
    """Оценка ника, но не дольше пары секунд: инлайн ждать не любит."""
    try:
        return await asyncio.wait_for(usernames.rate(name), 3)
    except asyncio.TimeoutError:
        return usernames.rate_offline(name)


async def inline_results(query: InlineQuery, bot: Bot) -> tuple[list, int, bool]:
    """Результаты, время кэша и личные ли они."""
    me = await bot.me()
    text = (query.query or "").strip()
    asked = SEARCH_RE.match(text)

    if not text:
        return await _menu_results(query, bot, me), 30, True

    if asked:
        seed = usernames.normalize(asked.group(1))
        if seed in ("рандом", "random"):
            seed = ""
        if seed and len(seed) < 3:
            return [_article(
                "short", "Слово покороче трёх букв", "Допиши слово для поиска",
                "Для поиска нужно слово хотя бы из трёх букв.",
            )], 5, False
        found = await find(seed, budget=config.INLINE_BUDGET)
        banner_id = await upload(bot, "banner")
        if not found:
            return [_article(
                f"none:{seed}", "Свободных не нашлось", "Попробуй другое слово",
                search_text(seed, found),
                _open(me.username, f"s_{seed}" if seed else "help", "Искать в боте"),
            )], 5, False
        listed = search_text(seed, found)
        blob = await _render(
            cards_glass.search, seed, [(r.username, r.total) for r in found], me.username or ""
        )
        picture = await upload(bot, f"sr:{seed}:" + ",".join(r.username for r in found), blob) or banner_id
        results = [
            _photo(f"all:{seed}:{len(found)}", picture, f"🔍 Все найденные — {len(found)} шт.",
                   ", ".join("@" + r.username for r in found), listed)
            if picture else
            _article(f"all:{seed}:{len(found)}", f"Все найденные — {len(found)} шт.",
                     ", ".join("@" + r.username for r in found), listed)
        ]
        results += [
            _article(
                f"free:{r.username}", f"@{r.username} — {r.total:.1f}/10",
                "свободен · отправить с оценкой", username_text(r),
            )
            for r in found
        ]
        return results, 60, False

    pair = versus.VS_RE.match(text)
    if pair:
        a, b = (usernames.normalize(x) for x in pair.groups())
        if usernames.ratable(a) and usernames.ratable(b) and a != b:
            ra, rb = await asyncio.gather(_quick_rate(a), _quick_rate(b))
            blob = await _render(
                cards_glass.versus, "Битва ников",
                (f"@{a}", f"@{a}", None, ra.total), (f"@{b}", f"@{b}", None, rb.total),
                _nick_rows(ra, rb), me.username or "", True,
            )
            banner_id = await upload(bot, f"nvs:{a}:{b}:{ra.total}:{rb.total}", blob) or await upload(bot, "banner")
            body = versus.names(ra, rb).split("\n<pre>")[0]
            key = f"vs:{a}:{b}:{ra.total}:{rb.total}"
            title = f"⚔️ @{a} vs @{b}"
            desc = f"{ra.total:.1f} : {rb.total:.1f}"
            return [
                _photo(key, banner_id, title, desc, body) if banner_id else
                _article(key, title, desc, body)
            ], 300, False

    name = usernames.normalize(text)
    if usernames.ratable(name):
        rating = await usernames.rate(name)
        markup = None
        if rating.status is not usernames.Status.FREE and usernames.valid(name):
            markup = _open(me.username, f"s_{name}", "🔍 Похожие свободные")
        blob = await asyncio.to_thread(
            card.render_username, rating, rating.headline, me.username or ""
        )
        file_id = await upload(bot, f"nk:{name}:{rating.total}:{rating.status}", blob)
        key = f"name:{name}:{rating.status}"
        title = f"🏷 @{name} — {rating.total:.1f}/10"
        if file_id:
            return [_photo(key, file_id, title, rating.headline, username_caption(rating), markup)], 300, False
        return [_article(key, title, rating.headline, username_text(rating), markup)], 300, False

    return [_article(
        "hint", "Что я умею",
        "пусто — меню · @ник — оценка · @ник1 @ник2 — битва · поиск слово",
        start_text("", me.username),
    )], 300, False


@router.inline_query()
async def on_inline(query: InlineQuery, bot: Bot) -> None:
    try:
        results, cache, personal = await inline_results(query, bot)
    except Exception:
        log.exception("инлайн-запрос «%s» упал", query.query)
        return
    if results and all(isinstance(r, InlineQueryResultCachedPhoto) for r in results):
        # (В режиме @send — с WEBAPP_URL — все пункты и так текстовые.)
        # Одни картинки Telegram выкладывает сеткой мелких превью без подписей,
        # и не понять, где профиль, а где битва. С одним текстовым пунктом
        # выдача становится списком: превью, заголовок и описание у каждого.
        me = await bot.me()
        results.append(_article(
            "howto", "ℹ️ Как пользоваться",
            "пусто — меню · @ник — оценка · @ник1 @ник2 — битва · поиск слово",
            start_text("", me.username),
        ))
    try:
        await query.answer(
            results, cache_time=cache, is_personal=personal,
            button=InlineQueryResultsButton(text="Открыть бота", start_parameter="help"),
        )
    except Exception as error:
        # Человек допечатал дальше, и Telegram этот запрос уже забыл.
        log.debug("инлайн не ответил: %s", error)


# --- битвы из инлайна -------------------------------------------------------
#
# Вызов лежит в чате картинкой с кнопкой. Кто первый нажал — тот соперник:
# бот считает обоих и меняет подпись под картинкой на таблицу битвы.
# Кнопку снимаем сразу, чтобы второй нажавший не запустил битву повторно.

_fought: set[str] = set()


async def _finish(bot: Bot, call: CallbackQuery, text: str, blob: bytes | None = None) -> None:
    """Поставить итог на место вызова: новую картинку, а если её не залить —
    хотя бы подпись с таблицей под старой."""
    me = await bot.me()
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚔️ Новый вызов", switch_inline_query_current_chat="")],
        [InlineKeyboardButton(text="🚀 Открыть бота", url=f"https://t.me/{me.username}?start=help")],
    ])
    file_id = await upload(bot, f"fight:{call.inline_message_id}", blob) if blob else None
    if file_id and file_id.startswith("https://"):
        # Вызов ушёл текстом с превью — меняем текст и ссылку на итог.
        await bot.edit_message_text(
            inline_message_id=call.inline_message_id, text=text.split("\n<pre>")[0],
            link_preview_options=_preview(file_id), reply_markup=markup,
        )
        return
    if file_id:
        await bot.edit_message_media(
            inline_message_id=call.inline_message_id,
            media=InputMediaPhoto(
                media=file_id, caption=text.split("\n<pre>")[0], parse_mode=ParseMode.HTML
            ),
            reply_markup=markup,
        )
        return
    await bot.edit_message_caption(
        inline_message_id=call.inline_message_id, caption=text, reply_markup=markup
    )


async def _person(bot: Bot, user_id: int) -> tuple[str | None, str]:
    """Ник и имя того, кто бросил вызов: в callback у нас только его id."""
    try:
        chat = await bot.get_chat(user_id)
        name = " ".join(p for p in (chat.first_name, chat.last_name) if p)
        return chat.username, name or "Игрок"
    except Exception:
        return None, "Игрок"


@router.callback_query(F.data.startswith("pv:") | F.data.startswith("nv:"))
async def accept_battle(call: CallbackQuery, bot: Bot) -> None:
    kind, challenger, *rest = call.data.split(":")
    challenger_id = int(challenger)
    if call.from_user.id == challenger_id:
        await call.answer("Это твой вызов — ждём соперника.", show_alert=True)
        return
    place = call.inline_message_id or f"{call.message.chat.id}:{call.message.message_id}"
    if place in _fought:
        await call.answer("Битва уже идёт.")
        return
    if kind == "nv" and not call.from_user.username:
        await call.answer("Для битвы ников нужен @username в профиле Telegram.", show_alert=True)
        return
    left = _wait_left(call.from_user.id)
    if left:
        await call.answer(f"Подожди {left} с.", show_alert=True)
        return
    _fought.add(place)
    await call.answer("Считаю…")
    try:
        if kind == "pv":
            username, name = await _person(bot, challenger_id)
            mine, theirs = await asyncio.gather(
                collect.fetch(bot, challenger_id, username, name),
                collect.fetch(bot, call.from_user.id, call.from_user.username, call.from_user.full_name),
            )
            sa, sb = evaluate(mine), evaluate(theirs)
            text = versus.profiles(name, sa, call.from_user.full_name, sb)
            me = await bot.me()
            blob = await _render(
                cards_glass.versus, "Битва профилей",
                (name, f"@{username}" if username else "без юзернейма", mine.avatar, sa.total),
                (call.from_user.full_name,
                 f"@{call.from_user.username}" if call.from_user.username else "без юзернейма",
                 theirs.avatar, sb.total),
                _profile_rows(sa, sb), me.username or "",
            )
        else:
            ra, rb = await asyncio.gather(_quick_rate(rest[0]), _quick_rate(call.from_user.username))
            text = versus.names(ra, rb)
            me = await bot.me()
            blob = await _render(
                cards_glass.versus, "Битва ников",
                (f"@{ra.username}", f"@{ra.username}", None, ra.total),
                (f"@{rb.username}", f"@{rb.username}", None, rb.total),
                _nick_rows(ra, rb), me.username or "", True,
            )
        await _finish(bot, call, text, blob)
    except Exception:
        # Битва не случилась — пусть кнопку можно будет нажать снова.
        _fought.discard(place)
        log.exception("битва %s не досчиталась", call.data)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    config.check()
    session = AiohttpSession(proxy=config.PROXY) if config.PROXY else None
    bot = Bot(
        config.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    if not BANNER.exists():
        banner.render().save(BANNER, optimize=True)
    emoji.register(bot)
    log.info("премиум-эмодзи: %s", (await emoji.load(bot)).replace("\n", "; "))
    # В наборе Solutions эти значки — плашки с надписями («SALE», «100»,
    # «BACK»): на кнопках они читаются как реклама. Пока своего пака нет,
    # пусть там будут обычные эмодзи; свой пак (pack_keeper) вернёт иконки.
    for plain in (config.SOLUTIONS_SKIP if config.PREMIUM_PACK == config.SOLUTIONS_PACK else ()):
        for form in emoji._forms(plain):
            emoji.PACK.pop(form, None)
    emoji._PATTERN.clear()
    dp = Dispatcher()
    dp.include_router(router)
    await db.init()
    webapp.use_bot(bot)
    runner = await webapp.serve()
    await battle.current_tourney()
    if config.WEBAPP_URL.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="Батл", web_app=WebAppInfo(url=config.WEBAPP_URL))
        )
    else:
        log.warning("WEBAPP_URL не задан или не https — кнопки «Батл» не будет")
    tick = asyncio.create_task(ticker(bot))
    # Свой пак, созданный руками, уже задан в PREMIUM_PACK — создавать нечего.
    keeper = asyncio.create_task(
        pack_keeper(bot) if config.PREMIUM_PACK == config.SOLUTIONS_PACK else asyncio.sleep(0)
    )
    # Накопившиеся апдейты не выбрасываем: среди них могут быть оплаты.
    await bot.delete_webhook(drop_pending_updates=False)
    log.info("бот запущен: @%s", (await bot.me()).username)
    try:
        await dp.start_polling(bot)
    finally:
        tick.cancel()
        keeper.cancel()
        await runner.cleanup()
        await usernames.close()
        await fragment.close()
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
