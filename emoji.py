"""Премиум-эмодзи в сообщениях бота.

Telegram разрешает ботам вставлять кастомные эмодзи прямо в текст —
разметкой `<tg-emoji emoji-id="...">🔥</tg-emoji>`. Обычный огонь
заменяется на анимированный из платного набора, и карточка результата
выглядит так, как у ботов, за которых платят.

Важная оговорка: **это умеют не все боты**. Bot API пускает кастомные
эмодзи только у тех, кто купил себе юзернейм на Fragment. У остальных
запрос отбивается ошибкой, и без страховки на этом падал бы каждый
сообщение — не «эмодзи не показался», а сообщение не ушло вовсе.

Поэтому работа идёт одной прослойкой поверх всех запросов к Telegram
(`Premium`). На пути туда она одевает знакомые эмодзи в разметку, на
отказе — раздевает обратно и отправляет заново. Первый же отказ
выключает разметку до перезапуска, чтобы не платить лишним запросом за
каждое сообщение.

Одно место на все сообщения выбрано намеренно: эмодзи живут в десятке
текстов, и обернуть каждый руками — значит однажды забыть про новый.
Картинку карточки это не трогает вовсе: там рисует Pillow, и премиум-
эмодзи ему взять неоткуда.
"""

from __future__ import annotations

import logging
import random
import re
import zlib
from typing import Any

import config

log = logging.getLogger("profilerate.emoji")

#: Обычный эмодзи → варианты премиального. Подставляется в текст
#: сообщений, на картинку карточки не влияет: там рисует Pillow, и
#: премиум-эмодзи ему взять неоткуда.
#:
#: Вариантов может быть несколько — тогда на каждое сообщение берётся
#: случайный. Так сделана искорка у карточек редкости: одна и та же
#: блёстка в каждом сообщении примелькалась бы, а десяток разных
#: читается как набор, а не как штамп.
PACK: dict[str, tuple[str, ...]] = config.PREMIUM_EMOJI

#: Разметка, которую понимает Telegram в режиме HTML.
TAG = '<tg-emoji emoji-id="{id}">{emoji}</tg-emoji>'

#: Ею же и раздеваем обратно.
UNTAG = re.compile(r"<tg-emoji emoji-id=\"\d+\">(.*?)</tg-emoji>", re.S)

#: Выключается на первом отказе Telegram и до перезапуска не включается.
_allowed = True

#: Про какие запросы уже отчитались в лог. Нужен ровно один раз на
#: каждый вид метода: «в чате премиум-эмодзи есть, а в инлайне нет» иначе
#: не отличить от «на сервере крутится старый код».
_reported: set[str] = set()


def _report(method: Any) -> None:
    name = type(method).__name__
    if name in _reported:
        return
    _reported.add(name)
    log.info("премиум-эмодзи ушли в %s", name)


def on() -> bool:
    return _allowed and bool(PACK) and config.PREMIUM_EMOJI_ON


#: Набор искорок, разложенный в закреплённом «случайном» порядке.
#: Ключ — сам набор, чтобы переопределение через PREMIUM_EMOJI не
#: осталось с чужим порядком.
_ORDER: dict[str, list[str]] = {}


def _order() -> list[str]:
    choices = list(PACK.get("✨") or ())
    key = ",".join(choices)
    if key not in _ORDER:
        mixed = choices[:]
        # Перемешано один раз и с фиксированным зерном: порядок должен
        # выглядеть случайным, но не меняться между запусками. Иначе
        # эмодзи перестаёт быть приметой фона — человек выбирает фон
        # глазами, а не читает список заново каждый раз.
        random.Random(0).shuffle(mixed)
        _ORDER[key] = mixed
    return _ORDER[key]


#: Что свой набор не перебивает.
#:
#: Искорка — не один значок, а два десятка разных: ими помечены фоны за
#: серию, и каждому фону достаётся свой. Заменить их одной картинкой из
#: нового набора значит превратить набор в штамп — все двенадцать фонов
#: станут одинаковыми. Эти эмодзи подбирались под ступени отдельно, и
#: живут они своей жизнью.
KEEP: tuple[str, ...] = ()

#: Чем кончилась последняя загрузка своего набора. Показывается в
#: админке: «почему эмодзи не наши» иначе выясняется только по логу.
_loaded = "набор ещё не читали"


def _forms(plain: str) -> set[str]:
    """Как этот эмодзи может быть записан в тексте.

    «⚔️» и «⚔» — один и тот же значок: второй просто без невидимого
    селектора U+FE0F. Telegram отдаёт в наборе одну запись, а в текстах
    бота встречаются обе, и незакрытая — остаётся обычным эмодзи.
    """
    base = plain.replace("\ufe0f", "")
    forms = {plain, base}
    if len(base) == 1:
        forms.add(base + "\ufe0f")
    if base.endswith("\u20e3") and len(base) == 2:
        forms.add(base[0] + "\ufe0f\u20e3")
    return forms


async def load(bot: Any, override: str | None = None) -> str:
    """Прочитать свой набор эмодзи у Telegram и подставить его id.

    Набор задаётся именем из ссылки t.me/addemoji/ИМЯ, а не списком id:
    id меняются при каждой правке набора, а имя — нет. Telegram отдаёт
    набор целиком вместе с тем, какой обычный эмодзи каждая картинка
    изображает, — по нему и раскладываем.

    Что набор закрывает, то и берём; остальное остаётся прежним. Иначе
    одна незакрытая ступенька — и в тексте вместо эмодзи пустота.

    Возвращает строку для лога: она же показывается в админке.
    """
    global _loaded

    name = (override or config.PREMIUM_PACK).strip()
    if not name:
        _loaded = "свой набор не задан"
        return _loaded
    try:
        got = await bot.get_sticker_set(name)
    except Exception as error:
        log.warning("набор «%s» не прочитать: %s", name, error)
        _loaded = f"набор «{name}» не прочитать: {error}"
        return _loaded

    свои: dict[str, list[str]] = {}
    for one in getattr(got, "stickers", None) or []:
        emoji_id = getattr(one, "custom_emoji_id", None)
        plain = getattr(one, "emoji", None)
        if emoji_id and plain:
            for form in _forms(plain):
                свои.setdefault(form, []).append(str(emoji_id))
    if not свои:
        _loaded = f"в наборе «{name}» нет кастомных эмодзи"
        return _loaded

    # Своё бережём: см. KEEP.
    оставили = sorted(set(свои) & set(KEEP))
    for plain in оставили:
        свои.pop(plain, None)

    закрыто = sorted(set(свои) & set(PACK))
    PACK.update({plain: tuple(ids) for plain, ids in свои.items()})
    _ORDER.clear()
    _PATTERN.clear()
    мимо = sorted(set(PACK) - set(свои))
    log.info(
        "набор «%s»: %s картинок, своими стали %s",
        name,
        sum(len(ids) for ids in свои.values()),
        " ".join(свои) or "—",
    )
    всего = len({one for ids in свои.values() for one in ids})
    строки = [
        f"набор «{name}»: {всего} картинок",
        f"свои: {' '.join(sorted(свои)) or '—'}",
        f"осталось прежними: {' '.join(мимо) or '—'}",
    ]
    if закрыто:
        строки.append(f"заменили прежние: {' '.join(закрыто)}")
    if оставили:
        строки.append(
            f"не тронули: {' '.join(оставили)} — ими помечены фоны за серию, "
            f"и каждому фону нужен свой значок"
        )
    _loaded = "\n".join(строки)
    return _loaded


def pick(plain: str) -> str | None:
    """Премиум-эмодзи для обычного символа. None — такого нет."""
    choices = PACK.get(plain) or ()
    return random.choice(choices) if choices and on() else None


def sparkle() -> str | None:
    """Случайная искорка из набора. None — премиум недоступен."""
    order = _order()
    return random.choice(order) if order and on() else None


def badge(slot: int) -> str | None:
    """Премиум-эмодзи для фона под номером slot. None — премиум недоступен.

    Раздаём по порядку, а не по хешу имени: хеш на дюжине фонов давал
    повторы — три ступени с одной и той же искоркой, и набор читался
    ошибкой, а не набором.
    """
    order = _order()
    if not order or not on():
        return None
    return order[slot % len(order)]


def dress(text: str | None) -> str | None:
    """Заменить известные эмодзи премиальными.

    Текст уже в HTML, поэтому подстановка безопасна: сам эмодзи в
    разметке остаётся и покажется тем, у кого премиума нет.
    """
    if not text or not on():
        return text
    # Уже одетые куски не трогаем. В рассылке админ сам ставит премиум-
    # эмодзи, и его разметка приходит готовой: одень мы её ещё раз,
    # получилась бы метка внутри метки, а такую Telegram не принимает.
    parts = re.split(r"(<tg-emoji emoji-id=\"\d+\">.*?</tg-emoji>)", text, flags=re.S)
    pattern = _pattern()
    if pattern is None:
        return text

    def one(found: re.Match) -> str:
        plain = found.group(0)
        return TAG.format(id=random.choice(PACK[plain]), emoji=plain)

    for index, part in enumerate(parts):
        if not index % 2:
            parts[index] = pattern.sub(one, part)
    return "".join(parts)


#: Скомпилированный поиск всех знакомых эмодзи. Длинные записи первыми:
#: «⚔️» должен найтись целиком, а не как «⚔» с хвостом.
_PATTERN: dict[str, re.Pattern] = {}


def _pattern() -> re.Pattern | None:
    key = "\x00".join(sorted(PACK))
    if key not in _PATTERN:
        _PATTERN.clear()
        keys = sorted((plain for plain in PACK if PACK[plain]), key=len, reverse=True)
        _PATTERN[key] = re.compile("|".join(re.escape(plain) for plain in keys)) if keys else None
    return _PATTERN[key]


def strip(text: str | None) -> str | None:
    """Снять разметку, оставив сами эмодзи."""
    if not text:
        return text
    return UNTAG.sub(r"\1", text)


#: По каким словам в ответе Telegram понятно, что дело в эмодзи, а не в
#: чём-то ещё. Текст ошибки Telegram менял не раз, поэтому смотрим на
#: куски, а не на строку целиком.
REFUSALS = ("custom emoji", "custom_emoji", "emoji_id", "tg-emoji")


def refused(error: Exception) -> bool:
    low = str(error).lower()
    return any(mark in low for mark in REFUSALS)


#: Где в запросе к Telegram лежит текст, который стоит одеть.
TEXT_FIELDS = ("text", "caption", "message_text")


def _holders(method: Any) -> list[Any]:
    """Объекты запроса, внутри которых бывает текст сообщения.

    Кроме самого метода — содержимое инлайн-результатов и медиа: текст
    там лежит на этаж глубже, а подпись под карточкой баттла уходит
    именно так.
    """
    out = [method]
    for item in list(getattr(method, "results", None) or []):
        out.append(item)
        content = getattr(item, "input_message_content", None)
        if content is not None:
            out.append(content)
    media = getattr(method, "media", None)
    if media is not None:
        out.extend(media if isinstance(media, list) else [media])
    return out


def _put(holder: Any, field: str, value: str) -> bool:
    """Положить текст обратно в объект запроса.

    Методы у aiogram обычные, а вот типы — `InputMediaPhoto` и его родня
    — заморожены pydantic, и присваивание в них поднимает ValidationError.
    Ровно этим путём уходит подпись под карточкой баттла в инлайне
    (editMessageMedia), и премиум-эмодзи туда молча не доезжали: строка
    не менялась, ошибка гасилась, а снаружи выглядело как «в группе
    работает, в инлайне нет».

    Заморозку обходим через object.__setattr__: поле строковое, валидации
    там всё равно нечего делать, а класть значение мимо pydantic
    безопасно — сериализация читает тот же __dict__.
    """
    try:
        setattr(holder, field, value)
        return True
    except (AttributeError, ValueError, TypeError):
        pass
    try:
        object.__setattr__(holder, field, value)
        return True
    except Exception as error:
        log.debug("не переписать %s.%s: %s", type(holder).__name__, field, error)
        return False


def _rewrite(method: Any, change) -> bool:
    """Пройтись по всем текстам запроса. False — менять было нечего."""
    touched = False
    for holder in _holders(method):
        # Текст без разметки (parse_mode=None) не трогаем: там <tg-emoji>
        # не превратится в эмодзи, а покажется человеку сырыми тегами.
        if hasattr(holder, "parse_mode") and holder.parse_mode is None:
            continue
        for field in TEXT_FIELDS:
            value = getattr(holder, field, None)
            if not isinstance(value, str):
                continue
            fixed = change(value)
            if fixed != value and _put(holder, field, fixed):
                touched = True
    return touched


# --------------------------------------------------------------------------
# Кнопки
# --------------------------------------------------------------------------
#
# В подписи кнопки разметки нет вовсе: текст там голый, и `<tg-emoji>`
# показался бы угловыми скобками. Зато у кнопки есть своё поле
# icon_custom_emoji_id — кастомный эмодзи перед текстом. Ограничение то
# же самое: только боты с купленным на Fragment юзернеймом.


def _buttons(method: Any) -> list[Any]:
    """Кнопки самого сообщения. Клавиатуры инлайн-результатов — мимо.

    Проверено в живом чате: два вызова подряд, один словом «мог», второй
    инлайном. У первого иконка на кнопке есть, у второго кнопка пустая —
    хотя запрос уходит одинаковый. Telegram теряет icon_custom_emoji_id
    именно у клавиатуры инлайн-результата, и молча: ошибки нет, значит
    страховке не на что сработать.

    Раз вернуть иконку там нечем, не трогаем такие кнопки вовсе — пусть
    остаётся обычный эмодзи в подписи. Пустая кнопка хуже простой.
    """
    if type(method).__name__ == "AnswerInlineQuery":
        return []
    out = []
    markup = getattr(method, "reply_markup", None)
    for row in list(getattr(markup, "inline_keyboard", None) or []):
        out.extend(row)
    return out


def dress_buttons(method: Any) -> bool:
    """Перенести эмодзи из начала подписи кнопки в её иконку."""
    touched = False
    for button in _buttons(method):
        text = getattr(button, "text", None)
        if not isinstance(text, str) or getattr(button, "icon_custom_emoji_id", None):
            continue
        # Длинные записи первыми: иначе от «⚔️» отрезался бы «⚔», а
        # невидимый селектор остался бы первым символом подписи.
        for plain in sorted(PACK, key=len, reverse=True):
            choices = PACK[plain]
            if not choices or not text.startswith(plain):
                continue
            emoji_id = random.choice(choices)
            # Эмодзи из текста убираем: иконка рисуется перед подписью, и
            # оставленный символ задвоил бы её.
            if _put(button, "text", text[len(plain):].lstrip() or text) and _put(
                button, "icon_custom_emoji_id", emoji_id
            ):
                touched = True
            break
    return touched


def strip_buttons(method: Any) -> bool:
    """Вернуть эмодзи обратно в подпись кнопки."""
    back = {
        emoji_id: plain for plain, choices in PACK.items() for emoji_id in choices
    }
    touched = False
    for button in _buttons(method):
        icon = getattr(button, "icon_custom_emoji_id", None) or ""
        # Цвет кнопки снимаем заодно: если Telegram отказал, отказал он
        # всему сообщению целиком, и повторная попытка с тем же полем
        # отвалится так же. Кнопка без цвета — кнопка, кнопки нет вовсе —
        # поломка.
        if getattr(button, "style", None):
            _put(button, "style", None)
            touched = True
        if not icon:
            continue
        # Подпись возвращаем только знакомому эмодзи: у иконок, которые
        # ставились сразу премиальными, обычной пары нет, и выдумывать её
        # неоткуда — текст кнопки и так самодостаточен.
        plain = back.get(icon)
        text = getattr(button, "text", "") or ""
        if plain and _put(button, "text", f"{plain} {text}".strip()) and _put(
            button, "icon_custom_emoji_id", None
        ):
            touched = True
        elif not plain and _put(button, "icon_custom_emoji_id", None):
            touched = True
    return touched


class Premium:
    """Прослойка: одевает эмодзи и страхует, если Telegram их не примет.

    Одно место на все сообщения — и на подписи к карточкам, и на инлайн,
    и на то, что напишут позже: иначе новый текст пришлось бы каждый раз
    не забыть обернуть руками.

    Ошибку разбираем по тексту: отдельного кода для «тебе нельзя
    кастомные эмодзи» у Telegram нет, а ронять баттл из-за украшения
    нельзя.
    """

    async def __call__(self, make_request, bot, method):
        global _allowed

        if on():
            if _rewrite(method, lambda text: dress(text) or text):
                _report(method)
            dress_buttons(method)

        try:
            return await make_request(bot, method)
        except Exception as error:
            if not refused(error):
                raise
            # Отказ мог прийти и на текст, и на иконку кнопки: какой
            # именно — Telegram не уточняет, поэтому раздеваем оба.
            undressed = _rewrite(method, strip)
            if not strip_buttons(method) and not undressed:
                raise
            _allowed = False
            log.warning(
                "Telegram не пускает премиум-эмодзи (%s). Отключаю их до "
                "перезапуска: кастомные эмодзи разрешены только ботам, у "
                "которых есть купленный на Fragment юзернейм.",
                error,
            )
            return await make_request(bot, method)


def state() -> dict[str, Any]:
    """Что показать в админке: набор, состояние, куда уже доехало.

    «В чате премиум-эмодзи есть, а в инлайне нет» снаружи неотличимо от
    «на сервере крутится старый код», а лог в живом чате тонет в чужих
    сообщениях. Кнопка отвечает на это за секунду.
    """
    return {
        "own": _loaded,
        "pack": {plain: len(choices) for plain, choices in PACK.items()},
        "on": on(),
        "off_by_telegram": bool(PACK) and config.PREMIUM_EMOJI_ON and not _allowed,
        "sent": sorted(_reported),
    }


def register(bot: Any) -> None:
    """Повесить прослойку на сессию бота.

    Вешаем всегда, а не только при непустом наборе: набор здесь читается
    у Telegram уже после запуска (load), и до того он пуст.
    """
    bot.session.middleware(Premium())
