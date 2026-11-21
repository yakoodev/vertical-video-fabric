"""Раскладка split по правилам клипперов (findings.md п. 54–56) и автопроверки к ней.

* вебка сверху и неподвижна, рефрейм (камера, наезды) — только в зоне игры: ``check_split_layout``
  (cam_pos=top, ``stream_layout.camera_regions`` ведёт камеру только под швом);
* в зоне игры нет чёрных полос: ``black_bar_rows`` / ``check_no_black_bars`` по кадру выхода;
* центр лица в вебке на одной высоте: ``face_y_spread`` ≤ 3 % высоты кадра;
* цель рефрейма меняется не чаще раза в 1,5–2 с: ``hold_targets`` (гистерезис, встроен в autofocus) и
  ``target_switches`` (замер);
* подписи на шве и не пересекают бокс лица: ``seam_caption_y`` (позиция по умолчанию) и ``check_caption_box``.
"""

from __future__ import annotations

from typing import Any, Iterable

MIN_HOLD_SEC = 1.75       # между сменами цели рефрейма (правило 1,5–2 с)
SWITCH_DX = 0.08          # скачок x фокуса, который считается сменой цели (доля ширины)
FACE_Y_SPREAD_MAX = 0.03  # разброс центра лица по высоте кадра
BAR_LUMA = 24             # строка «чёрная», если ярче этого меньше BAR_BRIGHT_SHARE пикселей
BAR_BRIGHT_SHARE = 0.03
BAR_MAX_ROWS_SHARE = 0.01  # подряд чёрных строк у края зоны — не больше 1 % её высоты
CAPTION_HALF_H = 0.045    # половина высоты блока подписей (2 строки ~88 px на 1920) в долях кадра


# --------------------------------------------------------------------------- #
# Рефрейм: гистерезис смены цели
# --------------------------------------------------------------------------- #


def hold_targets(points: list[dict], min_hold: float = MIN_HOLD_SEC, dx: float = SWITCH_DX) -> list[dict]:
    """Смена цели (скачок x ≥ ``dx`` между соседними точками, не на склейке плана) не раньше ``min_hold`` после
    прошлой: ранний скачок держит прежнюю цель. Склейка (``cut``) — новый план, смена разрешена и сбрасывает отсчёт.
    Плавное ведение (шаги < dx) не трогается. Возвращает новые точки, исходные не меняет."""
    out: list[dict] = []
    last_switch = None
    for p in sorted((dict(p) for p in points or []), key=lambda p: float(p.get("t", 0))):
        t = float(p.get("t", 0))
        if out and not p.get("cut"):
            prev = float(out[-1]["x"])
            if abs(float(p["x"]) - prev) >= dx:
                if last_switch is not None and t - last_switch < min_hold:
                    p["x"] = prev
                else:
                    last_switch = t
        elif p.get("cut"):
            last_switch = t
        if last_switch is None:
            last_switch = t
        out.append(p)
    return out


def target_switches(points: list[dict], dx: float = SWITCH_DX) -> list[float]:
    """Моменты смены цели (скачки x ≥ dx не на склейке)."""
    pts = sorted(points or [], key=lambda p: float(p.get("t", 0)))
    return [float(b["t"]) for a, b in zip(pts, pts[1:]) if not b.get("cut") and abs(float(b["x"]) - float(a["x"])) >= dx]


def check_target_switches(points: list[dict], min_hold: float = 1.5) -> dict:
    """Замер: смен цели в минуту и самый короткий промежуток; ok — не чаще раза в ``min_hold`` с."""
    sw = target_switches(points)
    span = max((float(p["t"]) for p in points or []), default=0.0) - min((float(p["t"]) for p in points or []), default=0.0)
    gaps = [b - a for a, b in zip(sw, sw[1:])]
    shortest = min(gaps, default=None)
    ok = shortest is None or shortest >= min_hold - 1e-6
    return {"ok": ok, "switches": len(sw), "per_min": round(len(sw) * 60 / span, 2) if span > 0 else 0.0,
            "min_gap_sec": round(shortest, 2) if shortest is not None else None}


# --------------------------------------------------------------------------- #
# Геометрия split
# --------------------------------------------------------------------------- #


def seam_y(layout: dict, height: int = 1920, width: int = 1080) -> float:
    """y шва вебка/игра в долях кадра (середина зазора)."""
    from app.stream_layout import normalize_layout, split_geometry

    lay = normalize_layout(layout)
    cam_h, gap, game_h = split_geometry(lay, height, width)
    y = (cam_h + gap / 2) if lay["cam_pos"] != "bottom" else (game_h + gap / 2)
    return y / height


def seam_caption_y(layout: dict, height: int = 1920, width: int = 1080) -> float:
    """Позиция подписей по умолчанию для split: центр блока на шве."""
    return round(seam_y(layout, height, width), 4)


def check_split_layout(layout: dict, height: int = 1920, width: int = 1080) -> list[str]:
    """Вебка сверху и не двигается камерой; рефрейм — только в зоне игры."""
    from app.stream_layout import camera_regions, normalize_layout, split_geometry

    lay = normalize_layout(layout)
    problems = []
    if lay.get("mode") != "split":
        return ["раскладка не split"]
    if lay.get("cam_pos") != "top":
        problems.append("вебка не сверху (низ кадра в TikTok закрыт описанием)")
    cam_h, gap, _game = split_geometry(lay, height, width)
    regions = camera_regions(lay, width, height) or {}
    box = regions.get("box") or {"y": 0}
    if lay.get("cam_pos") == "top" and int(box.get("y", 0)) < cam_h + gap:
        problems.append("камера двигает и вебку: рефрейм должен быть только в зоне игры")
    return problems


def face_y_spread(face_boxes: Iterable[tuple[float, float, float, float]]) -> float | None:
    """Разброс (max − min) y центра лица по кадрам, в долях высоты кадра; лица — (x, y, w, h) в долях кадра."""
    ys = [float(y) + float(h) / 2 for x, y, w, h in face_boxes or []]
    return round(max(ys) - min(ys), 4) if ys else None


def check_face_height(face_boxes: list[tuple[float, float, float, float]], limit: float = FACE_Y_SPREAD_MAX) -> dict:
    spread = face_y_spread(face_boxes)
    return {"ok": spread is not None and spread <= limit + 1e-9, "spread": spread, "limit": limit,
            "frames": len(face_boxes or [])}


def cam_face_out_box(cam: dict, face: tuple[float, float, float, float], cam_h_frac: float) -> tuple[float, float, float, float]:
    """Бокс лица (доли исходника) → доли кадра выхода при вебке полосой сверху высотой ``cam_h_frac``
    (вебка растянута по ширине на всю полосу, как в split)."""
    fx, fy, fw, fh = face
    x = (fx - cam["x"]) / cam["w"]
    y = (fy - cam["y"]) / cam["h"] * cam_h_frac
    return (x, y, fw / cam["w"], fh / cam["h"] * cam_h_frac)


# --------------------------------------------------------------------------- #
# Кадр: чёрные полосы
# --------------------------------------------------------------------------- #


def black_bar_rows(gray: Any, y0: int, y1: int) -> tuple[int, int]:
    """Сколько подряд «чёрных» строк у верхнего и нижнего края зоны [y0, y1) кадра (numpy, оттенки серого)."""
    import numpy as np

    zone = np.asarray(gray)[y0:y1]
    if zone.size == 0:
        return 0, 0
    dark = (zone > BAR_LUMA).mean(axis=1) < BAR_BRIGHT_SHARE

    def run(mask: Any) -> int:
        n = 0
        for v in mask:
            if not v:
                break
            n += 1
        return n

    return run(dark), run(dark[::-1])


def check_no_black_bars(gray: Any, y0: int, y1: int) -> dict:
    top, bottom = black_bar_rows(gray, y0, y1)
    limit = max(2, int((y1 - y0) * BAR_MAX_ROWS_SHARE))
    return {"ok": top <= limit and bottom <= limit, "top_rows": top, "bottom_rows": bottom, "limit_rows": limit}


# --------------------------------------------------------------------------- #
# Подписи: на шве и не по лицу
# --------------------------------------------------------------------------- #


def _intersect(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return max(0.0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0.0, min(ay + ah, by + bh) - max(ay, by))


def caption_box(y: float, width: float = 0.84, half_h: float = CAPTION_HALF_H) -> tuple[float, float, float, float]:
    """Бокс блока подписей (доли кадра) по его центру ``y``."""
    return ((1 - width) / 2, y - half_h, width, 2 * half_h)


def check_caption_box(box: tuple[float, float, float, float], seam: float,
                      faces: Iterable[tuple[float, float, float, float]] = (),
                      tolerance: float = 0.06) -> dict:
    """Подпись на шве (центр блока не дальше ``tolerance`` от шва) и не пересекает ни одного бокса лица."""
    cy = box[1] + box[3] / 2
    hits = [round(_intersect(box, f), 5) for f in faces or []]
    problems = []
    if abs(cy - seam) > tolerance:
        problems.append(f"подпись {cy:.2f} далеко от шва {seam:.2f}")
    if any(h > 0 for h in hits):
        problems.append(f"подпись пересекает лицо (площадь {max(hits):.4f})")
    return {"ok": not problems, "problems": problems, "seam": round(seam, 4), "caption_y": round(cy, 4),
            "face_overlap": max(hits, default=0.0)}
