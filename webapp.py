"""Сервер мини-аппа «Батл на призы»: статика и API.

Кто спрашивает — узнаём только по initData, подписанной токеном бота
(проверка перенесена из rassylka). id, ник и имя берутся из неё, а не из
тела запроса: иначе ставить в дуэли ников можно было бы чужим @durov.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
import urllib.parse
from functools import wraps

from aiogram import Bot
from aiohttp import web

import battle
import collect
import config
import db
import payouts
import usernames
from scoring import METRICS, Score, evaluate, subtitle

log = logging.getLogger("profilerate.webapp")

INIT_HEADER = "X-Init-Data"
_bot: Bot | None = None

#: Последняя оценка для батла: id → (балл профиля, строки, когда).
_scores: dict[int, tuple[Score, float]] = {}
_nicks: dict[str, tuple[usernames.Rating, float]] = {}


def use_bot(bot: Bot) -> None:
    global _bot
    _bot = bot


# --- initData -----------------------------------------------------------------


def parse_init_data(raw: str) -> dict | None:
    """Проверить подпись initData и вернуть человека. None — не сошлось."""
    if not raw or not config.BOT_TOKEN:
        return None
    try:
        fields = dict(urllib.parse.parse_qsl(raw, keep_blank_values=True))
    except ValueError:
        return None
    got = fields.pop("hash", "")
    if not got:
        return None
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    for drop in ({"hash"}, {"hash", "signature"}):
        check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()) if k not in drop)
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, got):
            break
    else:
        return None
    try:
        auth_date = int(fields.get("auth_date", "0"))
        user = json.loads(fields.get("user") or "{}")
    except (ValueError, json.JSONDecodeError):
        return None
    if config.INITDATA_TTL and time.time() - auth_date > config.INITDATA_TTL:
        return None
    if not isinstance(user, dict) or not user.get("id"):
        return None
    return user


def _name(user: dict) -> str:
    return " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or "Игрок"


def authed(handler):
    @wraps(handler)
    async def wrapper(request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except (json.JSONDecodeError, ValueError):
            data = {}
        data = data if isinstance(data, dict) else {}
        user = parse_init_data(request.headers.get(INIT_HEADER) or data.get("init_data") or "")
        if user is None:
            return web.json_response(
                {"ok": False, "error": "Открой приложение из бота заново."}, status=401
            )
        await db.touch(int(user["id"]), user.get("username"), _name(user))
        try:
            return await handler(request, user, data)
        except (battle.Refused, payouts.Refused) as refusal:
            return web.json_response({"ok": False, "error": str(refusal)})

    return wrapper


def _ok(**payload) -> web.Response:
    return web.json_response({"ok": True, **payload})


def _int(data: dict, key: str, default: int = 0) -> int:
    try:
        return int(data.get(key, default))
    except (TypeError, ValueError):
        return default


# --- оценки для батла ------------------------------------------------------------


async def profile_score(user: dict, fresh: bool = False) -> Score:
    """Балл профиля из 10. Переоценка — не чаще SCORE_TTL.

    Старше SCORE_TTL оценка не живёт: иначе можно было бы оцениться с
    полной витриной подарков, раздать их и ещё неделю ставить старым баллом.
    """
    uid = int(user["id"])
    cached = _scores.get(uid)
    if cached and time.time() - cached[1] < config.SCORE_TTL:
        return cached[0]
    profile = await collect.fetch(_bot, uid, user.get("username"), _name(user))
    score = evaluate(profile)
    _scores[uid] = (score, time.time())
    return score


async def nick_score(username: str | None) -> usernames.Rating | None:
    if not username:
        return None
    name = username.lower()
    cached = _nicks.get(name)
    if cached and time.time() - cached[1] < config.SCORE_TTL:
        return cached[0]
    rating = await usernames.rate(name)
    _nicks[name] = (rating, time.time())
    return rating


def _score_view(score: Score) -> dict:
    p = score.profile
    uid = p.user_id
    left = config.SCORE_TTL - (time.time() - _scores.get(uid, (None, 0))[1])
    return {
        "total": score.total,
        "refresh_in": max(0, int(left)),
        "rows": [
            {
                "title": title,
                "note": subtitle(key, p),
                "value": None if key in p.guessed else score.parts[key],
            }
            for key, title in METRICS
        ],
    }


def _nick_view(rating: usernames.Rating | None) -> dict | None:
    if rating is None:
        return None
    return {
        "username": rating.username,
        "total": rating.total,
        "headline": rating.headline,
        "rows": [{"title": r.title, "note": r.note, "value": r.value} for r in rating.rows],
    }


# --- API ---------------------------------------------------------------------------


@authed
async def api_state(request, user, data):
    uid = int(user["id"])
    score = await profile_score(user)
    nick = await nick_score(user.get("username"))
    waiting = await db.my_waiting(uid)
    return _ok(
        me={
            "id": uid,
            "name": _name(user),
            "username": user.get("username"),
            "photo": user.get("photo_url"),
            "balance": await db.balance(uid),
        },
        score=_score_view(score),
        nick=_nick_view(nick),
        tourney=await battle.tourney_view(uid),
        queues={kind: await db.queue_sizes(kind, uid) for kind in battle.KINDS},
        waiting=[
            {"id": row["id"], "kind": row["kind"], "stake": row["stake"], "created": row["created"]}
            for row in waiting
        ],
        rules={
            "house": config.HOUSE_PERCENT,
            "fee": config.TOURNEY_FEE,
            "split": list(config.TOURNEY_SPLIT),
            "duel_stakes": list(config.DUEL_STAKES),
            "nick_stakes": list(config.NICK_STAKES),
            "topups": list(config.TOPUP_AMOUNTS),
            "withdraw_min": config.WITHDRAW_MIN,
            "duel_ttl_h": config.DUEL_TTL // 3600,
        },
    )


@authed
async def api_rescore(request, user, data):
    """Переоценить профиль (и обновить балл в турнире, если человек там)."""
    uid = int(user["id"])
    cached = _scores.get(uid)
    if cached and time.time() - cached[1] < config.SCORE_TTL:
        left = int(config.SCORE_TTL - (time.time() - cached[1]))
        raise battle.Refused(f"Переоценить можно через {left // 60 + 1} мин.")
    score = await profile_score(user, fresh=True)
    t = await battle.current_tourney()
    if await db.entry(t.id, uid):
        await db.put_entry(t.id, uid, score.total)
    return _ok(score=_score_view(score), tourney=await battle.tourney_view(uid))


@authed
async def api_tourney(request, user, data):
    return _ok(tourney=await battle.tourney_view(int(user["id"]), max(0, _int(data, "offset"))))


@authed
async def api_tourney_join(request, user, data):
    uid = int(user["id"])
    score = await profile_score(user)
    await battle.join_tourney(uid, score.total)
    return _ok(tourney=await battle.tourney_view(uid), balance=await db.balance(uid))


@authed
async def api_duel_join(request, user, data):
    uid = int(user["id"])
    kind = str(data.get("kind") or "")
    stake = _int(data, "stake")
    if kind == "nick":
        rating = await nick_score(user.get("username"))
        if rating is None:
            raise battle.Refused("Для дуэли ников нужен @username в профиле Telegram.")
        score, label = rating.total, "@" + rating.username
    else:
        score_obj = await profile_score(user)
        score, label = score_obj.total, _name(user)
    await _recheck_rival(kind, stake, uid)
    outcome = await battle.join_duel(kind, stake, uid, score, label)
    if outcome.matched and outcome.opponent_id:
        await _notify_opponent(outcome, kind, stake, label)
    return _ok(
        outcome={
            "id": outcome.duel_id,
            "matched": outcome.matched,
            "result": None if not outcome.matched else (
                "draw" if outcome.winner is None else "win" if outcome.winner == uid else "lose"
            ),
            "prize": outcome.prize if outcome.winner == uid else 0,
            "my_score": outcome.my_score,
            "their_score": outcome.their_score,
            "their_label": outcome.their_label,
        },
        balance=await db.balance(uid),
    )


async def live_score(user_id: int) -> float | None:
    """Балл профиля прямо сейчас — для проверки ждущих и призёров."""
    row = await db.user(user_id)
    if row is None or _bot is None:
        return None
    try:
        profile = await collect.fetch(_bot, user_id, row["username"], row["name"])
    except Exception as error:
        log.info("переоценить %s не вышло: %s", user_id, error)
        return None
    score = evaluate(profile)
    _scores[user_id] = (score, time.time())
    return score.total


async def _recheck_rival(kind: str, stake: int, uid: int) -> None:
    """Перед боем проверить того, кто ждёт: не растерял ли он профиль.

    Балл в заявке заморожен, и пока она висит, можно раздать подарки или
    продать ник. Поэтому в бой идёт меньшее из «тогда» и «сейчас», а
    заявку на ник, который больше не принадлежит заявителю, отзываем.
    """
    rival = await db.waiting_duel(kind, stake, uid)
    if rival is None:
        return
    if kind == "nick":
        chat = await collect._chat(_bot, rival["a_id"]) if _bot else None
        owned = (getattr(chat, "username", None) or "").lower()
        if chat is not None and "@" + owned != str(rival["a_label"]).lower():
            try:
                await battle.cancel_duel(rival["id"], rival["a_id"])
                log.info("заявка #%s отозвана: ника %s у заявителя больше нет", rival["id"], rival["a_label"])
            except battle.Refused:
                pass
        return
    now = await live_score(rival["a_id"])
    if now is not None and now < float(rival["a_score"]):
        await db.conn().execute("UPDATE duels SET a_score = ? WHERE id = ? AND status = 'waiting'", (now, rival["id"]))
        await db.conn().commit()


async def _notify_opponent(outcome: battle.Outcome, kind: str, stake: int, label: str) -> None:
    """Тот, кто ждал, узнаёт итог сообщением — мини-апп у него закрыт."""
    if _bot is None:
        return
    what = "ников" if kind == "nick" else "профилей"
    if outcome.winner is None:
        text = f"Дуэль {what} на {stake}⭐: ничья с {label} — ставка вернулась."
    elif outcome.winner == outcome.opponent_id:
        text = (
            f"Победа в дуэли {what}! {outcome.their_score:.2f} против {outcome.my_score:.2f} "
            f"({label}). +{outcome.prize}⭐ на баланс."
        )
    else:
        text = (
            f"Дуэль {what} на {stake}⭐ проиграна: {outcome.their_score:.2f} "
            f"против {outcome.my_score:.2f} ({label})."
        )
    try:
        await _bot.send_message(outcome.opponent_id, text, parse_mode=None)
    except Exception as error:
        log.info("не сообщить %s об итоге дуэли: %s", outcome.opponent_id, error)


@authed
async def api_duel_cancel(request, user, data):
    await battle.cancel_duel(_int(data, "id"), int(user["id"]))
    return _ok(balance=await db.balance(int(user["id"])))


@authed
async def api_duels(request, user, data):
    uid = int(user["id"])
    kind = "nick" if data.get("kind") == "nick" else "profile"
    rows, total = await db.recent_duels(
        uid, kind, max(0, _int(data, "offset")), max(1, min(10, _int(data, "limit", 5)))
    )
    return _ok(rows=[battle.duel_view(row, uid) for row in rows], total=total)


@authed
async def api_topup(request, user, data):
    link = await payouts.invoice_link(_bot, int(user["id"]), _int(data, "amount"))
    return _ok(link=link)


@authed
async def api_withdraw(request, user, data):
    uid = int(user["id"])
    sent, gifts = await payouts.withdraw(_bot, uid, _int(data, "amount"))
    return _ok(sent=sent, gifts=gifts, balance=await db.balance(uid))


@authed
async def api_history(request, user, data):
    rows, total = await db.history(int(user["id"]), max(0, _int(data, "offset")), max(1, min(10, _int(data, "limit", 6))))
    return _ok(rows=rows, total=total)


# --- картинки для превью ссылок в инлайне -----------------------------------------
#
# Как у @send: в чат уходит текст, а над ним — большое превью ссылки на
# картинку. Telegram скачивает её сам, поэтому адрес должен быть
# публичным — это и есть этот сервер, когда он на хостинге (WEBAPP_URL).
# Имя — хеш содержимого: изменилась карточка — новый адрес, и старое
# превью из кэша Telegram не всплывёт.

IMAGES: dict[str, bytes] = {}
IMAGES_MAX = 2000


def publish(blob: bytes) -> str:
    """Положить картинку и вернуть её публичный адрес."""
    name = hashlib.sha1(blob).hexdigest()[:24] + ".png"
    if name not in IMAGES:
        if len(IMAGES) >= IMAGES_MAX:
            # Выкидываем самые старые: словарь помнит порядок добавления.
            for old in list(IMAGES)[: IMAGES_MAX // 4]:
                IMAGES.pop(old, None)
        IMAGES[name] = blob
    return f"{config.WEBAPP_URL}/img/{name}"


async def image(request: web.Request) -> web.Response:
    blob = IMAGES.get(request.match_info["name"])
    if blob is None:
        raise web.HTTPNotFound()
    return web.Response(body=blob, content_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


# --- статика ---------------------------------------------------------------------

STATIC = {
    "app.css": "text/css",
    "app.js": "application/javascript",
    "solik.png": "image/png",
    "pattern.png": "image/png",
}


async def index(request: web.Request) -> web.Response:
    return web.FileResponse(config.WEBAPP_DIR / "index.html", headers={"Cache-Control": "no-cache"})


async def static(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name not in STATIC:
        raise web.HTTPNotFound()
    return web.FileResponse(
        config.WEBAPP_DIR / name,
        headers={"Content-Type": STATIC[name], "Cache-Control": "no-cache"},
    )


def build() -> web.Application:
    app = web.Application(client_max_size=64 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/img/{name}", image)
    app.router.add_get("/{name}", static)
    for path, handler in (
        ("state", api_state),
        ("rescore", api_rescore),
        ("tourney", api_tourney),
        ("tourney/join", api_tourney_join),
        ("duel/join", api_duel_join),
        ("duel/cancel", api_duel_cancel),
        ("duels", api_duels),
        ("topup", api_topup),
        ("withdraw", api_withdraw),
        ("history", api_history),
    ):
        app.router.add_post(f"/api/{path}", handler)
    return app


async def serve() -> web.AppRunner:
    runner = web.AppRunner(build(), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, config.HOST, config.PORT).start()
    log.info("мини-апп слушает порт %s%s", config.PORT,
             f", адрес: {config.WEBAPP_URL}" if config.WEBAPP_URL else "")
    return runner
