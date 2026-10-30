"""Юзернеймы: оценка по десятибалльной шкале, проверка и подбор свободных.

Оценка — тот же разбор, что в строке «@username» профиля (naming.py),
только развёрнутый: шесть признаков, у каждого балл из десяти, сверху
общий. Общий считается той же формулой, что и в профиле, — один ник в
обоих местах получает одну цифру.

Проверка свободы перенесена из usernamebot (checker.py), там же
описано, почему одного t.me мало: заглушку «Contact @name» отдают и
зарезервированные имена, правду знает только Fragment.
"""

from __future__ import annotations

import asyncio
import enum
import logging
import math
import random
import re
import time
from dataclasses import dataclass, field

import aiohttp

import config
import fragment
import naming

log = logging.getLogger("profilerate.usernames")

#: Telegram: 5–32 символа, латиница, цифры и подчёркивание, начинается с
#: буквы и не заканчивается подчёркиванием.
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{3,30}[a-z0-9]$")
#: Короче пяти бывает только с аукциона — оценить можно, занять нельзя.
SHORT_RE = re.compile(r"^[a-z][a-z0-9]{0,3}$")

_TME_TAKEN_RE = re.compile(r"tgme_page_photo|tgme_page_title|tgme_page_extra", re.I)
_TME_STUB_RE = re.compile(r"tgme_page_description", re.I)
_TME_TITLE_RE = re.compile(r"<title>([^<]*)</title>", re.I)
_OG_DESC_RE = re.compile(r'og:description"\s+content="([^"]*)"', re.I)
_FRAGMENT_STATUS_RE = re.compile(r"tm-status-(taken|unavail|avail|sale|auction)", re.I)
_PRICE_RE = re.compile(r"(\d[\d\s,]{0,14})\s*TON\b", re.I)

#: Подписи признаков разбора — в том порядке, в каком их отдаёт naming.
LABELS = {
    "слово": "Слово",
    "произносимость": "Произносимость",
    "длина": "Длина",
    "символы": "Символы",
    "набор": "Набор",
    "повторы": "Повторы",
}


class Status(enum.StrEnum):
    FREE = "free"
    TAKEN = "taken"
    RESERVED = "reserved"
    AUCTION = "auction"
    UNKNOWN = "unknown"


STATUS_TEXT = {
    Status.FREE: "свободен",
    Status.TAKEN: "занят",
    Status.RESERVED: "занять нельзя",
    Status.AUCTION: "продаётся на Fragment",
    Status.UNKNOWN: "статус не узнать",
}


@dataclass(slots=True)
class Row:
    title: str
    note: str
    value: float | None


@dataclass(slots=True)
class Rating:
    username: str
    total: float
    rows: list[Row]
    status: Status = Status.UNKNOWN
    #: Цена с Fragment, если имя оттуда, $.
    usd: float = 0.0
    collectible: bool = False
    lot_price: str | None = None
    pros: list[str] = field(default_factory=list)
    cons: list[str] = field(default_factory=list)

    @property
    def headline(self) -> str:
        """Статус и цена одной строкой — под ником на карточке."""
        parts = [STATUS_TEXT[self.status]]
        if self.lot_price:
            parts.append(self.lot_price)
        elif self.usd >= 1:
            parts.append(f"NFT ~${self.usd:,.0f}".replace(",", " "))
        return " · ".join(parts)


def normalize(raw: str) -> str:
    """@Name, t.me/Name, ссылка — всё приводится к чистому имени."""
    value = (raw or "").strip().lower()
    for prefix in ("https://", "http://", "t.me/", "telegram.me/", "@"):
        if value.startswith(prefix):
            value = value[len(prefix):]
    return value.split("?")[0].split("/")[0].strip()


def valid(name: str) -> bool:
    return bool(USERNAME_RE.match(name))


def ratable(name: str) -> bool:
    """Можно ли оценить: обычное имя или короткое аукционное."""
    return valid(name) or bool(SHORT_RE.match(name))


def _verdicts(name: str) -> dict[str, str]:
    """Словами — для подписи под каждой строкой."""
    kind = naming.word_kind(name)[0]
    speech = naming._speech(name)
    digits = any(ch.isdigit() for ch in name)
    ease = naming.typing_ease(name)
    rep = naming.repeats(name)
    if kind == "слово":
        kind = "целое слово"
    return {
        "слово": kind,
        "произносимость": (
            "читается легко" if speech >= 0.8 else "читается средне" if speech >= 0.55
            else "трудно произнести" if speech >= 0.3 else "набор согласных"
        ),
        "длина": f"{len(name)} символов",
        "символы": "есть цифры" if digits else ("подчёркивание" if "_" in name else "только буквы"),
        "набор": (
            "подчёркивание мешает" if "_" in name else "легко ошибиться" if ease <= 0.3
            else "почти без путаницы" if ease < 1 else "диктуется без ошибок"
        ),
        "повторы": "три буквы подряд" if rep <= 0.1 else "есть сдвоенная" if rep < 1 else "без повторов",
    }


def total_score(name: str, collectible: bool = False, usd: float = 0.0) -> float:
    """Общий балл — формула строки «@username» из профиля, без сдвига."""
    base = 2.6 + 5.6 * naming.appeal(name)
    if collectible:
        base += 0.5
    if usd > 0:
        base = max(base, 3.2 + 1.5 * math.log10(1 + usd))
    return round(max(1.0, min(9.9, base)), 2)


def rate_offline(name: str) -> Rating:
    """Оценка без сети: только разбор самого имени."""
    notes = _verdicts(name)
    rows = [
        Row(LABELS[key], notes[key], round(1 + 9 * value, 1))
        for key, value, _ in naming.breakdown(name)
    ]
    rating = Rating(username=name, total=total_score(name), rows=rows)
    for row in rows:
        (rating.pros if row.value >= 7 else rating.cons if row.value <= 5 else []).append(
            f"{row.title} — {row.note}"
        )
    return rating


# --------------------------------------------------------------------------
# Сеть
# --------------------------------------------------------------------------

_session: aiohttp.ClientSession | None = None


def _http() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            # Прокси из HTTPS_PROXY: дома Telegram и Fragment бывают
            # доступны только через локальный VPN.
            trust_env=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; profilerate)", "Accept": "text/html"},
            timeout=aiohttp.ClientTimeout(total=config.HTTP_TIMEOUT),
        )
    return _session


async def close() -> None:
    if _session is not None and not _session.closed:
        await _session.close()


async def _fetch(url: str) -> tuple[int, str]:
    async with _http().get(url, allow_redirects=False) as response:
        body = await response.text(errors="ignore") if response.status == 200 else ""
        return response.status, body


async def _telegram(name: str) -> str:
    """`profile` — занято, `contact` — похоже на свободное, `unknown`."""
    try:
        status, body = await _fetch(f"https://t.me/{name}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        log.debug("t.me/%s недоступен: %s", name, error)
        return "unknown"
    if status != 200:
        return "unknown"
    if _TME_TAKEN_RE.search(body):
        return "profile"
    described = _OG_DESC_RE.search(body)
    if described and described.group(1).strip():
        return "profile"
    title = _TME_TITLE_RE.search(body)
    if not title or f"@{name}" not in title.group(1).lower():
        return "unknown"
    if "contact" not in title.group(1).lower() or not _TME_STUB_RE.search(body):
        return "unknown"
    return "contact"


async def _fragment(name: str) -> tuple[str, str | None]:
    """Статус на Fragment и цена лота. `absent` — Fragment имени не знает."""
    try:
        status, body = await _fetch(f"https://fragment.com/username/{name}")
    except (aiohttp.ClientError, asyncio.TimeoutError) as error:
        log.debug("fragment/%s недоступен: %s", name, error)
        return "unknown", None
    if status in (301, 302, 303, 307, 308, 404):
        return "absent", None
    if status != 200:
        return "unknown", None
    match = _FRAGMENT_STATUS_RE.search(body)
    if not match:
        return "absent", None
    kind = match.group(1).lower()
    price = None
    if kind in ("avail", "sale", "auction"):
        found = _PRICE_RE.search(body)
        if found:
            price = f"{' '.join(found.group(1).split())} TON"
    return kind, price


#: Проверенные имена: имя → (статус, цена лота, когда).
_checked: dict[str, tuple[Status, str | None, float]] = {}


async def check(name: str) -> tuple[Status, str | None]:
    """Свести t.me и Fragment в один вердикт. Кэш — CHECK_TTL секунд."""
    cached = _checked.get(name)
    if cached and time.time() - cached[2] < config.CHECK_TTL:
        return cached[0], cached[1]
    if not valid(name):
        # Короче пяти — только аукцион: обычной регистрацией не занять.
        frag, price = await _fragment(name) if SHORT_RE.match(name) else ("absent", None)
        status = Status.AUCTION if frag in ("avail", "sale", "auction") else Status.RESERVED
        return status, price

    tg, (frag, price) = await asyncio.gather(_telegram(name), _fragment(name))
    if tg == "profile" or frag == "taken":
        status = Status.TAKEN
    elif frag in ("avail", "sale", "auction"):
        status = Status.AUCTION
    elif frag == "unavail":
        status = Status.RESERVED
    elif tg == "contact" and frag == "absent":
        status = Status.FREE
    else:
        status = Status.UNKNOWN
    if status is not Status.UNKNOWN:
        if len(_checked) > 5000:
            _checked.clear()
        _checked[name] = (status, price, time.time())
    return status, price


async def rate(name: str) -> Rating:
    """Полная оценка: разбор, статус и цена с Fragment."""
    rating = rate_offline(name)
    (status, price), found = await asyncio.gather(check(name), _look(name))
    rating.status, rating.lot_price = status, price
    rating.collectible = found.collectible
    rating.usd = found.usd if found.collectible else 0.0
    rating.total = total_score(name, rating.collectible, rating.usd)
    return rating


async def _look(name: str) -> fragment.Name:
    try:
        return await asyncio.wait_for(fragment.look(name), config.FRAGMENT_TIMEOUT * 2 + 1)
    except asyncio.TimeoutError:
        return fragment.Name()


# --------------------------------------------------------------------------
# Подбор свободных
# --------------------------------------------------------------------------

VOWELS = "aeiou"
CONSONANTS = "bcdfghklmnprstvz"
PREFIXES = ("the", "its", "my", "im", "get", "go", "mr", "real", "hey", "x")
SUFFIXES = (
    "x", "o", "y", "ly", "io", "ex", "on", "ix", "us", "ra", "hq", "tg", "app",
    "pro", "dev", "lab", "club", "zone", "verse", "ism", "er", "ik",
)


def _pronounceable(length: int) -> str:
    return "".join(
        random.choice(CONSONANTS if i % 2 == 0 else VOWELS) for i in range(length)
    )


def _semi(length: int) -> str:
    """Случайные буквы с гласными на случайных местах — короткие строгие
    чередования разобраны подчистую (замеры usernamebot)."""
    slots = random.sample(range(length), max(1, length // 3))
    return "".join(
        random.choice(VOWELS if i in slots else CONSONANTS) for i in range(length)
    )


def candidates(seed: str, count: int) -> list[str]:
    """Кандидаты вокруг слова, а без слова — короткие произносимые."""
    seed = re.sub(r"[^a-z0-9]", "", normalize(seed))
    out: list[str] = []
    if seed:
        if seed[0].isdigit():
            seed = "x" + seed
        out.append(seed)
        out += [seed + s for s in SUFFIXES]
        out += [p + seed for p in PREFIXES]
        out += [seed + seed[-1], seed[:1] + seed, seed + "_" + random.choice(("tg", "hq", "x"))]
        last_vowel = max((i for i, ch in enumerate(seed) if ch in VOWELS), default=-1)
        if last_vowel > 0:
            # Подменить последнюю гласную: storm → stirm, starm.
            for vowel in VOWELS:
                out.append(seed[:last_vowel] + vowel + seed[last_vowel + 1:])
        rest = out[1:]
        random.shuffle(rest)
        out = [seed] + rest
        out +=[seed + str(random.randint(1, 99)) for _ in range(3)]
    else:
        for _ in range(count * 2):
            length = random.choice((5, 6, 6, 7))
            out.append(_pronounceable(length) if random.random() < 0.4 else _semi(length))
    seen: list[str] = []
    for name in out:
        if valid(name) and name not in seen:
            seen.append(name)
    return seen[: count * 3]


async def search(seed: str, want: int = 6, budget: float | None = None) -> list[Rating]:
    """Свободные имена вокруг слова, лучшие сверху.

    Проверяем пачкой с ограничением параллельности и общим временем:
    инлайн-запрос ждёт ответа считанные секунды, и лучше отдать четыре
    найденных, чем ничего.
    """
    pool = candidates(seed, want)
    gate = asyncio.Semaphore(config.SEARCH_PARALLEL)
    found: list[str] = []

    async def one(name: str) -> None:
        if len(found) >= want:
            return
        async with gate:
            if len(found) >= want:
                return
            status, _ = await check(name)
            if status is Status.FREE:
                found.append(name)

    tasks = [asyncio.ensure_future(one(name)) for name in pool]
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True), budget or config.SEARCH_BUDGET
        )
    except asyncio.TimeoutError:
        for task in tasks:
            task.cancel()
    ratings = [rate_offline(name) for name in found]
    for rating in ratings:
        rating.status = Status.FREE
    return sorted(ratings, key=lambda r: r.total, reverse=True)[:want]
