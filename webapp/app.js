/* Батл профилей — мини-апп. Всё на одной странице, данные — из /api.
   Списки идут страницами: сколько строк влезает в карточку, столько и
   показываем, дальше — стрелки. Бесконечной ленты нет нигде. */

const tg = window.Telegram ? window.Telegram.WebApp : null;
const INIT = (tg && tg.initData) || new URLSearchParams(location.search).get("init") || "";

const $ = (sel, root = document) => root.querySelector(sel);
const STAR = '<svg class="star"><use href="#xtr"/></svg>';
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const fmt = (n) => Number(n).toFixed(2).replace(/0$/, "");

let S = null;               // последнее состояние с сервера
const pages = { t: 0, w: 0, profile: 0, nick: 0 };
// Строка с подписью: два этажа текста и отступы — замерено в браузере.
const ROW_H = 56;

async function api(path, body = {}) {
  const res = await fetch(`/api/${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Init-Data": INIT },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({ ok: false, error: "Сервер не ответил" }));
  if (!data.ok) throw new Error(data.error || "Не вышло");
  return data;
}

function toast(text) {
  const el = $("#toast");
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.hidden = true), 2600);
}

function haptic(kind) {
  try { tg && tg.HapticFeedback.notificationOccurred(kind); } catch (e) { /* не телеграм */ }
}

async function guard(button, fn) {
  if (button) button.disabled = true;
  try { await fn(); } catch (e) { toast(e.message); haptic("error"); }
  finally { if (button) button.disabled = false; }
}

/* --- навигация ------------------------------------------------------------ */

function go(name) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === `s-${name}`));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.go === name));
  if (name === "wallet") loadHistory();
  if (name === "duel" || name === "nick") loadDuels(name === "duel" ? "profile" : "nick");
  requestAnimationFrame(() => renderAll());
}

document.addEventListener("click", (e) => {
  const target = e.target.closest("[data-go]");
  if (target) go(target.dataset.go);
});

/* --- общие куски ------------------------------------------------------------ */

function scale(value) {
  let out = '<div class="scale">';
  for (let i = 0; i < 10; i++) {
    const share = value == null ? 0 : Math.max(0, Math.min(1, value - i));
    out += `<i><b style="width:${share * 100}%"></b></i>`;
  }
  return out + "</div>";
}

/** Сколько строк влезает в карточку-список — по её реальной высоте. */
function fits(listEl) {
  const head = listEl.querySelector(".list-head");
  const room = listEl.clientHeight - (head ? head.offsetHeight + 8 : 0) - 28;
  return Math.max(2, Math.floor(room / ROW_H));
}

function pager(el, page, perPage, total, onMove) {
  const last = Math.max(0, Math.ceil(total / perPage) - 1);
  if (total <= perPage) { el.innerHTML = ""; return; }
  el.innerHTML = `<button ${page <= 0 ? "disabled" : ""}><svg><use href="#i-left"/></svg></button>
    ${page + 1}/${last + 1}
    <button ${page >= last ? "disabled" : ""}><svg><use href="#i-right"/></svg></button>`;
  const [prev, next] = el.querySelectorAll("button");
  prev.onclick = () => onMove(Math.max(0, page - 1));
  next.onclick = () => onMove(Math.min(last, page + 1));
}

function left(ts) {
  const s = Math.max(0, ts - Date.now() / 1000);
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d} д ${h} ч` : h ? `${h} ч ${m} мин` : `${m} мин`;
}

function plural(n, one, few, many) {
  const a = n % 10, b = n % 100;
  return a === 1 && b !== 11 ? one : a >= 2 && a <= 4 && (b < 12 || b > 14) ? few : many;
}

/* --- шапка ------------------------------------------------------------------ */

function renderTop() {
  const me = S.me;
  $("#who-name").textContent = me.name;
  const ava = $("#ava");
  if (me.photo) { ava.style.backgroundImage = `url("${me.photo}")`; ava.textContent = ""; }
  else ava.textContent = (me.name || "?").slice(0, 1).toUpperCase();
  $("#balance").textContent = me.balance;
  $("#w-balance").textContent = me.balance;
}

/* --- турнир ---------------------------------------------------------------- */

async function renderTourney() {
  const t = S.tourney;
  $("#t-prize").textContent = t.prize;
  $("#t-meta").textContent =
    `до итогов ${left(t.ends)} · ${t.entrants} ${plural(t.entrants, "участник", "участника", "участников")}`;
  const medals = ["1 место", "2 место", "3 место"];
  $("#t-podium").innerHTML = t.prizes.slice(0, 3).map((p, i) =>
    `<div><b>${STAR} ${p}</b><span>${medals[i]}</span></div>`).join("");

  $("#t-score").textContent = (t.joined ? t.score : S.score.total).toFixed(1);
  $("#t-place").textContent = t.joined ? `Ты на ${t.place} месте` : "Твой балл профиля";
  $("#t-hint").innerHTML = t.joined
    ? (t.refresh_in > 0 ? `обновить балл — через ${Math.ceil(t.refresh_in / 60)} мин` : "подтянул профиль? обнови балл")
    : `взнос ${STAR} ${t.fee} · банк делят топ-3`;
  $("#rescore").disabled = S.score.refresh_in > 0;

  const join = $("#t-join");
  join.hidden = t.joined;
  join.innerHTML = `Участвовать за ${STAR} ${t.fee}`;

  const list = $("#s-tourney .list");
  const per = fits(list);
  const rows = (await api("tourney", { offset: pages.t * per })).tourney;
  const box = $("#t-rows");
  box.innerHTML = rows.rows.slice(0, per).map((r) => `
    <div class="row ${r.me ? "me" : ""}">
      <span class="n">${r.place}</span>
      <span class="t">${esc(r.name)}<small>${r.username ? "@" + esc(r.username) : "&nbsp;"}</small></span>
      <span class="v">${r.score.toFixed(2)}</span>
    </div>`).join("") || '<div class="empty">Пока никого — стань первым)</div>';
  pager($("#t-pager"), pages.t, per, rows.total_rows, (p) => { pages.t = p; renderTourney(); });
}

$("#t-join").onclick = (e) => guard(e.currentTarget, async () => {
  const t = S.tourney;
  if (!(await confirmSheet("Турнир недели", `Взнос ${STAR} ${t.fee}. В таблицу попадёт твой балл ${S.score.total.toFixed(2)} — его можно обновлять раз в час.`, `Вступить за ${STAR} ${t.fee}`))) return;
  const data = await api("tourney/join");
  S.tourney = data.tourney; S.me.balance = data.balance;
  haptic("success"); toast("Ты в турнире!");
  renderAll();
});

$("#rescore").onclick = (e) => guard(e.currentTarget, async () => {
  const data = await api("rescore");
  S.score = data.score; S.tourney = data.tourney;
  toast(`Новый балл: ${data.score.total.toFixed(2)}`);
  renderAll();
});

/* --- дуэли --------------------------------------------------------------- */

const DUEL_TEXT = {
  profile: { title: "Дуэль профилей", what: "балл профиля", stakes: "duel_stakes" },
  nick: { title: "Дуэль ников", what: "оценка ника", stakes: "nick_stakes" },
};
const duelRows = { profile: { rows: [], total: 0 }, nick: { rows: [], total: 0 } };

function duelScore(kind) {
  if (kind === "profile") return { total: S.score.total, label: "твой профиль" };
  return S.nick ? { total: S.nick.total, label: "@" + S.nick.username } : null;
}

function renderDuel(kind) {
  const el = $(kind === "profile" ? "#s-duel" : "#s-nick");
  const mine = duelScore(kind);
  if (!mine) {
    el.innerHTML = `<div class="card"><div class="kicker">Дуэль ников</div>
      <p>Чтобы биться ником, поставь <b>@username</b> в настройках Telegram и открой приложение заново.</p>
      <div class="note">бьются только своим ником — чужой не подставишь)</div></div>`;
    return;
  }
  const queue = S.queues[kind] || {};
  const waits = S.waiting.filter((w) => w.kind === kind);
  el.innerHTML = `
    <div class="card mine">
      <div class="mine-score">${mine.total.toFixed(1)}<small>/10</small></div>
      <div class="mine-text"><div class="mine-title">${esc(mine.label)}</div>
        <div class="muted">${DUEL_TEXT[kind].what} · выше — забирает банк</div>${scale(mine.total)}</div>
    </div>
    <div class="stakes">${S.rules[DUEL_TEXT[kind].stakes].map((stake) => `
      <button class="stake" data-stake="${stake}"><b>${STAR}${stake}</b>
        <span>${queue[stake] ? `ждут ${queue[stake]} — бой сразу` : "встанешь в очередь"}</span></button>`).join("")}
    </div>
    ${waits.length ? `<div class="waits">${waits.map((w) => `
      <span class="wait">ждёшь соперника · ${STAR}${w.stake}
      <button data-cancel="${w.id}" aria-label="Отозвать"><svg><use href="#i-x"/></svg></button></span>`).join("")}</div>` : ""}
    <div class="card list"><div class="list-head"><span>Мои бои</span><span class="pager"></span></div><div class="rows"></div></div>`;

  el.querySelectorAll("[data-stake]").forEach((b) => b.onclick = () => duel(kind, Number(b.dataset.stake), b));
  el.querySelectorAll("[data-cancel]").forEach((b) => b.onclick = () => guard(b, async () => {
    const data = await api("duel/cancel", { id: Number(b.dataset.cancel) });
    S.me.balance = data.balance; await reload(); toast("Заявка отозвана, ставка вернулась");
  }));
  drawDuelRows(kind);
}

function drawDuelRows(kind) {
  const el = $(kind === "profile" ? "#s-duel" : "#s-nick");
  const list = el.querySelector(".list");
  if (!list) return;
  const per = fits(list);
  const { rows, total } = duelRows[kind];
  el.querySelector(".rows").innerHTML = rows.slice(0, per).map((r) => `
    <div class="row"><span class="t">${r.result === "win" ? "Победа" : r.result === "lose" ? "Поражение" : "Ничья"}
      <small>против ${esc(r.their_label)} · ${r.my_score.toFixed(2)} : ${r.their_score.toFixed(2)}</small></span>
      <span class="v ${r.result === "win" ? "plus" : "minus"}">${r.result === "win" ? "+" + r.prize : r.result === "lose" ? "−" + r.stake : "0"}</span>
    </div>`).join("") || '<div class="empty">Боёв пока не было</div>';
  pager(el.querySelector(".pager"), pages[kind], per, total, (p) => { pages[kind] = p; loadDuels(kind); });
}

async function loadDuels(kind) {
  const el = $(kind === "profile" ? "#s-duel" : "#s-nick");
  const list = el.querySelector(".list");
  const per = list ? fits(list) : 5;
  try {
    const data = await api("duels", { kind, offset: pages[kind] * per, limit: per });
    duelRows[kind] = { rows: data.rows, total: data.total };
    drawDuelRows(kind);
  } catch (e) { /* список подождёт */ }
}

async function duel(kind, stake, button) {
  const mine = duelScore(kind);
  const ok = await confirmSheet(DUEL_TEXT[kind].title,
    `Ставка ${STAR} ${stake}. В бой идёт ${esc(mine.label)} — ${mine.total.toFixed(2)}. Выше балл — забирает ${STAR} ${stake * 2 - Math.floor(stake * 2 * S.rules.house / 100)}, ничья — возврат.`,
    `Поставить ${STAR} ${stake}`);
  if (!ok) return;
  await guard(button, async () => {
    const data = await api("duel/join", { kind, stake });
    S.me.balance = data.balance;
    const o = data.outcome;
    if (!o.matched) {
      toast("Ты в очереди — итог придёт сообщением");
    } else {
      haptic(o.result === "win" ? "success" : o.result === "lose" ? "error" : "warning");
      showResult(o, stake, mine.label);
    }
    await reload();
    loadDuels(kind);
  });
}

function showResult(o, stake, myLabel) {
  const won = o.result === "win", draw = o.result === "draw";
  openSheet(`
    <h3>${won ? "Победа!" : draw ? "Ничья" : "Проигрыш"}</h3>
    <div class="muted">${draw ? "ставка вернулась на баланс" : won ? "банк твой" : "в следующий раз повезёт"}</div>
    <div class="vs">
      <div class="side ${won ? "won" : "lost"}"><b>${o.my_score.toFixed(2)}</b><span>${esc(myLabel)}</span></div>
      <em>vs</em>
      <div class="side ${won ? "lost" : "won"}"><b>${o.their_score.toFixed(2)}</b><span>${esc(o.their_label)}</span></div>
    </div>
    ${won ? `<div class="result-line">+${STAR}${o.prize}</div>` : ""}
    <div class="btns" style="margin-top:14px"><button class="btn primary" data-close>Ок</button></div>`);
}

/* --- кошелёк -------------------------------------------------------------- */

function renderWallet() {
  $("#w-topups").innerHTML = S.rules.topups.map((a) => `<button class="chip" data-topup="${a}">${STAR}${a}</button>`).join("");
  $("#w-min").innerHTML = `от ${STAR}${S.rules.withdraw_min}`;
  document.querySelectorAll("[data-topup]").forEach((b) => b.onclick = () => topup(Number(b.dataset.topup), b));
}

async function topup(amount, button) {
  await guard(button, async () => {
    const { link } = await api("topup", { amount });
    if (!tg || !tg.openInvoice) { window.open(link, "_blank"); return; }
    tg.openInvoice(link, async (status) => {
      if (status === "paid") {
        haptic("success"); toast(`+${amount} звёзд на балансе`);
        // Зачисление приходит апдейтом в бот — даём ему секунду.
        setTimeout(reload, 1200);
      }
    });
  });
}

$("#w-go").onclick = (e) => guard(e.currentTarget, async () => {
  const amount = Number($("#w-amount").value.replace(/\D/g, ""));
  if (!amount) throw new Error("Сколько звёзд вывести?");
  if (!(await confirmSheet("Вывод подарками", `Соберём ${STAR} ${amount} подарками Telegram — их можно оставить в профиле или обменять на звёзды. Остаток, на который подарка нет, останется на балансе.`, "Вывести"))) return;
  const data = await api("withdraw", { amount });
  S.me.balance = data.balance; $("#w-amount").value = "";
  haptic("success"); toast(`Отправлено подарков: ${data.gifts.length} на ${data.sent} звёзд`);
  renderTop(); loadHistory();
});

async function loadHistory() {
  const list = $("#s-wallet .list");
  const per = fits(list);
  try {
    const data = await api("history", { offset: pages.w * per, limit: per });
    $("#w-rows").innerHTML = data.rows.map((r) => `
      <div class="row"><span class="t">${esc(r.note || r.kind)}<small>${new Date(r.at * 1000).toLocaleString("ru", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}</small></span>
      <span class="v ${r.delta > 0 ? "plus" : "minus"}">${r.delta > 0 ? "+" : "−"}${Math.abs(r.delta)}</span></div>`).join("")
      || '<div class="empty">Тут появятся пополнения и выигрыши</div>';
    pager($("#w-pager"), pages.w, per, data.total, (p) => { pages.w = p; loadHistory(); });
  } catch (e) { /* ничего */ }
}

/* --- шторка ---------------------------------------------------------------- */

function openSheet(html) {
  $("#sheet-card").innerHTML = html;
  $("#sheet").hidden = false;
}

function closeSheet() { $("#sheet").hidden = true; }

$("#sheet").addEventListener("click", (e) => {
  if (e.target.id === "sheet" || e.target.closest("[data-close]")) closeSheet();
});

function confirmSheet(title, text, yes) {
  return new Promise((resolve) => {
    openSheet(`<h3>${title}</h3><p class="muted" style="font-size:14px">${text}</p>
      <div class="btns"><button class="btn primary" data-yes>${yes}</button><button class="btn" data-close>Отмена</button></div>`);
    $("#sheet-card [data-yes]").onclick = () => { closeSheet(); resolve(true); };
    $("#sheet-card [data-close]").onclick = () => { closeSheet(); resolve(false); };
  });
}

/* --- загрузка -------------------------------------------------------------- */

function renderAll() {
  if (!S) return;
  renderTop();
  renderTourney();
  renderDuel("profile");
  renderDuel("nick");
  renderWallet();
}

async function reload() {
  S = await api("state");
  renderAll();
}

(async function start() {
  if (tg) {
    tg.ready(); tg.expand();
    try { tg.setHeaderColor("#1d1d21"); tg.setBackgroundColor("#0d0d10"); } catch (e) { /* старый клиент */ }
  }
  try { await reload(); }
  catch (e) { toast(e.message); }
  setInterval(() => S && ($("#t-meta").textContent = $("#t-meta").textContent.replace(/до итогов [^·]+/, `до итогов ${left(S.tourney.ends)} `)), 30000);
  window.addEventListener("resize", () => renderAll());
})();
