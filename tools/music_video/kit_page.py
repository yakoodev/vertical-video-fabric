# -*- coding: utf-8 -*-
"""Страница комикса для стиля comic: раскладка панелей, пузыри реплик и рамки рассказчика, слова для пузырей,
процедурные панели (лучи + надпись), клякса туши, растр для растворения. RGBA uint8 в порядке RGB."""
from __future__ import annotations

import math

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from kit_comic import _halftone, col, font, letter_word, speed_burst, star_poly


def cover(rgb: np.ndarray, w: int, h: int, focus=(0.5, 0.5)) -> np.ndarray:
    """Кадрировать картинку под размер панели без искажений (как object-fit: cover)."""
    ih, iw = rgb.shape[:2]
    k = max(w / iw, h / ih)
    rs = cv2.resize(rgb, (max(w, int(round(iw * k))), max(h, int(round(ih * k)))),
                    interpolation=cv2.INTER_AREA if k < 1 else cv2.INTER_CUBIC)
    x0 = int((rs.shape[1] - w) * focus[0]); y0 = int((rs.shape[0] - h) * focus[1])
    return rs[y0:y0 + h, x0:x0 + w]


def page(width: int, rows: list[list[dict]], rng: np.random.Generator, margin: int = 50, gutter: int = 34,
         ink: int = 9, paper="cream") -> tuple[np.ndarray, dict]:
    """Страница: ряды панелей; ряд заполняет ширину, ширины по пропорциям; углы панелей слегка «от руки».

    Панель: {"id", "rgb": HxWx3 uint8 RGB, "aspect": w/h, "focus": (fx, fy)}.
    Возвращает RGBA страницы и раскладку {id: {"quad": 4 точки, "rect": [x0, y0, x1, y1]}} в px страницы."""
    inner = width - 2 * margin
    layout, y = [], margin
    for row in rows:
        tot = sum(p["aspect"] for p in row)
        h = (inner - gutter * (len(row) - 1)) / tot
        x = margin
        cells = []
        for p in row:
            w = h * p["aspect"]
            cells.append((p, [x, y, x + w, y + h]))
            x += w + gutter
        layout.append(cells)
        y += h + gutter
    H = int(y - gutter + margin)
    base = np.array(col(paper), np.float32) / 255
    img = np.ones((H, width, 3), np.float32) * base
    dots = _halftone(H, width, cell=14, angle=0.6, amount=np.full((H, width), 0.18, np.float32))
    img *= 1 - dots[..., None] * 0.12
    out = {}
    for cells in layout:
        for p, (x0, y0, x1, y1) in cells:
            j = 14
            quad = np.float32([[x0 + rng.uniform(-j, j), y0 + rng.uniform(-j, j)], [x1 + rng.uniform(-j, j), y0 + rng.uniform(-j, j)],
                               [x1 + rng.uniform(-j, j), y1 + rng.uniform(-j, j)], [x0 + rng.uniform(-j, j), y1 + rng.uniform(-j, j)]])
            pw, ph = int(x1 - x0), int(y1 - y0)
            src = cover(p["rgb"], pw, ph, p.get("focus", (0.5, 0.5))).astype(np.float32) / 255
            M = cv2.getPerspectiveTransform(np.float32([[0, 0], [pw, 0], [pw, ph], [0, ph]]), quad)
            bx0, by0 = int(max(0, quad[:, 0].min() - 4)), int(max(0, quad[:, 1].min() - 4))
            bx1, by1 = int(min(width, quad[:, 0].max() + 4)), int(min(H, quad[:, 1].max() + 4))
            T = np.float32([[1, 0, -bx0], [0, 1, -by0], [0, 0, 1]]) @ M
            warped = cv2.warpPerspective(src, T, (bx1 - bx0, by1 - by0), flags=cv2.INTER_LINEAR)
            m = np.zeros((by1 - by0, bx1 - bx0), np.uint8)
            cv2.fillPoly(m, [np.int32(quad - [bx0, by0])], 255)
            m = cv2.GaussianBlur(m.astype(np.float32) / 255, (0, 0), 0.6)[..., None]
            sub = img[by0:by1, bx0:bx1]
            img[by0:by1, bx0:bx1] = sub * (1 - m) + warped * m
            cv2.polylines(img, [np.int32(quad)], True, (0.07, 0.055, 0.086), ink, cv2.LINE_AA)
            out[p["id"]] = {"quad": quad.tolist(), "rect": [float(quad[:, 0].min()), float(quad[:, 1].min()),
                                                            float(quad[:, 0].max()), float(quad[:, 1].max())]}
    rgba = np.dstack([np.clip(img, 0, 1), np.ones((H, width))])
    return (rgba * 255 + 0.5).astype(np.uint8), out


def stamp_panel(text: str, w: int, h: int, rng: np.random.Generator, bg="yellow", line="orange", fill="red",
                fill2="orange", font_name="Rubik-Black.ttf") -> np.ndarray:
    """Процедурная панель: лучи скорости + крупная надпись комикс-леттерингом. RGB."""
    sb = speed_burst(h, w, rng, bg, line, n=110, center=(0.5, 0.5))[..., :3]
    base = Image.fromarray(np.ascontiguousarray(sb)).convert("RGBA")
    lines = [s for s in text.split("\n") if s] or [""]
    cap = int(min(h / (len(lines) * 1.3), w / (max(len(s) for s in lines) * 0.9 + 0.5)))
    tiles = [Image.fromarray(letter_word(s, rng, cap, font_name, fill, fill2), "RGBA") for s in lines if s]
    k = min(1.0, 0.9 * w / max(t.width for t in tiles), 0.85 * h / sum(t.height * 0.9 for t in tiles))
    if k < 1.0:
        tiles = [t.resize((int(t.width * k), int(t.height * k)), Image.LANCZOS) for t in tiles]
    y = h / 2 - sum(t.height * 0.9 for t in tiles) / 2
    for L in tiles:
        base.alpha_composite(L, (int(w / 2 - L.width / 2), int(y)))
        y += L.height * 0.9
    return np.asarray(base)[..., :3].copy()


def bubble(w: int, h: int, kind: str = "speech", tail=(0.3, 1.35), seed: int = 1) -> tuple[np.ndarray, tuple]:
    """Пузырь под блок текста w×h px. speech — овал с хвостом, shout — зубчатый, whisper — пунктир,
    caption — жёлтая рамка рассказчика. Возвращает RGBA и левый верхний угол текстового блока внутри картинки."""
    rng = np.random.default_rng(seed)
    if kind == "caption":
        pad = int(h * 0.12) + 22
        W, H = w + 2 * pad + 14, h + 2 * pad + 14
        im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
        d.rectangle((12, 12, W - 1, H - 1), fill=col("black") + (255,))
        d.rectangle((0, 0, W - 13, H - 13), fill=col("black") + (255,))
        d.rectangle((7, 7, W - 20, H - 20), fill=col("yellow") + (255,))
        return np.asarray(im).copy(), (pad, pad)
    ax, ay = w * 0.5 * 1.25 + 34, h * 0.5 * 1.42 + 30
    tx, ty = tail
    tl = max(ay * 0.75, 70)
    W = int(ax * 2 + 70)
    up = ty < 0
    H = int(ay * 2 + tl + 70)
    cx = W / 2
    cy = (tl + 35 + ay) if up else (ay + 35)
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    lw = 9
    tipx = cx + (tx - 0.5) * w * 1.1
    tip = (tipx, cy - ay - tl * 0.95) if up else (tipx, cy + ay + tl * 0.95)
    sy = cy - ay * 0.7 if up else cy + ay * 0.7
    bx = cx + (tx - 0.5) * w * 0.5
    bl, br = (bx - ay * 0.22, sy), (bx + ay * 0.22, sy)
    if kind == "shout":
        pts = star_poly(cx, cy, ax * 1.1, ay * 1.15, 18, rng, 0.18)
        d.polygon([bl, tip, br], fill=col("black") + (255,))
        d.polygon(pts, fill=col("black") + (255,))
        d.polygon([(cx + (x - cx) * 0.95, cy + (y - cy) * 0.95) for x, y in pts], fill=col("white") + (255,))
        d.polygon([(bl[0] + 6, bl[1]), (tip[0], tip[1] + (14 if up else -14)), (br[0] - 6, br[1])], fill=col("white") + (255,))
    else:
        d.polygon([(bl[0] - lw, bl[1]), tip, (br[0] + lw, br[1])], fill=col("black") + (255,))
        d.ellipse((cx - ax - lw, cy - ay - lw, cx + ax + lw, cy + ay + lw), fill=col("black") + (255,))
        d.ellipse((cx - ax, cy - ay, cx + ax, cy + ay), fill=col("white") + (255,))
        d.polygon([bl, (tip[0], tip[1] + (lw * 1.8 if up else -lw * 1.8)), br], fill=col("white") + (255,))
        if kind == "whisper":
            for k in range(0, 360, 12):
                d.arc((cx - ax - lw, cy - ay - lw, cx + ax + lw, cy + ay + lw), k, k + 5, fill=col("white") + (255,), width=lw + 2)
    return np.asarray(im).copy(), (int(cx - w / 2), int(cy - h / 2))


def text_tile(word: str, cap: int = 64, font_name: str = "Rubik-Bold.ttf", color="black") -> np.ndarray:
    """Слово для пузыря: только буквы на прозрачном фоне; высота всегда cap·1.3 (строки ровные)."""
    f = font(font_name, cap)
    l, t, r, b = f.getbbox(word)
    asc = f.getbbox("ЙЁ")[1]
    im = Image.new("RGBA", (max(8, r - l + 8), int(cap * 1.3)), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4 - l, int(cap * 0.12) - asc), word, font=f, fill=col(color) + (255,))
    return np.asarray(im).copy()


def ink_splat(size: int, rng: np.random.Generator, color="black") -> np.ndarray:
    """Клякса туши: неровный круг с выплесками и каплями."""
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0)); d = ImageDraw.Draw(im)
    c = size / 2
    pts = []
    for i in range(56):
        a = i / 56 * 2 * math.pi
        r = size * (0.30 + 0.05 * rng.random() + (0.13 * rng.random() if i % 4 == 0 else 0))
        pts.append((c + math.cos(a) * r, c + math.sin(a) * r))
    d.polygon(pts, fill=col(color) + (255,))
    for _ in range(30):
        a = rng.uniform(0, 2 * math.pi); r = size * rng.uniform(0.36, 0.48); s = size * rng.uniform(0.006, 0.028)
        d.ellipse((c + math.cos(a) * r - s, c + math.sin(a) * r - s, c + math.cos(a) * r + s, c + math.sin(a) * r + s),
                  fill=col(color) + (255,))
    return np.asarray(im.filter(ImageFilter.GaussianBlur(1.2))).copy()


def dots_field(w: int, h: int, cell: int = 64) -> np.ndarray:
    """Расстояние до центра ячейки растра (0 — центр точки, 255 — угол ячейки): по нему растут точки растворения."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    u, v = xx / cell, yy / cell
    dd = np.hypot(u - np.floor(u) - 0.5, v - np.floor(v) - 0.5) / 0.7072
    g = (np.clip(dd, 0, 1) * 255).astype(np.uint8)
    return np.dstack([g, g, g, np.full((h, w), 255, np.uint8)])


def tbc_card(w: int, h: int, rng: np.random.Generator) -> np.ndarray:
    """Плашка-гэг «ПРОДОЛЖЕНИЕ СЛЕДУЕТ…»: панель с лучами, надпись и стрелка. RGB."""
    rgb = stamp_panel("ПРОДОЛЖЕНИЕ\nСЛЕДУЕТ...", w, h, rng, bg="cream", line="purple", fill="yellow", fill2="orange")
    im = Image.fromarray(rgb).convert("RGBA"); d = ImageDraw.Draw(im)
    ax, ay = w * 0.62, h * 0.82
    arrow = [(ax, ay - 40), (ax + 220, ay - 40), (ax + 220, ay - 90), (ax + 340, ay), (ax + 220, ay + 90), (ax + 220, ay + 40), (ax, ay + 40)]
    d.polygon([(x + 8, y + 10) for x, y in arrow], fill=col("black") + (255,))
    d.polygon(arrow, fill=col("orange") + (255,), outline=col("black") + (255,), width=8)
    return np.asarray(im)[..., :3].copy()
