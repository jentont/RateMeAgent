"""Оценка юзернейма по тому, за что за него платят.

Раньше метрика была линейкой: `9.8 − 0.35 × длина`. По ней `@kwjpz`
стоил ровно столько же, сколько `@storm`, — пять букв есть пять букв.
Рынок так не считает: покупают не короткие строки, а слова.

Логика перенесена из соседнего бота-оценщика имён, где она построена на
реальных продажах Fragment. Здесь от неё взята та часть, что не требует
похода в сеть: разбор самого имени по шести признакам.

* **Слово** — главное. `@storm` и `@stworn` читаются одинаково, но
  первое есть в словаре, и это единственный честный признак цены.
* **Произносимость** — частокол согласных не продиктовать по телефону.
* **Длина** — короткое дороже, но само по себе ничего не стоит.
* **Символы** — цифры обнуляют цену, подчёркивание сильно роняет.
* **Набор** — похожие знаки (`l`/`1`, `0`/`o`) заставляют переспрашивать.
* **Повторы** — три одинаковые буквы подряд выглядят опечаткой.

Ключевая тонкость в свёртке: длина, отсутствие цифр и удобство набора
не складываются с главным, а умножаются на него. Иначе `@kwjpz` набирал
бы почти половину балла за одну только краткость.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache

import config

log = logging.getLogger("profilerate.naming")

VOWELS = set("aeiouy")

#: Словари лежат рядом с ботом и читаются один раз.
_WORDS: set[str] | None = None
_FAMOUS: set[str] | None = None


def _load(filename: str) -> set[str]:
    path = config.BASE_DIR / "assets" / filename
    found: set[str] = set()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                continue
            found.update(word for word in line.split() if len(word) >= 3)
    except OSError as error:
        log.warning("словарь %s не прочитан: %s", path, error)
    return found


def words() -> set[str]:
    global _WORDS
    if _WORDS is None:
        _WORDS = _load("words.txt")
    return _WORDS


def famous() -> set[str]:
    global _FAMOUS
    if _FAMOUS is None:
        _FAMOUS = _load("names.txt")
    return _FAMOUS


@lru_cache(maxsize=1)
def vocabulary() -> frozenset[str]:
    """Оба словаря одним множеством.

    Слияние кэшируется: без этого пара тысяч слов пересыпалась в новое
    множество на каждый вызов, а вызовов на одно имя три, и ещё столько
    же на каждую сделку, с которой это имя сравнивается.
    """
    return frozenset(words() | famous())


@lru_cache(maxsize=8192)
def word_kind(name: str) -> tuple[str, float]:
    """Насколько имя — настоящее слово. Подпись и вес 0..1."""
    plain = name.strip("_")
    if plain in famous():
        return "известное имя", 1.0
    if plain in words():
        return "слово", 1.0

    known = vocabulary()
    letters = "".join(ch for ch in plain if ch.isalpha())
    if letters != plain and letters in known:
        return "слово с цифрами", 0.7

    # Слово плюс короткий хвост: storm7, storms, cryptox.
    for cut in range(len(plain) - 1, 2, -1):
        if plain[:cut] in known and len(plain) - cut <= 2:
            return "слово с хвостом", 0.6

    # Два слова подряд: goldfish, sunbeam.
    for cut in range(3, len(plain) - 2):
        if plain[:cut] in known and plain[cut:] in known:
            return "два слова", 0.9

    # Идём по подстрокам имени, а не по словарю: подстрок десятки, а
    # словарь — пара тысяч слов, и полный проход превращал разбор
    # одного баттла в заметные доли секунды.
    for size in range(len(plain), 3, -1):
        for start in range(0, len(plain) - size + 1):
            if plain[start : start + size] in known:
                return "содержит слово", 0.45
    return "не слово", 0.0


def _speech(name: str) -> float:
    """Произносимость без словарной надбавки."""
    letters = [ch for ch in name if ch.isalpha()]
    if not letters:
        return 0.0

    vowels = sum(1 for ch in letters if ch in VOWELS)
    share = vowels / len(letters)
    vowel_score = 1.0 if 0.3 <= share <= 0.55 else max(0.0, 1 - abs(share - 0.42) * 4)

    run = worst = 0
    for char in name:
        run = 0 if char in VOWELS else run + 1
        worst = max(worst, run)
    run_score = {0: 0.5, 1: 1.0, 2: 0.9, 3: 0.35}.get(worst, 0.05)

    pairs = sum(1 for a, b in zip(name, name[1:]) if (a in VOWELS) != (b in VOWELS))
    alternation = pairs / max(1, len(name) - 1)

    score = vowel_score * 0.55 + run_score * 0.3 + alternation * 0.15
    if vowels <= 1 and len(letters) >= 5:
        score = min(score, 0.3)

    # Настоящее слово произносимо по определению: правило про долю
    # гласных иначе топит storm, crypt и spark — одна гласная на пять
    # букв это норма английского, а не согласная каша.
    if word_kind(name)[1] >= 0.6:
        score = max(score, 0.75 + 0.25 * word_kind(name)[1])
    return max(0.0, min(1.0, score))


def typing_ease(name: str) -> float:
    """Легко ли продиктовать без переспрашивания."""
    if "_" in name:
        return 0.2
    confusing = sum(1 for ch in name if ch in "l1o0i")
    rare = sum(1 for ch in name if ch in "qxzjv")
    if confusing >= 3 or rare >= 3:
        return 0.3
    if confusing or rare:
        return 0.7
    return 1.0


def repeats(name: str) -> float:
    if re.search(r"(.)\1{2,}", name):
        return 0.1
    if re.search(r"(.)\1", name):
        return 0.6
    return 1.0


def length_score(name: str) -> float:
    """Короче — дороже. Ниже пяти Telegram не пускает, выше двенадцати
    имя перестают набирать руками."""
    table = {5: 1.0, 6: 0.85, 7: 0.7, 8: 0.55, 9: 0.45, 10: 0.35, 11: 0.28}
    size = len(name)
    return table.get(size, 0.2 if size > 11 else 1.0)


#: Признак, его вес и подпись для разбора.
def breakdown(name: str) -> tuple[tuple[str, float, float], ...]:
    digits = any(ch.isdigit() for ch in name)
    symbols = 0.0 if digits else (0.4 if "_" in name else 1.0)
    return (
        ("слово", word_kind(name)[1], 0.30),
        ("произносимость", _speech(name), 0.25),
        ("длина", length_score(name), 0.20),
        ("символы", symbols, 0.12),
        ("набор", typing_ease(name), 0.08),
        ("повторы", repeats(name), 0.05),
    )


@lru_cache(maxsize=8192)
def appeal(username: str | None) -> float:
    """Насколько имя вообще кому-то нужно, 0..1.

    Кэшируется: в баттле имя оценивается дважды, а в топе — десятками, и
    словарный разбор одного и того же имени незачем повторять.
    """
    if not username:
        return 0.0
    name = username.lstrip("@").lower()
    items = breakdown(name)
    total = sum(weight for _, _, weight in items) or 1.0
    base = sum(value * weight for _, value, weight in items) / total

    # Длина, отсутствие цифр и удобство набора сами по себе не стоят
    # ничего: без слова и без произносимости имя остаётся мусором, каким
    # бы коротким ни было. Поэтому они умножаются на главное, а не
    # складываются с ним.
    core = max(items[0][1], items[1][1], scarcity(name))
    return max(0.0, min(1.0, base * (0.25 + 0.75 * core)))


def scarcity(name: str) -> float:
    """Ценность одной только редкости, 0..1.

    Правило «не слово — значит мусор» верно почти всегда, но ломается на
    самом верху рынка. Telegram не даёт занять имя короче пяти символов:
    четырёхбуквенные и короче существуют только через аукцион Fragment, и
    уходят там за тысячи долларов независимо от того, слово это или нет.
    `@qq` не читается и не значит ничего — и стоит больше, чем `@goldfish`.
    """
    return {1: 1.0, 2: 1.0, 3: 0.9, 4: 0.7}.get(len(name), 0.0)


def verdict(username: str | None) -> str:
    """Короткая подпись для карточки."""
    if not username:
        return "не задан"
    return word_kind(username.lstrip("@").lower())[0]
