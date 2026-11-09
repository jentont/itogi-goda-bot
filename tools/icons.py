"""Белые иконки в стиле iOS (SF Symbols) для кнопок бота — отдельный пак.

Как в паке tgiosicons: одноцветные белые глифы с круглыми концами линий.
Пак создаётся с needs_repainting=True — Telegram перекрашивает иконки в цвет
текста (белые в тёмной теме, тёмные в светлой). Уточки остаются в тексте,
а на кнопках — эти иконки.

Рисуем векторно (Lottie), холст 512×512, 60 к/с, 2 с; лёгкая анимация.
Запуск: python tools/icons.py [--preview]  → assets/icons/*.tgs
"""

from __future__ import annotations

import gzip
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "icons"
FR, OP = 60, 120
W = 40  # толщина линий на холсте 512 (после ужатия до 100 — ~8 px, как у SF Symbols)
WHITE = [1, 1, 1, 1]


def v(k):
    return {"a": 0, "k": k}


def anim(frames):
    keys = []
    for i, (t, val) in enumerate(frames):
        key = {"t": t, "s": val}
        if i < len(frames) - 1:
            key["i"] = {"x": [0.42] * len(val), "y": [1] * len(val)}
            key["o"] = {"x": [0.58] * len(val), "y": [0] * len(val)}
        keys.append(key)
    return {"a": 1, "k": keys}


def fill(op=100):
    return {"ty": "fl", "c": v(WHITE), "o": op if isinstance(op, dict) else v(op), "r": 1}


def stroke(width=W, op=100):
    return {"ty": "st", "c": v(WHITE), "o": op if isinstance(op, dict) else v(op), "w": v(width), "lc": 2, "lj": 2}


def trim(end):
    return {"ty": "tm", "s": v(0), "e": end, "o": v(0), "m": 1}


def tr(p=(0, 0), a=(0, 0), s=(100, 100), r=0, o=100):
    def val(x):
        return x if isinstance(x, dict) else v(list(x) if isinstance(x, tuple) else x)
    return {"ty": "tr", "p": val(p), "a": val(a), "s": val(s), "r": val(r), "o": val(o)}


def path(points, closed=False, curve=None):
    """Ломаная; curve — словарь {индекс: (вход, выход)} касательных для дуг."""
    ins = [[0, 0] for _ in points]
    outs = [[0, 0] for _ in points]
    for i, (a, b) in (curve or {}).items():
        ins[i], outs[i] = list(a), list(b)
    return {"ty": "sh", "ks": v({"i": ins, "o": outs, "v": [list(p) for p in points], "c": closed})}


def arc(cx, cy, r, a0, a1, steps=16):
    """Дуга окружности ломаной из steps отрезков (углы в градусах, 0 — вправо, по часовой)."""
    pts = []
    for k in range(steps + 1):
        t = math.radians(a0 + (a1 - a0) * k / steps)
        pts.append((cx + r * math.cos(t), cy + r * math.sin(t)))
    return path(pts)


def ellipse(cx, cy, w, h):
    return {"ty": "el", "p": v([cx, cy]), "s": v([w, h])}


def rect(cx, cy, w, h, r=0):
    return {"ty": "rc", "p": v([cx, cy]), "s": v([w, h]), "r": v(r)}


def star(cx, cy, outer, inner, points=5, rot=0, roundness=0):
    return {"ty": "sr", "sy": 1, "d": 1, "pt": v(points), "p": v([cx, cy]), "r": v(rot),
            "ir": v(inner), "is": v(roundness), "or": v(outer), "os": v(roundness)}


def group(*items, t=None):
    return {"ty": "gr", "it": list(items) + [t or tr()]}


def layer(shapes, ks=None):
    base = {"o": v(100), "r": v(0), "p": v([256, 256, 0]), "a": v([256, 256, 0]), "s": v([100, 100, 100])}
    base.update(ks or {})
    return {"ddd": 0, "ind": 1, "ty": 4, "sr": 1, "ks": base, "ao": 0, "shapes": shapes,
            "ip": 0, "op": OP, "st": 0, "bm": 0}


def doc(*layers):
    for i, ly in enumerate(layers, 1):
        ly["ind"] = i
    return {"tgs": 1, "v": "5.7.4", "fr": FR, "ip": 0, "op": OP, "w": 512, "h": 512,
            "ddd": 0, "assets": [], "layers": list(layers)}


def loop3(a, b):
    return anim([(0, a), (60, b), (120, a)])


def pivot(x, y):
    return {"a": v([x, y, 0]), "p": v([x, y, 0])}


# --------------------------------------------------------------- глифы

def i_back():
    return doc(layer([group(path([(300, 120), (170, 256), (300, 392)]), stroke(52))],
                     {"p": loop3([256, 256, 0], [236, 256, 0])}))


def i_plus():
    return doc(layer([group(path([(256, 110), (256, 402)]), path([(110, 256), (402, 256)]), stroke(52))],
                     {"r": anim([(0, [0]), (40, [90]), (120, [90])]), **pivot(256, 256)}))


def i_close():
    return doc(layer([group(path([(140, 140), (372, 372)]), path([(372, 140), (140, 372)]), stroke(52))],
                     {"s": loop3([100, 100, 100], [88, 88, 100]), **pivot(256, 256)}))


def i_check():
    circle = group(ellipse(256, 256, 400, 400), fill())
    tick = group(path([(160, 262), (232, 334), (360, 192)]), stroke(48), trim(anim([(0, [0]), (40, [100]), (120, [100])])))
    # Галочка «вырезана» из круга: рисуем её цветом фона нельзя (одноцветный пак),
    # поэтому — обводка круга и галочка внутри.
    ring = group(ellipse(256, 256, 380, 380), stroke(36))
    return doc(layer([tick]), layer([ring]))


def i_sparkles():
    big = layer([group(star(220, 270, 150, 34, 4, roundness=20), fill())], {"s": loop3([100, 100, 100], [86, 86, 100]), **pivot(220, 270)})
    small = layer([group(star(380, 130, 70, 16, 4, roundness=20), fill())],
                  {"s": anim([(0, [60, 60, 100]), (60, [110, 110, 100]), (120, [60, 60, 100])]), **pivot(380, 130)})
    tiny = layer([group(star(390, 380, 46, 11, 4, roundness=20), fill())],
                 {"s": anim([(0, [110, 110, 100]), (60, [50, 50, 100]), (120, [110, 110, 100])]), **pivot(390, 380)})
    return doc(small, tiny, big)


def i_gift():
    box = group(rect(256, 330, 330, 220, 30), stroke(36))
    ribbon = group(path([(256, 222), (256, 440)]), stroke(36))
    lid = layer([group(rect(256, 200, 380, 70, 22), stroke(36)),
                 group(path([(256, 166), (190, 96), (150, 130), (256, 166)], curve={1: ((20, 10), (-30, -20))}),
                       path([(256, 166), (322, 96), (362, 130), (256, 166)], curve={1: ((-20, 10), (30, -20))}), stroke(30))],
                {"p": anim([(0, [256, 256, 0]), (20, [256, 226, 0]), (40, [256, 256, 0]), (120, [256, 256, 0])])})
    return doc(lid, layer([box, ribbon]))


def i_people():
    front = group(ellipse(210, 190, 130, 130), stroke(36), t=tr())
    body = group(path([(90, 420), (100, 360), (150, 310), (210, 300), (270, 310), (320, 360), (330, 420)],
                      curve={3: ((-40, 0), (40, 0))}), stroke(36))
    back = group(ellipse(345, 170, 100, 100), stroke(32))
    back_body = group(path([(330, 270), (380, 280), (420, 320), (432, 380)]), stroke(32))
    return doc(layer([front, body], {"s": loop3([100, 100, 100], [104, 104, 100]), **pivot(210, 300)}),
               layer([back, back_body], {"o": loop3([60], [100])}))


def i_megaphone():
    horn = group(path([(110, 210), (300, 120), (300, 392), (110, 302)], closed=True), stroke(36))
    mouth = group(rect(110, 256, 40, 92, 10), fill())
    handle = group(path([(150, 302), (175, 400), (215, 400), (205, 318)]), stroke(30))
    waves = layer([group(arc(300, 256, 90, -35, 35), stroke(30)), group(arc(300, 256, 150, -35, 35), stroke(30))],
                  {"o": anim([(0, [0]), (30, [100]), (90, [100]), (120, [0])])})
    return doc(waves, layer([horn, mouth, handle], {"r": loop3([0], [-6]), **pivot(200, 256)}))


def i_trash():
    can = group(path([(150, 190), (175, 430), (337, 430), (362, 190)]), stroke(36))
    lines = group(path([(220, 240), (226, 380)]), path([(292, 240), (286, 380)]), stroke(28))
    lid = layer([group(path([(110, 170), (402, 170)]), path([(210, 170), (220, 120), (292, 120), (302, 170)]), stroke(36))],
                {"r": anim([(0, [0]), (25, [-18]), (50, [0]), (120, [0])]), **pivot(130, 170)})
    return doc(lid, layer([can, lines]))


def i_refresh():
    circle = group(arc(256, 256, 150, -60, 230, 24), stroke(40))
    head = group(path([(300, 70), (340, 130), (270, 150)]), stroke(40))
    return doc(layer([circle, head], {"r": anim([(0, [0]), (60, [360]), (120, [360])]), **pivot(256, 256)}))


def i_chart():
    bars = []
    for i, (x, h) in enumerate(((150, 120), (256, 210), (362, 300))):
        bars.append(layer([group(rect(x, 420 - h / 2, 70, h, 18), fill())],
                          {"s": anim([(i * 8, [100, 30, 100]), (i * 8 + 30, [100, 100, 100]), (100, [100, 100, 100]), (120, [100, 30, 100])]),
                           **pivot(x, 420)}))
    return doc(*bars)


def i_envelope():
    body = group(rect(256, 266, 380, 270, 40), stroke(36))
    flap = layer([group(path([(80, 160), (256, 290), (432, 160)]), stroke(36))],
                 {"s": anim([(0, [100, 100, 100]), (30, [100, -60, 100]), (60, [100, 100, 100]), (120, [100, 100, 100])]), **pivot(256, 150)})
    return doc(flap, layer([body]))


def i_lock():
    body = group(rect(256, 330, 290, 220, 40), fill())
    shackle = layer([group(path([(176, 220), (176, 170), (256, 90), (336, 170), (336, 220)],
                                curve={2: ((-60, 0), (60, 0))}), stroke(40))],
                    {"p": anim([(0, [256, 256, 0]), (40, [256, 256, 0]), (60, [256, 230, 0]), (80, [256, 256, 0]), (120, [256, 256, 0])])})
    return doc(shackle, layer([body]))


def i_target():
    rings = [group(ellipse(256, 256, d, d), stroke(32)) for d in (380, 240)]
    dot = layer([group(ellipse(256, 256, 70, 70), fill())], {"s": loop3([100, 100, 100], [140, 140, 100]), **pivot(256, 256)})
    return doc(dot, layer(rings))


def i_rocket():
    body = group(path([(256, 70), (330, 170), (330, 330), (182, 330), (182, 170)], closed=True,
                      curve={0: ((-50, 0), (50, 0))}), stroke(36))
    window = group(ellipse(256, 200, 70, 70), stroke(28))
    fins = group(path([(182, 260), (130, 330), (182, 330)]), path([(330, 260), (382, 330), (330, 330)]), stroke(30))
    flame = layer([group(path([(222, 360), (256, 450), (290, 360)], closed=True), fill())],
                  {"s": anim([(0, [100, 70, 100]), (15, [100, 110, 100]), (30, [100, 70, 100]), (45, [100, 110, 100]),
                             (60, [100, 70, 100]), (90, [100, 110, 100]), (120, [100, 70, 100])]), **pivot(256, 360)})
    return doc(flame, layer([body, window, fins], {"p": loop3([256, 256, 0], [256, 240, 0])}))


def i_key():
    head = group(ellipse(170, 256, 170, 170), stroke(36))
    shaft = group(path([(255, 256), (430, 256)]), path([(380, 256), (380, 320)]), path([(430, 256), (430, 310)]), stroke(36))
    return doc(layer([head, shaft], {"r": anim([(0, [0]), (30, [-20]), (60, [0]), (120, [0])]), **pivot(256, 256)}))


def i_coin():
    rim = group(ellipse(256, 256, 380, 380), stroke(36))
    sign = group(path([(306, 176), (226, 176), (206, 216), (226, 256), (286, 256), (306, 296), (286, 336), (206, 336)],
                      curve={2: ((0, -20), (0, 20)), 5: ((0, -20), (0, 20))}),
                 path([(256, 140), (256, 372)]), stroke(30))
    return doc(layer([rim, sign], {"s": anim([(0, [100, 100, 100]), (30, [-100, 100, 100]), (60, [100, 100, 100]), (120, [100, 100, 100])]),
                                   **pivot(256, 256)}))


def i_star():
    return doc(layer([group(star(256, 270, 190, 82, 5, roundness=10), fill())],
                     {"s": loop3([100, 100, 100], [90, 90, 100]), "r": loop3([0], [8]), **pivot(256, 270)}))


#: файл → эмодзи кнопки. Эти эмодзи на кнопках получают белую иконку.
ICONS = (
    ("back.tgs", "⬅️", i_back), ("plus.tgs", "➕", i_plus), ("close.tgs", "✖️", i_close),
    ("check.tgs", "✅", i_check), ("sparkles.tgs", "✨", i_sparkles), ("gift.tgs", "🎁", i_gift),
    ("people.tgs", "👥", i_people), ("megaphone.tgs", "📢", i_megaphone), ("trash.tgs", "🗑", i_trash),
    ("refresh.tgs", "🔄", i_refresh), ("chart.tgs", "📈", i_chart), ("chart2.tgs", "📊", i_chart),
    ("mail.tgs", "📨", i_envelope), ("lock.tgs", "🔒", i_lock), ("target.tgs", "🎯", i_target),
    ("rocket.tgs", "🚀", i_rocket), ("key.tgs", "🔑", i_key), ("coin.tgs", "💰", i_coin),
    ("star.tgs", "⭐", i_star),
)


def build() -> list[pathlib.Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    paths = []
    for file, _, make in ICONS:
        out = OUT / file
        out.write_bytes(gzip.compress(json.dumps(make(), separators=(",", ":")).encode(), 9))
        paths.append(out)
    return paths


if __name__ == "__main__":
    files = build()
    print("готово:", len(files), "иконок")
    if "--preview" in sys.argv:
        from PIL import Image
        from rlottie_python import LottieAnimation

        cols = 10
        rows = (len(files) + cols - 1) // cols
        sheet = Image.new("RGBA", (cols * 110, rows * 2 * 110), (36, 40, 52, 255))
        for i, p in enumerate(files):
            an = LottieAnimation.from_tgs(str(p))
            for k, frame in enumerate((10, 70)):
                im = an.render_pillow_frame(frame_num=frame, width=96, height=96).convert("RGBA")
                sheet.alpha_composite(im, ((i % cols) * 110 + 7, ((i // cols) * 2 + k) * 110 + 7))
        out = ROOT / "preview" / "icons.png"
        sheet.save(out)
        print("превью:", out)
