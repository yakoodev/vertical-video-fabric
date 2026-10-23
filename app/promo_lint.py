"""Линт промо приложения: геометрия слоёв во времени (ключи x/y/scale/opacity, камера).

Проверочный прогон Gemini (клип 575, промо на холсте) собрал промо новыми инструментами,
но слабее эталона: заголовки шагов легли на экран телефона, на наезде телефон уехал за
кадр, логотип финала влетел поверх не ушедшего телефона, ссылка в финале бледная, хук
висел все 18 с. Здесь — предупреждения (не ошибки) про это, с числом и советом.

Правила 3–5 (переход сцен, мелкий/бледный финальный текст, долгий хук) включаются только
на холсте или когда в клипе есть телефон (device) — на клипах со стримов не шумят.
"""

from __future__ import annotations

import math
import re
from typing import Any

W, H = 1080.0, 1920.0
STEP = 0.1  # шаг выборки по времени, с
MIN_VISIBLE_OPACITY = 0.3
# 1) титр на экране телефона
TEXT_ON_SCREEN_SHARE = 0.15
TEXT_ON_SCREEN_MIN_SEC = 0.3
PHONE_GAP_PX = 24
# 2) слой за краем кадра
OFF_FRAME_SHARE = 0.12
OFF_FRAME_MIN_SEC = 0.25
EDGE_MOTION_SEC = 0.5  # въезд/выезд у начала и конца слоя — намеренный
BIG_SHARE = 0.4  # «крупная» картинка/видео — не уже 40 % кадра
BACKGROUND_SHARE = 0.9  # во всю ширину — фон холста, не сцена
# 3) переход сцен без разведения
SCENE_OVERLAP_SHARE = 0.25
SCENE_OVERLAP_SEC = 0.3
# 4) финал: мелкий или бледный текст
FINAL_SEC = 4.0
FINAL_MIN_FONT = 44
MIN_CONTRAST = 3.0
# Крупный жирный текст на сплошной плашке читается и при 2.5:1 — белый на фирменном
# оранжевом (#ff6a1f, 2.9:1) — обычная кнопка CTA, не шумим.
MIN_PLATE_CONTRAST = 2.5
DARK_BASE = "#0b1018"
# 5) хук висит слишком долго
HOOK_START_SEC = 0.5
HOOK_MAX_SEC = 6.0


# ---------------------------------------------------------------- ключи и камера


def _num(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _ease(name: Any, p: float) -> float:
    """Кривые как во frontend/src/motion/easing.ts (Back/Elastic — без «пружинки»)."""
    p = min(1.0, max(0.0, p))
    name = str(name or "inOutCubic")
    if name == "linear" or name.startswith("bezier"):
        return p
    if name == "step":
        return 1.0 if p >= 1 else 0.0
    if name == "inQuad":
        return p * p
    if name == "outQuad":
        return 1 - (1 - p) ** 2
    if name == "inOutQuad":
        return 2 * p * p if p < 0.5 else 1 - (-2 * p + 2) ** 2 / 2
    if name in ("inCubic", "inBack"):
        return p ** 3
    if name in ("outCubic", "outBack", "outElastic", "outBounce"):
        return 1 - (1 - p) ** 3
    return 4 * p ** 3 if p < 0.5 else 1 - (-2 * p + 2) ** 3 / 2


def key_value(points: Any, t: float, fallback: float) -> float:
    """Значение ключей в момент ``t`` (как keys.ts: ease точки — на отрезке после неё)."""
    if not isinstance(points, list) or not points:
        return fallback
    pts = sorted((p for p in points if isinstance(p, dict)), key=lambda p: _num(p.get("t"), 0.0))
    if not pts:
        return fallback
    if t <= _num(pts[0].get("t"), 0.0):
        return _num(pts[0].get("v"), fallback)
    for a, b in zip(pts, pts[1:]):
        ta, tb = _num(a.get("t"), 0.0), _num(b.get("t"), 0.0)
        if ta <= t < tb:
            p = (t - ta) / (tb - ta) if tb > ta else 1.0
            va, vb = _num(a.get("v"), fallback), _num(b.get("v"), fallback)
            return va + (vb - va) * _ease(a.get("ease"), p)
    return _num(pts[-1].get("v"), fallback)


def layer_state(layer: dict, t: float) -> dict:
    """x/y/scale/opacity слоя в момент ``t`` клипа (ключи — от начала слоя)."""
    keys = layer.get("keys") if isinstance(layer.get("keys"), dict) else {}
    local = t - _num(layer.get("start"), 0.0)
    return {
        "x": key_value(keys.get("x"), local, _num(layer.get("x"), 0.5)),
        "y": key_value(keys.get("y"), local, _num(layer.get("y"), 0.5)),
        "scale": max(0.0, key_value(keys.get("scale"), local, _num(layer.get("scale"), 1.0))),
        "opacity": min(1.0, max(0.0, key_value(keys.get("opacity"), local, _num(layer.get("opacity"), 1.0)))),
    }


def camera_view(camera: Any, t: float) -> tuple[float, float, float]:
    """(zoom, x, y) камеры в момент ``t`` (как camera.ts, без тряски)."""
    keys = sorted((k for k in camera or [] if isinstance(k, dict)), key=lambda k: _num(k.get("t"), 0.0))
    if not keys:
        return 1.0, 0.5, 0.5

    def pick(k: dict) -> tuple[float, float, float]:
        return _num(k.get("zoom"), 1.0), _num(k.get("x"), 0.5), _num(k.get("y"), 0.5)

    state = pick(keys[-1])
    if t <= _num(keys[0].get("t"), 0.0):
        state = pick(keys[0])
    else:
        for a, b in zip(keys, keys[1:]):
            ta, tb = _num(a.get("t"), 0.0), _num(b.get("t"), 0.0)
            if ta <= t < tb:
                p = _ease(a.get("ease"), (t - ta) / (tb - ta) if tb > ta else 1.0)
                state = tuple(u + (v - u) * p for u, v in zip(pick(a), pick(b)))
                break
    zoom = min(4.0, max(1.0, state[0]))
    half = 0.5 / zoom
    return zoom, min(max(state[1], half), 1 - half), min(max(state[2], half), 1 - half)


def _through_camera(rect: tuple, camera: Any, t: float) -> tuple:
    """Прямоугольник слоя ``followCamera`` после наезда камеры."""
    zoom, cx, cy = camera_view(camera, t)
    if zoom == 1.0:
        return rect
    tx, ty = W / 2 - cx * W * zoom, H / 2 - cy * H * zoom
    x0, y0, x1, y1 = rect
    return x0 * zoom + tx, y0 * zoom + ty, x1 * zoom + tx, y1 * zoom + ty


# ---------------------------------------------------------------- размеры слоёв


def _props(layer: dict) -> dict:
    return layer.get("props") if isinstance(layer.get("props"), dict) else {}


def is_phone(layer: dict) -> bool:
    return layer.get("type") in ("image", "video") and _props(layer).get("device") == "phone"


def has_device_layers(layers: list[dict]) -> bool:
    return any(is_phone(l) and not l.get("hidden") for l in layers)


def _aspect(layer: dict, fallback: float) -> float:
    """Высота/ширина содержимого с окном crop: по размеру файла (``_natural`` из библиотеки) или ``fallback``."""
    props = _props(layer)
    natural = layer.get("_natural")
    base = fallback
    if isinstance(natural, (list, tuple)) and len(natural) == 2 and natural[0] and natural[1]:
        base = float(natural[1]) / float(natural[0])
    crop = props.get("crop") if isinstance(props.get("crop"), dict) else {}
    return base * _num(crop.get("h"), 1.0) / max(0.01, _num(crop.get("w"), 1.0))


def phone_size(layer: dict) -> tuple[float, float, float, float]:
    """(экран w, экран h, корпус w, корпус h) телефона в опорных px (как device.ts phoneMetrics)."""
    from app import motion

    props = _props(layer)
    w = _num(props.get("w"), 600.0)
    bar = props.get("statusBar")
    bar = motion.STATUS_BAR_AUTO if bar is True else (_num(bar, 0.0) if bar not in (None, False) else 0.0)
    sh = w * _aspect(layer, 19.5 / 9) + w * bar  # размер файла неизвестен — запись экрана 9:19.5
    edge = w * (0.017 + 0.03)
    return w, sh, w + 2 * edge + 2 * w * 0.012, sh + 2 * edge


def picture_size(layer: dict) -> tuple[float, float]:
    """Размер картинки/видео (без scale), опорные px: телефон — корпус; без размера файла
    картинка — квадрат, видео — 9:16."""
    props = _props(layer)
    if is_phone(layer):
        _sw, _sh, ow, oh = phone_size(layer)
        return ow, oh
    w = _num(props.get("w"), 600.0 if layer.get("type") == "video" else 400.0)
    border = 2 * _num((props.get("border") or {}).get("width") if isinstance(props.get("border"), dict) else 0, 0.0)
    h = w * _aspect(layer, 16 / 9 if layer.get("type") == "video" else 1.0)
    return w + border, h + border


def text_size(layer: dict) -> tuple[float, float]:
    """Примерный размер текста (без scale), опорные px: строки × кегль, ширина по длине строк."""
    from app.agent_tools import text_lines

    props = _props(layer)
    text = str(props.get("text") or "")
    size = _num(props.get("size"), 96.0)
    line_height = _num(props.get("lineHeight"), 1.15)
    max_width = _num(props.get("maxWidth"), 920.0)
    heavy = bool(props.get("uppercase")) or text.upper() == text
    factor = 0.68 if heavy and _num(props.get("weight"), 800.0) >= 700 else 0.58
    longest = max((len(line) for line in text.split("\n")), default=0)
    width = min(max_width, longest * size * factor)
    height = size * line_height * text_lines(props)
    bg = props.get("bg") if isinstance(props.get("bg"), dict) else None
    if bg:
        width += 2 * _num(bg.get("padX"), 20.0)
        height += 2 * _num(bg.get("padY"), 10.0)
    return width, height


def layer_rect(layer: dict, t: float, camera: Any = None, screen: bool = False) -> tuple | None:
    """Прямоугольник слоя в кадре (px) в момент ``t``; ``screen`` — только экран телефона."""
    st = layer_state(layer, t)
    kind = layer.get("type")
    if kind == "text":
        w, h = text_size(layer)
    elif kind in ("image", "video"):
        # Экран телефона — по центру корпуса (рамка одинаковая сверху и снизу).
        w, h = phone_size(layer)[:2] if screen and is_phone(layer) else picture_size(layer)
    else:
        return None
    w, h = w * st["scale"], h * st["scale"]
    cx, cy = st["x"] * W, st["y"] * H
    rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    if layer.get("followCamera") and camera:
        rect = _through_camera(rect, camera, t)
    return rect


def _area(rect: tuple) -> float:
    return max(0.0, rect[2] - rect[0]) * max(0.0, rect[3] - rect[1])


def _intersection(a: tuple, b: tuple) -> float:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def _visible(layer: dict, t: float) -> bool:
    start, end = _num(layer.get("start"), 0.0), _num(layer.get("end"), 0.0)
    return start <= t < end and layer_state(layer, t)["opacity"] >= MIN_VISIBLE_OPACITY


def _samples(start: float, end: float) -> list[float]:
    count = int(math.floor((end - start) / STEP + 1e-6))
    return [round(start + i * STEP, 3) for i in range(max(0, count))]


def _width_share(layer: dict) -> float:
    w, _h = picture_size(layer)
    return w * _num(layer.get("scale"), 1.0) / W


def is_big_picture(layer: dict) -> bool:
    """Крупная картинка/видео (сцена): телефон или не уже 40 % кадра, но не фон во всю ширину."""
    if layer.get("type") not in ("image", "video") or layer.get("behind"):
        return False
    if is_phone(layer):
        return True
    return BIG_SHARE <= _width_share(layer) < BACKGROUND_SHARE


def _short(text: Any, limit: int = 24) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ---------------------------------------------------------------- цвет


def _rgb(color: Any) -> tuple[int, int, int] | None:
    text = str(color or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(c * 2 for c in text)
    if len(text) not in (6, 8):
        return None
    try:
        return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except ValueError:
        return None


def luminance(color: Any) -> float | None:
    """Относительная яркость по WCAG (0 — чёрный, 1 — белый)."""
    rgb = _rgb(color)
    if not rgb:
        return None

    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: Any, b: Any) -> float | None:
    la, lb = luminance(a), luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _close(a: Any, b: Any, distance: float = 90.0) -> bool:
    ra, rb = _rgb(a), _rgb(b)
    return bool(ra and rb) and sum((x - y) ** 2 for x, y in zip(ra, rb)) < distance ** 2


def _backdrop_colors(backdrop: Any) -> list[str]:
    colors = backdrop.get("colors") if isinstance(backdrop, dict) else None
    return [str(c) for c in colors or [] if _rgb(c)]


def _zoomed(layer: dict, t: float, camera: Any) -> bool:
    """Слой сейчас на наезде: ключ scale больше обычного или камера приближает (followCamera)."""
    if layer_state(layer, t)["scale"] > _num(layer.get("scale"), 1.0) * 1.02:
        return True
    return bool(layer.get("followCamera") and camera) and camera_view(camera, t)[0] > 1.02


# ---------------------------------------------------------------- правила


def text_over_phone(texts: list[dict], phones: list[dict], camera: Any) -> list[tuple[tuple, str]]:
    """1) Титр поверх экрана телефона: закрывает интерфейс приложения."""
    out = []
    for text in texts:
        worst = None  # (доля, телефон, t0, t1, верх телефона)
        # Заголовок с затемнением (props.backdrop) телефон может накрыть на наезде — так и задумано.
        shaded = bool(_props(text).get("backdrop"))
        for phone in phones:
            lo = max(_num(text.get("start"), 0.0), _num(phone.get("start"), 0.0))
            hi = min(_num(text.get("end"), 0.0), _num(phone.get("end"), 0.0))
            hits, share_max, top = [], 0.0, H
            for t in _samples(lo, hi):
                if not (_visible(text, t) and _visible(phone, t)):
                    continue
                if shaded and _zoomed(phone, t, camera):
                    continue
                tr = layer_rect(text, t, camera)
                sr = layer_rect(phone, t, camera, screen=True)
                if not tr or not sr or _area(tr) <= 0:
                    continue
                share = _intersection(tr, sr) / _area(tr)
                if share > TEXT_ON_SCREEN_SHARE:
                    hits.append(t)
                    share_max = max(share_max, share)
                    body = layer_rect(phone, t, camera)
                    top = min(top, body[1]) if body else top
            if len(hits) * STEP >= TEXT_ON_SCREEN_MIN_SEC - 1e-6 and (worst is None or share_max > worst[0]):
                worst = (share_max, phone, hits[0], hits[-1] + STEP, top)
        if worst:
            share, phone, t0, t1, top = worst
            _w, th = text_size(text)
            half = th * layer_state(text, t0)["scale"] / 2
            limit = (top - PHONE_GAP_PX - half) / H
            from app.agent_tools import SAFE_TOP

            where = (f"подними титр выше верха телефона: y ≤ {limit:.3f} (верх корпуса на {top / H:.3f}, "
                     f"зазор ≥ {PHONE_GAP_PX} px)" if limit - half / H >= SAFE_TOP else
                     f"над телефоном места нет (верх корпуса на {top / H:.3f}) — уменьши телефон (w/scale) или "
                     "опусти его (y), потом титр над ним с зазором ≥ 24 px; заезжает под титр только на наезде — "
                     "props.backdrop у титра")
            label = _short(_props(text).get("text"))
            out.append(((text.get("id"), phone.get("id")),
                        f"{text.get('id')}: титр «{label}» на экране телефона {phone.get('id')} ({share:.0%} "
                        f"текста, {t0:.1f}–{t1:.1f} с) — закрывает интерфейс приложения; {where}"))
    return out


def off_frame(layers: list[dict], camera: Any, promo: bool) -> list[tuple[tuple, str]]:
    """2) Телефон/крупная картинка уезжает за край кадра (не въезд/выезд у краёв слоя)."""
    out = []
    frame = (0.0, 0.0, W, H)
    for layer in layers:
        if not (is_phone(layer) or (promo and is_big_picture(layer))):
            continue
        start, end = _num(layer.get("start"), 0.0), _num(layer.get("end"), 0.0)
        hits, worst = [], (0.0, start)
        for t in _samples(start + EDGE_MOTION_SEC, end - EDGE_MOTION_SEC + 1e-6):
            if layer_state(layer, t)["opacity"] < 0.5:  # растворяется — не считаем
                continue
            rect = layer_rect(layer, t, camera)
            if not rect or _area(rect) <= 0:
                continue
            share = 1 - _intersection(rect, frame) / _area(rect)
            if share > OFF_FRAME_SHARE:
                hits.append(t)
                if share > worst[0]:
                    worst = (share, t)
        if len(hits) * STEP >= OFF_FRAME_MIN_SEC - 1e-6:
            share, t = worst
            st = layer_state(layer, t)
            what = "телефон" if is_phone(layer) else "картинка"
            out.append(((layer.get("id"),),
                        f"{layer.get('id')}: на {t:.1f} с {what} за краем кадра на {share:.0%} (scale "
                        f"{st['scale']:.2f}, x {st['x']:.2f}, y {st['y']:.2f}) — на пике наезда уменьши scale или "
                        "сдвинь x/y, чтобы корпус был в кадре; наезд лучше камерой (set_camera) + followCamera, "
                        "проверь view_frame"))
    return out


def scenes_without_gap(layers: list[dict], camera: Any) -> list[tuple[tuple, str]]:
    """3) Две крупные сцены одновременно и внахлёст: логотип влетел поверх не ушедшего телефона."""
    big = [l for l in layers if is_big_picture(l)]
    out = []
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            if not (is_phone(a) or is_phone(b)):
                continue
            lo = max(_num(a.get("start"), 0.0), _num(b.get("start"), 0.0))
            hi = min(_num(a.get("end"), 0.0), _num(b.get("end"), 0.0))
            hits, share_max = [], 0.0
            for t in _samples(lo, hi):
                if not (_visible(a, t) and _visible(b, t)):
                    continue
                ra, rb = layer_rect(a, t, camera), layer_rect(b, t, camera)
                if not ra or not rb:
                    continue
                small = min(_area(ra), _area(rb))
                share = _intersection(ra, rb) / small if small > 0 else 0.0
                if share > SCENE_OVERLAP_SHARE:
                    hits.append(t)
                    share_max = max(share_max, share)
            if len(hits) * STEP > SCENE_OVERLAP_SEC + 1e-6:
                first, later = sorted((a, b), key=lambda l: (_num(l.get("start"), 0.0), str(l.get("id"))))
                enter = _num(later.get("start"), 0.0)
                out.append(((a.get("id"), b.get("id")),
                            f"{first.get('id')} и {later.get('id')}: {len(hits) * STEP:.1f} с вместе и внахлёст "
                            f"({share_max:.0%} меньшего, {hits[0]:.1f}–{hits[-1] + STEP:.1f} с) — переход сцен без "
                            f"разведения: пусть {first.get('id')} уйдёт (end/выезд) до {enter:.1f} с или "
                            f"{later.get('id')} войдёт позже"))
    return out


def final_text(texts: list[dict], duration: float, backdrop: Any) -> list[tuple[tuple, str]]:
    """4) Финал (последние 4 с): ссылка/CTA мелкая или сливается с фоном."""
    colors = _backdrop_colors(backdrop)
    base = colors[0] if colors else DARK_BASE
    accents = colors[1:]
    out = []
    for text in texts:
        if _num(text.get("end"), 0.0) <= duration - FINAL_SEC:
            continue
        props = _props(text)
        lid = text.get("id")
        size = _num(props.get("size"), 80.0) * _num(text.get("scale"), 1.0)
        if size < FINAL_MIN_FONT:
            out.append(((lid,), f"{lid}: в финале шрифт {size:.0f}px — ссылку/CTA не прочитать; "
                                f"≥ {FINAL_MIN_FONT} px (лучше 56–64) и контрастным цветом"))
        color = props.get("color") or "#ffffff"
        if props.get("gradient"):
            continue
        stroke = props.get("stroke") if isinstance(props.get("stroke"), dict) else None
        bg = props.get("bg") if isinstance(props.get("bg"), dict) else None
        if bg and _num(bg.get("opacity"), 1.0) >= 0.6:
            ratio = contrast(color, bg.get("color"))
            if ratio is not None and ratio < MIN_PLATE_CONTRAST:
                out.append(((lid,), f"{lid}: в финале текст {color} на плашке {bg.get('color')} — контраст "
                                    f"{ratio:.1f}:1 < {MIN_PLATE_CONTRAST:g}:1; белый на тёмной плашке или тёмный на светлой"))
            continue
        if stroke and _num(stroke.get("width"), 0.0) >= 3:
            continue  # обводка держит читаемость на любом фоне
        ratio = contrast(color, base)
        blend = next((c for c in accents if _close(color, c)), None)
        if ratio is not None and ratio < MIN_CONTRAST:
            out.append(((lid,), f"{lid}: в финале текст {color} на фоне {base} — контраст {ratio:.1f}:1 "
                                f"< {MIN_CONTRAST:g}:1; белый/яркий цвет или плашка bg"))
        elif blend:
            out.append(((lid,), f"{lid}: в финале текст {color} того же цвета, что пятна фона ({blend}) — "
                                "сольётся; белый/яркий другой цвет или плашка bg (CTA — плашкой акцентного цвета)"))
    return out


def long_hook(texts: list[dict], scenes: list[dict]) -> list[tuple[tuple, str]]:
    """5) Хук с первых 0.5 с висит больше 6 с, а под ним меняются сцены."""
    out = []
    for text in texts:
        start, end = _num(text.get("start"), 0.0), _num(text.get("end"), 0.0)
        if start > HOOK_START_SEC or end - start <= HOOK_MAX_SEC:
            continue
        changes = sum(1 for s in scenes if start + 1.0 < _num(s.get("start"), 0.0) < end)
        if not changes:
            continue
        label = _short(_props(text).get("text"))
        out.append(((text.get("id"),),
                    f"{text.get('id')}: хук «{label}» висит {end - start:.1f} с поверх {changes} смен сцен — хук "
                    f"2–4 с (end ≈ {start + 3:.1f}), дальше заголовки шагов над телефоном"))
    return out


# 6) пустой кадр и 7) нет финала — прогон 578: пачка финала (логотип, «РЕГИСТРАЦИЯ ОТКРЫТА»,
# частицы) отклонена из-за одного звука, агент не повторил — последние 4 с ролика были пустым фоном.
EMPTY_MIN_SEC = 1.0
EMPTY_NOTE = "нет слоёв — проверь, применились ли последние пачки"
# Ссылка или призыв в финальном тексте: адрес, бот, «жми», «регистрация», «скачай»…
CTA_RE = re.compile(
    r"https?://|www\.|t\.me|@\w|\.(?:ru|com|io|app|me|gg|tv|net|org|рф)\b|"
    r"регистр|скача|установ|жми|нажми|переход|подпис|присоедин|заход|попробуй|участв|ссылк|бот\b|"
    r"начни|открой|забирай|получи|ждём|ждем|link|join|download|sign\s*up",
    re.IGNORECASE,
)


def _anim_half(layer: dict, key: str) -> float:
    anim = layer.get(key) if isinstance(layer.get(key), dict) else {}
    if not anim or anim.get("type") in (None, "none"):
        return 0.0
    return _num(anim.get("duration"), 0.0) / 2


def is_backdrop_layer(layer: dict) -> bool:
    """Фон, а не содержимое кадра: картинка/видео/фигура во всю ширину (не телефон)."""
    kind = layer.get("type")
    if kind not in ("image", "video", "shape") or is_phone(layer):
        return False
    props = _props(layer)
    scale = _num(layer.get("scale"), 1.0)
    width = _num(props.get("w"), 600.0) * scale
    if kind == "shape":
        return width >= BACKGROUND_SHARE * W and _num(props.get("h"), 400.0) * scale >= BACKGROUND_SHARE * H
    return width >= BACKGROUND_SHARE * W


def is_content_layer(layer: dict) -> bool:
    """Что делает кадр непустым: всё видимое, кроме фона, частиц и субтитров (их видно только на речи)."""
    return (layer.get("type") not in ("audio", "particles", "captions") and not layer.get("hidden")
            and not is_backdrop_layer(layer))


def shown(layer: dict, t: float, camera: Any = None) -> bool:
    """Слой виден в момент ``t``: внутри start..end без половин входа/выхода, не прозрачный,
    не сжат в точку и хоть краем в кадре (телефон, уехавший ключами за край, не считается)."""
    start, end = _num(layer.get("start"), 0.0), _num(layer.get("end"), 0.0)
    if not (start + _anim_half(layer, "in") <= t < end - _anim_half(layer, "out")):
        return False
    state = layer_state(layer, t)
    if state["opacity"] < MIN_VISIBLE_OPACITY or state["scale"] < 0.05:
        return False
    rect = layer_rect(layer, t, camera)
    return rect is None or _intersection(rect, (0.0, 0.0, W, H)) > 0


def empty_frames(layers: list[dict], duration: float, camera: Any = None) -> list[tuple[tuple, str]]:
    """6) На холсте отрезки > 1 с, где кроме фона ничего не видно."""
    content = [l for l in layers if is_content_layer(l)]
    out, run = [], None
    for t in _samples(0.0, duration) + [None]:
        empty = t is not None and not any(shown(l, t, camera) for l in content)
        if empty:
            run = run if run is not None else t
            continue
        if run is not None:
            stop = min(duration, (t if t is not None else duration))
            if stop - run > EMPTY_MIN_SEC + 1e-6:
                out.append(((), f"пустой кадр {run:.1f}–{stop:.1f} с: {EMPTY_NOTE}"))
            run = None
    return out


def _cta_like(layer: dict) -> bool:
    props = _props(layer)
    if layer.get("type") == "text":
        return bool(CTA_RE.search(str(props.get("text") or "")))
    if layer.get("type") == "callout":
        return bool(CTA_RE.search(f"{props.get('title') or ''} {props.get('subtitle') or ''}"))
    return False


def missing_final(layers: list[dict], duration: float, camera: Any = None) -> list[tuple[tuple, str]]:
    """7) Промо с телефоном без финала: в последних 4 с ни логотипа, ни ссылки/призыва."""
    lo = max(0.0, duration - FINAL_SEC)
    times = _samples(lo, duration)
    for layer in layers:
        if layer.get("hidden"):
            continue
        logo = layer.get("type") == "image" and not is_phone(layer) and not is_backdrop_layer(layer)
        if (logo or _cta_like(layer)) and any(shown(layer, t, camera) for t in times):
            return []
    return [((), f"нет финала: в последних {FINAL_SEC:g} с ({lo:.1f}–{duration:.1f} с) ни логотипа (image), ни "
                 "текста со ссылкой/призывом — промо обрывается; финал ≥ 3 с: логотип, дата, плашка CTA и "
                 "ссылка ≥ 44 px")]


def scene_warnings(layers: list[dict], duration: float | None, canvas: bool = False, camera: Any = None) -> list[str]:
    """Пустой кадр (холст) и нет финала (промо с телефоном) — про весь клип, не про отдельный слой:
    add_layers/update_layer показывают их всегда, а не только про новые слои."""
    visible = [l for l in layers if l.get("type") != "audio" and not l.get("hidden")]
    if not duration or not visible:
        return []
    found = empty_frames(visible, float(duration), camera) if canvas else []
    if any(is_phone(l) for l in visible):
        found += missing_final(visible, float(duration), camera)
    return [text for _ids, text in found]


def promo_warnings(layers: list[dict], duration: float | None = None, only_ids: set[str] | None = None,
                   canvas: bool = False, backdrop: Any = None, camera: Any = None,
                   sizes: dict | None = None) -> list[str]:
    """Все предупреждения промо по видимым слоям; ``only_ids`` — только про эти слои (и пары с ними).
    ``sizes`` — {asset_id: (ширина, высота)} файлов библиотеки: точная пропорция картинок и записей."""
    visible = [l for l in layers if l.get("type") != "audio" and not l.get("hidden")]
    if sizes:
        visible = [{**l, "_natural": sizes.get(str(_props(l).get("asset_id")))}
                   if l.get("type") in ("image", "video") else l for l in visible]
    phones = [l for l in visible if is_phone(l)]
    promo = canvas or bool(phones)
    if not promo:
        return []
    texts = [l for l in visible if l.get("type") == "text"]
    if duration is None:
        duration = max((_num(l.get("end"), 0.0) for l in visible), default=0.0)

    found = (text_over_phone(texts, phones, camera) + off_frame(visible, camera, promo)
             + scenes_without_gap(visible, camera) + final_text(texts, float(duration), backdrop)
             + long_hook(texts, [l for l in visible if is_big_picture(l)]))
    out = [text for ids, text in found if only_ids is None or any(str(i) in only_ids for i in ids)]
    if only_ids is None:
        out += scene_warnings(visible, duration, canvas=canvas, camera=camera)
    return out
