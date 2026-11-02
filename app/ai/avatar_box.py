"""Где на кадре стрима аватар стримера: VTuber/PNGtuber-модель или вебка — глазами Gemini.

QA 01.11 (клип 889, sareamura): заказ «в кадре только аватар Муры», а рамка героя
(``subject_box``) поймала человека из ютуб-клипа на экране стрима — агент поставил окно 9:16 на
него, аватара в ролике не было ни секунды. Детектор лиц (``detect-facecam``) нарисованную модель не
видит, а Gemini видит: показываем ему ЦЕЛЫЕ кадры исходника (не кроп 9:16) с кусков клипа и
просим рамку аватара, отличая его от людей внутри видео, игры и браузера на экране.

Ответ — прямоугольник ``cam`` в долях ВСЕГО кадра исходника (как ``facecam`` и ``layout.cam``),
с запасом по краям: раскладка ``avatar_full`` (app/stream_layout.py) ставит его на весь кадр.
Аватар может переезжать между сценами (QA 02.11, клип 892: Мура крупно левее до 281 с, потом в углу
справа) — ``cam`` — место, где он чаще всего, а ``places`` — ВСЕ места с отрезками исходника (граница —
склейка плана между кадрами): set_layout avatar_full кладёт их в ``layout.cams``, рамка меняется по ходу.

Кэш по составу кусков клипа: модель зовём один раз.
"""

from __future__ import annotations

import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from app.settings import settings

MAX_FRAMES = 6
PAD = 0.06  # запас вокруг рамки модели (волосы, уши, руки) — доля её размера с каждой стороны
SAME_PLACE_IOU = 0.35  # рамки разных кадров с таким перекрытием — одно место аватара
SAMPLE_W = 640
MAX_CUT_WINDOW_SEC = 120.0  # склейку между кадрами двух мест ищем ffmpeg-ом в окне не длиннее

PROMPT = (
    "Это {n} кадров стрима (целиком, по порядку). Найди на каждом АВАТАР СТРИМЕРА: нарисованную "
    "VTuber/PNGtuber-модель или вебку с лицом стримера — она поверх игры/браузера, обычно сбоку или в углу, "
    "иногда крупно. Люди и персонажи ВНУТРИ видео, ютуба, игры, фильма, браузера на экране — НЕ аватар, "
    "даже если они крупнее. Рамка — вся видимая модель (голова и тело). В face — рамка ЛИЦА аватара (глаза и "
    "рот, без волос). Ещё отметь окна ЧАТА, текста и интерфейса стрима (оверлеи, донаты, подписи, рамки) рядом "
    "с аватаром и ПОВЕРХ него — в ui, каждое сообщение чата отдельной рамкой. Ответь ТОЛЬКО JSON-объектом: "
    "{{\"frames\": [{{\"frame\": 1, \"avatar\": true, \"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…, "
    "\"face\": {{\"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…}}, \"ui\": [{{\"x0\":…,\"y0\":…,\"x1\":…,\"y1\":…}}]}}, …]}} — "
    "запись на КАЖДЫЙ кадр в том же порядке (frame — номер кадра с 1), координаты — ДОЛИ кадра 0..1 (не пиксели и "
    "не 0–1000); кадр без аватара — {{\"frame\": N, \"avatar\": false}}."
)


def _cache_key(plan: dict) -> str:
    key = [(round(float(s["start_sec"]), 2), round(float(s["end_sec"]), 2)) for s in plan.get("segments") or []]
    return hashlib.sha256(json.dumps(key).encode()).hexdigest()[:16]


def _cache_path(clip_plan_id: int, plan: dict) -> Path:
    folder = settings.runtime_dir / "avatar-box"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{int(clip_plan_id)}-{_cache_key(plan)}-v3.json"  # v3: лицо (face) и окна поверх модели


def cached_avatar(store: Any, clip_plan_id: int) -> dict | None:
    """Уже найденный аватар клипа (без вызова модели) или None."""
    try:
        plan = store.get_clip_plan(int(clip_plan_id))
        data = json.loads(_cache_path(clip_plan_id, plan).read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError):
        return None
    return refresh_geometry(data) if isinstance(data, dict) else None


def refresh_geometry(data: dict) -> dict:
    """Рамки мест из кэша — заново по ответу модели (``per_frame``) текущими правилами (запас над лицом, срез чата
    сбоку): кэш, снятый до правки правил, иначе так и отдавал бы прежние рамки (QA 4 — срезанная макушка).
    Отрезки мест (границы по склейке, наплывы) — как в кэше; другое число/порядок мест — кэш как есть."""
    boxes, times = data.get("per_frame"), data.get("times")
    places = data.get("places")
    if not isinstance(boxes, list) or not isinstance(places, list) or not places:
        return data
    try:
        fresh = place_avatar(boxes, times if isinstance(times, list) else None)
    except Exception:  # noqa: BLE001 - битый кэш — как есть
        return data
    if [p.get("place") for p in fresh["places"]] != [p.get("place") for p in places]:
        return data
    out_places = []
    for old, new in zip(places, fresh["places"]):
        merged = {**old, "cam": new["cam"], "box": new["box"]}
        for key in ("face", "ui_left"):
            if key in new:
                merged[key] = new[key]
            else:
                merged.pop(key, None)
        out_places.append(merged)
    top = {k: v for k, v in data.items() if k not in ("face", "ui_left")}
    return {**top, "cam": fresh["cam"], "box": fresh["box"], "places": out_places,
            **{k: fresh[k] for k in ("face", "ui_left") if k in fresh}}


def source_times(plan: dict, n: int = MAX_FRAMES) -> list[float]:
    """Секунды исходника для кадров: равномерно по кускам клипа (без самых краёв)."""
    spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []
             if float(s["end_sec"]) > float(s["start_sec"])]
    total = sum(b - a for a, b in spans)
    if total <= 0:
        return []
    out = []
    for i in range(n):
        at = total * (i + 0.5) / n
        for a, b in spans:
            if at <= b - a:
                out.append(round(a + at, 3))
                break
            at -= b - a
    return out


def _grab(path: str, t: float, out: Path) -> bytes | None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, t):.3f}", "-i", path,
                    "-frames:v", "1", "-vf", f"scale={SAMPLE_W}:-2", "-q:v", "4", str(out)],
                   capture_output=True, timeout=60)
    return out.read_bytes() if out.exists() and out.stat().st_size else None


def _box(data: Any) -> dict | None:
    try:
        box = {k: min(1.0, max(0.0, float(data[k]))) for k in ("x0", "y0", "x1", "y1")}
    except (KeyError, TypeError, ValueError):
        return None
    if box["x1"] - box["x0"] < 0.03 or box["y1"] - box["y0"] < 0.05:
        return None
    return box


# --------------------------------------------------------------------------- разбор ответа Gemini с рамками
#
# QA 6 (клип 898): тот же запрос Gemini (temperature 0) один раз ответил ``{"frames": [...]}`` в долях 0..1, другой —
# ГОЛЫМ массивом ``[{...}, ...]`` в шкале 0–1000 (``"x0": 490``). Прежний разбор вырезал текст от первой «{» до
# последней «}» — у массива это «{…}, {…}», невалидный JSON, и сбой разбора выдавался агенту как «аватара нет».
# Теперь разбор принимает любую из форм, которые Gemini выдаёт на практике, и отличает «не разобрал» от «аватара нет».

FRAME_LIST_KEYS = ("frames", "boxes", "results", "detections", "avatars", "items", "data")
# Вложенная рамка модели внутри записи кадра: {"avatar": {...}}, {"box": {...}}, {"bbox": [...]}.
BOX_KEYS = ("avatar", "box", "bbox", "rect", "bounding_box", "model")
FACE_KEYS = ("face", "face_box", "face_2d", "face_bbox")
UI_KEYS = ("ui", "ui_boxes", "chat", "overlays")
# Явная отметка «на кадре аватара нет».
_NO_AVATAR_KEYS = ("avatar", "found", "has_avatar", "present")
_RECT_KEYS = (("x0", "y0", "x1", "y1"), ("xmin", "ymin", "xmax", "ymax"), ("left", "top", "right", "bottom"))


class AvatarAnswerError(RuntimeError):
    """Gemini ответил, но рамок из ответа не разобрать (или не ответил) — это сбой сервиса, а не «аватара нет»."""


def _strip_fences(text: str) -> str:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    return text.strip()


def _load_json(text: str) -> Any:
    """Текст ответа → JSON: целиком, без ```-обёртки, иначе первое значение с первой «{» или «[». Не вышло —
    ``ValueError``."""
    clean = _strip_fences(text)
    if not clean:
        raise ValueError("пустой ответ")
    try:
        return json.loads(clean)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    starts = sorted(i for i in (clean.find("{"), clean.find("[")) if i >= 0)
    for start in starts:
        try:
            return decoder.raw_decode(clean[start:])[0]
        except ValueError:
            continue
    raise ValueError("ответ не JSON")


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and abs(out) != float("inf") else None


def _quad(values: Any, y_first: bool) -> tuple[float, float, float, float] | None:
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        return None
    nums = [_num(v) for v in values]
    if any(v is None for v in nums):
        return None
    a, b, c, d = nums  # type: ignore[misc]
    return (b, a, d, c) if y_first else (a, b, c, d)


def _raw_rect(obj: Any, depth: int = 0) -> tuple[float, float, float, float] | None:
    """Прямоугольник (x0, y0, x1, y1) в шкале ответа (доли, 0–1000 или пиксели — нормализуем потом).

    Формы: ``{x0,y0,x1,y1}`` (и xmin/…, left/top/…), ``{x, y, w, h}``, ``box_2d`` ``[ymin, xmin, ymax, xmax]``
    (норма Gemini), ``bbox``/``box`` списком ``[x0, y0, x1, y1]`` или объектом, голый список из четырёх чисел
    (порядок ``box_2d``), рамка вложенная в ``avatar``/``box``."""
    if depth > 3:
        return None
    if isinstance(obj, (list, tuple)):
        return _quad(obj, y_first=True)
    if not isinstance(obj, dict):
        return None
    for keys in _RECT_KEYS:
        if all(k in obj for k in keys):
            nums = [_num(obj[k]) for k in keys]
            if all(v is not None for v in nums):
                return tuple(nums)  # type: ignore[return-value]
    if all(k in obj for k in ("x", "y", "w", "h")):
        x, y, w, h = (_num(obj[k]) for k in ("x", "y", "w", "h"))
        if None not in (x, y, w, h):
            return (x, y, x + w, y + h)  # type: ignore[operator]
    for key in ("box_2d", "box2d", "bbox_2d"):
        quad = _quad(obj.get(key), y_first=True)
        if quad:
            return quad
    for key in BOX_KEYS:
        inner = obj.get(key)
        if isinstance(inner, (list, tuple)):
            quad = _quad(inner, y_first=False)
            if quad:
                return quad
        elif isinstance(inner, dict):
            found = _raw_rect(inner, depth + 1)
            if found:
                return found
    return None


def _first_of(obj: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        if obj.get(key) not in (None, "", [], {}):
            return obj[key]
    return None


def _says_no_avatar(item: Any) -> bool:
    """Запись кадра — явное «аватара нет»: null, {}, [], ``avatar: false``, строка «нет»."""
    if item in (None, {}, []):
        return True
    if isinstance(item, str):
        return item.strip().lower() in ("", "none", "null", "no", "нет", "-")
    if isinstance(item, dict):
        if any(item.get(k) is False for k in _NO_AVATAR_KEYS):
            return True
        meaningful = {k: v for k, v in item.items() if k not in ("frame", "index", "i", "n", "id", "t", "time")}
        return not meaningful or all(v in (None, {}, [], "") for v in meaningful.values())
    return False


def _frame_items(data: Any) -> list | None:
    """Список записей кадров из ответа: ``{"frames": [...]}`` (и boxes/results/…), голый массив, вложенный
    ``{"result": {"frames": [...]}}``, массив с одной обёрткой ``[{"frames": [...]}]``. Одна рамка без списка —
    ``[рамка]`` (на все кадры)."""
    for _ in range(3):
        if isinstance(data, list):
            if len(data) == 1 and isinstance(data[0], dict) and not _raw_rect(data[0]) \
                    and any(isinstance(data[0].get(k), list) for k in FRAME_LIST_KEYS):
                data = data[0]
                continue
            return data
        if not isinstance(data, dict):
            return None
        for key in FRAME_LIST_KEYS:
            if isinstance(data.get(key), list):
                return data[key]
        nested = [v for v in data.values() if isinstance(v, dict)]
        lists = [v for v in data.values() if isinstance(v, list) and v and all(isinstance(x, (dict, type(None))) for x in v)]
        if _raw_rect(data):
            return [data]
        if len(lists) == 1:
            return lists[0]
        if len(nested) == 1:
            data = nested[0]
            continue
        return None
    return None


def _ordered(items: list, n: int) -> list:
    """Записи по порядку кадров: у каждой номер ``frame``/``index`` (с 1 или с 0) — ставим по нему, иначе как есть."""
    marks = []
    for item in items:
        mark = _num(item.get("frame", item.get("index"))) if isinstance(item, dict) else None
        if mark is None or mark != int(mark):
            return items[:n]
        marks.append(int(mark))
    if len(set(marks)) != len(marks):
        return items[:n]
    base = 1 if min(marks) >= 1 and max(marks) <= n else 0 if min(marks) >= 0 and max(marks) < n else None
    if base is None:
        return items[:n]
    out: list = [None] * n
    for mark, item in zip(marks, items):
        out[mark - base] = item
    return out


def _scale_of(rects: list[tuple], size: tuple[int, int] | None) -> tuple[float, float] | None:
    """Делитель (по x, по y) для рамок не в долях: шкала 0–1000 (норма Gemini для box_2d) или пиксели кадра
    ``size`` (координаты больше 1000). Пиксели без размера кадра — None (не разобрать)."""
    big = [v for r in rects if max(r) > 1.5 for v in r]
    if not big:
        return (1.0, 1.0)
    if max(big) <= 1000.5:
        return (1000.0, 1000.0)
    if size and size[0] > 0 and size[1] > 0:
        return (float(size[0]), float(size[1]))
    return None


def _norm_rect(rect: tuple, scale: tuple[float, float]) -> dict:
    """В доли 0..1 (рамка уже в долях — как есть), углы по порядку."""
    sx, sy = (1.0, 1.0) if max(rect) <= 1.5 else scale
    x0, y0, x1, y1 = rect[0] / sx, rect[1] / sy, rect[2] / sx, rect[3] / sy
    return {"x0": min(x0, x1), "y0": min(y0, y1), "x1": max(x0, x1), "y1": max(y0, y1)}


def parse_frames(text: str, n: int, size: tuple[int, int] | None = None) -> tuple[list[dict | None], bool]:
    """Ответ Gemini → (рамка аватара на каждый из ``n`` кадров, разобран ли ответ).

    Разобран — если в ответе нашёлся список кадров (или одна рамка) и хоть одна запись — рамка или явное «аватара
    нет». ``False`` — пустой/не JSON/ни одной понятной записи: это сбой ответа, а не «аватара нет».
    ``size`` — (ширина, высота) кадров, что видела модель: для координат в пикселях."""
    try:
        data = _load_json(text)
    except ValueError:
        return [None] * n, False
    items = _frame_items(data)
    if items is None:
        return [None] * n, False
    # Одна рамка без списка кадров (старый ответ, в том числе в обёртке {"result": {...}}) — на все кадры.
    single = n > 1 and len(items) == 1 and not isinstance(data, list) and not any(
        isinstance(data, dict) and isinstance(data.get(k), list) for k in FRAME_LIST_KEYS)
    items = _ordered(items, n)
    raw: list[tuple[tuple, tuple | None, list[tuple]] | None] = []
    understood = 0
    for item in items:
        rect = _raw_rect(item)
        if rect is None:
            if _says_no_avatar(item):
                understood += 1
            raw.append(None)
            continue
        understood += 1
        face = ui = None
        if isinstance(item, dict):
            face_obj = _first_of(item, FACE_KEYS)
            face = _raw_rect(face_obj) if face_obj is not None else None
            ui_obj = _first_of(item, UI_KEYS)
            ui = [r for r in (_raw_rect(u) for u in ui_obj[:12]) if r] if isinstance(ui_obj, list) else None
        raw.append((rect, face, ui or []))
    if not understood:
        return [None] * n, False
    rects = [r for entry in raw if entry for r in [entry[0], *([entry[1]] if entry[1] else []), *entry[2]]]
    scale = _scale_of(rects, size)
    if scale is None:
        return [None] * n, False
    boxes: list[dict | None] = []
    for entry in raw:
        if entry is None:
            boxes.append(None)
            continue
        rect, face_raw, ui_raw = entry
        box = _box(_norm_rect(rect, scale))
        if box is not None and ui_raw:
            ui = [u for u in (_ui_box(_norm_rect(r, scale)) for r in ui_raw) if u]
            if ui:
                box["ui"] = ui
        if box is not None and face_raw:
            face = _ui_box(_norm_rect(face_raw, scale))
            if face and _inside(face, box):
                box["face"] = face
        boxes.append(box)
    if single:
        boxes = boxes[:1] * n  # одна рамка без списка кадров — на все кадры (лучше, чем ничего)
    return boxes[:n] + [None] * (n - len(boxes[:n])), True


def parse_boxes(text: str, n: int, size: tuple[int, int] | None = None) -> list[dict | None]:
    """Рамка аватара на каждый из ``n`` кадров (``parse_frames`` без признака разбора)."""
    return parse_frames(text, n, size)[0]


def _ui_box(data: Any) -> dict | None:
    """Окно чата/текста/интерфейса рядом с аватаром — строка чата бывает тонкой, порог меньше, чем у модели."""
    try:
        box = {k: min(1.0, max(0.0, float(data[k]))) for k in ("x0", "y0", "x1", "y1")}
    except (KeyError, TypeError, ValueError):
        return None
    return box if box["x1"] - box["x0"] >= 0.01 and box["y1"] - box["y0"] >= 0.01 else None


def _inside(inner: dict, outer: dict, eps: float = 0.02) -> bool:
    return (inner["x0"] >= outer["x0"] - eps and inner["y0"] >= outer["y0"] - eps
            and inner["x1"] <= outer["x1"] + eps and inner["y1"] <= outer["y1"] + eps)


MAX_MODEL_CUT = 0.3          # окно чата поверх края модели: срезать модель не больше этой доли её размера
MAX_MODEL_CUT_BLIND = 0.25   # то же, когда лицо неизвестно (кэш без face)
UI_WEIGHT = 3.0              # доля кадра под чатом в рамке «стоит» втрое дороже срезанной доли модели
UI_LEFT_MIN = 0.0005         # меньше этой площади (доли кадра) чата в рамке — не замечание
CHAT_MOSTLY_GONE = 0.25      # вариант без лишнего среза сверху, где чата от лучшего по цене осталось не больше доли


def _overlap(rect: tuple, box: dict) -> float:
    ix = min(rect[2], box["x1"]) - max(rect[0], box["x0"])
    iy = min(rect[3], box["y1"]) - max(rect[1], box["y0"])
    return ix * iy if ix > 0 and iy > 0 else 0.0


def trim_ui(cam: tuple[float, float, float, float], union: dict, ui: list[dict],
            face: dict | None = None) -> tuple[tuple, list[str]]:
    """Рамка с запасом ``cam`` (x0, y0, x1, y1) без окон чата/интерфейса ``ui``. → (рамка, срезанные стороны).

    QA 02.11 (клип 892): запас PAD захватил обрывки чата «SaZiann…» — рамка резалась до окна, но не глубже
    края модели. QA 3 (клип 895) — этого мало: чат в углу лежит ПОВЕРХ головы модели (окно до y 0.58 при
    верхе модели 0.46), и нижние сообщения оставались в кадре весь ролик. Теперь край может зайти и внутрь
    модели — не глубже ``MAX_MODEL_CUT`` её размера и не в лицо ``face`` (глаза и рот от Gemini): из
    вариантов краёв по окнам берётся тот, где чата в рамке меньше всего при самой малой потере модели
    (чат весит ``UI_WEIGHT``). Окно посреди модели (подпись на теле) срезать дороже — оно остаётся.
    Среди вариантов, что убирают чат не хуже лучшего, берётся тот, что меньше режет макушку: чат сбоку от
    головы убирается боковым краем, а не срезом сверху до лба (QA 4, клип 896).

    QA 5 (клип 897): край по окну чата встал в 0.02 от бокса лица (глаза и рот) — в кадре срезан правый глаз.
    Инвариант: рамка не заходит в ``face_zone`` (лицо с запасом ``FACE_MARGIN_*``); край можно поставить на
    сам край этой зоны — чат срезается хотя бы частично, остаток назван в ``ui_left``."""
    x0, y0, x1, y1 = cam
    wins: list[dict] = []
    for u in ui:
        key = {k: round(float(u[k]), 3) for k in ("x0", "y0", "x1", "y1")}
        if _overlap(cam, key) > 0 and key not in wins:
            wins.append(key)
    if not wins:
        return cam, []
    w, h = union["x1"] - union["x0"], union["y1"] - union["y0"]
    limit = MAX_MODEL_CUT if face else MAX_MODEL_CUT_BLIND
    zone = face_zone(face) if face else None
    # Края-кандидаты: по окнам чата и по краю зоны лица (дальше в лицо — нельзя).
    top_c = [u["y1"] for u in wins] + ([zone["y0"]] if zone else [])
    bottom_c = [u["y0"] for u in wins] + ([zone["y1"]] if zone else [])
    left_c = [u["x1"] for u in wins] + ([zone["x0"]] if zone else [])
    right_c = [u["x0"] for u in wins] + ([zone["x1"]] if zone else [])
    tops = {y0} | {v for v in top_c if y0 < v <= union["y0"] + limit * h and (zone is None or v <= zone["y0"])}
    bottoms = {y1} | {v for v in bottom_c if y1 > v >= union["y1"] - limit * h and (zone is None or v >= zone["y1"])}
    lefts = {x0} | {v for v in left_c if x0 < v <= union["x0"] + limit * w and (zone is None or v <= zone["x0"])}
    rights = {x1} | {v for v in right_c if x1 > v >= union["x1"] - limit * w and (zone is None or v >= zone["x1"])}
    model = w * h
    variants = []
    for t in sorted(tops):
        for b in sorted(bottoms):
            for left in sorted(lefts):
                for r in sorted(rights):
                    rect = (left, t, r, b)
                    chat = sum(_overlap(rect, u) for u in wins)
                    loss = model - _overlap(rect, union)
                    variants.append(((UI_WEIGHT * chat + loss, loss), chat, rect))
    best_cost, best_chat, best = min(variants)
    # QA 5: край по зоне лица убирает чат не целиком — при той же глубине среза сверху берём вариант, где чата
    # заметно меньше (чат сбоку от головы — боковым краем до зоны лица, а не только запасом сверху).
    intrusion = max(0.0, best[1] - union["y0"])
    least = min((chat, cost, rect) for cost, chat, rect in variants
                if max(0.0, rect[1] - union["y0"]) <= intrusion + 1e-9)
    if least[0] < best_chat - UI_LEFT_MIN and least[0] <= CHAT_MOSTLY_GONE * best_chat:
        best_chat, best = least[0], least[2]
    # QA 4 (клип 896): чат сбоку от головы — рамка резалась сверху до лба (рожки и волосы долой), хотя убрать
    # чат можно было боковым краем. Из вариантов, что убирают чат не хуже лучшего, берём тот, что меньше
    # заходит сверху в модель (макушку держим), и только потом — по прежней цене.
    head = [(max(0.0, rect[1] - union["y0"]), cost, rect) for cost, chat, rect in variants
            if chat <= best_chat + UI_LEFT_MIN]
    best = min(head)[2] if head else best
    sides = [name for name, before, after in (("top", y0, best[1]), ("bottom", y1, best[3]),
                                              ("left", x0, best[0]), ("right", x1, best[2])) if before != after]
    return best, sides


def ui_left(cam: dict, ui: list[dict]) -> list[dict]:
    """Окна чата/интерфейса, что остались в рамке ``cam`` (доли исходника) заметной площадью."""
    rect = (cam["x"], cam["y"], cam["x"] + cam["w"], cam["y"] + cam["h"])
    out: list[dict] = []
    for u in ui:
        box = {k: round(float(u[k]), 3) for k in ("x0", "y0", "x1", "y1")}
        if _overlap(rect, box) >= UI_LEFT_MIN and box not in out:
            out.append(box)
    return out


def _iou(a: dict, b: dict) -> float:
    ix = max(0.0, min(a["x1"], b["x1"]) - max(a["x0"], b["x0"]))
    iy = max(0.0, min(a["y1"], b["y1"]) - max(a["y0"], b["y0"]))
    inter = ix * iy
    union = (a["x1"] - a["x0"]) * (a["y1"] - a["y0"]) + (b["x1"] - b["x0"]) * (b["y1"] - b["y0"]) - inter
    return inter / union if union > 0 else 0.0


HEAD_ABOVE_FACE = 1.5  # макушка с волосами и рожками — не ниже стольких высот лица над его верхом
FACE_GUESS = (0.2, 0.15, 0.8, 0.65)  # лицо внутри рамки модели, когда Gemini его не дал (доли рамки)
# Запас вокруг бокса лица (глаза и рот от Gemini), который рамка аватара не режет никогда (QA 5, клип 897):
# доли ширины лица с боков, высоты — сверху и снизу.
FACE_MARGIN_X = 0.25
FACE_MARGIN_TOP = 0.5
FACE_MARGIN_BOTTOM = 0.25


def face_zone(face: dict) -> dict:
    """Лицо с обязательным запасом (доли исходника, в пределах кадра): рамка аватара всегда содержит его
    целиком — никакая обрезка чата/интерфейса и никакая крупность этого не нарушают."""
    x0, y0, x1, y1 = (float(face[k]) for k in ("x0", "y0", "x1", "y1"))
    fw, fh = max(0.0, x1 - x0), max(0.0, y1 - y0)
    return {"x0": round(max(0.0, x0 - FACE_MARGIN_X * fw), 4), "y0": round(max(0.0, y0 - FACE_MARGIN_TOP * fh), 4),
            "x1": round(min(1.0, x1 + FACE_MARGIN_X * fw), 4), "y1": round(min(1.0, y1 + FACE_MARGIN_BOTTOM * fh), 4)}


def face_of(box: dict, face: dict | None = None) -> dict:
    """Лицо аватара (доли исходника): от Gemini или с запасом — середина верхней части рамки модели
    (у чиби голова — полмодели, у вебки — верх кадра)."""
    if isinstance(face, dict) and all(k in face for k in ("x0", "y0", "x1", "y1")):
        return {k: float(face[k]) for k in ("x0", "y0", "x1", "y1")}
    w, h = float(box["x1"]) - float(box["x0"]), float(box["y1"]) - float(box["y0"])
    gx0, gy0, gx1, gy1 = FACE_GUESS
    return {"x0": float(box["x0"]) + w * gx0, "y0": float(box["y0"]) + h * gy0,
            "x1": float(box["x0"]) + w * gx1, "y1": float(box["y0"]) + h * gy1}


def _union(boxes: list[dict]) -> dict:
    return {"x0": min(b["x0"] for b in boxes), "y0": min(b["y0"] for b in boxes),
            "x1": max(b["x1"] for b in boxes), "y1": max(b["y1"] for b in boxes)}


def _cam_of(group: list[dict]) -> tuple[dict, dict, dict]:
    """Рамки одного места → (объединение, cam с запасом PAD без окон чата, ``{face?, ui_left?}``) в долях
    кадра исходника."""
    union = _union(group)
    faces = [b["face"] for b in group if isinstance(b.get("face"), dict)]
    face = _union(faces) if faces else None
    if face:
        # QA 4 (клип 896): рамка модели от Gemini кончалась над глазами (y0 0.32 при макушке 0.03 и рожках ≈0.1) —
        # верх головы срезан. Над лицом (глаза и рот) у модели волосы, рожки, уши — не меньше HEAD_ABOVE_FACE его
        # высот: верх рамки не ниже этого.
        union["y0"] = max(0.0, min(union["y0"], face["y0"] - HEAD_ABOVE_FACE * (face["y1"] - face["y0"])))
    w, h = union["x1"] - union["x0"], union["y1"] - union["y0"]
    x0, y0 = max(0.0, union["x0"] - w * PAD), max(0.0, union["y0"] - h * PAD)
    x1, y1 = min(1.0, union["x1"] + w * PAD), min(1.0, union["y1"] + h * PAD)
    ui = [u for b in group for u in b.get("ui") or []]
    if ui:
        (x0, y0, x1, y1), _sides = trim_ui((x0, y0, x1, y1), union, ui, face)
    if face:
        # Инвариант (QA 5, клип 897): лицо с запасом — всегда в рамке, даже если запас PAD до него не дотянул.
        zone = face_zone(face)
        x0, y0, x1, y1 = min(x0, zone["x0"]), min(y0, zone["y0"]), max(x1, zone["x1"]), max(y1, zone["y1"])
    cam = {"x": round(x0, 4), "y": round(y0, 4), "w": round(x1 - x0, 4), "h": round(y1 - y0, 4)}
    extra: dict = {}
    if face:
        extra["face"] = {k: round(v, 4) for k, v in face.items()}
    left = ui_left(cam, ui) if ui else []
    if left:
        extra["ui_left"] = left
    return {k: round(v, 4) for k, v in union.items()}, cam, extra


def place_avatar(boxes: list[dict | None], times: list[float] | None = None) -> dict:
    """Рамки по кадрам → ``{cam, box, frames_with_avatar, moves, place_count, places}``.

    ``cam`` — место, где аватар чаще всего (объединение рамок этого места + запас PAD), в долях
    кадра исходника. ``places`` — ВСЕ места по времени: ``[{place, cam, box, frames, src_from,
    src_to}]`` подряд идущими отрезками (место 0 — самое частое); с ``times`` (секунды исходника
    кадров) граница двух отрезков — посередине между их соседними кадрами (detect_avatar уточняет
    её склейкой плана). QA 02.11 (клип 892): вернули одну рамку, и 4 с клипа были без лица."""
    found = [(i, b) for i, b in enumerate(boxes) if b]
    if not found:
        return {"cam": None, "box": None, "frames_with_avatar": 0, "moves": False, "place_count": 0, "places": []}
    groups: list[list[tuple[int, dict]]] = []
    for item in found:
        for group in groups:
            if _iou(group[0][1], item[1]) >= SAME_PLACE_IOU:
                group.append(item)
                break
        else:
            groups.append([item])
    groups.sort(key=lambda g: (-len(g), g[0][0]))  # место 0 — самое частое, при равенстве — раньше
    looks = [_cam_of([b for _i, b in group]) for group in groups]
    place_of = {i: g for g, group in enumerate(groups) for i, _b in group}
    runs: list[dict] = []
    for i, _box in found:
        g = place_of[i]
        if runs and runs[-1]["place"] == g:
            runs[-1]["frames"] += 1
            runs[-1]["last"] = i
            continue
        runs.append({"place": g, "first": i, "last": i, "frames": 1})
    timed = times is not None and len(times) >= len(boxes)
    places = []
    for k, run in enumerate(runs):
        box, cam, extra = looks[run["place"]]
        place = {"place": run["place"], "cam": cam, "box": box, "frames": run["frames"], **extra}
        if timed:
            place["src_from"] = round(float(times[run["first"]]) if k == 0 else
                                      (float(times[runs[k - 1]["last"]]) + float(times[run["first"]])) / 2, 3)
            place["src_to"] = round(float(times[run["last"]]) if k == len(runs) - 1 else
                                    (float(times[run["last"]]) + float(times[runs[k + 1]["first"]])) / 2, 3)
            place["frame_times"] = [round(float(times[i]), 3) for i, _b in found if run["first"] <= i <= run["last"]]
        places.append(place)
    box, cam, extra = looks[0]
    return {"cam": cam, "box": box, **extra, "frames_with_avatar": len(found), "moves": len(groups) > 1,
            "place_count": len(groups), "places": places}


def refine_boundaries(places: list[dict], cuts: list[float]) -> list[dict]:
    """Граница двух отрезков мест — на склейку плана (секунды исходника) между их соседними кадрами,
    ближайшую к середине; склейки нет — середина как была."""
    out = [dict(p) for p in places]
    for left, right in zip(out, out[1:]):
        lo = (left.get("frame_times") or [left.get("src_to")])[-1]
        hi = (right.get("frame_times") or [right.get("src_from")])[0]
        if lo is None or hi is None:
            continue
        inside = [float(c) for c in cuts if float(lo) < float(c) < float(hi)]
        if inside:
            mid = (float(lo) + float(hi)) / 2
            at = round(min(inside, key=lambda c: abs(c - mid)), 3)
            left["src_to"], right["src_from"] = at, at
            right["from_shot_cut"] = True
    return out


def settle_transitions(places: list[dict], finder: Any) -> list[dict]:
    """Граница мест по самой смене сцены: ``finder(lo, hi, hint)`` → ``{start, end, mid, hard}``
    (``scene_cuts.transition_span``) между соседними кадрами мест; ``hint`` — склейка из
    ``refine_boundaries``.

    QA 02.11 (клип 895): в стриме сцена сменилась наплывом ≈0.4 с, граница встала на его конец
    (склейку ffmpeg) — рамка старого места 0.35 с показывала ютуб. Жёсткая склейка — граница на первом
    кадре новой сцены; наплыв — на его середине: каждая рамка видит чужую сцену не больше чем
    наполовину прозрачной и только половину наплыва (кросс-фейд рамок дал бы не меньше чужого)."""
    out = [dict(p) for p in places]
    for left, right in zip(out, out[1:]):
        lo = (left.get("frame_times") or [left.get("src_to")])[-1]
        hi = (right.get("frame_times") or [right.get("src_from")])[0]
        if lo is None or hi is None or float(hi) <= float(lo):
            continue
        hint = float(right["src_from"]) if right.get("from_shot_cut") and right.get("src_from") is not None else None
        try:
            span = finder(float(lo), float(hi), hint)
        except Exception:  # noqa: BLE001 - не нашли — граница остаётся, где была
            span = None
        if not span or not (float(lo) < float(span["start"]) <= float(span["end"]) < float(hi)):
            continue
        at = round(float(span["start"] if span.get("hard") else span["mid"]), 3)
        left["src_to"], right["src_from"] = at, at
        right["from_shot_cut"] = True
        if not span.get("hard"):
            right["transition"] = {"start": float(span["start"]), "end": float(span["end"])}
    return out


HOLD_PAD_SEC = 0.04      # стоп-кадр на наплыве — с запасом в кадр (1/30 с) по обе стороны
HOLD_SNAP_SEC = 0.6      # граница рамок руками ближе этого к наплыву — тот же стоп-кадр
HOLD_MAX_SEC = 1.5       # наплыв длиннее — не замораживаем (это уже не смена сцены, а монтаж стрима)


def hold_for(transition: Any) -> tuple[float, float] | None:
    """Наплыв ``{start, end}`` (секунды исходника) → (с какой секунды стоп-кадр, где граница рамок) или None."""
    if not isinstance(transition, dict):
        return None
    try:
        start, end = float(transition["start"]), float(transition["end"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 < end - start <= HOLD_MAX_SEC:
        return None
    return round(start - HOLD_PAD_SEC, 3), round(end + HOLD_PAD_SEC, 3)


def cams_from_places(places: list[dict]) -> list[dict]:
    """Места аватара → ``layout.cams`` ``[{from, to, cam, freeze_from?}]``.

    QA 5 (клип 897): граница мест на середине наплыва — всё равно ≈0.2 с полупрозрачного ютуба в рамке
    (каждая рамка видит чужую сцену половину наплыва). Теперь на наплыве ни одного кадра с чужой сценой:
    последний чистый кадр старой сцены стоит стоп-кадром до конца наплыва (``freeze_from`` у записи нового
    места — с этой секунды исходника и до её ``from``), рамка нового места — с первого чистого кадра новой."""
    cams = [{"from": p["src_from"], "to": p["src_to"], "cam": p["cam"]} for p in places]
    for left, right, place in zip(cams, cams[1:], places[1:]):
        hold = hold_for(place.get("transition"))
        if hold and float(left["from"]) < hold[0] and hold[1] < float(right["to"]):
            right["freeze_from"], right["from"] = hold
            left["to"] = hold[1]
    return cams


def snap_holds(cams: list[dict], places: list[dict]) -> list[dict]:
    """Рамки по отрезкам руками (``cams`` с from/to в секундах исходника): граница рядом с наплывом, который
    нашёл detect_avatar, — на его конец со стоп-кадром (как ``cams_from_places``)."""
    holds = [h for h in (hold_for(p.get("transition")) for p in places) if h]
    out = [dict(c) for c in cams]
    if not holds:
        return out
    ordered = sorted(range(len(out)), key=lambda i: float(out[i].get("from") or 0))
    for li, ri in zip(ordered, ordered[1:]):
        left, right = out[li], out[ri]
        try:
            at = float(right["from"])
        except (KeyError, TypeError, ValueError):
            continue
        for freeze, end in holds:
            if freeze - HOLD_SNAP_SEC <= at <= end + HOLD_SNAP_SEC and float(left["from"]) < freeze \
                    and end < float(right.get("to") or end + 1):
                right["freeze_from"], right["from"] = freeze, end
                left["to"] = end
                break
    return out


def _boundary_cuts(store: Any, source: dict, local: str, places: list[dict]) -> list[float]:
    """Склейки планов исходника между кадрами соседних мест: кэш источника (get_shots), иначе
    ffmpeg только по этим окнам (секунды, не весь стрим)."""
    windows = []
    for left, right in zip(places, places[1:]):
        lo = (left.get("frame_times") or [left.get("src_to")])[-1]
        hi = (right.get("frame_times") or [right.get("src_from")])[0]
        if lo is not None and hi is not None and float(hi) > float(lo):
            windows.append((float(lo), float(hi)))
    if not windows:
        return []
    cached = source.get("scene_cuts")
    if cached is None:
        try:
            from app.store import _parse_transcript

            row = store.db.query_one("SELECT scene_cuts_json FROM sources WHERE id = ?", (int(source["id"]),))
            cached = _parse_transcript(row["scene_cuts_json"]) if row else None
        except Exception:  # noqa: BLE001 - нет кэша — посчитаем по окнам
            cached = None
    if cached:
        return [float(c) for c in cached]
    from app.scene_cuts import detect_scene_cuts

    out: list[float] = []
    for lo, hi in windows:
        if hi - lo <= MAX_CUT_WINDOW_SEC:
            out += detect_scene_cuts(local, start=lo, duration=hi - lo)
    return out


def _rect_schema() -> dict:
    return {"type": "object", "properties": {k: {"type": "number"} for k in ("x0", "y0", "x1", "y1")},
            "required": ["x0", "y0", "x1", "y1"]}


def response_schema() -> dict:
    """Жёсткая форма ответа (``responseSchema``): объект ``frames`` с записью на каждый кадр — номер, есть ли аватар,
    рамки в долях кадра. Кадр без аватара — ``avatar: false`` без рамки."""
    rect = {k: {"type": "number"} for k in ("x0", "y0", "x1", "y1")}
    return {
        "type": "object",
        "properties": {"frames": {"type": "array", "items": {
            "type": "object",
            "properties": {"frame": {"type": "integer"}, "avatar": {"type": "boolean"}, **rect,
                           "face": _rect_schema(), "ui": {"type": "array", "items": _rect_schema()}},
            "required": ["frame", "avatar"]}}},
        "required": ["frames"],
    }


ASK_ATTEMPTS = 2  # пустой/неразобранный ответ — один повтор запроса


def response_text(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    candidates = response.get("candidates") or [{}]
    first = candidates[0] if isinstance(candidates[0], dict) else {}
    return "".join(str(p.get("text") or "") for p in (first.get("content") or {}).get("parts") or []
                   if isinstance(p, dict) and not p.get("thought"))


def sample_size(source: dict) -> tuple[int, int] | None:
    """Размер кадров, что видит модель (``_grab``: ширина SAMPLE_W, высота по пропорции исходника)."""
    try:
        w, h = float(source.get("width") or 0), float(source.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return SAMPLE_W, int(round(SAMPLE_W * h / w / 2) * 2)


def ask_boxes(client: Any, parts: list[dict], size: tuple[int, int] | None = None) -> list[dict | None]:
    """Рамки аватара по кадрам ``parts`` (inlineData) от Gemini: запрос с жёсткой схемой ответа, при пустом или
    неразобранном ответе — ещё один. Не вышло и со второго раза — ``AvatarAnswerError`` (сбой, а не «аватара нет»)."""
    payload = {"contents": [{"role": "user", "parts": [{"text": PROMPT.format(n=len(parts))}, *parts]}],
               "generationConfig": {"responseMimeType": "application/json", "temperature": 0,
                                    "responseSchema": response_schema()}}
    problem = "пустой ответ"
    for _attempt in range(ASK_ATTEMPTS):
        try:
            response = client.generate_content(settings.gemini_text_model, payload)
        except RuntimeError as exc:
            if "responseSchema" in str(exc) or "response_schema" in str(exc):
                # Старый эндпоинт без схемы — повторяем без неё (формат держит PROMPT и разбор).
                payload = {**payload, "generationConfig": {k: v for k, v in payload["generationConfig"].items()
                                                           if k != "responseSchema"}}
            problem = f"Gemini не ответил: {str(exc)[:160]}"
            continue
        text = response_text(response)
        boxes, ok = parse_frames(text, len(parts), size)
        if ok:
            return boxes
        problem = "пустой ответ" if not text.strip() else f"ответ не разобран: {text.strip()[:120]!r}"
    raise AvatarAnswerError(f"{problem} (попыток {ASK_ATTEMPTS})")


def detect_avatar(store: Any, clip_plan_id: int, client: Any = None, *, refresh: bool = False) -> dict:
    """Аватар стримера на кусках клипа: ``{cam, box, frames, frames_with_avatar, moves, places, times}``.
    ``cam`` None — Gemini ответил, что аватара на кадрах нет. Ответ не разобран/модель не ответила и после повтора —
    ``AvatarAnswerError`` (в кэш не пишется). Нет файла/кусков/ключа — ``ValueError``. Кэш по составу кусков."""
    plan = store.get_clip_plan(int(clip_plan_id))
    cache = _cache_path(clip_plan_id, plan)
    if not refresh and cache.exists():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return refresh_geometry(data)
        except (OSError, ValueError):
            pass
    source = store.get_source(int(plan["source_id"]))
    local = str(source.get("local_path") or "")
    if not local or not Path(local).exists():
        raise ValueError("у источника нет файла видео")
    times = source_times(plan)
    if not times:
        raise ValueError("в клипе нет кусков")
    if client is None:
        if not settings.gemini_api_key:
            raise ValueError("нужна Gemini (GEMINI_API_KEY) — без неё аватар не найти; задай cam руками")
        from app.ai.gemini import GeminiClient

        client = GeminiClient()
    parts: list[dict] = []
    used: list[float] = []
    with tempfile.TemporaryDirectory() as folder:
        for i, t in enumerate(times):
            data = _grab(local, t, Path(folder) / f"a{i}.jpg")
            if data:
                used.append(t)
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": base64.b64encode(data).decode()}})
    if not parts:
        raise ValueError("ffmpeg не отдал ни одного кадра исходника")
    boxes = ask_boxes(client, parts, sample_size(source))
    found = place_avatar(boxes, used)
    places = found["places"]
    if len(places) > 1:
        try:
            places = refine_boundaries(places, _boundary_cuts(store, source, local, places))
        except Exception:  # noqa: BLE001 - склейку не нашли — граница посередине между кадрами
            pass
        from app.scene_cuts import transition_span

        # Наплыв ffmpeg-склейка ловит только концом (или не ловит совсем) — граница по самой смене.
        places = settle_transitions(places, lambda lo, hi, hint: transition_span(local, lo, hi, hint=hint))
    spans = [(float(s["start_sec"]), float(s["end_sec"])) for s in plan.get("segments") or []]
    if places and spans:
        # Первое и последнее место — до краёв клипа (кадры берутся не с самых краёв).
        places[0]["src_from"] = round(min(a for a, _b in spans), 3)
        places[-1]["src_to"] = round(max(b for _a, b in spans), 3)
    out = {**found, "places": places, "frames": len(parts), "times": used,
           "per_frame": [b for b in boxes]}
    try:
        cache.write_text(json.dumps(out), encoding="utf-8")
    except OSError:
        pass
    return out


def avatar_center_in_window(cam: dict, x: float, window_w: float) -> bool:
    """Центр аватара (доли исходника) внутри окна шириной ``window_w`` с центром ``x``."""
    cx = float(cam["x"]) + float(cam["w"]) / 2
    return x - window_w / 2 - 1e-6 <= cx <= x + window_w / 2 + 1e-6
