"""Настройки оценщика. Всё читается из .env, у всего есть значение по умолчанию.

Формулы метрик взяты из Мог Батла как есть: один и тот же профиль в
обоих ботах получает одинаковые баллы по каждой строке.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = Path(os.getenv("DATA_DIR") or BASE_DIR / "data")
DATA_DIR.mkdir(parents=True, exist_ok=True)


def _ints(raw: str | None) -> tuple[int, ...]:
    return tuple(int(part) for part in (raw or "").replace(" ", "").split(",") if part)


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS = _ints(os.getenv("ADMIN_IDS"))

#: Прокси до Telegram (например http://127.0.0.1:1080 у локального VPN).
#: Пусто — берётся из HTTPS_PROXY, если он задан в системе.
PROXY = (os.getenv("PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or "").strip()


def live_proxy() -> str:
    """PROXY, если он отвечает; иначе пусто — и бот идёт в Telegram напрямую.

    Прокси нужен дома за VPN, а на хостинге его нет. Если .env залили туда
    целиком вместе со строкой PROXY, без этой проверки бот падал на старте:
    «Couldn't connect to proxy 127.0.0.1:…».
    """
    if not PROXY:
        return ""
    import logging
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(PROXY if "://" in PROXY else f"http://{PROXY}")
    try:
        socket.create_connection((parts.hostname, parts.port or 80), timeout=3).close()
        return PROXY
    except OSError:
        logging.getLogger("profilerate").warning(
            "прокси %s не отвечает — работаю без него, напрямую", PROXY
        )
        return ""

#: Склад картинок для инлайна: приватный канал, где бот админ (id вида
#: -100…). Инлайн-фото Telegram берёт только по file_id, поэтому карточку
#: сначала надо куда-то залить. Пусто — бот заливает в личку владельца
#: (первый из ADMIN_IDS) и сразу удаляет сообщение.
STORAGE_CHAT_ID = int(os.getenv("STORAGE_CHAT_ID") or 0)

#: Подпись в углу карточки.
BRAND = os.getenv("BRAND", "SOLUTIONS")

#: Сколько секунд между двумя оценками от одного человека.
RATE_COOLDOWN = float(os.getenv("RATE_COOLDOWN", "10"))

# --- Премиум-эмодзи ---------------------------------------------------------

#: Набор премиум-эмодзи студии (t.me/addemoji/ИМЯ). Бот читает его у
#: Telegram при запуске: эмодзи в тексте и иконки кнопок берутся оттуда.
SOLUTIONS_PACK = "solutions_project_by_Solutmark_bot"
#: Если свой пак создан руками через @Stickers — впиши сюда его имя (из
#: ссылки t.me/addemoji/ИМЯ): бот возьмёт иконки оттуда и сам пытаться
#: создать пак перестанет.
PREMIUM_PACK = os.getenv("PREMIUM_PACK", SOLUTIONS_PACK)
PREMIUM_EMOJI: dict[str, tuple[str, ...]] = {}
#: Как часто бот пробует создать свой пак (rateme_icons_by_<бот>), пока
#: Telegram держит создание под флуд-лимитом, секунд.
PACK_RETRY = int(os.getenv("PACK_RETRY", str(6 * 3600)))
#: Значки набора Solutions, которые не брать: там они — плашки с надписью.
SOLUTIONS_SKIP = ("🏷", "💯", "🔙")
#: Чей аккаунт станет владельцем своего пака. Пусто — первый из ADMIN_IDS.
#: Отдельно потому, что Telegram держит создание наборов под лимитом на
#: владельце: если с аккаунта админа их создают другие боты, лимит не
#: остывает, и владельцем лучше поставить другой аккаунт (он должен
#: хоть раз написать боту /start).
PACK_OWNER = int(os.getenv("PACK_OWNER") or 0)
PREMIUM_EMOJI_ON = os.getenv("PREMIUM_EMOJI_ON", "1").strip() not in ("0", "", "off")

# --- Fragment: цена ника -----------------------------------------------------

FRAGMENT_CHECK = os.getenv("FRAGMENT_CHECK", "1").strip() not in ("0", "", "off")
FRAGMENT_TIMEOUT = float(os.getenv("FRAGMENT_TIMEOUT", "6.0"))
FRAGMENT_TTL = int(os.getenv("FRAGMENT_TTL", "86400"))
FRAGMENT_PRICE = os.getenv("FRAGMENT_PRICE", "1").strip() not in ("0", "", "off")
TON_USD_FALLBACK = float(os.getenv("TON_USD_FALLBACK", "3.1"))

# --- Юзернеймы: проверка и подбор ----------------------------------------------

HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "6"))
#: Сколько секунд помнить статус проверенного имени.
CHECK_TTL = int(os.getenv("CHECK_TTL", "900"))
#: Сколько имён проверять одновременно при подборе.
SEARCH_PARALLEL = int(os.getenv("SEARCH_PARALLEL", "6"))
#: Сколько секунд на подбор в инлайне и в личке.
SEARCH_BUDGET = float(os.getenv("SEARCH_BUDGET", "7"))
#: В инлайне меньше: Telegram ждёт ответа на запрос считанные секунды.
INLINE_BUDGET = float(os.getenv("INLINE_BUDGET", "4.5"))

# --- Подарки ------------------------------------------------------------------

#: Сколько стоит звезда в долларах — для строки «Стоимость».
USD_PER_STAR = float(os.getenv("USD_PER_STAR", "0.015"))
#: Во сколько звёзд оценивать NFT-подарок. Bot API не отдаёт цену с
#: рынка, поэтому берётся средняя, а редкость признаков её поднимает.
NFT_STARS = int(os.getenv("NFT_STARS", "1200"))
RARITY_BOOST = float(os.getenv("RARITY_BOOST", "5.0"))
#: Сколько страниц подарков читать (по 100 штук).
GIFT_PAGES = int(os.getenv("GIFT_PAGES", "5"))

# --- Балл ---------------------------------------------------------------------

#: Веса метрик в общем балле — те же, что в Мог Батле.
WEIGHTS: dict[str, float] = {
    "avatar": float(os.getenv("W_AVATAR", "0.18")),
    "username": float(os.getenv("W_USERNAME", "0.20")),
    "bio": float(os.getenv("W_BIO", "0.07")),
    "gifts": float(os.getenv("W_GIFTS", "0.18")),
    "value": float(os.getenv("W_VALUE", "0.18")),
    "registered": float(os.getenv("W_REGISTERED", "0.10")),
    "level": float(os.getenv("W_LEVEL", "0.09")),
}

#: Насколько растягивать общий балл вокруг середины шкалы.
SCORE_SPREAD = float(os.getenv("SCORE_SPREAD", "1.35"))


# --- Батл на призы (мини-апп) ---------------------------------------------------

DB_PATH = Path(os.getenv("DB_PATH") or DATA_DIR / "battle.db")
#: Адрес мини-аппа (https, без слеша на конце). Пусто — кнопки «Батл» нет.
WEBAPP_URL = (os.getenv("WEBAPP_URL") or "").strip().rstrip("/")
WEBAPP_DIR = BASE_DIR / "webapp"
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8080"))
#: Сколько секунд живёт подпись initData — дольше не пускаем.
INITDATA_TTL = int(os.getenv("INITDATA_TTL", "86400"))


def _int_list(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.replace(" ", "").split(",") if part)


#: Доля кассы с каждого банка, %.
HOUSE_PERCENT = int(os.getenv("HOUSE_PERCENT", "15"))
#: Взнос в турнир недели, звёзд.
TOURNEY_FEE = int(os.getenv("TOURNEY_FEE", "25"))
#: Как делится призовой банк турнира между местами, доли.
TOURNEY_SPLIT = _int_list(os.getenv("TOURNEY_SPLIT", "50,30,20"))
#: Когда кончается неделя: день (0 — понедельник, 6 — воскресенье) и час UTC.
TOURNEY_END_WEEKDAY = int(os.getenv("TOURNEY_END_WEEKDAY", "6"))
TOURNEY_END_HOUR = int(os.getenv("TOURNEY_END_HOUR", "18"))
#: Ставки дуэлей профилей и ников, звёзд.
DUEL_STAKES = _int_list(os.getenv("DUEL_STAKES", "10,25,50,100"))
NICK_STAKES = _int_list(os.getenv("NICK_STAKES", "10,25,50,100"))
#: Сколько заявок может висеть у одного человека разом.
MAX_WAITING = int(os.getenv("MAX_WAITING", "3"))
#: Через сколько секунд заявку без соперника отозвать с возвратом.
DUEL_TTL = int(os.getenv("DUEL_TTL", str(24 * 3600)))
#: Как часто можно переоценить профиль для батла, секунд.
SCORE_TTL = int(os.getenv("SCORE_TTL", "3600"))
#: Кнопки пополнения, звёзд.
TOPUP_AMOUNTS = _int_list(os.getenv("TOPUP_AMOUNTS", "25,50,100,250,500"))
#: Меньше этого не вывести — дешевле подарка в Telegram нет.
WITHDRAW_MIN = int(os.getenv("WITHDRAW_MIN", "15"))


def check() -> None:
    """Ранняя проверка: без токена стартовать бессмысленно."""
    if not BOT_TOKEN:
        raise SystemExit(
            "Не задан BOT_TOKEN. Скопируй .env.example в .env и впиши токен "
            "от @BotFather."
        )
