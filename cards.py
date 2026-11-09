"""Анимированная карточка «Мои итоги» для инлайна и историй.

Карточка — короткое зацикленное видео (MP4 без звука, Telegram показывает
его как GIF): живая уточка из TGS, падающий снег, искры бенгальского огня
и сами итоги. Три секунды, 30 кадров в секунду, цикл без шва: снег и
искры считаются от фазы цикла, а стикер сам по себе зациклен.

Два формата:
* card  — 720×900, для инлайна и «отправить другу»;
* story — 720×1280, для публикации в истории.

Рисуется в отдельном потоке, кладётся в CARDS_DIR и переиспользуется,
пока итоги не поменяются (ключ — facts.digest).
"""

from __future__ import annotations

import logging
import math
import random
import subprocess
import threading
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from rlottie_python import LottieAnimation

import config
import facts

log = logging.getLogger("itogi.cards")

FPS = 30
FRAMES = 90  # 3 секунды — ровно один цикл уточки (181 кадр при 60 fps)

SIZES = {"card": (720, 900), "story": (720, 1280), "photo": (1280, 720)}

#: Юзернейм бота — водяной знак на видео (для TikTok/Reels, где ссылки не кликаются).
BOT_USERNAME = ""

#: Ровные фоны инлайн-баннера (без градиента) — выбираются случайно.
BANNER_BGS = (
    (42, 125, 240), (118, 76, 245), (226, 70, 140), (14, 160, 150),
    (222, 58, 72), (36, 46, 120), (240, 128, 34), (28, 150, 90),
)
FLAKE_COLORS = ((255, 255, 255), (190, 230, 255), (255, 222, 120), (255, 190, 220), (200, 255, 230))
BULB_COLORS = ((255, 80, 90), (255, 214, 102), (110, 230, 140), (100, 190, 255), (255, 140, 220))


def _tinted(sprite: Image.Image, color) -> Image.Image:
    """Перекрасить белую снежинку в цвет, сохранив прозрачность и ореол."""
    out = Image.new("RGBA", sprite.size, color + (0,))
    out.putalpha(sprite.getchannel("A"))
    return out


class _Big:
    """Рисуем украшения в K раз крупнее и потом уменьшаем — края гладкие, без «лесенки».
    Обёртка над ImageDraw, которая сама умножает координаты и толщины."""

    K = 3

    def __init__(self, img: Image.Image) -> None:
        self.d = ImageDraw.Draw(img)

    def _xy(self, xy):
        k = self.K
        if isinstance(xy[0], (tuple, list)):
            return [(x * k, y * k) for x, y in xy]
        return [v * k for v in xy]

    def polygon(self, xy, **kw):
        self.d.polygon(self._xy(xy), **kw)

    def ellipse(self, xy, **kw):
        self.d.ellipse(self._xy(xy), **kw)

    def rectangle(self, xy, **kw):
        self.d.rectangle(self._xy(xy), **kw)

    def rounded_rectangle(self, xy, radius, **kw):
        self.d.rounded_rectangle(self._xy(xy), radius * self.K, **kw)

    def line(self, xy, fill=None, width=1, **kw):
        self.d.line(self._xy(xy), fill=fill, width=max(1, int(width * self.K)), **kw)


def _sphere(d, x: float, y: float, r: float, color) -> None:
    """Объёмный шарик: тёмный низ, цвет, светлая середина, блик."""
    dark = tuple(int(c * 0.62) for c in color)
    light = tuple(min(255, int(c + (255 - c) * 0.45)) for c in color)
    d.ellipse((x - r, y - r, x + r, y + r), fill=dark + (255,))
    d.ellipse((x - r * 0.96, y - r * 1.0, x + r * 0.9, y + r * 0.82), fill=color + (255,))
    d.ellipse((x - r * 0.72, y - r * 0.8, x + r * 0.42, y + r * 0.3), fill=light + (150,))
    d.ellipse((x - r * 0.55, y - r * 0.62, x - r * 0.12, y - r * 0.22), fill=(255, 255, 255, 220))


def _icicles(d, rnd: random.Random, w: int, max_len: int) -> None:
    """Сосульки: светлая левая грань, тень справа, блик и капля на кончике."""
    x = rnd.uniform(-10, 20)
    while x < w:
        width = rnd.uniform(16, 34)
        length = rnd.uniform(max_len * 0.3, max_len)
        if x > 600:  # над текстом справа — короче
            length = min(length, 46)
        tx = x + width / 2 + rnd.uniform(-3, 3)
        d.polygon([(x, 0), (x + width, 0), (tx, length)], fill=(150, 205, 240, 235))          # тень
        d.polygon([(x, 0), (x + width * 0.62, 0), (tx - 0.5, length - 1)], fill=(215, 240, 255, 245))  # тело
        d.polygon([(x + width * 0.14, 0), (x + width * 0.34, 0), (tx - 1, length * 0.78)], fill=(255, 255, 255, 250))  # блик
        d.ellipse((tx - 3.2, length - 3, tx + 3.2, length + 4), fill=(225, 245, 255, 240))
        d.ellipse((tx - 1.4, length - 1, tx + 0.6, length + 1.5), fill=(255, 255, 255, 255))
        x += width + rnd.uniform(-4, 10)
    d.rectangle((0, 0, w, 9), fill=(232, 246, 255, 250))
    d.rectangle((0, 0, w, 4), fill=(255, 255, 255, 255))


def _garland(d, rnd: random.Random, w: int) -> None:
    """Гирлянда: провод провисает дугами, лампочки — объёмные с бликом и свечением."""
    spans = rnd.choice((3, 4, 5))
    step = w / spans
    k = rnd.randrange(len(BULB_COLORS))
    for sp in range(spans):
        x0 = sp * step
        sag = rnd.uniform(16, 26)
        pts = [(x0 + step * t / 40, 14 + sag * math.sin(math.pi * t / 40)) for t in range(41)]
        d.line(pts, fill=(25, 35, 45, 220), width=3)
        for t in range(4, 40, 7):
            bx, by = pts[t]
            c = BULB_COLORS[k % len(BULB_COLORS)]
            k += 1
            d.ellipse((bx - 15, by - 2, bx + 15, by + 32), fill=c + (60,))  # свечение
            d.rounded_rectangle((bx - 4.5, by - 3, bx + 4.5, by + 6), 2, fill=(85, 95, 105, 255))
            d.rounded_rectangle((bx - 4.5, by - 3, bx - 1, by + 6), 2, fill=(140, 150, 160, 255))
            d.ellipse((bx - 7.5, by + 3, bx + 7.5, by + 25), fill=tuple(int(v * 0.7) for v in c) + (255,))
            d.ellipse((bx - 7, by + 3, bx + 6, by + 22), fill=c + (255,))
            d.ellipse((bx - 4.5, by + 6, bx - 0.5, by + 13), fill=(255, 255, 255, 200))


def _baubles(d, rnd: random.Random) -> None:
    """Ёлочные шарики на нитках — слева, над уточкой. Объёмные."""
    for _ in range(rnd.choice((2, 3))):
        x = rnd.uniform(40, 560)
        y = rnd.uniform(60, 140)
        r = rnd.uniform(16, 26)
        c = rnd.choice(BULB_COLORS)
        d.line((x, 0, x, y - r), fill=(255, 255, 255, 190), width=2)
        d.rounded_rectangle((x - 6.5, y - r - 8, x + 6.5, y - r + 3), 2.5, fill=(214, 160, 50, 255))
        d.rounded_rectangle((x - 6.5, y - r - 8, x - 1, y - r + 3), 2.5, fill=(255, 225, 130, 255))
        _sphere(d, x, y, r, c)


def _drift(d, rnd: random.Random, w: int, h: int) -> None:
    """Сугроб: каждый холмик с голубой тенью снизу и белой шапкой — получается объём."""
    x = -40
    lumps = []
    while x < w + 40:
        rw = rnd.uniform(90, 170)
        rh = rnd.uniform(30, 58)
        lumps.append((x, rw, rh))
        x += rw * rnd.uniform(0.55, 0.8)
    for x, rw, rh in lumps:
        d.ellipse((x, h - rh, x + rw, h + rh), fill=(190, 215, 240, 255))
    for x, rw, rh in lumps:
        d.ellipse((x + 2, h - rh - 2, x + rw - 8, h + rh - 10), fill=(248, 252, 255, 255))
        d.ellipse((x + rw * 0.18, h - rh + 4, x + rw * 0.55, h - rh + 16), fill=(255, 255, 255, 255))


def _sparkles(d, rnd: random.Random, w: int, h: int) -> None:
    for _ in range(rnd.randint(10, 18)):
        x, y = rnd.uniform(20, w - 20), rnd.uniform(20, h - 20)
        if 590 < x and 50 < y < 660:  # не лезем в текст
            continue
        _star(d, x, y, rnd.uniform(4, 9), rnd.randint(150, 240), rnd.choice(FLAKE_COLORS))


def _shadow(layer: Image.Image, dy: int = 6, blur: int = 7, strength: float = 0.32) -> Image.Image:
    """Мягкая тень от всего непрозрачного на слое — предметы чуть «приподняты»."""
    a = layer.getchannel("A").point(lambda v: int(v * strength)).filter(ImageFilter.GaussianBlur(blur))
    sh = Image.new("RGBA", layer.size, (10, 15, 45, 0))
    sh.putalpha(a)
    out = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    out.alpha_composite(sh, (0, dy))
    return out


def _decorate(ov: Image.Image, d, rnd: random.Random, w: int, h: int) -> set[str]:
    """Случайный набор украшений. Сверху — одно из: сосульки / гирлянда / шарики.
    Рисуется в K раз крупнее и уменьшается, затем получает мягкую тень."""
    K = _Big.K
    big = Image.new("RGBA", (w * K, h * K), (0, 0, 0, 0))
    bd = _Big(big)
    top = rnd.choice(("icicles", "garland", "baubles", "icicles+baubles"))
    if "icicles" in top:
        _icicles(bd, rnd, w, 70)
    if top == "garland":
        _garland(bd, rnd, w)
    if "baubles" in top:
        _baubles(bd, rnd)
    used = {top}
    if rnd.random() < 0.55:
        _drift(bd, rnd, w, h)
        used.add("drift")
    _sparkles(bd, rnd, w, h)
    layer = big.resize((w, h), Image.LANCZOS)
    # Цветные снежинки-кристаллы (спрайты и так сглажены).
    colors = rnd.sample(FLAKE_COLORS, 2)
    for _ in range(rnd.randint(10, 16)):
        x, y = rnd.uniform(0, w), rnd.uniform(40, h - 30)
        if 590 < x < w - 30 and 50 < y < 660:
            continue
        sp = _tinted(_flake_sprite(rnd.choice((18, 24, 32, 40)), rnd.randrange(3)), rnd.choice(colors))
        sp = sp.rotate(rnd.uniform(0, 60), resample=Image.BICUBIC)
        layer.alpha_composite(sp, (max(0, int(x - sp.width / 2)), max(0, int(y - sp.height / 2))))
    ov.alpha_composite(_shadow(layer, dy=5, blur=5, strength=0.35))
    ov.alpha_composite(layer)
    return used

# Ночное новогоднее небо Telegram: синий сверху, фиолетовый снизу.
TOP = (46, 134, 255)
MID = (88, 86, 235)
BOTTOM = (124, 58, 237)
GOLD = (255, 214, 102)


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(config.FONTS / name), size)


def _fit(draw: ImageDraw.ImageDraw, text: str, name: str, size: int, width: int) -> ImageFont.FreeTypeFont:
    while size > 18:
        font = _font(name, size)
        if draw.textlength(text, font=font) <= width:
            return font
        size -= 2
    return _font(name, size)


def _gradient(w: int, h: int) -> Image.Image:
    grad = Image.new("RGB", (1, h))
    for y in range(h):
        t = y / (h - 1)
        if t < 0.55:
            k = t / 0.55
            c = tuple(int(TOP[i] + (MID[i] - TOP[i]) * k) for i in range(3))
        else:
            k = (t - 0.55) / 0.45
            c = tuple(int(MID[i] + (BOTTOM[i] - MID[i]) * k) for i in range(3))
        grad.putpixel((0, y), c)
    return grad.resize((w, h)).convert("RGBA")


def _glow(w: int, h: int, cx: int, cy: int, r: int, color, alpha: int) -> Image.Image:
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*color, alpha))
    return layer.filter(ImageFilter.GaussianBlur(r // 2))


@lru_cache(maxsize=64)
def _sticker_frames(code: str, size: int) -> tuple[Image.Image, ...]:
    anim = LottieAnimation.from_tgs(str(facts.sticker_path(code)))
    total = anim.lottie_animation_get_totalframe()
    out = []
    for i in range(FRAMES):
        n = int(i * total / FRAMES) % total
        out.append(anim.render_pillow_frame(frame_num=n, width=size, height=size).convert("RGBA"))
    return tuple(out)


@lru_cache(maxsize=64)
def _icon(code: str, size: int) -> Image.Image:
    anim = LottieAnimation.from_tgs(str(facts.sticker_path(code)))
    total = anim.lottie_animation_get_totalframe()
    return anim.render_pillow_frame(frame_num=total // 3, width=size, height=size).convert("RGBA")


def _rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=2 if outline else 0)


def _star(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, alpha: int, color=(255, 255, 255)) -> None:
    """Четырёхлучевая искра, как блёстки в интерфейсе Telegram."""
    if r < 0.6 or alpha <= 0:
        return
    k = r * 0.28
    pts = [(x, y - r), (x + k, y - k), (x + r, y), (x + k, y + k), (x, y + r), (x - k, y + k), (x - r, y), (x - k, y - k)]
    draw.polygon(pts, fill=(*color, alpha))


def _flake_sprite(size: int, kind: int) -> Image.Image:
    """Шестилучевая снежинка с веточками (как в мини-аппе), белая с голубым ореолом."""
    k = 4  # рисуем крупнее и уменьшаем — так линии гладкие
    S = size * k
    pad = S // 6
    img = Image.new("RGBA", (S + pad * 2, S + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = cy = S / 2 + pad
    r = S / 2
    lw = max(2, int(S / 16))
    twigs = {0: ((0.45, 0.32), (0.72, 0.22)), 1: ((0.3, 0.2), (0.55, 0.3), (0.8, 0.16)), 2: ((0.6, 0.38),)}[kind]

    def rot(x: float, y: float, ang: float) -> tuple[float, float]:
        c, s_ = math.cos(ang), math.sin(ang)
        return cx + x * c - y * s_, cy + x * s_ + y * c

    for i in range(6):
        ang = i * math.pi / 3
        d.line([rot(0, 0, ang), rot(0, -r, ang)], fill="white", width=lw)
        for at, ln in twigs:
            d.line([rot(0, -r * at, ang), rot(-r * ln, -r * (at + ln * 0.8), ang)], fill="white", width=lw)
            d.line([rot(0, -r * at, ang), rot(r * ln, -r * (at + ln * 0.8), ang)], fill="white", width=lw)
    d.ellipse((cx - lw, cy - lw, cx + lw, cy + lw), fill="white")
    glow = img.filter(ImageFilter.GaussianBlur(S / 14))
    tint = Image.new("RGBA", img.size, (190, 225, 255, 0))
    tint.putalpha(glow.getchannel("A").point(lambda v: int(v * 0.8)))
    out = Image.alpha_composite(tint, img)
    return out.resize((out.width // k, out.height // k), Image.LANCZOS)


class _Snow:
    """Снег, зацикленный по фазе: за FRAMES кадров каждая снежинка
    пролетает экран целое число раз и возвращается в исходную точку.
    Дальний план — мягкие точки, ближний — настоящие кристаллы, которые
    поворачиваются на 60° за цикл (у шестилучевой снежинки это незаметный стык)."""

    def __init__(self, w: int, h: int, count: int, seed: int) -> None:
        rnd = random.Random(seed)
        self.w, self.h = w, h
        self.flakes = [
            (rnd.random() * w, rnd.random() * h, rnd.choice((1, 1, 2)), rnd.uniform(1.0, 2.6),
             rnd.uniform(0, math.tau), rnd.randint(90, 190))
            for _ in range(count)
        ]
        sprites = [_flake_sprite(sz, kd) for sz in (18, 26, 36) for kd in range(3)]
        self.crystals = []
        for _ in range(max(8, count // 3)):
            sp = rnd.choice(sprites)
            self.crystals.append((
                sp, rnd.random() * w, rnd.random() * h, rnd.choice((1, 1, 2)),
                rnd.uniform(0, math.tau), rnd.uniform(0, 60), rnd.choice((-1, 1)), rnd.uniform(0.55, 0.95),
            ))

    def draw(self, d: ImageDraw.ImageDraw, frame: int, layer: Image.Image | None = None) -> None:
        phase = frame / FRAMES
        for x0, y0, laps, r, sway, a in self.flakes:
            y = (y0 + phase * laps * self.h) % self.h
            x = x0 + math.sin(phase * math.tau * laps + sway) * 10
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, a))
        if layer is None:
            return
        for sp, x0, y0, laps, sway, rot0, sign, alpha in self.crystals:
            span = self.h + sp.height
            y = (y0 + phase * laps * span) % span - sp.height / 2
            x = x0 + math.sin(phase * math.tau * laps + sway) * 14
            img = sp.rotate(rot0 + sign * 60 * phase, resample=Image.BICUBIC)
            if alpha < 1:
                img = img.copy()
                img.putalpha(img.getchannel("A").point(lambda v, k=alpha: int(v * k)))
            layer.paste(img, (int(x - img.width / 2), int(y - img.height / 2)), img)


class _Sparkler:
    """Искры бенгальского огня: вылетают из точки и гаснут.

    Каждая искра живёт часть цикла и стартует со своим сдвигом — так
    поток выглядит непрерывным и стыкуется на границе цикла.
    """

    def __init__(self, cx: float, cy: float, count: int, spread: float, seed: int) -> None:
        rnd = random.Random(seed)
        self.cx, self.cy = cx, cy
        self.sparks = [
            (rnd.random(), rnd.uniform(0, math.tau), rnd.uniform(0.35 * spread, spread),
             rnd.uniform(0.25, 0.5), rnd.uniform(2.0, 4.6), rnd.choice((GOLD, (255, 255, 255), (255, 176, 92))))
            for _ in range(count)
        ]

    def draw(self, d: ImageDraw.ImageDraw, frame: int) -> None:
        phase = frame / FRAMES
        for start, angle, dist, life, size, color in self.sparks:
            t = ((phase - start) % 1.0) / life
            if t > 1:
                continue
            ease = 1 - (1 - t) ** 2
            x = self.cx + math.cos(angle) * dist * ease
            y = self.cy + math.sin(angle) * dist * ease + 40 * t * t
            alpha = int(255 * (1 - t))
            _star(d, x, y, size * (1.2 - 0.6 * t), alpha, color)


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _stats(data: dict | None) -> list[tuple[str, str, str]]:
    """До трёх пар «иконка, число, подпись» для нижней плашки."""
    if not data:
        return [("031", "?", "дней в Telegram"), ("018", "?", "подарков"), ("032", "?", "уровень")]
    out = []
    a = data["age"]
    out.append(("031", _fmt(a["days"]), f"{a['word']} в Telegram"))
    g = data.get("gifts")
    if g and g.get("now"):
        out.append(("018", _fmt(g["now"]), facts.plural(g["now"], "подарок", "подарка", "подарков")))
    r = data.get("rating")
    if r and r.get("level"):
        out.append(("032", str(r["level"]), "уровень"))
    if len(out) < 3 and data.get("premium"):
        out.append(("010", "Premium", "легенда"))
    if len(out) < 3:
        out.append(("029", f"№{_fmt(data['rank'])}", "в итогах года"))
    return out[:3]


def _base(data: dict | None, kind: str) -> tuple[Image.Image, dict]:
    """Всё неподвижное: фон, тексты, плашки. Возвращает картинку и
    координаты для живых слоёв."""
    w, h = SIZES[kind]
    img = _gradient(w, h)
    story = kind == "story"
    duck_size = 340 if story else 330
    duck_y = 290 if story else 170
    img.alpha_composite(_glow(w, h, w // 2, duck_y + duck_size // 2, 230, (255, 255, 255), 70))
    img.alpha_composite(_glow(w, h, w // 2, duck_y + duck_size // 2 + 40, 170, GOLD, 60))
    # Всё полупрозрачное рисуем на отдельном слое и потом смешиваем:
    # ImageDraw по RGBA не смешивает, а заменяет пиксели.
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    # Шапка: плашка «ИТОГИ 2026».
    top = 70 if story else 44
    pill = f"ИТОГИ {config.YEAR}"
    pf = _font("Unbounded-Bold.ttf", 26)
    pw = d.textlength(pill, font=pf) + 56
    _rounded(d, ((w - pw) / 2, top, (w + pw) / 2, top + 50), 25, (255, 255, 255, 46), (255, 255, 255, 90))
    d.text((w / 2, top + 25), pill, font=pf, fill="white", anchor="mm")

    name = (data or {}).get("user", {}).get("name") or "Мой год в Telegram"
    if story:
        nf = _fit(d, "Мой 2026 в Telegram", "Unbounded-Black.ttf", 52, w - 80)
        d.text((w / 2, top + 120), f"Мой {config.YEAR} в Telegram", font=nf, fill="white", anchor="mm")
        sf = _fit(d, name, "Manrope-Bold.ttf", 36, w - 120)
        d.text((w / 2, top + 178), name, font=sf, fill=(255, 255, 255, 215), anchor="mm")
    else:
        nf = _fit(d, name, "Unbounded-Black.ttf", 46, w - 90)
        d.text((w / 2, top + 98), name, font=nf, fill="white", anchor="mm")

    # Номинация под уточкой.
    nom_y = duck_y + duck_size + (26 if story else 22)
    if data:
        nom = facts.verdict(data)["title"]
        label = "Итог года"
    else:
        nom = "Узнай свои итоги"
        label = "Твой год в Telegram"
    lf = _font("Manrope-Bold.ttf", 24)
    d.text((w / 2, nom_y + 8), label.upper(), font=lf, fill=(255, 255, 255, 170), anchor="mm")
    tf = _fit(d, nom, "Unbounded-Bold.ttf", 44 if story else 40, w - 100)
    d.text((w / 2, nom_y + 56), nom, font=tf, fill=GOLD, anchor="mm", stroke_width=0)

    # Нижняя плашка со статистикой.
    stats = _stats(data)
    px0, px1 = 40, w - 40
    py0 = nom_y + (130 if story else 108)
    py1 = py0 + 190
    _rounded(d, (px0, py0, px1, py1), 36, (255, 255, 255, 36), (255, 255, 255, 70))
    col = (px1 - px0) / len(stats)
    for i, (code, num, cap) in enumerate(stats):
        cx = px0 + col * i + col / 2
        ico = _icon(code, 72)
        ov.alpha_composite(ico, (int(cx - 36), py0 + 12))
        nf2 = _fit(d, num, "Unbounded-Bold.ttf", 34, int(col) - 20)
        d.text((cx, py0 + 112), num, font=nf2, fill="white", anchor="mm")
        cf = _fit(d, cap, "Manrope-Medium.ttf", 20, int(col) - 16)
        d.text((cx, py0 + 152), cap, font=cf, fill=(255, 255, 255, 190), anchor="mm")
        if i:
            x = px0 + col * i
            d.line((x, py0 + 30, x, py1 - 30), fill=(255, 255, 255, 50), width=2)

    # В истории места больше — показываем предсказание.
    if story and data:
        label_y = py1 + 60
        d.text((w / 2, label_y), f"ПРЕДСКАЗАНИЕ НА {config.YEAR + 1}", font=lf,
               fill=(255, 255, 255, 170), anchor="mm")
        pf2 = _font("Manrope-Bold.ttf", 32)
        words, lines, cur = data["prediction"].split(), [], ""
        for word in words:
            probe = f"{cur} {word}".strip()
            if d.textlength(probe, font=pf2) > w - 120 and cur:
                lines.append(cur)
                cur = word
            else:
                cur = probe
        lines.append(cur)
        for k, line in enumerate(lines[:3]):
            d.text((w / 2, label_y + 52 + k * 44), line, font=pf2, fill="white", anchor="mm")

    # Подвал.
    if story:
        # Видео уходит и в TikTok, где ссылок нет, — поэтому пишем, где искать бота.
        foot = f"Свои итоги — в Telegram: @{BOT_USERNAME}" if BOT_USERNAME else "Узнай свои итоги года — жми на ссылку в истории"
    else:
        foot = "Узнай свои итоги года — жми кнопку ниже"
    ff = _font("Manrope-Medium.ttf", 22)
    d.text((w / 2, h - (44 if story else 34)), foot, font=ff, fill=(255, 255, 255, 160), anchor="mm")

    img.alpha_composite(ov)
    places = {
        "duck": (w // 2 - duck_size // 2, duck_y, duck_size),
        "spark": (w // 2 + duck_size * 0.30, duck_y + duck_size * 0.18),
        "spark2": (w // 2 - duck_size * 0.36, duck_y + duck_size * 0.24),
    }
    return img, places


def _banner(data: dict | None) -> tuple[Image.Image, dict]:
    """Инлайн-баннер 16:9 на ровном фоне: слева уточка, справа итоги."""
    w, h = SIZES["photo"]
    seed = int((data or {}).get("user", {}).get("id") or 0) or random.randrange(1 << 30)
    rnd = random.Random(f"banner:{seed}:{config.YEAR}")
    bg = rnd.choice(BANNER_BGS)
    img = Image.new("RGBA", (w, h), bg + (255,))
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)
    deco = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    used = _decorate(deco, ImageDraw.Draw(deco), rnd, w, h)
    img.alpha_composite(deco)

    # Слева — круглая «сцена» под уточку (ровный цвет, без подсветки).
    duck_size = 440
    dcx, dcy = 320, h // 2 + 6
    d.ellipse((dcx - 250, dcy - 244, dcx + 250, dcy + 256), fill=(0, 0, 0, 22))
    d.ellipse((dcx - 250, dcy - 250, dcx + 250, dcy + 250), fill=(255, 255, 255, 34))

    x0 = 610
    right = w - 60
    # Плашка «ИТОГИ 2026».
    pill = f"ИТОГИ {config.YEAR}"
    pf = _font("Unbounded-Bold.ttf", 26)
    pw = d.textlength(pill, font=pf) + 56
    py = 84 if "garland" in used else 64  # под гирляндой плашку чуть ниже
    _rounded(d, (x0, py, x0 + pw, py + 50), 25, (255, 255, 255, 46), (255, 255, 255, 90))
    d.text((x0 + pw / 2, py + 25), pill, font=pf, fill="white", anchor="mm")
    foot_y = 616 if "drift" in used else 628

    name = (data or {}).get("user", {}).get("name") or f"Мой {config.YEAR} в Telegram"
    nf = _fit(d, name, "Unbounded-Black.ttf", 64, right - x0)
    d.text((x0, 184), name, font=nf, fill="white", anchor="lm")

    if data:
        v = facts.verdict(data)
        label, nom = "ИТОГ ГОДА", v["title"]
    else:
        v = None
        label, nom = "ТВОЙ ГОД В TELEGRAM", "Узнай свои итоги"
    lf = _font("Manrope-Bold.ttf", 24)
    d.text((x0, 252), label, font=lf, fill=(255, 255, 255, 175), anchor="lm")
    tf = _fit(d, nom, "Unbounded-Bold.ttf", 46, right - x0)
    d.text((x0, 302), nom, font=tf, fill=GOLD, anchor="lm")

    # Плашка с тремя цифрами.
    stats = _stats(data)
    py0, py1 = 370, 570
    _rounded(d, (x0 - 6, py0, right, py1), 36, (255, 255, 255, 36), (255, 255, 255, 70))
    col = (right - x0 + 6) / len(stats)
    for i, (code, num, cap) in enumerate(stats):
        cx = x0 - 6 + col * i + col / 2
        ov.alpha_composite(_icon(code, 76), (int(cx - 38), py0 + 14))
        nf2 = _fit(d, num, "Unbounded-Bold.ttf", 38, int(col) - 20)
        d.text((cx, py0 + 122), num, font=nf2, fill="white", anchor="mm")
        cf = _fit(d, cap, "Manrope-Medium.ttf", 21, int(col) - 16)
        d.text((cx, py0 + 162), cap, font=cf, fill=(255, 255, 255, 195), anchor="mm")
        if i:
            x = x0 - 6 + col * i
            d.line((x, py0 + 30, x, py1 - 30), fill=(255, 255, 255, 50), width=2)

    # Подвал: самый цепляющий факт или призыв.
    if v:
        foot = v["why"][:1].upper() + v["why"][1:]
    else:
        foot = "Узнай свои итоги года — жми кнопку ниже"
    ff = _fit(d, foot, "Manrope-Bold.ttf", 26, right - x0)
    fc = (255, 255, 255, 230)
    if "drift" in used:  # на сугробе белый текст не читается — подложка
        tw = d.textlength(foot, font=ff)
        _rounded(d, (x0 - 14, foot_y - 24, x0 + tw + 14, foot_y + 24), 24, bg + (235,))
    d.text((x0, foot_y), foot, font=ff, fill=fc, anchor="lm")

    img.alpha_composite(_shadow(ov, dy=5, blur=6, strength=0.28))
    img.alpha_composite(ov)
    places = {
        "duck": (dcx - duck_size // 2, dcy - duck_size // 2, duck_size),
        "spark": (dcx + duck_size * 0.30, dcy - duck_size * 0.32),
        "spark2": (dcx - duck_size * 0.36, dcy - duck_size * 0.26),
    }
    return img, places


def _duck_code(data: dict | None) -> str:
    return "039" if not data else facts.verdict(data)["sticker"]


def render(data: dict | None, kind: str, out: Path) -> Path:
    """Нарисовать видео и превью. Возвращает путь к mp4; рядом — .jpg."""
    import imageio_ffmpeg

    w, h = SIZES[kind]
    base, places = _base(data, kind)
    seed = int((data or {}).get("user", {}).get("id") or 7)
    snow = _Snow(w, h, 70 if kind == "story" else 55, seed)
    spark = _Sparkler(*places["spark"], count=46, spread=120, seed=seed + 1)
    spark2 = _Sparkler(*places["spark2"], count=30, spread=90, seed=seed + 2)
    dx, dy, ds = places["duck"]
    duck = _sticker_frames(_duck_code(data), ds)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.mp4")
    cmd = [
        imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(FPS), "-i", "-",
        "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "23", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(tmp),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    preview = None
    try:
        for i in range(FRAMES):
            frame = base.copy()
            layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            snow.draw(ld, i, layer)
            frame.alpha_composite(layer)
            # Уточка чуть «дышит» — плавно покачивается по вертикали.
            bob = int(math.sin(i / FRAMES * math.tau) * 6)
            frame.alpha_composite(duck[i], (dx, dy + bob))
            sl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            sd = ImageDraw.Draw(sl)
            spark.draw(sd, i)
            spark2.draw(sd, (i + FRAMES // 2) % FRAMES)
            frame.alpha_composite(sl)
            rgb = frame.convert("RGB")
            if i == FRAMES // 3:
                preview = rgb
            assert proc.stdin is not None
            proc.stdin.write(rgb.tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg завершился с кодом {proc.returncode}")
    tmp.replace(out)
    if preview is not None:
        preview.save(out.with_suffix(".jpg"), quality=88)
        thumb = preview.copy()
        thumb.thumbnail((320, 400))
        thumb.save(out.with_name(out.stem + "_thumb.jpg"), quality=82)
    return out


def still(data: dict | None, out: Path) -> Path:
    """Инлайн-баннер 16:9: один кадр в JPG (и маленькая превьюшка)."""
    base, places = _banner(data)
    seed = int((data or {}).get("user", {}).get("id") or 7)
    return _finish(base, places, _duck_code(data), seed, out)


def _finish(base: Image.Image, places: dict, duck_code: str, seed: int, out: Path) -> Path:
    """Дорисовать к баннеру снег, уточку с тенью и искры и сохранить JPG."""
    w, h = base.size
    i = FRAMES // 3
    frame = base.copy()
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    _Snow(w, h, 60, seed).draw(ImageDraw.Draw(layer), i)
    frame.alpha_composite(layer)
    dx, dy, ds = places["duck"]
    duck = _icon(duck_code, ds)
    frame.alpha_composite(_shadow(duck, dy=12, blur=12, strength=0.4), (dx, dy))
    frame.alpha_composite(duck, (dx, dy))
    sl = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sl)
    _Sparkler(*places["spark"], count=46, spread=120, seed=seed + 1).draw(sd, i)
    _Sparkler(*places["spark2"], count=30, spread=90, seed=seed + 2).draw(sd, (i + FRAMES // 2) % FRAMES)
    frame.alpha_composite(sl)
    rgb = frame.convert("RGB")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp.jpg")
    rgb.save(tmp, quality=92)
    tmp.replace(out)
    thumb = rgb.copy()
    thumb.thumbnail((480, 270))
    thumb.save(out.with_name(out.stem + "_thumb.jpg"), quality=82)
    return out


def ensure_still(data: dict | None) -> Path:
    """Готовая статичная карточка из кэша или свежая. Блокирующая — звать в потоке."""
    digest = facts.digest(data) if data else "v5"
    path = path_for((data or {}).get("user", {}).get("id"), digest, "photo").with_suffix(".jpg")
    if path.exists():
        return path
    with _render_lock:
        if not path.exists():
            still(data, path)
    return path


# --------------------------------------------------------------- баннеры бота

#: Меняй, когда перерисовываешь баннеры меню/ОП — бот перезальёт их в Telegram.
PROMO_VERSION = "p2"

PROMO = {
    # Обязательная подписка: уточка просит, три шага.
    "gate": {
        "bg": (118, 76, 245), "duck": "012", "seed": "gate-3",
        "pill": "ПОСЛЕДНИЙ ШАГ",
        "title": "Подпишись —\nи итоги твои!",
        "sub": "Каналы наших друзей ждут тебя ниже",
        "chips": (("013", "1. Подпишись"), ("003", "2. Проверь"), ("039", "3. Смотри итоги"), ("024", "Это бесплатно")),
    },
    # Картинка описания бота в BotFather (640×360).
    "about": {
        "bg": (42, 125, 240), "duck": "010", "seed": "about-2",
        "pill": "НОВОГОДНИЙ БОТ",
        "title": f"Итоги {config.YEAR}\nв Telegram",
        "sub": "Честно, красиво и с уточками",
        "chips": (("004", "Шок-факты"), ("018", "Подарки"), ("032", "Уровень"), ("030", f"План {config.YEAR + 1}")),
    },
    # Главное меню: что внутри итогов.
    "menu": {
        "bg": (42, 125, 240), "duck": "039", "seed": "menu-7",
        "pill": f"ИТОГИ {config.YEAR}",
        "title": "Твой год\nуже готов!",
        "sub": "Жми «Смотреть итоги» — и поехали",
        "chips": (("018", "Подарки"), ("032", "Уровень"), ("004", "Шок-факты"), ("030", f"План {config.YEAR + 1}")),
    },
}


def _promo(kind: str) -> tuple[Image.Image, dict]:
    """Баннер 16:9 для сообщений бота (меню и ОП) в стиле инлайн-карточки."""
    cfg = PROMO[kind]
    w, h = SIZES["photo"]
    rnd = random.Random(cfg["seed"])
    bg = cfg["bg"]
    img = Image.new("RGBA", (w, h), bg + (255,))
    deco = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    used = _decorate(deco, ImageDraw.Draw(deco), rnd, w, h)
    img.alpha_composite(deco)
    ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(ov)

    duck_size = 440
    dcx, dcy = 320, h // 2 + 6
    d.ellipse((dcx - 250, dcy - 244, dcx + 250, dcy + 256), fill=(0, 0, 0, 22))
    d.ellipse((dcx - 250, dcy - 250, dcx + 250, dcy + 250), fill=(255, 255, 255, 34))

    x0, right = 610, w - 60
    pf = _font("Unbounded-Bold.ttf", 26)
    pw = d.textlength(cfg["pill"], font=pf) + 56
    lines = cfg["title"].split("\n")
    # Весь блок (плашка, заголовок, подзаголовок, 2×2 плашки) — по центру высоты.
    block = 50 + 46 + len(lines) * 76 - 10 + 44 + 134
    py = max(84 if "garland" in used else 70, int((h - block) / 2) - 6)
    _rounded(d, (x0, py, x0 + pw, py + 50), 25, (255, 255, 255, 46), (255, 255, 255, 90))
    d.text((x0 + pw / 2, py + 25), cfg["pill"], font=pf, fill="white", anchor="mm")

    tf = _fit(d, max(lines, key=len), "Unbounded-Black.ttf", 66, right - x0)
    ty = py + 96
    for k, line in enumerate(lines):
        d.text((x0, ty + k * 76), line, font=tf, fill=GOLD if k else "white", anchor="lm")
    sf = _fit(d, cfg["sub"], "Manrope-Bold.ttf", 28, right - x0)
    sy = ty + len(lines) * 76 - 10
    d.text((x0, sy), cfg["sub"], font=sf, fill=(255, 255, 255, 215), anchor="lm")

    # Плашки 2×2 с уточками.
    cw = (right - x0 - 14) / 2
    cy0 = sy + 44
    cf = _font("Manrope-Bold.ttf", 25)
    for i, (code, label) in enumerate(cfg["chips"]):
        cx = x0 + (i % 2) * (cw + 14)
        cy = cy0 + (i // 2) * 74
        _rounded(d, (cx, cy, cx + cw, cy + 60), 30, (255, 255, 255, 40), (255, 255, 255, 80))
        ov.alpha_composite(_icon(code, 50), (int(cx + 8), int(cy + 5)))
        lf = _fit(d, label, "Manrope-Bold.ttf", 25, int(cw - 76))
        d.text((cx + 66, cy + 30), label, font=lf, fill="white", anchor="lm")

    img.alpha_composite(_shadow(ov, dy=5, blur=6, strength=0.28))
    img.alpha_composite(ov)
    places = {
        "duck": (dcx - duck_size // 2, dcy - duck_size // 2, duck_size),
        "spark": (dcx + duck_size * 0.30, dcy - duck_size * 0.32),
        "spark2": (dcx - duck_size * 0.36, dcy - duck_size * 0.26),
    }
    return img, places


def promo(kind: str, out: Path) -> Path:
    base, places = _promo(kind)
    return _finish(base, places, PROMO[kind]["duck"], 11, out)


def about_picture(out: Path) -> Path:
    """Картинка для «Что умеет этот бот» в BotFather: ровно 640×360."""
    big = out.with_name(out.stem + "_big.jpg")
    promo("about", big)
    Image.open(big).resize((640, 360), Image.LANCZOS).save(out, quality=94)
    return out


#: Варианты аватарки бота. Telegram режет картинку в круг — всё важное в центре.
AVATARS = {
    # Классика: синий Telegram, праздничная уточка, золотой год.
    "party": {"bg": (42, 125, 240), "duck": "039", "deco": "snow", "year": GOLD, "stroke": (30, 70, 170)},
    # Уточка в очках на фиолетовом, гирлянда дугой сверху.
    "cool": {"bg": (42, 125, 240), "duck": "010", "deco": "garland", "year": (255, 255, 255), "stroke": (24, 72, 170)},
    # Тот же «в очках», но золотой год — на выбор.
    "cool_gold": {"bg": (42, 125, 240), "duck": "010", "deco": "garland", "year": GOLD, "stroke": (24, 72, 170)},
    # Ночной: тёмно-синий, уточка с сердечками, звёзды.
    "night": {"bg": (36, 46, 120), "duck": "024", "deco": "stars", "year": GOLD, "stroke": (20, 25, 80)},
    # Розовый с шариками: уточка-богач с подарками.
    "gifts": {"bg": (226, 70, 140), "duck": "018", "deco": "baubles", "year": (255, 255, 255), "stroke": (160, 30, 90)},
}


def avatar(out: Path, variant: str = "cool") -> Path:
    """Аватарка бота 640×640 в выбранном варианте."""
    v = AVATARS[variant]
    S = 640
    rnd = random.Random(f"avatar-{variant}")
    img = Image.new("RGBA", (S, S), v["bg"] + (255,))
    K = _Big.K
    big = Image.new("RGBA", (S * K, S * K), (0, 0, 0, 0))
    bd = _Big(big)
    # Сцена под уточкой: ровный светлый круг с тенью-ободком.
    bd.ellipse((70, 66, 570, 566), fill=(0, 0, 0, 26))
    bd.ellipse((70, 58, 570, 558), fill=(255, 255, 255, 36))
    if v["deco"] == "garland":
        k = 0
        pts = [(80 + 480 * t / 60, 120 - 70 * math.sin(math.pi * t / 60)) for t in range(61)]
        bd.line(pts, fill=(25, 35, 45, 230), width=3)
        for t in range(3, 60, 7):
            bx, by = pts[t]
            c = BULB_COLORS[k % len(BULB_COLORS)]
            k += 1
            bd.ellipse((bx - 15, by - 2, bx + 15, by + 32), fill=c + (70,))
            bd.rounded_rectangle((bx - 4.5, by - 3, bx + 4.5, by + 6), 2, fill=(85, 95, 105, 255))
            bd.ellipse((bx - 7.5, by + 3, bx + 7.5, by + 25), fill=tuple(int(x * 0.7) for x in c) + (255,))
            bd.ellipse((bx - 7, by + 3, bx + 6, by + 22), fill=c + (255,))
            bd.ellipse((bx - 4.5, by + 6, bx - 0.5, by + 13), fill=(255, 255, 255, 200))
    if v["deco"] == "baubles":
        for x, y, r, c in ((150, 150, 26, BULB_COLORS[1]), (500, 170, 22, BULB_COLORS[3]), (440, 110, 16, BULB_COLORS[2])):
            bd.line((x, 0, x, y - r), fill=(255, 255, 255, 200), width=2)
            bd.rounded_rectangle((x - 6.5, y - r - 8, x + 6.5, y - r + 3), 2.5, fill=(214, 160, 50, 255))
            _sphere(bd, x, y, r, c)
    n_stars = 22 if v["deco"] == "stars" else 12
    for _ in range(n_stars):
        ang = rnd.uniform(0, math.tau)
        rad = rnd.uniform(160, 285)
        _star(bd, S / 2 + math.cos(ang) * rad, S / 2 + math.sin(ang) * rad, rnd.uniform(4, 9), 230, rnd.choice(FLAKE_COLORS))
    layer = big.resize((S, S), Image.LANCZOS)
    for _ in range(9 if v["deco"] in ("snow", "stars") else 5):
        ang = rnd.uniform(0, math.tau)
        rad = rnd.uniform(230, 285)
        x, y = S / 2 + math.cos(ang) * rad, S / 2 + math.sin(ang) * rad
        sp = _tinted(_flake_sprite(rnd.choice((22, 28, 34)), rnd.randrange(3)), rnd.choice(FLAKE_COLORS))
        layer.alpha_composite(sp, (int(x - sp.width / 2), int(y - sp.height / 2)))
    img.alpha_composite(_shadow(layer, dy=5, blur=5, strength=0.3))
    img.alpha_composite(layer)
    duck = _icon(v["duck"], 400)
    img.alpha_composite(_shadow(duck, dy=12, blur=12, strength=0.4), (120, 62))
    img.alpha_composite(duck, (120, 62))
    t = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    td = ImageDraw.Draw(t)
    f = _font("Unbounded-Black.ttf", 92)
    td.text((S / 2, 500), str(config.YEAR), font=f, fill=v["year"], anchor="mm", stroke_width=6, stroke_fill=v["stroke"])
    img.alpha_composite(_shadow(t, dy=6, blur=6, strength=0.35))
    img.alpha_composite(t)
    sl = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    _Sparkler(440, 150, count=40, spread=90, seed=5).draw(ImageDraw.Draw(sl), FRAMES // 3)
    img.alpha_composite(sl)
    img.convert("RGB").save(out, quality=95)
    return out


def ensure_promo(kind: str) -> Path:
    path = config.CARDS_DIR / f"promo_{kind}_{PROMO_VERSION}.jpg"
    if path.exists():
        return path
    with _render_lock:
        if not path.exists():
            promo(kind, path)
    return path


def path_for(user_id: int | None, digest: str, kind: str) -> Path:
    who = user_id if user_id else "generic"
    return config.CARDS_DIR / f"{who}_{kind}_{digest}.mp4"


#: rlottie не потокобезопасен, а ffmpeg и так грузит процессор целиком:
#: карточки рисуются строго по одной.
_render_lock = threading.Lock()


def ensure(data: dict | None, kind: str) -> Path:
    """Готовая карточка из кэша или свежая. Блокирующая — звать в потоке."""
    digest = facts.digest(data) if data else "v5"
    path = path_for((data or {}).get("user", {}).get("id"), digest, kind)
    if path.exists():
        return path
    with _render_lock:
        if not path.exists():
            render(data, kind, path)
    return path
