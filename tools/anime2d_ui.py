"""Интерфейс стрима, нарисованный кодом поверх AI-картинок (чётко, в любом размере): экран монитора
с аватаром и чатом, угловая карточка эфира, плашка «ЗРИТЕЛЕЙ». PIL рисует в 2× и уменьшает — ровные края.

Все функции возвращают RGBA float32 0..1 с премультиплицированной альфой (как tools/anime2d.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from tools.anime2d import clamp01, ease_out_back, smooth

FONTS = Path(__file__).resolve().parent.parent / "vendor" / "fonts"
SS = 2  # суперсэмплинг


@lru_cache(maxsize=64)
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def to_np(img: Image.Image) -> np.ndarray:
    """PIL RGBA (2×) → float32 премульт. в целевом размере."""
    w, h = img.size
    img = img.resize((w // SS, h // SS), Image.LANCZOS)
    a = np.asarray(img, np.float32) / 255.0
    a[..., :3] *= a[..., 3:4]
    return a


def pill(d: ImageDraw.ImageDraw, xy, r, fill, outline=None, width=0) -> None:
    d.rounded_rectangle([v * SS for v in xy], radius=r * SS, fill=fill, outline=outline, width=width * SS)


def text(d: ImageDraw.ImageDraw, xy, s: str, fnt: str, size: int, fill, anchor="la") -> None:
    d.text((xy[0] * SS, xy[1] * SS), s, font=font(fnt, size * SS), fill=fill, anchor=anchor)


def text_w(s: str, fnt: str, size: int) -> float:
    return font(fnt, size * SS).getlength(s) / SS


def live_badge(d: ImageDraw.ImageDraw, x: float, y: float, size: int, t: float) -> float:
    """Красная плашка LIVE с пульсирующей точкой; возвращает ширину."""
    w = text_w("LIVE", "GolosText-Black.ttf", size) + size * 1.9
    h = size * 1.55
    pill(d, (x, y, x + w, y + h), h * 0.3, (235, 30, 60, 255))
    dot = 0.6 + 0.4 * math.sin(t * 6.0)
    r = size * 0.24
    cx, cy = x + size * 0.62, y + h / 2
    d.ellipse([(cx - r) * SS, (cy - r) * SS, (cx + r) * SS, (cy + r) * SS], fill=(255, 255, 255, int(255 * dot)))
    text(d, (x + size * 1.05, y + h / 2), "LIVE", "GolosText-Black.ttf", size, (255, 255, 255, 255), "lm")
    return w


def viewers_value(t: float, t0: float, t1: float, a: int, b: int) -> int:
    """Счётчик зрителей: тикает от a к b с ускорением в начале и мягкой остановкой."""
    x = clamp01((t - t0) / max(1e-6, t1 - t0))
    return int(round(a + (b - a) * (1 - (1 - x) ** 2.2)))


@dataclass
class Msg:
    t: float
    user: str
    color: tuple[int, int, int]
    text: str
    big: bool = False


def screen(t: float, w: int, h: int, msgs: list[Msg], viewers: int, plus: tuple[float, int] | None,
           title: str) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Экран монитора: верхняя полоса (LIVE, название, зрители), слева окно аватара, справа чат.
    Возвращает (RGBA, прямоугольник окна аватара x0, y0, x1, y1) — аватар кладёт вызывающий."""
    img = Image.new("RGBA", (w * SS, h * SS), (12, 16, 30, 255))
    d = ImageDraw.Draw(img)
    bar = int(h * 0.12)
    d.rectangle([0, 0, w * SS, bar * SS], fill=(20, 24, 42, 255))
    bw = live_badge(d, w * 0.025, bar * 0.2, int(bar * 0.38), t)
    text(d, (w * 0.025 + bw + 16, bar / 2), title, "GolosText-Bold.ttf", int(bar * 0.36), (205, 214, 240, 255), "lm")
    vs = f"ЗРИТЕЛЕЙ {viewers}"
    fs = int(bar * 0.42)
    vw = text_w(vs, "GolosText-Black.ttf", fs)
    pill(d, (w * 0.975 - vw - 28, bar * 0.16, w * 0.975, bar * 0.84), bar * 0.2, (36, 44, 72, 255))
    text(d, (w * 0.975 - 14, bar / 2), vs, "GolosText-Black.ttf", fs, (255, 255, 255, 255), "rm")
    if plus is not None:
        pt, pv = plus
        k = clamp01((t - pt) / 1.2)
        if 0 < k < 1:
            a = int(255 * (1 - smooth((k - 0.6) / 0.4)))
            yy = bar * 1.25 - 26 * smooth(k)
            pill(d, (w * 0.975 - 120, yy, w * 0.975, yy + bar * 0.62), bar * 0.2, (46, 232, 111, a))
            text(d, (w * 0.975 - 60, yy + bar * 0.31), f"+{pv}", "GolosText-Black.ttf", int(bar * 0.4),
                 (10, 40, 20, a), "mm")
    # окно аватара
    av = (int(w * 0.02), bar + int(h * 0.03), int(w * 0.6), h - int(h * 0.04))
    grad = Image.linear_gradient("L").resize(((av[2] - av[0]) * SS, (av[3] - av[1]) * SS))
    top, bot = Image.new("RGBA", grad.size, (92, 64, 190, 255)), Image.new("RGBA", grad.size, (30, 120, 200, 255))
    panel = Image.composite(bot, top, grad)
    mask = Image.new("L", grad.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, grad.size[0], grad.size[1]], radius=18 * SS, fill=255)
    img.paste(panel, (av[0] * SS, av[1] * SS), mask)
    # чат
    cx0 = int(w * 0.63)
    text(d, (cx0, bar + h * 0.07), "ЧАТ", "GolosText-Black.ttf", int(h * 0.045), (255, 90, 120, 255), "lm")
    cy0, cy1 = int(bar + h * 0.12), h - int(h * 0.04)
    chat = Image.new("RGBA", ((w - cx0) * SS, (cy1 - cy0) * SS), (0, 0, 0, 0))
    _chat(ImageDraw.Draw(chat), t, msgs, 0, 0, w - cx0 - int(w * 0.02), cy1 - cy0)
    fade = Image.linear_gradient("L").resize((chat.size[0], 40 * SS))  # верх чата растворяется
    ca = chat.getchannel("A")
    ca.paste(Image.composite(ca.crop((0, 0, chat.size[0], 40 * SS)), Image.new("L", fade.size, 0), fade), (0, 0))
    chat.putalpha(ca)
    img.alpha_composite(chat, (cx0 * SS, cy0 * SS))
    return to_np(img), av


def _chat(d: ImageDraw.ImageDraw, t: float, msgs: list[Msg], x0: float, y0: float, x1: float, y1: float) -> None:
    """Сообщения прижаты к низу; новое въезжает снизу (обрезка — по холсту чата), старые уезжают вверх."""
    us, ts, tb = 25, 34, 40
    rows = []
    for m in msgs:
        if t < m.t:
            continue
        k = smooth((t - m.t) / 0.28)
        hgt = (us + 6 + (tb if m.big else ts) + 16) * k
        rows.append((m, k, hgt))
    y = y1
    for m, k, hgt in reversed(rows):
        y -= hgt
        if y + hgt < y0:
            break
        a = int(255 * k)
        fresh = clamp01(1 - (t - m.t) / 0.9)
        if fresh > 0 and m.big and hgt > 12:
            pill(d, (x0 - 10, y - 4, x1, y + hgt - 6), 12, (255, 211, 92, int(70 * fresh * k)))
        sx = x0 + (1 - ease_out_back(k)) * 40
        text(d, (sx, y), m.user, "GolosText-Black.ttf", us, (*m.color, a))
        size = tb if m.big else ts
        text(d, (sx, y + us + 6), m.text, "GolosText-Bold.ttf", size, (255, 255, 255, a))


def stream_card(w: int, h: int, t: float, viewers: int, title: str) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Угловая карточка «мой эфир»: рамка, LIVE, зрители, окно под аватар (возвращается его прямоугольник)."""
    pad = 18
    img = Image.new("RGBA", ((w + 2 * pad) * SS, (h + 2 * pad) * SS), (0, 0, 0, 0))
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([pad * SS, (pad + 8) * SS, (w + pad) * SS, (h + pad + 8) * SS],
                                         radius=26 * SS, fill=(0, 0, 0, 150))
    img = Image.alpha_composite(img, sh.filter(ImageFilter.GaussianBlur(14 * SS)))
    d = ImageDraw.Draw(img)
    pill(d, (pad, pad, w + pad, h + pad), 26, (16, 20, 36, 245), outline=(120, 150, 255, 255), width=3)
    bar = int(h * 0.16)
    bw = live_badge(d, pad + 16, pad + bar * 0.24, int(bar * 0.36), t)
    text(d, (pad + 16 + bw + 12, pad + bar * 0.24 + bar * 0.28), title, "GolosText-Bold.ttf", int(bar * 0.32),
         (205, 214, 240, 255), "lm")
    vs = f"ЗРИТЕЛЕЙ {viewers}"
    text(d, (w + pad - 16, pad + bar * 0.52), vs, "GolosText-Black.ttf", int(bar * 0.34), (255, 255, 255, 255), "rm")
    slot = (pad + 14, pad + bar + 6, w + pad - 14, h + pad - 14)
    grad = Image.linear_gradient("L").resize(((slot[2] - slot[0]) * SS, (slot[3] - slot[1]) * SS))
    top, bot = Image.new("RGBA", grad.size, (92, 64, 190, 255)), Image.new("RGBA", grad.size, (30, 120, 200, 255))
    m = Image.new("L", grad.size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, grad.size[0], grad.size[1]], radius=16 * SS, fill=255)
    img.paste(Image.composite(bot, top, grad), (slot[0] * SS, slot[1] * SS), m)
    return to_np(img), slot


def hud(label: str, value: str, size: int = 30) -> np.ndarray:
    """Маленькая плашка «ЗРИТЕЛЕЙ 41» как в историях (полупрозрачная тёмная)."""
    lw, vw = text_w(label, "GolosText-Black.ttf", int(size * 0.6)), text_w(value, "GolosText-Black.ttf", size)
    w, h = int(lw + vw + size * 1.4), int(size * 1.7)
    img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pill(d, (0, 0, w, h), h * 0.3, (10, 12, 22, 200))
    text(d, (size * 0.5, h / 2), label, "GolosText-Black.ttf", int(size * 0.6), (200, 205, 220, 255), "lm")
    text(d, (size * 0.9 + lw, h / 2), value, "GolosText-Black.ttf", size, (255, 255, 255, 255), "lm")
    return to_np(img)
