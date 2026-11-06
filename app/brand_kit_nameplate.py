"""Фирменный стиль: плашка с именем (из ``app/brand_kit.py``)."""

from __future__ import annotations

import math
from typing import Any, Callable

from app import motion
from app.brand_kit_normalize import LOGO_CORNERS, normalize_nameplate
from app.brand_kit_props import LOGO_ID, CTA_ID, LOGO_PX, LOGO_MARGIN_PX, find_hook

NAMEPLATE_ID = "brand_nameplate"


CAPTION_HALF = 0.075  # половина высоты двух строк субтитров в долях кадра


NAMEPLATE_CORNER_SIZE = 58.0  # пресет corner: маленькая плашка в верхнем углу (опорные px высоты пилюли)


# Опорные px кадра 1080×1920: плашка на 72 px ниже разделителя игры и вебки (как ручная плашка
# шортсов), 24 px от края кадра, 16 px под верхней безопасной зоной; зазор до соседей — 16 px.
NAMEPLATE_GAP_PX = 72
NAMEPLATE_MARGIN_PX = 24
NAMEPLATE_TOP_PX = 16
NAMEPLATE_CLEAR_PX = 16
NAMEPLATE_SHADOW = {"color": "#000000", "blur": 14, "x": 0, "y": 4, "opacity": 0.45}
# Кнопки лайков/комментариев TikTok справа (9:16): x > 900, y 900–1600 из 1080×1920.
TIKTOK_BUTTONS = (900 / 1080, 1.0, 900 / 1920, 1600 / 1920)


# --------------------------------------------------------------------------- #
# Плашка канала (nameplate)
# --------------------------------------------------------------------------- #


def nameplate_enabled(np: dict | None) -> bool:
    """Плашка что-то покажет: есть ник или значок площадки."""
    return bool(np) and (bool(np.get("handle")) or np.get("platform", "none") != "none")


def _nameplate_width(np: dict) -> float:
    """Оценка ширины пилюли в опорных px — та же, что app/aspect.layer_size (зеркало reframe.ts)."""
    from app.aspect import layer_size

    size = layer_size({"type": "nameplate", "props": {k: np.get(k) for k in ("platform", "handle", "size")}})
    return float(size[0]) if size else float(np["size"]) * 4


def _split_line(render: dict, fmt: Any) -> float | None:
    """Низ полосы-разделителя между игрой и вебкой (доли кадра) в раскладке split; иначе None.

    Вебка снизу: разделитель (зазор ``gap``) идёт от верха полосы вебки ``cam_strip`` вниз на свою
    высоту — его низ ровно 1 − ratio. Вебка сверху: низ полосы вебки с зазором — ``cam_strip[1]``."""
    from app.agent_tools import cam_strip
    from app.stream_layout import normalize_layout

    layout = render.get("layout")
    strip = cam_strip(layout, fmt)
    if not strip:
        return None
    if strip[0] <= 0.0:
        return strip[1]
    return round(1.0 - float(normalize_layout(layout)["ratio"]), 4)


def _caption_bands(render: dict, layers: list[dict], cap_half: float) -> list[tuple[float, float]]:
    """Где по высоте субтитры: слои captions и прожигаемые (ASS) субтитры, если слоя нет."""
    bands = [
        (float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) - cap_half, float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) + cap_half)
        for l in layers if l.get("type") == "captions" and not l.get("hidden")
    ]
    if not bands and render.get("subs_on"):
        # Прожигаемые субтитры прижаты низом к отступу sub_pos_pct % высоты от низа кадра, вверх — до двух строк.
        try:
            bottom = 1.0 - float(render.get("sub_pos_pct") or 12.0) / 100.0
        except (TypeError, ValueError):
            bottom = 0.88
        bands.append((bottom - 2 * cap_half, bottom))
    return bands


def nameplate_place(
    render: dict, layers: list[dict], np: dict, *, logo_corner: str | None = None, fmt: Any = None,
    start: float = 0.0, end: float | None = None,
) -> dict:
    """Где поставить плашку канала: ``{x, y, align, boxWidth, position, fits, note}``.

    ``auto`` и ``under-split-*`` берут линию разделения из раскладки клипа (``render.layout`` split:
    игра и вебка полосами) — плашка на ``NAMEPLATE_GAP_PX`` ниже разделителя, а если там субтитры
    (их ставят на шов) — ниже субтитров. Нет раскладки или не влезает — верхний безопасный угол
    (у логотипа бренда — противоположный). В любом месте плашка уходит ниже того, что ей мешает:
    субтитры, хук, логотип, окошко вебки pip, кнопки TikTok справа (9:16); до нижней безопасной
    зоны площадки (подпись ролика) не опускается — тогда ``fits`` False.
    ``logo_corner`` — угол логотипа бренда (нет логотипа — None). ``start``/``end`` — когда плашка на
    экране: хук, ушедший до ``start`` (плашка «после хука»), ей не мешает."""
    from app.agent_tools import text_half_height
    from app.formats import ref_size, settings_format
    from app.safe_zones import safe_zone
    from app.stream_layout import camera_regions

    fmt = settings_format(fmt)
    ref_w, ref_h = ref_size(fmt)
    zone = safe_zone(fmt)
    h = float(np["size"]) / ref_h
    gap = NAMEPLATE_CLEAR_PX / ref_h
    margin = max(float(NAMEPLATE_MARGIN_PX), zone["left"] * ref_w)
    pill = _nameplate_width(np)
    box = round(min(ref_w - 2 * margin, pill * 1.15 + 40))
    cap_half = CAPTION_HALF if ref_h == 1920 else CAPTION_HALF * 1920 / ref_h

    # Препятствия — прямоугольники (x0, x1, y0, y1) в долях кадра.
    blocks: list[tuple[float, float, float, float]] = [
        (0.0, 1.0, y0, y1) for y0, y1 in _caption_bands(render, layers, cap_half)
    ]
    hook = find_hook(layers)
    if hook is not None and not hook.get("hidden") and _overlaps_time(hook, start, end):
        half = text_half_height(hook.get("props") or {}, float(hook.get("scale") or 1.0), ref_h)
        hy = float(hook.get("y", 0.5))
        blocks.append((0.0, 1.0, hy - half, hy + half))
    if logo_corner in LOGO_CORNERS:
        lw = (max(LOGO_MARGIN_PX, zone["left"] * ref_w) + LOGO_PX + 8) / ref_w
        ly1 = zone["top"] + (LOGO_PX + 32) / ref_h
        blocks.append((0.0, lw, zone["top"], ly1) if logo_corner == "top-left" else (1.0 - lw, 1.0, zone["top"], ly1))
    regions = camera_regions(render.get("layout"), int(ref_w), int(ref_h))
    for rect in (regions or {}).get("pinned") or []:
        blocks.append((rect["x"] / ref_w, (rect["x"] + rect["w"]) / ref_w, rect["y"] / ref_h, (rect["y"] + rect["h"]) / ref_h))
    if fmt == "9:16":
        blocks.append(TIKTOK_BUTTONS)

    def span(align: str) -> tuple[float, float]:
        if align == "left":
            return margin / ref_w, (margin + pill) / ref_w
        if align == "right":
            return 1.0 - (margin + pill) / ref_w, 1.0 - margin / ref_w
        return 0.5 - pill / ref_w / 2, 0.5 + pill / ref_w / 2

    def settle(top: float, align: str) -> tuple[float, bool]:
        """Опускать верх плашки ниже всего, что она задевает; влезла ли выше нижней зоны."""
        x0, x1 = span(align)
        for _ in range(len(blocks) + 1):
            hits = [b for b in blocks if b[0] < x1 and b[1] > x0 and b[2] < top + h + gap and b[3] > top - gap]
            if not hits:
                break
            top = max(b[3] for b in hits) + gap
        return top, top + h <= zone["bottom"] and top >= zone["top"]

    def at(position: str, align: str, top: float, fits: bool, note: str) -> dict:
        if align == "left":
            x = (margin + box / 2) / ref_w
        elif align == "right":
            x = 1.0 - (margin + box / 2) / ref_w
        else:
            x = 0.5
        return {"x": round(x, 4), "y": round(top + h / 2, 4), "align": align, "boxWidth": box,
                "position": position, "fits": fits, "note": note}

    corner_align = "right" if logo_corner == "top-left" else "left"
    want = np["position"]
    notes: list[str] = []
    if want in ("auto", "under-split-left", "under-split-center"):
        line = _split_line(render, fmt)
        align = "center" if want == "under-split-center" else "left"
        if line is not None:
            top, fits = settle(line + NAMEPLATE_GAP_PX / ref_h, align)
            if fits:
                return at("under-split-" + ("center" if align == "center" else "left"), align, top, True,
                          f"под разделителем игры и вебки ({line:.3f} кадра)")
            notes.append("под разделителем не влезла (субтитры/нижняя зона площадки) — в верхнем углу")
        elif want != "auto":
            notes.append("в клипе нет раскладки split (игра + вебка полосами) — в верхнем углу")
        corner = "top-right" if corner_align == "right" else "top-left"
    else:
        corner = want
        corner_align = "right" if want == "top-right" else "left"
    top, fits = settle(zone["top"] + NAMEPLATE_TOP_PX / ref_h, corner_align)
    if not fits:
        notes.append("плашке не нашлось свободного места — проверь view_frame и подвинь brand_nameplate (update_layer y)")
    return at(corner, corner_align, top, fits, "; ".join(notes) or "в верхнем углу, в безопасной зоне площадки")


def _overlaps_time(layer: dict, start: float, end: float | None) -> bool:
    a, b = float(layer.get("start") or 0.0), float(layer.get("end") or 0.0)
    return a < (end if end is not None else math.inf) and b > start


def build_nameplate(np: dict, place: dict, duration: float, font: str = "", cta_start: float | None = None,
                    start: float = 0.0, end: float | None = None) -> dict:
    """Слой плашки канала на весь ролик. Анимация — своя у слоя (``anim``: вход от значка, редкий
    блик и тик значка); ``fade`` — мягкий вход только у неподвижной (``anim: none``).
    Свёртка в значок (``outro``): auto — если есть призыв (``cta_start``), плашка сворачивается
    за NAMEPLATE_OUTRO_SEC с его начала и уходит; collapse — то же в конце ролика."""
    collapse = np["outro"] == "collapse" or (np["outro"] == "auto" and cta_start is not None)
    end = min(duration, end) if end is not None else duration
    if collapse and cta_start is not None:
        end = min(duration, max(cta_start, 2 * motion.NAMEPLATE_OUTRO_SEC + 0.5) + motion.NAMEPLATE_OUTRO_SEC)
    props = {
        "platform": np["platform"], "handle": np["handle"], "style": np["style"], "accent": np["accent"],
        "size": np["size"], "font": font, "weight": 800, "color": "#ffffff",
        "align": place["align"], "boxWidth": place["boxWidth"],
        "anim": np["anim"], "live": np["live"], "outro": "collapse" if collapse else "none",
    }
    return {
        "id": NAMEPLATE_ID, "type": "nameplate", "name": "Плашка канала", "start": round(max(0.0, start), 3),
        "end": round(end, 3),
        "track": 4, "x": place["x"], "y": place["y"], "props": props,
        "shadow": dict(NAMEPLATE_SHADOW),
        "in": {"type": "fade", "duration": 0.4} if np["fade"] and np["anim"] == "none" else {"type": "none"},
        "out": {"type": "none"},
    }


def _logo_corner_of(layers: list[dict]) -> str | None:
    """Угол уже стоящего логотипа бренда (слой brand_logo), чтобы плашка встала в другой."""
    logo = next((l for l in layers if l.get("id") == LOGO_ID and not l.get("hidden")), None)
    if logo is None:
        return None
    return "top-left" if float(logo.get("x", 1.0)) < 0.5 else "top-right"


def set_nameplate(
    store: Any, clip_plan_id: int, settings: dict | None = None, *, brand_id: int | None = None,
    remove: bool = False, before_change: Callable[[], None] | None = None,
    corner: str | None = None, start: float | None = None, end: float | None = None,
) -> dict:
    """Поставить (или снять) плашку канала на клип — без остального оформления бренда.

    ``corner`` — пресет «маленькая в верхнем углу» (top-right|top-left): угол безопасной зоны,
    кегль NAMEPLATE_CORNER_SIZE (если size не задан), а если есть хук и ``start`` не задан —
    плашка выходит, когда хук уходит (QA 01.11: плашка «после хука» встала на 0.40 кадра —
    место считалось с хуком, хотя на экране они не встречаются). ``start``/``end`` — секунды клипа.

    ``settings`` — поля плашки (platform, handle, style, accent, position, size, fade); с
    ``brand_id`` основа — плашка бренда (и его шрифт, акцент, угол логотипа). Повтор заменяет
    прежнюю плашку (слой ``brand_nameplate``), место считается заново по текущей раскладке.
    Ошибки — ValueError (нечего показать, слой не прошёл проверку), KeyError (нет клипа/бренда)."""
    from app.agent_tools import clip_timeline, lint_layers
    from app.formats import settings_layers_format

    brand = store.get_brand(int(brand_id)) if brand_id else None
    timeline = clip_timeline(store, clip_plan_id)
    render, duration = dict(timeline["render"]), float(timeline["duration"])
    if duration <= 0:
        raise ValueError("в клипе нет кусков")
    current = list(render.get("layers") or [])
    had = any(l.get("id") == NAMEPLATE_ID for l in current)
    layers = [dict(l) for l in current if l.get("id") != NAMEPLATE_ID]

    def save(new_layers: list[dict], label: str) -> dict:
        render["layers"] = new_layers
        if before_change is not None:
            before_change()
        else:
            from app.clip_history import snapshot

            snapshot(store, clip_plan_id, label)
        return store.set_clip_plan_render_settings(clip_plan_id, render)

    if remove:
        if not had:
            return {"ok": True, "removed": False, "note": "плашки канала в клипе не было"}
        save(layers, "плашка канала снята")
        return {"ok": True, "removed": True}

    base = dict(brand["nameplate"]) if brand else {}
    asked = {k: v for k, v in (settings or {}).items() if v is not None}
    if corner is not None:
        if corner not in LOGO_CORNERS:
            raise ValueError(f"corner: одно из {', '.join(LOGO_CORNERS)}")
        asked["position"] = corner
        asked.setdefault("size", NAMEPLATE_CORNER_SIZE)
    np = normalize_nameplate({**base, **asked})
    if not nameplate_enabled(np):
        raise ValueError("плашке нечего показать: нужен handle (ник) и/или platform")
    if brand and not np["accent"] and np["platform"] == "none":
        np["accent"] = brand["colors"]["accent"]
    fmt = settings_layers_format(render)
    logo_corner = (brand["logo_corner"] if brand and brand.get("logo_asset_id") else None) or _logo_corner_of(layers)
    if start is None and corner is not None:
        hook = find_hook(layers)
        if hook is not None and not hook.get("hidden") and float(hook.get("end") or 0.0) < duration - 1.0:
            start = float(hook.get("end") or 0.0)  # плашка выходит, когда уходит хук
    start = max(0.0, min(float(start or 0.0), duration - 0.5))
    if end is not None and float(end) <= start:
        raise ValueError("end плашки должен быть позже start")
    place = nameplate_place(render, layers, np, logo_corner=logo_corner, fmt=fmt, start=start, end=end)
    cta = next((l for l in layers if l.get("id") == CTA_ID and not l.get("hidden")), None)
    cta_start = float(cta.get("start") or 0.0) if cta is not None else None
    layer = build_nameplate(np, place, duration, (brand or {}).get("font") or "", cta_start, start=start,
                            end=float(end) if end is not None else None)
    problems = motion.validate_layers([layer], duration)
    if problems:
        raise ValueError("плашка не прошла проверку: " + "; ".join(problems))
    fresh = save(layers + [layer], f"плашка канала «{np['handle'] or np['platform']}»")
    fresh_layers = (fresh.get("render_settings") or {}).get("layers") or []
    # Не влезла или встала не туда, куда просили, — сказать почему.
    warnings = [place["note"]] if not place["fits"] or np["position"] not in ("auto", place["position"]) else []
    return {
        "ok": True,
        "layer_id": NAMEPLATE_ID,
        "position": place["position"],
        "where": place["note"],
        "x": place["x"], "y": place["y"],
        "start": layer["start"], "end": layer["end"],
        "warnings": warnings + lint_layers(fresh_layers, duration, only_ids={NAMEPLATE_ID}, fmt=fmt),
        "check": f"view_frame t={min(duration, 1.0):.1f} — плашка не на лице и не под субтитрами",
    }
