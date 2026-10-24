"""Автомонтаж без ИИ: титр-хук, анимированные субтитры, наезды и звуки по речи.

ИИ-монтажёр живёт на Gemini, а Gemini часто недоступен (геоблок без VPN). Базовую
«динамику» шортса можно собрать и без модели — по словам клипа и громкости:

* хук — название клипа крупно в первые ~3 с (+ whoosh);
* субтитры-слой по словам, ключевые слова цветом (без эмодзи — владелец против);
* наезды камеры на ударные слова (громкие и перед паузой), не чаще раза в ~5 с,
  на самый сильный — тряска и удар;
* без эмодзи и без «попов» на них.

Правило «не перегружать»: на экране максимум пара элементов, смысловое слово
цветом не чаще раза в 2 с. Всё ставится слоями — дальше руками в студии или ИИ, если он есть.
"""

from __future__ import annotations

import re
from typing import Any

from app.motion import normalize_camera, normalize_layers

# Корни смысловых слов (бывший словарь эмодзи): теперь по ним слово красится в
# субтитрах — сами эмодзи в монтаж не ставим.
EMOJI_STEMS: list[tuple[str, str]] = [
    ("деньг", "💸"), ("денег", "💸"), ("бабк", "💸"), ("рубл", "💸"), ("доллар", "💵"), ("ипотек", "🏠"), ("квартир", "🏠"),
    ("смешн", "😂"), ("ржу", "😂"), ("ахах", "😂"), ("хаха", "😂"), ("угар", "😂"),
    ("страш", "😱"), ("испуг", "😱"), ("напуг", "😱"), ("боюсь", "😱"), ("скример", "😱"), ("жуть", "😱"),
    ("сердц", "💔"), ("люблю", "❤️"), ("любов", "❤️"), ("краш", "😍"),
    ("умер", "💀"), ("смерт", "💀"), ("убил", "💀"), ("пиздец", "💀"), ("капец", "💀"),
    ("огон", "🔥"), ("горит", "🔥"), ("жёстк", "🔥"), ("жестк", "🔥"), ("имба", "🔥"),
    ("плач", "😭"), ("груст", "😭"), ("обид", "😭"),
    ("злю", "😤"), ("бесит", "😤"), ("охуе", "🤯"), ("шок", "🤯"), ("взрыв", "💥"), ("бум", "💥"),
    ("кот", "🐱"), ("кошк", "🐱"), ("собак", "🐶"), ("муха", "🪰"), ("мух", "🪰"), ("таракан", "🪳"),
    ("еда", "🍔"), ("ед", "🍔"), ("пицц", "🍕"), ("майонез", "🥫"), ("хлеб", "🍞"),
    ("игр", "🎮"), ("стрим", "🎥"), ("музык", "🎵"), ("песн", "🎤"), ("рэп", "🎤"), ("бит", "🎧"),
    ("клоун", "🤡"), ("чухн", "🤡"), ("тупо", "🤡"),
    ("спать", "😴"), ("работ", "💼"), ("школ", "🏫"), ("врач", "🩺"), ("психиатр", "🧠"), ("мозг", "🧠"),
]
# Слова, которые подсвечиваются цветом (усилители и мат — то, на что зритель реагирует).
STRONG_STEMS = ("бляд", "пизд", "хуй", "хуё", "ебат", "ебал", "нахуй", "никогда", "всегда", "самый", "миллион", "реально", "серьёзн")

HOOK_Y = 0.13
# Стили субтитров (frontend/src/motion/layers/captions.ts), где звучащее слово жёлтое;
# неизвестный стиль компоновщик рисует как hormozi.
YELLOW_ACTIVE_STYLES = {"hormozi", "mrbeast", "karaoke"}
KNOWN_STYLES = YELLOW_ACTIVE_STYLES | {"box", "neon", "minimal", "typewriter", "comic", "gradient", "outline"}
STRONG_COLOR_ON_YELLOW = "#ff3b30"
KEYWORD_GAP_SEC = 2.0
PUNCH_EVERY_SEC = 6.5

_PUNCT = re.compile(r"[^\w\-]+", re.UNICODE)


def _clean(word: str) -> str:
    return _PUNCT.sub("", word).lower()


def _emoji_for(word: str) -> str | None:
    w = _clean(word)
    if len(w) < 3:
        return None
    for stem, emoji in EMOJI_STEMS:
        if w.startswith(stem):
            return emoji
    return None


def _loudness(peaks: list[float], duration: float, start: float, end: float) -> float:
    if not peaks or duration <= 0:
        return 0.5
    i0 = max(0, int(start / duration * len(peaks)))
    i1 = min(len(peaks), int(end / duration * len(peaks)) + 1)
    return max(peaks[i0:i1] or [0.0])


def punch_moments(words: list[dict], peaks: list[float], duration: float) -> list[tuple[float, float, float]]:
    """Ударные слова: громкие, перед паузой, с «!» — (время, длина слова, сила)."""
    scored = []
    for i, w in enumerate(words):
        gap = (words[i + 1]["start"] if i + 1 < len(words) else duration) - w["end"]
        clean = _clean(w["word"])
        score = _loudness(peaks, duration, w["start"], w["end"]) + min(0.6, max(0.0, gap)) * 0.8
        if "!" in w["word"] or "?" in w["word"]:
            score += 0.25
        if any(clean.startswith(s) for s in STRONG_STEMS):
            score += 0.3
        if len(clean) >= 5:
            score += 0.1
        scored.append((score, w))
    picked: list[tuple[float, dict]] = []
    budget = max(1, round(duration / PUNCH_EVERY_SEC))
    for score, w in sorted(scored, key=lambda x: -x[0]):
        if len(picked) >= budget:
            break
        if w["start"] < 3.0 or w["end"] > duration - 0.8:
            continue
        if any(abs(p[1]["start"] - w["start"]) < PUNCH_EVERY_SEC - 1 for p in picked):
            continue
        picked.append((score, w))
    picked.sort(key=lambda p: p[1]["start"])
    return [(p[1]["start"], p[1]["end"] - p[1]["start"], p[0]) for p in picked]


def caption_y_for(settings: dict | None) -> float:
    """Где ставить субтитры, чтобы не закрыть лицо.

    В раскладке «видео сверху, вебка снизу» лицо стримера — в нижней части кадра,
    и обычные 0.72 легли бы ему на лицо: ставим субтитры на шов, чуть выше вебки.
    """
    layout = (settings or {}).get("layout") or {}
    from app.formats import settings_layers_format

    fmt = settings_layers_format(settings)
    if fmt == "16:9":
        # Слои размечены в горизонтали: субтитры внизу, как у YouTube, — над нижним полем зоны
        # (app/safe_zones.py); полосы вебки в 16:9 нет (окошко в углу).
        from app.safe_zones import safe_zone

        return round(safe_zone(fmt)["captions_bottom"] - 0.12, 3)
    if layout.get("mode") == "split" and layout.get("cam_pos", "top") == "bottom":
        ratio = float(layout.get("ratio") or 0.38)
        return round(max(0.3, 1.0 - ratio - 0.05), 3)
    return 0.72


def hook_size(text: str) -> int:
    """Кегль хука по длине: до двух строк в ширине 980 px."""
    n = len(text)
    return 96 if n <= 18 else 84 if n <= 26 else 72 if n <= 36 else 64


def build_auto_edit(
    title: str,
    words: list[dict],
    peaks: list[float],
    duration: float,
    *,
    style: str = "hormozi",
    caption_y: float = 0.72,
    anchor: dict | None = None,
    hook_top: float | None = None,
) -> dict:
    """Слои и камера автомонтажа. ``words`` — [{word, start, end}] во времени клипа.

    ``anchor`` — точка, которая при наезде стоит на месте (``subject_box.zoom_anchor``):
    герой растёт в сторону от текста, а не под него. ``hook_top`` — верх зоны текста (в split
    с вебкой сверху — под полосой вебки, ``agent_tools.text_band``)."""
    from app.ai.subject_box import camera_center

    from app.agent_tools import safe_text_y

    layers: list[dict] = []
    hook = (title or "").strip().upper()[:60]
    if hook:
        # Длинный заголовок мельче: в 96 px он ложится в три строки и упирается в верх кадра.
        hook_props = {"text": hook, "font": "Montserrat", "weight": 900, "size": hook_size(hook),
                      "color": "#ffd600", "uppercase": True, "stroke": {"color": "#000000", "width": 12}, "maxWidth": 980}
        layers.append({
            "id": "auto_hook", "type": "text", "name": "Хук", "start": 0.0, "end": min(3.0, duration), "track": 3,
            # Две строки на 0.13 вылезают верхом за безопасную зону 0.10 — центр ниже на высоту текста.
            "x": 0.5, "y": safe_text_y(hook_props, HOOK_Y, **({"top": hook_top} if hook_top else {})),
            "in": {"type": "pop", "duration": 0.35},
            "out": {"type": "fade", "duration": 0.2}, "props": hook_props,
        })
        layers.append({"id": "auto_whoosh", "type": "audio", "start": 0.0, "end": min(0.75, duration),
                       "props": {"asset_id": "sfx:freesound/whoosh-1.ogg", "volume": 0.4}})

    # Эмодзи в монтаже владелец не хочет («хуёво выглядят»): акцент — цвет слова.
    # Сильные слова — жёлтым, смысловые (деньги, страх, еда…) — зелёным, не чаще раза в 2 с.
    # В hormozi/mrbeast/karaoke звучащее слово и так жёлтое — жёлтая подсветка на нём не видна
    # (агент-монтажёр: «highlightWords другим цветом незаметны»), там сильные слова — красным.
    strong = STRONG_COLOR_ON_YELLOW if style in YELLOW_ACTIVE_STYLES or style not in KNOWN_STYLES else "#ffd600"
    highlights, last_key = [], -99.0
    for i, w in enumerate(words):
        clean = _clean(w["word"])
        if any(clean.startswith(s) for s in STRONG_STEMS):
            highlights.append({"i": i, "color": strong})
        elif _emoji_for(w["word"]) and w["start"] - last_key >= KEYWORD_GAP_SEC:
            highlights.append({"i": i, "color": "#22c55e"})
            last_key = w["start"]
    if words:
        voices = sorted({w.get("speaker") for w in words if w.get("speaker") and w.get("speaker") != "A"})
        caps = {"style": style, "maxWords": 3, "highlightWords": highlights[:60]}
        if voices:
            caps["speakers"] = ["A"]
        layers.append({
            "id": "auto_caps", "type": "captions", "name": "Субтитры", "start": 0.0, "end": duration, "track": 0,
            "x": 0.5, "y": caption_y, "props": caps,
        })
        if voices:
            # Другие голоса (гость, донат, ролик) — своим слоем, мельче и рядом с главными:
            # одновременная речь не мешается в одну строку и не перебивает стримера.
            # Две строки главных субтитров — ~0.1 кадра: при 0.1 строки голосов ложились на
            # них («МНЕ НУЖНО» поверх «ЧЁРТ ВОЗЬМИ» в проверочном прогоне Gemini).
            other_y = caption_y - 0.15 if caption_y < 0.6 else caption_y + 0.11
            layers.append({
                "id": "auto_caps_voices", "type": "captions", "name": "Субтитры: другие голоса", "start": 0.0,
                "end": duration, "track": 0, "x": 0.5, "y": round(min(0.8, max(0.2, other_y)), 3),
                "props": {"style": "hormozi", "size": 60, "maxWords": 4, "speakers": voices,
                          "stroke": {"color": "#000000", "width": 7}, "wordScale": 1.05},
            })

    camera: list[dict] = []
    moments = punch_moments(words, peaks, duration)
    strongest = max((m[2] for m in moments), default=0)
    for t, length, score in moments:
        big = score >= strongest - 1e-6
        zoom = 1.3 if big else 1.18
        hold = min(1.3, max(0.6, length + 0.5))
        t0 = max(0.0, t - 0.06)
        at = camera_center(anchor, zoom)
        camera += [
            {"t": round(t0, 3), "zoom": 1.0, "ease": "outCubic"},
            {"t": round(t0 + 0.14, 3), "zoom": zoom, **at, "shake": 0.6 if big else 0.0, "ease": "linear"},
            {"t": round(t0 + 0.14 + hold, 3), "zoom": zoom, **at, "shake": 0.0, "ease": "inOutCubic"},
            {"t": round(min(duration, t0 + 0.44 + hold), 3), "zoom": 1.0},
        ]
        if big:
            layers.append({"id": "auto_boom", "type": "audio", "start": round(t0, 2), "end": round(min(duration, t0 + 1.2), 2),
                           "props": {"asset_id": "sfx:freesound/drama-boom.ogg", "volume": 0.4}})
    return {"layers": normalize_layers(layers), "camera": normalize_camera(camera)}


def apply_auto_edit(store: Any, clip_plan_id: int, *, style: str = "hormozi", keep_layers: bool = False) -> dict:
    """Собрать автомонтаж и записать в настройки клипа (снимок для Ctrl+Z — заранее)."""
    from app.clip_history import snapshot
    from app.clip_waveform import clip_waveform

    plan = store.get_clip_plan(clip_plan_id)
    subs = store.get_clip_plan_subtitles(clip_plan_id) or {}
    if not [w for w in subs.get("words") or [] if str(w.get("word", "")).strip()]:
        raise ValueError("у клипа нет распознанной речи — сначала «Распознать речь»")
    from app.settings import settings as app_settings

    if app_settings.gemini_api_key:
        try:
            # Разные голоса (гость, донат, ролик) — разным цветом субтитров (кэш по словам).
            from app.ai.speakers import label_speakers

            label_speakers(store, clip_plan_id)
            subs = store.get_clip_plan_subtitles(clip_plan_id) or subs
        except Exception:  # noqa: BLE001 - без разметки голосов субтитры всё равно будут
            pass
    # Слова сохранены во времени кусков подряд, а слои и камера — во времени
    # готового клипа (после переходов): переводим той же функцией, что и
    # слой «субтитры», иначе наезды и звуки встают позже речи.
    from app.cuts import subtitles_to_final
    from app.motion import clip_timeline

    segments = plan.get("segments") or []
    transition = (plan.get("render_settings") or {}).get("transition")
    final = subtitles_to_final({"words": subs.get("words") or []}, segments, transition)
    words = [w for w in final.get("words") or [] if str(w.get("word", "")).strip()]
    _starts, duration = clip_timeline([(float(s["start_sec"]), float(s["end_sec"])) for s in segments], transition)
    try:
        peaks = clip_waveform(store, clip_plan_id).get("peaks") or []
    except Exception:  # noqa: BLE001 - без волны наезды встанут только по паузам
        peaks = []
    caption_y = caption_y_for(plan.get("render_settings"))
    anchor = None
    try:
        # Где герой на кадре (Gemini-зрение): субтитры — в свободную зону, не на персонажа.
        from app.ai.subject_box import subject_box, text_positions, zoom_anchor

        box = subject_box(store, clip_plan_id)
        if box:
            caption_y = text_positions(box, caption_y)["captions_y"]
            anchor = zoom_anchor(box, caption_y)
    except Exception:  # noqa: BLE001 - без зрения — место по раскладке
        pass
    from app.agent_tools import text_band

    result = build_auto_edit(
        plan.get("title") or "", words, peaks, duration, style=style, caption_y=caption_y, anchor=anchor,
        hook_top=text_band(plan.get("render_settings"))[0],
    )
    settings = dict(plan.get("render_settings") or {})
    old_layers = settings.get("layers") or []
    if keep_layers:
        kept = [l for l in old_layers if not str(l.get("id", "")).startswith("auto_")]
        result["layers"] = kept + result["layers"]
    snapshot(store, clip_plan_id, "автомонтаж")
    settings["layers"] = result["layers"]
    settings["camera"] = result["camera"]
    # Субтитры-слой заменяет обычные — выключаем их, чтобы не было двух подписей.
    settings["subs_on"] = False
    store.set_clip_plan_render_settings(clip_plan_id, settings)
    return {"layers": len(result["layers"]), "camera": len(result["camera"]), "duration": round(duration, 2)}
