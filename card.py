"""Карточка-таблица: семь строк, у каждой балл из десяти, сверху общий.

Стиль солика, как в Мог Батле: графит, серебряные цифры Unbounded,
текст Manrope, одна розовая пометка от руки. Цвет не несёт смысла —
балл читается по яркости шкалы из десяти делений, а не по светофору.
"""

from __future__ import annotations

import io
import logging
import random
import unicodedata

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import config
from scoring import METRICS, Score, subtitle

log = logging.getLogger("profilerate.card")

FONT_DIR = config.BASE_DIR / "assets" / "fonts"

SIZE = (1200, 1260)
PAD = 70

BG_TOP = (29, 29, 33)
BG_BOTTOM = (5, 5, 6)
TEXT = (255, 255, 255)
MUTED = (148, 150, 160)
SILVER = (196, 197, 204)
STEEL = (125, 126, 136)
PINK = (242, 167, 184)
EMPTY = (44, 45, 52)
RULE = (255, 255, 255, 22)

HEAD_Y = 64
AVATAR = 184
TABLE_Y = 330
ROW_H = 110
CELLS_X = (620, 960)

_fonts: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    key = (name, size)
    if key not in _fonts:
        try:
            _fonts[key] = ImageFont.truetype(str(FONT_DIR / name), size)
        except OSError:
            log.warning("шрифта %s нет — беру встроенный", name)
            _fonts[key] = ImageFont.load_default(size)
    return _fonts[key]


def _clean(text: str) -> str:
    """Убрать эмодзи и прочее, чего нет в шрифте: иначе на карточке квадраты."""
    kept = [
        ch for ch in text
        if ord(ch) <= 0xFFFF and unicodedata.category(ch) not in ("So", "Cs", "Co", "Cn")
    ]
    return " ".join("".join(kept).split())


def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def _tracked(draw, xy, text: str, font, fill, tracking: float) -> None:
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) + tracking


def _background(size: tuple[int, int] = SIZE) -> Image.Image:
    w, h = size
    base = Image.new("RGBA", size)
    draw = ImageDraw.Draw(base)
    for y in range(h):
        k = y / (h - 1)
        draw.line(
            [(0, y), (w, y)],
            fill=tuple(int(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)),
        )
    # Два мягких пятна, холодное и тёплое: фон остаётся серым, но не плоским.
    glow = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((-260, -220, 620, 520), fill=(126, 140, 168, 46))
    gd.ellipse((700, h - 500, 1500, h + 240), fill=(168, 158, 148, 30))
    base.alpha_composite(glow.filter(ImageFilter.GaussianBlur(140)))

    dice = random.Random(7)
    grain = Image.new("L", (w // 2, h // 2))
    grain.putdata([dice.randint(0, 255) for _ in range(grain.width * grain.height)])
    grain = grain.resize(size)
    noise = Image.new("RGBA", size, (255, 255, 255, 0))
    noise.putalpha(grain.point(lambda v: int(v * 0.035)))
    base.alpha_composite(noise)
    return base


def _avatar(raw: bytes | None, name: str) -> Image.Image:
    """Аватарка скруглённым квадратом с тонким серебряным кантом."""
    tile = None
    if raw:
        try:
            tile = Image.open(io.BytesIO(raw)).convert("RGBA")
            side = min(tile.size)
            left, top = (tile.width - side) // 2, (tile.height - side) // 2
            tile = tile.crop((left, top, left + side, top + side)).resize(
                (AVATAR, AVATAR), Image.LANCZOS
            )
        except Exception as error:
            log.info("аватарка не открылась: %s", error)
            tile = None
    if tile is None:
        # Нет фото — первая буква имени на графитовой плашке.
        tile = Image.new("RGBA", (AVATAR, AVATAR), (36, 37, 43, 255))
        letter = (_clean(name)[:1] or "?").upper()
        d = ImageDraw.Draw(tile)
        d.text(
            (AVATAR / 2, AVATAR / 2), letter, font=_font("Unbounded-Bold.ttf", 78),
            fill=STEEL, anchor="mm",
        )
    mask = Image.new("L", (AVATAR, AVATAR), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, AVATAR - 1, AVATAR - 1), 44, fill=255)
    out = Image.new("RGBA", (AVATAR, AVATAR), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask)
    ImageDraw.Draw(out).rounded_rectangle(
        (1, 1, AVATAR - 2, AVATAR - 2), 44, outline=(*SILVER, 120), width=2
    )
    return out


def _solik(base: Image.Image, center_x: float, ledge_y: float, height: int) -> None:
    """Солик выглядывает из-за линии: лапки лежат на `ledge_y`."""
    try:
        art = Image.open(config.BASE_DIR / "assets" / "solik.png").convert("RGBA")
    except OSError:
        return
    width = int(art.width * height / art.height)
    cat = art.resize((width, height), Image.LANCZOS)
    x, y = int(center_x - width / 2), int(ledge_y - height + 4)
    shadow = Image.new("RGBA", (width + 60, height + 60), (0, 0, 0, 0))
    silhouette = Image.new("RGBA", cat.size, (0, 0, 0, 255))
    silhouette.putalpha(cat.getchannel("A").point(lambda v: int(v * 0.5)))
    shadow.alpha_composite(silhouette, (30, 36))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(12)), (x - 30, y - 30))
    base.alpha_composite(cat, (x, y))


def _scale(draw: ImageDraw.ImageDraw, y: float, value: float | None) -> None:
    """Десять делений; заполнено столько, сколько баллов, последнее — частично."""
    left, right = CELLS_X
    gap = 7
    cell = (right - left - gap * 9) / 10
    for i in range(10):
        x0 = left + i * (cell + gap)
        box = (x0, y - 9, x0 + cell, y + 9)
        draw.rounded_rectangle(box, 5, fill=EMPTY)
        if value is None:
            continue
        share = max(0.0, min(1.0, value - i))
        if share <= 0:
            continue
        # Чем выше деление, тем ярче серебро: шкала светлеет к десятке.
        tone = tuple(int(s + (t - s) * i / 9) for s, t in zip(STEEL, TEXT))
        draw.rounded_rectangle((x0, y - 9, x0 + max(10, cell * share), y + 9), 5, fill=tone)


def table(
    tile: Image.Image,
    title: str,
    handle: str,
    note: str,
    total: float,
    rows: list[tuple[str, str, float | None]],
    bot_username: str = "",
) -> bytes:
    """Общая раскладка: плитка, заголовок, общий балл и строки таблицы.

    Строка — (название, подпись, балл); балл None рисуется прочерком и
    пустой шкалой: так выглядит «нет данных».
    """
    height = TABLE_Y + 44 + len(rows) * ROW_H + 116
    size = (SIZE[0], height)
    base = _background(size)
    draw = ImageDraw.Draw(base)
    w, h = size

    # --- шапка: плитка, имя, подпись, общий балл ------------------------------
    base.alpha_composite(tile, (PAD, HEAD_Y))
    text_x = PAD + AVATAR + 36

    total_text = f"{total:.1f}"
    big = _font("Unbounded-Black.ttf", 132)
    small = _font("Manrope-Bold.ttf", 40)
    out_of_w = draw.textlength("/10", font=small)
    total_w = draw.textlength(total_text, font=big)
    total_x = w - PAD - out_of_w - 8 - total_w
    draw.text((total_x, HEAD_Y + 6), total_text, font=big, fill=TEXT)
    draw.text((w - PAD - out_of_w, HEAD_Y + 112), "/10", font=small, fill=MUTED)

    name_room = total_x - text_x - 30
    name_font = _font("NotoSans-Bold.ttf", 52)
    draw.text(
        (text_x, HEAD_Y + 14), _fit(draw, _clean(title) or "Без имени", name_font, name_room),
        font=name_font, fill=TEXT,
    )
    handle_font = _font("Manrope-Medium.ttf", 32)
    draw.text(
        (text_x, HEAD_Y + 88), _fit(draw, handle, handle_font, name_room),
        font=handle_font, fill=MUTED,
    )
    draw.text((text_x + 4, HEAD_Y + 132), note, font=_font("Caveat-Bold.ttf", 40), fill=PINK)

    # --- таблица --------------------------------------------------------------
    rule_y = TABLE_Y - 22
    draw.line([(PAD, rule_y), (w - PAD, rule_y)], fill=RULE, width=2)
    head = _font("Manrope-Bold.ttf", 19)
    _tracked(draw, (PAD, TABLE_Y), "МЕТРИКА", head, STEEL, 4)
    _tracked(draw, (CELLS_X[0], TABLE_Y), "ШКАЛА", head, STEEL, 4)
    label = "БАЛЛ"
    label_w = sum(draw.textlength(ch, font=head) + 4 for ch in label) - 4
    _tracked(draw, (w - PAD - label_w, TABLE_Y), label, head, STEEL, 4)

    title_font = _font("Manrope-Bold.ttf", 36)
    note_font = _font("Manrope-Medium.ttf", 25)
    value_font = _font("Unbounded-Bold.ttf", 42)
    top = TABLE_Y + 44
    for index, (row_title, row_note, value) in enumerate(rows):
        y = top + index * ROW_H
        draw.text((PAD, y + 14), row_title, font=title_font, fill=TEXT)
        line = _fit(draw, _clean(row_note), note_font, CELLS_X[0] - PAD - 30)
        draw.text((PAD, y + 60), line, font=note_font, fill=MUTED)
        _scale(draw, y + ROW_H / 2, value)
        draw.text(
            (w - PAD, y + ROW_H / 2), "—" if value is None else f"{value:.1f}",
            font=value_font, fill=STEEL if value is None else TEXT, anchor="rm",
        )
        if index < len(rows) - 1:
            draw.line([(PAD, y + ROW_H), (w - PAD, y + ROW_H)], fill=RULE, width=1)

    # --- подвал ---------------------------------------------------------------
    foot_y = top + len(rows) * ROW_H + 18
    draw.line([(PAD, foot_y), (w - PAD, foot_y)], fill=RULE, width=2)
    # В последней строке середина пустая: название и подпись короткие.
    _solik(base, 430, foot_y, 104)
    brand = _font("Unbounded-Bold.ttf", 22)
    _tracked(draw, (PAD, h - 66), config.BRAND, brand, STEEL, 6)
    if bot_username:
        draw.text(
            (w - PAD, h - 54), f"@{bot_username}", font=_font("Manrope-Medium.ttf", 26),
            fill=MUTED, anchor="rm",
        )

    buffer = io.BytesIO()
    base.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def render(score: Score, bot_username: str = "") -> bytes:
    """Карточка профиля: аватарка и семь метрик."""
    p = score.profile
    rows = [
        (title, subtitle(key, p), None if key in p.guessed else score.parts[key])
        for key, title in METRICS
    ]
    return table(
        _avatar(p.avatar, p.name), p.name, p.handle, "оценка профиля)",
        score.total, rows, bot_username,
    )


def _badge(text: str) -> Image.Image:
    """Плитка для ника вместо аватарки: знак @ серебром на графите."""
    tile = Image.new("RGBA", (AVATAR, AVATAR), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.rounded_rectangle((0, 0, AVATAR - 1, AVATAR - 1), 44, fill=(36, 37, 43, 255))
    d.rounded_rectangle((1, 1, AVATAR - 2, AVATAR - 2), 44, outline=(*SILVER, 120), width=2)
    d.text((AVATAR / 2, AVATAR / 2 - 6), text, font=_font("Unbounded-Bold.ttf", 96),
           fill=SILVER, anchor="mm")
    return tile


def render_username(rating, status_text: str, bot_username: str = "") -> bytes:
    """Карточка ника: шесть признаков разбора и строка цены."""
    rows = [(row.title, row.note, row.value) for row in rating.rows]
    return table(
        _badge("@"), f"@{rating.username}", status_text, "оценка юзернейма)",
        rating.total, rows, bot_username,
    )
