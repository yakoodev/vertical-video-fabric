"""Тест «2D-аниме из AI-картинок» для истории 2 Витубики «Без лица» (≈9.6 с, 1080×1920, 30 к/с).

    python tools/vtubika_ai2d.py [--src D:/vvf-offload/vtubika/2d/ai_src] [--out D:/vvf-offload/vtubika/2d]
        [--preview 0.5,4.2,8.8]   # только кадры PNG в OUT/preview, без видео

Картинки (нарисованы google/gemini-nano-banana-2.1, см. OUT/ai_spend.log): фон комнаты, стол с пустым
монитором, Тёма на зелёнке + правки той же картинки (глаза закрыты, зевок, улыбка), аватар + его зевок.
Оживление — tools/anime2d.py: ключ зелёнки, лицо из вариантов по растушёванным маскам (моргание, клевок
носом, зевок, улыбка), дыхание и голова через деформацию, камера с параллаксом и наездами, фон в DOF,
мерцание гирлянды, свет монитора на лице, пыль/боке; интерфейс эфира — tools/anime2d_ui.py.

Кадры:
1. 0–3.0 — Тёма устало смотрит в монитор, дышит, клюёт носом и вздрагивает.
2. 3.0–6.2 — крупнее: вдох, большой зевок; в углу карточка эфира — его аватар зевает следом.
3. 6.2–7.9 — монитор: чат «ОН ЗЕВАЕТ», «это я каждое утро», ЗРИТЕЛЕЙ 3 → 41;
   7.9–9.6 — Тёма улыбается.
Потом субтитры стилем историй (tools/vtubika_story.burn_captions) и музыка-подложка.
"""

from __future__ import annotations

import argparse
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tools import anime2d as A  # noqa: E402
from tools import anime2d_ui as U  # noqa: E402

W, H, FPS = 1080, 1920, 30
DUR = 9.6
S1, S2, S3A, S3B = (0.0, 3.0), (3.0, 6.2), (6.2, 7.9), (7.9, DUR)
WORLD = (1536, 2752)  # мир = пиксели 2K-картинок 9:16
BASE = W / WORLD[0]
SCREEN_NATIVE = (116, 534, 657, 844)  # экран монитора на 10_bg_desk.png (768×1376)
MUSIC = Path("D:/vvf-offload/vtubika/music/chill-keys.mp3")

CAPTIONS = [("Эфир.", 0.25, 0.85), ("Третий", 0.9, 1.3), ("час", 1.3, 1.65), ("ночи.", 1.65, 2.7),
            ("Ты", 3.25, 3.55), ("зеваешь", 3.55, 4.3), ("—", 4.3, 4.45), ("он", 4.45, 4.75),
            ("зевает.", 4.75, 5.9),
            ("Чат:", 6.3, 6.75), ("«ОН", 6.75, 7.05), ("ЗЕВАЕТ»,", 7.05, 7.75),
            ("«это", 7.95, 8.2), ("я", 8.2, 8.35), ("каждое", 8.35, 8.75), ("утро».", 8.75, 9.5)]
HIGHLIGHT = {"ночи.": "#ff9a3d", "зевает.": "#6fd3ff", "ЗЕВАЕТ»,": "#ffd35c", "утро».": "#ffd35c"}

CHAT = [U.Msg(0.0, "nochnoy_dozor", (126, 224, 255), "привет, тём"),
        U.Msg(0.0, "pelmen_404", (255, 211, 92), "го катку"),
        U.Msg(0.0, "kot_v_kepke", (255, 138, 216), "покажи лицо"),
        U.Msg(6.35, "pelmen_404", (255, 211, 92), "ОН ЗЕВАЕТ", True),
        U.Msg(6.9, "zhenya_ok", (46, 232, 111), "это я каждое утро", True),
        U.Msg(7.35, "kot_v_kepke", (255, 138, 216), "зевни ещё"),
        U.Msg(7.65, "nochnoy_dozor", (126, 224, 255), "ещё раз!!")]


def _log(msg: str) -> None:
    print(f"[ai2d {time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------- ассеты


class Assets:
    def __init__(self, src: Path) -> None:
        p = lambda n: str(src / n)  # noqa: E731
        # фон комнаты: половинное разрешение (он всё равно в расфокусе), гирлянда отдельно
        room = cv2.resize(A.load_rgb(p("02_bg_room.png")), (768, 1376), interpolation=cv2.INTER_AREA)
        self.room = A.blur(room, 3.0)
        self.lab, self.nbulbs, core = A.bulb_labels(room, reach=19)
        self.glow = (A.blur(core, 3.5) * 1.3 + A.blur(core, 10) * 0.9)[..., None] * np.float32([1.0, 0.6, 0.3])
        # Тёма: база и правки той же картинки, вырезанные с зелёнки
        self.tema = A.key_green(A.load_rgb(p("03_tema_base.png")))
        shape = self.tema.shape[:2]
        self.rig = A.FaceRig(self.tema, box=(420, 960, 1140, 1740))
        self.rig.add("smile", A.key_green(A.load_rgb(p("07_tema_smile.png"))),
                     A.ellipse_mask(shape, (760, 1290), (285, 250), 60))
        self.rig.add("yawn", A.key_green(A.load_rgb(p("06_tema_yawn2.png"))),
                     A.ellipse_mask(shape, (755, 1365), (305, 330), 70))
        self.rig.add("blink", A.key_green(A.load_rgb(p("04_tema_blink.png"))),
                     A.ellipse_mask(shape, (760, 1245), (260, 105), 45))
        self.body = A.Body(shape, pivot=(765, 1640), neck_y=1665, neck_soft=85, floor_y=2540)
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float32)
        self.light = (np.exp(-(((xx - 700) / 520) ** 2 + ((yy - 1300) / 700) ** 2)) * 0.8 + 0.2).astype(np.float32)
        # аватар (VTuber-модель Тёмы) и его зевок; моргание — глаза из зевка
        av = A.key_green(A.load_rgb(p("08_avatar.png")))
        avy = A.key_green(A.load_rgb(p("09_avatar_yawn.png")))
        self.arig = A.FaceRig(av, box=(250, 200, 780, 760))
        self.arig.add("yawn", avy, A.ellipse_mask((1024, 1024), (512, 470), (205, 220), 40))
        self.arig.add("blink", avy, A.ellipse_mask((1024, 1024), (512, 402), (180, 62), 26))
        # стол с монитором: фокус на экране, низ (стол, лапша) и края — в расфокусе
        desk = A.load_rgb(p("10_bg_desk.png"))
        dy = np.mgrid[0:desk.shape[0], 0:desk.shape[1]][0].astype(np.float32)
        far = np.clip((dy - 900) / 180, 0, 1) + np.clip((480 - dy) / 300, 0, 1) * 0.6
        self.desk = desk * (1 - far[..., None]) + A.blur(desk, 4.0) * far[..., None]
        # передний план кадров с Тёмой: край стола с подсветкой клавиатуры
        self.fg_desk = self._fg_desk()
        self.dust = A.Dust(W, H, n=44, seed=11)
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        r = np.sqrt(((xx - W / 2) / (W * 0.62)) ** 2 + ((yy - H / 2) / (H * 0.6)) ** 2)
        self.vignette = (1 - np.clip(r - 0.55, 0, 1) * 0.55)[..., None].astype(np.float32)
        rng = np.random.default_rng(5)
        self.grain = [(rng.standard_normal((H // 2, W // 2)).astype(np.float32) * 0.012) for _ in range(4)]

    @staticmethod
    def _fg_desk() -> np.ndarray:
        """Край стола у камеры (полуразрешение мира): тёмная столешница, сверху полоса света клавиатуры."""
        w, h = 1600, 420
        img = np.zeros((h, w, 4), np.float32)
        y = np.arange(h, dtype=np.float32)[:, None]
        top = 40
        a = np.clip((y - top) / 6, 0, 1)
        col = np.float32([0.035, 0.04, 0.07]) + np.clip(1 - (y - top) / 60, 0, 1)[..., None] * np.float32([0.10, 0.20, 0.28])
        img[..., :3] = col * a[..., None]
        img[..., 3] = a[..., 0] if a.ndim == 3 else a
        hue = np.linspace(0, 179, w, dtype=np.float32)
        rainbow = cv2.cvtColor(np.dstack([hue, np.full_like(hue, 200), np.full_like(hue, 255)]).astype(np.uint8),
                               cv2.COLOR_HSV2RGB)[0].astype(np.float32) / 255.0
        band = np.exp(-((y - top - 14) / 7) ** 2) * 0.9
        img[..., :3] += band[..., None] * rainbow[None, :, :] * a[..., None]
        return A.blur(img, 5.0)


# ---------------------------------------------------------------- игра Тёмы


def step(t: float, t0: float, t1: float) -> float:
    """1 на [t0, t1), иначе 0 — смена рисунка кадром, как в аниме (без наплыва)."""
    return 1.0 if t0 <= t < t1 else 0.0


def tema_state(t: float) -> dict:
    """Голова, дыхание, веса лица и свет монитора в момент t (общее время ролика)."""
    k = A.keys
    breath = 0.0065 * math.sin(2 * math.pi * t / 3.3)
    rot = 0.7 * A.noise1(t * 0.6, 1.0)
    dx = 3.0 * A.noise1(t * 0.5, 4.0)
    dy = 0.0
    w = {"smile": 0.0, "yawn": 0.0, "blink": 0.0}
    blinks = [0.55, 2.75, 6.0, 8.95]
    if t < S1[1]:  # клюёт носом и вздрагивает
        dy += k(t, [(1.35, 0), (2.2, 17), (2.3, 17), (2.4, -8), (2.65, 0)])
        rot += k(t, [(1.35, 0), (2.2, -1.6), (2.3, -1.6), (2.42, 0.6), (2.7, 0)])
        w["blink"] = step(t, 1.93, 2.33)
    elif t < S2[1]:  # вдох, зевок, выдох
        # рисованные смены без «двойных» полупрозрачных лиц: глаза закрываются → рот открыт → ...
        w["yawn"] = step(t, 3.6, 5.14)
        w["blink"] = max(step(t, 3.52, 3.6), step(t, 5.14, 5.4))
        rot += k(t, [(3.15, 0), (3.6, -2.4), (4.6, -3.6), (5.1, -3.0), (5.38, 0.9), (5.65, -0.3), (5.9, 0)])
        rot += 0.35 * math.sin(t * 21) * w["yawn"]
        dy += k(t, [(3.15, 0), (3.62, -18), (4.8, -24), (5.15, -12), (5.42, 7), (5.7, 0)])
        breath += k(t, [(3.15, 0), (3.62, 0.017), (5.0, 0.02), (5.4, -0.005), (5.85, 0)])
    else:  # улыбка
        w["smile"] = step(t, 8.08, 99)
        w["blink"] = step(t, 8.01, 8.08)
        rot += k(t, [(7.9, -0.4), (8.05, -0.4), (8.45, 2.0), (9.6, 1.4)])
        dy += k(t, [(8.0, 0), (8.18, -10), (8.5, -3)])
    w["blink"] = max(w["blink"], 1.0 if A.blink_weight(t, blinks) >= 0.5 else 0.0)
    flick = 1.0 + 0.035 * math.sin(t * 11.3) * math.sin(t * 3.7) + 0.02 * A.noise1(t * 4, 9)
    tint = np.float32([0.0, 0.0, 0.0])
    if S2[0] <= t < S2[1]:
        tint += np.float32([0.02, 0.07, 0.1]) * A.env(t, 3.12, 3.2, 3.25, 3.6)
    if t >= S3B[0]:
        tint += np.float32([0.10, 0.03, 0.07]) * A.smooth((t - 7.9) / 0.3) * (0.8 + 0.2 * math.sin(t * 7))
    # челюсть зевка: открывается за ~0.3 с, потом медленно «тянется» и слегка дрожит
    jaw = k(t, [(3.6, 0.8), (3.9, 1.0), (4.7, 1.07), (5.14, 0.9)]) + 0.012 * math.sin(t * 17)
    return {"rot": rot, "dx": dx, "dy": dy, "breath": breath, "w": w, "gain": flick, "tint": tint,
            "jaw": jaw}


def tema_camera(t: float) -> tuple[float, float, float]:
    k = A.keys
    n1, n2 = A.noise1(t * 0.45, 2.0), A.noise1(t * 0.38, 7.0)
    if t < S1[1]:
        return 768 + 6 * n1, k(t, [(0, 1380), (3, 1335)]) + 5 * n2, k(t, [(0, 1.0), (3, 1.085)])
    if t < S2[1]:
        return 760 + 5 * n1, k(t, [(3, 1300), (4.4, 1275), (6.2, 1285)]) + 4 * n2, \
            k(t, [(3.0, 1.30), (3.55, 1.31), (4.3, 1.39), (6.2, 1.41)])
    return 748 + 5 * n1, 1315 + 4 * n2, k(t, [(7.9, 1.16), (9.6, 1.25)])


# ---------------------------------------------------------------- кадр


def _place(m: np.ndarray, k: float, wx: float, wy: float) -> np.ndarray:
    """Матрица спрайта: (масштаб k, левый верх в точке мира wx, wy) ∘ камера."""
    out = m.copy()
    out[:, :2] = m[:, :2] * k
    out[:, 2] = m[:, :2] @ np.float32([wx, wy]) + m[:, 2]
    return out


def avatar_frame(a: Assets, t: float, w: int, h: int, rot: float, lag_state: dict, zoom: float = 1.0) -> np.ndarray:
    """Аватар в окне w×h: следит за головой Тёмы (с задержкой трекинга), моргает и зевает с ним."""
    weights = {"yawn": lag_state["w"]["yawn"], "blink": lag_state["w"]["blink"]}
    rgba = a.arig.compose(weights, {"yawn": (452, lag_state.get("jaw", 1.0))})
    s = h / 780 * zoom
    bob = 6 * math.sin(t * 2.1)
    m = A.affine(s, 512 - 1.5 * lag_state["dx"], 560 + 0.6 * lag_state["dy"] - bob, w, h,
                 rot * 1.3 + 1.2 * math.sin(t * 1.3))
    return A.warp_rgba(rgba, m, (w, h))


def render_tema(a: Assets, t: float) -> np.ndarray:
    cx, cy, z = tema_camera(t)
    st = tema_state(t)
    cam = A.Camera(W, H, BASE, (768, 1376))
    frame = A.warp(a.room, cam.matrix(cx, cy, z, 0.45, layer_scale=0.5), (W, H))
    tw = A.twinkle_layer(a.lab, a.nbulbs, t, a.glow)
    frame += A.warp(tw, cam.matrix(cx, cy, z, 0.45, layer_scale=0.5), (W, H)) * 0.8
    frame *= 0.92 + 0.08 * st["gain"]
    m = cam.matrix(cx, cy, z, 1.0)
    src = a.rig.compose(st["w"], {"yawn": (1425, st["jaw"])})
    mx, my = a.body.maps(m, (W, H), st["rot"], st["dx"], st["dy"], st["breath"], st["breath"] * 0.5)
    ch = A.remap_rgba(src, mx, my)
    lm = cv2.remap(a.light, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
    ch[..., :3] = ch[..., :3] * st["gain"] + st["tint"][None, None, :] * (lm * ch[..., 3])[..., None]
    shift = (-(cx - 768) * 0.4, -(cy - 1376) * 0.4)
    a.dust.draw(frame, t, shift, amount=0.0, bokeh_amount=0.9)  # боке гирлянды — за Тёмой
    A.over(frame, ch)
    mf = cam.matrix(cx, cy, z, 1.35)
    A.over(frame, A.warp_rgba(a.fg_desk, _place(mf, 2.0, -830, 2440), (W, H)))
    a.dust.draw(frame, t, (shift[0] * 1.5, shift[1] * 1.5), amount=1.0, bokeh_amount=0.0)
    if S2[0] <= t < S2[1]:
        _card(a, frame, t)
    viewers = "41" if t >= S3B[0] else "3"
    A.over_at(frame, U.hud("ЗРИТЕЛЕЙ", viewers, 30), 40, 110)
    return frame


def _card(a: Assets, frame: np.ndarray, t: float) -> None:
    k = A.clamp01((t - 3.12) / 0.38)
    if k <= 0:
        return
    card, slot = U.stream_card(400, 330, t, 3, "ночной стрим · Тёма")
    sw, sh = slot[2] - slot[0], slot[3] - slot[1]
    lag = tema_state(max(0.0, t - 0.1))
    av = avatar_frame(a, t, sw, sh, lag["rot"], lag)
    A.over(card[slot[1]:slot[3], slot[0]:slot[2]], av)
    s = A.ease_out_back(k, 1.8)
    if s < 0.999:
        nh, nw = max(2, int(card.shape[0] * s)), max(2, int(card.shape[1] * s))
        card = cv2.resize(card, (nw, nh), interpolation=cv2.INTER_LINEAR)
    cx, cy = 838, 318
    A.over_at(frame, card, int(cx - card.shape[1] / 2), int(cy - card.shape[0] / 2), min(1.0, k * 2.5))


def render_monitor(a: Assets, t: float) -> np.ndarray:
    k = A.keys
    sx0, sy0, sx1, sy1 = (v * 2 for v in SCREEN_NATIVE)
    cx, cy = (sx0 + sx1) / 2 + 6 * A.noise1(t * 0.5, 3.0), (sy0 + sy1) / 2 + 30 + 5 * A.noise1(t * 0.4, 5.0)
    z = k(t, [(6.2, 1.2), (6.75, 1.27), (7.9, 1.47)])
    cx += k(t, [(6.2, 0), (6.75, 20), (7.9, 165)])  # камера уходит к чату вслед за сообщениями
    cam = A.Camera(W, H, BASE, (768, 1376))
    frame = A.warp(a.desk, cam.matrix(cx, cy, z, 1.0, layer_scale=0.5), (W, H))
    viewers = U.viewers_value(t, 6.5, 7.55, 3, 41)
    ui, av = U.screen(t, sx1 - sx0, sy1 - sy0, CHAT, viewers, (6.55, 38), "ночной стрим · Тёма")
    lag = tema_state(t)
    lag["w"] = {"yawn": A.keys(t, [(6.2, 0.0)]), "blink": A.blink_weight(t, [6.75, 7.6])}
    lag["dx"], lag["dy"] = 0.0, 0.0
    aw, ah = av[2] - av[0], av[3] - av[1]
    A.over(ui[av[1]:av[3], av[0]:av[2]], avatar_frame(a, t, aw, ah, 0.8 * math.sin(t * 1.6), lag, zoom=1.08))
    mu = A.warp_rgba(ui, _place(cam.matrix(cx, cy, z, 1.0), 1.0, sx0, sy0), (W, H))
    small = cv2.resize(mu[..., :3], (W // 8, H // 8), interpolation=cv2.INTER_AREA)
    glow = cv2.resize(A.blur(small, 6), (W, H), interpolation=cv2.INTER_LINEAR)
    frame += glow * (0.55 + 0.1 * math.sin(t * 9))
    A.over(frame, mu)
    a.dust.draw(frame, t, (0, 0), amount=0.8, bokeh_amount=0.5)
    return frame


def render(a: Assets, t: float) -> np.ndarray:
    frame = render_monitor(a, t) if S3A[0] <= t < S3A[1] else render_tema(a, t)
    frame *= a.vignette
    g = a.grain[int(t * FPS) % len(a.grain)]
    frame += cv2.resize(g, (W, H), interpolation=cv2.INTER_NEAREST)[..., None]
    return np.clip(frame, 0, 1)


def to_u8(frame: np.ndarray) -> np.ndarray:
    return (frame * 255 + 0.5).astype(np.uint8)


# ---------------------------------------------------------------- сборка


def render_video(a: Assets, out: Path) -> float:
    n = int(round(DUR * FPS))
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    t0 = time.time()
    for i in range(n):
        proc.stdin.write(to_u8(render(a, i / FPS)).tobytes())
        if i % 30 == 0:
            _log(f"кадр {i}/{n}")
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg не собрал видео")
    return time.time() - t0


def captions(video: Path, out: Path, work: Path) -> None:
    from tools.vtubika_story import burn_captions

    words = [{"word": w, "start": s, "end": e} for w, s, e in CAPTIONS]
    burn_captions({"highlight": HIGHLIGHT}, words, video, out, work, DUR)


def add_music(video: Path, out: Path) -> None:
    if not MUSIC.is_file():
        shutil.copyfile(video, out)
        return
    af = f"volume=0.55,afade=t=in:d=0.4,afade=t=out:st={DUR - 1.2}:d=1.2,loudnorm=I=-18:TP=-1.5"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(MUSIC), "-filter_complex",
                    f"[1:a]{af}[a]", "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
                    "-t", f"{DUR}", str(out)], check=True)


def sheet(video: Path, out: Path, n: int = 12, cols: int = 4) -> None:
    """Лист n кадров с подписью времени (как check-листы историй)."""
    tiles = []
    for i in range(n):
        t = (i + 0.5) * DUR / n
        p = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
                            "-vf", "scale=360:640", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True, check=True).stdout
        img = np.frombuffer(p, np.uint8).reshape(640, 360, 3).copy()
        cv2.putText(img, f"{t:.2f}s", (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 5, cv2.LINE_AA)
        cv2.putText(img, f"{t:.2f}s", (10, 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2, cv2.LINE_AA)
        tiles.append(img)
    rows = [np.concatenate(tiles[r * cols:(r + 1) * cols], 1) for r in range(math.ceil(n / cols))]
    cv2.imwrite(str(out), cv2.cvtColor(np.concatenate(rows, 0), cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 88])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--src", default="D:/vvf-offload/vtubika/2d/ai_src")
    ap.add_argument("--out", default="D:/vvf-offload/vtubika/2d")
    ap.add_argument("--preview", default="", help="времена через запятую: только PNG-кадры")
    ap.add_argument("--still", type=float, default=8.9, help="время лучшего кадра для ai_still.png")
    args = ap.parse_args(argv)
    out = Path(args.out)
    work = out / "ai_work"
    work.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    a = Assets(Path(args.src))
    _log(f"ассеты {time.time() - t0:.1f} с")
    if args.preview:
        pdir = out / "preview"
        pdir.mkdir(exist_ok=True)
        for s in args.preview.split(","):
            t = float(s)
            cv2.imwrite(str(pdir / f"p_{t:05.2f}.png"), cv2.cvtColor(to_u8(render(a, t)), cv2.COLOR_RGB2BGR))
        _log(f"превью → {pdir}")
        return 0
    raw = work / "raw.mp4"
    secs = render_video(a, raw)
    _log(f"кадры отрисованы за {secs:.0f} с")
    capt = work / "caps.mp4"
    try:
        captions(raw, capt, work)
    except Exception as exc:  # субтитры не должны ронять тест — отчёт скажет
        _log(f"субтитры не наложены: {exc}")
        shutil.copyfile(raw, capt)
    final = out / "ai_test.mp4"
    add_music(capt, final)
    sheet(final, out / "ai_sheet.jpg")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{args.still:.3f}", "-i", str(final), "-frames:v", "1",
                    str(out / "ai_still.png")], check=True)
    _log(f"готово: {final} за {time.time() - t0:.0f} с всего")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
