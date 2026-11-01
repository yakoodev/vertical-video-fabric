"""Линт качества клипа перед сдачей (``lint_clip``): то, что глазами проверять долго и ненадёжно.

Закрывает строки 7, 8, 16, 17 docs/guides/missing.md. Четыре проверки (``CHECKS``):

* ``ui_zones`` — текст, субтитры (слой ``captions`` и прожигаемые ASS), плашка канала, эмодзи и
  стикеры против интерфейса площадок 9:16: колонка кнопок справа, подпись снизу (у Reels выше,
  чем у TikTok и Shorts), шапка сверху. Раскладки — ``app/safe_zones.PLATFORM_UI`` (приближение,
  источник там же). Находка: какой слой, на какой площадке, на сколько px заходит, когда, и как
  поправить (сдвинуть ``x``/``y``, сузить ``maxWidth``, поднять субтитры ``position_pct``).
* ``reading`` — темп чтения титров и страниц субтитров: время на экране против
  ``READ_SEC_PER_WORD`` · слов + ``READ_BASE_SEC`` и знаки в секунду. Время — на шкале clip
  (как слои и таймлайн редактора), длительность — в готовом ролике (вырезы внутри титра его
  съедают). Скорость кусков (app/piece_speed.py) уже в шкале clip: слова ускоренного куска
  стоят плотнее — находка называет кусок и его скорость. Субтитры — те же слова, что увидит
  зритель: после J/L-склеек (слова идут за звуком, ``audio_edit.heard_subtitles``), без кусков
  с реверсом.
* ``flashes`` — вспышки готового рендера (ffmpeg → кадры 40×40 по 30 в секунду): общие (перепад
  относительной яркости ≥ 10 %, тёмная сторона < 0.8) и насыщенно-красные, по всему кадру и по
  четвертям (площадь ≥ ¼ кадра, как у WCAG 2.3.1 / критерия Хардинга — упрощённо). Больше 3
  вспышек в секунду — диапазоны времени.
* ``loop`` — насколько конец стыкуется с началом: разница последнего и первого кадра, уровень
  звука последних ~0.3 с против первых 0.3 с → оценка 0..1 и подсказки (``set_end_hold(0)``,
  подрезать хвост, убрать концовку-баннер). Без рендера — только подсказки по настройкам клипа.

Только предупреждения: ничего не меняет и не блокирует. ``plan``-проверки (зоны, чтение,
подсказки лупа) — без медиа, за доли секунды; ``flashes`` и оценка лупа — по последнему
готовому рендеру (``media``: auto — если он есть). Время находок: ``t`` — шкала clip (перемотка
в редакторе, ``view_frame``), ``final_t`` — секунды готового ролика.
"""

from __future__ import annotations

import math
import re
import subprocess
import threading
from pathlib import Path
from typing import Any

CHECKS = ("ui_zones", "reading", "flashes", "loop")
CHECK_ALIASES = {
    "zones": "ui_zones", "ui": "ui_zones", "ui_zone": "ui_zones", "platform": "ui_zones", "buttons": "ui_zones",
    "read": "reading", "reading_speed": "reading", "speed": "reading", "subtitles": "reading",
    "flash": "flashes", "strobe": "flashes", "photosensitive": "flashes", "loops": "loop",
}
MEDIA_CHECKS = ("flashes",)  # без рендера не проверить; loop без рендера — только подсказки

W, H = 1080.0, 1920.0  # опорный кадр 9:16 — интерфейс площадок есть только у вертикали

# ---------------------------------------------------------------- 1) интерфейс площадок
MIN_PEN_PX = 12.0  # заходит меньше — край иконки и поле, не шумим
WARN_PEN_PX = 40.0  # глубже — уже под кнопкой/строкой подписи
WARN_AREA_SHARE = 0.10
ZONE_GAP_PX = 8.0  # запас до зоны в совете
SAMPLE_STEP = 0.25
SAMPLE_MAX = 40
MIN_OPACITY = 0.3
STICKER_MAX_SHARE = 0.45  # картинка уже этого — наклейка/мем поверх, а не сцена
ZONE_LABELS = {
    "right": "под колонку кнопок справа (лайк, комментарии, репост)",
    "bottom": "в нижнюю подпись площадки (ник, описание, звук)",
    "top": "под шапку площадки",
}

# ---------------------------------------------------------------- 2) темп чтения
READ_SEC_PER_WORD = 0.3
READ_BASE_SEC = 0.5
FAST_CPS = 25.0  # знаков в секунду: быстрее страницу субтитров не дочитать
FLASH_PAGE_SHARE = 0.5  # страница короче половины нормы — «мелькнула»
MAX_PAGE_FINDINGS = 10
SHORT_PAGES_WARN_SHARE = 0.4
# Слой captions (frontend/src/motion/layers/captions.ts): пауза делит фразу, хвост страницы,
# перекрытие короткой паузы до следующей страницы.
CAPTION_PAUSE_SPLIT = 0.6
CAPTION_HOLD = 0.45
CAPTION_BRIDGE = 0.35
CAPTION_BASE = {"size": 88.0, "maxWords": 3, "maxLines": 2, "maxWidth": 860.0, "lineHeight": 1.12,
                "uppercase": True, "punctuation": False, "weight": 900, "wordScale": 1.12, "letterSpacing": 0.0}
# Пресеты стилей (captions.ts CAPTION_STYLES): то, что влияет на ширину и страницы.
CAPTION_STYLE_LOOK: dict[str, dict] = {
    "hormozi": {"size": 92, "maxWords": 3, "maxLines": 2, "wordScale": 1.16},
    "mrbeast": {"size": 132, "maxWords": 2, "maxLines": 1, "maxWidth": 880, "wordScale": 1.08},
    "box": {"size": 86, "maxWords": 4, "maxLines": 2, "wordScale": 1.1},
    "karaoke": {"size": 84, "maxWords": 5, "maxLines": 2, "wordScale": 1.0},
    "neon": {"size": 76, "maxWords": 3, "maxLines": 2},
    "minimal": {"size": 62, "maxWords": 7, "maxLines": 2, "maxWidth": 880, "uppercase": False,
                "punctuation": True, "weight": 700},
    "typewriter": {"size": 70, "maxWords": 6, "maxLines": 2, "maxWidth": 880, "uppercase": False,
                   "punctuation": True},
    "comic": {"size": 90, "maxWords": 3, "maxLines": 2, "weight": 400},
    "gradient": {"size": 92, "maxWords": 3, "maxLines": 2, "wordScale": 1.15},
    "outline": {"size": 96, "maxWords": 3, "maxLines": 2},
    "stomp": {"size": 120, "maxWords": 2, "maxLines": 1, "maxWidth": 900, "wordScale": 1.06},
}
ORIG_SPEAKER = "orig"  # app/translation.ORIG_SPEAKER — оригинал перевода, только явным слоем
_EDGE_PUNCT = re.compile(r"^[^\w*]+|[^\w*]+$", re.UNICODE)
_SENTENCE_END = re.compile(r"[.!?…][\"»”)]*$")

# ---------------------------------------------------------------- 3) вспышки
FLASH_FPS = 30
FLASH_SIZE = 40  # кадр 40×40: доли площади те же, пропорция не важна
FLASH_LUM_DELTA = 0.10  # перепад относительной яркости (WCAG: 10 % максимума)
FLASH_DARK_MAX = 0.80  # тёмная сторона перепада ниже этого
RED_DELTA = 20.0  # насыщенный красный: (R − G − B)·320 при R/(R+G+B) ≥ 0.8
RED_RATIO = 0.8
MAX_FLASHES_PER_SEC = 3
FLASH_TIMEOUT_SEC = 600
_REGIONS = {
    "весь кадр": (slice(0, FLASH_SIZE), slice(0, FLASH_SIZE)),
    "левый верх": (slice(0, FLASH_SIZE // 2), slice(0, FLASH_SIZE // 2)),
    "правый верх": (slice(0, FLASH_SIZE // 2), slice(FLASH_SIZE // 2, FLASH_SIZE)),
    "левый низ": (slice(FLASH_SIZE // 2, FLASH_SIZE), slice(0, FLASH_SIZE // 2)),
    "правый низ": (slice(FLASH_SIZE // 2, FLASH_SIZE), slice(FLASH_SIZE // 2, FLASH_SIZE)),
    "центр": (slice(FLASH_SIZE // 4, 3 * FLASH_SIZE // 4), slice(FLASH_SIZE // 4, 3 * FLASH_SIZE // 4)),
}

# ---------------------------------------------------------------- 4) луп
LOOP_AUDIO_SEC = 0.3
LOOP_FRAME = 48
LOOP_MAD_SCALE = 0.25  # средняя разница кадров 25 % яркости — «совсем другой кадр»
LOOP_SILENT_DB = -50.0
LOOP_DB_SCALE = 24.0
LOOP_VISUAL_WEIGHT = 0.6


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _short(text: Any, limit: int = 28) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def normalize_checks(raw: Any) -> list[str]:
    """``checks`` — список или строка через запятую; пусто — все. Неизвестное — ValueError."""
    if raw in (None, "", []):
        return list(CHECKS)
    items = raw.replace(";", ",").split(",") if isinstance(raw, str) else list(raw) if isinstance(raw, (list, tuple)) else None
    if items is None:
        raise ValueError(f"checks — список из {', '.join(CHECKS)}")
    out: list[str] = []
    for item in items:
        name = str(item).strip().lower()
        if not name:
            continue
        name = CHECK_ALIASES.get(name, name)
        if name == "all":
            return list(CHECKS)
        if name not in CHECKS:
            raise ValueError(f"проверки {item!r} нет; есть: {', '.join(CHECKS)}")
        if name not in out:
            out.append(name)
    return out or list(CHECKS)


def _finding(check: str, severity: str, message: str, fix: str = "", t: float | None = None,
             until: float | None = None, layer: str | None = None, **data: Any) -> dict:
    out: dict[str, Any] = {"check": check, "severity": severity, "message": message}
    if fix:
        out["fix"] = fix
    if t is not None:
        out["t"] = round(max(0.0, float(t)), 2)
    if until is not None:
        out["until"] = round(max(0.0, float(until)), 2)
    if layer:
        out["layer"] = layer
    out.update({k: v for k, v in data.items() if v not in (None, [], {})})
    return out


# =============================================================== 1) интерфейс площадок


def caption_look(props: Any) -> dict:
    """Вид слоя субтитров, как resolveCaptions движка: база → пресет style → заданное в props."""
    props = props if isinstance(props, dict) else {}
    look = dict(CAPTION_BASE)
    look.update(CAPTION_STYLE_LOOK.get(str(props.get("style") or "hormozi"), {}))
    for key in CAPTION_BASE:
        if props.get(key) is not None:
            look[key] = props[key]
    look["size"] = max(12.0, _num(look["size"], 88.0))
    look["maxWords"] = max(1, min(8, int(round(_num(look["maxWords"], 3)))))
    look["maxLines"] = max(1, min(3, int(round(_num(look["maxLines"], 2)))))
    look["maxWidth"] = _num(look["maxWidth"], 860.0)
    look["wordScale"] = max(1.0, min(1.4, _num(look["wordScale"], 1.12)))
    look["lineHeight"] = _num(look["lineHeight"], 1.12)
    look["speakers"] = [str(s) for s in props.get("speakers") or [] if str(s)]
    return look


def _char_factor(uppercase: bool, weight: float) -> float:
    """Средняя ширина буквы в долях кегля — как agent_tools.text_lines: жирный капс шире."""
    return 0.68 if uppercase and weight >= 700 else 0.58


def _caption_word(raw: str, look: dict) -> str:
    text = str(raw or "").strip()
    if not look.get("punctuation"):
        text = _EDGE_PUNCT.sub("", text)
    return text.upper() if look.get("uppercase") else text


def _pack(widths: list[float], space: float, max_w: float) -> list[float]:
    """Строки жадно по ширине (как packLines движка): ширина каждой строки."""
    lines: list[float] = []
    cur = 0.0
    for k, w in enumerate(widths):
        if k > 0 and cur > 0 and cur + space + w > max_w:
            lines.append(cur)
            cur = 0.0
        cur = cur + space + w if cur > 0 else w
    lines.append(cur)
    return lines


def caption_layer_pages(words: list[dict], props: Any, start: float = 0.0, end: float = math.inf) -> list[dict]:
    """Страницы слоя ``captions`` (порт captionPages движка, ширина слов — оценкой по кеглю):
    ``[{t0, t1, words, text, lines, width}]`` на шкале clip. ``words`` — comp.words ``{t0, t1, w, s?}``."""
    look = caption_look(props)
    size = look["size"]
    factor = _char_factor(bool(look["uppercase"]), _num(look["weight"], 900))
    spacing = _num(look["letterSpacing"], 0.0)
    space = size * 0.3
    max_w = look["maxWidth"] / max(1.0, look["wordScale"] * 0.97)
    items = []
    for wd in words or []:
        if not isinstance(wd, dict):
            continue
        t0 = _num(wd.get("t0"), math.nan)
        if not math.isfinite(t0) or t0 < start - 0.05 or t0 >= end:
            continue
        who = str(wd.get("s") or "A")
        if (look["speakers"] and who not in look["speakers"]) or (not look["speakers"] and who == ORIG_SPEAKER):
            continue
        text = _caption_word(str(wd.get("w") or ""), look)
        if not text:
            continue
        t1 = max(t0 + 0.05, _num(wd.get("t1"), t0))
        items.append({"text": text, "raw": str(wd.get("w") or ""), "t0": t0, "t1": t1, "s": who,
                      "w": len(text) * (size * factor + spacing)})
    items.sort(key=lambda it: it["t0"])
    phrases: list[list[dict]] = []
    cur: list[dict] = []
    for it in items:
        if cur:
            prev = cur[-1]
            if (it["t0"] - prev["t1"] > CAPTION_PAUSE_SPLIT or _SENTENCE_END.search(prev["raw"].strip())
                    or it["s"] != prev["s"]):
                phrases.append(cur)
                cur = []
        cur.append(it)
    if cur:
        phrases.append(cur)

    def fits(group: list[dict]) -> bool:
        return len(_pack([g["w"] for g in group], space, max_w)) <= look["maxLines"]

    groups: list[list[dict]] = []
    for ph in phrases:
        count = math.ceil(len(ph) / look["maxWords"])
        base, extra = divmod(len(ph), count)
        pos = 0
        for k in range(count):
            size_k = base + (1 if k < extra else 0)
            chunk = ph[pos:pos + size_k]
            pos += size_k
            if fits(chunk):
                groups.append(chunk)
                continue
            part: list[dict] = []
            for it in chunk:
                if part and not fits([*part, it]):
                    groups.append(part)
                    part = []
                part.append(it)
            if part:
                groups.append(part)
    pages = []
    for g in groups:
        lines = _pack([x["w"] for x in g], space, max_w)
        # Звучащее слово крупнее (wordScale) — строка шире на его прибавку, не вся целиком.
        grow = (look["wordScale"] - 1) * max(x["w"] for x in g)
        pages.append({"t0": g[0]["t0"], "t1": g[-1]["t1"] + CAPTION_HOLD, "words": [x["text"] for x in g],
                      "text": " ".join(x["text"] for x in g), "lines": len(lines),
                      "width": min(max(lines), max_w) + grow,
                      "height": len(lines) * size * look["lineHeight"]})
    for k in range(len(pages) - 1):
        nxt = pages[k + 1]["t0"]
        pages[k]["t1"] = nxt if nxt - pages[k]["t1"] < CAPTION_BRIDGE else min(pages[k]["t1"], nxt)
    if pages and math.isfinite(end):
        pages[-1]["t1"] = min(pages[-1]["t1"], end)
    return [p for p in pages if p["t1"] > p["t0"]]


def ass_pages(words: list[dict], profile: dict, width: int = 1080) -> list[dict]:
    """Страницы прожигаемых субтитров (app/subtitles/ass.py — та же разбивка и тот же хвост
    слова): ``[{t0, t1, words, text, lines, width, height}]``. ``words`` — ``{t0, t1, w}``."""
    from app.subtitles.ass import (
        CHAR_WIDTH_RATIO,
        CHAR_WIDTH_RATIO_UPPERCASE,
        MAX_SUBTITLE_HOLD_AFTER_WORD_SEC,
        _chars_per_line,
        _normalized_words,
        _pack_lines,
        _subtitle_page_indices,
    )
    from app.subtitles.contracts import SubtitleWord

    uppercase = bool(profile.get("uppercase"))
    font = max(8, int(_num(profile.get("font_size"), 64) or 64))
    max_words = max(1, int(_num(profile.get("max_words_per_line"), 5) or 5))
    per_line = _chars_per_line(profile, width, uppercase)
    raw = [SubtitleWord(word=str(w.get("w") or ""), start=_num(w.get("t0")), end=_num(w.get("t1")))
           for w in words or [] if isinstance(w, dict) and str(w.get("s") or "") != ORIG_SPEAKER]
    norm = _normalized_words(raw, uppercase)
    ratio = CHAR_WIDTH_RATIO_UPPERCASE if uppercase else CHAR_WIDTH_RATIO
    out = []
    for indices in _subtitle_page_indices(norm, max_words, per_line):
        page = [norm[i] for i in indices]
        lines = _pack_lines(page, max_words, per_line)
        last = indices[-1]
        end = page[-1].end + MAX_SUBTITLE_HOLD_AFTER_WORD_SEC
        if last + 1 < len(norm):
            end = min(end, norm[last + 1].start)
        end = max(end, page[-1].end)
        chars = max(sum(len(page[i].word) for i in line) + len(line) - 1 for line in lines)
        out.append({"t0": page[0].start, "t1": end, "words": [w.word for w in page],
                    "text": " ".join(w.word for w in page), "lines": len(lines),
                    "width": min(width - 160.0, chars * font * ratio), "height": len(lines) * font * 1.2})
    return out


def _nameplate_box(props: dict) -> tuple[float, float, float]:
    """(ширина пилюли, высота, смещение её центра от центра слоя) — как geometry() nameplate.ts."""
    h = max(20.0, _num(props.get("size"), 74.0))
    handle = str(props.get("handle") or "").strip()
    icon = h * 0.56 if str(props.get("platform") or "none") != "none" else 0.0
    text = len(handle) * h * 0.5 * 0.6
    live = h * 0.22 if props.get("live") else 0.0
    parts = [p for p in (icon, text, live) if p > 0]
    w = max(h, h * 0.84 + sum(parts) + max(0, len(parts) - 1) * h * 0.26)
    box = max(w, _num(props.get("boxWidth"), 0.0))
    align = props.get("align") or "center"
    shift = -box / 2 + w / 2 if align == "left" else box / 2 - w / 2 if align == "right" else 0.0
    return w, h, shift


def _counter_text(props: dict) -> str:
    to = _num(props.get("to"), 100.0)
    decimals = int(_num(props.get("decimals"), 0))
    return f"{props.get('prefix') or ''}{to:.{decimals}f}{props.get('suffix') or ''}"


def is_sticker(layer: dict) -> bool:
    """Наклейка/мем поверх: картинка из пака (sticker:/emoji:) или неширокая картинка."""
    if layer.get("type") != "image" or layer.get("behind"):
        return False
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    if props.get("device"):
        return False
    ref = str(props.get("src") or props.get("asset_id") or "")
    if ref.startswith(("sticker:", "emoji:")):
        return True
    return _num(props.get("w"), 400.0) * _num(layer.get("scale"), 1.0) < STICKER_MAX_SHARE * W


ZONE_TYPES = ("text", "counter", "callout", "nameplate", "emoji", "image")


def layer_box(layer: dict, t: float, camera: Any = None) -> tuple[float, float, float, float] | None:
    """Прямоугольник слоя в кадре 1080×1920 (px) в момент ``t`` с ключами x/y/scale/opacity и
    наездом камеры (followCamera); None — не виден или не из проверяемых."""
    from app.promo_lint import _through_camera, layer_state, picture_size, text_size

    kind = layer.get("type")
    if kind not in ZONE_TYPES or (kind == "image" and not is_sticker(layer)):
        return None
    st = layer_state(layer, t)
    if st["opacity"] < MIN_OPACITY or st["scale"] <= 0:
        return None
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    shift = 0.0
    if kind == "text":
        w, h = text_size(layer)
    elif kind == "counter":
        w, h = text_size({"props": {**props, "text": _counter_text(props)}})
    elif kind == "callout":
        w, h = _num(props.get("w"), 900.0), 220.0 if props.get("subtitle") else 160.0
    elif kind == "nameplate":
        w, h, shift = _nameplate_box(props)
    elif kind == "emoji":
        w = h = _num(props.get("size"), 160.0)
    else:
        w, h = picture_size(layer)
    w, h, shift = w * st["scale"], h * st["scale"], shift * st["scale"]
    cx, cy = st["x"] * W + shift, st["y"] * H
    rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    if layer.get("followCamera") and camera:
        rect = _through_camera(rect, camera, t)
    return rect


def _samples(start: float, end: float) -> list[float]:
    if end <= start:
        return []
    span = end - start
    step = max(SAMPLE_STEP, span / SAMPLE_MAX)
    times, t = [], start + min(0.05, span / 2)
    while t < end and len(times) < SAMPLE_MAX + 1:
        times.append(round(t, 3))
        t += step
    return times


def _on_frame(rect: tuple) -> bool:
    x0, y0, x1, y1 = rect
    return x1 > 0 and x0 < W and y1 > 0 and y0 < H


def zone_penetration(rect: tuple, zone: str, ui: dict, captions: bool = False) -> tuple[float, float]:
    """(на сколько px слой заходит в зону, доля площади слоя в зоне) для раскладки ``ui``."""
    if not _on_frame(rect):
        return 0.0, 0.0
    x0, y0, x1, y1 = rect
    area = max(1.0, (x1 - x0) * (y1 - y0))
    if zone == "right":
        z = ui["right"]
        zx0, zy0, zy1 = z["x0"] * W, z["y0"] * H, z["y1"] * H
        overlap_h = min(y1, zy1) - max(y0, zy0)
        if overlap_h < MIN_PEN_PX:
            return 0.0, 0.0
        pen = min(x1, W) - zx0
        return max(0.0, pen), max(0.0, pen) * overlap_h / area
    if zone == "bottom":
        zy = (ui["bottom"]["captions_y0"] if captions else ui["bottom"]["y0"]) * H
        pen = min(y1, H) - zy
        return max(0.0, pen), max(0.0, pen) * (min(x1, W) - max(x0, 0.0)) / area
    zy = ui["top"]["y1"] * H
    pen = zy - max(y0, 0.0)
    return max(0.0, pen), max(0.0, pen) * (min(x1, W) - max(x0, 0.0)) / area


def _fix_for(zone: str, rect: tuple, ui_list: list[dict], kind: str, captions: bool, ass: bool) -> str:
    """Совет против самой строгой из задетых площадок."""
    x0, y0, x1, y1 = rect
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    if zone == "right":
        limit = min(ui["right"]["x0"] for ui in ui_list) * W - ZONE_GAP_PX
        shift = x1 - limit
        new_x = max(0.0, (cx - shift) / W)
        if ass:
            return "строки субтитров длинные — меньше слов в строке (стиль субтитров, max_words_per_line) или кегль меньше"
        if kind in ("text", "counter", "callout") or captions:
            width = max(0.0, 2 * (limit - cx))
            what = "maxWidth" if kind != "callout" else "props.w"
            return (f"сузь {what} до ≤ {int(width)} px (перенесётся на строку ниже) или сдвинь левее: x ≤ {new_x:.3f}"
                    if cx < limit else f"сдвинь левее: x ≤ {new_x:.3f}")
        return f"сдвинь левее: x ≤ {new_x:.3f}"
    if zone == "bottom":
        key = "captions_y0" if captions else "y0"
        limit = min(ui["bottom"][key] for ui in ui_list) * H - ZONE_GAP_PX
        if ass:
            pct = math.ceil((H - limit) / H * 100)
            return f"подними субтитры: set_subtitles(position_pct ≥ {pct})"
        new_y = (cy - (y1 - limit)) / H
        return f"подними: y ≤ {math.floor(new_y * 1000) / 1000:.3f}"
    limit = max(ui["top"]["y1"] for ui in ui_list) * H + ZONE_GAP_PX
    new_y = (cy + (limit - y0)) / H
    return f"опусти: y ≥ {math.ceil(new_y * 1000) / 1000:.3f}"


def _what(layer: dict) -> str:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    kind = layer.get("type")
    if kind == "text":
        return f"титр «{_short(props.get('text'))}»"
    if kind == "callout":
        return f"плашка «{_short(props.get('title') or props.get('subtitle'))}»"
    if kind == "nameplate":
        return f"плашка канала {_short(props.get('handle'))}".strip()
    if kind == "captions":
        return "субтитры (слой)"
    if kind == "counter":
        return "счётчик"
    if kind == "emoji":
        return f"эмодзи {props.get('char') or ''}".strip()
    return "стикер" if str(props.get("src") or props.get("asset_id") or "").startswith("sticker:") else "картинка"


def _zone_findings_for(boxes: list[tuple[float, tuple]], what: str, kind: str, lid: str | None,
                       uis: dict[str, dict], captions: bool = False, ass: bool = False) -> list[dict]:
    """Находки одного слоя: ``boxes`` — (момент clip, прямоугольник px) по выборке времени."""
    out = []
    for zone in ("right", "bottom", "top"):
        worst: dict[str, tuple[float, float, float, tuple]] = {}
        for t, rect in boxes:
            for key, ui in uis.items():
                pen, share = zone_penetration(rect, zone, ui, captions)
                if pen >= MIN_PEN_PX and pen > worst.get(key, (0.0,))[0]:
                    worst[key] = (pen, share, t, rect)
        if not worst:
            continue
        hit = sorted(worst, key=lambda k: -worst[k][0])
        pen, share, t, rect = worst[hit[0]]
        severity = "warn" if pen >= WARN_PEN_PX or share >= WARN_AREA_SHARE else "info"
        names = ", ".join(f"{uis[k]['name']} — {int(round(worst[k][0]))} px" for k in hit)
        times = [b[0] for b in boxes]
        out.append(_finding(
            "ui_zones", severity, f"{what} заходит {ZONE_LABELS[zone]}: {names} (сильнее всего на {t:.1f} с)",
            _fix_for(zone, rect, [uis[k] for k in hit], kind, captions, ass), t=t,
            until=max(times) if len(times) > 1 else None, layer=lid, zone=zone, platforms=hit,
            overlap_px={k: int(round(worst[k][0])) for k in hit}))
    return out


def ui_zone_findings(layers: list[dict], platforms: Any = None, camera: Any = None,
                     caption_pages: dict[str, list[dict]] | None = None, ass_block: dict | None = None) -> list[dict]:
    """Слои 9:16 против интерфейса площадок (``safe_zones.PLATFORM_UI``).

    ``caption_pages`` — {id слоя captions: его страницы} (``caption_layer_pages``): у субтитров
    рамка по странице (своя ширина и число строк). ``ass_block`` — прожигаемые субтитры:
    ``{pages, bottom_pct, align}`` (``sub_pos_pct`` от низа кадра)."""
    from app.promo_lint import layer_state
    from app.safe_zones import platform_ui

    uis = platform_ui(platforms)
    out: list[dict] = []
    for layer in layers:
        if layer.get("hidden"):
            continue
        lid = str(layer.get("id") or layer.get("type") or "?")
        start, end = _num(layer.get("start")), _num(layer.get("end"))
        if layer.get("type") == "captions":
            pages = (caption_pages or {}).get(lid) or []
            look = caption_look(layer.get("props"))
            boxes = []
            for page in pages:
                st = layer_state(layer, page["t0"])
                if st["opacity"] < MIN_OPACITY:
                    continue
                w, h = page["width"] * st["scale"], page["height"] * st["scale"]
                cx, cy = st["x"] * W, st["y"] * H
                boxes.append((round(page["t0"], 2), (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
            if not boxes:  # слов ещё нет — рамка по виду слоя (полная ширина, все строки)
                st = layer_state(layer, start)
                w = look["maxWidth"] * st["scale"]
                h = look["maxLines"] * look["size"] * look["lineHeight"] * st["scale"]
                boxes = [(start, (st["x"] * W - w / 2, st["y"] * H - h / 2, st["x"] * W + w / 2, st["y"] * H + h / 2))]
            out += _zone_findings_for(boxes, "субтитры (слой)", "captions", lid, uis, captions=True)
            continue
        boxes = [(t, rect) for t in _samples(start, end) if (rect := layer_box(layer, t, camera))]
        if boxes:
            out += _zone_findings_for(boxes, _what(layer), str(layer.get("type")), lid, uis)
    if ass_block and ass_block.get("pages"):
        bottom = H * (1 - _num(ass_block.get("bottom_pct"), 12.0) / 100)
        align = int(_num(ass_block.get("align"), 2))
        boxes = []
        for page in ass_block["pages"]:
            w, h = page["width"], page["height"]
            if align in (7, 8, 9):
                y0 = H * _num(ass_block.get("bottom_pct"), 12.0) / 100
                rect = (W / 2 - w / 2, y0, W / 2 + w / 2, y0 + h)
            elif align in (4, 5, 6):
                rect = (W / 2 - w / 2, H / 2 - h / 2, W / 2 + w / 2, H / 2 + h / 2)
            else:
                rect = (W / 2 - w / 2, bottom - h, W / 2 + w / 2, bottom)
            boxes.append((round(page["t0"], 2), rect))
        out += _zone_findings_for(boxes, "прожигаемые субтитры", "captions", None, uis, captions=True, ass=True)
    return out


# =============================================================== 2) темп чтения


def read_need(words: int) -> float:
    """Сколько секунд нужно на экране, чтобы прочитать ``words`` слов."""
    return READ_SEC_PER_WORD * max(1, words) + READ_BASE_SEC


def _final_span(clock: Any, start: float, end: float) -> float:
    """Сколько секунд отрезок шкалы clip идёт в готовом ролике (вырезы внутри его съедают)."""
    if clock is None or end <= start:
        return max(0.0, end - start)
    try:
        return max(0.0, clock.clip_to_final(end) - clock.clip_to_final(start))
    except Exception:  # noqa: BLE001 - без часов — как есть
        return max(0.0, end - start)


def _piece_speed_note(clock: Any, t: float) -> tuple[int | None, float]:
    """(номер куска, его скорость) в момент ``t`` шкалы clip."""
    if clock is None or not getattr(clock, "segments", None):
        return None, 1.0
    from app.piece_speed import seg_speed

    try:
        index = clock.piece_at_clip(t)
        return index, float(seg_speed(clock.segments[index]))
    except Exception:  # noqa: BLE001
        return None, 1.0


def _title_text(layer: dict) -> str:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    kind = layer.get("type")
    if kind == "callout":
        return " ".join(str(props.get(k) or "") for k in ("title", "subtitle")).strip()
    if kind == "counter":
        return _counter_text(props)
    return str(props.get("text") or "")


def _anim_len(layer: dict, key: str) -> float:
    anim = layer.get(key) if isinstance(layer.get(key), dict) else {}
    if not anim or anim.get("type") in (None, "", "none"):
        return 0.0
    duration = _num(anim.get("duration"), 0.3)
    return duration if anim.get("type") in ("typewriter", "type", "reveal") else duration / 2


def title_reading_findings(layers: list[dict], clock: Any = None, duration: float | None = None) -> list[dict]:
    """Титры (text, counter, callout): виден меньше, чем нужно на его слова."""
    out = []
    for layer in layers:
        if layer.get("hidden") or layer.get("type") not in ("text", "counter", "callout"):
            continue
        text = " ".join(_title_text(layer).split())
        words = len([w for w in text.split() if any(ch.isalnum() for ch in w)])
        if not words:
            continue
        start, end = _num(layer.get("start")), _num(layer.get("end"))
        if duration:
            end = min(end, float(duration))
        if duration and start <= 0.05 and end >= float(duration) - 0.05:
            continue  # весь ролик — прочитают
        anim = _anim_len(layer, "in") + _anim_len(layer, "out")
        shown = _final_span(clock, start, end)
        readable = max(0.0, shown - anim)
        need = read_need(words)
        if readable >= need:
            continue
        lid = str(layer.get("id") or "?")
        index, speed = _piece_speed_note(clock, start)
        cut = shown < (end - start) - 0.05
        fits = max(0, int((readable - READ_BASE_SEC) / READ_SEC_PER_WORD))
        want_end = start + need + anim + ((end - start) - shown)
        if duration and want_end > float(duration):
            fix = f"до конца ролика не хватит — начни раньше (start ≤ {max(0.0, float(duration) - (want_end - start)):.2f})"
        else:
            fix = f"продли: end ≥ {want_end:.2f}"
        fix += f" или сократи до {fits} сл." if 0 < fits < words else ""
        if cut:
            fix += " (часть титра съедает вырез — сдвинь титр с выреза)"
        out.append(_finding(
            "reading", "warn",
            f"{_what(layer)}: на экране {readable:.1f} с, а на {words} сл. нужно ≥ {need:.1f} с — исчезнет раньше, "
            f"чем прочитают ({len(text) / max(0.05, readable):.0f} знаков/с)" + (
                f"; кусок {index + 1} ускорен ×{speed:g}" if index is not None and speed > 1.01 else ""),
            fix, t=start, until=end, layer=lid, words=words, shown_sec=round(readable, 2), need_sec=round(need, 2)))
    return out


def page_reading_findings(pages: list[dict], label: str, clock: Any = None, duration: float | None = None,
                          layer: str | None = None) -> list[dict]:
    """Страницы субтитров: «мелькнула» (короче половины нормы), слишком быстрая (знаков/с) —
    по отдельности (до ``MAX_PAGE_FINDINGS`` худших), короче нормы — одной сводкой."""
    if not pages:
        return []
    rows = []
    for page in pages:
        words = len(page["words"])
        shown = _final_span(clock, page["t0"], page["t1"])
        if shown <= 0.02:
            continue  # страница целиком вырезана
        need = read_need(words)
        cps = len(page["text"]) / max(0.05, shown)
        rows.append((page, words, shown, need, cps))
    if not rows:
        return []
    bad = [r for r in rows if r[2] < FLASH_PAGE_SHARE * r[3] or r[4] > FAST_CPS]
    short = [r for r in rows if r[2] < r[3]]
    out = []
    clip_end = float(duration or 0.0)
    for page, words, shown, need, cps in sorted(bad, key=lambda r: r[2] / r[3])[:MAX_PAGE_FINDINGS]:
        index, speed = _piece_speed_note(clock, page["t0"])
        if clip_end and page["t1"] >= clip_end - 0.1:
            fix = "конец ролика обрывает страницу — продли последний кусок (trim_piece) на ≥ 0.5 с"
        elif shown < (page["t1"] - page["t0"]) - 0.05:
            fix = "страницу съедает вырез — сдвинь вырез (apply_cuts) за конец фразы"
        elif index is not None and speed > 1.01:
            fix = f"кусок {index + 1} ускорен ×{speed:g} — субтитры бегут: замедли (set_segment_speed) или сократи фразу"
        elif cps > FAST_CPS:
            fix = "быстрая речь — меньше слов на странице (maxWords 2) или оставь фразу целиком дольше паузой"
        else:
            fix = "короткая страница — объедини со следующей (maxWords +1) или растяни паузу после фразы"
        out.append(_finding(
            "reading", "warn",
            f"{label}: «{_short(page['text'], 40)}» видна {shown:.2f} с ({words} сл., {cps:.0f} знаков/с) — "
            f"нужно ≥ {need:.1f} с" + (f"; кусок {index + 1} ×{speed:g}" if index is not None and speed > 1.01 else ""),
            fix, t=page["t0"], until=page["t1"], layer=layer, words=words, shown_sec=round(shown, 2),
            need_sec=round(need, 2), cps=round(cps, 1)))
    rest = [r for r in short if r not in bad]
    if rest:
        # Сводка — подсказка: субтитры идут за голосом, норма чтения для них ориентир, а не закон
        # (страницы, что правда мелькают или бегут, — предупреждения выше).
        share = len(short) / len(rows)
        first = min(rest, key=lambda r: r[0]["t0"])[0]
        out.append(_finding(
            "reading", "info",
            f"{label}: ещё {len(rest)} из {len(rows)} страниц короче нормы чтения (0.3 с/слово + 0.5 с) — "
            "успевают только за голосом" + (", речь быстрая для текста" if share >= SHORT_PAGES_WARN_SHARE else ""),
            "речь не растянуть — сократи слов на странице (maxWords 2–3), убери паузы-паразиты, а важное вынеси "
            "титром на 1.5–2 с" if share >= SHORT_PAGES_WARN_SHARE else "",
            t=first["t0"], layer=layer, pages=len(rows), short_pages=len(short)))
    return out


# =============================================================== 3) вспышки


def _relative_luminance(rgb: Any) -> Any:
    import numpy as np

    lin = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return 0.2126 * lin[..., 0] + 0.7152 * lin[..., 1] + 0.0722 * lin[..., 2]


def _red_value(rgb: Any) -> Any:
    import numpy as np

    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    ratio = r / np.maximum(r + g + b, 1e-6)
    return np.where(ratio >= RED_RATIO, np.maximum(0.0, r - g - b) * 320.0, 0.0)


def frame_series(frames: Any) -> dict[str, dict[str, list[float]]]:
    """Кадры ``(n, S, S, 3)`` uint8 → средняя относительная яркость и «красное» по областям."""
    import numpy as np

    rgb = np.asarray(frames, dtype=np.float32) / 255.0
    lum, red = _relative_luminance(rgb), _red_value(rgb)
    out: dict[str, dict[str, list[float]]] = {"lum": {}, "red": {}}
    for name, (ys, xs) in _REGIONS.items():
        out["lum"][name] = lum[:, ys, xs].mean(axis=(1, 2)).tolist()
        out["red"][name] = red[:, ys, xs].mean(axis=(1, 2)).tolist()
    return out


def transitions(series: list[float], delta: float, dark_max: float | None = None) -> list[tuple[int, int]]:
    """Перепады ряда с гистерезисом ``delta``: [(кадр начала, кадр конца)]. Плавное движение
    меньше ``delta`` не считается; ``dark_max`` — тёмная сторона перепада ниже этого (иначе
    это перепад между двумя светлыми кадрами — не вспышка)."""
    if len(series) < 2:
        return []
    legs: list[tuple[int, int, float, float]] = []
    lo_i = hi_i = 0
    trend = 0
    piv_i = ext_i = 0
    for i in range(1, len(series)):
        v = series[i]
        if trend == 0:
            if v < series[lo_i]:
                lo_i = i
            if v > series[hi_i]:
                hi_i = i
            if series[hi_i] - series[lo_i] >= delta:
                if hi_i > lo_i:
                    trend, piv_i, ext_i = 1, lo_i, hi_i
                else:
                    trend, piv_i, ext_i = -1, hi_i, lo_i
            continue
        if (trend == 1 and v > series[ext_i]) or (trend == -1 and v < series[ext_i]):
            ext_i = i
        elif abs(series[ext_i] - v) >= delta:
            legs.append((piv_i, ext_i, series[piv_i], series[ext_i]))
            piv_i, ext_i, trend = ext_i, i, -trend
    if trend and abs(series[ext_i] - series[piv_i]) >= delta:
        legs.append((piv_i, ext_i, series[piv_i], series[ext_i]))
    return [(a, b) for a, b, va, vb in legs if dark_max is None or min(va, vb) < dark_max]


def flash_windows(legs: list[tuple[int, int]], fps: float, limit: int = MAX_FLASHES_PER_SEC) -> list[tuple[float, float, float]]:
    """Окна, где за секунду больше ``limit`` вспышек (вспышка — пара встречных перепадов):
    [(начало, конец, вспышек в секунду)], слитые."""
    ends = sorted(b for _a, b in legs)
    starts = {b: a for a, b in legs}
    need = 2 * limit + 1  # больше трёх вспышек — от семи перепадов за секунду
    window = max(1, int(round(fps)))
    out: list[list[float]] = []
    j = 0
    for i, end in enumerate(ends):
        while ends[j] <= end - window:
            j += 1
        count = i - j + 1
        if count >= need:
            a, b = starts.get(ends[j], ends[j]) / fps, end / fps
            rate = count / 2
            if out and a <= out[-1][1] + 1e-6:
                out[-1][1] = max(out[-1][1], b)
                out[-1][2] = max(out[-1][2], rate)
            else:
                out.append([a, b, rate])
    return [(round(a, 2), round(b, 2), round(r, 1)) for a, b, r in out]


def analyze_series(series: dict[str, dict[str, list[float]]], fps: float = FLASH_FPS) -> dict:
    """Ряды областей → {general: [...], red: [...], max_per_sec: {...}}: окна нарушений
    ``{start, end, per_sec, regions}`` (слиты по всем областям)."""
    result: dict[str, Any] = {"max_per_sec": {}}
    for kind, key, delta, dark in (("general", "lum", FLASH_LUM_DELTA, FLASH_DARK_MAX), ("red", "red", RED_DELTA, None)):
        spans: list[dict] = []
        peak = 0.0
        for region, values in series.get(key, {}).items():
            legs = transitions(values, delta, dark)
            if legs:
                per_sec = _peak_rate(legs, fps)
                peak = max(peak, per_sec)
            for a, b, rate in flash_windows(legs, fps):
                spans.append({"start": a, "end": b, "per_sec": rate, "regions": [region]})
        spans.sort(key=lambda s: s["start"])
        merged: list[dict] = []
        for span in spans:
            if merged and span["start"] <= merged[-1]["end"] + 0.05:
                last = merged[-1]
                last["end"] = max(last["end"], span["end"])
                last["per_sec"] = max(last["per_sec"], span["per_sec"])
                last["regions"] = sorted(set(last["regions"]) | set(span["regions"]))
            else:
                merged.append(dict(span))
        result[kind] = merged
        result["max_per_sec"][kind] = round(peak, 1)
    return result


def _peak_rate(legs: list[tuple[int, int]], fps: float) -> float:
    ends = sorted(b for _a, b in legs)
    window = max(1, int(round(fps)))
    best, j = 0, 0
    for i, end in enumerate(ends):
        while ends[j] <= end - window:
            j += 1
        best = max(best, i - j + 1)
    return best / 2


def read_frames(path: Path, fps: int = FLASH_FPS, size: int = FLASH_SIZE, timeout: int = FLASH_TIMEOUT_SEC):
    """Кадры файла по ``fps`` в секунду, ``size``×``size`` RGB — потоком, пачками (память не растёт
    с длиной ролика: в ряд идут только средние по областям)."""
    import numpy as np

    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-an", "-sn", "-vf",
           f"fps={fps},scale={size}:{size}:flags=area,format=rgb24", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    frame = size * size * 3
    buf = b""
    try:
        assert proc.stdout is not None
        while True:
            data = proc.stdout.read(frame * 150)
            if not data:
                break
            buf += data
            count = len(buf) // frame
            if count:
                yield np.frombuffer(buf[: count * frame], dtype=np.uint8).reshape(count, size, size, 3)
                buf = buf[count * frame:]
        proc.wait()
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
    if proc.returncode not in (0, None):
        raise RuntimeError(f"ffmpeg не прочитал {path.name} (код {proc.returncode})")


def analyze_flashes(path: Path, fps: int = FLASH_FPS) -> dict:
    """Вспышки файла: окна нарушений (секунды файла) и пик вспышек в секунду."""
    series: dict[str, dict[str, list[float]]] = {"lum": {k: [] for k in _REGIONS}, "red": {k: [] for k in _REGIONS}}
    frames = 0
    for chunk in read_frames(Path(path), fps):
        part = frame_series(chunk)
        frames += len(chunk)
        for key in ("lum", "red"):
            for region, values in part[key].items():
                series[key][region].extend(values)
    if not frames:
        raise RuntimeError(f"в {Path(path).name} нет кадров")
    out = analyze_series(series, fps)
    out["frames"] = frames
    out["seconds"] = round(frames / fps, 2)
    return out


def flash_findings(result: dict, clock: Any = None) -> list[dict]:
    out = []
    for kind, label in (("general", "вспышки яркости"), ("red", "насыщенные красные вспышки")):
        for span in result.get(kind) or []:
            t, until = _to_clip(clock, span["start"]), _to_clip(clock, span["end"])
            out.append(_finding(
                "flashes", "warn",
                f"{label} {span['per_sec']:g} в секунду на {span['start']:.1f}–{span['end']:.1f} с готового ролика "
                f"({', '.join(span['regions'])}) — больше {MAX_FLASHES_PER_SEC} в секунду опасно для "
                "фоточувствительных зрителей",
                "реже (≤ 3 в секунду: эффект flash/strobe, мигающий глитч, частые склейки чёрное/белое), меньше по "
                "площади (< ¼ кадра) или мягче по яркости" + ("; красный — замени цвет или убери мигание" if kind == "red" else ""),
                t=t, until=until, final_t=span["start"], final_until=span["end"], kind=kind,
                per_sec=span["per_sec"], regions=span["regions"]))
    return out


def _to_clip(clock: Any, final_t: float) -> float:
    if clock is None:
        return float(final_t)
    try:
        return clock.final_to_clip(min(float(final_t), clock.final_duration))
    except Exception:  # noqa: BLE001
        return float(final_t)


# =============================================================== 4) луп


def _run_bytes(args: list[str], timeout: int = 60) -> bytes:
    proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", *args], capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", "replace")[-300:] or "ffmpeg failed")
    return proc.stdout


def _gray(path: Path, last: bool) -> Any:
    import numpy as np

    size = LOOP_FRAME * LOOP_FRAME
    head = ["-sseof", "-0.6"] if last else []
    tail = [] if last else ["-frames:v", "1"]
    data = _run_bytes([*head, "-i", str(path), "-an", *tail, "-vf", f"scale={LOOP_FRAME}:{LOOP_FRAME},format=gray",
                       "-f", "rawvideo", "-pix_fmt", "gray", "-"])
    if len(data) < size:
        return None
    chunk = data[-size:] if last else data[:size]
    return np.frombuffer(chunk, dtype=np.uint8).astype(np.float32) / 255.0


def _level_db(path: Path, args: list[str]) -> float | None:
    """RMS дБ отрезка звука; нет звука — None."""
    import numpy as np

    try:
        data = _run_bytes([*args, "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"])
    except RuntimeError:
        return None
    if len(data) < 64:
        return None
    x = np.frombuffer(data[: len(data) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
    return round(20 * math.log10(max(rms, 1e-6)), 1)


def loop_metrics(path: Path) -> dict:
    """Стык конца с началом готового файла: разница первого и последнего кадра (0..1), уровни
    звука первых и последних ``LOOP_AUDIO_SEC`` и перед финальным затуханием рендера."""
    from app.render import END_AUDIO_FADE_SEC

    path = Path(path)
    first, last = _gray(path, last=False), _gray(path, last=True)
    mad = float(abs(first - last).mean()) if first is not None and last is not None else None
    sec = f"{LOOP_AUDIO_SEC:.2f}"
    head = _level_db(path, ["-i", str(path), "-t", sec])
    tail = _level_db(path, ["-sseof", f"-{sec}", "-i", str(path)])
    pre = _level_db(path, ["-sseof", f"-{LOOP_AUDIO_SEC + END_AUDIO_FADE_SEC + 0.1:.2f}", "-i", str(path), "-t", sec])
    return {"frame_diff": None if mad is None else round(mad, 3), "head_db": head, "tail_db": tail, "pre_fade_db": pre}


def loop_score(metrics: dict) -> dict:
    """Оценка лупа 0..1: картинка (вес 0.6) и звук (0.4); звука нет — только картинка."""
    mad = metrics.get("frame_diff")
    visual = None if mad is None else max(0.0, min(1.0, 1 - float(mad) / LOOP_MAD_SCALE))
    head, tail = metrics.get("head_db"), metrics.get("pre_fade_db")
    if tail is None:
        tail = metrics.get("tail_db")
    audio = None
    if head is not None and tail is not None:
        if head < LOOP_SILENT_DB and tail < LOOP_SILENT_DB:
            audio = 1.0
        else:
            audio = max(0.0, min(1.0, 1 - abs(float(tail) - float(head)) / LOOP_DB_SCALE))
    parts = [(v, w) for v, w in ((visual, LOOP_VISUAL_WEIGHT), (audio, 1 - LOOP_VISUAL_WEIGHT)) if v is not None]
    score = sum(v * w for v, w in parts) / sum(w for _v, w in parts) if parts else None
    return {"score": None if score is None else round(score, 2), "visual": None if visual is None else round(visual, 2),
            "audio": None if audio is None else round(audio, 2)}


def loop_findings(metrics: dict | None, hints: list[dict], render_id: int | None = None) -> tuple[list[dict], dict]:
    """Находка-сводка лупа (оценка и подсказки) и подсказки по настройкам клипа."""
    out = list(hints)
    info: dict[str, Any] = {}
    if metrics:
        score = loop_score(metrics)
        info = {**metrics, **score, "render_id": render_id}
        tips = []
        mad = metrics.get("frame_diff")
        if mad is not None and score["visual"] is not None and score["visual"] < 0.6:
            tips.append(f"последний кадр не похож на первый (разница {mad:.0%}): подрежь хвост (trim_piece / "
                        "apply_cuts) до кадра, похожего на начало, или начни ролик с того же плана")
        head, tail, pre = metrics.get("head_db"), metrics.get("tail_db"), metrics.get("pre_fade_db")
        level = pre if pre is not None else tail
        if head is not None and level is not None and level < LOOP_SILENT_DB and head >= LOOP_SILENT_DB + 10:
            tips.append(f"в конце тишина ({level:g} дБ), а начало громкое ({head:g} дБ) — на повторе провал: "
                        "подрежь тихий хвост")
        elif head is not None and level is not None and abs(level - head) > 12:
            tips.append(f"громкость на стыке прыгает на {abs(level - head):.0f} дБ (конец {level:g}, начало {head:g}) — "
                        "подрежь так, чтобы конец и начало были на одной фразе/звуке")
        if score["score"] is not None:
            good = score["score"] >= 0.75
            out.insert(0, _finding(
                "loop", "info",
                f"луп {score['score']:.2f} из 1 (картинка {score['visual'] if score['visual'] is not None else '—'}, "
                f"звук {score['audio'] if score['audio'] is not None else '—'})" + (
                    " — конец хорошо стыкуется с началом" if good and not tips else ""),
                "; ".join(tips), score=score["score"]))
    return out, info


def loop_plan_hints(rs: dict, clock: Any = None) -> list[dict]:
    """Что в настройках клипа ломает луп (без медиа)."""
    from app.settings import settings

    out = []
    hold = rs.get("end_hold_sec")
    hold = float(settings.render_end_hold_sec or 0) if hold is None else float(hold)
    end = clock.clip_duration if clock is not None else None
    if hold > 0 and settings.render_loudnorm:
        out.append(_finding("loop", "info", f"в конце стоп-кадр {hold:g} с — на повторе ролик «замирает» перед началом",
                            "для лупа (мем, Shorts) — set_end_hold(sec: 0)", t=end, end_hold_sec=hold))
    if rs.get("end_screen"):
        out.append(_finding("loop", "info", "концовка-баннер с размытием на хвосте — луп не получится",
                            "ролику под повторы — set_end_screen(remove: true); баннер — роликам с призывом",
                            t=end))
    return out


# =============================================================== всё вместе


def _ass_block(store: Any, rs: dict, words: list[dict]) -> dict | None:
    """Прожигаемые субтитры клипа: стиль (как frame_subtitles_ass) → страницы и место."""
    if not rs.get("subs_on") or not words:
        return None
    from app.clip_settings import settings_to_render_kwargs
    from app.clip_subtitles import _default_profile

    try:
        default_id = int(store.get_app_setting_value("default_subtitle_profile_id") or 0) or None
    except (TypeError, ValueError, AttributeError):
        default_id = None
    kwargs = settings_to_render_kwargs(rs, default_id)
    try:
        profile = (store.get_subtitle_profile(int(kwargs["subtitle_profile_id"])) if kwargs.get("subtitle_profile_id")
                   else _default_profile(store))
    except (KeyError, TypeError, ValueError):
        return None
    pages = ass_pages(words, profile or {})
    return {"pages": pages, "bottom_pct": _num(rs.get("sub_pos_pct"), 12.0),
            "align": int(_num((profile or {}).get("alignment"), 2) or 2)}


def _latest_render_file(store: Any, clip_plan_id: int) -> tuple[dict | None, Path | None]:
    for clip in store.list_clips_for_plan(int(clip_plan_id)):
        if clip.get("status") == "succeeded":
            path = Path(clip.get("output_path") or "")
            return clip, (path if clip.get("output_path") and path.is_file() else None)
    return None, None


def plan_fingerprint(store: Any, clip_plan_id: int) -> str | None:
    """Отпечаток того, что попадает в рендер: клип-файл (куски, монтаж, слои) и субтитры — без
    названия, описания и заметок (их правит автоописание после рендера). Тот же клип — тот же
    отпечаток; правка слоя, выреза или слова — другой."""
    import hashlib
    import json

    try:
        from app.clip_history import capture

        state = capture(store, int(clip_plan_id))
    except Exception:  # noqa: BLE001 - не сняли — проверка по снимкам истории
        return None
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in ("title", "description", "notes", "favorite")}
    raw = json.dumps({"spec": spec, "subtitles": state.get("subtitles")}, ensure_ascii=False, sort_keys=True,
                     default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def render_is_stale(store: Any, clip_plan_id: int, clip: dict) -> bool:
    """Клип правили после этого рендера? По отпечатку рендера (``clips.plan_fingerprint``), а у старых
    рендеров без него — по снимку истории правок новее конца рендера. Статус клипа («rendered»,
    который рендер ставит в конце) правкой не считается — раньше из-за него свежий рендер
    сразу был «устаревшим»."""
    stamp = clip.get("plan_fingerprint")
    if stamp:
        current = plan_fingerprint(store, clip_plan_id)
        return bool(current) and current != stamp
    try:
        edited = store.last_clip_plan_history_at(int(clip_plan_id))
    except Exception:  # noqa: BLE001
        return False
    done = str(clip.get("finished_at") or clip.get("created_at") or "")
    return bool(edited and done and edited > done)


def _project_platforms(store: Any, clip_plan_id: int) -> list[str]:
    try:
        from app import autopilot as A
        from app import projects as P

        project = P.project_of_plan(store, int(clip_plan_id))
        return list(A.get_config(store, project)["platforms"]) if project is not None else []
    except Exception:  # noqa: BLE001 - без проекта — все площадки
        return []


def _media_flag(media: Any) -> str:
    if media in (None, "", "auto"):
        return "auto"
    if isinstance(media, str):
        return "yes" if media.strip().lower() in ("1", "true", "yes", "on", "да") else "no"
    return "yes" if media else "no"


def lint_clip(store: Any, clip_plan_id: int, checks: Any = None, platforms: Any = None, media: Any = "auto") -> dict:
    """Все проверки клипа. ``checks`` — подмножество ``CHECKS``; ``platforms`` — tiktok/reels/shorts
    (youtube/instagram; пусто — площадки проекта, нет — все три); ``media`` — auto (по готовому
    рендеру, если он есть) | true | false. Ничего не меняет."""
    from app.agent_tools import plan_clock
    from app.clip_settings import normalize_render_settings
    from app.formats import settings_layers_format, settings_render_format

    wanted = normalize_checks(checks)
    plan = store.get_clip_plan(int(clip_plan_id))
    rs = normalize_render_settings(plan.get("render_settings") or {})
    clock = plan_clock(store, plan)
    duration = clock.clip_duration
    layers = [layer for layer in rs.get("layers") or [] if not layer.get("hidden") and layer.get("type") != "audio"]
    camera = rs.get("camera")
    fmt, layers_fmt = settings_render_format(rs), settings_layers_format(rs)
    used_platforms = platforms if platforms not in (None, "", []) else _project_platforms(store, clip_plan_id)
    from app.safe_zones import platform_ui

    uis = platform_ui(used_platforms)
    findings: list[dict] = []
    skipped: dict[str, str] = {}
    out: dict[str, Any] = {"ok": True, "clip_id": int(clip_plan_id), "format": fmt, "duration": duration,
                           "final_duration": clock.final_duration, "platforms": list(uis)}

    words: list[dict] = []
    if "ui_zones" in wanted or "reading" in wanted:
        try:
            from app.motion_render import plan_caption_words

            words = plan_caption_words(store, plan)
        except Exception:  # noqa: BLE001 - без субтитров — только титры
            words = []
    caption_layers = [layer for layer in layers if layer.get("type") == "captions"]
    pages_by_layer = {str(layer.get("id")): caption_layer_pages(words, layer.get("props"), _num(layer.get("start")),
                                                                min(_num(layer.get("end"), duration), duration or math.inf))
                      for layer in caption_layers}
    try:
        ass = _ass_block(store, rs, words) if words else None
    except Exception:  # noqa: BLE001 - стиль не прочитать — без прожигаемых
        ass = None

    if "ui_zones" in wanted:
        if fmt != "9:16" or layers_fmt != "9:16":
            skipped["ui_zones"] = (f"формат {fmt}: колонка кнопок и подпись TikTok/Reels/Shorts — только у вертикали 9:16 "
                                   "(края 1:1/4:5/16:9 проверяет add_layers)")
        else:
            findings += ui_zone_findings(layers, list(uis), camera, pages_by_layer, ass)

    if "reading" in wanted:
        findings += title_reading_findings(layers, clock, duration)
        for layer in caption_layers:
            findings += page_reading_findings(pages_by_layer.get(str(layer.get("id"))) or [], "субтитры", clock,
                                              duration, layer=str(layer.get("id")))
        if ass:
            findings += page_reading_findings(ass["pages"], "прожигаемые субтитры", clock, duration)

    mode = _media_flag(media)
    clip, path = (None, None)
    if mode != "no" and ("flashes" in wanted or "loop" in wanted):
        clip, path = _latest_render_file(store, clip_plan_id)
        if clip is not None:
            out["render_id"] = int(clip["id"])
            if render_is_stale(store, int(clip_plan_id), clip):
                out["render_stale"] = ("клип правили после этого рендера — вспышки и луп проверены по старому файлу; "
                                       "перерендерь и проверь снова")
    no_media = ("проверка медиа выключена (media: false)" if mode == "no" else
                "нет готового рендера — render, потом lint_clip" if clip is None else "файла рендера нет на диске")

    if "flashes" in wanted:
        if path is None:
            skipped["flashes"] = no_media
        else:
            try:
                result = analyze_flashes(path)
                findings += flash_findings(result, clock)
                out["flashes"] = {"max_per_sec": result["max_per_sec"], "seconds": result["seconds"],
                                  "limit_per_sec": MAX_FLASHES_PER_SEC}
            except Exception as exc:  # noqa: BLE001 - не прочитали файл — скажем, не упадём
                skipped["flashes"] = f"не удалось разобрать рендер: {str(exc)[:200]}"

    if "loop" in wanted:
        metrics = None
        if path is not None:
            try:
                metrics = loop_metrics(path)
            except Exception as exc:  # noqa: BLE001
                skipped["loop"] = f"оценка лупа не удалась: {str(exc)[:200]} — только подсказки по настройкам"
        elif mode != "no":
            skipped["loop"] = f"{no_media}: оценки лупа нет, только подсказки по настройкам"
        loop_items, loop_info = loop_findings(metrics, loop_plan_hints(rs, clock), out.get("render_id"))
        findings += loop_items
        if loop_info:
            out["loop"] = loop_info

    for item in findings:
        if "t" in item and "final_t" not in item:
            try:
                item["final_t"] = round(clock.clip_to_final(item["t"]), 2)
            except Exception:  # noqa: BLE001
                pass
    order = {"warn": 0, "info": 1}
    findings.sort(key=lambda f: (order.get(f["severity"], 2), CHECKS.index(f["check"]), f.get("t", 0.0)))
    counts = {"warn": sum(f["severity"] == "warn" for f in findings), "info": sum(f["severity"] == "info" for f in findings)}
    by_check = {c: sum(f["check"] == c for f in findings) for c in wanted}
    out.update(checks=wanted, findings=findings, counts=counts, by_check=by_check, skipped=skipped,
               summary=summary_text(counts, by_check, skipped))
    return out


def summary_text(counts: dict, by_check: dict, skipped: dict) -> str:
    labels = {"ui_zones": "зоны площадок", "reading": "темп чтения", "flashes": "вспышки", "loop": "луп"}
    if not counts.get("warn") and not counts.get("info"):
        head = "замечаний нет"
    else:
        head = f"предупреждений {counts.get('warn', 0)}, подсказок {counts.get('info', 0)}"
        parts = [f"{labels[c]} {n}" for c, n in by_check.items() if n]
        if parts:
            head += " (" + ", ".join(parts) + ")"
    if skipped:
        head += "; не проверено: " + ", ".join(labels.get(c, c) for c in skipped)
    return head


def brief(result: dict, limit: int = 8) -> dict:
    """Коротко для карточки одобрения и ответа submit_for_approval: счётчики и главные предупреждения."""
    warns = [f for f in result.get("findings") or [] if f.get("severity") == "warn"]
    return {"counts": result.get("counts") or {}, "summary": result.get("summary") or "",
            "warnings": [f["message"] + (f" → {f['fix']}" if f.get("fix") else "") for f in warns[:limit]]}
