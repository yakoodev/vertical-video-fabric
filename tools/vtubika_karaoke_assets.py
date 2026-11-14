"""Серия 9 «Караоке при свечах»: картинки для плана (всё — в site/media, рендер отдаёт site/ как /site/).

    python tools/vtubika_karaoke_assets.py [saylium] [sticks] [crypt] [aurora] [lyrics]   (без аргументов — всё)

* saylium — розовый сайлиум すやみや (превью товара «Интерактивный Live2D-лайтстик Сайлиум», 1.15–3.75 с, где он
  качается за мышкой): фон превью (сиреневое небо со звёздами) вырезан, своё мягкое свечение вокруг палочки.
* sticks — лайтстики зала すやみや LightSticks Concert: сырые кадры серии 8 (кусок превью с палочками), вариант
  розовый (цвет Лили) и сиреневый.
* crypt — фон эфира Лили «склеп при свечах»: свой процедурный зацикленный кадр (стрельчатое окно с луной, кладка,
  свечи с живым пламенем, боке, туман).
* aurora — фон Вари в войсе: своё пастельное сияние с искрами.
* lyrics — строка караоке (свои слова): стекло, название песни, строка заливается розовым по слогам, следующая — серым.
"""

from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(r"D:\vvf-offload\vtubika\story9_karaoke")  # папка серии: site/media — исходники и готовые кадры
MEDIA = HERE / "site" / "media"
EP8_RAW = Path(r"D:\vvf-offload\vtubika\story8_collab\site\media\_sticks_raw")
FONTS = Path(r"C:\Windows\Fonts")


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _clean(out: Path, pat: str = "*") -> None:
    out.mkdir(parents=True, exist_ok=True)
    for p in out.glob(pat):
        if p.is_file():
            p.unlink()


def _largest(mask: np.ndarray, keep: int = 1, min_px: int = 0) -> np.ndarray:
    import cv2

    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if n <= 1:
        return mask
    sizes = stats[1:, cv2.CC_STAT_AREA]
    ok = np.zeros(n, bool)
    for i in np.argsort(sizes)[::-1][:keep]:
        if sizes[i] >= min_px:
            ok[i + 1] = True
    return ok[lab]


def _blur(a: np.ndarray, r: float) -> np.ndarray:
    return np.asarray(Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(r))).astype(float)


# --------------------------------------------------------------------------- сайлиум


def saylium() -> None:
    tmp = MEDIA / "_saylium_raw"
    _clean(tmp, "*.png")
    ff("-ss", "1.15", "-i", str(MEDIA / "saylium.mp4"), "-t", "2.6", "-vf", "fps=30,crop=560:720:700:0", "-start_number", "1",
       str(tmp / "r_%03d.png"))
    out = MEDIA / "saylium_pink"
    _clean(out, "*.png")
    for i, f in enumerate(sorted(tmp.glob("r_*.png")), 1):
        a = np.asarray(Image.open(f).convert("RGB")).astype(float)
        r, g, b = a[..., 0], a[..., 1], a[..., 2]
        # Палочка: яркое розовое тело и белая ручка (R > 185), золотые кольца (R заметно больше B). Фон — сиреневый
        # (R ≈ 120–160), звёзды — мелкие белые точки (отпадают по связности).
        body = np.clip((r - 168) / 30, 0, 1)
        gold = np.clip((r - b - 25) / 25, 0, 1) * (r > 110)
        alpha = np.maximum(body, gold)
        core = _largest(alpha > 0.5, keep=1)
        # Дыры внутри (блёстки темнее) — закрыть: заливка по контуру.
        import cv2

        cnts, _ = cv2.findContours(core.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        filled = np.zeros(core.shape, np.uint8)
        cv2.drawContours(filled, cnts, -1, 1, thickness=-1)
        hard = np.asarray(Image.fromarray(filled * 255).filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.1))).astype(float) / 255
        col = a.copy()
        # Кромка без сиреневого ореола фона: краевые пиксели — к цвету тела.
        edge = (hard > 0.02) & (hard < 0.9)
        col[edge] = np.maximum(col[edge], [235, 120, 205])
        # Своё свечение: размытая розовая копия под палочкой.
        glow_a = _blur(hard * 255, 16) / 255 * 0.85
        glow_c = np.array([255.0, 110, 200])
        out_a = hard + glow_a * (1 - hard)
        out_c = (col * hard[..., None] + glow_c * (glow_a * (1 - hard))[..., None]) / np.maximum(out_a[..., None], 1e-3)
        rgba = Image.fromarray(out_c.clip(0, 255).astype(np.uint8)).convert("RGBA")
        rgba.putalpha(Image.fromarray((out_a * 255).clip(0, 255).astype(np.uint8)))
        rgba.save(out / f"f_{i:03d}.png")
    print("saylium frames", len(list(out.glob("*.png"))))


# --------------------------------------------------------------------------- лайтстики зала


def sticks() -> None:
    frames = sorted(EP8_RAW.glob("r_*.png"))
    if not frames:
        raise SystemExit(f"нет сырых кадров лайтстиков {EP8_RAW}")
    for variant, hue in (("sticks_pink", 0.92), ("sticks_violet", 0.78)):
        out = MEDIA / variant
        _clean(out, "*.png")
        for i, f in enumerate(frames, 1):
            a = np.asarray(Image.open(f).convert("RGB")).astype(float)
            r, g, b = a[..., 0], a[..., 1], a[..., 2]
            green = g - np.maximum(r, b)
            alpha = np.clip((green - 18) / 70, 0, 1)
            m = _largest(alpha > 0.35, keep=40, min_px=300)
            soft = np.asarray(Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(5))
                              .filter(ImageFilter.GaussianBlur(1.2))).astype(float) / 255
            alpha = np.clip(alpha * soft * 1.15, 0, 1)
            rgb = a.copy()
            rgb[..., 0] = np.minimum(r, g * 0.55)
            rgb[..., 2] = np.minimum(b, g * 0.55)
            img = Image.fromarray(rgb.clip(0, 255).astype(np.uint8))
            hsv = np.asarray(img.convert("HSV")).astype(float)
            hsv[..., 0] = (hsv[..., 0] + 255 * (hue - 0.33)) % 255
            img = Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB")
            col = np.asarray(img).astype(float)
            h = col.shape[0]
            core = np.clip((alpha - 0.5) * 2, 0, 1)[..., None]
            col = col * (1 - 0.35 * core) + 255 * 0.35 * core
            pre = col * alpha[..., None]
            ga = _blur(alpha * 255, 9) / 255
            gc = np.stack([_blur(pre[..., c], 9) for c in range(3)], -1) / np.maximum(ga[..., None], 1e-3)
            ga = ga * 0.75
            out_a = alpha + ga * (1 - alpha)
            out_c = (col * alpha[..., None] + gc * (ga * (1 - alpha))[..., None]) / np.maximum(out_a[..., None], 1e-3)
            fade = np.clip(np.linspace(1, 0, h) / 0.25, 0, 1)[:, None]
            out_a = out_a * fade
            rgba = Image.fromarray(out_c.clip(0, 255).astype(np.uint8)).convert("RGBA")
            rgba.putalpha(Image.fromarray((out_a * 255).clip(0, 255).astype(np.uint8)))
            rgba.save(out / f"f_{i:03d}.png")
    print("sticks ok")


# --------------------------------------------------------------------------- фоны


W, H = 1280, 720


def _noise(rng: np.random.Generator, h: int, w: int, scale: int) -> np.ndarray:
    small = rng.random((h // scale + 2, w // scale + 2))
    return np.asarray(Image.fromarray((small * 255).astype(np.uint8)).resize((w + 2 * scale, h + 2 * scale), Image.BICUBIC)).astype(float) / 255


def crypt(n: int = 48) -> None:
    out = MEDIA / "crypt"
    _clean(out, "*.jpg")
    rng = np.random.default_rng(13)
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    # Кладка: тёмно-сиреневый камень, швы, пятна.
    base = np.zeros((H, W, 3))
    top, bot = np.array([24, 18, 38.0]), np.array([10, 8, 16.0])
    k = (yy / H)[..., None]
    base[:] = top * (1 - k) + bot * k
    row_h = 54
    row = (yy // row_h).astype(int)
    off = (row % 2) * 60
    brick = ((xx + off) % 120 < 3) | (yy % row_h < 3)
    stain = _noise(rng, H, W, 40)[:H, :W]
    base *= (0.8 + 0.4 * stain)[..., None]
    base[brick] *= 0.55
    # Стрельчатое окно с луной по центру (за головой модели — чуть выше центра).
    cx, cy, ww, wh = W * 0.5, H * 0.36, 230, 360
    inside = (np.abs(xx - cx) < ww / 2) & (yy > cy - wh * 0.15) & (yy < cy + wh * 0.6)
    arch = ((xx - cx) ** 2 / (ww / 2) ** 2 + (yy - (cy - wh * 0.15)) ** 2 / (wh * 0.45) ** 2 < 1) & (yy <= cy - wh * 0.15)
    win = inside | arch
    sky = np.array([40, 46, 96.0]) * (1 - (yy / H)[..., None]) + np.array([16, 18, 52.0]) * (yy / H)[..., None]
    moon = np.exp(-(((xx - cx - 40) ** 2 + (yy - cy + 40) ** 2) / (2 * 34 ** 2)))
    sky = sky + moon[..., None] * np.array([210, 215, 240.0])
    # Переплёт окна.
    mullion = win & ((np.abs(xx - cx) < 4) | (np.abs(yy - (cy + wh * 0.18)) < 4))
    frame = np.zeros((H, W), bool)
    edge_m = np.asarray(Image.fromarray((win * 255).astype(np.uint8)).filter(ImageFilter.MaxFilter(15))).astype(bool) & ~win
    frame |= edge_m
    candles = [(150, 600, 1.0), (240, 640, 0.8), (1060, 610, 0.95), (1150, 650, 0.75), (330, 670, 0.6), (960, 680, 0.6)]
    bok = [(rng.uniform(0, W), rng.uniform(H * 0.35, H), rng.uniform(10, 34), rng.uniform(0, 6.28), rng.uniform(0.3, 1)) for _ in range(26)]
    fog = _noise(rng, H, W * 2, 90)
    for i in range(n):
        ph = 2 * math.pi * i / n
        img = base.copy()
        img[win] = sky[win]
        img[mullion] = [14, 12, 22]
        img[frame] = [30, 24, 44]
        # Лунный свет на полу/стене — мягкий конус.
        cone = np.exp(-(((xx - cx) / 260) ** 2)) * np.clip((yy - cy) / H, 0, 1) * 30
        img += cone[..., None] * np.array([0.6, 0.7, 1.0])
        # Свечи: тёплый свет, пламя дрожит.
        warm = np.zeros((H, W))
        for j, (x0, y0, s) in enumerate(candles):
            fl = 1 + 0.12 * math.sin(ph * 3 + j * 1.7) + 0.07 * math.sin(ph * 7 + j)
            warm += s * fl * np.exp(-(((xx - x0) ** 2 + (yy - y0 + 40) ** 2) / (2 * (150 * s) ** 2)))
        img += warm[..., None] * np.array([120, 60, 20.0])
        pil = Image.fromarray(img.clip(0, 255).astype(np.uint8))
        d = ImageDraw.Draw(pil, "RGBA")
        for j, (x0, y0, s) in enumerate(candles):
            cw, ch = 26 * s, 90 * s
            d.rounded_rectangle((x0 - cw / 2, y0 - ch * 0.3, x0 + cw / 2, y0 + ch), 6, fill=(232, 220, 200, 255))
            d.rectangle((x0 - cw / 2, y0 - ch * 0.3, x0 - cw / 2 + cw * 0.3, y0 + ch), fill=(250, 244, 232, 255))
            sway = 3 * math.sin(ph * 5 + j * 2.1) * s
            fh = (34 + 6 * math.sin(ph * 6 + j)) * s
            d.ellipse((x0 - 9 * s + sway, y0 - ch * 0.3 - fh, x0 + 9 * s + sway, y0 - ch * 0.3 + 4), fill=(255, 190, 80, 235))
            d.ellipse((x0 - 4 * s + sway, y0 - ch * 0.3 - fh * 0.6, x0 + 4 * s + sway, y0 - ch * 0.3 + 2), fill=(255, 248, 210, 255))
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dl = ImageDraw.Draw(layer)
        for x0, y0, r, p0, a0 in bok:
            x = x0 + 18 * math.sin(ph + p0)
            y = y0 - 12 * math.sin(ph * 2 + p0)
            al = int(70 * a0 * (0.6 + 0.4 * math.sin(ph * 2 + p0 * 3)))
            dl.ellipse((x - r, y - r, x + r, y + r), fill=(255, 170, 90, al))
        layer = layer.filter(ImageFilter.GaussianBlur(6))
        pil = Image.alpha_composite(pil.convert("RGBA"), layer)
        # Туман у пола, медленно плывёт (по кругу: сдвиг на ширину кадра за цикл).
        sh = int(W * i / n)
        fg = fog[:H, sh:sh + W]
        fmask = (fg * np.clip((yy - H * 0.55) / (H * 0.45), 0, 1) * 110).clip(0, 255).astype(np.uint8)
        fogl = Image.new("RGBA", (W, H), (150, 140, 190, 0))
        fogl.putalpha(Image.fromarray(fmask))
        pil = Image.alpha_composite(pil, fogl).convert("RGB")
        # Виньетка.
        v = 1 - 0.45 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 2
        arr = np.asarray(pil).astype(float) * v[..., None]
        Image.fromarray(arr.clip(0, 255).astype(np.uint8)).save(out / f"f_{i + 1:03d}.jpg", quality=90)
    print("crypt ok")


def aurora(n: int = 48) -> None:
    out = MEDIA / "aurora"
    _clean(out, "*.jpg")
    rng = np.random.default_rng(7)
    yy, xx = np.mgrid[0:H, 0:W].astype(float)
    blobs = [((255, 150, 200), 0.3, 0.35, 260), ((120, 230, 210), 0.72, 0.4, 300), ((190, 160, 255), 0.5, 0.75, 320),
             ((255, 210, 150), 0.15, 0.8, 220)]
    stars = [(rng.uniform(0, W), rng.uniform(0, H), rng.uniform(1.5, 3.5), rng.uniform(0, 6.28)) for _ in range(40)]
    for i in range(n):
        ph = 2 * math.pi * i / n
        img = np.zeros((H, W, 3)) + np.array([26, 22, 48.0])
        for j, (c, bx, by, r) in enumerate(blobs):
            x = W * bx + 90 * math.sin(ph + j * 1.3)
            y = H * by + 50 * math.cos(ph + j * 0.9)
            img += np.exp(-(((xx - x) ** 2 + (yy - y) ** 2) / (2 * r ** 2)))[..., None] * np.array(c, float) * 0.32
        pil = Image.fromarray(img.clip(0, 255).astype(np.uint8)).convert("RGBA")
        lay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        for x, y, r, p0 in stars:
            al = int(200 * max(0.0, math.sin(ph * 2 + p0)))
            d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, al))
        pil = Image.alpha_composite(pil, lay.filter(ImageFilter.GaussianBlur(1))).convert("RGB")
        pil.save(out / f"f_{i + 1:03d}.jpg", quality=90)
    print("aurora ok")


# --------------------------------------------------------------------------- строка караоке

LYRICS = [  # (строка, начало заливки, конец заливки) — секунды от появления строки на эфире
    ("В темноте не страшно,", 0.5, 2.9),
    ("если ты поёшь со мной —", 3.2, 5.9),
    ("свети, свети, мой сайлиум!", 6.2, 9.2),
    ("пусть весь склеп горит со мной!", 9.6, 12.6),
]
LYR_DUR, LYR_FPS = 22.0, 10


def lyrics() -> None:
    out = MEDIA / "lyrics"
    _clean(out, "*.png")
    bw, bh = 1500, 330
    big = ImageFont.truetype(str(FONTS / "seguibl.ttf"), 92)
    small = ImageFont.truetype(str(FONTS / "segoeuib.ttf"), 52)
    tag = ImageFont.truetype(str(FONTS / "segoeuib.ttf"), 40)
    pink, white, grey = (255, 92, 176), (255, 255, 255), (170, 166, 196)
    for i in range(int(LYR_DUR * LYR_FPS)):
        t = i / LYR_FPS
        im = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        glow = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle((14, 14, bw - 14, bh - 14), 52, outline=(255, 80, 170, 200), width=10)
        im = Image.alpha_composite(im, glow.filter(ImageFilter.GaussianBlur(8)))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((20, 20, bw - 20, bh - 20), 46, fill=(12, 8, 24, 205), outline=(255, 120, 200, 230), width=4)
        d.text((70, 44), "КАРАОКЕ  ·  Песня из склепа", font=tag, fill=(255, 170, 215))
        cur = 0
        for k, (_, a, _b) in enumerate(LYRICS):
            if t >= a - 0.3:
                cur = k
        text, a, b = LYRICS[cur]
        nxt = LYRICS[cur + 1][0] if cur + 1 < len(LYRICS) else ""
        tw = d.textlength(text, font=big)
        x0, y0 = (bw - tw) / 2, 112
        frac = min(1.0, max(0.0, (t - a) / (b - a)))
        # Непропетое — белым с тёмной обводкой, пропетое — розовым (заливка слева направо).
        d.text((x0, y0), text, font=big, fill=white, stroke_width=6, stroke_fill=(30, 10, 40))
        if frac > 0:
            sung = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
            ImageDraw.Draw(sung).text((x0, y0), text, font=big, fill=pink, stroke_width=6, stroke_fill=(70, 0, 50))
            mask = Image.new("L", (bw, bh), 0)
            ImageDraw.Draw(mask).rectangle((0, 0, x0 + tw * frac, bh), fill=255)
            im.paste(sung, (0, 0), Image.composite(sung, Image.new("RGBA", (bw, bh)), mask).getchannel("A"))
        if nxt:
            nw = d.textlength(nxt, font=small)
            d.text(((bw - nw) / 2, 236), nxt, font=small, fill=grey)
        im.save(out / f"f_{i + 1:03d}.png")
    print("lyrics frames", int(LYR_DUR * LYR_FPS))


STEPS = {"saylium": saylium, "sticks": sticks, "crypt": crypt, "aurora": aurora, "lyrics": lyrics}

if __name__ == "__main__":
    for name in sys.argv[1:] or list(STEPS):
        STEPS[name]()
        print("ok", name)
