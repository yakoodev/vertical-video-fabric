"""Правила кадра (композиция и монтаж): ось 180°, порядок персонажей по X, место перед взглядом, линия глаз,
запас над головой, баланс веса, ракурс по намерению, широкий объектив вблизи, «самое контрастное — герой»,
туман по глубине при слиянии с фоном (docs/knowledge/youtube/studioigor.md, G:\\vvf-hot\\yt\\related\\camera-ai3d).

Чистые функции без сцены и без БД: на входе камера ``{pos, target, fov (верт., °), roll?, lens?}`` и точки мира,
на выходе находки ``{rule, level: error|warning, text, ...числа}``. Ось «вверх» — параметр ``up`` (блокинг: Y
вверх, сантиметры; Blender: Z вверх, метры — ``unit`` = сколько единиц в метре). Кадр по умолчанию 9:16 (верт.).

Каждое правило проверено на отрицательном контроле (заведомо плохой кадр) в ``tests/test_shot_rules.py``.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

Y_UP = (0.0, 1.0, 0.0)
Z_UP = (0.0, 0.0, 1.0)
ASPECT = 9 / 16  # ширина / высота кадра

AXIS_ON_LINE_DEG = 15.0   # камера почти на линии действия — перескок допустим (нейтральный план)
LOOK_ROOM_MIN = 1.5       # место перед взглядом ≥ 1.5 × места за спиной
EYE_LINES = (1 / 3, 0.382)  # глаза на трети или на золотом сечении (доля высоты сверху)
EYE_TOL = 0.08
EYE_SIZES = ("medium", "medium_close", "close")
# Крупный план: над макушкой не больше 15% высоты кадра. В разборе — «≈≤12%, подобрать по эталонам»; при глазах на
# трети и кадре ~2.5 высоты головы макушка уже на 12–15% сверху, поэтому 12% спорило бы с линией глаз. Эталонами
# не калибровано.
HEAD_ROOM_MAX = 0.15
HEAD_ROOM_SIZES = ("medium_close", "close")
BALANCE_MAX = 0.25        # центр визуального веса по X — не дальше 0.25 ширины кадра от центра
INTENT_PITCH = 10.0       # power — снизу вверх ≥ 10°, weak — сверху вниз ≥ 10°
ROLL_UNEASY = 5.0         # «тревога» — крен ≥ 5°; в остальных планах крен ≤ 2°
ROLL_FLAT = 2.0
WIDE_LENS_MM = 35.0
WIDE_LENS_NEAR_M = 1.0
WIDE_LENS_SIZES = ("medium_close", "close", "extreme_close")
INTENTS = ("power", "weak", "neutral", "uneasy")
INTENT_ALIASES = {"сила": "power", "мощь": "power", "heroic": "power", "dominant": "power", "слабость": "weak",
                  "small": "weak", "submissive": "weak", "нейтрально": "neutral", "normal": "neutral", "eye": "neutral",
                  "тревога": "uneasy", "dutch": "uneasy", "tense": "uneasy", "anxious": "uneasy"}
TAGS = ("imbalance", "obstacle", "reaction", "cross_line")


def _sub(a, b):
    return [a[i] - b[i] for i in range(3)]


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _unit(a):
    n = math.sqrt(_dot(a, a)) or 1.0
    return [v / n for v in a]


def basis(camera: dict, up: Iterable[float] = Y_UP) -> tuple[list[float], list[float], list[float]]:
    """(вперёд, вправо, вверх) камеры; крен ``roll`` (°, + — по часовой для зрителя) поворачивает право/верх."""
    up = list(up)
    f = _unit(_sub(camera["target"], camera["pos"]))
    r = _cross(f, up)
    if _dot(r, r) < 1e-12:
        r = _cross(f, [up[1], up[2], up[0]])
    r = _unit(r)
    u = _cross(r, f)
    roll = math.radians(float(camera.get("roll") or 0.0))
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        r, u = [c * r[i] - s * u[i] for i in range(3)], [s * r[i] + c * u[i] for i in range(3)]
    return f, r, u


def project(camera: dict, p: Iterable[float], up: Iterable[float] = Y_UP, aspect: float = ASPECT
            ) -> tuple[float, float, float] | None:
    """Точка мира → (x, y в NDC −1..1, глубина); за камерой — None. ``fov`` — вертикальный угол кадра."""
    f, r, u = basis(camera, up)
    q = _sub(list(p), camera["pos"])
    z = _dot(q, f)
    if z <= 1e-6:
        return None
    tv = math.tan(math.radians(float(camera.get("fov") or 40.0)) / 2)
    th = tv * aspect
    return _dot(q, r) / z / th, _dot(q, u) / z / tv, z


def lens_mm(camera: dict) -> float:
    """Фокусное: ``lens`` как есть, иначе из вертикального угла (длинная сторона кадра 36 мм)."""
    if camera.get("lens"):
        return float(camera["lens"])
    return 18.0 / math.tan(math.radians(float(camera.get("fov") or 40.0)) / 2)


def _f(rule: str, level: str, text: str, **nums: Any) -> dict:
    return {"rule": rule, "level": level, "text": text, **nums}


def normalize_intent(value: Any) -> str:
    v = str(value or "").strip().lower()
    v = INTENT_ALIASES.get(v, v)
    return v if v in INTENTS else ""


def normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [t for t in value.replace(";", ",").split(",")]
    out = []
    for t in value or []:
        t = str(t).strip().lower().replace("-", "_").replace(" ", "_")
        t = {"дисбаланс": "imbalance", "преграда": "obstacle", "реакция": "reaction"}.get(t, t)
        if t in TAGS and t not in out:
            out.append(t)
    return out


# --------------------------------------------------------------------------- #
# Монтаж: ось 180° и порядок по X между соседними планами
# --------------------------------------------------------------------------- #


def axis_side(cam_pos: Iterable[float], a: Iterable[float], b: Iterable[float], up: Iterable[float] = Y_UP) -> float:
    """Знак стороны линии действия A→B, на которой стоит камера: ``cross(B−A, Cam−A)·up``."""
    a, b = list(a), list(b)
    return _dot(_cross(_sub(b, a), _sub(list(cam_pos), a)), list(up))


def _on_line_deg(cam_pos, a, b, up) -> float:
    """Угол (°) между линией действия и направлением «середина пары → камера» на плоскости пола; 0 — на линии."""
    up = _unit(list(up))
    def flat(v):
        return _sub(v, [up[i] * _dot(v, up) for i in range(3)])
    line = _unit(flat(_sub(list(b), list(a))))
    mid = [(a[i] + b[i]) / 2 for i in range(3)]
    to_cam = _unit(flat(_sub(list(cam_pos), mid)))
    c = abs(max(-1.0, min(1.0, _dot(line, to_cam))))
    return math.degrees(math.acos(c))


def axis_180(prev: dict, cur: dict, up: Iterable[float] = Y_UP) -> list[dict]:
    """Перескок оси между планами одной сцены. ``prev``/``cur``: ``{camera, actors: {id: точка головы},
    camera_end?, tags?}``. Линия — через первых двух общих персонажей. Можно: камера одного из планов в пределах
    ±15° от линии; камера пересекает линию внутри плана (``camera_end`` по другую сторону или тег cross_line)."""
    common = [k for k in prev.get("actors") or {} if k in (cur.get("actors") or {})]
    if len(common) < 2:
        return []
    a_id, b_id = common[0], common[1]
    out = []
    pa, pb = prev["actors"][a_id], prev["actors"][b_id]
    ca, cb = cur["actors"][a_id], cur["actors"][b_id]
    s0 = axis_side(prev["camera"]["pos"], pa, pb, up)
    s1 = axis_side(cur["camera"]["pos"], ca, cb, up)
    if s0 * s1 >= 0:
        return out
    near = min(_on_line_deg(prev["camera"]["pos"], pa, pb, up), _on_line_deg(cur["camera"]["pos"], ca, cb, up))
    if near <= AXIS_ON_LINE_DEG:
        return out
    if "cross_line" in (cur.get("tags") or []) or "cross_line" in (prev.get("tags") or []):
        return out
    for shot in (prev, cur):
        end = shot.get("camera_end")
        if end and axis_side(shot["camera"]["pos"], pa, pb, up) * axis_side(end["pos"], pa, pb, up) < 0:
            return out
    out.append(_f("axis_180", "error", f"перескок оси: камера перешла линию {a_id}–{b_id} на склейке",
                  pair=[a_id, b_id], off_line_deg=round(near, 1)))
    return out


def screen_order(prev: dict, cur: dict, up: Iterable[float] = Y_UP) -> list[dict]:
    """Кто был левее в кадре, тот и остаётся левее (общие персонажи двух соседних планов)."""
    common = [k for k in prev.get("actors") or {} if k in (cur.get("actors") or {})]
    xs = []
    for shot in (prev, cur):
        row = {}
        for k in common:
            p = project(shot["camera"], shot["actors"][k], up)
            if p is not None and abs(p[0]) <= 1.2:
                row[k] = p[0]
        xs.append(row)
    both = [k for k in common if k in xs[0] and k in xs[1]]
    out = []
    for i, a in enumerate(both):
        for b in both[i + 1:]:
            if (xs[0][a] - xs[0][b]) * (xs[1][a] - xs[1][b]) < 0:
                out.append(_f("screen_order", "error", f"{a} и {b} поменялись местами в кадре",
                              pair=[a, b], before=[round(xs[0][a], 2), round(xs[0][b], 2)],
                              after=[round(xs[1][a], 2), round(xs[1][b], 2)]))
    return out


# --------------------------------------------------------------------------- #
# Композиция одного кадра
# --------------------------------------------------------------------------- #


def look_room(camera: dict, head: Iterable[float], gaze: Iterable[float], up: Iterable[float] = Y_UP,
              who: str = "", reach: float | None = None) -> list[dict]:
    """Место перед взглядом ≥ 1.5 × места за спиной. Взгляд почти в камеру/от камеры (по экрану короче 25% от
    длины в профиль) не проверяется."""
    head = list(head)
    p0 = project(camera, head, up)
    if p0 is None:
        return []
    dist = p0[2]
    k = reach or dist * 0.25
    g = _unit(list(gaze))
    p1 = project(camera, [head[i] + g[i] * k for i in range(3)], up)
    if p1 is None:
        return []
    f, _r, _u = basis(camera, up)
    side = abs(math.sqrt(max(0.0, 1.0 - _dot(g, f) ** 2)))
    dx = p1[0] - p0[0]
    if side < 0.25 or abs(dx) < 1e-6:
        return []
    x = max(-1.0, min(1.0, p0[0]))
    front, back = (1.0 - x, 1.0 + x) if dx > 0 else (1.0 + x, 1.0 - x)
    ratio = front / max(1e-6, back)
    if ratio < LOOK_ROOM_MIN:
        return [_f("look_room", "error", f"{who or 'персонаж'}: перед взглядом места меньше, чем за спиной "
                   f"({ratio:.2f}× < {LOOK_ROOM_MIN}×)", who=who, ratio=round(ratio, 2), x=round(p0[0], 3))]
    return []


def eye_line(camera: dict, eyes: Iterable[float], size: str, up: Iterable[float] = Y_UP, who: str = "") -> list[dict]:
    """Средний и крупный план: глаза на 33% или 38.2% высоты сверху, ±8%."""
    if size not in EYE_SIZES:
        return []
    p = project(camera, eyes, up)
    if p is None:
        return []
    y = (1.0 - p[1]) / 2.0
    off = min(abs(y - t) for t in EYE_LINES)
    if off > EYE_TOL:
        return [_f("eye_line", "error", f"{who or 'глаза'}: глаза на {100 * y:.0f}% сверху (нужно 33% или 38% ±8%)",
                   who=who, eyes_from_top=round(y, 3))]
    return []


def head_room_max(camera: dict, head_top: Iterable[float], size: str, up: Iterable[float] = Y_UP,
                  who: str = "") -> list[dict]:
    """Крупный план: над макушкой не больше ``HEAD_ROOM_MAX`` высоты кадра (нижний предел — в check_frame)."""
    if size not in HEAD_ROOM_SIZES:
        return []
    p = project(camera, head_top, up)
    if p is None:
        return []
    room = (1.0 - p[1]) / 2.0
    if room > HEAD_ROOM_MAX:
        return [_f("head_room_max", "error", f"{who or 'голова'}: над макушкой {100 * room:.0f}% кадра "
                   f"(крупный план — не больше {100 * HEAD_ROOM_MAX:.0f}%)", who=who, head_room=round(room, 3))]
    return []


def balance(weights: Iterable[tuple[float, float]], tags: Iterable[str] = ()) -> list[dict]:
    """``weights``: (x центра в NDC, доля кадра) по пятнам веса. Центр веса по X (0..1 ширины) — в 0.5 ± 0.25,
    если план не помечен «imbalance»."""
    ws = [(x, w) for x, w in weights if w > 0]
    total = sum(w for _x, w in ws)
    if total <= 0 or "imbalance" in set(tags or ()):
        return []
    cx = sum((x + 1) / 2 * w for x, w in ws) / total
    if abs(cx - 0.5) > BALANCE_MAX:
        return [_f("balance", "error", f"кадр «заваливается»: центр веса на {100 * cx:.0f}% ширины "
                   "(нужно 25–75% или тег imbalance)", center_x=round(cx, 3))]
    return []


def angle_intent(camera: dict, eyes: Iterable[float], intent: str = "", up: Iterable[float] = Y_UP) -> list[dict]:
    """Ракурс по намерению: power — камера ниже глаз и смотрит вверх ≥10°; weak — выше и вниз ≥10°; neutral —
    наклон меньше 10°; uneasy — крен ≥5°. Крен больше 2° без uneasy — ошибка."""
    up = _unit(list(up))
    f, _r, _u = basis({**camera, "roll": 0}, up)
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, _dot(f, up)))))
    above = _dot(_sub(camera["pos"], list(eyes)), up)
    roll = abs(float(camera.get("roll") or 0.0))
    intent = normalize_intent(intent)
    out = []
    if intent == "power" and not (above < 0 and pitch >= INTENT_PITCH):
        out.append(_f("angle_intent", "error", f"power: камера должна быть ниже глаз и смотреть вверх ≥{INTENT_PITCH:g}° "
                      f"(сейчас наклон {pitch:+.0f}°)", pitch=round(pitch, 1), intent=intent))
    elif intent == "weak" and not (above > 0 and pitch <= -INTENT_PITCH):
        out.append(_f("angle_intent", "error", f"weak: камера должна быть выше глаз и смотреть вниз ≥{INTENT_PITCH:g}° "
                      f"(сейчас наклон {pitch:+.0f}°)", pitch=round(pitch, 1), intent=intent))
    elif intent == "neutral" and abs(pitch) >= INTENT_PITCH:
        out.append(_f("angle_intent", "error", f"neutral: наклон камеры {pitch:+.0f}° (нужно меньше {INTENT_PITCH:g}°)",
                      pitch=round(pitch, 1), intent=intent))
    if intent == "uneasy" and roll < ROLL_UNEASY:
        out.append(_f("angle_intent", "error", f"uneasy: нужен крен горизонта ≥{ROLL_UNEASY:g}° (сейчас {roll:.0f}°)",
                      roll=round(roll, 1), intent=intent))
    elif intent != "uneasy" and roll > ROLL_FLAT:
        out.append(_f("angle_intent", "error", f"крен {roll:.0f}° без намерения uneasy — горизонт завален",
                      roll=round(roll, 1), intent=intent))
    return out


def wide_lens(camera: dict, face: Iterable[float], size: str, unit: float = 100.0) -> list[dict]:
    """Предупреждение: объектив <35 мм на крупном плане и камера ближе ~1 м к лицу — лицо искажается.
    ``unit`` — единиц длины в метре (блокинг — сантиметры: 100; Blender — 1)."""
    if size not in WIDE_LENS_SIZES:
        return []
    mm = lens_mm(camera)
    d = math.sqrt(_dot(_sub(list(face), camera["pos"]), _sub(list(face), camera["pos"]))) / unit
    if mm < WIDE_LENS_MM and d < WIDE_LENS_NEAR_M:
        return [_f("wide_lens", "warning", f"широкий объектив {mm:.0f} мм в {d:.2f} м от лица на крупном плане — "
                   "лицо исказится (возьми ≥35 мм и отъедь)", lens_mm=round(mm, 1), distance_m=round(d, 2))]
    return []


# --------------------------------------------------------------------------- #
# Кадр-картинка: контраст на герое, слияние с фоном, туман по глубине
# --------------------------------------------------------------------------- #


def _box_blur(img: Any, r: int) -> Any:
    import numpy as np

    if r <= 0:
        return img.astype(float)
    a = np.pad(img.astype(float), r, mode="edge")
    c = a.cumsum(0).cumsum(1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    h, w = img.shape
    return (c[k:k + h, k:k + w] - c[:h, k:k + w] - c[k:k + h, :w] + c[:h, :w]) / (k * k)


def _dilate(mask: Any, r: int) -> Any:
    return _box_blur(mask.astype(float), r) > 1e-6


def gray(rgb: Any) -> Any:
    """Ч/б по яркости (Rec. 709), значения 0..1."""
    import numpy as np

    a = np.asarray(rgb, float)
    if a.max() > 1.5:
        a = a / 255.0
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def contrast_map(g: Any, frac: float = 0.03) -> Any:
    """Локальный контраст ч/б кадра: |яркость − среднее окна| в окне ~3% высоты, сглаженный тем же окном."""
    import numpy as np

    r = max(1, int(round(frac * g.shape[0])))
    return _box_blur(np.abs(g - _box_blur(g, r)), r)


def hero_contrast(rgb_or_gray: Any, hero_mask: Any, spot_frac: float = 0.04) -> list[dict]:
    """Самое контрастное пятно ч/б кадра должно касаться главного героя (маска AOV): иначе взгляд уходит
    на фон (пример CG Boost — дерево контрастнее героя)."""
    import numpy as np

    g = np.asarray(rgb_or_gray, float)
    g = gray(g) if g.ndim == 3 else (g / 255.0 if g.max() > 1.5 else g)
    m = np.asarray(hero_mask, bool)
    if not m.any():
        return [_f("hero_contrast", "error", "маска героя пуста — героя нет в кадре")]
    c = contrast_map(g)
    y, x = np.unravel_index(int(np.argmax(c)), c.shape)
    r = max(1, int(round(spot_frac * g.shape[0])))
    near = _dilate(m, r)
    hero_peak = float(c[near].max())
    if not near[y, x]:
        return [_f("hero_contrast", "error", "самое контрастное место кадра — не герой (взгляд уйдёт туда)",
                   spot=[round(x / g.shape[1], 3), round(y / g.shape[0], 3)],
                   hero_vs_max=round(hero_peak / max(1e-9, float(c.max())), 3))]
    return []


def figure_ground(rgb: Any, hero_mask: Any, depth: Any = None, ring_frac: float = 0.04, min_luma: float = 0.08,
                  min_hue: float = 0.06) -> list[dict]:
    """Силуэт героя против кольца фона вокруг него: разница яркости < 0.08 и оттенка (по a/b-хроме) < 0.06 —
    сливается. Тогда предлагается туман по глубине: ``near``/``far`` от глубины героя (Map Range c Clamp)."""
    import numpy as np

    a = np.asarray(rgb, float)
    if a.max() > 1.5:
        a = a / 255.0
    m = np.asarray(hero_mask, bool)
    if not m.any():
        return []
    r = max(1, int(round(ring_frac * a.shape[0])))
    ring = _dilate(m, r) & ~m
    if not ring.any():
        return []
    L = gray(a)
    dl = abs(float(L[m].mean()) - float(L[ring].mean()))
    # оттенок: оппонентные каналы (без перевода в Lab — хватает для «слилось или нет»)
    def chroma(px):
        return np.stack([px[..., 0] - px[..., 1], (px[..., 0] + px[..., 1]) / 2 - px[..., 2]], axis=-1)
    ch = chroma(a)
    dh = float(np.linalg.norm(ch[m].mean(0) - ch[ring].mean(0)))
    if dl >= min_luma or dh >= min_hue:
        return []
    out = _f("figure_ground", "error", f"герой сливается с фоном (яркость Δ{dl:.2f}, оттенок Δ{dh:.2f}) — "
             "нужен туман по глубине или контровой свет", luma_delta=round(dl, 3), hue_delta=round(dh, 3))
    if depth is not None:
        z = np.asarray(depth, float)
        hz = float(np.median(z[m]))
        out["fog"] = {"near": round(hz * 1.05, 4), "far": round(hz * 2.5, 4)}
    return [out]


def depth_fog(depth: Any, near: float, far: float) -> Any:
    """Фактор тумана по глубине: Map Range с Clamp — всегда в [0, 1] (без Clamp туман пересвечивает даль)."""
    import numpy as np

    z = np.asarray(depth, float)
    return np.clip((z - near) / max(1e-9, far - near), 0.0, 1.0)


# --------------------------------------------------------------------------- #
# Всё вместе
# --------------------------------------------------------------------------- #


def frame_rules(camera: dict, actors: list[dict], size: str, *, intent: str = "", tags: Iterable[str] = (),
                weights: Iterable[tuple[float, float]] | None = None, up: Iterable[float] = Y_UP,
                unit: float = 100.0) -> dict:
    """Правила одного кадра. ``actors``: ``[{id, eyes, head_top, gaze?, main?}]`` (точки мира). ``weights`` —
    пятна веса для баланса (по умолчанию — персонажи с весом 1). Возвращает ``{ok, problems, warnings, findings}``:
    ok — нет ошибок (предупреждения не валят)."""
    tags = normalize_tags(list(tags or ()))
    found: list[dict] = []
    main = next((a for a in actors if a.get("main")), actors[0] if actors else None)
    for a in actors:
        who = str(a.get("id") or "")
        if a.get("gaze") is not None and a.get("eyes") is not None:
            found += look_room(camera, a["eyes"], a["gaze"], up, who)
    if main is not None and main.get("eyes") is not None:
        found += eye_line(camera, main["eyes"], size, up, str(main.get("id") or ""))
        found += angle_intent(camera, main["eyes"], intent, up)
        found += wide_lens(camera, main["eyes"], size, unit)
    if main is not None and main.get("head_top") is not None:
        found += head_room_max(camera, main["head_top"], size, up, str(main.get("id") or ""))
    if weights is None:
        weights = []
        for a in actors:
            p = project(camera, a.get("eyes") or a.get("center"), up) if (a.get("eyes") or a.get("center")) else None
            if p is not None:
                weights.append((p[0], 1.0))
    found += balance(weights, tags)
    return {"ok": not any(f["level"] == "error" for f in found),
            "problems": [f["text"] for f in found if f["level"] == "error"],
            "warnings": [f["text"] for f in found if f["level"] != "error"], "findings": found}


def sequence_rules(shots: list[dict], up: Iterable[float] = Y_UP) -> list[dict]:
    """Соседние планы одной сцены (``continues``/та же ``scene``): ось 180° и порядок по X. ``shots``:
    ``[{idx, scene?, continues?, camera, camera_end?, actors: {id: точка}, tags?}]``."""
    out = []
    for prev, cur in zip(shots, shots[1:]):
        same = cur.get("continues") or (cur.get("scene") is not None and cur.get("scene") == prev.get("scene"))
        if not same:
            continue
        for f in axis_180(prev, cur, up) + screen_order(prev, cur, up):
            out.append({**f, "shots": [prev.get("idx"), cur.get("idx")]})
    return out
