"""Картинки для инлайна в стиле карточки рулетки солика.

Сверху игрок (круглая аватарка, имя, @ник, пилюля режима), в центре
большая стеклянная панель со счётом и розовой пометкой от руки, снизу
сетка маленьких стеклянных плашек, в самом низу — солик. Фон тот же
графит, что у остальных карточек, плюс редкие звёздочки.

Здесь три картинки: битва (профилей и ников, вызов и итог) и поиск
свободных ников. Таблицы профиля и ника остаются в card.py.
"""

from __future__ import annotations

import io
import random

from PIL import Image, ImageDraw, ImageFilter

import card
from card import MUTED, PINK, SILVER, STEEL, TEXT, _clean, _fit, _font

W = 1200
CIRCLE = 132

#: Сторона одного игрока: имя, ник, аватарка (байты или None), общий балл.
Side = tuple[str, str, bytes | None, float | None]


def _canvas(height: int) -> Image.Image:
    base = card._background((W, height))
    # Редкие звёздочки, как на карточке рулетки: фон живой, но не шумный.
    dots = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(dots)
    dice = random.Random(height)
    for _ in range(70):
        x, y = dice.randint(0, W), dice.randint(0, height)
        r = dice.choice((1, 1, 1, 2))
        draw.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, dice.randint(40, 120)))
    base.alpha_composite(dots)
    return base


def _glass(base: Image.Image, box: tuple[float, float, float, float], radius: int = 34) -> None:
    """Стеклянная плашка: чуть светлее фона, тонкий кант, мягкая тень."""
    x0, y0, x1, y1 = (int(v) for v in box)
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 10, x1, y1 + 10), radius, fill=(0, 0, 0, 120))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18)))
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rounded_rectangle((x0, y0, x1, y1), radius, fill=(255, 255, 255, 13))
    draw.rounded_rectangle((x0, y0, x1, y1), radius, outline=(255, 255, 255, 38), width=2)
    base.alpha_composite(layer)


def _circle(raw: bytes | None, label: str, size: int = CIRCLE) -> Image.Image:
    """Круглая аватарка с серебряным кантом; без фото — буква или знак."""
    tile = None
    if raw:
        try:
            tile = Image.open(io.BytesIO(raw)).convert("RGBA")
            side = min(tile.size)
            left, top = (tile.width - side) // 2, (tile.height - side) // 2
            tile = tile.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
        except Exception:
            tile = None
    if tile is None:
        tile = Image.new("RGBA", (size, size), (36, 37, 43, 255))
        mark = label if len(label) == 1 else (_clean(label)[:1] or "?").upper()
        ImageDraw.Draw(tile).text(
            (size / 2, size / 2), mark, font=_font("Unbounded-Bold.ttf", size // 2 - 6),
            fill=STEEL, anchor="mm",
        )
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * 4 - 1, size * 4 - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size + 8, size + 8), (0, 0, 0, 0))
    ring = ImageDraw.Draw(out)
    ring.ellipse((0, 0, size + 7, size + 7), outline=(*SILVER, 150), width=3)
    out.paste(tile, (4, 4), mask)
    return out


def _pill(draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, anchor: str = "lt") -> float:
    """Обведённая пилюля режима («Битва профилей»). Возвращает её ширину."""
    font = _font("Manrope-Bold.ttf", 22)
    width = draw.textlength(text, font=font) + 34
    x, y = xy
    if anchor == "mt":
        x -= width / 2
    draw.rounded_rectangle((x, y, x + width, y + 40), 20, outline=(*SILVER, 170), width=2)
    draw.text((x + width / 2, y + 20), text, font=font, fill=TEXT, anchor="mm")
    return width


def _silver(base: Image.Image, center: tuple[float, float], text: str, size: int) -> None:
    """Крупная надпись серебряным градиентом сверху вниз, как «+20 000»."""
    font = _font("Unbounded-Black.ttf", size)
    mask = Image.new("L", base.size, 0)
    ImageDraw.Draw(mask).text(center, text, font=font, fill=255, anchor="mm")
    bbox = mask.getbbox()
    if not bbox:
        return
    grad = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(grad)
    top, bottom = bbox[1], bbox[3]
    for y in range(top, bottom + 1):
        k = (y - top) / max(1, bottom - top)
        tone = tuple(int(a + (b - a) * k) for a, b in zip((255, 255, 255), SILVER))
        gd.line([(bbox[0], y), (bbox[2], y)], fill=(*tone, 255))
    grad.putalpha(mask)
    base.alpha_composite(grad)


def _footer(base: Image.Image, bot_username: str) -> None:
    draw = ImageDraw.Draw(base)
    w, h = base.size
    cat_h = 66
    card._solik(base, w / 2 - 70, h - 26, cat_h)
    draw.text((w / 2 - 22, h - 52), f"@{bot_username}" if bot_username else "solutions",
              font=_font("Manrope-Bold.ttf", 26), fill=TEXT, anchor="lm")


def _player(base: Image.Image, side: Side, x: float, right: bool, label: str = "") -> None:
    """Игрок в шапке: круг и подписи; правый — зеркально."""
    name, handle, avatar, _ = side
    draw = ImageDraw.Draw(base)
    pic = _circle(avatar, label or name)
    px = x - pic.width if right else x
    base.alpha_composite(pic, (int(px), 46))
    name_font = _font("NotoSans-Bold.ttf", 40)
    room = 360
    tx = px - 24 if right else px + pic.width + 24
    anchor = "ra" if right else "la"
    draw.text((tx, 64), _fit(draw, _clean(name) or "Игрок", name_font, room), font=name_font,
              fill=TEXT, anchor=anchor)
    draw.text((tx, 118), _fit(draw, handle, _font("Manrope-Medium.ttf", 26), room),
              font=_font("Manrope-Medium.ttf", 26), fill=MUTED, anchor=anchor)


def _number(value: float | None) -> str:
    return "?" if value is None else f"{value:.1f}"


def versus(
    mode: str,
    left: Side,
    right: Side | None,
    rows: list[tuple[str, float | None, float | None]],
    bot_username: str = "",
    nick: bool = False,
) -> bytes:
    """Битва: вызов (right=None — соперника ещё нет) или итог.

    rows — (метрика, у левого, у правого). У победителя строки цифра
    белая, у проигравшего — сталь: смысл несёт яркость, не цвет.
    """
    cols = 4 if len(rows) > 6 else 3
    lines = (len(rows) + cols - 1) // cols
    box_h, gap = 112, 16
    grid_top = 520
    height = grid_top + lines * box_h + (lines - 1) * gap + 130
    base = _canvas(height)
    draw = ImageDraw.Draw(base)

    waiting = right is None
    right = right or ("Кто примет?", "соперника пока нет", None, None)
    _player(base, left, 56, right=False, label="@" if nick else "")
    _player(base, right, W - 56, right=True, label="?" if waiting else ("@" if nick else ""))

    # --- большая панель: счёт и пометка -----------------------------------
    panel = (56, 228, W - 56, 488)
    card_ = panel
    _glass(base, card_)
    _pill(draw, (W / 2, panel[1] + 22), mode, anchor="mt")
    a, b = left[3], right[3]
    _silver(base, (W / 2, panel[1] + 140), f"{_number(a)}  vs  {_number(b)}", 96)
    if waiting:
        note = "кто примет вызов?)"
    elif a is not None and b is not None and abs(a - b) >= 0.005:
        winner = left if a > b else right
        note = f"победа — {_clean(winner[1] if winner[1].startswith('@') else winner[0])})"
    else:
        note = "ничья)"
    draw.text((W / 2, panel[3] - 46), _fit(draw, note, _font("Caveat-Bold.ttf", 46), W - 200),
              font=_font("Caveat-Bold.ttf", 46), fill=PINK, anchor="mm")

    # --- сетка метрик -----------------------------------------------------
    box_w = (W - 112 - gap * (cols - 1)) / cols
    label_font = _font("Manrope-Medium.ttf", 22)
    value_font = _font("Unbounded-Bold.ttf", 32)
    for i, (title, va, vb) in enumerate(rows):
        r, c = divmod(i, cols)
        x0 = 56 + c * (box_w + gap)
        y0 = grid_top + r * (box_h + gap)
        _glass(base, (x0, y0, x0 + box_w, y0 + box_h), radius=24)
        draw.text((x0 + 22, y0 + 18), _fit(draw, title, label_font, box_w - 40),
                  font=label_font, fill=MUTED)
        hi = None
        if va is not None and vb is not None and abs(va - vb) >= 0.05:
            hi = "a" if va > vb else "b"
        sa, sb = _number(va), _number(vb if not waiting else None)
        x = x0 + 22
        y = y0 + 54
        draw.text((x, y), sa, font=value_font, fill=TEXT if hi != "b" else STEEL)
        x += draw.textlength(sa, font=value_font) + 10
        draw.text((x, y), ":", font=value_font, fill=STEEL)
        x += draw.textlength(":", font=value_font) + 10
        draw.text((x, y), sb, font=value_font, fill=TEXT if hi == "b" else STEEL)

    _footer(base, bot_username)
    return _png(base)


def search(seed: str, found: list[tuple[str, float]], bot_username: str = "") -> bytes:
    """Свободные ники: лучший — в большой панели, остальные — плашками."""
    rest = found[1:7]
    cols = 3
    lines = max(1, (len(rest) + cols - 1) // cols) if rest else 0
    box_h, gap = 104, 16
    grid_top = 520
    height = (grid_top + lines * box_h + (lines - 1) * gap if lines else grid_top - 20) + 130
    base = _canvas(height)
    draw = ImageDraw.Draw(base)

    pic = _circle(None, "@")
    base.alpha_composite(pic, (56, 46))
    tx = 56 + pic.width + 24
    draw.text((tx, 58), "Свободные ники", font=_font("Unbounded-Bold.ttf", 40), fill=TEXT)
    sub = f"по «{seed}»" if seed else "случайные, короткие"
    draw.text((tx, 116), _fit(draw, sub, _font("Manrope-Medium.ttf", 26), 600),
              font=_font("Manrope-Medium.ttf", 26), fill=MUTED)
    _pill(draw, (tx + 2, 156), "Поиск")

    panel = (56, 228, W - 56, 488)
    _glass(base, panel)
    if found:
        best, total = found[0]
        size = 96 if len(best) <= 10 else 76 if len(best) <= 14 else 58
        _silver(base, (W / 2, panel[1] + 104), f"@{best}", size)
        draw.text((W / 2, panel[1] + 186), f"{total:.1f}/10 · свободен",
                  font=_font("Manrope-Bold.ttf", 30), fill=TEXT, anchor="mm")
        draw.text((W / 2, panel[3] - 34), "займи, пока не забрали)",
                  font=_font("Caveat-Bold.ttf", 44), fill=PINK, anchor="mm")
    else:
        _silver(base, (W / 2, panel[1] + 120), "не нашлось", 84)
        draw.text((W / 2, panel[3] - 40), "попробуй другое слово)",
                  font=_font("Caveat-Bold.ttf", 44), fill=PINK, anchor="mm")

    box_w = (W - 112 - gap * (cols - 1)) / cols
    for i, (name, total) in enumerate(rest):
        r, c = divmod(i, cols)
        x0 = 56 + c * (box_w + gap)
        y0 = grid_top + r * (box_h + gap)
        _glass(base, (x0, y0, x0 + box_w, y0 + box_h), radius=24)
        font = _font("Manrope-Bold.ttf", 28)
        draw.text((x0 + 22, y0 + 18), _fit(draw, f"@{name}", font, box_w - 44), font=font, fill=TEXT)
        draw.text((x0 + 22, y0 + 60), f"{total:.1f}/10 · свободен",
                  font=_font("Manrope-Medium.ttf", 22), fill=MUTED)

    _footer(base, bot_username)
    return _png(base)


def _png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
