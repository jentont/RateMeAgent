"""Проверка батла на временной базе: деньги, турнир, дуэли, выводы, API.

Главная проверка — сохранение денег: балансы, касса и залог (висящие
ставки и взносы открытого турнира) вместе всегда равны «пополнили − вывели». Она гоняется после каждого сценария.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import tempfile
import time
import urllib.parse
from types import SimpleNamespace as NS

failed: list[str] = []


def ok(cond: bool, what: str) -> None:
    print(("  ok  " if cond else "  FAIL ") + what)
    if not cond:
        failed.append(what)


async def conserved(db) -> bool:
    async with db.conn().execute("SELECT COALESCE(SUM(balance),0) AS s FROM users") as cur:
        held = (await cur.fetchone())["s"]
    async with db.conn().execute(
        "SELECT COALESCE(SUM(delta),0) AS s FROM ledger WHERE kind IN ('topup','withdraw','refund_withdraw')"
    ) as cur:
        flow = (await cur.fetchone())["s"]
    async with db.conn().execute(
        "SELECT COALESCE(SUM(delta),0) AS s FROM ledger WHERE ref LIKE 'withdraw-back:%'"
    ) as cur:
        back = (await cur.fetchone())["s"]
    # Ставки висящих заявок и взносы открытого турнира — в залоге: их уже
    # нет на балансах, но они ещё ничьи.
    async with db.conn().execute(
        "SELECT COALESCE(SUM(stake),0) AS s FROM duels WHERE status = 'waiting'"
    ) as cur:
        escrow = (await cur.fetchone())["s"]
    async with db.conn().execute(
        """SELECT COALESCE(SUM(t.fee),0) AS s FROM entries e JOIN tourneys t ON t.id = e.tourney_id
           WHERE t.status = 'open'"""
    ) as cur:
        escrow += (await cur.fetchone())["s"]
    return held + escrow == flow + back


def signed_init(token: str, user: dict, age: int = 0) -> str:
    fields = {"auth_date": str(int(time.time()) - age), "user": json.dumps(user), "query_id": "x"}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode(fields)


class GiftBot:
    """Бот, который покупает подарки; `fail_after` — сколько отправит до ошибки."""

    def __init__(self, stars: int = 10_000, fail_after: int | None = None):
        self.stars, self.fail_after, self.sent = stars, fail_after, []

    async def get_available_gifts(self):
        gift = lambda gid, price, left=None: NS(id=gid, star_count=price, remaining_count=left, publisher_chat=None)  # noqa: E731
        return NS(gifts=[gift("g15", 15), gift("g25", 25), gift("g50", 50), gift("g100", 100), gift("sold", 500, 0)])

    async def get_my_star_balance(self):
        return NS(amount=self.stars)

    async def send_gift(self, gift_id, user_id, text=None):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise RuntimeError("flood")
        self.sent.append(gift_id)
        return True

    async def create_invoice_link(self, **kw):
        return f"https://t.me/$invoice-{kw['payload']}"

    async def send_message(self, *a, **kw):
        return None


async def run() -> list[str]:
    import config

    tmp = tempfile.mkdtemp()
    config.DB_PATH = os.path.join(tmp, "battle.db")
    config.HOUSE_PERCENT = 10
    config.TOURNEY_FEE = 20
    config.TOURNEY_SPLIT = (50, 30, 20)
    config.DUEL_STAKES = (10, 25)
    config.NICK_STAKES = (10,)
    config.MAX_WAITING = 2
    config.SCORE_TTL = 3600

    import battle
    import db
    import payouts

    await db.init()
    try:
        await _scenarios(db, battle, payouts)
        await api_checks(db, battle)
    finally:
        await db.close()
    return failed


async def _scenarios(db, battle, payouts) -> None:
    print("батл: деньги")
    async with db.money_lock:
        ok(await db.move(1, 100, "topup", "topup:a"), "пополнение")
        ok(not await db.move(1, 100, "topup", "topup:a"), "та же оплата второй раз не зачисляется")
    ok(await payouts.credit_topup(2, 100, "b") and not await payouts.credit_topup(2, 100, "b"), "credit_topup идемпотентен")
    for uid in (3, 4, 5):
        await payouts.credit_topup(uid, 100, f"c{uid}")
    await payouts.credit_topup(6, 5, "poor")
    try:
        async with db.money_lock:
            await db.move(6, -10, "x", "neg")
        ok(False, "в минус уйти нельзя")
    except db.NotEnough:
        ok(True, "в минус уйти нельзя")
    ok(await conserved(db), "деньги сходятся")

    print("батл: дуэли")
    first = await battle.join_duel("profile", 10, 1, 7.0, "Один")
    ok(not first.matched and await db.balance(1) == 90, "заявка ждёт, ставка списана")
    again = await battle.join_duel("profile", 10, 1, 7.0, "Один")
    ok(not again.matched, "сам с собой не матчится")
    try:
        await battle.join_duel("profile", 10, 1, 7.0, "Один")
        ok(False, "лимит заявок")
    except battle.Refused:
        ok(True, "лимит заявок")
    win = await battle.join_duel("profile", 10, 2, 5.0, "Два")
    ok(win.matched and win.winner == 1 and win.prize == 18, f"бой: победил ждавший, приз {win.prize}")
    ok(await db.balance(1) == 90 - 10 + 18 and await db.balance(2) == 90, "балансы после боя")
    ok(await db.balance(db.HOUSE) == 2, "касса получила 10%")
    tie = await battle.join_duel("profile", 10, 3, 7.0, "Три")
    ok(tie.matched and tie.winner is None and await db.balance(3) == 100 and await db.balance(1) == 108,
       "ничья — возврат обоим")
    try:
        await battle.join_duel("profile", 25, 6, 9.0, "Бедный")
        ok(False, "без денег не пускает")
    except battle.Refused:
        ok(True, "без денег не пускает")
    ok(not await db.my_waiting(6), "отказ не оставил висящую заявку")
    try:
        await battle.join_duel("profile", 77, 3, 9.0, "x")
        ok(False, "чужая ставка")
    except battle.Refused:
        ok(True, "чужая ставка")
    w = await battle.join_duel("nick", 10, 4, 8.1, "@storm")
    await battle.cancel_duel(w.duel_id, 4)
    ok(await db.balance(4) == 100, "отзыв заявки — возврат")
    try:
        await battle.cancel_duel(w.duel_id, 4)
        ok(False, "второй отзыв")
    except battle.Refused:
        ok(True, "второй отзыв не вернёт дважды")
    w = await battle.join_duel("nick", 10, 4, 8.1, "@storm")
    await db.conn().execute("UPDATE duels SET created = 0 WHERE id = ?", (w.duel_id,))
    await db.conn().commit()
    expired = await battle.expire_duels()
    ok(len(expired) == 1 and await db.balance(4) == 100, "просроченная заявка вернула ставку")
    ok(await conserved(db), "деньги сходятся")

    print("батл: турнир")
    t = await battle.current_tourney()
    ok(t.ends > time.time(), "турнир открыт и кончится в будущем")
    ok(battle.next_end(time.time()) - time.time() <= 7 * 86400 + 3600, "конец — в пределах недели")
    await battle.join_tourney(5, 6.0)
    ok(await db.balance(5) == 80, "взнос списан")
    try:
        await battle.join_tourney(5, 6.0)
        ok(False, "дважды в турнир")
    except battle.Refused:
        ok(True, "дважды в турнир нельзя")
    try:
        await battle.refresh_entry(5, 9.0)
        ok(False, "обновление раньше часа")
    except battle.Refused:
        ok(True, "обновить балл раньше часа нельзя")
    # Один участник — возврат.
    await db.conn().execute("UPDATE tourneys SET ends = 0 WHERE id = ?", (t.id,))
    await db.conn().commit()
    due = await battle.due_tourney()
    awards = await battle.settle_tourney(due)
    ok(not awards and await db.balance(5) == 100, "один участник — взнос вернулся")
    awards2 = await battle.settle_tourney(due)
    ok(await db.balance(5) == 100, "повторные итоги ничего не выплатили")

    t2 = await battle.current_tourney()
    ok(t2.id != t.id, "открылся новый турнир")
    for uid, score in ((1, 8.0), (2, 6.0), (3, 7.0), (4, 9.5), (5, 5.0)):
        await battle.join_tourney(uid, score)
    before = {uid: await db.balance(uid) for uid in (1, 2, 3, 4, 5)}
    house_before = await db.balance(db.HOUSE)
    view = await battle.tourney_view(4)
    ok(view["place"] == 1 and view["bank"] == 100 and view["prize"] == 90, f"таблица: {view['place']} место, банк {view['bank']}")

    async def rescore(uid):
        # Четвёртый раздал подарки после вступления — живой балл ниже.
        return 4.0 if uid == 4 else None

    await db.conn().execute("UPDATE tourneys SET ends = 0 WHERE id = ?", (t2.id,))
    await db.conn().commit()
    awards = await battle.settle_tourney(await battle.due_tourney(), rescore=rescore)
    got = {a.user_id: a.stars for a in awards}
    ok([a.user_id for a in awards] == [1, 3, 2], f"призёры после перепроверки: {[a.user_id for a in awards]}")
    ok(sum(got.values()) == 90 and await db.balance(db.HOUSE) - house_before == 10, "банк разошёлся до звезды")
    after = {u: await db.balance(u) for u in before}
    ok(all(after[u] == before[u] + got.get(u, 0) for u in before), "призы на балансах")
    ok(battle.split(91, 3) == [46, 27, 18] and sum(battle.split(7, 2)) == 7, "деление банка без потерь")
    ok(await conserved(db), "деньги сходятся")

    print("батл: вывод подарками")
    ok(payouts.plan(140, [15, 25, 50, 100]) == [100, 25, 15], "140 = 100 + 25 + 15")
    ok(payouts.plan(14, [15, 25]) == [], "меньше самого дешёвого — ничего")
    bal = await db.balance(1)
    sent, gifts = await payouts.withdraw(GiftBot(), 1, 75)
    ok(sent == 75 and gifts == [50, 25] and await db.balance(1) == bal - 75, "вывод 75 = 50 + 25")
    bal = await db.balance(3)
    bot = GiftBot(fail_after=1)
    sent, gifts = await payouts.withdraw(bot, 3, 75)
    ok(sent == 50 and await db.balance(3) == bal - 50, "подарок не ушёл — остаток вернулся")
    try:
        await payouts.withdraw(GiftBot(stars=10), 2, 50)
        ok(False, "у бота мало звёзд")
    except payouts.Refused:
        ok(True, "у бота мало звёзд — вывод не идёт, баланс цел")
    try:
        await payouts.withdraw(GiftBot(), 6, 15)
        ok(False, "вывод больше баланса")
    except payouts.Refused:
        ok(True, "вывод больше баланса нельзя")
    ok("topup:9:50" in await payouts.invoice_link(GiftBot(), 9, 50), "счёт на пополнение")
    ok(await conserved(db), "деньги сходятся")


async def api_checks(db, battle) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    import config
    import usernames
    import webapp
    from scoring import Profile

    print("батл: API мини-аппа")
    config.BOT_TOKEN = "123:TEST"
    webapp.use_bot(GiftBot())

    async def fake_fetch(bot, uid, username, name):
        return Profile(user_id=uid, username=username, name=name, bio="живой профиль", level=10, gifts=5)

    async def fake_rate(name):
        rating = usernames.rate_offline(name)
        rating.status = usernames.Status.TAKEN
        return rating

    webapp.collect.fetch = fake_fetch
    webapp.usernames.rate = fake_rate
    webapp._scores.clear()

    user = {"id": 501, "first_name": "Тест", "username": "storm"}
    client = TestClient(TestServer(webapp.build()))
    await client.start_server()
    try:
        head = {"X-Init-Data": signed_init(config.BOT_TOKEN, user)}
        res = await client.post("/api/state", json={})
        ok(res.status == 401, "без подписи — 401")
        res = await client.post("/api/state", json={}, headers={"X-Init-Data": signed_init("999:OTHER", user)})
        ok(res.status == 401, "чужая подпись — 401")
        res = await client.post("/api/state", json={}, headers={"X-Init-Data": signed_init(config.BOT_TOKEN, user, age=10**6)})
        ok(res.status == 401, "старая подпись — 401")

        data = await (await client.post("/api/state", json={}, headers=head)).json()
        ok(data["ok"] and data["me"]["balance"] == 0 and data["nick"]["username"] == "storm", "состояние")
        ok(len(data["score"]["rows"]) == 7 and data["tourney"]["fee"] == config.TOURNEY_FEE, "балл и турнир в состоянии")

        data = await (await client.post("/api/duel/join", json={"kind": "profile", "stake": 10}, headers=head)).json()
        ok(not data["ok"] and "пополни" in data["error"], "без денег — понятный отказ")

        await payouts_credit(db, 501, 100)
        data = await (await client.post("/api/duel/join", json={"kind": "nick", "stake": 10}, headers=head)).json()
        ok(data["ok"] and not data["outcome"]["matched"] and data["balance"] == 90, "заявка ником")
        async with db.conn().execute("SELECT a_label FROM duels WHERE a_id = 501") as cur:
            ok((await cur.fetchone())["a_label"] == "@storm", "ник взят из подписи, а не из запроса")

        nobody = {"id": 502, "first_name": "Безника"}
        head2 = {"X-Init-Data": signed_init(config.BOT_TOKEN, nobody)}
        await payouts_credit(db, 502, 50)
        data = await (await client.post("/api/duel/join", json={"kind": "nick", "stake": 10}, headers=head2)).json()
        ok(not data["ok"] and "username" in data["error"], "без ника в дуэль ников нельзя")

        data = await (await client.post("/api/tourney/join", json={}, headers=head)).json()
        ok(data["ok"] and data["tourney"]["joined"], "вступление в турнир")
        data = await (await client.post("/api/rescore", json={}, headers=head)).json()
        ok(not data["ok"] and "мин" in data["error"], "переоценка не чаще часа")
        data = await (await client.post("/api/duels", json={"kind": "nick"}, headers=head)).json()
        ok(data["ok"] and data["total"] == 0, "история дуэлей")
        data = await (await client.post("/api/history", json={}, headers=head)).json()
        ok(data["ok"] and data["total"] >= 3, "история баланса")
        data = await (await client.post("/api/topup", json={"amount": 50}, headers=head)).json()
        ok(data["ok"] and data["link"].startswith("https://"), "ссылка на оплату")
        data = await (await client.post("/api/topup", json={"amount": 7}, headers=head)).json()
        ok(not data["ok"], "чужая сумма пополнения")
        res = await client.get("/app.js")
        ok(res.status == 200, "статика")
        res = await client.get("/battle.db")
        ok(res.status == 404, "кроме белого списка ничего не отдаётся")
        ok(await conserved(db), "деньги сходятся")
    finally:
        await client.close()


async def payouts_credit(db, uid: int, amount: int) -> None:
    import payouts

    await payouts.credit_topup(uid, amount, f"api-{uid}-{amount}")


if __name__ == "__main__":
    asyncio.run(run())
    print("\nВсё в порядке" if not failed else f"\nПровалено: {len(failed)}")
    raise SystemExit(1 if failed else 0)
