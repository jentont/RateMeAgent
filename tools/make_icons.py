"""Свой пак премиум-эмодзи бота: assets/emoji/*.tgs (генератор из Gift Drop).

Иконки контурные, белые, на сетке 24×24 — как в UnigramIcons. Белый цвет
не случайность: пак создаётся с needs_repainting, и Telegram перекрашивает
такие эмодзи в цвет текста. В тёмной теме они светлые, в светлой тёмные,
а в подписи админа — его цветом ника.

Рисуются иконки обычными SVG-путями (M, L, H, V, C, S, Q, A, Z — и
абсолютные, и относительные), а сюда переводятся в Lottie. Так их можно
править как обычный SVG, не разбираясь в формате TGS.

Запуск: python tools/make_icons.py [--png] — с --png рядом кладутся
превью, если стоит rlottie-python.
"""

from __future__ import annotations

import gzip
import json
import math
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "emoji"

SIZE = 512
PAD = 40
SCALE = (SIZE - 2 * PAD) / 24
STROKE = 2.0 * SCALE
FPS = 60
FRAMES = 120

# --- SVG-путь в кубические сегменты -------------------------------------

_TOKEN = re.compile(r"[MmLlHhVvCcSsQqAaZz]|-?\d*\.?\d+(?:e-?\d+)?")


def _arc(x1, y1, rx, ry, phi, large, sweep, x2, y2):
    """Дуга SVG → список кубических сегментов (c1, c2, end)."""
    if rx == 0 or ry == 0:
        return [((x1, y1), (x2, y2), (x2, y2))]
    phi = math.radians(phi)
    cos, sin = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cos * dx + sin * dy, -sin * dx + cos * dy
    rx, ry = abs(rx), abs(ry)
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp, cyp = coef * rx * y1p / ry, -coef * ry * x1p / rx
    cx = cos * cxp - sin * cyp + (x1 + x2) / 2
    cy = sin * cxp + cos * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    t1 = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dt = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and dt > 0:
        dt -= 2 * math.pi
    elif sweep and dt < 0:
        dt += 2 * math.pi

    parts = max(1, math.ceil(abs(dt) / (math.pi / 2)))
    step = dt / parts
    k = 4 / 3 * math.tan(step / 4)
    out = []
    t = t1
    for _ in range(parts):
        c_a, s_a = math.cos(t), math.sin(t)
        c_b, s_b = math.cos(t + step), math.sin(t + step)

        def pt(px, py):
            return (cx + cos * rx * px - sin * ry * py, cy + sin * rx * px + cos * ry * py)

        p1 = pt(c_a - k * s_a, s_a + k * c_a)
        p2 = pt(c_b + k * s_b, s_b - k * c_b)
        p3 = pt(c_b, s_b)
        out.append((p1, p2, p3))
        t += step
    return out


def parse(d: str) -> list[dict]:
    """SVG-путь → подпути: {'start', 'segs': [(c1, c2, end)], 'closed'}."""
    tokens = _TOKEN.findall(d)
    i = 0
    cmd = ""
    x = y = 0.0
    sx = sy = 0.0
    last_c2 = None
    paths: list[dict] = []
    cur: dict | None = None

    def num():
        nonlocal i
        value = float(tokens[i])
        i += 1
        return value

    def line_to(nx, ny):
        cur["segs"].append(((x, y), (nx, ny), (nx, ny)))

    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd in "Zz":
                if cur is not None:
                    if (x, y) != (sx, sy):
                        line_to(sx, sy)
                    cur["closed"] = True
                    cur = None
                x, y = sx, sy
                last_c2 = None
                continue
        rel = cmd.islower()
        c = cmd.upper()
        if c == "M":
            nx, ny = num(), num()
            if rel:
                nx, ny = x + nx, y + ny
            x, y = sx, sy = nx, ny
            cur = {"start": (x, y), "segs": [], "closed": False}
            paths.append(cur)
            cmd = "l" if rel else "L"
            last_c2 = None
        elif c == "L":
            nx, ny = num(), num()
            if rel:
                nx, ny = x + nx, y + ny
            line_to(nx, ny)
            x, y = nx, ny
            last_c2 = None
        elif c == "H":
            nx = num()
            nx = x + nx if rel else nx
            line_to(nx, y)
            x = nx
            last_c2 = None
        elif c == "V":
            ny = num()
            ny = y + ny if rel else ny
            line_to(x, ny)
            y = ny
            last_c2 = None
        elif c == "C":
            vals = [num() for _ in range(6)]
            if rel:
                vals = [v + (x if k % 2 == 0 else y) for k, v in enumerate(vals)]
            c1, c2, end = (vals[0], vals[1]), (vals[2], vals[3]), (vals[4], vals[5])
            cur["segs"].append((c1, c2, end))
            last_c2 = c2
            x, y = end
        elif c == "S":
            vals = [num() for _ in range(4)]
            if rel:
                vals = [v + (x if k % 2 == 0 else y) for k, v in enumerate(vals)]
            c1 = (2 * x - last_c2[0], 2 * y - last_c2[1]) if last_c2 else (x, y)
            c2, end = (vals[0], vals[1]), (vals[2], vals[3])
            cur["segs"].append((c1, c2, end))
            last_c2 = c2
            x, y = end
        elif c == "Q":
            vals = [num() for _ in range(4)]
            if rel:
                vals = [v + (x if k % 2 == 0 else y) for k, v in enumerate(vals)]
            q, end = (vals[0], vals[1]), (vals[2], vals[3])
            c1 = (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y))
            c2 = (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1]))
            cur["segs"].append((c1, c2, end))
            last_c2 = None
            x, y = end
        elif c == "A":
            rx, ry, phi, large, sweep, nx, ny = (num() for _ in range(7))
            if rel:
                nx, ny = x + nx, y + ny
            cur["segs"].extend(_arc(x, y, rx, ry, phi, int(large), int(sweep), nx, ny))
            x, y = nx, ny
            last_c2 = None
        else:
            raise ValueError(f"команда {cmd!r} не поддерживается")
    return paths


# --- Lottie ------------------------------------------------------------

def _p(point):
    return [round(PAD + point[0] * SCALE, 2), round(PAD + point[1] * SCALE, 2)]


def _shape(sub: dict) -> dict:
    """Подпуть → фигура Lottie: вершины и касательные относительно них."""
    verts = [sub["start"]] + [seg[2] for seg in sub["segs"]]
    ins = [(0.0, 0.0)] * len(verts)
    outs = [(0.0, 0.0)] * len(verts)
    for k, (c1, c2, end) in enumerate(sub["segs"]):
        start = verts[k]
        outs[k] = (c1[0] - start[0], c1[1] - start[1])
        ins[k + 1] = (c2[0] - end[0], c2[1] - end[1])
    if sub["closed"] and len(verts) > 1 and verts[-1] == verts[0]:
        # Замыкание Lottie делает само: последняя точка совпадает с первой
        # и сливается с ней, унося свою входящую касательную.
        ins[0] = ins[-1]
        verts, ins, outs = verts[:-1], ins[:-1], outs[:-1]
    return {
        "ty": "sh",
        "ks": {"a": 0, "k": {
            "i": [[round(a * SCALE, 2), round(b * SCALE, 2)] for a, b in ins],
            "o": [[round(a * SCALE, 2), round(b * SCALE, 2)] for a, b in outs],
            "v": [_p(v) for v in verts],
            "c": bool(sub["closed"]),
        }},
    }


def _ellipse(cx, cy, rx, ry=None) -> dict:
    ry = rx if ry is None else ry
    return {"ty": "el", "p": {"a": 0, "k": _p((cx, cy))},
            "s": {"a": 0, "k": [round(2 * rx * SCALE, 2), round(2 * ry * SCALE, 2)]}}


def _tr() -> dict:
    return {"ty": "tr", "p": {"a": 0, "k": [0, 0]}, "a": {"a": 0, "k": [0, 0]},
            "s": {"a": 0, "k": [100, 100]}, "r": {"a": 0, "k": 0},
            "o": {"a": 0, "k": 100}, "sk": {"a": 0, "k": 0}, "sa": {"a": 0, "k": 0}}


WHITE = {"a": 0, "k": [1, 1, 1, 1]}


def _ease(frames: list[tuple[int, list[float]]]) -> dict:
    keys = []
    dims = len(frames[0][1])
    for n, (t, value) in enumerate(frames):
        if n == len(frames) - 1:
            # Последний ключ без касательных: с ними rlottie (а это им
            # Telegram и рисует) считает отрезок до него пустым, и иконка
            # пропадает после первого же ключа.
            keys.append({"t": t, "s": value})
            continue
        keys.append({
            "t": t, "s": value,
            "i": {"x": [0.42] * dims, "y": [1] * dims},
            "o": {"x": [0.58] * dims, "y": [0] * dims},
        })
    return {"a": 1, "k": keys}


def _anim(kind: str) -> tuple[dict, dict]:
    """(rotation, scale) для слоя."""
    still_r = {"a": 0, "k": 0}
    still_s = {"a": 0, "k": [100, 100, 100]}
    if kind == "pulse":
        return still_r, _ease([(0, [100, 100, 100]), (30, [110, 110, 100]),
                               (60, [100, 100, 100]), (FRAMES, [100, 100, 100])])
    if kind == "beat":
        return still_r, _ease([(0, [100, 100, 100]), (12, [114, 114, 100]),
                               (24, [100, 100, 100]), (36, [110, 110, 100]),
                               (52, [100, 100, 100]), (FRAMES, [100, 100, 100])])
    if kind == "swing":
        return _ease([(0, [0]), (15, [-12]), (35, [10]), (55, [-6]), (72, [0]),
                      (FRAMES, [0])]), still_s
    if kind == "spin":
        return _ease([(0, [0]), (70, [360]), (FRAMES, [360])]), still_s
    if kind == "flip":
        return _ease([(0, [0]), (60, [0]), (95, [180]), (FRAMES, [180])]), still_s
    if kind == "pop":
        return still_r, _ease([(0, [86, 86, 100]), (22, [108, 108, 100]),
                               (40, [100, 100, 100]), (FRAMES, [100, 100, 100])])
    return still_r, still_s


def lottie(name: str, strokes: list[str], dots=(), fills=(), anim: str = "none") -> dict:
    """Собрать документ: контуры + залитые точки + залитые фигуры."""
    groups = []
    stroke_items = []
    for d in strokes:
        stroke_items.extend(_shape(sub) for sub in parse(d))
    if stroke_items:
        stroke_items.append({"ty": "st", "c": WHITE, "o": {"a": 0, "k": 100},
                             "w": {"a": 0, "k": round(STROKE, 2)}, "lc": 2, "lj": 2})
        stroke_items.append(_tr())
        groups.append({"ty": "gr", "nm": "lines", "it": stroke_items})
    fill_items = [_ellipse(*dot) for dot in dots]
    for d in fills:
        fill_items.extend(_shape(sub) for sub in parse(d))
    if fill_items:
        fill_items.append({"ty": "fl", "c": WHITE, "o": {"a": 0, "k": 100}, "r": 1})
        fill_items.append(_tr())
        groups.append({"ty": "gr", "nm": "fills", "it": fill_items})

    rotation, scale = _anim(anim)
    return {
        "tgs": 1, "v": "5.7.4", "fr": FPS, "ip": 0, "op": FRAMES,
        "w": SIZE, "h": SIZE, "nm": name, "ddd": 0, "assets": [],
        "layers": [{
            "ddd": 0, "ind": 1, "ty": 4, "nm": name, "sr": 1,
            "ks": {"o": {"a": 0, "k": 100}, "r": rotation,
                   "p": {"a": 0, "k": [SIZE / 2, SIZE / 2, 0]},
                   "a": {"a": 0, "k": [SIZE / 2, SIZE / 2, 0]}, "s": scale},
            "ao": 0, "shapes": groups, "ip": 0, "op": FRAMES, "st": 0, "bm": 0,
        }],
    }


# --- сами иконки -------------------------------------------------------

def _star(cx, cy, outer, inner, points=5, turn=-90.0) -> str:
    pts = []
    for k in range(points * 2):
        r = outer if k % 2 == 0 else inner
        a = math.radians(turn + k * 180 / points)
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return "M" + " L".join(f"{x:.3f} {y:.3f}" for x, y in pts) + " Z"


def _sparkle(cx, cy, r) -> str:
    q = r * 0.16
    return (f"M{cx} {cy - r} Q{cx + q} {cy - q} {cx + r} {cy} "
            f"Q{cx + q} {cy + q} {cx} {cy + r} Q{cx - q} {cy + q} {cx - r} {cy} "
            f"Q{cx - q} {cy - q} {cx} {cy - r} Z")


#: ключ → (обычный эмодзи, контуры, точки (cx, cy, r), заливки, анимация).
#: Обычный эмодзи — тот, что стоит в текстах и на кнопках бота: по нему
#: emoji.load находит иконку в паке. Порядок — порядок в паке.
ICONS: dict[str, tuple] = {
    "rocket": ("🚀", ["M12.5 15.5 L8.5 11.5 C10.5 6.5 14.5 3.5 20.5 3.5 C20.5 9.5 17.5 13.5 "
                     "12.5 15.5 Z", "M8.5 11.5 H5 L7.5 8 h4", "M12.5 15.5 V19 L16 16.5 v-4",
                     "M6.5 15 C4.8 16 4.3 18 4 20 C6 19.7 8 19.2 9 17.5"],
               [(15.6, 8.4, 1.3)], [], "pulse"),
    "user": ("👤", ["M12 12 a4 4 0 1 0 0 -8 a4 4 0 1 0 0 8 Z",
                   "M4.5 21 a7.5 6.5 0 0 1 15 0"], [], [], "pop"),
    "users": ("👥", ["M9 11.5 a3.5 3.5 0 1 0 0 -7 a3.5 3.5 0 1 0 0 7 Z",
                    "M2.5 20.5 a6.5 6 0 0 1 13 0", "M15.8 4.6 a3.5 3.5 0 0 1 0 6.8",
                    "M18 14.2 a5.2 5.6 0 0 1 3.5 6.3"], [], [], "pop"),
    "tag": ("🏷", ["M3.5 11.8 V4.8 a1.3 1.3 0 0 1 1.3 -1.3 h7 L20.6 12.3 a1.5 1.5 0 0 1 0 2.1 "
                  "l-6.2 6.2 a1.5 1.5 0 0 1 -2.1 0 Z"], [(8.2, 8.2, 1.5)], [], "swing"),
    "search": ("🔍", ["M10.5 17.5 a7 7 0 1 0 0 -14 a7 7 0 1 0 0 14 Z", "M15.6 15.6 L20.5 20.5"],
               [], [], "pulse"),
    "trophy": ("🏆", [
        "M7 3.5 h10 v5.5 a5 5 0 0 1 -10 0 Z",
        "M7 5.5 H4.5 v1.2 a3.3 3.3 0 0 0 3 3.3",
        "M17 5.5 h2.5 v1.2 a3.3 3.3 0 0 1 -3 3.3",
        "M12 14 v4", "M8 21 h8 l-1 -3 h-6 Z",
    ], [], [], "pop"),
    "question": ("❓", ["M12 21.5 a9.5 9.5 0 1 0 0 -19 a9.5 9.5 0 1 0 0 19 Z",
                       "M9.4 9.4 a2.7 2.7 0 1 1 3.9 2.4 c-0.8 0.4 -1.3 1 -1.3 1.9 v0.5"],
                 [(12, 17, 1.25)], [], "pop"),
    "back": ("⬅️", ["M19.5 12 h-15", "M10.5 6 L4.5 12 L10.5 18"], [], [], "pulse"),
    "refresh": ("🔄", [
        "M19.5 10.5 A7.8 7.8 0 0 0 5.6 7.4", "M4.5 3.5 v4.4 h4.4",
        "M4.5 13.5 A7.8 7.8 0 0 0 18.4 16.6", "M19.5 20.5 v-4.4 h-4.4",
    ], [], [], "spin"),
    "chart": ("📊", ["M3.5 20.5 h17", "M7 16.5 v-4", "M12 16.5 V6.5", "M17 16.5 v-7"],
              [], [], "pulse"),
    "bulb": ("💡", ["M9.2 17 C9.2 14.6 6 13.4 6 9.6 a6 6 0 0 1 12 0 C18 13.4 14.8 14.6 14.8 17 Z",
                   "M9.5 20.5 h5"], [], [], "pulse"),
    # Шкала из 10 — вместо «💯»: стрелка у края, как на спидометре.
    "gauge": ("💯", ["M3.5 17.5 a8.5 8.5 0 0 1 17 0", "M12 17.5 L16.6 11.3",
                    "M6.3 11.2 l1.2 1", "M12 9 v1.5", "M17.7 11.2 l-1.2 1"],
              [(12, 17.5, 1.5)], [], "pulse"),
    "star": ("⭐", [_star(12, 12.6, 9.6, 4.3)], [], [], "pulse"),
    "gift": ("🎁", [
        "M3.5 8.5 h17 v3.5 h-17 Z",
        "M5 12 v7.5 a1.5 1.5 0 0 0 1.5 1.5 h11 a1.5 1.5 0 0 0 1.5 -1.5 V12",
        "M12 8.5 V21",
        "M12 8.5 C10.5 5 7.5 3.8 6.8 5.6 C6.2 7.2 8.6 8.5 12 8.5",
        "M12 8.5 C13.5 5 16.5 3.8 17.2 5.6 C17.8 7.2 15.4 8.5 12 8.5",
    ], [], [], "pop"),
    "swords": ("⚔️", ["M9.6 14.4 L20 4", "M7.4 12.2 l4.4 4.4", "M9.6 14.4 L6.2 17.8",
                     "M14.4 14.4 L4 4", "M16.6 12.2 l-4.4 4.4", "M14.4 14.4 L17.8 17.8"],
               [(5.2, 18.8, 1.35), (18.8, 18.8, 1.35)], [], "swing"),
    "wallet": ("👛", ["M5.5 4 h10.5 v3", "M3.5 6 a2 2 0 0 1 2 -2",
                     "M3.5 6 v12.5 a2 2 0 0 0 2 2 h13 a2 2 0 0 0 2 -2 V9 a2 2 0 0 0 -2 -2 h-15",
                     "M20.5 11.5 h-3.8 a2 2 0 0 0 0 4 h3.8"], [(16.8, 13.5, 1.0)], [], "pop"),
    "hourglass": ("⏳", [
        "M6 2.5 h12", "M6 21.5 h12",
        "M7.5 2.5 v3 C7.5 9 12 10 12 12 C12 14 7.5 15 7.5 18.5 v3",
        "M16.5 2.5 v3 C16.5 9 12 10 12 12 C12 14 16.5 15 16.5 18.5 v3",
    ], [], ["M9.5 19.5 C9.5 17.6 12 16.2 12 16.2 C12 16.2 14.5 17.6 14.5 19.5 Z"], "flip"),
    "check": ("✅", ["M12 21.5 a9.5 9.5 0 1 0 0 -19 a9.5 9.5 0 1 0 0 19 Z",
                    "M7.8 12.4 L10.7 15.3 L16.3 9.2"], [], [], "pop"),
    "cross": ("❌", ["M12 21.5 a9.5 9.5 0 1 0 0 -19 a9.5 9.5 0 1 0 0 19 Z",
                    "M9 9 L15 15", "M15 9 L9 15"], [], [], "pop"),
    "warning": ("⚠️", ["M10.3 4 L2.6 17.6 A2 2 0 0 0 4.3 20.5 H19.7 A2 2 0 0 0 21.4 17.6 "
                      "L13.7 4 A2 2 0 0 0 10.3 4 Z", "M12 9.2 v4.6"],
                [(12, 17, 1.25)], [], "beat"),
    "chat": ("💬", ["M6 4 h12 a2.5 2.5 0 0 1 2.5 2.5 v7.5 a2.5 2.5 0 0 1 -2.5 2.5 H10 "
                   "L5.5 20.5 V16.5 H6 a2.5 2.5 0 0 1 -2.5 -2.5 V6.5 A2.5 2.5 0 0 1 6 4 Z"],
             [(8.5, 10.3, 1.15), (12, 10.3, 1.15), (15.5, 10.3, 1.15)], [], "pop"),
    "down": ("👇", ["M12 4.5 v15", "M6 13.5 L12 19.5 L18 13.5"], [], [], "pulse"),
    "crown": ("👑", ["M3 7.5 L7.2 12.8 L12 5 L16.8 12.8 L21 7.5 L19 18 H5 Z", "M5.5 21 h13"],
              [], [], "pop"),
    "fire": ("🔥", [
        "M12 21.5 C8 21.5 5.5 18.8 5.5 15.2 C5.5 11.2 9.3 9.6 9.6 5 C11.8 6.6 12.9 8.8 "
        "12.8 10.8 C13.9 9.8 14.4 8.4 14.3 6.8 C17.2 9.2 18.5 12.2 18.5 15.2 "
        "C18.5 18.8 16 21.5 12 21.5 Z",
    ], [], [], "beat"),
    "diamond": ("💎", ["M6.5 3.5 h11 L21.5 9 L12 20.5 L2.5 9 Z", "M2.5 9 h19",
                      "M12 20.5 L8.6 9 L10 3.5", "M12 20.5 L15.4 9 L14 3.5"],
                [], [], "pulse"),
    "lock": ("🔒", ["M7 11 h10 a2 2 0 0 1 2 2 v6 a2 2 0 0 1 -2 2 h-10 a2 2 0 0 1 -2 -2 "
                   "v-6 a2 2 0 0 1 2 -2 Z", "M8 11 V8 a4 4 0 0 1 8 0 v3"],
             [(12, 16, 1.3)], [], "swing"),
    "send": ("📤", ["M21 3 L3 10.6 L10.2 13.8 L13.4 21 Z", "M10.2 13.8 L21 3"], [], [],
             "pulse"),
}


def build(png: bool = False) -> list[str]:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for key, (emoji, strokes, dots, fills, anim) in ICONS.items():
        doc = lottie(key, strokes, dots, fills, anim)
        raw = json.dumps(doc, separators=(",", ":")).encode()
        target = OUT / f"{key}.tgs"
        target.write_bytes(gzip.compress(raw, 9))
        size = target.stat().st_size
        if size > 64 * 1024:
            raise SystemExit(f"{key}.tgs вышел {size} байт — Telegram примет до 64 КБ")
        manifest.append({"key": key, "emoji": emoji})
        if png:
            _render(target, OUT / "_preview" / f"{key}.png")
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return [item["key"] for item in manifest]


def _render(source: Path, target: Path) -> None:
    from rlottie_python import LottieAnimation

    target.parent.mkdir(parents=True, exist_ok=True)
    anim = LottieAnimation.from_tgs(str(source))
    anim.save_frame(str(target), frame_num=FRAMES - 1)


if __name__ == "__main__":
    keys = build(png="--png" in sys.argv)
    print(f"иконок: {len(keys)} → {OUT}")
