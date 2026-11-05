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
# Поправка «распознавание спешит» для слов внутри сплошной речи: медиана сдвигов слов после
# пауз, если таких слов хотя бы столько и сдвиг не меньше LEAD_MIN_SEC (иначе — не спешит).
LEAD_MIN_SAMPLES = 3
LEAD_MIN_SEC = 0.08
MAX_LEAD_SEC = 0.6
NOISY_VOICED_SHARE = 0.85  # громкость «звучит» в такой доле кадров — это не речь, а игра под ней
STRETCH_MARGIN_SEC = 0.3  # запас к правдоподобной длине слова: растянутое голосом «обязаан» не режем


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


def _vad_or_loudness(pcm: bytes, voiced: list[bool]) -> list[bool]:
    """Громкость «звучит» почти всё время (игра, музыка под голосом) — начала слов так не найти:
    берём Silero VAD. Обычная запись с паузами — громкость, как раньше."""
    if not voiced or sum(voiced) / len(voiced) < NOISY_VOICED_SHARE:
        return voiced
    try:
        import numpy as np

        from app.audio_map import vad_segments

        spans, how = vad_segments(np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0, SAMPLE_RATE)
    except Exception:  # noqa: BLE001 - без numpy/VAD — как раньше
        return voiced
    if how != "silero" or not spans:
        return voiced
    frames = [False] * len(voiced)
    for a, b in spans:
        for i in range(int(a / FRAME_SEC), min(len(frames), int(b / FRAME_SEC) + 1)):
            frames[i] = True
    return frames


def _at(voiced: list[bool], t: float) -> bool:
    i = int(t / FRAME_SEC)
    return 0 <= i < len(voiced) and voiced[i]


def snap_words(words: list[dict], voiced: list[bool]) -> tuple[list[dict], int]:
    """Слова ``{word, start, end}`` (по времени) → подогнанные к звуку; второе — сколько сдвинуто.

    Слово после паузы прилипает к началу звука. Внутри сплошной речи паузы нет — там
    начало слова двигается на ту же поправку, на какую распознавание спешило у слов
    после пауз (медиана, если таких слов ≥ LEAD_MIN_SAMPLES): Whisper ставит начала
    на ~0.3 с раньше, и агент вручную сдвигал все субтитры на +0.25 с."""
    if not voiced:
        return [dict(w) for w in words], 0
    out = [dict(w) for w in words]
    original = [(float(w["start"]), float(w["end"])) for w in words]
    leads: list[float] = []
    stuck: list[int] = []  # начало в сплошном звуке — к началу звука не прилипло
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
                if idx and 0 < new_start - start <= MAX_LEAD_SEC:
                    leads.append(new_start - start)  # распознавание поспешило — на столько
        else:
            # Начало посреди звука — к началу этой фразы, если до неё была тишина
            # и предыдущее слово кончилось раньше (звук принадлежит этому слову).
            j = i
            while j > 0 and voiced[j - 1]:
                j -= 1
            onset = j * FRAME_SEC
            if j > 0 and start - onset <= MAX_SHIFT_SEC and onset >= prev_end - 1e-6:
                new_start = onset
            elif idx:
                stuck.append(idx)
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
        w["start"], w["end"] = round(new_start, 3), round(new_end, 3)
    lead = _median(leads) if len(leads) >= LEAD_MIN_SAMPLES else 0.0
    if lead >= LEAD_MIN_SEC:
        for idx in stuck:
            w = out[idx]
            start, end = float(w["start"]), float(w["end"])
            prev_end = float(out[idx - 1]["end"])
            # Не дальше конца своего слова и не раньше конца предыдущего.
            shifted = min(start + lead, end - MIN_WORD_SEC)
            if shifted > start and shifted >= prev_end - 1e-6:
                w["start"] = round(shifted, 3)
    for w, (start, end) in zip(out, original):
        if abs(float(w["start"]) - start) > 1e-3 or abs(float(w["end"]) - end) > 1e-3:
            moved += 1
    return out, moved


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def plausible_word_sec(word: str) -> float:
    """Сколько слово может звучать: распознавание тянет его начало назад по паузе и чужой речи, а конец ставит точно."""
    return 0.2 + 0.11 * sum(ch.isalnum() for ch in str(word))


def trim_stretched(words: list[dict]) -> int:
    """Начало растянутого слова — от его конца. Клип 934: «Обязан!» 3.23 с — вся страница на экране за 2.4 с до слова,
    и ни голос, ни VAD этого не видят: под растянутым началом говорит кто-то другой. → сколько слов подрезано."""
    moved = 0
    for w in words:
        limit = plausible_word_sec(w["word"]) + STRETCH_MARGIN_SEC
        if float(w["end"]) - float(w["start"]) > limit:
            w["start"] = round(float(w["end"]) - limit, 3)
            moved += 1
    return moved


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
    snapped, moved = snap_words(words, _vad_or_loudness(pcm, voiced_frames(pcm)))
    moved += trim_stretched(snapped)
    if not moved:
        return 0
    payload["words"] = snapped
    for line, idx in zip(lines, members):
        if idx:
            line["start"] = snapped[idx[0]]["start"]
            line["end"] = max(snapped[idx[-1]]["end"], line["start"] + MIN_WORD_SEC)
    return moved
