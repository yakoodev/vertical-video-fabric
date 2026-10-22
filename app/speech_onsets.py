"""Подгонка слов субтитров к настоящему звуку речи.

Whisper ставит начала слов с опозданием до полсекунды, а первое слово клипа —
иногда на 0.0, хотя голос начинается через 1.8 с. Агент-монтажёр исправлял это
вручную, замеряя громкость ffmpeg. Здесь то же автоматически: громкость
кадрами по 20 мс → где звучит голос → начало слова, попавшее в тишину,
переезжает на начало звука, а опоздавшее — к началу своей фразы, если до неё
никто не говорил; конец слова, ушедший в тишину, возвращается к концу звука.
"""

from __future__ import annotations

import math
import struct

FRAME_SEC = 0.02
SAMPLE_RATE = 16000
MAX_SHIFT_SEC = 0.7  # дальше — уже не опоздание распознавания, а другое слово
MIN_WORD_SEC = 0.05
_GAP_FILL_FRAMES = 3  # паузы короче 60 мс внутри слова — не тишина


def wav_pcm(path) -> bytes:
    """Сырые 16-битные сэмплы wav (ffmpeg пишет перед данными ещё и LIST — 44 байта не верны)."""
    import wave

    try:
        with wave.open(str(path), "rb") as f:
            return f.readframes(f.getnframes())
    except (OSError, EOFError, wave.Error):
        return b""  # звука нет — слова остаются как распознаны


def voiced_frames(pcm: bytes, rate: int = SAMPLE_RATE) -> list[bool]:
    """Кадры по 20 мс: звучит ли голос (порог от уровня шума записи)."""
    count = len(pcm) // 2
    if not count:
        return []
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    win = max(1, int(rate * FRAME_SEC))
    levels = []
    for i in range(0, count - win + 1, win):
        chunk = samples[i: i + win]
        rms = math.sqrt(sum(v * v for v in chunk) / win) / 32768
        levels.append(20 * math.log10(rms + 1e-9))
    if not levels:
        return []
    ordered = sorted(levels)
    floor = ordered[len(ordered) // 10]
    peak = ordered[-max(1, len(ordered) // 100)]
    threshold = max(floor + 10.0, peak - 35.0, -55.0)
    voiced = [db > threshold for db in levels]
    # Короткие провалы внутри речи (смычки согласных) заполняем.
    i = 0
    while i < len(voiced):
        if not voiced[i]:
            j = i
            while j < len(voiced) and not voiced[j]:
                j += 1
            if 0 < i and j < len(voiced) and j - i <= _GAP_FILL_FRAMES:
                for k in range(i, j):
                    voiced[k] = True
            i = j
        else:
            i += 1
    return voiced


def _at(voiced: list[bool], t: float) -> bool:
    i = int(t / FRAME_SEC)
    return 0 <= i < len(voiced) and voiced[i]


def snap_words(words: list[dict], voiced: list[bool]) -> tuple[list[dict], int]:
    """Слова ``{word, start, end}`` (по времени) → подогнанные к звуку; второе — сколько сдвинуто."""
    if not voiced:
        return [dict(w) for w in words], 0
    out = [dict(w) for w in words]
    moved = 0
    n = len(voiced)
    for idx, w in enumerate(out):
        start, end = float(w["start"]), float(w["end"])
        prev_end = float(out[idx - 1]["end"]) if idx else 0.0
        new_start = start
        i = int(start / FRAME_SEC)
        if not _at(voiced, start):
            # Начало в тишине — к первому звуку дальше: в пределах самого слова
            # сдвиг любой (первое слово на 0.0 при голосе с 1.8 с).
            j = i
            limit = min(n, int(end / FRAME_SEC))
            while j < limit and not voiced[j]:
                j += 1
            if j < limit:
                new_start = j * FRAME_SEC
        else:
            # Начало посреди звука — к началу этой фразы, если до неё была тишина
            # и предыдущее слово кончилось раньше (звук принадлежит этому слову).
            j = i
            while j > 0 and voiced[j - 1]:
                j -= 1
            onset = j * FRAME_SEC
            if j > 0 and start - onset <= MAX_SHIFT_SEC and onset >= prev_end - 1e-6:
                new_start = onset
        new_end = end
        if not _at(voiced, max(new_start, end - FRAME_SEC / 2)):
            k = min(n, int(end / FRAME_SEC))
            floor = int(new_start / FRAME_SEC)
            while k > floor and not (k - 1 < n and voiced[k - 1]):
                k -= 1
            back = k * FRAME_SEC
            if k > floor and end - back <= MAX_SHIFT_SEC:
                new_end = back
        new_start = max(new_start, prev_end)
        if new_end - new_start < MIN_WORD_SEC:
            new_start, new_end = start, end
        if abs(new_start - start) > 1e-3 or abs(new_end - end) > 1e-3:
            moved += 1
        w["start"], w["end"] = round(new_start, 3), round(new_end, 3)
    return out, moved


def snap_payload(payload: dict, pcm: bytes) -> int:
    """Подогнать слова субтитров (и границы строк по ним) к звуку; вернуть число сдвинутых."""
    words = payload.get("words") or []
    if not words:
        return 0
    lines = payload.get("lines") or []
    # Какие слова в какой строке — по старым временам, до сдвига.
    members = [
        [i for i, w in enumerate(words) if float(line["start"]) - 1e-3 <= float(w["start"]) < float(line["end"]) + 1e-3]
        for line in lines
    ]
    snapped, moved = snap_words(words, voiced_frames(pcm))
    if not moved:
        return 0
    payload["words"] = snapped
    for line, idx in zip(lines, members):
        if idx:
            line["start"] = snapped[idx[0]]["start"]
            line["end"] = max(snapped[idx[-1]]["end"], line["start"] + MIN_WORD_SEC)
    return moved
