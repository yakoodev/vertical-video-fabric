"""Подписи стрим-шортса (findings.md п. 54, 55): цвет по говорящему (стример жёлтый), выделение слов-эмоций,
ВСЕ ЗАГЛАВНЫЕ жирным с тенью, плавное появление фразы, медленный рост слоя 1,00→1,05.

Основа — существующий стиль ``hormozi`` слоя ``captions`` (моушн: превью == рендер). Новое в движке —
только ``pageFade`` (появление фразы с нуля); остальное — поля, которые слой уже умеет: ``speakerColors``
(голоса из инструмента «Голоса»: стример — главный голос A, его цвет — ``color`` слоя), эмфаза слова
(``words[i].emph``, app/word_emphasis.py), ``shadow`` и ключи ``scale``.

``check_stream_captions`` — автопроверка слоя и слов (с негативными контролями в тестах).
"""

from __future__ import annotations

import re
from typing import Any, Callable

STREAMER_YELLOW = "#FFD400"
OTHER_VOICES = [{"s": "B", "color": "#FFFFFF"}, {"s": "C", "color": "#7DD3FC"}, {"s": "D", "color": "#86EFAC"}]
EMPH_COLOR = "#FF3B3B"
PAGE_FADE = 0.12
GROW = (1.0, 1.05)
SHADOW = {"color": "#000000", "blur": 18, "x": 0, "y": 6, "opacity": 0.75}
LOUD_POP_DB = 6.0     # слово громче медианы речи на столько — эмфаза pop
LOUD_SHAKE_DB = 10.0  # на столько — shake
EMOTION_WORDS = frozenset("""
ааа аааа ааааа ахах ахаха ахахах хаха хахаха жесть капец пипец офигеть охренеть обалдеть ужас кошмар вау
неет нееет стоп бежим беги помогите мама господи боже блин ого красава
изи лол кринж треш имба рип сдох умер помер ахренеть
wow omg nooo wtf run help stop yesss lol lmao insane crazy dead rip bruh holy damn
""".split())


def _norm(word: str) -> str:
    return re.sub(r"[^\w?!]", "", (word or "").lower().replace("ё", "е")).strip("?!") or (word or "").lower()


def is_emotion_word(word: str) -> bool:
    w = _norm(word)
    return w in EMOTION_WORDS or bool(re.fullmatch(r"(?:а+|о+|[ах]{4,}|(?:ха){2,}|(?:ha){2,}|no{3,})", w))


def mark_emphasis(words: list[dict], loud_db: Callable[[float, float], float | None] | None = None) -> list[dict]:
    """Эмфаза слов-эмоций: слово из списка — pop (1); громкое (``loud_db(start, end)`` — дБ над медианой речи) —
    pop от +6, shake (2) от +10. Остальные слова не трогает (ручная эмфаза сохраняется)."""
    out = []
    for w in words or []:
        w = dict(w)
        level = 1 if is_emotion_word(str(w.get("word") or w.get("text") or "")) else 0
        if loud_db is not None and w.get("start") is not None and w.get("end") is not None:
            over = loud_db(float(w["start"]), float(w["end"]))
            if over is not None:
                level = max(level, 2 if over >= LOUD_SHAKE_DB else 1 if over >= LOUD_POP_DB else 0)
        if level and int(w.get("emph") or 0) < level:
            w["emph"] = level
        out.append(w)
    return out


def loudness_lookup(feat: dict, speech_spans: list[tuple[float, float]]) -> Callable[[float, float], float | None]:
    """По признакам ``audio_map.frame_features``: дБ слова над медианой громкости речи (LUFS-подобной)."""
    import numpy as np

    lufs, hop = np.asarray(feat["lufs"]), float(feat["hop"])
    mask = np.zeros(len(lufs), bool)
    for a, b in speech_spans or []:
        mask[int(a / hop): int(b / hop) + 1] = True
    ref = float(np.median(lufs[mask])) if mask.any() else float(np.median(lufs)) if len(lufs) else 0.0

    def over(a: float, b: float) -> float | None:
        part = lufs[int(a / hop): max(int(a / hop) + 1, int(b / hop))]
        return float(np.max(part)) - ref if len(part) else None

    return over


def stream_caption_layer(duration: float, y: float, *, layer_id: str = "stream_caps", style: str = "hormozi") -> dict:
    """Слой ``captions`` стрим-шортса: капс жирный с тенью, стример жёлтый, другие голоса своими цветами,
    эмфаза красным, фраза проявляется за ``PAGE_FADE``, слой растёт 1,00→1,05 за ролик."""
    end = round(max(0.1, float(duration)), 3)
    return {
        "id": layer_id, "type": "captions", "name": "Подписи стрима", "start": 0.0, "end": end, "track": 0,
        "x": 0.5, "y": round(float(y), 4), "shadow": dict(SHADOW),
        "keys": {"scale": [{"t": 0.0, "v": GROW[0], "ease": "linear"}, {"t": end, "v": GROW[1]}]},
        "props": {"style": style, "uppercase": True, "weight": 900, "color": STREAMER_YELLOW,
                  "activeColor": "#FFF27A", "emphColor": EMPH_COLOR, "emphScale": 1.3,
                  "speakerColors": [dict(c) for c in OTHER_VOICES], "pageFade": PAGE_FADE, "maxWords": 3},
    }


def _yellow(color: str) -> bool:
    try:
        r, g, b = (int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    except (ValueError, AttributeError):
        return False
    return r >= 200 and g >= 170 and b <= 100


def check_stream_captions(layer: dict, words: list[dict] | None = None, duration: float | None = None) -> dict:
    """Автопроверка слоя подписей стрим-шортса и эмфазы слов."""
    problems: list[str] = []
    props = layer.get("props") or {}
    if layer.get("type") != "captions":
        problems.append("слой не captions")
    if props.get("uppercase") is False:
        problems.append("не ВСЕ ЗАГЛАВНЫЕ")
    if float(props.get("weight") or 900) < 800:
        problems.append(f"шрифт не жирный ({props.get('weight')})")
    shadow = layer.get("shadow") or {}
    if not shadow or float(shadow.get("opacity") or 0) < 0.4:
        problems.append("нет тени")
    if not 0.05 <= float(props.get("pageFade") or 0) <= 0.3:
        problems.append("нет плавного появления фразы (pageFade 0.05–0.3 с)")
    if not _yellow(str(props.get("color") or "")):
        problems.append(f"стример (голос A) не жёлтый: {props.get('color')}")
    others = {c.get("s"): c.get("color") for c in props.get("speakerColors") or []}
    if others.get("B") and _yellow(str(others["B"])):
        problems.append("второй голос тем же жёлтым")
    keys = ((layer.get("keys") or {}).get("scale")) or []
    vals = [float(k["v"]) for k in sorted(keys, key=lambda k: float(k["t"]))]
    if len(vals) < 2 or abs(vals[0] - GROW[0]) > 0.005 or abs(vals[-1] - GROW[1]) > 0.005 \
            or any(b < a - 1e-9 for a, b in zip(vals, vals[1:])):
        problems.append(f"рост слоя не 1,00→1,05 монотонно: {vals}")
    if duration and keys and abs(float(max(k["t"] for k in keys)) - float(duration)) > 0.5:
        problems.append("рост слоя кончается не в конце ролика")
    emph = [w for w in words or [] if int(w.get("emph") or 0) > 0]
    if words and any(is_emotion_word(str(w.get("word") or "")) for w in words) and not emph:
        problems.append("слова-эмоции не выделены")
    return {"ok": not problems, "problems": problems, "emphasized": len(emph)}
