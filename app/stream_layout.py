"""Раскладки стрима для 9:16: вебка сверху + игра снизу и т.п.

Самый частый формат стрим-шортсов — «split»: лицо стримера (вебка) сверху,
геймплей снизу. Плюс «pip_cam»: обычный кадр 9:16 и вебка поверх окошком.

``render.layout`` клипа::

    {"mode": "single" | "split" | "pip_cam" | "meme",
     "cam": {"x", "y", "w", "h"},   # прямоугольник вебки в долях ИСХОДНИКА (весь кадр)
     "ratio": 0.38,                  # split: доля высоты кадра под вебку, 0.2..0.6
     "cam_pos": "top" | "bottom",    # split: где вебка
     "pip": {"x", "y", "w"},         # pip_cam: центр и ширина окошка в долях выхода
     "border": {"color": "#000000", "width": 0},
     "gap": 0,
     "video_y": 0.04,                 # meme: верх видео в долях высоты выхода
     "video_scale": 1.0,              # meme: ширина видео в ширинах кадра (>1 — крупнее, края срезаны)
     "bg": {"kind": "aurora", ...},   # meme / avatar_full: фон (app/backgrounds.py), анимированный
     "avatar_y": 0.5,                 # avatar_full: центр аватара по высоте выхода
     "bg_blur": true,                 # avatar_full: фон — размытый тот же аватар (false — bg)
     "cams": [{"from", "to", "cam", "video_scale"?}]}  # avatar_full: рамки по отрезкам исходника (с)

«avatar_full» — история витубера «только аватар»: прямоугольник аватара/вебки ``cam`` (доли
ВСЕГО кадра исходника, как у split) на весь кадр — шириной ``video_scale`` ширин кадра (1 —
во всю ширину, больше — крупнее, края срезаются), центр по высоте ``avatar_y``; вокруг —
размытый тот же аватар (``bg_blur``) или фон ``bg`` (тот же, что у set_backdrop: анимированный
рисует компоновщик). Игры, браузера и роликов с экрана в кадре нет (QA 01.11, клип 889:
кадрирование окном 9:16 по исходнику поймало человека из ютуба, аватара в кадре не было).

«meme» — мем-формат: горизонтальное видео целиком во всю ширину сверху, ниже —
фон под скриншоты и титры (как «Борат × сайт»): ffmpeg заливает его первым цветом,
анимированный фон поверх рисует компоновщик моушна. Раньше агент собирал
это из split с «вебкой» на весь кадр и закрывал низ прямоугольником.

``gap`` и ``border.width`` — пиксели кадра 1080×1920; на другом размере
выхода масштабируются пропорционально высоте (превью считает так же, см.
frontend/src/pages/workspace/layout.ts). В кадре другого формата (1:1, 4:5, 16:9 —
app/formats.py) — по короткой стороне, как px моушн-слоёв: рамка 6 px остаётся 6 px
(``frame_px_scale``). ``single`` — ровно прежний рендер.

Здесь только чистая геометрия и автопоиск вебки; граф ffmpeg собирает
``app.render._layout_filter``.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from app.backgrounds import normalize_background

LAYOUT_MODES = ("single", "split", "pip_cam", "meme", "avatar_full")
AVATAR_MODE = "avatar_full"
AVATAR_Y_RANGE = (0.2, 0.8)
AVATAR_MAX_AUTO_SCALE = 1.6  # авто-крупность аватара: не больше — иначе модель режется по бокам
CAM_POSITIONS = ("top", "bottom")
REFERENCE_HEIGHT = 1920

DEFAULT_CAM = {"x": 0.70, "y": 0.02, "w": 0.28, "h": 0.30}
DEFAULT_PIP = {"x": 0.5, "y": 0.12, "w": 0.42}
DEFAULT_RATIO = 0.38
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _num(value: object, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return min(hi, max(lo, number))


def normalize_cam(raw: object, default: dict | None = DEFAULT_CAM) -> dict | None:
    """Прямоугольник вебки в долях кадра исходника, целиком внутри кадра."""
    if not isinstance(raw, dict):
        return dict(default) if default else None
    base = default or DEFAULT_CAM
    w = _num(raw.get("w"), base["w"], 0.03, 1.0)
    h = _num(raw.get("h"), base["h"], 0.03, 1.0)
    x = _num(raw.get("x"), base["x"], 0.0, 1.0 - w)
    y = _num(raw.get("y"), base["y"], 0.0, 1.0 - h)
    return {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}


MAX_AVATAR_CAMS = 8


def normalize_avatar_cams(raw: object) -> list[dict]:
    """avatar_full: рамки аватара по отрезкам ИСХОДНИКА — ``[{from, to, cam, video_scale?}]``.

    QA 02.11 (клип 892): до 281 с исходника аватар крупно левее, потом — в углу справа; одна
    рамка ``cam`` на весь клип дала 4 с волос и юбки без лица. Отрезок — секунды исходника
    (не шкала клипа: переживает вырезы, подрезку и разрезание кусков); вне отрезков — ``cam``.
    По порядку, без наложений (наложение обрезается по началу следующего), не больше 8."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            a, b = float(item.get("from")), float(item.get("to"))
        except (TypeError, ValueError):
            continue
        if not (a == a and b == b) or b - a < 0.05:  # NaN или пустой отрезок
            continue
        box = item.get("cam") if isinstance(item.get("cam"), dict) else item
        if box.get("w") is None or box.get("h") is None:
            continue
        cam = normalize_cam(box, None)
        entry = {"from": round(max(0.0, a), 3), "to": round(b, 3), "cam": cam}
        if item.get("video_scale") is not None:
            entry["video_scale"] = round(_num(item.get("video_scale"), 1.0, 1.0, 4.0), 4)
        try:
            freeze = float(item.get("freeze_from")) if item.get("freeze_from") is not None else None
        except (TypeError, ValueError):
            freeze = None
        if freeze is not None and freeze == freeze and entry["from"] - FREEZE_MAX_SEC <= freeze < entry["from"] - 0.01:
            entry["freeze_from"] = round(max(0.0, freeze), 3)
        out.append(entry)
    out.sort(key=lambda e: e["from"])
    clean: list[dict] = []
    for entry in out:
        if clean and entry["from"] < clean[-1]["to"]:
            clean[-1]["to"] = entry["from"]
            if clean[-1]["to"] - clean[-1]["from"] < 0.05:
                clean.pop()
        if entry.get("freeze_from") is not None and clean and entry["freeze_from"] <= clean[-1]["from"]:
            entry.pop("freeze_from")  # стоп-кадр длиннее прежнего места — не трогаем
        clean.append(entry)
    return clean[:MAX_AVATAR_CAMS]


FREEZE_MAX_SEC = 1.5


def avatar_freezes(layout: dict, start: float, end: float) -> list[dict]:
    """Стоп-кадры на наплывах смены сцены в части исходника ``start..end``: ``[{t0, t1}]`` — секунды ОТ НАЧАЛА
    части, где вместо кадров исходника стоит последний кадр до ``t0`` (``layout.cams[].freeze_from`` .. ``from``).

    QA 5 (клип 897): ≈0.2 с полупрозрачного ютуба на смене сцены — ни одного кадра с чужой сценой. Стоп-кадр
    без кадра до него (часть начинается внутри наплыва) не ставится."""
    out: list[dict] = []
    for entry in normalize_avatar_cams(layout.get("cams")):
        freeze = entry.get("freeze_from")
        if freeze is None:
            continue
        a, b = float(freeze), min(float(entry["from"]), float(end))
        if a - start < 0.04 or b <= a or a >= end:
            continue
        out.append({"t0": round(a - start, 3), "t1": round(b - start, 3)})
    return out


def avatar_cam_spans(layout: dict, start: float, end: float) -> tuple[dict, list[dict]]:
    """Рамки аватара на части исходника ``start..end`` (секунды исходника).

    → (``{cam, video_scale}`` — рамка, что занимает большую часть отрезка: ею собирается основа
    части; ``[{t0, t1, cam, video_scale}]`` — где нужна другая рамка, секунды ОТ НАЧАЛА части).
    Без ``cams`` — рамка раскладки и пустой список (рендер ровно прежний)."""
    base = {"cam": layout.get("cam") or dict(DEFAULT_CAM), "video_scale": float(layout.get("video_scale") or 1.0)}
    cams = normalize_avatar_cams(layout.get("cams"))
    if not cams or end <= start:
        return base, []
    pieces: list[tuple[float, float, dict]] = []  # (t0, t1, рамка) по всей части, дыры — рамкой раскладки
    cursor = start
    for entry in cams:
        a, b = max(start, entry["from"]), min(end, entry["to"])
        if b <= a:
            continue
        if a > cursor:
            pieces.append((cursor, a, base))
        pieces.append((a, b, {"cam": entry["cam"], "video_scale": float(entry.get("video_scale") or base["video_scale"])}))
        cursor = b
    if cursor < end:
        pieces.append((cursor, end, base))
    if not pieces:
        return base, []
    length: dict[str, float] = {}
    for a, b, look in pieces:
        key = repr(sorted(look["cam"].items())) + f"{look['video_scale']:.4f}"
        length[key] = length.get(key, 0.0) + (b - a)
    main_key = max(length, key=length.get)
    main = next(look for _a, _b, look in pieces
                if repr(sorted(look["cam"].items())) + f"{look['video_scale']:.4f}" == main_key)
    spans = [{"t0": round(a - start, 3), "t1": round(b - start, 3), "cam": look["cam"], "video_scale": look["video_scale"]}
             for a, b, look in pieces if look is not main and (look["cam"] != main["cam"]
                                                                or look["video_scale"] != main["video_scale"])]
    return main, spans


def normalize_layout(raw: object) -> dict:
    """Привести что угодно к полной форме раскладки (по умолчанию — single)."""
    src = raw if isinstance(raw, dict) else {}
    mode = src.get("mode") if src.get("mode") in LAYOUT_MODES else "single"
    pip_raw = src.get("pip") if isinstance(src.get("pip"), dict) else {}
    border_raw = src.get("border") if isinstance(src.get("border"), dict) else {}
    color = border_raw.get("color")
    cams = normalize_avatar_cams(src.get("cams")) if mode == AVATAR_MODE else []
    return {
        "mode": mode,
        "cam": normalize_cam(src.get("cam")),
        "ratio": round(_num(src.get("ratio"), DEFAULT_RATIO, 0.2, 0.6), 4),
        "cam_pos": src.get("cam_pos") if src.get("cam_pos") in CAM_POSITIONS else "top",
        "pip": {
            "x": round(_num(pip_raw.get("x"), DEFAULT_PIP["x"], 0.0, 1.0), 4),
            "y": round(_num(pip_raw.get("y"), DEFAULT_PIP["y"], 0.0, 1.0), 4),
            "w": round(_num(pip_raw.get("w"), DEFAULT_PIP["w"], 0.1, 1.0), 4),
        },
        "border": {
            "color": color.lower() if isinstance(color, str) and _COLOR_RE.match(color) else "#000000",
            "width": int(round(_num(border_raw.get("width"), 0, 0, 40))),
        },
        "gap": int(round(_num(src.get("gap"), 0, 0, 200))),
        "video_y": round(_num(src.get("video_y"), 0.04, 0.0, 0.6), 4),
        "video_scale": round(_num(src.get("video_scale"), 1.0, 1.0, 4.0), 4),
        # Фон meme (app/backgrounds.py): по умолчанию анимированный; старый "#rrggbb" — заливка.
        "bg": normalize_background(src.get("bg")),
    } | ({"avatar_y": round(_num(src.get("avatar_y"), 0.5, *AVATAR_Y_RANGE), 4),
          "bg_blur": src.get("bg_blur") is not False} if mode == AVATAR_MODE else {}) | (
        {"cams": cams} if cams else {})


def layout_active(layout: object) -> bool:
    """Раскладка что-то меняет (не single)? Только тогда рендер идёт по новому пути."""
    return isinstance(layout, dict) and layout.get("mode") in ("split", "pip_cam", "meme", AVATAR_MODE)


def cam_aspect(cam: dict, src_w: float | None, src_h: float | None) -> float:
    """Ширина/высота прямоугольника ``cam`` (доли исходника) в пикселях исходника (нет размера — 16:9)."""
    return (float(cam["w"]) * float(src_w or 16)) / max(1e-6, float(cam["h"]) * float(src_h or 9))


def avatar_geometry(layout: dict, width: int, height: int, src_w: float | None = None,
                    src_h: float | None = None) -> dict:
    """avatar_full: где аватар на выходе ``width``×``height`` — ``{x, y, w, h}`` в px (может выходить
    за кадр: края срезаются). Как ``render._layout_filter``: ширина ``even(width·video_scale)``,
    высота по пропорции ``cam`` (чётная), по центру по ширине, центр по высоте — ``avatar_y``."""
    layout = normalize_layout(layout)
    vw = even_px(width * float(layout.get("video_scale") or 1.0))
    vh = even_px(vw / max(1e-6, cam_aspect(layout["cam"], src_w, src_h)))
    x = (width - vw) // 2
    y = int(float(layout.get("avatar_y", 0.5)) * height - vh / 2)
    return {"x": x, "y": y, "w": vw, "h": vh}


def avatar_auto_scale(cam: dict, width: int, height: int, src_w: float | None, src_h: float | None) -> float:
    """Крупность аватара по умолчанию: заполнить высоту кадра, но не шире AVATAR_MAX_AUTO_SCALE."""
    cover = (height / width) * cam_aspect(cam, src_w, src_h)  # ширин кадра, чтобы высота = кадр
    return round(min(AVATAR_MAX_AUTO_SCALE, max(1.0, cover)), 3)


def avatar_visible_rect(layout: dict, width: int, height: int, src_w: float | None = None,
                        src_h: float | None = None) -> dict:
    """avatar_full: какая часть ИСХОДНИКА (доли, ``{x0, y0, x1, y1}``) видна в кадре ``width``×``height``.

    Рамка ``cam`` ставится шириной ``video_scale`` ширин кадра по центру — при крупности > 1 её бока
    срезаются краями кадра (QA 5, клип 897: рамка с лицом у правого края, крупность 1.5 — правый глаз
    за кадром)."""
    lay = {**layout, "mode": AVATAR_MODE}
    geo = avatar_geometry(lay, width, height, src_w, src_h)
    cam = normalize_layout(lay)["cam"]
    vx0, vx1 = max(0, geo["x"]), min(width, geo["x"] + geo["w"])
    vy0, vy1 = max(0, geo["y"]), min(height, geo["y"] + geo["h"])
    fx = lambda px: float(cam["x"]) + (px - geo["x"]) / max(1e-6, geo["w"]) * float(cam["w"])  # noqa: E731
    fy = lambda py: float(cam["y"]) + (py - geo["y"]) / max(1e-6, geo["h"]) * float(cam["h"])  # noqa: E731
    return {"x0": fx(vx0), "y0": fy(vy0), "x1": fx(vx1), "y1": fy(vy1)}


def _covers(outer: dict, inner: dict, eps: float = 2e-3) -> bool:
    return (outer["x0"] <= inner["x0"] + eps and outer["y0"] <= inner["y0"] + eps
            and outer["x1"] >= inner["x1"] - eps and outer["y1"] >= inner["y1"] - eps)


def face_cut_sides(visible: dict, face: dict, eps: float = 2e-3) -> list[str]:
    """Стороны, где ``face`` (доли исходника) выходит за видимую часть ``visible``: left/right/top/bottom."""
    return [name for name, cut in (("left", face["x0"] < visible["x0"] - eps), ("right", face["x1"] > visible["x1"] + eps),
                                   ("top", face["y0"] < visible["y0"] - eps),
                                   ("bottom", face["y1"] > visible["y1"] + eps)) if cut]


def fit_avatar_face(cam: dict, zone: dict, video_scale: float, width: int, height: int, src_w: float | None,
                    src_h: float | None, avatar_y: float = 0.5, auto: bool = True) -> tuple[dict, float, list[str]]:
    """Рамка и крупность аватара, при которых лицо с запасом ``zone`` (доли исходника, ``avatar_box.face_zone``)
    целиком в кадре. → (cam, video_scale, что поправлено: ``widened`` / ``centered`` / ``scaled``).

    QA 5 (клип 897): рамку сузили ради чата, а крупность 1.5 срезала её бока по центру — лицо у края рамки
    ушло за кадр. Правило: (1) рамка содержит зону лица (расширяется до неё); (2) зона не видна при этой
    крупности — либо крупность меньше, либо рамка по центру лица (дальний от лица бок срезается) с крупностью
    ``auto`` (по умолчанию для новой рамки) или прежней; из двух берётся то, где лицо в кадре крупнее."""
    notes: list[str] = []
    x0, y0 = float(cam["x"]), float(cam["y"])
    x1, y1 = x0 + float(cam["w"]), y0 + float(cam["h"])
    zx0, zy0, zx1, zy1 = (max(0.0, min(1.0, float(zone[k]))) for k in ("x0", "y0", "x1", "y1"))
    nx0, ny0, nx1, ny1 = min(x0, zx0), min(y0, zy0), max(x1, zx1), max(y1, zy1)
    if (nx0, ny0, nx1, ny1) != (x0, y0, x1, y1):
        notes.append("widened")
    zone = {"x0": zx0, "y0": zy0, "x1": zx1, "y1": zy1}

    def box(a: float, b: float, c: float, d: float) -> dict:
        return {"x": round(a, 4), "y": round(b, 4), "w": round(c - a, 4), "h": round(d - b, 4)}

    def visible_ok(c: dict, s: float) -> bool:
        lay = {"mode": AVATAR_MODE, "cam": c, "video_scale": s, "avatar_y": avatar_y}
        return _covers(avatar_visible_rect(lay, width, height, src_w, src_h), zone)

    def best_scale(c: dict, target: float) -> float:
        target = max(1.0, float(target))
        if visible_ok(c, target):
            return round(target, 4)
        lo, hi = 1.0, target
        if not visible_ok(c, lo):
            return 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if visible_ok(c, mid) else (lo, mid)
        return round(max(1.0, lo - 1e-3), 4)

    wide = box(nx0, ny0, nx1, ny1)
    scale = float(video_scale or 1.0)
    if visible_ok(wide, scale):
        return wide, scale, notes
    options = [(wide, best_scale(wide, scale), [*notes, "scaled"])]
    zc = (zx0 + zx1) / 2
    cx = (nx0 + nx1) / 2
    if abs(cx - zc) > 1e-3:
        cx0, cx1 = (max(nx0, 2 * zc - nx1), nx1) if cx < zc else (nx0, min(nx1, 2 * zc - nx0))
        centered = box(cx0, ny0, cx1, ny1)
        target = avatar_auto_scale(centered, width, height, src_w, src_h) if auto else scale
        s = best_scale(centered, target)
        options.append((centered, s, [*notes, "centered"] + (["scaled"] if s < target - 1e-3 else [])))
    # Лицо в кадре крупнее — лучше: ширина рамки на выходе (scale·width) на долю лица в рамке.
    cam_out, scale_out, how = max(options, key=lambda o: o[1] / max(1e-6, o[0]["w"]))
    return cam_out, scale_out, how


def even_px(value: float) -> int:
    return max(2, int(round(value / 2.0)) * 2)


# Кадр выше этого отношения высоты к ширине — вертикаль (9:16): px считаются от высоты, как всегда.
VERTICAL_MIN_RATIO = 1.7


def frame_px_scale(width: int | float | None, height: int | float) -> float:
    """Множитель px кадра 1080×1920 → px выхода ``width``×``height``.

    Вертикаль (и ширина не задана) — высота / 1920, как всегда. Квадрат, 4:5 и 16:9 — по
    короткой стороне / 1080, как px моушн-слоёв: от высоты 1080 у 16:9 рамка 6 px стала бы 4."""
    h = float(height or 0)
    w = float(width or 0)
    if w <= 0 or h <= 0 or h >= w * VERTICAL_MIN_RATIO:
        return h / REFERENCE_HEIGHT
    return min(w, h) / 1080


def scaled_px(value: float, height: int, width: int | None = None) -> int:
    """Пиксели кадра 1080×1920 → пиксели выхода высотой ``height`` (чётные; 0 остаётся 0).
    ``width`` — ширина выхода: у не вертикального кадра — по короткой стороне (``frame_px_scale``)."""
    if value <= 0:
        return 0
    if width is None:
        return even_px(value * height / REFERENCE_HEIGHT)
    return even_px(value * frame_px_scale(width, height))


def split_geometry(layout: dict, height: int, width: int | None = None) -> tuple[int, int, int]:
    """(высота вебки, высота зазора, высота геймплея) в пикселях выхода; сумма = height."""
    cam_h = even_px(float(layout["ratio"]) * height)
    gap = scaled_px(float(layout.get("gap") or 0), height, width)
    gap = min(gap, max(0, height - cam_h - 2))
    return cam_h, gap, height - cam_h - gap


def gameplay_height(layout: object, width: int, height: int) -> int:
    """Высота окна геймплея: в split она меньше кадра, иначе — весь кадр."""
    if isinstance(layout, dict) and layout.get("mode") == "split":
        return split_geometry(layout, height, width)[2]
    return height


def camera_regions(
    layout: object, width: int, height: int, src_w: float | None = None, src_h: float | None = None,
    mirror: bool = False,
) -> dict | None:
    """Где камера (наезды) двигает кадр при раскладке стрима — в пикселях выхода.

    Зум камеры увеличивал весь собранный кадр: окошко вебки/килфида росло вместе с
    игрой и уезжало за край. Компоновщик (node/motion-render.ts) рисует камеру только
    внутри ``box`` (окно игры), а прямоугольники ``pinned`` (окошко pip) кладёт поверх
    из незумленного кадра. ``{"box": {x,y,w,h}, "pinned": [{x,y,w,h}]}`` или None
    (single/meme — камера двигает весь кадр, как раньше)."""
    if not isinstance(layout, dict) or layout.get("mode") not in ("split", "pip_cam"):
        return None
    layout = normalize_layout(layout)
    if layout["mode"] == "split":
        cam_h, gap, game_h = split_geometry(layout, height, width)
        top = layout["cam_pos"] != "bottom"
        return {"box": {"x": 0, "y": cam_h + gap if top else 0, "w": int(width), "h": game_h}, "pinned": []}
    cam, pip = layout["cam"], layout["pip"]
    border = scaled_px(float(layout["border"]["width"]), height, width)
    box_w = min(int(width), even_px(float(pip["w"]) * width))
    inner = max(2, box_w - 2 * border)
    aspect = (cam["w"] * float(src_w or 16)) / max(1e-6, cam["h"] * float(src_h or 9))
    w = inner + 2 * border
    h = min(int(height), even_px(inner / max(1e-6, aspect)) + 2 * border)
    x = min(max(0.0, float(pip["x"]) * width - w / 2), width - w)
    y = min(max(0.0, float(pip["y"]) * height - h / 2), height - h)
    if mirror:  # зеркало переворачивает уже собранный кадр — окошко тоже
        x = width - x - w
    pad = 1  # пиксель запаса: по краю окошка не мелькает зумленная игра
    rect = {"x": max(0, int(x) - pad), "y": max(0, int(y) - pad)}
    rect["w"] = min(int(width) - rect["x"], int(w) + 2 * pad)
    rect["h"] = min(int(height) - rect["y"], int(h) + 2 * pad)
    return {"box": {"x": 0, "y": 0, "w": int(width), "h": int(height)}, "pinned": [rect]}


# Горизонтальный кадр (16:9): вебка — окошком в углу. Ширина окошка из split — доля ширины
# кадра; поле от краёв — доля высоты (то же число px по обеим осям).
LANDSCAPE_PIP_W = 0.26
LANDSCAPE_PIP_MARGIN = 0.035


def layout_for_format(layout: object, fmt: object, source: dict | None = None) -> dict | None:
    """Раскладка стрима для формата вывода (app/formats.py). У вертикали, квадрата и 4:5 —
    как есть (геометрия считается от размера кадра). В горизонтальном кадре (16:9):

    * ``split`` (вебка сверху/снизу) → ``pip_cam``: игра на весь кадр, вебка окошком в
      правом верхнем (``cam_pos=top``) или правом нижнем (``bottom``) углу;
    * ``pip_cam`` → то же окошко в ближайшем углу (по ``pip.x``/``pip.y``), той же ширины
      в px, что и в вертикали (доля × 1080 / 1920);
    * ``meme`` (видео сверху, фон снизу) → обычный кадр: горизонтальное видео и так
      занимает весь кадр (None);
    * ``single`` — как есть."""
    from app.formats import frame_size, is_landscape

    if not layout_active(layout) or not is_landscape(fmt):
        return layout if isinstance(layout, dict) else None
    lay = normalize_layout(layout)
    if lay["mode"] == "meme":
        return None
    if lay["mode"] == AVATAR_MODE:
        return lay  # аватар на весь кадр — геометрия от размера кадра любого формата
    width, height = frame_size(fmt)
    if lay["mode"] == "split":
        w = LANDSCAPE_PIP_W
        right, top = True, lay["cam_pos"] != "bottom"
    else:
        pip = lay["pip"]
        w = min(0.45, max(0.12, float(pip["w"]) * height / width))
        right, top = float(pip["x"]) >= 0.5, float(pip["y"]) < 0.5
    src = source or {}
    cam = lay["cam"]
    aspect = (cam["w"] * float(src.get("width") or 16)) / max(1e-6, cam["h"] * float(src.get("height") or 9))
    h = min(1.0, w * width / max(1e-6, aspect) / height)
    mx = LANDSCAPE_PIP_MARGIN * height / width
    x = 1 - mx - w / 2 if right else mx + w / 2
    y = LANDSCAPE_PIP_MARGIN + h / 2 if top else 1 - LANDSCAPE_PIP_MARGIN - h / 2
    return {**lay, "mode": "pip_cam", "pip": {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4)}}


def ff_color(color: str) -> str:
    """``#rrggbb`` → ``0xrrggbb`` (решётка в графе фильтров лишний риск)."""
    return "0x" + (color[1:] if _COLOR_RE.match(color or "") else "000000")


# --------------------------------------------------------------------------- автопоиск вебки

_FACECAM_SAMPLES = 20
_SAMPLE_W = 640
# Лицо в вебке: не крошка (≥2% ширины кадра) и не весь кадр (≤30%).
_MIN_FACE_FRAC = 0.02
_MAX_FACE_FRAC = 0.30
# Центры лиц из разных кадров ближе этого (в долях кадра) — одно место.
_CLUSTER_DIST = 0.06


def _sample_frames(local_path: str, duration: float, count: int, out_dir: Path) -> list[Path]:
    """``count`` кадров равномерно по видео (без самых краёв — заставки/титры)."""
    frames: list[Path] = []
    if duration <= 0:
        times = [0.0]
    else:
        lo, hi = duration * 0.04, duration * 0.96
        times = [lo + (hi - lo) * (i + 0.5) / count for i in range(count)]
    for i, t in enumerate(times):
        out = out_dir / f"fc_{i:03d}.png"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-ss", f"{max(0.0, t):.3f}", "-i", local_path,
                "-frames:v", "1", "-vf", f"scale={_SAMPLE_W}:-2", str(out),
            ],
            capture_output=True,
            timeout=60,
        )
        if out.exists() and out.stat().st_size > 0:
            frames.append(out)
    return frames


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def facecam_from_faces(
    faces_per_frame: list[list[tuple[float, float, float, float]]],
    frame_aspect: float,
) -> dict:
    """Самое устойчивое лицо → прямоугольник вебки.

    ``faces_per_frame`` — для каждого кадра лица ``(x, y, w, h)`` в долях кадра.
    Лицо стримера в вебке стоит на одном месте весь стрим, а лица в игре/фильме
    гуляют — поэтому берём место, где лицо есть в наибольшем числе кадров. Вебку
    достраиваем вокруг лица как типичное окно 4:3 (лицо ≈ треть ширины), а если
    окно почти касается края кадра — прижимаем к краю: оверлеи так и ставят.
    ``frame_aspect`` — ширина/высота исходника (для пропорций 4:3 в долях).
    """
    total = len(faces_per_frame)
    clusters: list[dict] = []
    for index, faces in enumerate(faces_per_frame):
        for (fx, fy, fw, fh) in faces:
            if not (_MIN_FACE_FRAC <= fw <= _MAX_FACE_FRAC):
                continue
            cx, cy = fx + fw / 2, fy + fh / 2
            for cluster in clusters:
                if abs(cluster["cx"] - cx) <= _CLUSTER_DIST and abs(cluster["cy"] - cy) <= _CLUSTER_DIST * frame_aspect:
                    cluster["boxes"].append((fx, fy, fw, fh))
                    cluster["frames"].add(index)
                    n = len(cluster["boxes"])
                    cluster["cx"] += (cx - cluster["cx"]) / n
                    cluster["cy"] += (cy - cluster["cy"]) / n
                    break
            else:
                clusters.append({"cx": cx, "cy": cy, "boxes": [(fx, fy, fw, fh)], "frames": {index}})
    if not clusters or total <= 0:
        return {"cam": None, "confidence": 0.0, "frames": total}

    def edge_score(c: dict) -> float:
        # При равной частоте выигрывает лицо ближе к краю/углу — там вебки и живут.
        return max(abs(c["cx"] - 0.5), abs(c["cy"] - 0.5))

    best = max(clusters, key=lambda c: (len(c["frames"]), edge_score(c)))
    support = len(best["frames"]) / total
    if len(best["frames"]) < max(2, round(total * 0.2)):
        return {"cam": None, "confidence": round(support, 3), "frames": total}

    fw = _median([b[2] for b in best["boxes"]])
    fh = _median([b[3] for b in best["boxes"]])
    cx = _median([b[0] + b[2] / 2 for b in best["boxes"]])
    cy = _median([b[1] + b[3] / 2 for b in best["boxes"]])
    # Окно 4:3 в пикселях: ширина ≈ 3.2 ширины лица; в долях высоты кадра
    # это w * aspect * 3/4.
    w = min(1.0, fw * 3.2)
    h = min(1.0, max(fh * 2.4, w * frame_aspect * 0.75))
    x = cx - w / 2
    y = cy - h * 0.45  # лицо чуть выше центра окна
    snap = 0.05
    if x < snap:
        x = 0.0
    if x + w > 1 - snap:
        x = 1.0 - w
    if y < snap:
        y = 0.0
    if y + h > 1 - snap:
        y = 1.0 - h
    cam = normalize_cam({"x": max(0.0, x), "y": max(0.0, y), "w": w, "h": h})
    # Уверенность: доля кадров с лицом на месте, чуть срезанная, если лицо дрожит.
    jitter = _median([abs(b[0] + b[2] / 2 - cx) for b in best["boxes"]]) / max(fw, 1e-3)
    confidence = support * (1.0 if jitter < 0.25 else 0.75)
    return {"cam": cam, "confidence": round(min(1.0, confidence), 3), "frames": total}


def detect_facecam(source: dict, samples: int = _FACECAM_SAMPLES) -> dict:
    """Найти вебку стримера: ``{"cam": {x,y,w,h} | None, "confidence": 0..1, "frames": n}``."""
    import cv2

    from app.autofocus import _detect_faces

    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        raise ValueError("у источника нет файла видео")
    duration = float(source.get("duration_sec") or 0)
    with tempfile.TemporaryDirectory() as td:
        frames = _sample_frames(local, duration, samples, Path(td))
        if not frames:
            raise ValueError("ffmpeg не отдал ни одного кадра")
        faces_per_frame: list[list[tuple[float, float, float, float]]] = []
        aspect = 16 / 9
        for path in frames:
            img = cv2.imread(str(path))
            if img is None:
                continue
            h, w = img.shape[:2]
            aspect = w / h if h else aspect
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            faces = _detect_faces(img, gray, 0.6)
            faces_per_frame.append([(fx / w, fy / h, fw / w, fh / h) for (fx, fy, fw, fh) in faces])
    return facecam_from_faces(faces_per_frame, aspect)
