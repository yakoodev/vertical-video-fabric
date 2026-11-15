"""Мастер-логотип «yakoodev» из старого логотипа владельца «YAKOO» (кисть, рога над YAK).

Буквы «dev» не рисуются шрифтом: они собраны из его же мазков, чтобы «yakoo» и «dev» были
одним словом одной кистью:

* d — чаша от первой «o» + вертикальный мазок (стойка K, очищенная от пересечения с рогом);
* e — вторая «o» с вырезом справа снизу и перекладиной из той же стойки, повёрнутой на 90°;
* v — два мазка стойки, уменьшенные до высоты строчных и наклонённые навстречу.

Источник — последний полный кадр пошаговой анимации After Effects (webm VP8 с альфой; альфу
отдаёт только декодер libvpx, родной vp8 её отбрасывает). Папку исходников не трогаем — читаем.

    .venv/Scripts/python tools/yakoodev_logo.py --webm "<…>/steps animation_AME/white.webm" \
        --frame 240 --out D:/vvf-offload/memes/yakoo_logo/yakoodev_master.png

Выход: PNG RGBA, белые буквы на прозрачном. Флаг ``--dev-tint`` слегка красит «dev».
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

# Где лежат буквы на кадре 1024×1024 (кадр 240 пошаговой анимации), px кадра.
# Обрезка мастера: всё, что непрозрачно, + поле.
K_STEM = {"x0": 378, "x1": 470, "y0": 255, "y1": 735, "cx": 420}
# Отсчёт координат ниже — в системе кадра (до обрезки).


def read_frame(webm: Path, frame: int) -> np.ndarray:
    """Кадр webm с альфой → RGBA uint8 (декодер libvpx: родной vp8 не читает альфу)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "f.png"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-c:v", "libvpx", "-i", str(webm), "-vf", f"select=eq(n\\,{frame}),format=rgba",
             "-frames:v", "1", str(out)],
            check=True,
        )
        img = cv2.imread(str(out), cv2.IMREAD_UNCHANGED)
    if img is None or img.shape[2] != 4:
        raise SystemExit(f"не прочитан кадр {frame} из {webm}")
    return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)


def components(alpha: np.ndarray, thr: int = 60) -> tuple[int, np.ndarray, np.ndarray]:
    n, labels, stats, _ = cv2.connectedComponentsWithStats((alpha > thr).astype(np.uint8), connectivity=8)
    return n, labels, stats


def letter_piece(alpha: np.ndarray, box: tuple[int, int, int, int], pad: int = 24) -> np.ndarray:
    """Альфа буквы: связные куски, чей центр лежит в рамке (с брызгами рядом), и мягкий край вокруг них."""
    x0, y0, x1, y1 = box
    n, labels, stats = components(alpha)
    keep = np.zeros(alpha.shape, bool)
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        cx, cy = x + w / 2, y + h / 2
        if x0 - pad <= cx <= x1 + pad and y0 - pad <= cy <= y1 + pad and x >= x0 - pad and x + w <= x1 + pad:
            keep |= labels == i
    # Сглаженный край куска — свой (альфа ниже порога у кромки тоже нужна).
    grow = cv2.dilate(keep.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    return np.where(grow, alpha, 0).astype(np.uint8)


def clean_stem(alpha: np.ndarray, spec: dict) -> np.ndarray:
    """Вертикальный мазок без пересечений: по строкам — отрезок, который содержит центр мазка;
    строки, где отрезок шире обычного (там его пересекает рог/перекладина), берут края у соседей."""
    x0, x1, y0, y1, cx = spec["x0"], spec["x1"], spec["y0"], spec["y1"], spec["cx"]
    sub = alpha[y0:y1, x0:x1].astype(np.float32)
    h, w = sub.shape
    edges: list[tuple[int, int] | None] = []
    c = cx - x0
    for y in range(h):
        row = sub[y] > 110
        if not row[max(0, min(w - 1, c))]:
            near = np.flatnonzero(row)
            if near.size == 0 or np.min(np.abs(near - c)) > 12:
                edges.append(None)
                continue
            c = int(near[np.argmin(np.abs(near - c))])
        left = c
        while left > 0 and row[left - 1]:
            left -= 1
        right = c
        while right < w - 1 and row[right + 1]:
            right += 1
        edges.append((left, right))
        c = (left + right) // 2
    widths = np.array([e[1] - e[0] for e in edges if e])
    med = float(np.median(widths)) if widths.size else 30.0
    good = [i for i, e in enumerate(edges) if e and (e[1] - e[0]) <= med * 1.35]
    out = np.zeros_like(sub)
    for y, e in enumerate(edges):
        if e is None:
            continue
        if (e[1] - e[0]) <= med * 1.35:
            # Обычная строка: край свой, с мягкой кромкой из исходной альфы.
            lo, hi = max(0, e[0] - 3), min(w, e[1] + 4)
            out[y, lo:hi] = sub[y, lo:hi]
            continue
        prev = max((i for i in good if i < y), default=None)
        nxt = min((i for i in good if i > y), default=None)
        if prev is None or nxt is None:
            continue
        k = (y - prev) / max(1, nxt - prev)
        left = edges[prev][0] * (1 - k) + edges[nxt][0] * k
        right = edges[prev][1] * (1 - k) + edges[nxt][1] * k
        xs = np.arange(w, dtype=np.float32)
        ramp = np.clip(np.minimum(xs - left + 1.5, right - xs + 1.5) / 1.5, 0, 1)
        out[y] = np.maximum(out[y], 255 * ramp)
    full = np.zeros_like(alpha)
    full[y0:y1, x0:x1] = out.astype(np.uint8)
    return full


def crop_alpha(a: np.ndarray, pad: int = 4) -> np.ndarray:
    ys, xs = np.nonzero(a > 8)
    return a[max(0, ys.min() - pad): ys.max() + pad + 1, max(0, xs.min() - pad): xs.max() + pad + 1]


def transform(piece: np.ndarray, sx: float, sy: float, angle: float) -> np.ndarray:
    """Масштаб и поворот куска (градусы, по часовой) с полем, чтобы углы не обрезались."""
    p = cv2.resize(piece, None, fx=sx, fy=sy, interpolation=cv2.INTER_AREA if sx * sy < 1 else cv2.INTER_CUBIC)
    h, w = p.shape
    diag = int(np.ceil(np.hypot(w, h))) + 8
    canvas = np.zeros((diag, diag), np.uint8)
    canvas[(diag - h) // 2:(diag - h) // 2 + h, (diag - w) // 2:(diag - w) // 2 + w] = p
    m = cv2.getRotationMatrix2D((diag / 2, diag / 2), -angle, 1.0)
    return crop_alpha(cv2.warpAffine(canvas, m, (diag, diag), flags=cv2.INTER_CUBIC), 2)


def paste(dst: np.ndarray, piece: np.ndarray, x: int, y: int) -> None:
    """Положить кусок левым верхним углом в (x, y): объединение по максимуму альфы — швов нет."""
    h, w = piece.shape
    dst[y:y + h, x:x + w] = np.maximum(dst[y:y + h, x:x + w], piece)


def rough_cut(piece: np.ndarray, cx: float, cy: float, a0: float, a1: float, seed: int = 7) -> np.ndarray:
    """Вырезать сектор углов [a0, a1] (градусы от +x по часовой) с рваным краем — проём буквы «e»."""
    h, w = piece.shape
    yy, xx = np.mgrid[0:h, 0:w]
    ang = np.degrees(np.arctan2(yy - cy, xx - cx))
    rng = np.random.default_rng(seed)
    r = np.hypot(yy - cy, xx - cx)
    # Край проёма «рвётся» с радиусом — как сухая кисть, а не ножницы.
    wob = np.interp(r, np.linspace(0, r.max() + 1, 24), rng.uniform(-7, 7, 24))
    cut = (ang > a0 + wob) & (ang < a1 + wob)
    out = piece.copy()
    out[cut] = 0
    return out


def build(frame_rgba: np.ndarray, dev_tint: str | None = None, mark: np.ndarray | None = None) -> tuple[np.ndarray, dict]:
    """Мастер RGBA и разметка (доли ширины мастера): где «dev» и где знак «×»."""
    alpha = frame_rgba[..., 3].copy()
    ys, xs = np.nonzero(alpha > 8)
    top, bottom = ys.min(), ys.max()
    # Буквы исходника по рамкам кадра 1024 (кадр 240): две «o» и стойка K.
    o1 = crop_alpha(letter_piece(alpha, (585, 375, 768, 655)))
    o2 = crop_alpha(letter_piece(alpha, (775, 360, 992, 645)))
    stem = crop_alpha(clean_stem(alpha, K_STEM))
    stem_h = stem.shape[0]

    right = xs.max()
    gap = 18
    o_top = 380  # верх строчных «o» в кадре
    word = np.zeros((1024, 1024 + 900), np.uint8)
    word[:, :1024] = alpha

    # d: чаша (первая «o») + стойка справа, высотой как у K, нижний хвост ниже строки — как у A/K.
    dx = right + gap
    paste(word, o1, dx, o_top)
    stem_d = transform(stem, 1.05, 1.0, 3)
    paste(word, stem_d, dx + o1.shape[1] - int(stem_d.shape[1] * 0.78), top + 18)
    d_right = dx + o1.shape[1] + int(stem_d.shape[1] * 0.2)

    # e: вторая «o» с проёмом справа снизу и перекладиной посередине.
    ex = d_right + gap + 6
    e_body = o2.copy()
    eh, ew = e_body.shape
    e_body = rough_cut(e_body, ew * 0.5, eh * 0.5, -4, 64)
    paste(word, e_body, ex, o_top - 8)
    # Перекладина — верхняя половина стойки (с её острым концом) на боку: срез прячется в левой
    # стенке «e», острый конец смотрит вправо, как росчерк кисти.
    bar_len = ew * 0.8
    bar = transform(stem[: int(stem_h * 0.5)], 0.56, bar_len / (stem_h * 0.5), 90 - 3)
    paste(word, bar, ex + int(ew * 0.15), o_top - 8 + int(eh * 0.5) - bar.shape[0] // 2)
    e_right = ex + ew

    # v: два мазка стойки высотой строчных, навстречу друг другу; нижние концы сходятся в одну точку
    # чуть ниже строки (как хвосты A и K).
    x_h = o1.shape[0] * 1.08
    v_l = transform(stem, 0.78, x_h / stem_h, -24)
    v_r = transform(stem, 0.7, x_h / stem_h * 0.96, 22)
    tip_x = e_right + gap + int(x_h * np.tan(np.radians(24)) * 0.95)
    tip_y = o_top + o1.shape[0] + 10
    for piece in (v_l, v_r):
        py, px = np.nonzero(piece > 128)
        low = py.max()
        bx = int(np.mean(px[py >= low - 3]))
        paste(word, piece, tip_x - bx, tip_y - low)
    # Знак «×» владельца (из его же анимации) — над стыком двух «o», как в финальном логотипе.
    mark_box = None
    if mark is not None:
        m = transform(mark, 0.8, 0.8, 0)
        mx = 772 - m.shape[1] // 2
        my = o_top - 14 - m.shape[0]
        paste(word, m, mx, my)
        mark_box = (mx, my, mx + m.shape[1], my + m.shape[0])
    ys2, xs2 = np.nonzero(word > 8)
    pad = 24
    y0, y1 = max(0, ys2.min() - pad), ys2.max() + pad + 1
    x0, x1 = max(0, xs2.min() - pad), xs2.max() + pad + 1
    a = word[y0:y1, x0:x1]
    rgba = np.zeros((*a.shape, 4), np.uint8)
    rgba[..., :3] = 255
    if dev_tint:
        tint = np.array([int(dev_tint.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)
        dev_from = right + gap // 2 - x0
        rgba[:, dev_from:, :3] = tint
    rgba[..., 3] = a
    _ = bottom
    W = float(a.shape[1])
    meta = {"dev_x": round((right + gap // 2 - x0) / W, 4)}
    if mark_box:
        meta["mark"] = [round((mark_box[0] - x0) / W, 4), round((mark_box[1] - y0) / W, 4),
                        round((mark_box[2] - x0) / W, 4), round((mark_box[3] - y0) / W, 4)]
    return rgba, meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--webm", required=True, type=Path, help="white.webm пошаговой анимации (VP8 с альфой)")
    ap.add_argument("--frame", type=int, default=240, help="номер полного кадра (по умолчанию 240)")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--yakoo-out", type=Path, help="заодно сохранить чистый «YAKOO» (мастер владельца)")
    ap.add_argument("--dev-tint", default=None, help="слегка покрасить «dev», #rrggbb")
    ap.add_argument("--mark-frame", type=int, default=228, help="кадр, где дорисован знак «×» (−1 — без знака)")
    args = ap.parse_args(argv)
    frame = read_frame(args.webm, args.frame)
    if args.yakoo_out:
        a = frame[..., 3]
        ys, xs = np.nonzero(a > 8)
        crop = frame[ys.min() - 24: ys.max() + 25, xs.min() - 24: xs.max() + 25].copy()
        crop[..., :3] = 255
        cv2.imwrite(str(args.yakoo_out), cv2.cvtColor(crop, cv2.COLOR_RGBA2BGRA))
    mark = None
    if args.mark_frame >= 0:
        a = read_frame(args.webm, args.mark_frame)[..., 3]
        # Знак — крупные куски над строчными буквами справа от K (рамка кадра 1024).
        n, labels, stats = components(a)
        keep = np.zeros(a.shape, bool)
        for i in range(1, n):
            x, y, w, h, area = stats[i]
            if area > 200 and x >= 650 and y + h <= 330:
                keep |= labels == i
        if keep.any():
            grow = cv2.dilate(keep.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
            mark = crop_alpha(np.where(grow, a, 0).astype(np.uint8))
    rgba, meta = build(frame, args.dev_tint, mark)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.out), cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA))
    args.out.with_suffix(".json").write_text(json.dumps(meta), "utf-8")
    print(f"{args.out} {rgba.shape[1]}x{rgba.shape[0]} {json.dumps(meta)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
