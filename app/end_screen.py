"""Концовка-баннер шортса: основа уходит в размытие, поверх — анимированный баннер.

Отдельное решение монтажа (не «ещё один титр»): ``render.end_screen`` клипа + слой баннера
``end_banner``. На последних ``duration`` секундах ГОТОВОГО ролика:

* основа (видео, фон раскладки, слои ``behind``) плавно размывается и темнеет — это фильтр
  компоновщика между основой и слоями (``comp.endScreen``, frontend/src/motion/endscreen.ts), а не
  слой: превью редактора зовёт ту же функцию движка;
* сверху — баннер ``end_banner``: слой ``image`` с webm-альфой, собранной из SVG
  (app/svg_webm.py — перенос svg-to-webm владельца). SVG — встроенный шаблон «живого стрима»
  (``TEMPLATES``: точка LIVE, расписание, неоновый ник) или свой SVG из «Файлов для монтажа».

Начало размытия — начало слоя ``end_banner`` во времени композиции (вырезы и стоп-кадры его уже
сдвинули), без слоя — ``длина − duration``. Повтор ``set_end_screen`` заменяет концовку, снятие —
``remove``. Бренд: ``cta.style = "banner"`` — призыв бренда этой концовкой (app/brand_kit.py).
"""

from __future__ import annotations

import math
import re
from html import escape
from typing import Any, Callable

END_LAYER_ID = "end_banner"
DURATION_DEFAULT, DURATION_MIN, DURATION_MAX = 3.0, 1.5, 8.0
BLUR_DEFAULT, BLUR_MAX = 18.0, 60.0
DIM_DEFAULT, DIM_MAX = 0.35, 0.8
RAMP_DEFAULT, RAMP_MIN, RAMP_MAX = 0.6, 0.1, 2.0
BANNER_Y_DEFAULT = 0.44
BANNER_W_DEFAULT = 960.0  # ширина баннера на кадре, опорные px (кадр 1080 по короткой стороне)
WEBM_FPS = 30
WEBM_TAIL = 0.5  # webm длиннее слоя: анимация не начнётся заново на последнем кадре
LIVE_RED = "#ff2d55"
DEFAULT_TEXT = "Стримлю с 8 утра — заходи"

PLATFORMS = ("twitch", "youtube", "tiktok", "telegram", "kick", "none")
# Цвет площадки — акцент по умолчанию (зеркало PLATFORM_COLORS в layers/nameplate.ts).
PLATFORM_COLORS = {"twitch": "#9146ff", "youtube": "#ff0033", "tiktok": "#fe2c55", "telegram": "#229ed9",
                   "kick": "#53fc18", "none": "#ffd600"}
PLATFORM_URLS = {"twitch": "twitch.tv/{h}", "youtube": "youtube.com/@{h}", "tiktok": "tiktok.com/@{h}",
                 "telegram": "t.me/{h}", "kick": "kick.com/{h}"}
# Значки площадок — те же глифы, что у слоя nameplate (frontend/src/motion/glyphs.ts, PLATFORM_ICONS),
# переведённые в SVG-путь: центр (0, 0), высота 1, adv — ширина в долях высоты.
PLATFORM_ICONS = {
    "twitch": (0.86, "evenodd", "M-0.31 -0.5L0.43 -0.5L0.43 0.12L0.23 0.32L0.017 0.32L-0.138 0.5L-0.138 0.32L-0.43 "
                                "0.32L-0.43 -0.38ZM-0.069 -0.3L0.026 -0.3L0.026 0L-0.069 0ZM0.155 -0.3L0.249 -0.3L0.249 "
                                "0L0.155 0Z"),
    "youtube": (1.36, "evenodd", "M-0.417 -0.47L0.417 -0.47Q0.68 -0.47 0.68 -0.207L0.68 0.207Q0.68 0.47 0.417 "
                                 "0.47L-0.417 0.47Q-0.68 0.47 -0.68 0.207L-0.68 -0.207Q-0.68 -0.47 -0.417 -0.47ZM-0.15 "
                                 "-0.24L0.29 0L-0.15 0.24Z"),
    "tiktok": (0.92, "nonzero", "M0.15 0.24A0.25 0.25 0 1 1 -0.35 0.24A0.25 0.25 0 1 1 0.15 0.24ZM0.02 -0.5L0.15 "
                                "-0.5L0.15 0.24L0.02 0.24ZM0.02 -0.5Q0.15 -0.21 0.43 -0.21L0.43 -0.02Q0.15 -0.02 0.02 -0.2Z"),
    "telegram": (1.0, "evenodd", "M0.5 0A0.5 0.5 0 1 1 -0.5 0A0.5 0.5 0 1 1 0.5 0ZM-0.32 0.01L0.23 -0.22L0.14 "
                                 "0.25L0 0.13L-0.08 0.22L-0.1 0.07Z"),
    "kick": (0.74, "nonzero", "M-0.344 -0.43L-0.129 -0.43L-0.129 -0.129L0.022 -0.129L0.022 -0.267L0.172 -0.267L0.172 "
                              "-0.43L0.366 -0.43L0.366 -0.193L0.215 -0.193L0.215 -0.052L0.077 -0.052L0.077 0.052L0.215 "
                              "0.052L0.215 0.194L0.366 0.194L0.366 0.43L0.172 0.43L0.172 0.267L0.022 0.267L0.022 "
                              "0.129L-0.129 0.129L-0.129 0.43L-0.344 0.43Z"),
}

TEMPLATES = {
    "live": "Живой эфир: стеклянная карточка, пульсирующая точка LIVE, фраза крупно (после « — » — акцентом), "
            "ник со значком площадки и кнопка-стрелка",
    "schedule": "Расписание: дни недели чипами (активные — акцентом), время крупно со свечением, фраза и ник",
    "neon": "Неон: ник крупно — светящаяся обводка рисуется и вспыхивает, над ним LIVE, под ним фраза и адрес канала",
}
TEMPLATE_DEFAULT = "live"
HEAD_FONT, BODY_FONT = "Unbounded", "Manrope"
DAYS = ("ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС")
_DAY_KEYS = (("пн", "mon"), ("вт", "tue"), ("ср", "wed"), ("чт", "thu"), ("пт", "fri"), ("сб", "sat"), ("вс", "sun"))
# Ход «дорого»: быстро стартует, долго и мягко садится (expo-out) и пружинка для «поп».
EASE_OUT = "cubic-bezier(.16,1,.3,1)"
EASE_POP = "cubic-bezier(.34,1.56,.64,1)"


# --------------------------------------------------------------------------- #
# Нормализация
# --------------------------------------------------------------------------- #


def _num(value: Any, default: float | None, lo: float, hi: float) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return round(min(hi, max(lo, number)), 3)


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value).split())[:limit] if isinstance(value, (str, int, float)) and not isinstance(value, bool) else ""


def _color(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(ch * 2 for ch in value[1:])
    return value if re.fullmatch(r"#[0-9a-f]{6}", value) else None


def _opt_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def normalize_end_screen(raw: Any) -> dict | None:
    """``render.end_screen`` в полной форме или None (концовки нет).

    ``template`` — встроенный шаблон (по умолчанию live), ``svg_asset_id`` — свой SVG вместо шаблона;
    ``asset_id`` — собранный webm баннера (ставит set_end_screen). Тексты пустые — из шаблона."""
    if not isinstance(raw, dict) or raw.get("on") is False:
        return None
    template = raw.get("template")
    svg_asset = _opt_id(raw.get("svg_asset_id"))
    platform = raw.get("platform")
    duration = _num(raw.get("duration"), DURATION_DEFAULT, DURATION_MIN, DURATION_MAX) or DURATION_DEFAULT
    return {
        "duration": duration,
        "blur": _num(raw.get("blur"), BLUR_DEFAULT, 0.0, BLUR_MAX),
        "dim": _num(raw.get("dim"), DIM_DEFAULT, 0.0, DIM_MAX),
        "ramp": min(_num(raw.get("ramp"), RAMP_DEFAULT, RAMP_MIN, RAMP_MAX) or RAMP_DEFAULT, round(duration / 2, 3)),
        "template": None if svg_asset else (template if template in TEMPLATES else TEMPLATE_DEFAULT),
        "svg_asset_id": svg_asset,
        "asset_id": _opt_id(raw.get("asset_id")),
        "text": _text(raw.get("text"), 80),
        "handle": _text(raw.get("handle"), 40).lstrip("@"),
        "platform": platform if platform in PLATFORMS else "none",
        "accent": _color(raw.get("accent")),
        "days": _text(raw.get("days"), 40),
        "time": _text(raw.get("time"), 20),
        "font": _text(raw.get("font"), 60),
    }


def comp_end_screen(raw: Any, layers: list[dict], duration: float) -> dict | None:
    """``comp.endScreen`` компоновщика: начало — начало слоя баннера в этой композиции (вырезы и
    стоп-кадры его уже сдвинули), без слоя — ``duration − end_screen.duration``."""
    es = normalize_end_screen(raw)
    if not es or (es["blur"] <= 0 and es["dim"] <= 0):
        return None
    banner = next((l for l in layers or [] if l.get("id") == END_LAYER_ID and not l.get("hidden")), None)
    try:
        start = float(banner["start"]) if banner else max(0.0, float(duration) - es["duration"])
    except (KeyError, TypeError, ValueError):
        start = max(0.0, float(duration) - es["duration"])
    return {"start": round(start, 3), "ramp": es["ramp"], "blur": es["blur"], "dim": es["dim"]}


# --------------------------------------------------------------------------- #
# Шаблоны SVG
# --------------------------------------------------------------------------- #


def parse_days(value: str) -> list[bool]:
    """«пн–пт», «пн, ср, пт», «будни», «выходные», «ежедневно» → 7 флагов (пусто — каждый день)."""
    text = (value or "").lower().replace("—", "-").replace("–", "-")
    if not text.strip() or any(w in text for w in ("ежеднев", "каждый", "daily", "every")):
        return [True] * 7
    if "будн" in text or "weekday" in text:
        return [True] * 5 + [False] * 2
    if "выходн" in text or "weekend" in text:
        return [False] * 5 + [True] * 2

    def index(token: str) -> int | None:
        token = token.strip()[:3]
        return next((i for i, keys in enumerate(_DAY_KEYS) if any(token.startswith(k) for k in keys)), None)

    flags = [False] * 7
    for token in re.split(r"[\s,;/]+", re.sub(r"\s*-\s*", "-", text)):
        a, dash, b = token.partition("-")
        ia, ib = index(a), index(b) if dash else None
        if ia is not None and ib is not None:
            for k in range((ib - ia) % 7 + 1):
                flags[(ia + k) % 7] = True
        elif ia is not None:
            flags[ia] = True
    return flags if any(flags) else [True] * 7


def parse_time(time: str, text: str = "") -> tuple[str, str]:
    """Время эфира крупно и подпись к нему: «8:00 МСК» → («08:00», «МСК»); пусто — из фразы
    («с 8 утра» → 08:00, «в 20:30» → 20:30). Ничего — («», «»)."""
    for src in (time, text):
        m = re.search(r"(\d{1,2})[:.](\d{2})", src or "")
        if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
            rest = (src[m.end():] if src is time else "").strip(" ,·-—")
            return f"{int(m.group(1)):02d}:{m.group(2)}", rest[:12]
        m = re.search(r"(?:^|\s)(?:с|в|от)\s+(\d{1,2})\s*(утра|дня|вечера|ночи|ч)?", src or "", re.I)
        if m and int(m.group(1)) < 24:
            hour = int(m.group(1))
            if (m.group(2) or "").lower() in ("дня", "вечера") and hour < 12:
                hour += 12
            return f"{hour:02d}:00", ""
    return "", ""


def split_headline(text: str) -> tuple[list[str], int]:
    """Фраза → строки (≤ 2) и номер строки-акцента: после « — » — вторая строка акцентом."""
    text = " ".join((text or "").split())
    m = re.search(r"\s[—–-]\s", text)
    if m:
        first, second = text[:m.start()].strip(), text[m.end():].strip()
        if first and second:
            return [first + " —", second], 1
    words = text.split()
    if len(text) <= 16 or len(words) < 2:
        return [text], 0
    best, cut = None, 1
    for i in range(1, len(words)):
        a, b = " ".join(words[:i]), " ".join(words[i:])
        score = max(len(a), len(b))
        if best is None or score < best:
            best, cut = score, i
    return [" ".join(words[:cut]), " ".join(words[cut:])], 1


def _fit_size(lines: list[str], width: float, k: float, hi: float, lo: float) -> float:
    """Кегль, при котором самая длинная строка ≈ влезает в ``width`` (k — средняя ширина знака в
    долях кегля). Точнее — в браузере: data-fit ужимает, если оценка ошиблась."""
    longest = max((len(s) for s in lines), default=1) or 1
    return round(max(lo, min(hi, width / (longest * k))), 1)


def _icon(platform: str, x: float, cy: float, h: float, color: str, cls: str = "") -> tuple[str, float]:
    """Значок площадки левым краем в x, по центру cy, высотой h → (svg, ширина)."""
    icon = PLATFORM_ICONS.get(platform)
    if not icon:
        return "", 0.0
    adv, rule, d = icon
    w = adv * h
    attrs = f' class="{cls}"' if cls else ""
    return (f'<g{attrs}><path transform="translate({x + w / 2:.1f} {cy:.1f}) scale({h:.1f})" d="{d}" '
            f'fill="{color}" fill-rule="{rule}"/></g>'), w


def _esc(value: str) -> str:
    return escape(value or "", quote=True)


def _common_css(head_font: str) -> str:
    return f"""
.f-head{{font-family:"{_esc(head_font)}","Unbounded","Montserrat",sans-serif;font-weight:900}}
.f-body{{font-family:"{BODY_FONT}","Inter","Montserrat",sans-serif;font-weight:800}}
.fb{{transform-box:fill-box;transform-origin:50% 50%}}
@keyframes cardIn{{0%{{opacity:0;transform:translateY(46px) scale(.92)}}100%{{opacity:1;transform:none}}}}
@keyframes rise{{0%{{transform:translateY(118%)}}100%{{transform:none}}}}
@keyframes fadeUp{{0%{{opacity:0;transform:translateY(26px)}}100%{{opacity:1;transform:none}}}}
@keyframes pop{{0%{{opacity:0;transform:scale(.4)}}100%{{opacity:1;transform:none}}}}
@keyframes grow{{0%{{transform:scaleX(0)}}100%{{transform:none}}}}
@keyframes sheen{{0%{{transform:translateX(-420px) skewX(-18deg)}}100%{{transform:translateX(1320px) skewX(-18deg)}}}}
@keyframes pulse{{0%,100%{{transform:scale(1)}}50%{{transform:scale(.78)}}}}
@keyframes ring{{0%{{opacity:.75;transform:scale(1)}}100%{{opacity:0;transform:scale(3.2)}}}}
@keyframes breathe{{0%,100%{{opacity:.55}}50%{{opacity:1}}}}
@keyframes nudge{{0%,60%,100%{{transform:translateX(0)}}30%{{transform:translateX(9px)}}}}
@keyframes draw{{0%{{stroke-dashoffset:2400}}100%{{stroke-dashoffset:0}}}}
@keyframes fillIn{{0%{{fill-opacity:0}}100%{{fill-opacity:1}}}}
@keyframes flicker{{0%,100%{{opacity:1}}8%{{opacity:.35}}14%{{opacity:1}}22%{{opacity:.6}}30%{{opacity:1}}}}
"""


def _anim(name: str, dur: float, delay: float, ease: str = EASE_OUT, extra: str = "both") -> str:
    return f"animation:{name} {dur:.2f}s {ease} {delay:.2f}s {extra}"


def _live_dot(cx: float, cy: float, r: float, delay: float) -> str:
    """Пульсирующая точка LIVE: ядро дышит, от него расходится кольцо."""
    return (f'<g class="fb" style="{_anim("pop", 0.5, delay, EASE_POP)}">'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#ffffff" class="fb" '
            f'style="animation:ring 1.3s ease-out {delay + 0.3:.2f}s infinite both"/>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#ffffff" class="fb" '
            f'style="animation:pulse 1.3s ease-in-out {delay + 0.3:.2f}s infinite both"/></g>')


def _live_pill(x: float, y: float, delay: float, label: str = "LIVE") -> tuple[str, float]:
    """Красная пилюля «● LIVE» левым краем в (x, y-верх) → (svg, ширина)."""
    h, w = 52.0, 54.0 + 21.0 * len(label)
    return (f'<g class="fb" style="{_anim("pop", 0.55, delay, EASE_POP)}">'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{h / 2}" fill="{LIVE_RED}" filter="url(#redGlow)"/>'
            f'{_live_dot(x + 26, y + h / 2, 7, delay + 0.1)}'
            f'<text x="{x + 44}" y="{y + h / 2 + 8}" class="f-head" font-size="22" letter-spacing="2.4" '
            f'fill="#ffffff">{_esc(label)}</text></g>'), w


def _defs(accent: str, card: tuple[float, float, float, float] | None) -> str:
    clip = ""
    if card:
        x, y, w, h = card
        clip = f'<clipPath id="cardClip"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="44"/></clipPath>'
    return f"""<defs>
<linearGradient id="cardFill" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="#1a1530" stop-opacity=".86"/><stop offset="1" stop-color="#0a0814" stop-opacity=".9"/></linearGradient>
<linearGradient id="cardStroke" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#ffffff" stop-opacity=".34"/><stop offset=".45" stop-color="{accent}" stop-opacity=".55"/>
<stop offset="1" stop-color="#ffffff" stop-opacity=".06"/></linearGradient>
<linearGradient id="accentText" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="{_mix(accent, '#ffffff', 0.22)}"/><stop offset="1" stop-color="{_mix(accent, '#ffffff', 0.62)}"/></linearGradient>
<linearGradient id="sheenFill" x1="0" y1="0" x2="1" y2="0">
<stop offset="0" stop-color="#ffffff" stop-opacity="0"/><stop offset=".5" stop-color="#ffffff" stop-opacity=".16"/>
<stop offset="1" stop-color="#ffffff" stop-opacity="0"/></linearGradient>
<radialGradient id="leak" cx=".5" cy=".5" r=".5">
<stop offset="0" stop-color="{accent}" stop-opacity=".55"/><stop offset="1" stop-color="{accent}" stop-opacity="0"/></radialGradient>
<radialGradient id="scrim" cx=".5" cy=".5" r=".5">
<stop offset="0" stop-color="#05040a" stop-opacity=".62"/><stop offset=".7" stop-color="#05040a" stop-opacity=".3"/>
<stop offset="1" stop-color="#05040a" stop-opacity="0"/></radialGradient>
<filter id="shadow" x="-20%" y="-20%" width="140%" height="170%"><feDropShadow dx="0" dy="26" stdDeviation="30" flood-color="#000" flood-opacity=".55"/></filter>
<filter id="glow" x="-30%" y="-60%" width="160%" height="220%"><feGaussianBlur stdDeviation="9" result="b"/>
<feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="softGlow" x="-30%" y="-80%" width="160%" height="260%"><feGaussianBlur stdDeviation="16" result="b"/>
<feMerge><feMergeNode in="b"/><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="neon" x="-20%" y="-60%" width="140%" height="220%"><feGaussianBlur in="SourceAlpha" stdDeviation="12" result="b1"/>
<feFlood flood-color="{accent}" flood-opacity=".9"/><feComposite in2="b1" operator="in" result="c1"/>
<feGaussianBlur in="SourceAlpha" stdDeviation="3.5" result="b2"/><feFlood flood-color="{_mix(accent, '#ffffff', 0.3)}"/>
<feComposite in2="b2" operator="in" result="c2"/>
<feMerge><feMergeNode in="c1"/><feMergeNode in="c1"/><feMergeNode in="c2"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
<filter id="redGlow" x="-40%" y="-80%" width="180%" height="260%"><feDropShadow dx="0" dy="0" stdDeviation="9" flood-color="{LIVE_RED}" flood-opacity=".8"/></filter>
{clip}
</defs>"""


def _mix(a: str, b: str, k: float) -> str:
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(ca, cb))


def _handle_row(fields: dict, accent: str, x: float, cy: float, delay: float, size: float = 44.0,
                url: bool = False) -> tuple[str, float]:
    """Значок площадки + ник (или адрес канала) → (svg, ширина по оценке)."""
    handle = fields.get("handle") or ""
    if not handle:
        return "", 0.0
    platform = fields.get("platform") or "none"
    label = PLATFORM_URLS[platform].format(h=handle) if url and platform in PLATFORM_URLS else "@" + handle
    icon_svg, iw = _icon(platform, x, cy, size * 1.05, accent, "fb")
    gap = size * 0.42 if iw else 0.0
    out = (f'<g style="{_anim("fadeUp", 0.6, delay)}">'
           + (f'<g class="fb" style="{_anim("pop", 0.55, delay + 0.05, EASE_POP)}">{icon_svg}</g>' if icon_svg else "")
           + f'<text x="{x + iw + gap:.1f}" y="{cy + size * 0.36:.1f}" class="f-body" font-size="{size}" '
             f'fill="#ffffff" data-fit="{max(120.0, 760 - iw - gap):.0f}">{_esc(label)}</text></g>')
    return out, iw + gap + len(label) * size * 0.6


def _card(x: float, y: float, w: float, h: float, accent: str, delay: float) -> str:
    """Стеклянная карточка: тень, засветка акцентом из угла, обводка-градиент, блик по стеклу."""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="44" fill="url(#cardFill)" filter="url(#shadow)"/>'
            f'<g clip-path="url(#cardClip)"><ellipse cx="{x + w * 0.12:.0f}" cy="{y:.0f}" rx="{w * 0.5:.0f}" '
            f'ry="{h * 0.62:.0f}" fill="url(#leak)" style="animation:breathe 3s ease-in-out {delay + 0.9:.2f}s infinite both"/>'
            f'<ellipse cx="{x + w * 0.95:.0f}" cy="{y + h:.0f}" rx="{w * 0.3:.0f}" ry="{h * 0.4:.0f}" fill="url(#leak)" '
            f'opacity=".45"/></g>'
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="44" fill="none" stroke="url(#cardStroke)" stroke-width="2"/>'
            f'<g clip-path="url(#cardClip)"><rect x="{x}" y="{y - 60}" width="170" height="{h + 120}" fill="url(#sheenFill)" '
            f'style="{_anim("sheen", 1.25, delay + 1.05, "cubic-bezier(.45,0,.2,1)")}"/></g>')


def _headline(lines: list[str], accent_line: int, x: float, top: float, size: float, width: float,
              delay: float, anchor: str = "start", head_font: str = HEAD_FONT) -> tuple[str, float]:
    """Фраза строками: каждая выезжает снизу из-под своей маски (как вход ``rise``) со сдвигом
    0.1 с; строка-акцент — градиентом акцента со свечением. → (svg, высота блока)."""
    out = []
    line_h = size * 1.14
    for i, line in enumerate(lines):
        base = top + size * 0.86 + i * line_h
        fill = 'fill="url(#accentText)" filter="url(#glow)"' if i == accent_line and len(lines) > 1 else 'fill="#ffffff"'
        clip_top = base - size * 1.02
        out.append(
            f'<clipPath id="line{i}"><rect x="{x - (width if anchor != "start" else 0) - 40:.0f}" y="{clip_top:.0f}" '
            f'width="{width * 2 + 80:.0f}" height="{size * 1.36:.0f}"/></clipPath>'
            f'<g clip-path="url(#line{i})"><g style="{_anim("rise", 0.72, delay + i * 0.1)}">'
            f'<text x="{x:.1f}" y="{base:.1f}" class="f-head" font-size="{size}" text-anchor="{anchor}" '
            f'font-family="{_esc(head_font)}" {fill} data-fit="{width:.0f}">{_esc(line)}</text></g></g>')
    return "".join(out), line_h * len(lines)


def _svg(width: float, height: float, css: str, defs: str, body: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.0f}" height="{height:.0f}" '
            f'viewBox="0 0 {width:.0f} {height:.0f}"><style>{css}</style>{defs}{body}</svg>')


def _tpl_live(f: dict, accent: str, head_font: str) -> tuple[str, int, int]:
    W, pad = 960.0, 56.0
    x0, y0 = 40.0, 40.0
    lines, accent_line = split_headline(f["text"] or DEFAULT_TEXT)
    size = _fit_size(lines, W - 2 * x0 - 2 * pad, 0.74, 78.0, 44.0)
    hh = 52.0
    top = y0 + pad
    pill, pw = _live_pill(x0 + pad, top, 0.32)
    time, zone = parse_time(f["time"])
    when = " · ".join(t for t in (time and f"{time} {zone}".strip(), f["days"].replace("-", "–")) if t)
    side = (f'<text x="{x0 + pad + pw + 22:.0f}" y="{top + 35:.0f}" class="f-body" font-size="26" fill="#ffffff" '
            f'fill-opacity=".72" style="{_anim("fadeUp", 0.6, 0.42)}">{_esc(when)}</text>') if when else ""
    head_top = top + hh + 34
    head, head_h = _headline(lines, accent_line, x0 + pad, head_top, size, W - 2 * x0 - 2 * pad, 0.45, head_font=head_font)
    y = head_top + head_h + 30
    row = ""
    if f["handle"]:
        line = (f'<line x1="{x0 + pad}" y1="{y:.0f}" x2="{W - x0 - pad}" y2="{y:.0f}" stroke="#ffffff" stroke-opacity=".14" '
                f'stroke-width="2" class="fb" style="transform-origin:0 50%;{_anim("grow", 0.8, 0.8)}"/>')
        cy = y + 62
        handle, _ = _handle_row(f, accent, x0 + pad, cy, 0.92)
        bx = W - x0 - pad - 40
        button = (f'<g class="fb" style="{_anim("pop", 0.6, 1.02, EASE_POP)}">'
                  f'<circle cx="{bx:.0f}" cy="{cy:.0f}" r="40" fill="{accent}" filter="url(#glow)"/>'
                  f'<g style="animation:nudge 1.4s ease-in-out 1.6s infinite both"><path d="M{bx - 15:.0f} {cy:.0f}h28m-12 -13l13 13l-13 13" '
                  f'fill="none" stroke="#ffffff" stroke-width="7" stroke-linecap="round" stroke-linejoin="round"/></g></g>')
        row = line + handle + button
        y = cy + 40
    h = y + pad - y0
    H = h + 2 * y0 + 20
    card = (x0, y0, W - 2 * x0, h)
    body = (f'<g class="fb" style="transform-origin:50% 60%;{_anim("cardIn", 0.8, 0.12)}">{_card(*card, accent, 0.12)}'
            f'{pill}{side}{head}{row}</g>')
    return _svg(W, H, _common_css(head_font), _defs(accent, card), body), int(W), int(round(H))


def _tpl_schedule(f: dict, accent: str, head_font: str) -> tuple[str, int, int]:
    W, x0, y0, pad = 960.0, 40.0, 40.0, 56.0
    inner = W - 2 * x0 - 2 * pad
    top = y0 + pad
    head = (f'<text x="{x0 + pad}" y="{top + 30:.0f}" class="f-body" font-size="24" letter-spacing="5" fill="#ffffff" '
            f'fill-opacity=".62" style="{_anim("fadeUp", 0.6, 0.3)}">РАСПИСАНИЕ СТРИМОВ</text>')
    pill, pw = _live_pill(W - x0 - pad - (54 + 21 * 4), top - 6, 0.36)
    y = top + 70
    flags = parse_days(f["days"])
    chip_w, gap = (inner - 6 * 12) / 7, 12.0
    chips = []
    for i, (day, on) in enumerate(zip(DAYS, flags)):
        cx = x0 + pad + i * (chip_w + gap)
        fill = f'fill="{accent}" filter="url(#glow)"' if on else 'fill="#ffffff" fill-opacity=".06" stroke="#ffffff" stroke-opacity=".16" stroke-width="2"'
        color = 'fill="#ffffff"' if on else 'fill="#ffffff" fill-opacity=".42"'
        chips.append(f'<g class="fb" style="{_anim("pop", 0.5, 0.45 + i * 0.06, EASE_POP)}">'
                     f'<rect x="{cx:.1f}" y="{y:.0f}" width="{chip_w:.1f}" height="64" rx="18" {fill}/>'
                     f'<text x="{cx + chip_w / 2:.1f}" y="{y + 42:.0f}" text-anchor="middle" class="f-body" font-size="26" '
                     f'{color}>{day}</text></g>')
    y += 64 + 30
    time, zone = parse_time(f["time"], f["text"])
    big = ""
    if time:
        size = _fit_size([time + (zone or "")[:3]], inner - 40, 0.82, 128.0, 80.0)
        big = (f'<clipPath id="timeClip"><rect x="0" y="{y - 10:.0f}" width="{W:.0f}" height="{size * 1.12:.0f}"/></clipPath>'
               f'<g clip-path="url(#timeClip)"><g style="{_anim("rise", 0.8, 0.9)}">'
               f'<text x="{W / 2:.0f}" y="{y + size * 0.86:.0f}" text-anchor="middle" class="f-head" font-size="{size:.0f}" '
               f'font-family="{_esc(head_font)}" fill="#ffffff" filter="url(#neon)" letter-spacing="2" data-fit="{inner - 20:.0f}">'
               f'{_esc(time)}<tspan font-size="{size * 0.26:.0f}" dx="14" fill="{accent}" class="f-body">{_esc(zone or "")}</tspan>'
               f'</text></g></g>'
               f'<rect x="{W / 2 - 90:.0f}" y="{y + size * 1.1:.0f}" width="180" height="6" rx="3" fill="{accent}" '
               f'class="fb" style="{_anim("grow", 0.7, 1.15)}"/>')
        y += size * 1.1 + 34
    text = f["text"] or DEFAULT_TEXT
    tsize = _fit_size([text], inner, 0.6, 42.0, 28.0)
    phrase = (f'<text x="{W / 2:.0f}" y="{y + tsize:.0f}" text-anchor="middle" class="f-body" font-size="{tsize}" '
              f'fill="#ffffff" data-fit="{inner:.0f}" style="{_anim("fadeUp", 0.6, 1.1)}">{_esc(text)}</text>')
    y += tsize + 28
    row = ""
    if f["handle"]:
        cy = y + 36
        _, est = _handle_row(f, accent, 0, cy, 1.25, 38.0)
        row, _ = _handle_row(f, accent, max(x0 + pad, W / 2 - est / 2), cy, 1.25, 38.0)
        y = cy + 36
    h = y + pad - y0
    H = h + 2 * y0 + 20
    card = (x0, y0, W - 2 * x0, h)
    body = (f'<g class="fb" style="transform-origin:50% 60%;{_anim("cardIn", 0.8, 0.12)}">{_card(*card, accent, 0.12)}'
            f'{head}{pill}{"".join(chips)}{big}{phrase}{row}</g>')
    return _svg(W, H, _common_css(head_font), _defs(accent, card), body), int(W), int(round(H))


def _tpl_neon(f: dict, accent: str, head_font: str) -> tuple[str, int, int]:
    W = 960.0
    handle = f["handle"] or "мой канал"
    size = _fit_size([handle], 840, 0.78, 132.0, 64.0)
    pill, pw = _live_pill(W / 2 - (54 + 21 * 4) / 2, 70, 0.25)
    base = 150 + size * 0.86
    stroke = (f'<text x="{W / 2:.0f}" y="{base:.0f}" text-anchor="middle" class="f-head" font-size="{size}" '
              f'font-family="{_esc(head_font)}" data-fit="840" fill="#ffffff" fill-opacity="0" stroke="{accent}" '
              f'stroke-width="4" stroke-dasharray="2400" stroke-linejoin="round" filter="url(#softGlow)" '
              f'style="{_anim("draw", 1.3, 0.35, "cubic-bezier(.65,0,.35,1)")}">{_esc(handle)}</text>')
    fill = (f'<g style="animation:flicker .9s linear 1.55s both">'
            f'<text x="{W / 2:.0f}" y="{base:.0f}" text-anchor="middle" class="f-head" font-size="{size}" '
            f'font-family="{_esc(head_font)}" data-fit="840" fill="#ffffff" filter="url(#neon)" '
            f'style="{_anim("fillIn", 0.5, 1.35, "ease-out")}">{_esc(handle)}</text></g>')
    glow = (f'<ellipse cx="{W / 2:.0f}" cy="{base - size * 0.35:.0f}" rx="420" ry="{size * 0.9:.0f}" fill="url(#leak)" '
            f'style="animation:breathe 2.4s ease-in-out 1.4s infinite both"/>')
    y = base + 40
    text = f["text"] or DEFAULT_TEXT
    tsize = _fit_size([text], 840, 0.6, 46.0, 30.0)
    phrase = (f'<text x="{W / 2:.0f}" y="{y + tsize:.0f}" text-anchor="middle" class="f-body" font-size="{tsize}" '
              f'fill="#ffffff" data-fit="840" style="{_anim("fadeUp", 0.6, 1.1)}">{_esc(text)}</text>')
    y += tsize + 34
    row = ""
    if f["platform"] in PLATFORM_URLS and f["handle"]:
        label = PLATFORM_URLS[f["platform"]].format(h=f["handle"])
        est = 40 + 34 * 1.05 + 14 + len(label) * 34 * 0.56 + 40
        px = W / 2 - est / 2
        icon_svg, iw = _icon(f["platform"], px + 32, y + 32, 34 * 1.05, "#ffffff")
        row = (f'<g class="fb" style="{_anim("pop", 0.55, 1.3, EASE_POP)}">'
               f'<rect x="{px:.0f}" y="{y:.0f}" width="{est:.0f}" height="64" rx="32" fill="{accent}" filter="url(#glow)"/>'
               f'{icon_svg}<text x="{px + 32 + iw + 14:.0f}" y="{y + 44:.0f}" class="f-body" font-size="34" fill="#ffffff">'
               f'{_esc(label)}</text></g>')
        y += 64
    H = y + 70
    scrim = f'<ellipse cx="{W / 2:.0f}" cy="{H / 2:.0f}" rx="{W * 0.55:.0f}" ry="{H * 0.62:.0f}" fill="url(#scrim)" style="{_anim("fadeUp", 0.7, 0.05)}"/>'
    body = f'{scrim}{glow}{pill}{stroke}{fill}{phrase}{row}'
    return _svg(W, H, _common_css(head_font), _defs(accent, None), body), int(W), int(round(H))


_BUILDERS: dict[str, Callable[[dict, str, str], tuple[str, int, int]]] = {
    "live": _tpl_live, "schedule": _tpl_schedule, "neon": _tpl_neon,
}


def template_svg(template: str, fields: dict) -> tuple[str, int, int]:
    """SVG встроенного шаблона с текстами ``fields`` (text, handle, platform, accent, days, time, font)
    → (svg, ширина, высота) в единицах viewBox (= px webm при scale 1)."""
    es = normalize_end_screen({**fields, "template": template}) or {}
    accent = es.get("accent") or PLATFORM_COLORS.get(es.get("platform") or "none", PLATFORM_COLORS["none"])
    head_font = es.get("font") or HEAD_FONT
    return _BUILDERS.get(template, _tpl_live)(es, accent, head_font)


# --------------------------------------------------------------------------- #
# Клип
# --------------------------------------------------------------------------- #


def _speech_in(store: Any, clip_plan_id: int, start: float) -> int:
    """Сколько слов субтитров звучит в концовке (время клипа) — субтитры лягут поверх баннера."""
    try:
        words = (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
        return sum(1 for w in words if float(w.get("end") or w.get("start") or 0) > start + 0.15)
    except Exception:  # noqa: BLE001 — подсказка, не проверка
        return 0


def brand_fields(brand: dict) -> dict:
    """Поля концовки из бренда: шрифт, акцент, призыв (cta) и плашка канала (ник, площадка)."""
    cta, np = brand.get("cta") or {}, brand.get("nameplate") or {}
    text = " — ".join(t for t in (cta.get("text"), cta.get("url_text")) if t)
    out = {
        "text": text, "font": brand.get("font") or "", "accent": (brand.get("colors") or {}).get("accent"),
        "handle": np.get("handle") or "", "platform": np.get("platform") or "none",
        "template": cta.get("template") or TEMPLATE_DEFAULT,
        "duration": cta.get("duration_sec"),
    }
    if cta.get("blur") is not None:
        out["blur"] = cta["blur"]
    return {k: v for k, v in out.items() if v not in (None, "")}


def build_end_screen(
    store: Any, clip_plan_id: int, render: dict, duration: float, src: dict,
    run: Callable[[list[str], float], str] | None = None,
) -> dict:
    """Собрать концовку без записи: ``{layer, end_screen, start, info, warnings}``.

    ``src`` — поля ``normalize_end_screen`` + ``y`` (центр баннера, доля кадра) и ``w`` (ширина, опорные
    px). Баннер — webm из SVG шаблона или своего SVG (кэш по хешу: тот же текст — тот же файл)."""
    from app import motion
    from app.brand_kit import tail_start
    from app.svg_webm import asset_svg_text, svg_size, svg_to_asset

    es = normalize_end_screen(src)
    if es is None:
        raise ValueError("концовка выключена (on: false) — сними её remove: true")
    want = min(es["duration"], max(1.0, round(duration * 0.5, 3)))
    start = tail_start(render.get("cuts") or [], duration, want)
    warnings: list[str] = []
    if want < es["duration"]:
        warnings.append(f"клип {duration:.1f} с — концовка урезана до {want:.1f} с (не больше половины ролика)")
    if es["svg_asset_id"]:
        svg = asset_svg_text(store, es["svg_asset_id"])
        sw, _sh = svg_size(svg)
        width = float(_num(src.get("w"), min(BANNER_W_DEFAULT, max(400.0, float(sw))), 120.0, 1080.0))
        label = f"Концовка: свой SVG #{es['svg_asset_id']}"
    else:
        svg, sw, _sh = template_svg(es["template"], es)
        width = float(_num(src.get("w"), BANNER_W_DEFAULT, 120.0, 1080.0))
        label = f"Концовка: {es['template']} — {es['handle'] or es['text'] or DEFAULT_TEXT}"[:120]
    # webm — в том размере, что баннер займёт на кадре 1080 (не мельче — не мылится).
    scale = max(0.5, min(2.0, width / max(1.0, float(sw))))
    asset, info = svg_to_asset(store, svg, {"duration": round(want + WEBM_TAIL, 3), "fps": WEBM_FPS, "scale": scale},
                               label=label, run=run)
    es["asset_id"], es["duration"] = int(asset["id"]), want
    y = _num(src.get("y"), BANNER_Y_DEFAULT, 0.12, 0.8)
    layer = {
        "id": END_LAYER_ID, "type": "image", "name": "Концовка: баннер", "start": start, "end": round(duration, 3),
        "track": 7, "x": 0.5, "y": y, "props": {"asset_id": int(asset["id"]), "w": round(width, 1)},
        "in": {"type": "none"}, "out": {"type": "none"},
    }
    problems = motion.validate_layers([layer], duration)
    if problems:
        raise ValueError("баннер концовки не прошёл проверку: " + "; ".join(problems))
    talk = _speech_in(store, clip_plan_id, start)
    if talk >= 3:
        warnings.append(f"в концовке ещё звучит речь ({talk} слов) — субтитры лягут поверх баннера; "
                        "ставь концовку после развязки или продли клип")
    return {"layer": layer, "end_screen": es, "start": start, "info": info, "warnings": warnings}


def set_end_screen(
    store: Any, clip_plan_id: int, settings: dict | None = None, *, brand_id: int | None = None,
    remove: bool = False, before_change: Callable[[], None] | None = None,
    run: Callable[[list[str], float], str] | None = None,
) -> dict:
    """Поставить (заменить) или снять концовку клипа.

    С ``brand_id`` основа — бренд (``brand_fields``), поля ``settings`` поверх. Повтор заменяет прежнюю
    концовку целиком. Ошибки: ValueError (нечего ставить, SVG/слой не прошёл), KeyError (нет клипа),
    app.svg_webm.SvgWebmError (нет браузера/Node)."""
    from app.agent_tools import clip_timeline

    timeline = clip_timeline(store, clip_plan_id)
    render, duration = dict(timeline["render"]), float(timeline["duration"])
    if duration <= 0:
        raise ValueError("в клипе нет кусков")
    current = list(render.get("layers") or [])
    layers = [dict(l) for l in current if l.get("id") != END_LAYER_ID]

    def commit(label: str) -> dict:
        render["layers"] = layers
        if before_change is not None:
            before_change()
        else:
            from app.clip_history import snapshot

            snapshot(store, clip_plan_id, label)
        return store.set_clip_plan_render_settings(clip_plan_id, render)

    if remove:
        had = bool(render.get("end_screen")) or len(layers) != len(current)
        if had:
            render["end_screen"] = None
            commit("концовка снята")
        return {"ok": True, "removed": had, **({} if had else {"note": "концовки в клипе не было"})}

    src = dict(settings or {})
    if brand_id:
        src = {**brand_fields(store.get_brand(int(brand_id))), **{k: v for k, v in src.items() if v is not None}}
    built = build_end_screen(store, clip_plan_id, render, duration, src, run=run)
    es, start, info = built["end_screen"], built["start"], built["info"]
    layers.append(built["layer"])
    render["end_screen"] = es
    warnings = list(built["warnings"])
    if any(l.get("id") == "brand_cta" and not l.get("hidden") for l in layers):
        warnings.append("в клипе есть плашка-призыв бренда (brand_cta) — на концовке будет два призыва; оставь один")
    commit(f"концовка «{es['template'] or 'свой SVG'}»")
    seen = min(duration, start + es["ramp"] + 1.2)
    return {
        "ok": True, "layer_id": END_LAYER_ID, "asset_id": es["asset_id"], "template": es["template"],
        "svg_asset_id": es["svg_asset_id"], "start": start, "duration": es["duration"], "blur": es["blur"],
        "dim": es["dim"], "cached": bool(info.get("cached")),
        "banner": {"w": built["layer"]["props"]["w"], "y": built["layer"]["y"], "frames": info.get("frames")},
        "warnings": warnings,
        "check": (f"view_frame t={seen:.1f} — баннер читается, видео под ним размыто; "
                  f"view_frame t={max(0.0, start - 0.3):.1f} — до концовки кадр резкий"),
    }

