"""Формат кадра ролика: 9:16 (как всегда), квадрат 1:1 и 4:5 из того же монтажа.

Все слои размечены в опорном кадре 1080×1920 (docs/motion/spec.md): ``x``, ``y`` — доли
кадра, размеры — опорные пиксели (движок умножает их на ширину / 1080). В кадре
1080×1080 те же доли и размеры не влезают: телефон высотой 1350 px занял бы весь
квадрат, а титр сверху наехал бы на него.

Правило перекомпоновки — один масштаб на весь монтаж («зум по содержимому»):

1. Рамка слоя — от верха до низа (опорные px, обрезано по кадру 0..1920; учитываются
   ``y`` слоя и его ключи ``y``, масштаб — ``scale`` и последний ключ ``scale``). Фоновые
   слои во всю ширину (≥ 1060 px) и вспышки (ключи прозрачности кончаются нулём: взрыв
   звёздочек, кольцо касания) в рамки не входят.
2. Сцены — слои, что идут вместе (перекрываются по времени ≥ 0.6 с; короткий нахлёст
   свайпа или ухода телефона под новый титр сцены не склеивает). Рамка сцены — общая
   рамка её слоёв; соседние сцены с почти той же рамкой (± 10 % кадра) — одна сцена,
   чтобы телефон, стоящий весь ролик на месте, не прыгал на сменах титров.
3. Масштаб ``s`` = min(1, 1.25 · H/1920, W/1080, H · (1 − 2·4 %) / высота самой высокой
   сцены): каждая сцена целиком входит в новый кадр с полями 4 %, но не крупнее, чем
   1.25 от «вписать весь кадр» (холст с одним титром не раздувается). Масштаб один на
   весь ролик — размеры между сценами не прыгают.
4. Каждый слой: центр рамки его сцены → центр кадра, ``x``/``y`` и их ключи — тем же
   аффинным переносом, ``scale`` и ключи ``scale`` × ``s``. Внутри сцены взаимное
   расположение не меняется вовсе: титр не наезжает на телефон, если не наезжал в 9:16,
   касания по экрану телефона остаются на своих кнопках; концовка с логотипом встаёт
   по центру квадрата.
5. Фон во всю ширину масштаб не меняет (как был во всю ширину, так и остаётся),
   переносится только его центр (по сцене, с которой он дольше всего идёт) —
   затемнение под титрами остаётся под ними.

Пока формат не 9:16 — только у холстов (app/canvas.py): у клипа с исходником
кадрирование видео, раскладки, субтитры и баннеры размечены под 9:16.
"""

from __future__ import annotations

import math
from typing import Any

REF_W = 1080
REF_H = 1920
DEFAULT_ASPECT = "9:16"
# Ширина у всех форматов — 1080: шрифты и слои меряются от неё (W / 1080).
ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350),
}
_ALIASES = {
    "9x16": "9:16", "vertical": "9:16", "вертикаль": "9:16",
    "1x1": "1:1", "square": "1:1", "квадрат": "1:1",
    "4x5": "4:5",
}
# Не крупнее, чем «вписать весь опорный кадр» × столько.
MAX_BOOST = 1.25
# Поля сверху и снизу нового кадра (доля высоты).
MARGIN = 0.04
# Слой шире этого (опорные px) — фон во всю ширину: масштаб не трогаем, рамку не считаем.
FULL_BLEED_W = 1060.0
# Слои одной сцены перекрываются по времени хотя бы на столько (с); короче — переход.
SCENE_LINK_SEC = 0.6
# Соседние сцены, у которых верх и низ рамки ближе этого (опорные px), — одна сцена.
SCENE_SIMILAR = 0.10 * REF_H
# Нет слоёв — рамка = безопасная зона текста (10 %..80 % кадра).
DEFAULT_BOX = (0.10 * REF_H, 0.80 * REF_H)
# Пропорции экрана телефона по умолчанию (запись экрана 9:19.5) и корпус вокруг него.
PHONE_SCREEN_RATIO = 19.5 / 9
PHONE_EDGE = 0.047  # обод + рамка с каждой стороны, доля ширины экрана (device.ts)
# Средняя ширина знака в долях кегля (жирный кириллический текст).
GLYPH_W = 0.58

ONLY_CANVAS = (
    "формат {aspect} пока только для пустого холста (create_canvas_clip): у клипа с исходником "
    "кадрирование видео, раскладка, субтитры и баннер размечены под 9:16"
)


def normalize_aspect(value: Any) -> str:
    """'9:16' | '1:1' | '4:5' (и синонимы 1x1, square, квадрат); пусто — 9:16."""
    if value is None or value == "":
        return DEFAULT_ASPECT
    text = str(value).strip().lower().replace(" ", "")
    text = _ALIASES.get(text, text)
    if text not in ASPECTS:
        raise ValueError(f"aspect — один из {', '.join(ASPECTS)}")
    return text


def settings_aspect(value: Any) -> str:
    """Для настроек клипа: мусор — 9:16 (нормализация настроек не падает)."""
    try:
        return normalize_aspect(value)
    except ValueError:
        return DEFAULT_ASPECT


def aspect_size(aspect: Any) -> tuple[int, int]:
    return ASPECTS[normalize_aspect(aspect)]


def check_aspect_source(aspect: Any, source: dict | None) -> str:
    """Формат для рендера этого источника: не 9:16 — только у холста (ValueError)."""
    from app.canvas import is_canvas

    aspect = normalize_aspect(aspect)
    if aspect != DEFAULT_ASPECT and not is_canvas(source):
        raise ValueError(ONLY_CANVAS.format(aspect=aspect))
    return aspect


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


def is_full_bleed(layer: dict, sizes: dict[str, tuple[int, int]] | None = None) -> bool:
    """Фон во всю ширину кадра (звёзды, затемнение, лента): картинка/видео/фигура ≥ 1060 px."""
    if layer.get("type") not in ("image", "video", "shape"):
        return False
    size = layer_size(layer, sizes)
    return bool(size) and size[0] * _num(layer.get("scale"), 1.0) >= FULL_BLEED_W


def _counts_in_box(layer: dict, sizes: dict[str, tuple[int, int]] | None) -> bool:
    return not (layer.get("hidden") or layer.get("type") == "audio" or is_full_bleed(layer, sizes)
                or is_transient(layer) or not layer_size(layer, sizes))


def layer_extent(layer: dict, sizes: dict[str, tuple[int, int]] | None = None) -> tuple[float, float] | None:
    """Верх и низ слоя в опорных px (0..1920) по ``y`` и всем ключам ``y``; за кадром — None."""
    size = layer_size(layer, sizes)
    if not size:
        return None
    half = size[1] * _settled_scale(layer) / 2
    top, bottom = math.inf, -math.inf
    ys = [_num(layer.get("y"), 0.5)] + [_num(k.get("v"), 0.5) for k in (layer.get("keys") or {}).get("y") or []]
    for y in ys:
        a, b = max(0.0, y * REF_H - half), min(float(REF_H), y * REF_H + half)
        if b > a:
            top, bottom = min(top, a), max(bottom, b)
    return (top, bottom) if bottom > top else None


def content_box(layers: list[dict], sizes: dict[str, tuple[int, int]] | None = None) -> tuple[float, float] | None:
    """Верх и низ всего видимого содержимого в опорных px (0..1920); нет — None."""
    extents = [layer_extent(layer, sizes) for layer in layers if _counts_in_box(layer, sizes)]
    extents = [e for e in extents if e]
    if not extents:
        return None
    return min(e[0] for e in extents), max(e[1] for e in extents)


def _span(layer: dict) -> tuple[float, float]:
    start = _num(layer.get("start"), 0.0)
    return start, max(start, _num(layer.get("end"), start))


def scenes(layers: list[dict], sizes: dict[str, tuple[int, int]] | None = None) -> list[dict]:
    """Сцены монтажа: слои содержимого, связанные по времени (перекрытие ≥ SCENE_LINK_SEC
    или половины короткого слоя). Короткий нахлёст на смене сцены (свайп, уход телефона
    под новый титр) сцены не склеивает. ``[{start, end, box, members}]`` по времени."""
    items = sorted(
        ((i, *_span(layer), layer_extent(layer, sizes)) for i, layer in enumerate(layers) if _counts_in_box(layer, sizes)),
        key=lambda item: item[1],
    )
    out: list[dict] = []
    for index, start, end, extent in items:
        if not extent:
            continue
        cur = out[-1] if out else None
        if cur is not None and min(cur["end"], end) - start >= min(SCENE_LINK_SEC, 0.5 * (end - start)):
            cur["end"] = max(cur["end"], end)
            cur["box"] = (min(cur["box"][0], extent[0]), max(cur["box"][1], extent[1]))
            cur["members"].append(index)
        else:
            out.append({"start": start, "end": end, "box": extent, "members": [index]})
    return _merge_similar(out)


def _merge_similar(parts: list[dict]) -> list[dict]:
    """Соседние сцены с почти той же рамкой (верх и низ ближе SCENE_SIMILAR) — одна:
    иначе телефон, что стоит на месте весь ролик, прыгал бы на несколько px на каждой
    смене титра. Отдельной остаётся сцена с другой рамкой (концовка с логотипом)."""
    out: list[dict] = []
    for part in parts:
        cur = out[-1] if out else None
        if (cur is not None and abs(cur["box"][0] - part["box"][0]) <= SCENE_SIMILAR
                and abs(cur["box"][1] - part["box"][1]) <= SCENE_SIMILAR):
            cur["end"] = max(cur["end"], part["end"])
            cur["box"] = (min(cur["box"][0], part["box"][0]), max(cur["box"][1], part["box"][1]))
            cur["members"].extend(part["members"])
        else:
            out.append({**part, "members": list(part["members"])})
    return out


def reframe_plan(layers: list[dict], width: int, height: int,
                 sizes: dict[str, tuple[int, int]] | None = None) -> dict:
    """План перекомпоновки в кадр ``width``×``height``: общий масштаб ``scale`` и центр
    рамки (опорные px) — свой у каждой сцены (``scenes``) и общий ``center``.

    Масштаб один на весь ролик (размеры не прыгают между сценами): самая высокая сцена
    входит в кадр с полями. Центр — свой у сцены: концовка с логотипом встаёт по центру
    квадрата, а не туда, где она была в 9:16 относительно телефона из прошлой сцены."""
    parts = scenes(layers, sizes)
    box = content_box(layers, sizes) or DEFAULT_BOX
    tallest = max((p["box"][1] - p["box"][0] for p in parts), default=box[1] - box[0])
    cap = min(1.0, MAX_BOOST * height / REF_H, width / REF_W)
    scale = min(cap, height * (1 - 2 * MARGIN) / max(1.0, tallest))
    return {
        "scale": round(scale, 4),
        "center": round((box[0] + box[1]) / 2, 2),
        "box": [round(v, 1) for v in box],
        "scenes": [
            {"start": round(p["start"], 3), "end": round(p["end"], 3), "box": [round(v, 1) for v in p["box"]],
             "center": round((p["box"][0] + p["box"][1]) / 2, 2), "members": p["members"]}
            for p in parts
        ],
    }


def _layer_center(index: int, layer: dict, plan: dict) -> float:
    """Центр рамки для слоя: его сцены; фон и вспышки — сцены, с которой дольше всего
    совпадают по времени; ни с какой — общий."""
    best, best_overlap = plan["center"], 0.0
    start, end = _span(layer)
    for scene in plan.get("scenes") or []:
        if index in scene["members"]:
            return scene["center"]
        overlap = min(end, scene["end"]) - max(start, scene["start"])
        if overlap > best_overlap:
            best, best_overlap = scene["center"], overlap
    return best


def _map_x(x: float, scale: float, width: int) -> float:
    return round((width / 2 + (x * REF_W - REF_W / 2) * scale) / width, 4)


def _map_y(y: float, scale: float, center: float, height: int) -> float:
    return round((height / 2 + (y * REF_H - center) * scale) / height, 4)


def reframe_layer(layer: dict, scale: float, center: float, width: int, height: int,
                  full_bleed: bool = False) -> dict:
    """Слой в новый кадр: x/y (и ключи) — переносом рамки с центром ``center`` в центр
    кадра, scale (и ключи) — × ``scale`` (фон во всю ширину — как был)."""
    out = dict(layer)
    out["x"] = _map_x(_num(layer.get("x"), 0.5), scale, width)
    out["y"] = _map_y(_num(layer.get("y"), 0.5), scale, center, height)
    k = 1.0 if full_bleed else scale
    out["scale"] = round(_num(layer.get("scale"), 1.0) * k, 4)
    keys = layer.get("keys") or {}
    if keys:
        fresh = dict(keys)
        if keys.get("x"):
            fresh["x"] = [{**key, "v": _map_x(_num(key.get("v"), 0.5), scale, width)} for key in keys["x"]]
        if keys.get("y"):
            fresh["y"] = [{**key, "v": _map_y(_num(key.get("v"), 0.5), scale, center, height)} for key in keys["y"]]
        if keys.get("scale") and not full_bleed:
            fresh["scale"] = [{**key, "v": round(_num(key.get("v"), 1.0) * k, 4)} for key in keys["scale"]]
        out["keys"] = fresh
    return out


def reframe_layers(layers: list[dict], width: int, height: int,
                   sizes: dict[str, tuple[int, int]] | None = None) -> list[dict]:
    """Слои из опорного 1080×1920 в кадр ``width``×``height`` (правило — в начале модуля).
    Кадр 9:16 — слои как есть."""
    if not layers or (int(width), int(height)) == (REF_W, REF_H):
        return layers
    plan = reframe_plan(layers, int(width), int(height), sizes)
    return [
        layer if layer.get("type") == "audio"
        else reframe_layer(layer, plan["scale"], _layer_center(i, layer, plan), int(width), int(height),
                           is_full_bleed(layer, sizes))
        for i, layer in enumerate(layers)
    ]


def asset_sizes(layers: list[dict], assets: dict[str, str]) -> dict[str, tuple[int, int]]:
    """Собственные размеры картинок и видео слоёв (ffprobe) — для высоты в рамке."""
    from app.montage_assets import probe_asset
    from app.motion import asset_key

    out: dict[str, tuple[int, int]] = {}
    for layer in layers:
        if layer.get("type") not in ("image", "video"):
            continue
        props = layer.get("props") or {}
        key = asset_key(props.get("asset_id") or props.get("src"))
        path = assets.get(key)
        if not key or key in out or not path:
            continue
        meta = probe_asset(path)
        w, h = int(meta.get("width") or 0), int(meta.get("height") or 0)
        if w > 0 and h > 0:
            out[key] = (w, h)
    return out
