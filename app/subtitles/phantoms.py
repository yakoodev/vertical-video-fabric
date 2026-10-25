"""Галлюцинации распознавания на тишине: «Субтитры сделал DimaTorzok», «Продолжение следует…».

Whisper (и Gemini, когда слушает голоса) на тихих местах выдаёт строки из обучающих
титров. Монтаж пачки апекса: такие строки попадали в субтитры клипа и в расшифровку
исходника, а по ней get_clip ругался «речь есть, а субтитров нет» на кусках, где никто
не говорил. Фильтр двух видов:

* текст из стоп-листа известных фантомов (``is_phantom_text``) — выкидывается всегда;
* сегмент, на время которого дорожка речи (``speech_tracks`` — микрофон) тише порога
  речи (``speech_threshold`` — тот же авто-порог, что у голоса PNGtuber: шум дорожки +
  12 дБ в рамках −55..−40) — распознан на тишине. На смеси с игрой громко почти всегда,
  такой сегмент остаётся: проверка ничего не выкидывает, когда звук не тихий.

Чистые функции; звук — сырые 16-битные сэмплы моно (``wav_pcm``).
"""

from __future__ import annotations

import re
from typing import Any

# Известные «субтитры из ниоткуда» (из обучающих титров). Сравнение — по нормализованному
# тексту (нижний регистр, ё → е, без знаков), подстрокой.
PHANTOM_PHRASES = (
    "субтитры сделал", "субтитры делал", "субтитры создавал", "субтитры подготовил",
    "субтитры подогнал", "субтитры by", "редактор субтитров", "корректор",
    "продолжение следует", "спасибо за просмотр", "подписывайтесь на канал",
    "amara org", "dimatorzok", "диматорзок",
)
FRAME_SEC = 0.02
# Сегмент «звучит», если над порогом речи хотя бы столько секунд (щелчок мыши — меньше).
MIN_HEARD_SEC = 0.12
# Запас по краям сегмента: время Whisper гуляет на десятые доли секунды.
EDGE_SEC = 0.15


def _norm(text: Any) -> str:
    low = str(text or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", low)).strip()


def is_phantom_text(text: Any) -> bool:
    """Текст — известная галлюцинация распознавания (титры, «продолжение следует»)."""
    norm = _norm(text)
    return bool(norm) and any(phrase in norm for phrase in PHANTOM_PHRASES)


def frame_levels(pcm: bytes, rate: int = 16000, step: float = FRAME_SEC) -> list[float]:
    """Громкость кадров ``step`` (RMS дБ полной шкалы). Блоками во float32: кусок расшифровки
    исходника — 20 минут звука, целиком во float64 это сотни мегабайт."""
    import numpy as np

    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2")
    win = max(1, int(round(rate * step)))
    usable = len(samples) // win * win
    out: list[float] = []
    block = win * 8192
    for i in range(0, usable, block):
        chunk = samples[i: min(i + block, usable)].astype(np.float32) / 32768.0
        rms = np.sqrt(np.mean((chunk * chunk).reshape(-1, win), axis=1))
        out.extend(np.round(20 * np.log10(np.maximum(rms, 10 ** (-90 / 20))), 1).tolist())
    return out


def speech_threshold(levels: list[float]) -> float:
    """Порог «звучит голос» по шуму дорожки — как авто-порог голоса PNGtuber."""
    from app.pngtuber import auto_threshold

    return auto_threshold(levels)


def heard_sec(levels: list[float], threshold: float, start: float, end: float, step: float = FRAME_SEC) -> float:
    """Сколько секунд отрезка ``[start, end]`` (с запасом по краям) громче порога речи."""
    lo = max(0, int((start - EDGE_SEC) / step))
    hi = min(len(levels), int((end + EDGE_SEC) / step) + 1)
    return sum(1 for db in levels[lo:hi] if db >= threshold) * step


def is_silent(levels: list[float], threshold: float, start: float, end: float, step: float = FRAME_SEC) -> bool:
    """На отрезке речи нет: громче порога меньше ``MIN_HEARD_SEC``. Звука нет совсем — не судим."""
    if not levels or start >= len(levels) * step:
        return False
    return heard_sec(levels, threshold, start, end, step) < MIN_HEARD_SEC


def drop_phantoms(result: Any, pcm: bytes, rate: int = 16000) -> tuple[Any, int]:
    """Сегменты распознавания (``SubtitleResult``) без фантомов: известный текст или тишина на
    дорожке речи — сегмент и его слова выкидываются. Второе — сколько выкинуто (слов, а у
    движка без слов — сегментов)."""
    from dataclasses import replace

    segments = list(getattr(result, "segments", None) or [])
    words = list(getattr(result, "words", None) or [])
    levels = frame_levels(pcm, rate)
    threshold = speech_threshold(levels) if levels else 0.0
    dropped: list[tuple[float, float]] = []
    kept = []
    for seg in segments:
        silent = bool(levels) and is_silent(levels, threshold, float(seg.start), float(seg.end))
        if is_phantom_text(seg.text) or silent:
            dropped.append((float(seg.start), float(seg.end)))
        else:
            kept.append(seg)

    def gone(w: Any) -> bool:
        mid = (float(w.start) + float(w.end)) / 2
        if any(a - 1e-3 <= mid <= b + 1e-3 for a, b in dropped):
            return True
        # Слово вне сегментов (движок дал только слова) — по своему времени.
        if not any(float(s.start) - 1e-3 <= mid <= float(s.end) + 1e-3 for s in segments):
            return bool(levels) and is_silent(levels, threshold, float(w.start), float(w.end))
        return False

    clean_words = [w for w in words if not gone(w)]
    # Фантом из нескольких слов без сегментов: «Продолжение следует» словами подряд.
    clean_words = _drop_phantom_runs(clean_words)
    removed = len(words) - len(clean_words)
    if not dropped and not removed:
        return result, 0
    text = " ".join(s.text for s in kept).strip() or " ".join(w.word for w in clean_words)
    usage = {**(getattr(result, "usage", None) or {}), "droppedPhantoms": max(removed, len(dropped))}
    return replace(result, segments=kept, words=clean_words, text=text, usage=usage), max(removed, len(dropped))


def _run_is_phantom(run: str) -> bool:
    """Слова подряд — ровно фантом: фраза целиком (и, у титров, ещё одно слово — имя)."""
    for phrase in PHANTOM_PHRASES:
        if run == phrase:
            return True
        credits = "субтитр" in phrase and " " in phrase
        if credits and run.startswith(phrase + " ") and len(run[len(phrase) + 1:].split()) == 1:
            return True
    return False


def _drop_phantom_runs(words: list[Any], max_len: int = 4) -> list[Any]:
    """Слова подряд (до ``max_len``), которые вместе — фантом, выкидываются. Одно слово —
    только имя «автора титров» (корректор в живой речи бывает)."""
    out: list[Any] = []
    i = 0
    while i < len(words):
        hit = 0
        for n in range(min(max_len, len(words) - i), 0, -1):
            run = _norm(" ".join(str(w.word) for w in words[i:i + n]))
            if (n > 1 and _run_is_phantom(run)) or run in ("dimatorzok", "диматорзок"):
                hit = n
                break
        if hit:
            i += hit
            continue
        out.append(words[i])
        i += 1
    return out


def silent_cue(levels: list[float], threshold: float, start: float, end: float, step: float = FRAME_SEC) -> bool:
    """Реплика расшифровки на тишине дорожки речи (время — от начала ``levels``)."""
    return is_silent(levels, threshold, start, end, step)
