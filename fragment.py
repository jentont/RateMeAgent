"""Юзернейм на Fragment: коллекционный ли он и сколько такие стоят.

Короткий ник и купленный на аукционе — разные вещи. Пятибуквенное имя
может достаться случайно, а вот `@auto` или `@crypto` кто-то купил на
Fragment за тысячи долларов, и на карточке это должно быть видно.

Telegram такого признака не отдаёт нигде: ни Bot API, ни MTProto не
знают, откуда у человека имя. Зато Fragment публикует статус каждого
юзернейма открытой страницей, и статусы различимы:

    tm-status-taken">Taken        — обычное имя, занято в Telegram
    tm-status-unavail">Sold       — продано на аукционе, это NFT
    tm-status-avail">Available    — свободно

Одного флага «коллекционное» мало. Под ним лежит и `@auto` за десятки
тысяч долларов, и трёхсловный мусор, ушедший за минимальную ставку, — а
надбавка к баллу им доставалась одинаковая. Поэтому рядом со статусом
берётся цена: Fragment открыто публикует историю продаж, и по сделкам с
похожими именами той же длины видно, сколько имя стоит на самом деле.

Запрос уходит не чаще одного раза на имя в сутки: статус меняется редко,
а лишний поход в сеть посреди баттла стоит людям ожидания.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

import config
import naming

log = logging.getLogger("profilerate.fragment")

URL = "https://fragment.com/username/{name}"
SEARCH_URL = "https://fragment.com/?query={query}&type=usernames&filter=sold"

#: Статус со страницы. Забираем именно класс, а не текст: текст
#: переводится и меняется, класс держится годами.
STATUS = re.compile(r'tm-status-(taken|unavail|avail)"[^>]*>([^<]*)', re.IGNORECASE)

#: Что считаем коллекционным. «Sold» — продан, «On auction» и «For sale»
#: — выставлен, но всё равно уже вынесен на Fragment, то есть NFT.
COLLECTIBLE = ("sold", "auction", "sale")

#: Строка таблицы проданных: имя, цена в TON, дата сделки.
ROW_RE = re.compile(r'<tr class="tm-row-selectable">(.*?)</tr>', re.S)
NAME_RE = re.compile(r"/username/([a-z0-9_]+)\"")
PRICE_RE = re.compile(r'icon-ton">([\d,\s]+)</div>')

#: Источники курса TON. Двух хватает: они независимы, и когда один
#: недоступен из сети хостинга, второй обычно отвечает.
RATE_SOURCES = (
    (
        "https://api.coingecko.com/api/v3/simple/price"
        "?ids=the-open-network&vs_currencies=usd",
        lambda data: data["the-open-network"]["usd"],
    ),
    (
        "https://tonapi.io/v2/rates?tokens=ton&currencies=usd",
        lambda data: data["rates"]["TON"]["prices"]["USD"],
    ),
)


@dataclass(slots=True)
class Sale:
    """Состоявшаяся продажа имени."""

    username: str
    ton: float

    @property
    def length(self) -> int:
        return len(self.username)


@dataclass(slots=True)
class Name:
    """Что Fragment знает про имя."""

    username: str = ""
    #: Куплено на аукционе, а не занято обычной регистрацией.
    collectible: bool = False
    #: Во сколько рынок оценивает имя, TON. Ноль — неизвестно.
    ton: float = 0.0
    #: Цена самой этой продажи, а не прикидка по аналогам.
    exact: bool = False

    @property
    def usd(self) -> float:
        return self.ton * rate()


#: Ответы Fragment: имя → (что узнали, когда).
_cache: dict[str, tuple[Name, float]] = {}
_session = None
_lock = asyncio.Lock()

_rate: float = 0.0
_rate_at: float = 0.0


async def _http():
    """Ленивая сессия. aiohttp тянется вместе с aiogram, ставить нечего."""
    global _session
    if _session is None:
        import aiohttp

        _session = aiohttp.ClientSession(
            # Прокси из HTTPS_PROXY: дома Telegram и Fragment бывают
            # доступны только через локальный VPN.
            trust_env=True,
            timeout=aiohttp.ClientTimeout(total=config.FRAGMENT_TIMEOUT),
            headers={"User-Agent": "Mozilla/5.0 (compatible; profilerate)"},
        )
    return _session


async def close() -> None:
    global _session
    if _session is not None:
        await _session.close()
        _session = None


async def _get(url: str) -> str | None:
    """Страница по адресу. Одна повторная попытка — и хватит.

    Соединение с Fragment иногда рвётся на рукопожатии TLS, и вторая
    попытка обычно проходит. Больше двух не делаем: человек в чате ждёт
    карточку, а метрика без надбавки — не катастрофа.
    """
    for attempt in range(2):
        try:
            session = await _http()
            async with _lock:
                async with session.get(url) as response:
                    if response.status != 200:
                        log.debug("Fragment ответил %s на %s", response.status, url)
                        return None
                    return await response.text(errors="ignore")
        except Exception as error:
            log.debug("Fragment недоступен (%s, попытка %s): %s", url, attempt + 1, error)
    return None


# --------------------------------------------------------------------------
# Курс TON
# --------------------------------------------------------------------------


def rate() -> float:
    """Последний известный курс TON. Никогда не ноль."""
    return _rate or config.TON_USD_FALLBACK


async def refresh_rate() -> float:
    """Обновить курс, но не чаще раза в час.

    Запасное значение из настроек — это не «текущий курс», а цифра,
    вписанная когда-то руками: она стареет, и жить на ней постоянно
    значит ошибаться в цене имён вдвое.
    """
    global _rate, _rate_at
    if _rate and time.time() - _rate_at < 3600:
        return _rate

    for url, pick in RATE_SOURCES:
        body = await _get(url)
        if not body:
            continue
        try:
            import json

            value = float(pick(json.loads(body)))
        except Exception as error:
            log.debug("Курс TON: %s ответил непонятным (%s)", url, error)
            continue
        if value > 0:
            _rate, _rate_at = value, time.time()
            log.info("Курс TON обновлён: $%.2f", value)
            return _rate

    # Чтобы не ходить за курсом на каждый баттл, когда сеть закрыта.
    _rate_at = time.time()
    return rate()


# --------------------------------------------------------------------------
# Статус и цена
# --------------------------------------------------------------------------


def parse(body: str) -> bool:
    """Коллекционное ли имя по странице Fragment."""
    found = STATUS.search(body)
    if not found:
        return False
    label = (found.group(2) or "").strip().lower()
    return found.group(1).lower() == "unavail" and any(
        mark in label for mark in COLLECTIBLE
    )


def parse_sales(body: str) -> list[Sale]:
    """Продажи из HTML поисковой выдачи."""
    out: list[Sale] = []
    for chunk in ROW_RE.findall(body):
        name = NAME_RE.search(chunk)
        price = PRICE_RE.search(chunk)
        if not (name and price):
            continue
        try:
            ton = float(price.group(1).replace(",", "").replace(" ", ""))
        except ValueError:
            continue
        out.append(Sale(name.group(1), ton))
    return out


def price_of(username: str, sales: list[Sale]) -> tuple[float, bool]:
    """Оценка имени по продажам. Цена в TON и «это точная сделка».

    Само имя в выдаче — лучший из возможных ответов: за него уже
    заплатили именно столько. Если его там нет, считаем по соседям.

    Соседей отбираем сперва по длине, потом по качеству. Иначе выборка
    двугорбая: по корню `joi` находятся и шесть мусорных сделок по 7–19
    TON, и три словарных по 100–644, а середина между горбами — цена, по
    которой не продалось ничего.
    """
    for sale in sales:
        if sale.username == username:
            return sale.ton, True

    same = [s for s in sales if s.length == len(username)]
    pool = same or [s for s in sales if abs(s.length - len(username)) <= 1]
    if not pool:
        return 0.0, False

    quality = naming.appeal(username)
    close = pool
    for window in (0.15, 0.25, 0.4):
        picked = [s for s in pool if abs(naming.appeal(s.username) - quality) <= window]
        if len(picked) >= 3:
            close = picked
            break

    prices = sorted(s.ton for s in close)
    # Медиана, а не среднее: одна сделка на 60 000 TON среди пятибуквенных
    # утащила бы оценку соседей к небесам.
    middle = len(prices) // 2
    if len(prices) % 2:
        return prices[middle], False
    return (prices[middle - 1] + prices[middle]) / 2, False


async def _sales(query: str) -> list[Sale]:
    if not query:
        return []
    body = await _get(SEARCH_URL.format(query=query))
    return parse_sales(body) if body else []


async def _price(name: str) -> tuple[float, bool]:
    """Сколько стоит имя по рынку. Ноль — сравнить не с чем.

    Корень из первых четырёх букв: у Fragment поиск по подстроке, и
    запрос короче тонет в мусоре вроде тридцатисимвольных имён с
    подчёркиваниями, а длиннее чаще всего не находит ничего.
    """
    stem = name[: max(3, min(4, len(name) - 1))]
    sales = await _sales(stem)
    if len(sales) < 5 and len(stem) > 3:
        wider = await _sales(stem[:3])
        seen = {s.username for s in sales}
        sales += [s for s in wider if s.username not in seen]
    return price_of(name, sales)


async def look(username: str | None) -> Name:
    """Что известно про имя: коллекционное ли и почём такие уходят.

    Сомнение трактуем в пользу «обычное»: приписать человеку NFT,
    которого у него нет, хуже, чем не заметить настоящий. Сеть у
    хостингов бывает закрыта, и тогда метрика просто останется без
    надбавки.
    """
    if not username or not config.FRAGMENT_CHECK:
        return Name()

    name = username.lstrip("@").lower()
    cached = _cache.get(name)
    if cached and time.time() - cached[1] < config.FRAGMENT_TTL:
        return cached[0]

    body = await _get(URL.format(name=name))
    if body is None:
        return Name(username=name)

    found = Name(username=name, collectible=parse(body))
    # В кэш кладём сразу: статус — главное, а цена допишется в этот же
    # объект, хоть сейчас, хоть фоновой задачей.
    _cache[name] = (found, time.time())

    if found.collectible:
        log.info("@%s — коллекционный юзернейм", name)
        if config.FRAGMENT_PRICE:
            await _wait_price(found)
    return found


async def _fill(found: Name) -> None:
    """Дописать цену в уже выданный объект."""
    try:
        await refresh_rate()
        found.ton, found.exact = await _price(found.username)
    except Exception as error:
        log.debug("цена @%s не узналась: %s", found.username, error)
        return
    log.info(
        "@%s оценён: %s",
        found.username,
        f"~{found.ton:,.0f} TON (${found.usd:,.0f})" if found.ton else "не с чем сравнить",
    )


async def _wait_price(found: Name) -> None:
    """Узнать цену, но не задерживать баттл.

    Поход за ценой — это ещё два запроса поверх страницы статуса, и все
    они идут, пока человек смотрит на нажатую кнопку. Ждём столько же,
    сколько на любой другой запрос к Fragment; не успели — оставляем
    задачу дописывать в фоне. Объект уже лежит в кэше, так что к
    следующему баттлу цена окажется на месте, а этот не встанет.
    """
    task = asyncio.ensure_future(_fill(found))
    try:
        await asyncio.wait_for(asyncio.shield(task), config.FRAGMENT_TIMEOUT)
    except asyncio.TimeoutError:
        log.debug("цена @%s досчитается в фоне", found.username)


async def collectible(username: str | None) -> bool:
    """Куплен ли ник на Fragment. False — обычный или проверить не вышло."""
    return (await look(username)).collectible
