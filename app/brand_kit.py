"""Брендовый шаблон (brand kit): ролики одного заказчика — в одном стиле одной командой.

Реклама магазина (например, VTuber-моделей) идёт сериями: каждый ролик монтируется
заново, но шрифт, цвета, логотип, концовка-призыв и музыка у бренда одни. Держать
это в голове монтажёра (или в промпте ИИ) — значит каждый раз чуть по-разному.
Поэтому бренд хранится один раз (таблица ``brands``), а ``apply_brand`` раскладывает
его на клип поверх уже сделанного монтажа:

* субтитры-слои и хук перекрашиваются (текст и тайминг не трогаем);
* логотип — маленький, в верхнем углу, после хука и до концовки (видео не закрывает);
* концовка — плашка-призыв на последние ``cta.duration_sec`` готового ролика, в
  свободной от субтитров (и героя, если его рамка известна) зоне; у источника на
  зелёнке плашка лежит ЗА персонажем (``behind``), текст — поверх;
* музыка бренда, фон за персонажем, политика звуковых эффектов.

Слои бренда помечены id с префиксом ``brand_`` — повторное применение (или другой
бренд) снимает прежние и ставит заново, дублей нет. Снимок для Ctrl+Z делает
вызывающий (MCP — ``ctx.before_change``), иначе — сам ``apply_brand``.
"""

from __future__ import annotations

import math
from typing import Any, Callable

from app import motion

BRAND_PREFIX = "brand_"
LOGO_ID = "brand_logo"
CTA_ID = "brand_cta"
CTA_PLATE_ID = "brand_cta_plate"
CAPS_ID = "brand_caps"

SFX_POLICIES = ("none", "minimal")
# «minimal»: в рекламе 0–2 эффекта на ролик (docs/AGENTS.md) — лишние снимаем.
MINIMAL_SFX = 2
LOGO_CORNERS = ("top-right", "top-left")
LOGO_PX = 150  # длинная сторона логотипа в опорных пикселях кадра 1080×1920
LOGO_MARGIN_PX = 36
CTA_SEC_DEFAULT = 3.0
CTA_SEC_MIN = 1.5
CTA_SEC_MAX = 8.0
CTA_SIZE = 72
CTA_MAX_WIDTH = 900
CTA_PAD_Y = 22
# Хук — первый текст в первые секунды клипа (если нет auto_hook из автомонтажа).
HOOK_WINDOW_SEC = 3.0
CAPTION_HALF = 0.075  # половина высоты двух строк субтитров в долях кадра

DEFAULT_COLORS = {"text": "#ffffff", "accent": "#ffd600", "plate": "#0b0d14"}
DEFAULT_HOOK_STROKE = {"color": "#000000", "width": 10}


# --------------------------------------------------------------------------- #
# Нормализация: что бы ни прислали, в базе — полная и безопасная форма
# --------------------------------------------------------------------------- #


def _color(value: Any) -> str | None:
    """#rrggbb (или #rgb) → #rrggbb в нижнем регистре; прозрачность у бренда не нужна."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    if motion.SHORT_COLOR_RE.match(value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    if motion.COLOR_RE.match(value) and len(value) == 7:
        return value.lower()
    return None


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) else ""


def _num(value: Any, default: float | None, lo: float, hi: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return round(min(hi, max(lo, number)), 3)


def _opt_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _stroke(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    width = _num(value.get("width"), 6.0, 0.0, 60.0)
    if not color or not width:
        return None
    return {"color": color, "width": width}


def _plate_bg(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    color = _color(value.get("color"))
    if not color:
        return None
    return {"color": color, "opacity": _num(value.get("opacity"), 0.6, 0.0, 1.0)}


def _normalize_caption_style(raw: Any) -> dict:
    """Стиль слоя captions + переопределения. Пустой font/color/activeColor — из бренда
    (шрифт, цвет текста, акцент); stroke/bg null — как в пресете стиля."""
    src = raw if isinstance(raw, dict) else {}
    style = src.get("style")
    return {
        "style": style if style in motion.CAPTION_STYLES else motion.DEFAULT_CAPTION_STYLE,
        "font": _text(src.get("font"), 60),
        "color": _color(src.get("color")),
        "activeColor": _color(src.get("activeColor")),
        "stroke": _stroke(src.get("stroke")),
        "bg": _plate_bg(src.get("bg")),
    }


def _normalize_hook_style(raw: Any) -> dict:
    """Заголовок-хук: шрифт (пусто — шрифт бренда), цвет (пусто — акцент), кегль (пусто —
    как поставил автомонтаж под длину текста), плашка цвета plate, обводка."""
    src = raw if isinstance(raw, dict) else {}
    # Нет ключа — обводка по умолчанию; явный null — без обводки (на плашке она не нужна).
    stroke = _stroke(src["stroke"]) if "stroke" in src else dict(DEFAULT_HOOK_STROKE)
    uppercase = src.get("uppercase", True)
    return {
        "font": _text(src.get("font"), 60),
        "color": _color(src.get("color")),
        "size": _num(src.get("size"), None, 40.0, 160.0) if src.get("size") not in (None, "", 0) else None,
        "plate": src.get("plate") is True or src.get("plate") in (1, "1", "true"),
        "stroke": stroke,
        "uppercase": uppercase is True or uppercase in (1, "1", "true"),
    }


def _normalize_cta(raw: Any) -> dict:
    src = raw if isinstance(raw, dict) else {}
    return {
        "text": _text(src.get("text"), 60),
        "url_text": _text(src.get("url_text"), 60),
        "duration_sec": _num(src.get("duration_sec"), CTA_SEC_DEFAULT, CTA_SEC_MIN, CTA_SEC_MAX),
    }


def normalize_brand(raw: Any) -> dict:
    """Бренд в полной форме (без id и дат) — так он хранится и так отдаётся API."""
    src = raw if isinstance(raw, dict) else {}
    colors_src = src.get("colors") if isinstance(src.get("colors"), dict) else {}
    colors = {k: _color(colors_src.get(k)) or v for k, v in DEFAULT_COLORS.items()}
    backdrop_src = src.get("backdrop") if isinstance(src.get("backdrop"), dict) else {}
    corner = src.get("logo_corner")
    policy = src.get("sfx_policy")
    return {
        "name": _text(src.get("name"), 80),
        "font": _text(src.get("font"), 60),
        "colors": colors,
        "logo_asset_id": _opt_id(src.get("logo_asset_id")),
        "logo_corner": corner if corner in LOGO_CORNERS else LOGO_CORNERS[0],
        "caption_style": _normalize_caption_style(src.get("caption_style")),
        "hook_style": _normalize_hook_style(src.get("hook_style")),
        "cta": _normalize_cta(src.get("cta")),
        "music_track_id": _opt_id(src.get("music_track_id")),
        # Фон за вырезанным персонажем; не задан — цвет плашки бренда.
        "backdrop": {
            "color": _color(backdrop_src.get("color")) or colors["plate"],
            "color2": _color(backdrop_src.get("color2")) or "",
        },
        "sfx_policy": policy if policy in SFX_POLICIES else "minimal",
    }


def merge_brand(current: dict, patch: dict) -> dict:
    """Правка бренда: вложенные объекты (colors, cta, …) сливаются на один уровень,
    чтобы PATCH {"colors": {"accent": …}} не стирал остальные цвета."""
    out = dict(current)
    for key, value in (patch or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return normalize_brand(out)


def brand_problems(store: Any, brand: dict) -> list[str]:
    """Что в бренде ссылается в пустоту: шрифт не из пака, логотип/трек удалены."""
    problems: list[str] = []
    if not brand.get("name"):
        problems.append("нужно название бренда")
    families = font_families()
    fonts = {brand.get("font"), brand["caption_style"].get("font"), brand["hook_style"].get("font")} - {""}
    if families:
        for font in sorted(f for f in fonts if f):
            if font not in families:
                problems.append(f"шрифта «{font}» нет в паке (list_fonts)")
    if brand.get("logo_asset_id"):
        try:
            asset = store.get_montage_asset(int(brand["logo_asset_id"]))
            if asset.get("kind") not in ("image", "gif"):
                problems.append(f"логотип #{brand['logo_asset_id']} — не картинка ({asset.get('kind')})")
        except KeyError:
            problems.append(f"логотипа #{brand['logo_asset_id']} нет в «Файлах для монтажа»")
    if brand.get("music_track_id"):
        try:
            store.get_audio_track(int(brand["music_track_id"]))
        except KeyError:
            problems.append(f"трека #{brand['music_track_id']} нет в фоновой музыке")
    return problems


def font_families() -> set[str]:
    """Семейства шрифтов пака (пусто — пак недоступен, шрифт тогда не проверяем)."""
    from app.agent_tools import _font_manifest

    return {str(f.get("family") or "").strip() for f in _font_manifest()} - {""}


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
        # Выделенные слова — акцентом бренда, а не жёлтым/зелёным автомонтажа.
        out["highlightWords"] = [{**w, "color": colors["accent"]} for w in out["highlightWords"] if isinstance(w, dict)]
    return out


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


def cta_y(height: float, blocked: list[tuple[float, float]]) -> tuple[float, bool]:
    """Центр плашки-призыва по высоте и «влезла ли в свободное место».

    Свободные зоны — безопасная область кадра минус субтитры (и герой, если известен).
    Берём самую нижнюю, где плашка помещается: призыв читается в нижней трети, а лицо
    обычно выше. Плашка прижимается к низу зоны — ровно над субтитрами."""
    from app.agent_tools import SAFE_BOTTOM, SAFE_TOP

    gap = 0.015
    zones = _free_zones(blocked, SAFE_TOP + 0.01, SAFE_BOTTOM - 0.01)
    fitting = [z for z in zones if z[1] - z[0] >= height + 2 * gap]
    if fitting:
        z0, z1 = max(fitting, key=lambda z: z[1])
        return round(z1 - gap - height / 2, 3), True
    if zones:
        z0, z1 = max(zones, key=lambda z: z[1] - z[0])
        return round((z0 + z1) / 2, 3), False
    return 0.5, False


def _subject_box(store: Any, clip_plan_id: int) -> dict | None:
    """Где герой (Gemini-зрение, кэш) — чтобы призыв не лёг на лицо. Без Gemini — None."""
    try:
        from app.ai.subject_box import subject_box

        return subject_box(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - без зрения — место по субтитрам
        return None


def build_cta(brand: dict, start: float, end: float, y: float, keyed: bool) -> list[dict]:
    """Плашка-призыв: текст бренда + адрес акцентом. На зелёнке плашка — за персонажем."""
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
            "props": {"kind": "rect", "w": 1080, "h": round(height), "fill": colors["plate"], "stroke": None},
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


def build_logo(brand: dict, asset: dict, start: float, end: float) -> dict:
    """Логотип в верхнем углу под шапкой площадки: ~1 % кадра, видео не закрывает."""
    from app.agent_tools import SAFE_TOP

    aw, ah = float(asset.get("width") or 0), float(asset.get("height") or 0)
    ratio = ah / aw if aw > 0 and ah > 0 else 1.0  # высота / ширина
    w = LOGO_PX if ratio <= 1 else LOGO_PX / ratio
    h = w * ratio
    half_x = (LOGO_MARGIN_PX + w / 2) / 1080
    x = half_x if brand["logo_corner"] == "top-left" else 1 - half_x
    return {
        "id": LOGO_ID, "type": "image", "name": "Бренд: логотип", "start": start, "end": end, "track": 4,
        "x": round(x, 4), "y": round(SAFE_TOP + (h / 2 + 16) / 1920, 4), "opacity": 0.95,
        "props": {"asset_id": int(asset["id"]), "w": round(w, 1)},  # высота — по пропорциям картинки
        "in": {"type": "fade", "duration": 0.4}, "out": {"type": "fade", "duration": 0.3},
    }


def apply_brand(
    store: Any, clip_plan_id: int, brand_id: int, *, before_change: Callable[[], None] | None = None,
) -> dict:
    """Оформить клип в стиле бренда. Повторный вызов даёт тот же результат (без дублей).

    ``before_change`` — снимок истории от вызывающего (MCP); без него снимок делается здесь.
    Ошибки — ValueError (слои не прошли проверку), KeyError (нет клипа/бренда)."""
    from app.agent_tools import clip_timeline, covering_problems, lint_layers
    from app.auto_edit import caption_y_for
    from app.chroma_key import normalize_backdrop

    brand = store.get_brand(int(brand_id))
    timeline = clip_timeline(store, clip_plan_id)
    plan, render, duration = timeline["plan"], dict(timeline["render"]), float(timeline["duration"])
    if duration <= 0:
        raise ValueError("в клипе нет кусков")
    keyed = bool(store.get_source(int(plan["source_id"])).get("chroma_key"))
    changes: list[str] = []
    warnings: list[str] = []
    layers = [dict(l) for l in render.get("layers") or [] if not _brand_owned(l)]

    # 1. Субтитры: перекрасить все слои captions; нет ни одного — поставить свой.
    caps = [l for l in layers if l.get("type") == "captions"]
    for layer in caps:
        layer["props"] = caption_props(layer.get("props") or {}, brand)
    if caps:
        changes.append(f"субтитры в стиле бренда ({len(caps)} слоя)" if len(caps) > 1 else "субтитры в стиле бренда")
    else:
        words = [w for w in (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
                 if str(w.get("word", "")).strip()]
        if words and not render.get("subs_on"):
            layers.append({
                "id": CAPS_ID, "type": "captions", "name": "Субтитры (бренд)", "start": 0.0, "end": duration,
                "track": 0, "x": 0.5, "y": caption_y_for(render), "props": caption_props({"maxWords": 3}, brand),
            })
            changes.append("субтитры-слой в стиле бренда")
        elif words:
            warnings.append("субтитры идут обычной дорожкой (стиль субтитров) — бренд их не красит; "
                            "⚡ автомонтаж сделает слой субтитров, затем примени бренд снова")
        else:
            warnings.append("в клипе нет распознанной речи — субтитров нет")

    # 2. Хук.
    hook = find_hook(layers)
    if hook is not None:
        hook["props"] = hook_props(hook.get("props") or {}, brand)
        changes.append("хук в стиле бренда")

    # 3. Звуковые эффекты по политике бренда.
    layers, dropped = apply_sfx_policy(layers, brand["sfx_policy"])
    if dropped:
        changes.append(f"снято звуковых эффектов: {dropped} (политика бренда «{brand['sfx_policy']}»)")
    transition = dict(render.get("transition") or {})
    if brand["sfx_policy"] == "none" and transition.get("sfx", "none") != "none":
        transition["sfx"] = "none"
        changes.append("без звука на склейках")

    # 4. Концовка-призыв на последние cta.duration_sec готового ролика.
    added: list[dict] = []
    cta = brand["cta"]
    cta_start: float | None = None
    if cta["text"] or cta["url_text"]:
        want = min(cta["duration_sec"], max(1.0, duration * 0.4))
        cta_start = tail_start(render.get("cuts") or [], duration, want)
        blocked = [
            (float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) - CAPTION_HALF, float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) + CAPTION_HALF)
            for l in layers
            if l.get("type") == "captions" and not l.get("hidden")
            and float(l.get("start") or 0) < duration and float(l.get("end") or 0) > cta_start
        ]
        box = _subject_box(store, clip_plan_id)
        if box:
            blocked.append((float(box["y0"]), float(box["y1"])))
        text = "\n".join(t for t in (cta["text"], cta["url_text"]) if t)
        height = (_cta_lines(text, CTA_SIZE) * CTA_SIZE * 1.15 + 2 * CTA_PAD_Y) / 1920
        y, fits = cta_y(height, blocked)
        if not fits:
            warnings.append("призыву не нашлось свободного места между субтитрами и героем — проверь view_frame, "
                            "при нужде подвинь brand_cta (update_layer y)")
        added += build_cta(brand, cta_start, duration, y, keyed)
        changes.append(f"призыв «{text.replace(chr(10), ' / ')}» с {cta_start:.1f} с до конца")

    # 5. Логотип: после хука (он тоже наверху) и до призыва.
    if brand.get("logo_asset_id"):
        try:
            asset = store.get_montage_asset(int(brand["logo_asset_id"]))
        except KeyError:
            asset = None
            warnings.append(f"логотипа #{brand['logo_asset_id']} больше нет в библиотеке — без логотипа")
        if asset is not None and asset.get("kind") not in ("image", "gif"):
            warnings.append(f"логотип #{asset['id']} — не картинка, пропущен")
            asset = None
        if asset is not None:
            start = 0.0
            if hook is not None and float(hook.get("y", 0.5)) < 0.35:
                start = float(hook.get("end") or 0.0)
            end = cta_start if cta_start is not None else duration
            if end - start >= 1.0:
                added.append(build_logo(brand, asset, round(start, 3), round(end, 3)))
                changes.append("логотип в углу")
            else:
                warnings.append("клип слишком короткий для логотипа между хуком и призывом")

    def asset_exists(asset_id: int) -> bool:
        try:
            store.get_montage_asset(int(asset_id))
            return True
        except (KeyError, TypeError, ValueError):
            return False

    problems = covering_problems(added, keyed) + motion.validate_layers(added, duration, asset_exists=asset_exists)
    if problems:
        raise ValueError("слои бренда не прошли проверку: " + "; ".join(problems))

    # 6. Музыка и фон за персонажем.
    if brand.get("music_track_id"):
        try:
            store.get_audio_track(int(brand["music_track_id"]))
            render["music_on"], render["track_id"] = True, int(brand["music_track_id"])
            changes.append("музыка бренда")
        except KeyError:
            warnings.append(f"трека #{brand['music_track_id']} больше нет — музыка не тронута")
    render["backdrop"] = normalize_backdrop(brand["backdrop"])
    if keyed:
        changes.append("фон за персонажем")

    render["layers"] = layers + added
    render["transition"] = transition
    if before_change is not None:
        before_change()
    else:
        from app.clip_history import snapshot

        snapshot(store, clip_plan_id, f"бренд «{brand['name']}»")
    fresh = store.set_clip_plan_render_settings(clip_plan_id, render)
    fresh_layers = (fresh.get("render_settings") or {}).get("layers") or []
    brand_ids = {str(l["id"]) for l in added}
    out: dict = {
        "ok": True,
        "brand": {"id": brand["id"], "name": brand["name"]},
        "changes": changes,
        "warnings": warnings + lint_layers(fresh_layers, duration, only_ids=brand_ids),
    }
    if cta_start is not None:
        out["check"] = (f"посмотри концовку: view_frame t={min(duration, cta_start + 0.8):.1f} — призыв читается "
                        "и не лежит на лице")
    return out
