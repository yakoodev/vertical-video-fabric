"""Фирменный стиль: подписи, крючок, звуки, призыв и логотип (из ``app/brand_kit.py``)."""

from __future__ import annotations

import math
from typing import Any

BRAND_PREFIX = "brand_"
LOGO_ID = "brand_logo"
CTA_ID = "brand_cta"
CTA_PLATE_ID = "brand_cta_plate"


# «minimal»: в рекламе 0–2 эффекта на ролик (docs/AGENTS.md) — лишние снимаем.
MINIMAL_SFX = 2


LOGO_PX = 150  # длинная сторона логотипа в опорных пикселях кадра 1080×1920
LOGO_MARGIN_PX = 36


CTA_SIZE = 72
CTA_MAX_WIDTH = 900
CTA_PAD_Y = 22
# Хук — первый текст в первые секунды клипа (если нет auto_hook из автомонтажа).
HOOK_WINDOW_SEC = 3.0


# --------------------------------------------------------------------------- #
# Применение к клипу
# --------------------------------------------------------------------------- #


def _brand_owned(layer: dict) -> bool:
    """Слой ставит бренд целиком (логотип, призыв) — при повторе снимается и ставится заново.
    Субтитры бренда (brand_caps) — содержимое клипа: их только перекрашиваем."""
    return str(layer.get("id") or "").startswith(BRAND_PREFIX) and layer.get("type") != "captions"


def caption_props(props: dict, brand: dict) -> dict:
    """Перекрасить субтитры-слой: стиль, шрифт, цвета; слова, позиция, голоса — как были."""
    cs, colors = brand["caption_style"], brand["colors"]
    out = dict(props or {})
    out["style"] = cs["style"]
    font = cs["font"] or brand["font"]
    if font:
        out["font"] = font
    else:
        out.pop("font", None)
    out["color"] = cs["color"] or colors["text"]
    out["activeColor"] = cs["activeColor"] or colors["accent"]
    out.pop("gradient", None)  # градиент перебил бы цвет бренда
    for key in ("stroke", "bg"):
        if cs[key]:
            out[key] = dict(cs[key])
        else:
            out.pop(key, None)  # как в пресете стиля
    if isinstance(out.get("highlightWords"), list):
        # Выделенные слова — цветом бренда, а не жёлтым/зелёным автомонтажа; и не тем же, что
        # звучащее слово (акцент), иначе подсветки не видно.
        color = highlight_color(brand, out["activeColor"], out["color"])
        out["highlightWords"] = [{**w, "color": color} for w in out["highlightWords"] if isinstance(w, dict)]
    return out


# Контрастные цвета подсветки по порядку предпочтения (красный — эмоция, зелёный — смысл).
HIGHLIGHT_FALLBACKS = ("#ff3b30", "#22c55e", "#3b82f6", "#ff4fd8", "#ffffff", "#ffd600")


def highlight_color(brand: dict, active: str, text: str) -> str:
    """Цвет выделенных слов субтитров: виден и рядом со звучащим словом, и на обычном тексте.

    Раньше выделенные слова красились акцентом бренда — тем же цветом, что и звучащее слово,
    и подсветки не было видно (проверка контраста в add_layers на это ругалась). Порядок:
    второй цвет бренда (``colors.highlight``), акцент, контрастный из запасных — первый,
    что не похож ни на звучащее слово, ни на обычный текст."""
    from app.agent_tools import colors_close

    colors = brand["colors"]
    for candidate in (colors.get("highlight"), colors.get("accent"), *HIGHLIGHT_FALLBACKS):
        if candidate and not colors_close(candidate, active) and not colors_close(candidate, text):
            return candidate
    return HIGHLIGHT_FALLBACKS[0]


def hook_props(props: dict, brand: dict) -> dict:
    hs, colors = brand["hook_style"], brand["colors"]
    out = dict(props or {})
    font = hs["font"] or brand["font"]
    if font:
        out["font"] = font
    out["color"] = hs["color"] or colors["accent"]
    out["gradient"] = None
    if hs["size"]:
        out["size"] = hs["size"]
    out["uppercase"] = hs["uppercase"]
    out["stroke"] = dict(hs["stroke"]) if hs["stroke"] else None
    out["bg"] = (
        {"color": colors["plate"], "opacity": 0.88, "radius": 18, "padX": 30, "padY": 14} if hs["plate"] else None
    )
    return out


def find_hook(layers: list[dict]) -> dict | None:
    """Хук: слой auto_hook, иначе первый видимый текст, начавшийся в первые 3 с."""
    hook = next((l for l in layers if l.get("id") == "auto_hook"), None)
    if hook is not None:
        return hook
    early = [
        l for l in layers
        if l.get("type") == "text" and not l.get("hidden") and float(l.get("start") or 0) < HOOK_WINDOW_SEC
        and not str(l.get("id") or "").startswith(BRAND_PREFIX)
    ]
    return min(early, key=lambda l: float(l.get("start") or 0)) if early else None


def _is_sfx(layer: dict) -> bool:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    return layer.get("type") == "audio" and str(props.get("asset_id") or "").startswith("sfx:")


def apply_sfx_policy(layers: list[dict], policy: str) -> tuple[list[dict], int]:
    """none — без эффектов; minimal — не больше двух (свои, поставленные руками, важнее
    автоматических). Озвучку и звуки из библиотеки (asset_id-число) не трогаем."""
    sfx = [l for l in layers if _is_sfx(l)]
    if policy == "none":
        drop = {id(l) for l in sfx}
    else:
        keep = sorted(sfx, key=lambda l: (str(l.get("id") or "").startswith("auto_"), float(l.get("start") or 0)))
        drop = {id(l) for l in keep[MINIMAL_SFX:]}
    return [l for l in layers if id(l) not in drop], len(drop)


def tail_start(cuts: list[dict], duration: float, want: float) -> float:
    """Начало концовки (время клипа ДО вырезов), чтобы в ГОТОВОМ ролике она шла ``want`` с:
    вырез в хвосте укоротил бы плашку, поэтому начинаем раньше на его длину."""
    from app.cuts import CutMap, normalize_cuts

    mapper = CutMap(normalize_cuts(cuts))
    if not mapper:
        return round(max(0.0, duration - want), 3)
    target = mapper.clamp(duration) - want
    if target <= 0:
        return 0.0
    lo, hi = 0.0, duration
    for _ in range(40):  # clamp монотонен: ищем последнее s, где clamp(s) ≤ target
        mid = (lo + hi) / 2
        if mapper.clamp(mid) <= target:
            lo = mid
        else:
            hi = mid
    return round(lo, 3)


def _cta_lines(text: str, size: float) -> int:
    per_line = max(8, int(CTA_MAX_WIDTH / (size * 0.58)))
    return sum(max(1, math.ceil(len(line) / per_line)) for line in text.split("\n"))


def _free_zones(blocked: list[tuple[float, float]], top: float, bottom: float) -> list[tuple[float, float]]:
    zones = [(top, bottom)]
    for a, b in sorted(blocked):
        nxt = []
        for z0, z1 in zones:
            if b <= z0 or a >= z1:
                nxt.append((z0, z1))
                continue
            if a > z0:
                nxt.append((z0, a))
            if b < z1:
                nxt.append((b, z1))
        zones = nxt
    return zones


def cta_y(height: float, blocked: list[tuple[float, float]], fmt: Any = None) -> tuple[float, bool]:
    """Центр плашки-призыва по высоте и «влезла ли в свободное место».

    Свободные зоны — безопасная область кадра минус субтитры (и герой, если известен).
    Берём самую нижнюю, где плашка помещается: призыв читается в нижней трети, а лицо
    обычно выше. Плашка прижимается к низу зоны — ровно над субтитрами. ``fmt`` — формат
    разметки слоёв: его безопасная зона (app/safe_zones.py; нет — 9:16)."""
    from app.safe_zones import safe_zone

    zone = safe_zone(fmt)
    gap = 0.015
    zones = _free_zones(blocked, zone["top"] + 0.01, zone["bottom"] - 0.01)
    fitting = [z for z in zones if z[1] - z[0] >= height + 2 * gap]
    if fitting:
        z0, z1 = max(fitting, key=lambda z: z[1])
        return round(z1 - gap - height / 2, 3), True
    if zones:
        z0, z1 = max(zones, key=lambda z: z[1] - z[0])
        return round((z0 + z1) / 2, 3), False
    return 0.5, False


def build_cta(brand: dict, start: float, end: float, y: float, keyed: bool, fmt: Any = None) -> list[dict]:
    """Плашка-призыв: текст бренда + адрес акцентом. На зелёнке плашка — за персонажем, во всю
    ширину опорного кадра формата ``fmt`` (нет — 9:16, 1080)."""
    from app.formats import ref_size, settings_format

    cta, colors = brand["cta"], brand["colors"]
    lines = [t for t in (cta["text"], cta["url_text"]) if t]
    text = "\n".join(lines)
    props: dict = {
        "text": text, "font": brand["font"], "weight": 800, "size": CTA_SIZE, "color": colors["text"],
        "uppercase": False, "maxWidth": CTA_MAX_WIDTH, "lineHeight": 1.15, "stroke": None,
    }
    if cta["url_text"]:
        first = len(cta["text"].split()) if cta["text"] else 0
        props["highlight"] = {"words": list(range(first, first + len(cta["url_text"].split()))),
                              "color": colors["accent"], "bg": None}
    out: list[dict] = []
    if keyed:
        height = _cta_lines(text, CTA_SIZE) * CTA_SIZE * 1.15 + 2 * CTA_PAD_Y + 40
        out.append({
            "id": CTA_PLATE_ID, "type": "shape", "name": "Бренд: плашка призыва", "start": start, "end": end,
            "track": 0, "x": 0.5, "y": y, "behind": True, "opacity": 0.94,
            "props": {"kind": "rect", "w": ref_size(settings_format(fmt))[0], "h": round(height),
                      "fill": colors["plate"], "stroke": None},
            "in": {"type": "fade", "duration": 0.3}, "out": {"type": "none"},
        })
    else:
        props["bg"] = {"color": colors["plate"], "opacity": 0.9, "radius": 22, "padX": 36, "padY": CTA_PAD_Y}
    out.append({
        "id": CTA_ID, "type": "text", "name": "Бренд: призыв", "start": start, "end": end, "track": 4,
        "x": 0.5, "y": y, "props": props,
        "in": {"type": "slideUp", "duration": 0.35}, "out": {"type": "none"},
    })
    return out


def build_logo(brand: dict, asset: dict, start: float, end: float, fmt: Any = None) -> dict:
    """Логотип в верхнем углу под шапкой площадки: ~1 % кадра, видео не закрывает. ``fmt`` —
    формат разметки слоёв: опорный кадр и безопасная зона (у 16:9 — поля YouTube 5 %)."""
    from app.formats import ref_size, settings_format
    from app.safe_zones import safe_zone

    fmt = settings_format(fmt)
    ref_w, ref_h = ref_size(fmt)
    zone = safe_zone(fmt)
    aw, ah = float(asset.get("width") or 0), float(asset.get("height") or 0)
    ratio = ah / aw if aw > 0 and ah > 0 else 1.0  # высота / ширина
    w = LOGO_PX if ratio <= 1 else LOGO_PX / ratio
    h = w * ratio
    margin = max(LOGO_MARGIN_PX, zone["left"] * ref_w)
    half_x = (margin + w / 2) / ref_w
    x = half_x if brand["logo_corner"] == "top-left" else 1 - half_x
    return {
        "id": LOGO_ID, "type": "image", "name": "Бренд: логотип", "start": start, "end": end, "track": 4,
        "x": round(x, 4), "y": round(zone["top"] + (h / 2 + 16) / ref_h, 4), "opacity": 0.95,
        "props": {"asset_id": int(asset["id"]), "w": round(w, 1)},  # высота — по пропорциям картинки
        "in": {"type": "fade", "duration": 0.4}, "out": {"type": "fade", "duration": 0.3},
    }
