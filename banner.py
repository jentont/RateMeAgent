"""Баннер главного меню: python banner.py → assets/banner.png.

Стиль тот же, что у карточек (card.py): графит, серебро Unbounded, текст
Manrope, одна розовая пометка Caveat и солик. Справа — кусочек той самой
таблицы из 10, чтобы с первого взгляда было ясно, что бот делает.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFilter

import card
import config

SIZE = (1280, 720)
OUT = config.BASE_DIR / "assets" / "banner.png"

#: Пример строк на баннере. Цифры показательные, не чьи-то.
ROWS = (
    ("Аватар", 8.1),
    ("@username", 8.6),
    ("Подарки", 7.4),
)
TOTAL = 8.4


def _panel(base: Image.Image, box: tuple[int, int, int, int]) -> None:
    """Стеклянная плашка: светлее фона, тонкий кант, мягкая тень."""
    x0, y0, x1, y1 = box
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + 18, x1, y1 + 18), 40, fill=(0, 0, 0, 150))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(28)))
    glass = Image.new("RGBA", base.size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    gd.rounded_rectangle(box, 40, fill=(255, 255, 255, 14))
    gd.rounded_rectangle(box, 40, outline=(255, 255, 255, 34), width=2)
    base.alpha_composite(glass)


def render() -> Image.Image:
    base = card._background(SIZE)
    draw = ImageDraw.Draw(base)
    w, h = SIZE
    left = 84

    # --- левая часть: о чём бот ---------------------------------------------
    card._tracked(draw, (left, 112), "SOLUTIONS", card._font("Unbounded-Bold.ttf", 22), card.STEEL, 7)
    title = card._font("Unbounded-Black.ttf", 92)
    draw.text((left - 4, 162), "ОЦЕНКА", font=title, fill=card.TEXT)
    draw.text((left - 4, 268), "ПРОФИЛЯ", font=title, fill=card.SILVER)
    sub = card._font("Manrope-Medium.ttf", 32)
    draw.text((left, 404), "профили и ники Telegram", font=sub, fill=card.MUTED)
    draw.text((left, 446), "по 10-балльной шкале", font=sub, fill=card.MUTED)
    draw.text((left + 2, 520), "сколько ты из 10?)", font=card._font("Caveat-Bold.ttf", 54), fill=card.PINK)

    # --- правая часть: кусочек таблицы ------------------------------------------
    box = (742, 168, 1196, 620)
    _panel(base, box)
    card._solik(base, box[2] - 112, box[1], 118)
    px = box[0] + 40
    big = card._font("Unbounded-Black.ttf", 96)
    total = f"{TOTAL:.1f}"
    draw.text((px, box[1] + 30), total, font=big, fill=card.TEXT)
    tw = draw.textlength(total, font=big)
    draw.text((px + tw + 10, box[1] + 92), "/10", font=card._font("Manrope-Bold.ttf", 34), fill=card.MUTED)

    row_font = card._font("Manrope-Bold.ttf", 27)
    value_font = card._font("Unbounded-Bold.ttf", 28)
    y = box[1] + 176
    for name, value in ROWS:
        draw.text((px, y), name, font=row_font, fill=card.TEXT)
        draw.text((box[2] - 40, y + 2), f"{value:.1f}", font=value_font, fill=card.TEXT, anchor="ra")
        _scale(draw, px, box[2] - 40, y + 50, value)
        y += 98
    return base.convert("RGB")


def _scale(draw: ImageDraw.ImageDraw, x0: float, x1: float, y: float, value: float) -> None:
    """Десять делений, как на карточке, только по ширине плашки."""
    gap = 6
    cell = (x1 - x0 - gap * 9) / 10
    for i in range(10):
        cx = x0 + i * (cell + gap)
        draw.rounded_rectangle((cx, y - 6, cx + cell, y + 6), 4, fill=card.EMPTY)
        share = max(0.0, min(1.0, value - i))
        if share > 0:
            tone = tuple(int(s + (t - s) * i / 9) for s, t in zip(card.STEEL, card.TEXT))
            draw.rounded_rectangle((cx, y - 6, cx + max(8, cell * share), y + 6), 4, fill=tone)


if __name__ == "__main__":
    render().save(OUT, optimize=True)
    print(f"баннер → {OUT}")
