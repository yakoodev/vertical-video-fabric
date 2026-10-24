"""Заготовка промо приложения: «рецепт» сильного промо → детерминированная сборка слоёв.

Зачем. Claude-сабагенты собирали промо приложений на холсте очень хорошо (клипы 574, 579:
телефон меняет место и наклон по сценам, наезды на кнопки на долях, крупные окна записи на
кульминации, вспышка/кольцо/искры, финал с плашкой CTA), а Gemini теми же инструментами — корректно,
но скучно (прогон №3, клип 580: телефон весь ролик в одной точке, мелкие титры, наезды почти
незаметны). Здесь общие приёмы эталонов собраны в одну чистую функцию: Gemini описывает промо
коротким spec (хук, сцены с записями, финал, музыка), получает сильную основу и только подстраивает.

Приёмы (сборщики эталонов: scratchpad/vtubikanews/build.py, rentplay/build_v3.py, collabika/build_v2.py):

* сетка долей по BPM трека; музыка стартует так, чтобы дроп (drop_sec) пришёлся на кульминацию,
  иначе — с energy_start_sec (без тихого вступления);
* хук 2–4 с: 1–3 строки-удара крупно (110–150 px), каждая на свою долю, последняя — акцентом с
  вспышкой и кольцом; под ним — затемнённая запись кульминации во весь кадр;
* сцены по долям, телефон чередует позы (крупно по центру снизу / слева с наклоном / справа с
  наклоном); стык — свайп: прежний уезжает в сторону, следующий въезжает со своей стороны на
  0.1 с раньше, чем прежний скрылся (пустого кадра нет, с окном записи и без);
* наезд камеры (followCamera у телефона) на focus — точку интереса — на сильной доле, касание
  кольцом там же; глубина и центр наезда подобраны так, чтобы корпус не уходил за кадр, а верх
  корпуса оставался ниже титра сцены (зазор ≥ 24 px и на пике наезда);
* окно записи (video с crop) на ключевой момент сцены с window — поверх своего телефона, который
  остаётся под ним до конца сцены (убрали окно — телефон на месте); кульминация — на сильной доле
  (дроп трека или первая доля такта): окно + вспышка + кольцо + частицы + бас-дроп;
* титры сцен над телефоном с затемнением (props.backdrop), крупно, хвост после «—» — акцентом;
* финал ≥ 3 с: логотип с частицами, строки даты/оффера, плашка CTA ≥ 56 px со ссылкой;
* тихие звуки из пака freesound (только существующие имена): свайпы, окна, кульминация, финал.

Результат проходит motion.validate_layers и линт промо (app/promo_lint.py) без предупреждений и
укладывается в пределы (motion.MAX_LAYERS, MAX_CAMERA_KEYS). Все слои — с именем «promo: …»:
инструмент build_app_promo при повторном вызове заменяет только их.

Форматы (app/formats.py). Заготовка размечает слои в опорном кадре 9:16 (``layers_format`` 9:16):
клип 9:16 — как всегда; 1:1 и 4:5 — те же слои переносятся в кадр формата одним масштабом
(app/aspect.py, телефон с титром — колонкой, ≈0.57 в квадрате, ≈0.7 в 4:5). 16:9 — честный отказ
(``ScaffoldError``): приёмы эталонов — «телефон во весь рост + титр над ним + наезд на кнопку» —
держатся на высоком кадре; в горизонтали нужна своя раскладка (телефон сбоку, титры рядом), её
заготовка не собирает — промо 16:9 собирают из вертикали (set_format 16:9 переносит колонкой по
центру) или вручную слоями с layers_format 16:9.
"""

from __future__ import annotations

import math
import random
import re
from typing import Any

from app.formats import ref_size, settings_format

# Формат, в опорном кадре которого заготовка размечает слои, и форматы клипа, где она работает.
SCAFFOLD_FORMAT = "9:16"
SCAFFOLD_OUTPUT_FORMATS = ("9:16", "1:1", "4:5")
LANDSCAPE_REFUSAL = ("build_app_promo — только для 9:16 / 1:1 / 4:5: заготовка промо (телефон во весь рост, титр "
                     "над ним, наезды) держится на высоком кадре, в 16:9 своей раскладки у неё нет")
W, H = (float(v) for v in ref_size(SCAFFOLD_FORMAT))
MIN_DURATION_SEC, MAX_DURATION_SEC = 20.0, 40.0
DEFAULT_BPM = 120.0
MAX_SCENES = 6
MAX_HOOK_LINES = 3
MAX_TITLE_CHARS = 42
NAME_PREFIX = "promo:"
ID_PREFIX = "pr"

# Телефон: ширина экрана (опорные px), строка статуса, рамка — как device.ts phoneMetrics.
SCREEN_W = 520.0
STATUS_BAR = 0.14
EDGE = 0.017 + 0.03
DEFAULT_ASPECT = 19.5 / 9  # запись экрана, если размер файла неизвестен
TITLE_TOP = 0.106  # верх титров сцен: чуть ниже безопасной зоны площадки (0.10)
TITLE_GAP_PX = 34.0  # от низа титра до верха корпуса (плюс запас на наклон)
TITLE_MAXW = 1000.0
# Позы телефона (x, y, scale, наклон): центр снизу крупно / слева / справа (эталон rentplay v3).
POSES = {
    "center": (0.5, 0.63, 1.04, -2.5),
    "left": (0.41, 0.60, 0.95, -7.0),
    "right": (0.59, 0.60, 0.95, 7.0),
}
POSE_CYCLES = (("center", "left", "right"), ("left", "right", "center"), ("right", "center", "left"))
ZOOM_MAX = 1.3
ZOOM_MIN = 1.12
FOCUS_AT = (0.5, 0.56)  # куда камера ставит точку интереса (доли кадра)
MAX_OFF_FRAME = 0.08  # доля корпуса за кадром на пике наезда (линт ругается с 0.12)
# Наезд не загоняет телефон под титр: верх корпуса (с наклоном) в кадре после камеры — ниже низа
# титра сцены на столько px (линт и тесты требуют ≥ 24; запас — на тряску камеры кульминации).
ZOOM_TITLE_GAP_PX = 36.0
ZOOM_FOCUS_MAX_Y = 0.92  # кнопка касания на пике наезда — не ниже этой доли кадра
WINDOW_MAX_W = 960.0  # окно записи: уже 0.9 кадра — линт считает его сценой, а не фоном
WINDOW_PUSH = 0.92  # телефон под окном записи чуть отходит назад (масштаб)
# Стык сцен — свайп: прежний телефон уезжает за EXIT_SEC до конца сцены, следующий въезжает
# за ENTER_SEC и стартует на OVERLAP_SEC раньше конца прежнего — пустого кадра на стыке нет.
ENTER_SEC = 0.36
EXIT_SEC = 0.34
OVERLAP_SEC = 0.1
NONE = {"type": "none", "duration": 0.05}
INK = "#0b1018"

# Звуки — только существующие в паке freesound (tools/fetch_sfx.py).
SFX = {
    "riser": ("riser-subtle.ogg", 2.62),
    "impact": ("impact-low.ogg", 1.5),
    "whoosh": ("whoosh-1.ogg", 0.42),
    "whip": ("whip.ogg", 0.22),
    "drop": ("bass-drop.ogg", 1.8),
    "outro": ("whoosh-cinematic.ogg", 1.3),
    "pop": ("pop.ogg", 0.45),
}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


class ScaffoldError(ValueError):
    """Spec не годится: сообщение + конкретные проблемы (агент исправит и повторит)."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems or [])


# ---------------------------------------------------------------- spec


def _num(value: Any, default: float | None = None) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _point(raw: Any, where: str, problems: list[str], keys: tuple[str, ...] = ("x", "y")) -> dict | None:
    if raw in (None, {}):
        return None
    if not isinstance(raw, dict):
        problems.append(f"{where}: объект {{{', '.join(keys)}}} в долях экрана записи 0..1")
        return None
    out = {}
    for key in keys:
        value = _num(raw.get(key))
        if value is None or not 0.0 <= value <= 1.0:
            problems.append(f"{where}.{key}: доля экрана записи 0..1")
            return None
        out[key] = round(value, 4)
    return out


def normalize_spec(spec: Any) -> dict:
    """Проверить и дополнить spec умолчаниями; ScaffoldError со списком проблем."""
    if not isinstance(spec, dict):
        raise ScaffoldError("spec — объект {brand, hook, scenes, end, music?, duration?}")
    problems: list[str] = []
    duration = _num(spec.get("duration"))
    if spec.get("duration") is not None and (duration is None or not MIN_DURATION_SEC <= duration <= MAX_DURATION_SEC):
        problems.append(f"duration: {MIN_DURATION_SEC:g}–{MAX_DURATION_SEC:g} с (не задано — по сценам)")

    brand_raw = spec.get("brand") if isinstance(spec.get("brand"), dict) else {}
    brand = {"accent": str(brand_raw.get("accent") or ""), "text": str(brand_raw.get("text") or "#ffffff"),
             "dark": str(brand_raw.get("dark") or INK), "font": str(brand_raw.get("font") or "Unbounded")[:60],
             "weight": int(_num(brand_raw.get("weight"), 900) or 900)}
    for key in ("accent", "text", "dark"):
        if not COLOR_RE.match(brand[key]):
            problems.append(f"brand.{key}: цвет #rrggbb" + (" (фирменный акцент, обязателен)" if key == "accent" else ""))
    brand["weight"] = min(900, max(100, brand["weight"]))

    hook = spec.get("hook")
    if isinstance(hook, str):
        hook = [hook]
    hook = [" ".join(str(h).split()) for h in hook or [] if str(h or "").strip()]
    if not 1 <= len(hook) <= MAX_HOOK_LINES:
        problems.append(f"hook: 1–{MAX_HOOK_LINES} строки-удара по 1–3 слова («Новинка вышла?», «Не покупай.», «Арендуй.»)")
    for i, line in enumerate(hook):
        if len(line) > 28:
            problems.append(f"hook[{i}]: «{line[:30]}…» — {len(line)} символов; удар хука ≤ 28 символов (1–3 слова)")

    scenes = []
    raw_scenes = spec.get("scenes") if isinstance(spec.get("scenes"), list) else []
    if not 1 <= len(raw_scenes) <= MAX_SCENES:
        problems.append(f"scenes: 1–{MAX_SCENES} сцен [{{asset_id, from, to, title, focus?, window?, climax?}}]")
    for i, raw in enumerate(raw_scenes[:MAX_SCENES]):
        where = f"scenes[{i}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: объект сцены")
            continue
        asset = raw.get("asset_id")
        try:
            asset = int(asset)
        except (TypeError, ValueError):
            problems.append(f"{where}.asset_id: id записи экрана из библиотеки (list_assets, kind video)")
            asset = None
        start = _num(raw.get("from"), 0.0) or 0.0
        end = _num(raw.get("to"))
        if start < 0 or end is None or end - start < 0.5:
            problems.append(f"{where}: from/to — секунды записи, to − from ≥ 0.5")
        title = " ".join(str(raw.get("title") or "").split())
        if not title:
            problems.append(f"{where}.title: титр сцены 2–6 слов («Код Steam Guard — кнопкой»)")
        elif len(title) > MAX_TITLE_CHARS:
            problems.append(f"{where}.title: {len(title)} символов — ≤ {MAX_TITLE_CHARS} (на экране читают 3–6 слов)")
        focus = _point(raw.get("focus"), f"{where}.focus", problems)
        if focus is not None and isinstance(raw.get("focus"), dict) and raw["focus"].get("t") is not None:
            t = _num(raw["focus"].get("t"))
            if t is None or t < 0:
                problems.append(f"{where}.focus.t: секунда записи, когда нажимают кнопку")
            else:
                focus["t"] = round(t, 3)
        window = _point(raw.get("window"), f"{where}.window", problems, ("x", "y", "w", "h"))
        if window is not None:
            if window["w"] < 0.2 or window["h"] < 0.04 or window["x"] + window["w"] > 1.0001 \
                    or window["y"] + window["h"] > 1.0001:
                problems.append(f"{where}.window: x+w ≤ 1, y+h ≤ 1, w ≥ 0.2 (окно записи — доли её кадра)")
                window = None
        scenes.append({"asset_id": asset, "from": round(start, 3), "to": round(end or start + 3.0, 3),
                       "title": title, "focus": focus, "window": window, "climax": bool(raw.get("climax"))})
    if sum(1 for s in scenes if s["climax"]) > 1:
        problems.append("climax: true — только у одной сцены")

    end_raw = spec.get("end") if isinstance(spec.get("end"), dict) else {}
    logo = end_raw.get("logo_asset_id")
    if logo not in (None, ""):
        try:
            logo = int(logo)
        except (TypeError, ValueError):
            problems.append("end.logo_asset_id: id картинки логотипа из библиотеки")
            logo = None
    else:
        logo = None
    lines = end_raw.get("lines")
    if isinstance(lines, str):
        lines = [lines]
    lines = [" ".join(str(l).split()) for l in lines or [] if str(l or "").strip()][:2]
    cta = " ".join(str(end_raw.get("cta") or "").split())
    if not cta:
        problems.append("end.cta: ссылка или призыв на плашке («t.me/Collabikabot»)")
    elif len(cta) > 32:
        problems.append(f"end.cta: {len(cta)} символов — плашка ≤ 32 символов")
    sub = " ".join(str(end_raw.get("sub") or "").split())
    for text in lines + ([sub] if sub else []):
        if len(text) > 34:
            problems.append(f"end: строка «{text[:30]}…» длиннее 34 символов — разбей или сократи")
    if logo is None and not lines:
        problems.append("end: нужен logo_asset_id или хотя бы одна строка lines")

    music = spec.get("music")
    if music is not None and not isinstance(music, dict):
        problems.append("music: {track_id, start_sec?} или null")
        music = None
    if isinstance(music, dict):
        try:
            music = {**music, "track_id": int(music.get("track_id"))}
        except (TypeError, ValueError):
            problems.append("music.track_id: id трека из list_music")
            music = None

    background = spec.get("background")
    if background is not None and not isinstance(background, (dict, str)):
        problems.append("background: {kind, colors, speed, intensity} как у set_backdrop")
    try:
        variant = int(_num(spec.get("variant"), 0) or 0)
    except (TypeError, ValueError, OverflowError):
        variant = 0
    if problems:
        raise ScaffoldError("spec заготовки не принят", problems)
    return {"duration": duration, "brand": brand, "hook": hook, "scenes": scenes,
            "end": {"logo_asset_id": logo, "lines": lines, "cta": cta, "sub": sub},
            "music": music, "background": background, "variant": variant}


# ---------------------------------------------------------------- цвет и текст


def _rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def _mix(a: str, b: str, k: float) -> str:
    """Цвет a, смешанный с b на долю k."""
    ra, rb = _rgb(a), _rgb(b)
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(ra, rb))


def _ink_on(accent: str) -> str:
    """Цвет текста на плашке акцентного цвета: тёмный или белый — что контрастнее."""
    from app.promo_lint import contrast

    dark, white = contrast(INK, accent) or 0.0, contrast("#ffffff", accent) or 0.0
    return INK if dark >= white else "#ffffff"


def _char_w(font: str) -> float:
    """Ширина буквы в долях кегля (с запасом): Unbounded широкий."""
    name = font.lower()
    if "unbounded" in name or "russo" in name or "rubik mono" in name:
        return 0.8
    if "montserrat" in name or "exo" in name:
        return 0.68
    return 0.62


def _split_words(text: str, max_chars: int) -> list[str]:
    """Текст на 1–2 строки: «голова — хвост» по тире, иначе поровну по словам."""
    for sep in (" — ", " – ", " - "):
        if sep in text:
            head, tail = text.split(sep, 1)
            return [head.strip(), "— " + tail.strip()]
    words = text.split()
    if len(text) <= max_chars or len(words) < 2:
        return [text]
    best = min(range(1, len(words)),
               key=lambda i: (abs(len(" ".join(words[:i])) - len(" ".join(words[i:]))), i))
    return [" ".join(words[:best]), " ".join(words[best:])]


def _fit_size(lines: list[str], font: str, lo: float, hi: float, width: float = TITLE_MAXW) -> float:
    longest = max((len(l) for l in lines), default=1)
    return float(max(lo, min(hi, math.floor(width / (max(1, longest) * _char_w(font)) / 2) * 2)))


def _text_layer(text: str, size: float, color: str, brand: dict, *, hl: list[int] | None = None,
                hl_color: str | None = None, backdrop: bool = True, bg: dict | None = None,
                weight: int | None = None, lh: float = 1.04) -> dict:
    props: dict = {"text": text, "font": brand["font"], "weight": weight or brand["weight"], "size": size,
                   "color": color, "align": "center", "maxWidth": TITLE_MAXW, "lineHeight": lh, "stroke": None,
                   "bg": bg}
    if hl:
        props["highlight"] = {"words": hl, "color": hl_color or brand["accent"]}
    if backdrop:
        props["backdrop"] = {"color": "#000000", "opacity": 0.55, "spread": 120}
    return props


def _half_h(props: dict) -> float:
    """Половина высоты текста в долях кадра — той же оценкой, что линт безопасных зон."""
    from app.agent_tools import text_half_height

    return text_half_height(props)


# ---------------------------------------------------------------- геометрия телефона


def _phone_size(aspect: float) -> tuple[float, float, float, float]:
    """(корпус w, корпус h, экран записи h, строка статуса h) в опорных px при scale 1."""
    rec_h = SCREEN_W * aspect
    bar = SCREEN_W * STATUS_BAR
    edge = SCREEN_W * EDGE
    return SCREEN_W + 2 * edge + 2 * SCREEN_W * 0.012, rec_h + bar + 2 * edge, rec_h, bar


def _on_phone(pose: tuple, fx: float, fy: float, aspect: float) -> tuple[float, float]:
    """Точка экрана записи (доли) → доли кадра (без камеры), с наклоном телефона."""
    x, y, s, rot = pose
    _bw, _bh, rec_h, bar = _phone_size(aspect)
    dx = (fx - 0.5) * SCREEN_W * s
    dy = ((fy - 0.5) * rec_h + bar / 2) * s
    a = math.radians(rot)
    return x + (dx * math.cos(a) - dy * math.sin(a)) / W, y + (dx * math.sin(a) + dy * math.cos(a)) / H


def _cam_center(p: tuple[float, float], z: float) -> tuple[float, float]:
    lo, hi = 0.5 / z, 1 - 0.5 / z
    cx = min(hi, max(lo, p[0] - (FOCUS_AT[0] - 0.5) / z))
    cy = min(hi, max(lo, p[1] - (FOCUS_AT[1] - 0.5) / z))
    return round(cx, 4), round(cy, 4)


def _fit_zoom(phone: dict, natural: tuple | None, point: tuple[float, float], z_max: float,
              title_bottom: float) -> tuple[float, float, tuple]:
    """Самый глубокий наезд ≤ z_max → (zoom, пик zoom на удержании, центр камеры).

    Условия и на zoom, и на пике удержания (zoom + 0.04): корпус почти весь в кадре (линт
    off_frame), верх корпуса с наклоном — ниже низа титра сцены на ZOOM_TITLE_GAP_PX (центр
    камеры поднимается по холсту — меньше y, телефон в кадре опускается), кнопка касания остаётся в кадре.
    Не вышло ни при каком zoom ≥ ZOOM_MIN — (1.0, 1.0, центр): вызывающий уменьшит телефон.
    Прогон Gemini №4 (клип 581): центр ставил кнопку на 0.56 кадра — верх телефона уходил под титр."""
    from app.promo_lint import body_top, camera_view, layer_rect

    probe = {**phone, "keys": {}, "start": 0.0, "end": 1.0, "_natural": natural}
    frame = (0.0, 0.0, W, H)
    limit = title_bottom + ZOOM_TITLE_GAP_PX
    top = body_top(probe, 0.5)
    z = z_max
    while z >= ZOOM_MIN - 1e-9:
        peak = round(min(z + 0.04, z_max), 3)
        cx, cy = _cam_center(point, z)
        for zz in (z, peak):  # верх корпуса после камеры: H/2 + (top − cy·H)·zoom ≥ limit
            cy = min(cy, (top - (limit - H / 2) / zz) / H)
        cy = math.floor(max(cy, 0.5 / z) * 1e4) / 1e4
        ok = True
        for zz in (z, peak):
            cam = [{"t": 0.0, "zoom": zz, "x": cx, "y": cy}]
            rect = layer_rect(probe, 0.5, cam)
            area = (rect[2] - rect[0]) * (rect[3] - rect[1])
            inside = max(0.0, min(rect[2], W) - max(rect[0], 0.0)) * max(0.0, min(rect[3], H) - max(rect[1], 0.0))
            zv, vx, vy = camera_view(cam, 0.0)
            fx, fy = W / 2 + (point[0] - vx) * W * zv, H / 2 + (point[1] - vy) * H * zv
            if (area <= 0 or 1 - inside / area > MAX_OFF_FRAME or body_top(probe, 0.5, cam) < limit - 0.5
                    or not 0.06 * W <= fx <= 0.94 * W or fy > ZOOM_FOCUS_MAX_Y * H):
                ok = False
                break
        if ok:
            return round(z, 3), peak, (cx, cy)
        z -= 0.02
    return 1.0, 1.0, (0.5, 0.5)


# ---------------------------------------------------------------- сетка долей


class _Grid:
    def __init__(self, bpm: float):
        self.bpm = bpm
        self.beat = 60.0 / bpm

    def t(self, k: float) -> float:
        return round(k * self.beat, 4)

    def beats(self, sec: float) -> int:
        return int(round(sec / self.beat))


def _even(n: float) -> int:
    return int(2 * math.ceil(n / 2 - 1e-9))


def _plan_beats(spec: dict, grid: _Grid, asset_info: dict) -> dict:
    """Хук, сцены и финал в долях: всё на сетке, кульминация — на первой доле такта."""
    beat = grid.beat
    n = len(spec["hook"])
    per = max(2, int(round(1.15 / beat)))
    hook = per * n
    if hook * beat < 2.0:
        hook = int(math.ceil(2.0 / beat))
    for step in (4, 2):  # хук кончается на сильной доле, если влезает в 4 с
        rounded = int(step * math.ceil(hook / step))
        if rounded * beat <= 4.0 + 1e-6:
            hook = rounded
            break
    hook = min(hook, max(per, int(4.0 / beat)))
    final = _even(3.3 / beat)
    min_scene = max(4, _even(2.0 / beat))

    nat = []
    for scene in spec["scenes"]:
        length = scene["to"] - scene["from"]
        info = asset_info.get(str(scene["asset_id"])) or {}
        if info.get("duration"):
            length = min(length, max(0.5, float(info["duration"]) - scene["from"]))
        extra = 0.9 if (scene["window"] or scene["climax"]) else 0.0
        nat.append(min(4.2, max(2.4, length * 0.7)) + extra)
    fixed = (hook + final) * beat
    if spec["duration"]:
        scene_sec = spec["duration"] - fixed
    else:
        scene_sec = min(MAX_DURATION_SEC - fixed, max(MIN_DURATION_SEC - fixed, sum(nat)))
    total = max(len(nat) * min_scene, 2 * int(round(scene_sec / beat / 2)))
    while (hook + total + final) * beat > MAX_DURATION_SEC + 1e-6:
        total -= 2
    if total < len(nat) * min_scene:
        raise ScaffoldError("сцен слишком много для длины ролика", [
            f"{len(nat)} сцен по ≥ {min_scene * beat:.1f} с не влезают в {MAX_DURATION_SEC:g} с — оставь ≤ "
            f"{max(1, int(total // min_scene))} сцен или сократи хук"])
    while (hook + total + final) * beat < MIN_DURATION_SEC - 1e-6:
        total += 2
    # Доли по сценам пропорционально длине записи, чётными числами (полутакт), не меньше min_scene.
    weight = sum(nat)
    shares = [max(min_scene, _even(total * w / weight - 1)) for w in nat]
    while sum(shares) > total:
        i = max(range(len(shares)), key=lambda j: (shares[j] - min_scene, -j))
        if shares[i] <= min_scene:
            break
        shares[i] -= 2
    while sum(shares) < total:
        i = max(range(len(shares)), key=lambda j: (nat[j] / shares[j], -j))
        shares[i] += 2
    return {"hook": hook, "final": final, "scenes": shares, "per_line": per}


def _climax_index(scenes: list[dict]) -> int:
    for i, scene in enumerate(scenes):
        if scene["climax"]:
            return i
    windows = [i for i, s in enumerate(scenes) if s["window"]]
    if windows:
        return windows[-1]
    return max(0, len(scenes) - 2) if len(scenes) > 2 else len(scenes) - 1


def _strong_beat(lo: int, hi: int, want: float, bar: bool = True) -> int:
    """Доля в [lo, hi] ближе к want: сначала первая доля такта (кратная 4), потом чётная."""
    for step in ((4, 2) if bar else (2,)):
        options = [k for k in range(lo, hi + 1) if k % step == 0]
        if options:
            return min(options, key=lambda k: (abs(k - want), k))
    return max(lo, min(hi, int(round(want))))


# ---------------------------------------------------------------- сборка


class _Builder:
    def __init__(self, brand: dict):
        self.brand = brand
        self.layers: list[dict] = []
        self.camera: list[dict] = []
        self._n = 0

    def add(self, layer: dict, name: str) -> dict:
        self._n += 1
        layer = {"id": f"{ID_PREFIX}{self._n}", "name": f"{NAME_PREFIX} {name}"[:80], **layer}
        for key in ("start", "end", "x", "y"):
            if key in layer:
                layer[key] = round(float(layer[key]), 4)
        self.layers.append(layer)
        return layer

    def text(self, name: str, start: float, end: float, y: float, props: dict, *, track: int = 6,
             inn: dict | None = None, keys: dict | None = None, loop: dict | None = None,
             shadow: dict | None = None, out: dict | None = None) -> dict:
        layer = {"type": "text", "start": start, "end": end, "track": track, "x": 0.5, "y": y, "props": props,
                 "shadow": shadow or {"color": "#000000", "blur": 30, "x": 0, "y": 8, "opacity": 0.7},
                 "in": inn or {"type": "rise", "duration": 0.32}, "out": out or NONE}
        if keys:
            layer["keys"] = keys
        if loop:
            layer["loop"] = loop
        return self.add(layer, name)

    def flash(self, t: float, peak: float = 0.2, dur: float = 0.28) -> None:
        color = _mix("#ffffff", self.brand["accent"], 0.18)
        self.add({"type": "shape", "start": max(0.0, t - 0.02), "end": t + dur, "track": 7, "x": 0.5, "y": 0.5,
                  "blend": "add", "props": {"kind": "rect", "w": 1080, "h": 1920, "fill": color},
                  "keys": {"opacity": [{"t": 0, "v": peak, "ease": "outCubic"}, {"t": round(dur, 3), "v": 0.0}]},
                  "in": NONE, "out": NONE}, "вспышка")

    def ring(self, t: float, x: float, y: float, size: float = 300, grow: float = 2.4, follow: bool = False,
             dur: float = 0.6) -> None:
        accent = self.brand["accent"]
        layer = {"type": "shape", "start": t, "end": t + dur, "track": 5, "x": x, "y": y,
                 "props": {"kind": "ellipse", "w": size, "h": size, "fill": None, "stroke": {"color": accent, "width": 9}},
                 "shadow": {"color": accent, "blur": 30, "x": 0, "y": 0, "opacity": 0.9},
                 "keys": {"scale": [{"t": 0, "v": 0.35, "ease": "outCubic"}, {"t": dur, "v": grow}],
                          "opacity": [{"t": 0, "v": 1.0, "ease": "inQuad"}, {"t": dur, "v": 0.0}]},
                 "in": NONE, "out": NONE}
        if follow:
            layer["followCamera"] = True
        self.add(layer, "кольцо")

    def tap(self, t: float, x: float, y: float) -> None:
        self.add({"type": "shape", "start": t, "end": t + 0.5, "track": 4, "x": x, "y": y, "followCamera": True,
                  "props": {"kind": "ellipse", "w": 100, "h": 100, "fill": self.brand["accent"] + "55",
                            "stroke": {"color": "#ffffff", "width": 7}},
                  "keys": {"scale": [{"t": 0, "v": 0.45, "ease": "outCubic"}, {"t": 0.5, "v": 1.6}],
                           "opacity": [{"t": 0, "v": 1.0, "ease": "inQuad"}, {"t": 0.5, "v": 0.0}]},
                  "shadow": {"color": "#000000", "blur": 18, "x": 0, "y": 2, "opacity": 0.5},
                  "in": NONE, "out": NONE}, "касание")

    def particles(self, name: str, start: float, end: float, x: float, y: float, kind: str, count: int,
                  spread: float, size: float = 30, follow: bool = False, track: int = 5, **extra: Any) -> None:
        props = {"kind": kind, "count": count, "colors": [self.brand["accent"], "#ffffff"], "spread": spread,
                 "size": size, "glow": True, **extra}
        layer = {"type": "particles", "start": start, "end": end, "track": track, "x": x, "y": y, "props": props,
                 "in": NONE, "out": {"type": "fade", "duration": 0.25}}
        if follow:
            layer["followCamera"] = True
        self.add(layer, name)

    def sfx(self, key: str, at: float, volume: float, fade_out: float = 0.0) -> None:
        ref, dur = SFX[key]
        props = {"asset_id": f"sfx:freesound/{ref}", "volume": volume}
        if fade_out:
            props["fadeOut"] = fade_out
        self.add({"type": "audio", "start": max(0.0, at), "end": at + dur, "track": 7, "props": props}, f"звук {key}")

    def cam(self, t: float, zoom: float = 1.0, center: tuple = (0.5, 0.5), ease: str = "inOutCubic",
            shake: float | None = None) -> None:
        key = {"t": round(max(0.0, t), 3), "zoom": round(zoom, 3), "x": center[0], "y": center[1], "ease": ease}
        if shake is not None:
            key["shake"] = shake
        if self.camera and abs(self.camera[-1]["t"] - key["t"]) < 1e-3:
            self.camera[-1] = key
        else:
            self.camera.append(key)


def _aspect(asset_info: dict, asset_id: Any, crop: dict | None = None) -> float:
    info = asset_info.get(str(asset_id)) or {}
    w, h = _num(info.get("width")), _num(info.get("height"))
    base = h / w if w and h else DEFAULT_ASPECT
    if crop:
        base *= crop["h"] / crop["w"]
    return base


def _natural(asset_info: dict, asset_id: Any) -> tuple | None:
    info = asset_info.get(str(asset_id)) or {}
    w, h = _num(info.get("width")), _num(info.get("height"))
    return (w, h) if w and h else None


def _clamp_rec(asset_info: dict, asset_id: Any, t: float) -> float:
    info = asset_info.get(str(asset_id)) or {}
    dur = _num(info.get("duration"))
    if dur:
        t = min(t, max(0.0, dur - 0.05))
    return round(max(0.0, t), 3)


def _music_start(music: dict | None, climax_t: float, duration: float) -> tuple[float | None, str]:
    """С какой секунды трека играть: дроп — на кульминацию, иначе с энергичной части."""
    if not music:
        return None, ""
    if music.get("start_sec") is not None:
        return round(max(0.0, _num(music.get("start_sec"), 0.0) or 0.0), 3), "start_sec из spec"
    energy = _num(music.get("energy_start_sec"))
    drop = _num(music.get("drop_sec"))
    length = _num(music.get("duration"))
    floor = energy or 0.0
    if drop is not None:
        start = drop - climax_t
        if start >= floor - 0.25 and (not length or start + duration <= length):
            return round(max(0.0, start), 3), f"дроп трека ({drop:g} с) — на кульминации"
    start = floor
    if length and start + duration > length:
        start = max(0.0, length - duration)
    return round(start, 3), (f"с энергичной части трека ({floor:g} с)" if energy else "с начала трека")


def check_format(fmt: Any) -> str:
    """Формат клипа, для которого собирается заготовка; 16:9 — ScaffoldError с объяснением."""
    fmt = settings_format(fmt)
    if fmt not in SCAFFOLD_OUTPUT_FORMATS:
        raise ScaffoldError(LANDSCAPE_REFUSAL, [
            "собери промо в 9:16 (set_format(\"9:16\")), потом set_format(\"16:9\") — слои перенесутся "
            "колонкой по центру кадра (проверь view_frame)",
            "или горизонтальная раскладка вручную: set_format(\"16:9\", layers_format=\"16:9\") и слои в опорном "
            "кадре 1920×1080 (телефон слева/справа, титры сбоку, финал по центру)",
        ])
    return fmt


def build_app_promo(spec: Any, assets: dict | None = None, fmt: Any = None) -> dict:
    """spec → {layers, camera, music, backdrop, duration, plan, notes}. Чистая функция: тот же spec —
    те же слои. ``assets`` — {asset_id: {width, height, duration, kind}} из библиотеки (пропорции
    записей и логотипа, длина записей); spec.music может нести bpm/energy_start_sec/drop_sec/duration
    трека (инструмент подставляет их из list_music). ``fmt`` — формат клипа: 9:16, 1:1, 4:5 (слои —
    в опорном кадре 9:16, ``SCAFFOLD_FORMAT``); 16:9 — ScaffoldError (см. шапку модуля)."""
    check_format(fmt)
    spec = normalize_spec(spec)
    info = {str(k): v for k, v in (assets or {}).items()}
    brand = spec["brand"]
    accent, dark, text_color = brand["accent"], brand["dark"], brand["text"]
    music = spec["music"]
    bpm = _num((music or {}).get("bpm")) or DEFAULT_BPM
    if not 60 <= bpm <= 200:
        bpm = DEFAULT_BPM
    grid = _Grid(bpm)
    beats = _plan_beats(spec, grid, info)
    rng = random.Random(spec["variant"])
    cycle = POSE_CYCLES[spec["variant"] % len(POSE_CYCLES)]
    b = _Builder(brand)
    scenes = spec["scenes"]
    climax_i = _climax_index(scenes)

    hook_end = beats["hook"]
    final_start = hook_end + sum(beats["scenes"])
    total_beats = final_start + beats["final"]
    duration = round(grid.t(total_beats), 3)
    notes: list[str] = []

    # ------------------------------------------------ сцены: доли и позы
    plan_scenes = []
    cursor = hook_end
    for i, (scene, length) in enumerate(zip(scenes, beats["scenes"])):
        start, end = cursor, cursor + length
        cursor = end
        has_window = bool(scene["window"])
        climax = i == climax_i
        k = None  # доля окна / кульминации
        if has_window or climax:
            lo = start + min(2, length // 2)
            hi = end - max(2, min(4, length // 2))
            k = _strong_beat(lo, max(lo, hi), start + length * 0.5, bar=True)
        plan_scenes.append({"i": i, "start": start, "end": end, "k": k, "climax": climax, "window": has_window,
                            "pose": cycle[i % len(cycle)]})
    climax_scene = plan_scenes[climax_i]
    climax_beat = climax_scene["k"] if climax_scene["k"] is not None else climax_scene["start"]
    climax_t = grid.t(climax_beat)

    # ------------------------------------------------ хук
    hook = spec["hook"]
    per = beats["per_line"]
    # Хук — крупные удары над телефоном первой сцены, который въезжает снизу с первого кадра
    # (эталон 574): приложение видно сразу, слова — по одному на доли.
    hook_props = []
    for j, line in enumerate(hook):
        lines = _split_words(line, 10)
        size = _fit_size(lines, brand["font"], 110, 140)
        words = " ".join(lines).split()
        if j == len(hook) - 1:
            hook_props.append(_text_layer("\n".join(lines), size, accent, brand, lh=1.0))
        else:
            hook_props.append(_text_layer("\n".join(lines), size, text_color, brand, hl=[len(words) - 1], lh=1.0))
    hook_half = max(_half_h(p) for p in hook_props)
    hook_y = round(TITLE_TOP + hook_half + 0.004, 4)
    hook_bottom = (hook_y + hook_half) * H
    for j, props in enumerate(hook_props):
        last = j == len(hook) - 1
        t0 = grid.t(j * per)
        t1 = grid.t(hook_end) if last else grid.t((j + 1) * per)
        punch = {"scale": [{"t": 0, "v": 1.08 if j == 0 else 1.18, "ease": "outCubic"},
                           {"t": 0.5 if j == 0 else 0.22, "v": 1.0, "ease": "linear"},
                           {"t": round(t1 - t0, 3), "v": 1.03}]}
        inn = NONE if j == 0 else {"type": "pop" if last else "rise", "duration": 0.24}
        b.text(f"хук {j + 1}", t0, t1, hook_y, props, inn=inn, keys=punch,
               shadow={"color": "#000000", "blur": 40, "x": 0, "y": 8, "opacity": 0.8})
        if j:
            b.flash(t0, peak=0.2 if last else 0.13, dur=0.3 if last else 0.16)
        if last:
            b.ring(t0 + 0.02, 0.5, hook_y, size=420, grow=2.3, dur=0.65)
            b.particles("искры хука", t0 + 0.05, t1, 0.5, hook_y, "sparkle", 26, 520, size=26)
    if grid.t(hook_end) >= SFX["riser"][1] + 0.3:
        b.sfx("riser", grid.t(hook_end) - SFX["riser"][1], 0.22, fade_out=0.1)

    # ------------------------------------------------ сцены
    b.cam(0.0)
    report = []
    for ps, scene in zip(plan_scenes, scenes):
        i = ps["i"]
        s_t, e_t = grid.t(ps["start"]), grid.t(ps["end"])
        aspect = _aspect(info, scene["asset_id"])
        natural = _natural(info, scene["asset_id"])
        body_w, body_h, rec_h, _bar = _phone_size(aspect)
        x0, y0, s0, rot0 = POSES[ps["pose"]]
        rot0 = round(rot0 + rng.uniform(-1.0, 1.0), 2) if ps["pose"] != "center" else rot0
        # Титр над телефоном: верх — у безопасной зоны, телефон опускается/уменьшается под ним.
        lines = _split_words(scene["title"], 14)
        size = _fit_size(lines, brand["font"], 76, 108)
        words = " ".join(lines).split()
        if len(lines) == 2 and lines[1].startswith("—"):
            hl = list(range(len(lines[0].split()), len(words)))
        else:
            hl = [len(words) - 1]
        props = _text_layer("\n".join(lines), size, text_color, brand, hl=hl, lh=1.04)
        half = _half_h(props)
        title_y = round(TITLE_TOP + half + 0.002, 4)
        title_bottom = (title_y + half) * H
        # Телефон первой сцены стоит с 0 с под хуком: место — под более высоким из двух текстов.
        # Следующие въезжают на OVERLAP_SEC раньше конца сцены прежнего — свайп без пустого кадра.
        p_start = 0.0 if i == 0 else s_t - OVERLAP_SEC
        room_bottom = max(title_bottom, hook_bottom) if i == 0 else title_bottom
        tilt = abs(math.sin(math.radians(rot0))) * body_w / 2
        top_min = room_bottom + TITLE_GAP_PX + tilt
        scale = min(s0, (H * 1.03 - top_min) / body_h)
        y = max(y0, (top_min + scale * body_h / 2) / H)
        pose = (x0, round(y, 4), round(scale, 4), rot0)
        b.text(f"титр сцены {i + 1}", s_t, e_t - 0.02, title_y, props)

        # С окном телефон «главный» до доли окна, потом остаётся под окном до конца сцены: убери
        # Gemini окно — на его месте телефон, а не пустой фон (прогон №4, клип 581).
        phone_end_beat = ps["k"] if ps["window"] else ps["end"]
        show_end = grid.t(phone_end_beat)
        last = i + 1 == len(plan_scenes)
        p_end = e_t + (OVERLAP_SEC if last else 0.0)  # последний уходит, когда финал уже входит
        # Запись: телефон показывает [from, to] (с окном — первые 70 % до окна), кнопка focus.t — на доле наезда.
        rec_from, rec_to = scene["from"], _clamp_rec(info, scene["asset_id"], scene["to"])
        rec_to = max(rec_to, rec_from + 0.3)
        phone_rec_to = rec_from + (rec_to - rec_from) * (0.7 if ps["window"] else 1.0)
        span = max(0.4, show_end - p_start)
        speed = min(2.5, max(0.5, (phone_rec_to - rec_from) / span))
        # Доля наезда/касания: середина телефонной части на сильной доле.
        lo_b = ps["start"] + 2
        hi_b = phone_end_beat - 2 if not ps["window"] else phone_end_beat - 1
        zoom_beat = None
        if scene["focus"] and hi_b >= lo_b:
            if ps["climax"] and not ps["window"]:
                zoom_beat = ps["k"]
            else:
                zoom_beat = _strong_beat(lo_b, hi_b, ps["start"] + (phone_end_beat - ps["start"]) * 0.45, bar=True)
        frm = rec_from
        if scene["focus"] and scene["focus"].get("t") is not None and zoom_beat is not None:
            frm = max(0.0, scene["focus"]["t"] - (grid.t(zoom_beat) - p_start) * speed)
        frm = _clamp_rec(info, scene["asset_id"], frm)
        # Наезд на focus: глубина и центр — под титром и в кадре (_fit_zoom). Крупный телефон с кнопкой
        # внизу (focus.y высокий) так не наехать: верх уходит под титр или кнопка — за низ кадра, —
        # тогда телефон сцены мельче (до −24 %), пока наезд не встанет.
        fit = None
        if zoom_beat is not None:
            z_cap = ZOOM_MAX + (0.05 if ps["climax"] else 0.0)
            for step in range(7):
                s_try = round(scale * (1 - 0.04 * step), 4)
                trial = (x0, round(max(y0, (top_min + s_try * body_h / 2) / H), 4), s_try, rot0)
                probe = {"type": "video", "x": trial[0], "y": trial[1], "scale": s_try, "rotation": rot0,
                         "followCamera": True,
                         "props": {"asset_id": scene["asset_id"], "w": SCREEN_W, "device": "phone", "statusBar": True}}
                point = _on_phone(trial, scene["focus"]["x"], scene["focus"]["y"], aspect)
                zoom, peak, center = _fit_zoom(probe, natural, point, z_cap, title_bottom)
                if zoom > 1.0:
                    pose, fit = trial, (zoom, peak, center, point)
                    break
        # Въезд со стороны позы (центр — снизу), выезд — в сторону, откуда не въедет следующий.
        # Выезд и въезд следующего идут внахлёст на OVERLAP_SEC: на стыке всегда виден телефон.
        enter = {"center": (0.0, 0.7, 8.0), "left": (-0.85, 0.04, -18.0), "right": (0.85, -0.04, 18.0)}[ps["pose"]]
        nxt = plan_scenes[i + 1]["pose"] if not last else None
        exit_dx = {"left": 0.95, "right": -0.95, "center": -0.95}.get(nxt or "", 0.0)
        exit_dy = 0.75 if last else 0.0
        local_end = round(p_end - p_start, 3)
        x, yy, sc, rot = pose
        keys = {
            "x": [{"t": 0, "v": round(x + enter[0], 4), "ease": "outCubic"}, {"t": ENTER_SEC, "v": x, "ease": "linear"}],
            "y": [{"t": 0, "v": round(yy + enter[1], 4), "ease": "outCubic"}, {"t": ENTER_SEC, "v": yy, "ease": "linear"}],
            "rotation": [{"t": 0, "v": round(rot + enter[2], 2), "ease": "outCubic"}, {"t": ENTER_SEC, "v": rot, "ease": "linear"},
                         {"t": local_end, "v": round(rot * 0.8, 2)}],
        }
        ex = round(local_end - EXIT_SEC, 3)
        keys["x"] += [{"t": ex, "v": x, "ease": "inCubic"}, {"t": local_end, "v": round(x + exit_dx, 4)}]
        keys["y"] += [{"t": ex, "v": yy, "ease": "inCubic"}, {"t": local_end, "v": round(yy + exit_dy, 4)}]
        if ps["window"]:  # под окном записи телефон чуть отходит назад и ждёт выезда
            wk = round(show_end - p_start, 3)
            keys["scale"] = [{"t": wk, "v": sc, "ease": "outCubic"}, {"t": round(wk + 0.25, 3), "v": round(sc * WINDOW_PUSH, 4)}]
        phone = {"type": "video", "start": p_start, "end": p_end, "track": 3, "x": x, "y": yy, "scale": sc, "rotation": rot,
                 "followCamera": True,
                 "props": {"asset_id": scene["asset_id"], "from": frm, "speed": round(speed, 3), "freeze": True,
                           "w": SCREEN_W, "device": "phone", "deviceColor": "#2c2c2e", "statusBar": True,
                           "statusBarColor": dark},
                 "shadow": {"color": "#000000", "blur": 90, "x": 0, "y": 40, "opacity": 0.7},
                 "keys": keys, "in": NONE, "out": NONE}
        phone = b.add(phone, f"телефон {i + 1}")
        if i:
            b.sfx("whoosh", s_t - 0.32, 0.3)
        else:
            b.sfx("impact", s_t, 0.26, fade_out=0.6)
            b.flash(s_t, peak=0.18, dur=0.3)

        # Наезд камеры на focus + касание на доле.
        zoom_info = None
        if zoom_beat is not None:
            zt = grid.t(zoom_beat)
            point = _on_phone(pose, scene["focus"]["x"], scene["focus"]["y"], aspect)
            if fit:
                zoom, peak, center, point = fit
                b.cam(max(s_t + ENTER_SEC, zt - 0.45), 1.0)
                b.cam(zt, zoom, center, "linear", shake=0.3 if ps["climax"] and not ps["window"] else None)
                if ps["window"]:
                    b.cam(show_end - 0.02, peak, center, "step", shake=0.0 if ps["climax"] else None)
                    b.cam(show_end, 1.0)
                else:
                    hold_end = max(zt + 0.3, e_t - EXIT_SEC - 0.45)
                    b.cam(hold_end, peak, center, "inOutCubic", shake=0.0 if ps["climax"] else None)
                    b.cam(e_t - EXIT_SEC, 1.0)
                zoom_info = {"t": zt, "zoom": zoom, "peak": peak, "center": list(center)}
            b.tap(zt, *point)

        # Окно записи на ключевой момент; кульминация — вспышка, кольцо, частицы, бас-дроп.
        window_info = None
        if ps["window"]:
            kt = grid.t(ps["k"])
            crop = scene["window"]
            w_aspect = _aspect(info, scene["asset_id"], crop)
            top_px = title_bottom + 40
            bottom_px = 0.86 * H
            ww = max(440.0, min(WINDOW_MAX_W, (bottom_px - top_px) / 1.05 / w_aspect))
            wy = (top_px + bottom_px) / 2 / H
            dur = e_t - kt
            wspeed = 0.6
            wfrom = _clamp_rec(info, scene["asset_id"], max(rec_from, rec_to - dur * wspeed))
            punch = 1.6 if ps["climax"] else 1.35
            b.add({"type": "video", "start": kt, "end": e_t, "track": 3, "x": 0.5, "y": wy,
                   "rotation": -1.5 if i % 2 == 0 else 1.5,
                   "props": {"asset_id": scene["asset_id"], "from": wfrom, "speed": wspeed, "freeze": True,
                             "w": round(ww, 1), "radius": 34, "crop": crop, "border": {"color": accent, "width": 5}},
                   "shadow": {"color": accent, "blur": 80, "x": 0, "y": 0, "opacity": 0.55},
                   "keys": {"scale": [{"t": 0, "v": punch, "ease": "outCubic"}, {"t": 0.22, "v": 1.0, "ease": "linear"},
                                      {"t": round(dur, 3), "v": 1.05}]},
                   "in": {"type": "fade", "duration": 0.06}, "out": {"type": "fade", "duration": EXIT_SEC}},
                  f"окно записи {i + 1}")
            window_info = {"t": kt}
            if ps["climax"]:
                b.flash(kt, peak=0.3, dur=0.35)
                b.ring(kt + 0.02, 0.5, wy, size=380, grow=2.6, dur=0.65)
                b.particles("взрыв кульминации", kt, kt + 1.4, 0.5, wy, "burst", 150, 700, size=34, track=2,
                            duration=1.3)
                if e_t - (kt + 0.35) >= 0.6:
                    b.particles("искры кульминации", kt + 0.35, e_t, 0.5, wy, "sparkle", 18, 640, size=24)
                b.sfx("drop", kt, 0.3, fade_out=0.6)
            else:
                b.flash(kt, peak=0.16, dur=0.2)
                b.sfx("whip", kt - 0.06, 0.26)
        elif ps["climax"]:
            kt = grid.t(ps["k"])
            focus = scene["focus"] or {"x": 0.5, "y": 0.5}
            point = _on_phone(pose, focus["x"], focus["y"], aspect)
            b.flash(kt, peak=0.28, dur=0.32)
            b.ring(kt + 0.02, *point, size=320, grow=2.5, follow=True)
            b.particles("взрыв кульминации", kt, kt + 1.4, *point, "burst", 150, 560, size=30, follow=True, duration=1.3)
            b.sfx("drop", kt, 0.3, fade_out=0.6)
        report.append({"scene": i + 1, "title": scene["title"], "start": s_t, "end": e_t, "pose": ps["pose"],
                       "zoom": zoom_info, "window": window_info, "climax": ps["climax"]})

    # ------------------------------------------------ финал
    f_t, end = grid.t(final_start), duration
    end_spec = spec["end"]
    b.cam(f_t, 1.0)
    b.sfx("outro", f_t - 0.2, 0.3)
    logo_y = 0.31
    logo_bottom = logo_y * H
    if end_spec["logo_asset_id"] is not None:
        la = _aspect(info, end_spec["logo_asset_id"]) if _natural(info, end_spec["logo_asset_id"]) else 0.6
        lw = round(min(720.0, 460.0 / la), 1)
        logo_bottom = logo_y * H + lw * la / 2
        b.add({"type": "image", "start": f_t + 0.04, "end": end, "track": 6, "x": 0.5, "y": logo_y,
               "props": {"asset_id": end_spec["logo_asset_id"], "w": lw},
               "shadow": {"color": accent, "blur": 90, "x": 0, "y": 0, "opacity": 0.4},
               "keys": {"scale": [{"t": 0, "v": 0.6, "ease": "outCubic"}, {"t": 0.45, "v": 1.0, "ease": "linear"},
                                  {"t": round(end - f_t - 0.04, 3), "v": 1.05}]},
               "in": {"type": "blur", "duration": 0.3}, "out": NONE}, "логотип")
        b.flash(f_t + 0.04, peak=0.2, dur=0.3)
        b.particles("взрыв у логотипа", f_t + 0.05, f_t + 1.5, 0.5, logo_y, "burst", 140, 620, size=30, duration=1.35)
        b.ring(f_t + 0.06, 0.5, logo_y, size=380, grow=2.4, dur=0.65)
        b.particles("искры у логотипа", f_t + 0.8, end, 0.5, logo_y, "sparkle", 22, 480, size=24)
    block = ([end_spec["sub"]] if end_spec["sub"] else []) + end_spec["lines"]
    cursor_px = logo_bottom + 70
    if block:
        size = _fit_size(block, brand["font"], 60, 72, width=1000)
        first_line_words = len(block[0].split()) if end_spec["sub"] and end_spec["lines"] else 0
        all_words = " ".join(block).split()
        hl = list(range(first_line_words, len(all_words))) if first_line_words else None
        props = _text_layer("\n".join(block), size, text_color, brand, hl=hl, backdrop=False, weight=800, lh=1.15)
        half = _half_h(props)
        y = (cursor_px / H) + half
        b.text("строки финала", f_t + grid.beat, end, round(y, 4), props, inn={"type": "rise", "duration": 0.45})
        cursor_px = (y + half) * H + 46
    cta = end_spec["cta"]
    cta_size = float(max(56, min(72, math.floor(900 / (max(1, len(cta)) * _char_w(brand["font"]) * 0.82)))))
    plate = {"color": accent, "opacity": 1, "radius": 52, "padX": 56, "padY": 28}
    props = _text_layer(cta, cta_size, _ink_on(accent), brand, backdrop=False, bg=plate, weight=800, lh=1.1)
    cta_y = round(min(0.74, max(cursor_px / H + _half_h(props) + 28 / H, 0.62)), 4)
    cta_t = f_t + 2 * grid.beat
    b.text("плашка CTA", cta_t, end, cta_y, props, inn={"type": "pop", "duration": 0.4},
           loop={"type": "pulse", "speed": 0.8, "amount": 0.4},
           shadow={"color": accent, "blur": 70, "x": 0, "y": 0, "opacity": 0.6})
    b.sfx("pop", cta_t, 0.32)
    ring_t = f_t + 4 * grid.beat
    if end - ring_t >= 1.0:
        b.ring(ring_t, 0.5, cta_y, size=520, grow=1.9, dur=0.6)
        b.particles("искры у плашки", ring_t, min(end, ring_t + 1.3), 0.5, cta_y, "burst", 80, 520, size=24,
                    duration=1.2)

    # ------------------------------------------------ итог
    from app import motion

    layers = motion.normalize_layers(b.layers)
    camera = motion.normalize_camera(b.camera)
    music_out = None
    if music:
        start_sec, why = _music_start(music, climax_t, duration)
        music_out = {"track_id": music["track_id"], "start_sec": start_sec}
        notes.append(f"музыка: трек {music['track_id']} с {start_sec:g} с — {why}")
    backdrop = spec["background"] if spec["background"] is not None else {
        "kind": "aurora", "colors": [dark, accent, _mix(dark, accent, 0.35)], "speed": 0.5, "intensity": 0.45}
    notes.append(f"сетка: {bpm:g} BPM, доля {grid.beat:.3f} с; хук 0–{grid.t(hook_end):.2f} с, "
                 f"кульминация {climax_t:.2f} с (сцена {climax_i + 1}), финал {f_t:.2f}–{duration:.2f} с")
    return {
        "layers": layers,
        "camera": camera,
        "music": music_out,
        "backdrop": backdrop,
        "duration": duration,
        "plan": {"bpm": bpm, "beat": round(grid.beat, 4), "hook": [0.0, grid.t(hook_end)], "scenes": report,
                 "climax": {"t": climax_t, "scene": climax_i + 1, "beat": climax_beat},
                 "final": [f_t, duration]},
        "notes": notes,
    }


def is_scaffold_layer(layer: dict) -> bool:
    """Слой заготовки (повторный build_app_promo заменяет только их)."""
    return str(layer.get("name") or "").startswith(NAME_PREFIX)
