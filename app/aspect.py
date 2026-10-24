"""Перекомпоновка монтажа между форматами: слои и камера из одного опорного кадра в другой.

Форматы и их размеры — app/formats.py (9:16, 1:1, 4:5, 16:9). Слои размечены в опорном
кадре формата ``layers_format`` (нет — 9:16, 1080×1920; docs/motion/spec.md): ``x``,
``y`` — доли кадра, размеры — опорные пиксели (движок умножает их на ширину кадра /
ширину опорного). В другом кадре те же доли и размеры не влезают: телефон высотой
1350 px занял бы весь квадрат, а титр сверху наехал бы на него. Ниже «исходный» опорный
кадр — ``ref`` (sw×sh), новый — ``width``×``height`` (опорный кадр нужного формата).

Правило перекомпоновки — один масштаб на весь монтаж («зум по содержимому»):

1. Рамка слоя — от верха до низа (опорные px, обрезано по кадру 0..sh; учитываются
   ``y`` слоя и его ключи ``y``, масштаб — ``scale`` и последний ключ ``scale``), и так
   же слева направо. Фоновые слои во всю ширину (≥ 98 % sw) и вспышки (ключи
   прозрачности кончаются нулём: взрыв звёздочек, кольцо касания) в рамки не входят.
2. Сцены — слои, что идут вместе (перекрываются по времени ≥ 0.6 с; короткий нахлёст
   свайпа или ухода телефона под новый титр сцены не склеивает). Рамка сцены — общая
   рамка её слоёв; соседние сцены с почти той же рамкой (± 10 % кадра) — одна сцена,
   чтобы телефон, стоящий весь ролик на месте, не прыгал на сменах титров.
3. Масштаб ``s`` = min(1, 1.25 · H/sh, по ширине, H · (1 − 2·4 %) / высота самой высокой
   сцены): каждая сцена целиком входит в новый кадр с полями 4 %, но не крупнее, чем
   1.25 от «вписать весь кадр» (холст с одним титром не раздувается). «По ширине» —
   W/sw, а если новый кадр уже исходного (16:9 → 9:16) — сколько позволяет самая
   широкая сцена (не меньше W/sw и не больше 1.25 · W/sw). Масштаб один на весь
   ролик — размеры между сценами не прыгают.
4. Каждый слой: центр рамки его сцены → центр кадра, ``x``/``y`` и их ключи — тем же
   аффинным переносом, ``scale`` и ключи ``scale`` × ``s``. По горизонтали центр сцены
   берётся только когда новый кадр уже исходного; иначе — центр исходного кадра
   (вертикаль → квадрат/16:9: середина остаётся серединой). Внутри сцены взаимное
   расположение не меняется вовсе: титр не наезжает на телефон, если не наезжал,
   касания по экрану телефона остаются на своих кнопках; концовка с логотипом встаёт
   по центру кадра.
5. Фон во всю ширину не уменьшается: как был во всю ширину, так и остаётся (в кадре шире
   исходного — растягивается до новой ширины: у фигуры растёт ``props.w``, у картинки —
   масштаб; фон на весь кадр — «cover» и по высоте). Переносится только его центр (по
   сцене, с которой он дольше всего идёт) — затемнение под титрами остаётся под ними.
6. Камера (``render.camera``: zoom и центр x/y — доли кадра) перекомпонуется вместе со
   слоями (``reframe_camera``): центр наезда — та же точка содержимого (перенос сцены),
   а слои ``followCamera`` (телефон, кольца касаний), которые камера зумит вместе с
   видео, при наезде целиком остаются в кадре с полями 4 %. Для этого масштаб ``s``
   учитывает самый сильный наезд (содержимое под камерой × zoom входит в кадр), но
   из-за камеры уменьшается не больше чем в CAMERA_MAX_SHRINK раз; что не влезло —
   ограничивает zoom ключа. Центр ключа сдвигается ровно настолько, чтобы зумленное
   содержимое не вышло за поля: наезд остаётся наездом, а низ телефона не уходит за
   край кадра.

Из 9:16 в 9:16 (и вообще в тот же опорный кадр) слои и камера идут как есть. Для
вертикального исходного кадра в 1:1 / 4:5 результат ровно прежний (до 16:9).

Ограничение этапа 1: перекомпоновка 9:16 → 16:9 ставит монтаж колонкой по центру
(масштаб по высоте), а не раскладывает сцены по горизонтали.
"""

from __future__ import annotations

import math
from typing import Any

from app.formats import (
    DEFAULT_FORMAT,
    FORMATS,
    frame_size,
    normalize_format,
    settings_format,
)

REF_W = 1080
REF_H = 1920
REF = (REF_W, REF_H)
DEFAULT_ASPECT = DEFAULT_FORMAT
# Кадр каждого формата (app/formats.py). Старое имя: раньше тут были только 9:16 / 1:1 / 4:5.
ASPECTS: dict[str, tuple[int, int]] = {fmt: info["frame"] for fmt, info in FORMATS.items()}
# Не крупнее, чем «вписать весь опорный кадр» × столько.
MAX_BOOST = 1.25
# Поля сверху и снизу нового кадра (доля высоты).
MARGIN = 0.04
# Слой шире этой доли ширины опорного кадра — фон во всю ширину (1060 px у 1080).
FULL_BLEED_FRAC = 1060.0 / 1080.0
FULL_BLEED_W = FULL_BLEED_FRAC * REF_W
# Слои одной сцены перекрываются по времени хотя бы на столько (с); короче — переход.
SCENE_LINK_SEC = 0.6
# Соседние сцены, у которых верх и низ рамки ближе этой доли высоты кадра, — одна сцена.
SCENE_SIMILAR_FRAC = 0.10
SCENE_SIMILAR = SCENE_SIMILAR_FRAC * REF_H
# Нет слоёв — рамка = безопасная зона текста (10 %..80 % кадра).
DEFAULT_BOX_FRAC = (0.10, 0.80)
DEFAULT_BOX = (DEFAULT_BOX_FRAC[0] * REF_H, DEFAULT_BOX_FRAC[1] * REF_H)
# Фон «на весь кадр» — высотой не меньше этой доли опорного кадра.
COVER_FRAC = 0.98
# Пропорции экрана телефона по умолчанию (запись экрана 9:19.5) и корпус вокруг него.
PHONE_SCREEN_RATIO = 19.5 / 9
PHONE_EDGE = 0.047  # обод + рамка с каждой стороны, доля ширины экрана (device.ts)
# Средняя ширина знака в долях кегля (жирный кириллический текст).
GLYPH_W = 0.58
# Наезд камеры может уменьшить общий масштаб не больше чем во столько раз (дальше —
# ограничивается zoom ключа): иначе один наезд ×2 сделал бы мелким весь ролик.
CAMERA_MAX_SHRINK = 1.25
# Шаг проверки содержимого под камерой, пока она стоит (ключи с тем же видом), с.
CAMERA_HOLD_STEP = 0.1


def normalize_aspect(value: Any) -> str:
    """Формат кадра (app/formats.normalize_format): '9:16' | '1:1' | '4:5' | '16:9'; пусто — 9:16."""
    return normalize_format(value)


def settings_aspect(value: Any) -> str:
    """Для настроек клипа: мусор — 9:16 (нормализация настроек не падает)."""
    return settings_format(value)


def aspect_size(aspect: Any) -> tuple[int, int]:
    return frame_size(aspect)


def check_aspect_source(aspect: Any, source: dict | None = None) -> str:
    """Формат для рендера этого источника. Раньше не 9:16 был только у холста; теперь
    любой формат у любого клипа — остаётся проверка имени (ValueError на мусор)."""
    return normalize_format(aspect)


def _ref(ref: Any) -> tuple[float, float]:
    if not ref:
        return float(REF_W), float(REF_H)
    return float(ref[0]), float(ref[1])


# ---------------------------------------------------------------- размеры слоёв


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _text_lines(text: str, size: float, wrap: float, letter: float = 0.0, upper: bool = False) -> int:
    """Сколько строк займёт текст: явные переносы и перенос по ширине (оценка)."""
    glyph = size * GLYPH_W * (1.08 if upper else 1.0) + letter
    lines = 0
    for par in str(text or "").split("\n"):
        width = len(par) * glyph
        lines += max(1, math.ceil(width / max(40.0, wrap)))
    return max(1, lines)


def _counter_text(props: dict) -> str:
    to = _num(props.get("to"), 100)
    decimals = int(_num(props.get("decimals"), 0))
    return f"{props.get('prefix') or ''}{to:,.{decimals}f}{props.get('suffix') or ''}"


def _picture_size(layer: dict, sizes: dict[str, tuple[int, int]] | None) -> tuple[float, float]:
    """Ширина и высота картинки/видео (с корпусом телефона) в опорных px до scale."""
    from app.motion import asset_key

    props = layer.get("props") or {}
    w = _num(props.get("w"), 600)
    natural = (sizes or {}).get(asset_key(props.get("asset_id") or props.get("src")))
    crop = props.get("crop") if isinstance(props.get("crop"), dict) else None
    ratio = None
    if natural and natural[0] > 0 and natural[1] > 0:
        ratio = natural[1] / natural[0]
        if crop:
            ratio *= _num(crop.get("h"), 1) / max(0.01, _num(crop.get("w"), 1))
    phone = props.get("device") == "phone"
    if ratio is None:
        ratio = PHONE_SCREEN_RATIO if phone else 1.0
    h = w * ratio
    if phone:
        return w * (1 + 2 * PHONE_EDGE), h + 2 * PHONE_EDGE * w
    border = _num((props.get("border") or {}).get("width"), 0) if isinstance(props.get("border"), dict) else 0
    return w + 2 * border, h + 2 * border


def layer_size(layer: dict, sizes: dict[str, tuple[int, int]] | None = None) -> tuple[float, float] | None:
    """Оценка ширины и высоты слоя в опорных px (без scale); None — звук и прочее без места."""
    kind = layer.get("type")
    props = layer.get("props") or {}
    if kind in ("text", "counter"):
        size = _num(props.get("size"), 96)
        wrap = _num(props.get("boxWidth"), 0) or _num(props.get("maxWidth"), 920)
        text = props.get("text", "") if kind == "text" else _counter_text(props)
        lines = _text_lines(text, size, wrap, _num(props.get("letterSpacing"), 0), bool(props.get("uppercase")))
        bg = props.get("bg") if isinstance(props.get("bg"), dict) else None
        pad_y = _num(bg.get("padY"), 12) if bg else 0.0
        pad_x = _num(bg.get("padX"), 24) if bg else 0.0
        longest = max((len(p) for p in str(text).split("\n")), default=0) * size * GLYPH_W
        return min(wrap, longest) + 2 * pad_x, lines * size * _num(props.get("lineHeight"), 1.15) + 2 * pad_y
    if kind == "emoji":
        size = _num(props.get("size"), 160)
        return size, size
    if kind in ("image", "video"):
        return _picture_size(layer, sizes)
    if kind == "pngtuber":
        # Аватар: ширина w, высота — по пропорции idle (как reframe.ts layerSize).
        from app.motion import asset_key

        w = _num(props.get("w"), 300)
        states = props.get("states") if isinstance(props.get("states"), dict) else {}
        natural = (sizes or {}).get(asset_key(states.get("idle")))
        return w, (w * natural[1] / natural[0] if natural and natural[0] > 0 and natural[1] > 0 else w)
    if kind in ("shape", "progress", "chart"):
        return _num(props.get("w"), 400), _num(props.get("h"), 400 if kind != "progress" else 40)
    if kind == "callout":
        return _num(props.get("w"), 900), 240.0 if props.get("subtitle") else 170.0
    if kind == "captions":
        size = _num(props.get("size"), 88)
        lines = _num(props.get("maxLines"), 2)
        return _num(props.get("maxWidth"), 900), lines * size * _num(props.get("lineHeight"), 1.12) + 40
    if kind == "chat":
        return _num(props.get("w"), 760), _num(props.get("maxMessages"), 6) * _num(props.get("fontSize"), 34) * 1.9
    return None


def _settled_scale(layer: dict) -> float:
    """Масштаб слоя «в покое»: scale × последний ключ scale (наезды и pop кончаются им)."""
    base = _num(layer.get("scale"), 1.0)
    keys = (layer.get("keys") or {}).get("scale") or []
    if keys:
        last = max(keys, key=lambda k: _num(k.get("t"), 0))
        base *= _num(last.get("v"), 1.0)
    return max(0.0, base)


def is_transient(layer: dict) -> bool:
    """Вспышка, а не содержимое: ключи прозрачности кончаются нулём (взрыв звёздочек,
    кольцо касания) — в рамку не входит, иначе её максимальный размах раздул бы рамку."""
    keys = (layer.get("keys") or {}).get("opacity") or []
    if not keys:
        return False
    last = max(keys, key=lambda k: _num(k.get("t"), 0))
    return _num(last.get("v"), 1.0) <= 0.05


def is_full_bleed(layer: dict, sizes: dict[str, tuple[int, int]] | None = None, ref: Any = None) -> bool:
    """Фон во всю ширину кадра (звёзды, затемнение, лента): картинка/видео/фигура ≥ 98 %
    ширины опорного кадра (1060 px у 1080)."""
    if layer.get("type") not in ("image", "video", "shape"):
        return False
    size = layer_size(layer, sizes)
    sw = _ref(ref)[0]
    limit = FULL_BLEED_W if sw == REF_W else FULL_BLEED_FRAC * sw
    return bool(size) and size[0] * _num(layer.get("scale"), 1.0) >= limit


def _counts_in_box(layer: dict, sizes: dict[str, tuple[int, int]] | None, ref: Any = None) -> bool:
    return not (layer.get("hidden") or layer.get("type") == "audio" or is_full_bleed(layer, sizes, ref)
                or is_transient(layer) or not layer_size(layer, sizes))


def _extent(center: float, values: list[float], half: float, span: float) -> tuple[float, float] | None:
    lo, hi = math.inf, -math.inf
    for v in values:
        a, b = max(0.0, v * span - half), min(float(span), v * span + half)
        if b > a:
            lo, hi = min(lo, a), max(hi, b)
    return (lo, hi) if hi > lo else None


def layer_extent(layer: dict, sizes: dict[str, tuple[int, int]] | None = None,
                 ref: Any = None) -> tuple[float, float] | None:
    """Верх и низ слоя в опорных px (0..sh) по ``y`` и всем ключам ``y``; за кадром — None."""
    size = layer_size(layer, sizes)
    if not size:
        return None
    half = size[1] * _settled_scale(layer) / 2
    ys = [_num(layer.get("y"), 0.5)] + [_num(k.get("v"), 0.5) for k in (layer.get("keys") or {}).get("y") or []]
    return _extent(0.0, ys, half, _ref(ref)[1])


def layer_hextent(layer: dict, sizes: dict[str, tuple[int, int]] | None = None,
                  ref: Any = None) -> tuple[float, float] | None:
    """Левый и правый край слоя в опорных px (0..sw) по ``x`` и ключам ``x``; за кадром — None."""
    size = layer_size(layer, sizes)
    if not size:
        return None
    half = size[0] * _settled_scale(layer) / 2
    xs = [_num(layer.get("x"), 0.5)] + [_num(k.get("v"), 0.5) for k in (layer.get("keys") or {}).get("x") or []]
    return _extent(0.0, xs, half, _ref(ref)[0])


def content_box(layers: list[dict], sizes: dict[str, tuple[int, int]] | None = None,
                ref: Any = None) -> tuple[float, float] | None:
    """Верх и низ всего видимого содержимого в опорных px (0..sh); нет — None."""
    extents = [layer_extent(layer, sizes, ref) for layer in layers if _counts_in_box(layer, sizes, ref)]
    extents = [e for e in extents if e]
    if not extents:
        return None
    return min(e[0] for e in extents), max(e[1] for e in extents)


def _span(layer: dict) -> tuple[float, float]:
    start = _num(layer.get("start"), 0.0)
    return start, max(start, _num(layer.get("end"), start))


def _union(a: tuple[float, float] | None, b: tuple[float, float] | None) -> tuple[float, float] | None:
    if not a:
        return b
    if not b:
        return a
    return min(a[0], b[0]), max(a[1], b[1])


def scenes(layers: list[dict], sizes: dict[str, tuple[int, int]] | None = None, ref: Any = None) -> list[dict]:
    """Сцены монтажа: слои содержимого, связанные по времени (перекрытие ≥ SCENE_LINK_SEC
    или половины короткого слоя). Короткий нахлёст на смене сцены (свайп, уход телефона
    под новый титр) сцены не склеивает. ``[{start, end, box, hbox, members}]`` по времени;
    ``box`` — верх/низ, ``hbox`` — лево/право (опорные px)."""
    items = sorted(
        ((i, *_span(layer), layer_extent(layer, sizes, ref), layer_hextent(layer, sizes, ref))
         for i, layer in enumerate(layers) if _counts_in_box(layer, sizes, ref)),
        key=lambda item: item[1],
    )
    out: list[dict] = []
    for index, start, end, extent, hextent in items:
        if not extent:
            continue
        cur = out[-1] if out else None
        if cur is not None and min(cur["end"], end) - start >= min(SCENE_LINK_SEC, 0.5 * (end - start)):
            cur["end"] = max(cur["end"], end)
            cur["box"] = (min(cur["box"][0], extent[0]), max(cur["box"][1], extent[1]))
            cur["hbox"] = _union(cur["hbox"], hextent)
            cur["members"].append(index)
        else:
            out.append({"start": start, "end": end, "box": extent, "hbox": hextent, "members": [index]})
    return _merge_similar(out, ref)


def _merge_similar(parts: list[dict], ref: Any = None) -> list[dict]:
    """Соседние сцены с почти той же рамкой (верх и низ ближе SCENE_SIMILAR) — одна:
    иначе телефон, что стоит на месте весь ролик, прыгал бы на несколько px на каждой
    смене титра. Отдельной остаётся сцена с другой рамкой (концовка с логотипом)."""
    sh = _ref(ref)[1]
    similar = SCENE_SIMILAR if sh == REF_H else SCENE_SIMILAR_FRAC * sh
    out: list[dict] = []
    for part in parts:
        cur = out[-1] if out else None
        if (cur is not None and abs(cur["box"][0] - part["box"][0]) <= similar
                and abs(cur["box"][1] - part["box"][1]) <= similar):
            cur["end"] = max(cur["end"], part["end"])
            cur["box"] = (min(cur["box"][0], part["box"][0]), max(cur["box"][1], part["box"][1]))
            cur["hbox"] = _union(cur.get("hbox"), part.get("hbox"))
            cur["members"].extend(part["members"])
        else:
            out.append({**part, "members": list(part["members"])})
    return out


def _width_cap(width: float, sw: float, widest: float | None) -> float:
    """Предел масштаба по ширине: W/sw; кадр уже исходного — сколько позволяет самая
    широкая сцена (с полями), но не меньше W/sw и не больше MAX_BOOST · W/sw."""
    fit = width / sw
    if width >= sw or not widest:
        return fit
    return max(fit, min(MAX_BOOST * fit, width * (1 - 2 * MARGIN) / max(1.0, widest)))


def reframe_plan(layers: list[dict], width: int, height: int,
                 sizes: dict[str, tuple[int, int]] | None = None, camera: list[dict] | None = None,
                 ref: Any = None) -> dict:
    """План перекомпоновки из опорного кадра ``ref`` (нет — 1080×1920) в кадр
    ``width``×``height`` (опорный кадр нужного формата): общий масштаб ``scale`` и центр
    рамки (опорные px) — свой у каждой сцены (``scenes``) и общий ``center``; ``cx`` —
    центр по горизонтали (у кадра не уже исходного — середина исходного кадра).

    Масштаб один на весь ролик (размеры не прыгают между сценами): самая высокая сцена
    входит в кадр с полями. Центр — свой у сцены: концовка с логотипом встаёт по центру
    кадра, а не туда, где она была относительно телефона из прошлой сцены.
    ``camera`` — ключи наездов: слои ``followCamera`` под самым сильным наездом тоже
    входят в кадр (масштаб меньше, но не больше чем в CAMERA_MAX_SHRINK раз)."""
    sw, sh = _ref(ref)
    parts = scenes(layers, sizes, ref)
    default_box = DEFAULT_BOX if sh == REF_H else (DEFAULT_BOX_FRAC[0] * sh, DEFAULT_BOX_FRAC[1] * sh)
    box = content_box(layers, sizes, ref) or default_box
    tallest = max((p["box"][1] - p["box"][0] for p in parts), default=box[1] - box[0])
    narrow = width < sw
    widest = max((p["hbox"][1] - p["hbox"][0] for p in parts if p.get("hbox")), default=None) if narrow else None
    cap = min(1.0, MAX_BOOST * height / sh, _width_cap(width, sw, widest))
    scale = min(cap, height * (1 - 2 * MARGIN) / max(1.0, tallest))
    zoomed = _camera_scale(layers, camera, width, height, sizes, ref)
    if zoomed < scale:
        scale = max(zoomed, scale / CAMERA_MAX_SHRINK)

    def hcenter(hbox: tuple[float, float] | None) -> float:
        return round((hbox[0] + hbox[1]) / 2, 2) if narrow and hbox else sw / 2

    all_h = None
    for p in parts:
        all_h = _union(all_h, p.get("hbox"))
    return {
        "scale": round(scale, 4),
        "center": round((box[0] + box[1]) / 2, 2),
        "cx": hcenter(all_h),
        "box": [round(v, 1) for v in box],
        "ref": [sw, sh],
        "scenes": [
            {"start": round(p["start"], 3), "end": round(p["end"], 3), "box": [round(v, 1) for v in p["box"]],
             "center": round((p["box"][0] + p["box"][1]) / 2, 2), "cx": hcenter(p.get("hbox")),
             "members": p["members"]}
            for p in parts
        ],
    }


def _layer_scene(index: int, layer: dict, plan: dict) -> dict | None:
    """Сцена слоя; фон и вспышки — сцена, с которой дольше всего совпадают по времени;
    ни с какой — None (общий центр)."""
    best, best_overlap = None, 0.0
    start, end = _span(layer)
    for scene in plan.get("scenes") or []:
        if index in scene["members"]:
            return scene
        overlap = min(end, scene["end"]) - max(start, scene["start"])
        if overlap > best_overlap:
            best, best_overlap = scene, overlap
    return best


def _layer_center(index: int, layer: dict, plan: dict) -> float:
    """Центр рамки (по высоте) для слоя: его сцены; фон и вспышки — сцены, с которой
    дольше всего совпадают по времени; ни с какой — общий."""
    scene = _layer_scene(index, layer, plan)
    return scene["center"] if scene else plan["center"]


def _plan_ref(plan: dict | None) -> tuple[float, float]:
    return _ref((plan or {}).get("ref"))


def _map_x(x: float, scale: float, width: int, ref_w: float = REF_W, cx: float | None = None) -> float:
    cx = ref_w / 2 if cx is None else cx
    return round((width / 2 + (x * ref_w - cx) * scale) / width, 4)


def _map_y(y: float, scale: float, center: float, height: int, ref_h: float = REF_H) -> float:
    return round((height / 2 + (y * ref_h - center) * scale) / height, 4)


def _bleed_growth(layer: dict, sizes: dict[str, tuple[int, int]] | None, ref: Any,
                  width: int, height: int) -> tuple[float, float]:
    """Во сколько раз растянуть фон во всю ширину (по ширине, по высоте): кадр шире
    исходного — до новой ширины; фон на весь кадр — ещё и до новой высоты. Уже — 1
    (фон как был: остаётся во всю ширину)."""
    sw, sh = _ref(ref)
    size = layer_size(layer, sizes)
    covers = bool(size) and size[1] * _num(layer.get("scale"), 1.0) >= COVER_FRAC * sh
    return max(1.0, width / sw), max(1.0, height / sh) if covers else 1.0


def reframe_layer(layer: dict, scale: float, center: float, width: int, height: int,
                  full_bleed: bool = False, *, ref: Any = None, cx: float | None = None,
                  growth: tuple[float, float] = (1.0, 1.0)) -> dict:
    """Слой в новый кадр: x/y (и ключи) — переносом рамки с центром ``center`` (по
    горизонтали — ``cx``) в центр кадра, scale (и ключи) — × ``scale``. Фон во всю
    ширину — масштаб как был, растянут на ``growth`` (по ширине, по высоте)."""
    ref_w, ref_h = _ref(ref)
    out = dict(layer)
    out["x"] = _map_x(_num(layer.get("x"), 0.5), scale, width, ref_w, cx)
    out["y"] = _map_y(_num(layer.get("y"), 0.5), scale, center, height, ref_h)
    k = 1.0
    if not full_bleed:
        k = scale
    elif growth != (1.0, 1.0):
        props = layer.get("props") or {}
        if layer.get("type") == "shape":
            # Фигуру (затемнение, лента) тянем по сторонам — толщина ленты не растёт.
            out["props"] = {**props, "w": round(_num(props.get("w"), 400) * growth[0], 1)}
            if growth[1] > 1.0:
                out["props"]["h"] = round(_num(props.get("h"), 400) * growth[1], 1)
        else:
            # Картинку/видео — одним масштабом «cover»: пропорции не ломаются.
            k = max(growth)
    out["scale"] = round(_num(layer.get("scale"), 1.0) * k, 4)
    keys = layer.get("keys") or {}
    if keys:
        fresh = dict(keys)
        if keys.get("x"):
            fresh["x"] = [{**key, "v": _map_x(_num(key.get("v"), 0.5), scale, width, ref_w, cx)} for key in keys["x"]]
        if keys.get("y"):
            fresh["y"] = [{**key, "v": _map_y(_num(key.get("v"), 0.5), scale, center, height, ref_h)}
                          for key in keys["y"]]
        if keys.get("scale") and (not full_bleed or k != 1.0):
            fresh["scale"] = [{**key, "v": round(_num(key.get("v"), 1.0) * k, 4)} for key in keys["scale"]]
        out["keys"] = fresh
    return out


def reframe_layers(layers: list[dict], width: int, height: int,
                   sizes: dict[str, tuple[int, int]] | None = None, camera: list[dict] | None = None,
                   plan: dict | None = None, ref: Any = None) -> list[dict]:
    """Слои из опорного кадра ``ref`` (нет — 1080×1920) в кадр ``width``×``height``
    (правило — в начале модуля). Тот же кадр — слои как есть. ``camera`` — ключи наездов
    (влияют на общий масштаб); саму камеру переносит ``reframe_camera`` (слои и камера
    вместе — ``reframe_motion``)."""
    ref = _plan_ref(plan) if plan and not ref else _ref(ref)
    if not layers or (int(width), int(height)) == (int(ref[0]), int(ref[1])):
        return layers
    plan = plan or reframe_plan(layers, int(width), int(height), sizes, camera, ref)
    out = []
    for i, layer in enumerate(layers):
        if layer.get("type") == "audio":
            out.append(layer)
            continue
        scene = _layer_scene(i, layer, plan)
        bleed = is_full_bleed(layer, sizes, ref)
        out.append(reframe_layer(
            layer, plan["scale"], scene["center"] if scene else plan["center"], int(width), int(height), bleed,
            ref=ref, cx=scene.get("cx", plan.get("cx")) if scene else plan.get("cx"),
            growth=_bleed_growth(layer, sizes, ref, int(width), int(height)) if bleed else (1.0, 1.0),
        ))
    return out


# ---------------------------------------------------------------- камера


def _interp(points: list[dict] | None, t: float, fallback: float) -> float:
    """Значение ключей в момент ``t`` (линейно; изгиб ease для рамки не важен)."""
    if not points:
        return fallback
    pts = sorted(points, key=lambda k: _num(k.get("t"), 0))
    if t <= _num(pts[0].get("t"), 0):
        return _num(pts[0].get("v"), fallback)
    for a, b in zip(pts, pts[1:]):
        ta, tb = _num(a.get("t"), 0), _num(b.get("t"), 0)
        if t < tb:
            p = (t - ta) / (tb - ta) if tb > ta else 1.0
            va, vb = _num(a.get("v"), fallback), _num(b.get("v"), fallback)
            return va + (vb - va) * p
    return _num(pts[-1].get("v"), fallback)


def _follow_extent(layer: dict, t: float, width: float, height: float,
                   sizes: dict[str, tuple[int, int]] | None) -> tuple[float, float, float, float] | None:
    """Рамка слоя (лево, верх, право, низ; опорные px кадра ``width``×``height`` — слой
    размечен в нём) в клиповое время ``t``. None — слой не идёт, или он въезжает/уезжает
    (частью за кадром): такой держать в кадре не нужно."""
    start, end = _span(layer)
    if not start <= t < end:
        return None
    size = layer_size(layer, sizes)
    if not size:
        return None
    keys = layer.get("keys") or {}
    local = t - start
    x = _interp(keys.get("x"), local, _num(layer.get("x"), 0.5)) * width
    y = _interp(keys.get("y"), local, _num(layer.get("y"), 0.5)) * height
    # px слоя — опорные px этого же кадра (width = его опорная ширина); запись та же,
    # что была с REF_W, — у вертикали числа не меняются ни на бит.
    k = max(0.0, _interp(keys.get("scale"), local, _num(layer.get("scale"), 1.0))) * width / width
    half_w, half_h = size[0] * k / 2, size[1] * k / 2
    box = (x - half_w, y - half_h, x + half_w, y + half_h)
    if box[0] < -0.5 or box[1] < -0.5 or box[2] > width + 0.5 or box[3] > height + 0.5:
        return None
    return box


def _follow_box(layers: list[dict], times: list[float], width: float, height: float,
                sizes: dict[str, tuple[int, int]] | None) -> tuple[float, float, float, float] | None:
    """Общая рамка слоёв ``followCamera`` (без фона во всю ширину и вспышек) в моменты ``times``."""
    boxes = [
        box
        for layer in layers
        if layer.get("followCamera") and _counts_in_box(layer, sizes, (width, height))
        for t in times
        if (box := _follow_extent(layer, t, width, height, sizes))
    ]
    if not boxes:
        return None
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def _camera_view(key: dict) -> tuple[float, float, float]:
    """Вид камеры ключа, как его покажет движок (camera.ts): zoom 1..4, центр зажат так,
    что окно не выходит за кадр."""
    zoom = min(4.0, max(1.0, _num(key.get("zoom"), 1.0)))
    half = 0.5 / zoom
    x = min(1 - half, max(half, _num(key.get("x"), 0.5)))
    y = min(1 - half, max(half, _num(key.get("y"), 0.5)))
    return zoom, x, y


def _sorted_camera(camera: list[dict] | None) -> list[dict]:
    return sorted((k for k in camera or [] if isinstance(k, dict)), key=lambda k: _num(k.get("t"), 0))


def _key_times(keys: list[dict], index: int) -> list[float]:
    """Моменты, в которые содержимое под ключом ``index`` должно входить в кадр: сам ключ
    и весь отрезок, пока камера стоит (соседний ключ с тем же видом)."""
    t = _num(keys[index].get("t"), 0)
    times = [t]
    view = _camera_view(keys[index])
    for other in (index - 1, index + 1):
        if not 0 <= other < len(keys):
            continue
        if any(abs(a - b) > 1e-3 for a, b in zip(view, _camera_view(keys[other]))):
            continue
        a, b = sorted((t, _num(keys[other].get("t"), 0)))
        n = max(1, math.ceil((b - a) / CAMERA_HOLD_STEP))
        times.extend(a + (b - a) * i / n for i in range(n + 1))
    return times


def _camera_scale(layers: list[dict], camera: list[dict] | None, width: int, height: int,
                  sizes: dict[str, tuple[int, int]] | None, ref: Any = None) -> float:
    """Наибольший масштаб, при котором слои ``followCamera`` под каждым наездом целиком
    входят в кадр с полями (бесконечность — наездов на такие слои нет)."""
    sw, sh = _ref(ref)
    keys = _sorted_camera(camera)
    best = math.inf
    for index, key in enumerate(keys):
        zoom = _camera_view(key)[0]
        if zoom <= 1.001:
            continue
        box = _follow_box(layers, _key_times(keys, index), sw, sh, sizes)
        if not box:
            continue
        w, h = box[2] - box[0], box[3] - box[1]
        if h > 0:
            best = min(best, height * (1 - 2 * MARGIN) / (zoom * h))
        if w > 0:
            best = min(best, width * (1 - 2 * MARGIN) / (zoom * w))
    return best


def _scene_at(t: float, layers: list[dict], plan: dict) -> dict | None:
    """Сцена, что идёт в момент ``t`` (лучше — та, где сейчас слой ``followCamera``)."""
    parts = plan.get("scenes") or []
    live = {i for i, layer in enumerate(layers)
            if layer.get("followCamera") and _span(layer)[0] <= t < _span(layer)[1]}
    for scene in parts:
        if live & set(scene["members"]):
            return scene
    near = [s for s in parts if s["start"] <= t <= s["end"]]
    if near:
        return max(near, key=lambda s: s["start"])
    if parts:
        return min(parts, key=lambda s: min(abs(t - s["start"]), abs(t - s["end"])))
    return None


def _scene_center_at(t: float, layers: list[dict], plan: dict) -> float:
    """Центр рамки сцены, что идёт в момент ``t``."""
    scene = _scene_at(t, layers, plan)
    return scene["center"] if scene else plan["center"]


def _fit_center(center: float, lo_edge: float, hi_edge: float, zoom: float, size: float) -> float:
    """Центр камеры (px), ближайший к ``center``, при котором отрезок [lo_edge, hi_edge]
    после наезда ``zoom`` лежит в кадре ``size`` с полями."""
    room = (0.5 - MARGIN) * size / zoom
    lo, hi = hi_edge - room, lo_edge + room
    return min(hi, max(lo, center)) if lo <= hi else (lo + hi) / 2


def reframe_camera(camera: list[dict] | None, layers: list[dict], width: int, height: int,
                   sizes: dict[str, tuple[int, int]] | None = None, plan: dict | None = None,
                   ref: Any = None) -> list[dict]:
    """Ключи камеры из опорного кадра ``ref`` (нет — 1080×1920) в кадр ``width``×``height``
    (правило 6 в начале модуля). ``layers`` — слои в исходном опорном кадре. Тот же кадр —
    камера как есть.

    Каждый ключ: центр — та же точка содержимого (перенос сцены); zoom — не сильнее, чем
    позволяют слои ``followCamera`` под ним (входят в кадр с полями); центр сдвинут ровно
    настолько, чтобы они не вышли за поля; итог зажат, как зажимает движок (окно в кадре)."""
    width, height = int(width), int(height)
    ref = _plan_ref(plan) if plan and not ref else _ref(ref)
    if not camera or (width, height) == (int(ref[0]), int(ref[1])):
        return camera or []
    plan = plan or reframe_plan(layers, width, height, sizes, camera, ref)
    scale = plan["scale"]
    framed = reframe_layers(layers, width, height, sizes, plan=plan, ref=ref)
    keys = _sorted_camera(camera)
    out: list[dict] = []
    for index, key in enumerate(keys):
        zoom, x, y = _camera_view(key)
        t = _num(key.get("t"), 0)
        scene = _scene_at(t, layers, plan)
        cx = _map_x(x, scale, width, ref[0], (scene or plan).get("cx")) * width
        cy = _map_y(y, scale, scene["center"] if scene else plan["center"], height, ref[1]) * height
        box = _follow_box(framed, _key_times(keys, index), width, height, sizes) if zoom > 1.001 else None
        if box:
            if box[3] > box[1]:
                zoom = min(zoom, max(1.0, height * (1 - 2 * MARGIN) / (box[3] - box[1])))
            if box[2] > box[0]:
                zoom = min(zoom, max(1.0, width * (1 - 2 * MARGIN) / (box[2] - box[0])))
            cx = _fit_center(cx, box[0], box[2], zoom, width)
            cy = _fit_center(cy, box[1], box[3], zoom, height)
        half = 0.5 / zoom
        out.append({
            **key,
            "zoom": round(zoom, 4),
            "x": round(min(1 - half, max(half, cx / width)), 4),
            "y": round(min(1 - half, max(half, cy / height)), 4),
        })
    return out


def reframe_motion(layers: list[dict], camera: list[dict] | None, width: int, height: int,
                   sizes: dict[str, tuple[int, int]] | None = None,
                   ref: Any = None) -> tuple[list[dict], list[dict]]:
    """Слои и камера из опорного кадра ``ref`` (нет — 1080×1920) в кадр ``width``×``height``
    одним планом (рендер, кадр проверки)."""
    width, height = int(width), int(height)
    ref = _ref(ref)
    if not layers or (width, height) == (int(ref[0]), int(ref[1])):
        return layers, camera or []
    plan = reframe_plan(layers, width, height, sizes, camera, ref)
    return (reframe_layers(layers, width, height, sizes, plan=plan, ref=ref),
            reframe_camera(camera, layers, width, height, sizes, plan, ref))


def reframe_between(layers: list[dict], camera: list[dict] | None, src_format: Any, dst_format: Any,
                    sizes: dict[str, tuple[int, int]] | None = None) -> tuple[list[dict], list[dict]]:
    """Слои и камера, размеченные в формате ``src_format``, — в формат ``dst_format``
    (опорные кадры app/formats.ref_size). Один формат — как есть."""
    from app.formats import ref_size

    src, dst = normalize_format(src_format), normalize_format(dst_format)
    if src == dst:
        return layers, camera or []
    dw, dh = ref_size(dst)
    return reframe_motion(layers, camera, dw, dh, sizes, ref_size(src))


def asset_sizes(layers: list[dict], assets: dict[str, str]) -> dict[str, tuple[int, int]]:
    """Собственные размеры картинок и видео слоёв (ffprobe) — для высоты в рамке."""
    from app.montage_assets import probe_asset
    from app.motion import asset_key

    out: dict[str, tuple[int, int]] = {}
    for layer in layers:
        if layer.get("type") not in ("image", "video", "pngtuber"):
            continue
        props = layer.get("props") or {}
        if layer.get("type") == "pngtuber":
            states = props.get("states") if isinstance(props.get("states"), dict) else {}
            key = asset_key(states.get("idle"))
        else:
            key = asset_key(props.get("asset_id") or props.get("src"))
        path = assets.get(key)
        if not key or key in out or not path:
            continue
        meta = probe_asset(path)
        w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
        if w > 0 and h > 0:
            out[key] = (w, h)
    return out
