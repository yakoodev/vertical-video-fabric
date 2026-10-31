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
* музыка бренда, фон за персонажем, политика звуковых эффектов;
* плашка канала (``nameplate``: значок площадки + ник) — весь ролик, под разделителем
  раскладки «игра + вебка» или в безопасном верхнем углу (``nameplate_place``); её же
  ставит отдельно ``set_nameplate``.

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
NAMEPLATE_ID = "brand_nameplate"

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

# Плашка канала: те же списки, что у слоя nameplate (app/motion.py, layers/nameplate.ts).
NAMEPLATE_PLATFORMS = motion.NAMEPLATE_PLATFORMS
NAMEPLATE_STYLES = motion.NAMEPLATE_STYLES
NAMEPLATE_POSITIONS = ("auto", "top-left", "top-right", "under-split-left", "under-split-center")
NAMEPLATE_OUTROS = ("auto", "collapse", "none")
# Цвет площадки — акцент плашки по умолчанию (зеркало PLATFORM_COLORS в layers/nameplate.ts).
PLATFORM_COLORS = {
    "twitch": "#9146ff", "youtube": "#ff0033", "tiktok": "#fe2c55", "telegram": "#229ed9", "kick": "#53fc18",
}
NAMEPLATE_SIZE_MIN, NAMEPLATE_SIZE_MAX = 40.0, 160.0
# Опорные px кадра 1080×1920: плашка на 72 px ниже разделителя игры и вебки (как ручная плашка
# шортсов), 24 px от края кадра, 16 px под верхней безопасной зоной; зазор до соседей — 16 px.
NAMEPLATE_GAP_PX = 72
NAMEPLATE_MARGIN_PX = 24
NAMEPLATE_TOP_PX = 16
NAMEPLATE_CLEAR_PX = 16
NAMEPLATE_SHADOW = {"color": "#000000", "blur": 14, "x": 0, "y": 4, "opacity": 0.45}
# Кнопки лайков/комментариев TikTok справа (9:16): x > 900, y 900–1600 из 1080×1920.
TIKTOK_BUTTONS = (900 / 1080, 1.0, 900 / 1920, 1600 / 1920)
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


def normalize_nameplate(raw: Any) -> dict:
    """Плашка канала бренда. Пустой ник и platform none — плашки нет (по умолчанию).
    accent None — цвет площадки (у none — акцент бренда при применении)."""
    src = raw if isinstance(raw, dict) else {}
    platform, style, position = src.get("platform"), src.get("style"), src.get("position")
    handle = _text(src.get("handle"), 40)
    fade, live = src.get("fade"), src.get("live")
    anim, outro = src.get("anim"), src.get("outro")
    return {
        "platform": platform if platform in NAMEPLATE_PLATFORMS else "none",
        "handle": handle,
        "style": style if style in NAMEPLATE_STYLES else "glass",
        "accent": _color(src.get("accent")),
        "position": position if position in NAMEPLATE_POSITIONS else "auto",
        "size": _num(src.get("size"), float(motion.NAMEPLATE_SIZE), NAMEPLATE_SIZE_MIN, NAMEPLATE_SIZE_MAX),
        "fade": fade is True or fade in (1, "1", "true"),
        # Своя анимация слоя: вход от значка + редкий блик/тик (subtle), ярче (lively), стоит (none).
        "anim": anim if anim in motion.NAMEPLATE_ANIMS else "subtle",
        "live": live is True or live in (1, "1", "true"),
        # Свёртка в значок: auto — перед призывом бренда (если он есть), collapse — в конце ролика.
        "outro": outro if outro in NAMEPLATE_OUTROS else "auto",
    }


def normalize_brand(raw: Any) -> dict:
    """Бренд в полной форме (без id и дат) — так он хранится и так отдаётся API."""
    src = raw if isinstance(raw, dict) else {}
    colors_src = src.get("colors") if isinstance(src.get("colors"), dict) else {}
    colors = {k: _color(colors_src.get(k)) or v for k, v in DEFAULT_COLORS.items()}
    # Второй цвет бренда — подсветка ключевых слов субтитров (необязательный).
    if _color(colors_src.get("highlight")):
        colors["highlight"] = _color(colors_src.get("highlight"))
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
        # Фон за персонажем и под видео meme — анимированный в цветах бренда (app/backgrounds.py).
        "backdrop": brand_backdrop(src.get("backdrop"), colors),
        "sfx_policy": policy if policy in SFX_POLICIES else "minimal",
        # Плашка канала (значок площадки + ник) на весь ролик; пустая — не ставится.
        "nameplate": normalize_nameplate(src.get("nameplate")),
    }


def _brand_palette(colors: dict) -> list[str]:
    return [colors["plate"], colors["accent"]] + ([colors["highlight"]] if colors.get("highlight") else [])


def brand_backdrop(raw: Any, colors: dict) -> dict:
    """Фон бренда, как он хранится: по умолчанию анимированный (аврора), цвета ``[]`` —
    «цвета бренда» (плашка, акцент, подсветка; правка цветов бренда меняет и фон).
    Свой ``{kind, colors, …}`` — как есть; заливка/градиент — только если выбраны явно.
    Старый ``{color, color2}`` без ``kind`` — тоже анимированный: его цвет — основа."""
    from app.backgrounds import normalize_background

    src = raw if isinstance(raw, dict) else {}
    if src.get("kind"):
        return normalize_background(src)
    base = _color(src.get("color"))
    custom = [base, *_brand_palette(colors)[1:]] if base and base != colors["plate"] else []
    return normalize_background({"kind": "aurora", "colors": custom})


def resolved_backdrop(brand: dict) -> dict:
    """Фон бренда для клипа: цвета «бренда» подставлены явно."""
    bg = dict(brand["backdrop"])
    if not bg.get("colors") and bg.get("kind") not in ("solid", "gradient"):
        bg["colors"] = _brand_palette(brand["colors"])
    return bg


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


def _subject_box(store: Any, clip_plan_id: int) -> dict | None:
    """Где герой (Gemini-зрение, кэш) — чтобы призыв не лёг на лицо. Без Gemini — None."""
    try:
        from app.ai.subject_box import subject_box

        return subject_box(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - без зрения — место по субтитрам
        return None


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
) -> dict:
    """Где поставить плашку канала: ``{x, y, align, boxWidth, position, fits, note}``.

    ``auto`` и ``under-split-*`` берут линию разделения из раскладки клипа (``render.layout`` split:
    игра и вебка полосами) — плашка на ``NAMEPLATE_GAP_PX`` ниже разделителя, а если там субтитры
    (их ставят на шов) — ниже субтитров. Нет раскладки или не влезает — верхний безопасный угол
    (у логотипа бренда — противоположный). В любом месте плашка уходит ниже того, что ей мешает:
    субтитры, хук, логотип, окошко вебки pip, кнопки TikTok справа (9:16); до нижней безопасной
    зоны площадки (подпись ролика) не опускается — тогда ``fits`` False.
    ``logo_corner`` — угол логотипа бренда (нет логотипа — None)."""
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
    if hook is not None and not hook.get("hidden"):
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


def build_nameplate(np: dict, place: dict, duration: float, font: str = "", cta_start: float | None = None) -> dict:
    """Слой плашки канала на весь ролик. Анимация — своя у слоя (``anim``: вход от значка, редкий
    блик и тик значка); ``fade`` — мягкий вход только у неподвижной (``anim: none``).
    Свёртка в значок (``outro``): auto — если есть призыв (``cta_start``), плашка сворачивается
    за NAMEPLATE_OUTRO_SEC с его начала и уходит; collapse — то же в конце ролика."""
    collapse = np["outro"] == "collapse" or (np["outro"] == "auto" and cta_start is not None)
    end = duration
    if collapse and cta_start is not None:
        end = min(duration, max(cta_start, 2 * motion.NAMEPLATE_OUTRO_SEC + 0.5) + motion.NAMEPLATE_OUTRO_SEC)
    props = {
        "platform": np["platform"], "handle": np["handle"], "style": np["style"], "accent": np["accent"],
        "size": np["size"], "font": font, "weight": 800, "color": "#ffffff",
        "align": place["align"], "boxWidth": place["boxWidth"],
        "anim": np["anim"], "live": np["live"], "outro": "collapse" if collapse else "none",
    }
    return {
        "id": NAMEPLATE_ID, "type": "nameplate", "name": "Плашка канала", "start": 0.0, "end": round(end, 3),
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
) -> dict:
    """Поставить (или снять) плашку канала на клип — без остального оформления бренда.

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
    np = normalize_nameplate({**base, **{k: v for k, v in (settings or {}).items() if v is not None}})
    if not nameplate_enabled(np):
        raise ValueError("плашке нечего показать: нужен handle (ник) и/или platform")
    if brand and not np["accent"] and np["platform"] == "none":
        np["accent"] = brand["colors"]["accent"]
    fmt = settings_layers_format(render)
    corner = (brand["logo_corner"] if brand and brand.get("logo_asset_id") else None) or _logo_corner_of(layers)
    place = nameplate_place(render, layers, np, logo_corner=corner, fmt=fmt)
    cta = next((l for l in layers if l.get("id") == CTA_ID and not l.get("hidden")), None)
    cta_start = float(cta.get("start") or 0.0) if cta is not None else None
    layer = build_nameplate(np, place, duration, (brand or {}).get("font") or "", cta_start)
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
        "warnings": warnings + lint_layers(fresh_layers, duration, only_ids={NAMEPLATE_ID}, fmt=fmt),
        "check": f"view_frame t={min(duration, 1.0):.1f} — плашка не на лице и не под субтитрами",
    }


def apply_brand(
    store: Any, clip_plan_id: int, brand_id: int, *, before_change: Callable[[], None] | None = None,
    nameplate: Any = None,
) -> dict:
    """Оформить клип в стиле бренда. Повторный вызов даёт тот же результат (без дублей).

    ``before_change`` — снимок истории от вызывающего (MCP); без него снимок делается здесь.
    ``nameplate`` — плашка канала вместо бренда: dict — поля поверх плашки бренда, False — снять;
    None — как в бренде (у бренда её нет — уже стоящая плашка, например от set_nameplate, остаётся).
    Ошибки — ValueError (слои не прошли проверку), KeyError (нет клипа/бренда)."""
    from app.agent_tools import clip_timeline, covering_problems, lint_layers
    from app.auto_edit import caption_y_for
    from app.chroma_key import normalize_backdrop
    from app.formats import ref_size, settings_layers_format

    brand = store.get_brand(int(brand_id))
    timeline = clip_timeline(store, clip_plan_id)
    plan, render, duration = timeline["plan"], dict(timeline["render"]), float(timeline["duration"])
    if duration <= 0:
        raise ValueError("в клипе нет кусков")
    keyed = bool(store.get_source(int(plan["source_id"])).get("chroma_key"))
    # Слои бренда — в опорном кадре формата разметки слоёв клипа (9:16 — 1080×1920, как всегда).
    fmt = settings_layers_format(render)
    ref_h = ref_size(fmt)[1]
    cap_half = CAPTION_HALF if ref_h == 1920 else CAPTION_HALF * 1920 / ref_h
    changes: list[str] = []
    warnings: list[str] = []
    old_plate = next((dict(l) for l in render.get("layers") or [] if l.get("id") == NAMEPLATE_ID), None)
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

    # 3b. Плашка канала — весь ролик; место — до призыва, чтобы призыв её обошёл.
    added: list[dict] = []
    plate: dict | None = None
    plate_band: tuple[float, float] | None = None
    np = dict(brand["nameplate"])
    if isinstance(nameplate, dict):
        np = normalize_nameplate({**np, **{k: v for k, v in nameplate.items() if v is not None}})
    if nameplate is False:
        if old_plate is not None:
            changes.append("плашка канала снята")
    elif nameplate_enabled(np):
        if not np["accent"] and np["platform"] == "none":
            np["accent"] = brand["colors"]["accent"]
        place = nameplate_place(render, layers, np, logo_corner=brand["logo_corner"] if brand.get("logo_asset_id") else None,
                                fmt=fmt)
        plate = build_nameplate(np, place, duration, brand["font"])
        added.append(plate)
        # Плашка, которая сворачивается перед призывом, ему не мешает; остающаяся — призыв обходит.
        if np["outro"] == "none" or not (brand["cta"]["text"] or brand["cta"]["url_text"]):
            plate_band = (plate["y"] - np["size"] / ref_h / 2, plate["y"] + np["size"] / ref_h / 2)
        changes.append(f"плашка канала «{np['handle'] or np['platform']}» {place['note']}")
        if not place["fits"] or np["position"] not in ("auto", place["position"]):
            warnings.append(place["note"])
    elif old_plate is not None:
        layers.append(old_plate)  # у бренда плашки нет — поставленная руками/set_nameplate остаётся

    # 4. Концовка-призыв на последние cta.duration_sec готового ролика.
    cta = brand["cta"]
    cta_start: float | None = None
    if cta["text"] or cta["url_text"]:
        want = min(cta["duration_sec"], max(1.0, duration * 0.4))
        cta_start = tail_start(render.get("cuts") or [], duration, want)
        blocked = [
            (float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) - cap_half, float(l.get("y", motion.DEFAULT_CAPTIONS_Y)) + cap_half)
            for l in layers
            if l.get("type") == "captions" and not l.get("hidden")
            and float(l.get("start") or 0) < duration and float(l.get("end") or 0) > cta_start
        ]
        box = _subject_box(store, clip_plan_id)
        if box:
            blocked.append((float(box["y0"]), float(box["y1"])))
        if plate_band:
            blocked.append(plate_band)
        text = "\n".join(t for t in (cta["text"], cta["url_text"]) if t)
        height = (_cta_lines(text, CTA_SIZE) * CTA_SIZE * 1.15 + 2 * CTA_PAD_Y) / ref_h
        y, fits = cta_y(height, blocked, fmt)
        if not fits:
            warnings.append("призыву не нашлось свободного места между субтитрами и героем — проверь view_frame, "
                            "при нужде подвинь brand_cta (update_layer y)")
        added += build_cta(brand, cta_start, duration, y, keyed, fmt)
        changes.append(f"призыв «{text.replace(chr(10), ' / ')}» с {cta_start:.1f} с до конца")
        if plate is not None and np["outro"] == "auto":
            # Плашка сворачивается в значок, когда выходит призыв.
            added[added.index(plate)] = plate = build_nameplate(np, place, duration, brand["font"], cta_start)
            changes.append(f"плашка канала сворачивается к призыву ({cta_start:.1f} с)")

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
                added.append(build_logo(brand, asset, round(start, 3), round(end, 3), fmt))
                changes.append("логотип в углу")
            else:
                warnings.append("клип слишком короткий для логотипа между хуком и призывом")

    def asset_exists(asset_id: int) -> bool:
        try:
            store.get_montage_asset(int(asset_id))
            return True
        except (KeyError, TypeError, ValueError):
            return False

    problems = covering_problems(added, keyed, fmt) + motion.validate_layers(added, duration, asset_exists=asset_exists)
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
    render["backdrop"] = normalize_backdrop(resolved_backdrop(brand))
    if keyed:
        changes.append("фон за персонажем")
    layout = render.get("layout")
    if isinstance(layout, dict) and layout.get("mode") == "meme":
        # Фон под видео meme — тот же фон бренда (анимированный), а не заливка.
        render["layout"] = {**layout, "bg": render["backdrop"]}
        changes.append("фон под видео")

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
        "warnings": warnings + lint_layers(fresh_layers, duration, only_ids=brand_ids, fmt=fmt),
    }
    if cta_start is not None:
        out["check"] = (f"посмотри концовку: view_frame t={min(duration, cta_start + 0.8):.1f} — призыв читается "
                        "и не лежит на лице")
    return out
