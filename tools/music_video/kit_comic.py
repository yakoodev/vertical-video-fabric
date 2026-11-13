# -*- coding: utf-8 -*-
"""Стиль «comic»: буквы комикс-леттеринга, вспышки-звукоподражания, лучи скорости, полоса земли, кусты,
рамка панели, плашки подписей. Всё процедурно (PIL + numpy), RGBA uint8 в порядке RGB.

Палитра по образцу comic_monster_show: фиолетовый, сливочный, оранжевый, горчичный, салатовый, чёрный контур."""
from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONTS = Path(__file__).resolve().parents[2] / "vendor" / "fonts"

PALETTE = {
    "cream": (250, 236, 200), "orange": (240, 112, 38), "yellow": (255, 206, 48), "purple": (88, 52, 140),
    "dpurple": (46, 28, 78), "lime": (156, 214, 64), "red": (222, 44, 44), "white": (255, 255, 255),
    "black": (18, 14, 22), "pink": (255, 120, 170), "blue": (70, 150, 230), "brown": (120, 72, 40),
}


def col(c) -> tuple[int, int, int]:
    return PALETTE[c] if isinstance(c, str) else tuple(int(v) for v in c)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _halftone(h: int, w: int, cell: int = 9, angle: float = 0.5, amount: np.ndarray | None = None) -> np.ndarray:
    """Маска точек растра 0..1; amount (h×w 0..1) — радиус точки."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    ca, sa = math.cos(angle), math.sin(angle)
    u, v = (xx * ca - yy * sa) / cell, (xx * sa + yy * ca) / cell
    d = np.hypot(u - np.round(u), v - np.round(v))
    r = 0.42 if amount is None else 0.62 * np.sqrt(np.clip(amount, 0, 1))
    return np.clip((r - d) * cell * 0.9, 0, 1)


def letter(ch: str, rng: np.random.Generator, height: int = 380, fontname: str = "Rubik-Black.ttf",
           fill="yellow", fill2="orange", outline="black", stroke: float = 0.085, shadow=(0.07, 0.09)) -> np.ndarray:
    """Буква комикс-леттеринга: заливка с переходом сверху вниз, растр в нижней части, толстый контур,
    жёсткая тень со смещением (как «выдавленные» буквы обложек)."""
    f = font(fontname, height)
    sw = max(3, int(height * stroke))
    l, t, r, b = f.getbbox(ch, stroke_width=sw)
    pad = int(height * 0.06) + int(height * max(shadow))
    W, H = r - l + 2 * pad, b - t + 2 * pad
    org = (pad - l, pad - t)
    m_fill = Image.new("L", (W, H), 0); ImageDraw.Draw(m_fill).text(org, ch, font=f, fill=255)
    m_all = Image.new("L", (W, H), 0); ImageDraw.Draw(m_all).text(org, ch, font=f, fill=255, stroke_width=sw, stroke_fill=255)
    fa = np.asarray(m_fill).astype(np.float32) / 255
    aa = np.asarray(m_all).astype(np.float32) / 255
    dx, dy = int(height * shadow[0]), int(height * shadow[1])
    sh = np.zeros_like(aa)
    sh[dy:, dx:] = aa[:H - dy, :W - dx]
    ys = np.linspace(0, 1, H)[:, None] * np.ones((1, W))
    c1, c2 = np.array(col(fill), np.float32) / 255, np.array(col(fill2), np.float32) / 255
    g = np.clip((ys - 0.25) / 0.6, 0, 1)[..., None]
    rgb = c1 * (1 - g) + c2 * g
    # растр тени внутри буквы (нижняя часть) + блик сверху
    dots = _halftone(H, W, cell=max(5, height // 34), amount=np.clip((ys - 0.45) * 1.6, 0, 1))
    rgb = rgb * (1 - dots[..., None] * 0.28)
    hl = np.clip(1 - ys / 0.18, 0, 1) * 0.25
    rgb = np.clip(rgb + hl[..., None], 0, 1)
    ink = np.array(col(outline), np.float32) / 255
    out_rgb = ink * (1 - fa[..., None]) + rgb * fa[..., None]
    alpha = np.maximum(aa, sh)
    out_rgb = out_rgb * (aa / np.maximum(alpha, 1e-4))[..., None] + ink * ((alpha - aa).clip(0) / np.maximum(alpha, 1e-4))[..., None]
    return (np.dstack([np.clip(out_rgb, 0, 1), alpha]) * 255 + 0.5).astype(np.uint8)


def chip(text: str, rng: np.random.Generator, cap: int = 70, fontname: str = "Rubik-Bold.ttf", bg="cream",
         ink="black", border="black") -> np.ndarray:
    """Плашка подписи (как «рамка рассказчика» в комиксе): светлый прямоугольник, контур, тень."""
    f = font(fontname, int(cap * 1.25))
    l, t, r, b = f.getbbox(text)
    px, py = int(cap * 0.38), int(cap * 0.26)
    bw = max(3, cap // 11)
    sd = max(4, cap // 8)
    W, H = r - l + 2 * px + 2 * bw + sd + 4, b - t + 2 * py + 2 * bw + sd + 4
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = 2, 2, W - sd - 2, H - sd - 2
    d.rectangle((x0 + sd, y0 + sd, x1 + sd, y1 + sd), fill=col(border) + (255,))
    d.rectangle((x0, y0, x1, y1), fill=col(border) + (255,))
    d.rectangle((x0 + bw, y0 + bw, x1 - bw, y1 - bw), fill=col(bg) + (255,))
    d.text((x0 + bw + px - l, y0 + bw + py - t), text, font=f, fill=col(ink) + (255,))
    return np.asarray(im).copy()


def star_poly(cx: float, cy: float, rx: float, ry: float, n: int, rng: np.random.Generator, spike: float = 0.38) -> list:
    pts = []
    for i in range(n * 2):
        a = i / (n * 2) * 2 * math.pi + rng.uniform(-0.08, 0.08)
        rr = 1.0 if i % 2 == 0 else 1 - spike * rng.uniform(0.75, 1.15)
        rr *= rng.uniform(0.9, 1.08) if i % 2 == 0 else 1
        pts.append((cx + math.cos(a) * rx * rr, cy + math.sin(a) * ry * rr))
    return pts


def burst(text: str, rng: np.random.Generator, cap: int = 210, fontname: str = "Rubik-Black.ttf", fill="yellow",
          fill2="orange", ring="red", ink_fill="white", ink_fill2="yellow", lines: int = 1) -> np.ndarray:
    """Вспышка-звукоподражание: зубчатая звезда (две обводки) + слово комикс-леттерингом внутри."""
    words = text.split("\n")
    tiles = [Image.fromarray(letter_word(wd, rng, cap, fontname, ink_fill, ink_fill2), "RGBA") for wd in words]
    tw = max(t.width for t in tiles)
    th = sum(t.height for t in tiles) - (len(tiles) - 1) * cap * 0.25
    rx, ry = tw * 0.66 + cap * 0.5, th * 0.66 + cap * 0.55
    W, H = int(rx * 2.25), int(ry * 2.25)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    n = int(rng.integers(11, 16))
    outer = star_poly(W / 2, H / 2, rx * 1.06, ry * 1.06, n, rng, 0.34)
    d.polygon(outer, fill=col("black") + (255,))
    mid = [(W / 2 + (x - W / 2) * 0.955, H / 2 + (y - H / 2) * 0.955) for x, y in outer]
    d.polygon(mid, fill=col(ring) + (255,))
    inner = [(W / 2 + (x - W / 2) * 0.82, H / 2 + (y - H / 2) * 0.82) for x, y in outer]
    d.polygon([(W / 2 + (x - W / 2) * 0.86, H / 2 + (y - H / 2) * 0.86) for x, y in outer], fill=col("black") + (255,))
    d.polygon(inner, fill=col(fill) + (255,))
    arr = np.asarray(im).astype(np.float32) / 255
    ys = np.linspace(0, 1, H)[:, None] * np.ones((1, W))
    innerm = np.zeros((H, W), np.uint8)
    cv2.fillPoly(innerm, [np.int32(inner)], 1)
    g = np.clip(ys * 1.2 - 0.1, 0, 1)
    c2 = np.array(col(fill2), np.float32) / 255
    arr[..., :3] = np.where(innerm[..., None] > 0, arr[..., :3] * (1 - g[..., None] * 0.7) + c2 * g[..., None] * 0.7, arr[..., :3])
    dots = _halftone(H, W, cell=max(6, cap // 18), amount=np.clip(np.hypot(ys - 0.5, (np.linspace(0, 1, W)[None] - 0.5)) * 1.4, 0, 1))
    arr[..., :3] = np.where(innerm[..., None] > 0, arr[..., :3] * (1 - dots[..., None] * 0.18), arr[..., :3])
    base = Image.fromarray((arr * 255).astype(np.uint8), "RGBA")
    # текст: строки комикс-букв, лёгкий наклон
    y = H / 2 - th / 2
    for L in tiles:
        base.alpha_composite(L, (int(W / 2 - L.width / 2), int(y)))
        y += L.height - cap * 0.25
    return np.asarray(base).copy()


def letter_word(word: str, rng: np.random.Generator, cap: int, fontname: str, fill, fill2) -> np.ndarray:
    """Слово целиком одной картинкой (для вспышек): буквы с лёгким разбросом по высоте и наклону."""
    tiles = [letter(ch, rng, cap, fontname, fill, fill2) if ch != " " else None for ch in word]
    widths = [t.shape[1] if t is not None else cap * 0.35 for t in tiles]
    H = max(t.shape[0] for t in tiles if t is not None) + cap // 3
    W = int(sum(w * 0.84 for w in widths) + widths[-1] * 0.2) + 10
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    x = 5.0
    for t, w in zip(tiles, widths):
        if t is not None:
            L = Image.fromarray(t, "RGBA").rotate(float(rng.uniform(-7, 7)), resample=Image.BICUBIC, expand=True)
            im.alpha_composite(L, (int(x), int(H / 2 - L.height / 2 + rng.uniform(-cap * 0.06, cap * 0.06))))
        x += w * 0.84
    return np.asarray(im).copy()


def speed_burst(h: int, w: int, rng: np.random.Generator, c_bg="cream", c_line="black", c_center="cream",
                n: int = 120, center=(0.5, 0.45)) -> np.ndarray:
    """Фон «лучи скорости»: тонкие клинья к центру, чистый овал в центре."""
    cx, cy = w * center[0], h * center[1]
    im = Image.new("RGB", (w, h), col(c_bg))
    d = ImageDraw.Draw(im)
    R = math.hypot(w, h)
    for i in range(n):
        a = rng.uniform(0, 2 * math.pi)
        da = rng.uniform(0.004, 0.022)
        r0 = rng.uniform(0.22, 0.42) * min(w, h)
        pts = [(cx + math.cos(a) * r0, cy + math.sin(a) * r0),
               (cx + math.cos(a - da) * R, cy + math.sin(a - da) * R),
               (cx + math.cos(a + da) * R, cy + math.sin(a + da) * R)]
        d.polygon(pts, fill=col(c_line))
    arr = np.asarray(im).astype(np.float32) / 255
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    rr = np.hypot((xx - cx) / (w * 0.5), (yy - cy) / (h * 0.5))
    dots = _halftone(h, w, cell=12, amount=np.clip((rr - 0.5) * 0.8, 0, 1))
    arr = arr * (1 - dots[..., None] * 0.22)
    arr = (arr * 255).astype(np.uint8)
    return np.dstack([arr, np.full((h, w), 255, np.uint8)])


def ground_strip(w: int, h: int, rng: np.random.Generator, body="purple", stripe="lime", waves: float = 2.2,
                 amp: float = 0.10) -> np.ndarray:
    """Волнистая полоса земли на переднем плане (как в эталоне): заливка, светлая кромка, чёрный контур, растр."""
    xs = np.arange(w)
    ph = rng.uniform(0, 6.28)
    top = h * 0.32 + amp * h * (np.sin(xs / w * waves * 2 * math.pi + ph) * 0.7 + np.sin(xs / w * waves * 4.1 * math.pi + ph * 2) * 0.3)
    yy = np.arange(h)[:, None]
    body_m = (yy >= top[None, :] + h * 0.07).astype(np.float32)
    stripe_m = ((yy >= top[None, :]) & (yy < top[None, :] + h * 0.07)).astype(np.float32)
    lw = max(4, h // 45)
    line_m = ((yy >= top[None, :] - lw) & (yy < top[None, :])).astype(np.float32) + \
        ((yy >= top[None, :] + h * 0.07) & (yy < top[None, :] + h * 0.07 + lw * 0.7)).astype(np.float32)
    rgb = np.zeros((h, w, 3), np.float32)
    cb, cs, ck = (np.array(col(c), np.float32) / 255 for c in (body, stripe, "black"))
    rgb += body_m[..., None] * cb + stripe_m[..., None] * cs
    dots = _halftone(h, w, cell=10, amount=np.clip((yy / h - 0.4) * 1.3, 0, 1) * np.ones((1, w)))
    rgb *= 1 - dots[..., None] * 0.3 * body_m[..., None]
    a = np.clip(body_m + stripe_m + line_m, 0, 1)
    rgb = rgb * (1 - np.clip(line_m, 0, 1)[..., None]) + ck * np.clip(line_m, 0, 1)[..., None]
    a = cv2.GaussianBlur(a, (0, 0), 0.7)
    return (np.dstack([np.clip(rgb, 0, 1), a]) * 255 + 0.5).astype(np.uint8)


def bush(w: int, h: int, rng: np.random.Generator, body="dpurple", rim="purple") -> np.ndarray:
    """Куст-силуэт для переднего плана: круглые «облака» с контуром и светлой кромкой."""
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    blobs = []
    for i in range(9):
        x = rng.uniform(0.12, 0.88) * w
        r = rng.uniform(0.16, 0.28) * w
        y = h - r * rng.uniform(0.3, 0.9)
        blobs.append((x, y, r))
    lw = max(5, w // 60)
    for x, y, r in blobs:
        d.ellipse((x - r - lw, y - r - lw, x + r + lw, y + r + lw), fill=col("black") + (255,))
    d.rectangle((0, h * 0.8, w, h), fill=col("black") + (255,))
    for x, y, r in blobs:
        d.ellipse((x - r, y - r, x + r, y + r), fill=col(rim) + (255,))
    for x, y, r in blobs:
        d.ellipse((x - r * 0.93 + r * 0.08, y - r * 0.93 + r * 0.1, x + r * 0.93 + r * 0.08, y + r * 0.93 + r * 0.1), fill=col(body) + (255,))
    d.rectangle((lw, h * 0.8 + lw, w - lw, h), fill=col(body) + (255,))
    return np.asarray(im).copy()


def panel(rgb: np.ndarray, border: int = 26, ink: int = 9, shadow: int = 22) -> np.ndarray:
    """Картинка сцены в рамке комикс-панели: белые поля, чёрный контур, жёсткая тень. На входе RGB uint8."""
    h, w = rgb.shape[:2]
    W, H = w + 2 * border + shadow, h + 2 * border + shadow
    out = np.zeros((H, W, 4), np.uint8)
    out[shadow:shadow + h + 2 * border, shadow:shadow + w + 2 * border] = (*col("black"), 200)
    out[0:h + 2 * border, 0:w + 2 * border] = (*col("black"), 255)
    out[ink:h + 2 * border - ink, ink:w + 2 * border - ink] = (*col("white"), 255)
    out[border:border + h, border:border + w, :3] = rgb
    out[border:border + h, border:border + w, 3] = 255
    out[border - 4:border, border - 4:border + w + 4] = (*col("black"), 255)
    out[border + h:border + h + 4, border - 4:border + w + 4] = (*col("black"), 255)
    out[border - 4:border + h + 4, border - 4:border] = (*col("black"), 255)
    out[border - 4:border + h + 4, border + w:border + w + 4] = (*col("black"), 255)
    return out


def shadow_sprite() -> np.ndarray:
    """Контактная тень: плоский тёмный овал с мягким краем (рисованная тень комикса)."""
    m = np.zeros((300, 1000), np.float32)
    cv2.ellipse(m, (500, 150), (440, 92), 0, 0, 360, 1.0, -1)
    m = cv2.GaussianBlur(m, (0, 0), 9) * 0.62
    rgba = np.zeros((300, 1000, 4), np.uint8)
    rgba[..., :3] = col("dpurple")
    rgba[..., 3] = (m * 255).astype(np.uint8)
    return rgba


def censor_bar(text: str, rng: np.random.Generator, cap: int = 120) -> np.ndarray:
    """Чёрная плашка цензуры с белыми буквами и красной рамкой-штампом."""
    f = font("RubikMonoOne-Regular.ttf", cap)
    l, t, r, b = f.getbbox(text)
    px, py = cap // 2, cap // 3
    W, H = r - l + 2 * px, b - t + 2 * py
    im = Image.new("RGBA", (W + 20, H + 20), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((10, 10, W + 10, H + 10), fill=(10, 10, 12, 255), outline=col("red") + (255,), width=max(6, cap // 12))
    d.text((10 + px - l, 10 + py - t), text, font=f, fill=(255, 255, 255, 255))
    return np.asarray(im).copy()


def flash_star(size: int, rng: np.random.Generator, fill="yellow", ring="orange", line="orange") -> np.ndarray:
    """Звезда-вспышка для перехода: зубчатый силуэт с лучами скорости внутри и чёрным контуром."""
    sb = speed_burst(size, size, rng, fill, line, n=90, center=(0.5, 0.5))
    W = H = size
    outer = star_poly(W / 2, H / 2, W * 0.49, H * 0.49, 14, rng, 0.3)
    m = np.zeros((H, W), np.uint8); cv2.fillPoly(m, [np.int32(outer)], 255)
    m_in = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m_in, [np.int32([(W / 2 + (x - W / 2) * 0.93, H / 2 + (y - H / 2) * 0.93) for x, y in outer])], 255)
    m_mid = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m_mid, [np.int32([(W / 2 + (x - W / 2) * 0.965, H / 2 + (y - H / 2) * 0.965) for x, y in outer])], 255)
    out = np.zeros((H, W, 4), np.uint8)
    out[..., :3] = col("black")
    out[m_mid > 0, :3] = col(ring)
    out[m_in > 0, :3] = sb[m_in > 0, :3]
    out[..., 3] = cv2.GaussianBlur(m, (0, 0), 0.8)
    return out
