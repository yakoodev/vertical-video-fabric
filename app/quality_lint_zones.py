"""Линт качества: зоны интерфейса площадок — страницы титров, коробки слоёв (из ``app/quality_lint.py``)."""

from __future__ import annotations

import math
import re
from typing import Any

from app.quality_lint_base import _num, _short, _finding

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
    ``[{t0, t1, words, idx, text, lines, width}]`` на шкале clip. ``words`` — comp.words ``{t0, t1, w, s?}``, ``idx`` — номера их."""
    look = caption_look(props)
    size = look["size"]
    factor = _char_factor(bool(look["uppercase"]), _num(look["weight"], 900))
    spacing = _num(look["letterSpacing"], 0.0)
    space = size * 0.3
    max_w = look["maxWidth"] / max(1.0, look["wordScale"] * 0.97)
    items = []
    for index, wd in enumerate(words or []):
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
        items.append({"i": index, "text": text, "raw": str(wd.get("w") or ""), "t0": t0, "t1": t1, "s": who,
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
                      "idx": [x["i"] for x in g], "text": " ".join(x["text"] for x in g), "lines": len(lines),
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


ZONE_TYPES = ("text", "counter", "callout", "nameplate", "social", "emoji", "image")


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
    elif kind == "social":
        from app.aspect import layer_size

        w, h = layer_size(layer) or (900.0, 300.0)
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
    if kind == "social":
        return f"карточка «{_short(props.get('text') or props.get('name'))}»"
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
