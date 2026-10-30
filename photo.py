"""Оценка самой аватарки, а не количества фото в профиле.

Бот считал фотографии: чем их больше, тем выше балл. Это неправильно по
сути — человек с одним хорошим снимком проигрывал тому, кто залил
двадцать размытых, — и легко накручивается: фото добавляются за минуту.

Здесь оценивается сам кадр. Нейросети нет и не нужно: то, что отличает
приличную аватарку от мусорной, измеряется арифметикой.

* **Резкость** — главное. Размытая фотография, скриншот из пережатого
  мессенджера, кадр в движении: у всех низкий контраст на границах.
  Меряется разбросом яркости после выделения краёв.
* **Настоящая детализация.** Размер в пикселях ничего не говорит:
  Telegram отдаёт все аватарки одинакового размера, и картинка из ста
  пикселей, растянутая до шестисот, по размеру неотличима от съёмки.
  Поэтому кадр сравнивается сам с собой, ужатым вдвое и растянутым
  обратно: у настоящего снимка при этом теряются детали, у растянутого
  терять нечего.
* **Контраст.** Тёмное пятно, белый квадрат, однотонная заливка — всё
  это узнаётся по узкому разбросу яркостей.
* **Цветность.** Чёрно-белый квадрат и однотонный фон отличаются от
  живого снимка разбросом по каналам.

Всё считается на копии в 256 пикселей: разница в цифрах после такого
уменьшения в третьем знаке, а полноразмерный фильтр стоил бы заметного
времени на каждом баттле.
"""

from __future__ import annotations

import io
import logging

from PIL import Image, ImageFilter, ImageStat

log = logging.getLogger("profilerate.photo")

#: До какого размера ужимать перед замерами.
WORK_SIZE = 256

#: Границы, между которыми признак растягивается в 0..1. Подобраны по
#: живым аватаркам: ниже нижней — заведомо плохо, выше верхней — разницы
#: на глаз уже нет.
SHARP_RANGE = (4.0, 26.0)
CONTRAST_RANGE = (18.0, 70.0)
COLOR_RANGE = (4.0, 45.0)
DETAIL_RANGE = (0.6, 6.0)

#: Веса признаков. Резкость решает: именно она отличает фотографию от
#: мыла, а всё остальное — уточнения.
WEIGHTS = {"sharp": 0.45, "detail": 0.25, "contrast": 0.18, "color": 0.12}


def _scale(value: float, low: float, high: float) -> float:
    return max(0.0, min(1.0, (value - low) / (high - low)))


def _edges(gray: Image.Image) -> float:
    """Разброс яркости по границам.

    Рамка в два пикселя срезается: FIND_EDGES подсвечивает край холста
    сам по себе, и без обрезки даже сплошная заливка выглядела резкой —
    серый квадрат получал полбалла из ничего.
    """
    found = gray.filter(ImageFilter.FIND_EDGES)
    width, height = found.size
    if width > 6 and height > 6:
        found = found.crop((2, 2, width - 2, height - 2))
    return ImageStat.Stat(found).stddev[0]


def measure(raw: bytes | None) -> dict[str, float]:
    """Признаки снимка по отдельности. Пустой словарь — фото нет."""
    if not raw:
        return {}

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as error:
        log.debug("аватарку не разобрать: %s", error)
        return {}

    work = image.copy()
    work.thumbnail((WORK_SIZE, WORK_SIZE), Image.LANCZOS)
    gray = work.convert("L")

    # FIND_EDGES вместо матрицы Лапласа: numpy в зависимостях нет, а
    # разброс яркости по найденным краям даёт ту же картину — у резкого
    # кадра границы яркие, у размытого их почти нет.
    edges = _edges(gray)

    # Тот же замер на кадре, ужатом вдвое и растянутом обратно. У снимка
    # с настоящей детализацией разница большая, у растянутого апскейла —
    # почти нулевая: терять там уже нечего.
    half = gray.resize(
        (max(1, gray.width // 2), max(1, gray.height // 2)), Image.BILINEAR
    ).resize(gray.size, Image.BILINEAR)
    detail = max(0.0, edges - _edges(half))

    contrast = ImageStat.Stat(gray).stddev[0]
    channels = ImageStat.Stat(work).mean
    color = max(channels) - min(channels) + ImageStat.Stat(work).stddev[1]

    return {
        "sharp": _scale(edges, *SHARP_RANGE),
        "detail": _scale(detail, *DETAIL_RANGE),
        "contrast": _scale(contrast, *CONTRAST_RANGE),
        "color": _scale(color, *COLOR_RANGE),
    }


def quality(raw: bytes | None) -> float:
    """Качество аватарки, 0..1. Ноль — фото нет или не открылось."""
    parts = measure(raw)
    if not parts:
        return 0.0
    return round(sum(parts[key] * weight for key, weight in WEIGHTS.items()), 3)


def verdict(value: float) -> str:
    """Словами — для подписи под метрикой на карточке."""
    if value <= 0:
        return "нет фото"
    if value < 0.25:
        return "мыло"
    if value < 0.45:
        return "так себе"
    if value < 0.65:
        return "нормальное"
    if value < 0.82:
        return "чёткое"
    return "отличное"
