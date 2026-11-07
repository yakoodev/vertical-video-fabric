"""3D-серия: озвучка под темп серии. Лишние паузы короче, повторенная слово в слово реплика — тот же кусок звука.

Зачем. Нейронка читает длинный текст с короткими фразами медленно и с большими паузами (72 с против 58 с по темпу
части 1), а одну и ту же реплику дважды читает по-разному, хотя по сюжету это запись. Поэтому перед режиссурой и
рендером из готовой озвучки собирается новый файл, платных вызовов нет:

1. паузы ищем по энергии звука и укорачиваем: после конца фразы до ``PAUSE_END``, на запятой до ``PAUSE_COMMA``,
   перед панчлайном («Была лишь одна проблема») оставляем ``PAUSE_BEAT``;
2. реплика, которая целиком повторяет хвост более ранней фразы (не короче ``REPEAT_MIN_WORDS`` слов), заменяется
   копией первого звучания: те же слова, та же пауза, совпадает до сэмпла;
3. если всё ещё медленнее ``TARGET_WPS`` — ровный ``atempo`` не больше ``MAX_TEMPO`` (высота голоса не меняется).

Времена слов пересчитываются по тем же кускам, Whisper второй раз не нужен. Исходная запись не меняется: новый файл
лежит отдельным ассетом, ``timeline.voice_fit`` эпизода хранит его и новые слова. Опция эпизода
``voice_fit: false`` — оставить озвучку как есть.
"""

from __future__ import annotations

import re
import subprocess
import wave
from pathlib import Path
from typing import Any

VERSION = 1
SR = 48000
TARGET_WPS = 2.37          # темп части 1: 121 слово за 50.98 с (замер по базе рендера v3)
MAX_TEMPO = 1.12           # выше ухо начинает слышать ускорение
PAUSE_END, PAUSE_COMMA, PAUSE_INNER = 0.34, 0.18, 0.14
PAUSE_BEAT = 0.46          # пауза перед панчлайном остаётся: такая же в части 1
BEAT_RX = r"одна проблем|лишь одн"
REPEAT_MIN_WORDS = 4
SILENCE_DB = -34.0
SILENCE_MIN_SEC = 0.09
FRAME_SEC = 0.01
FADE_SEC = 0.005
HEAD_SEC, TAIL_SEC = 0.04, 0.16


class VoiceFitError(RuntimeError):
    pass


def _norm(word: Any) -> str:
    return re.sub(r"[^\wё%:]+", "", str(word or "").lower().replace("ё", "е"))


def _letters(words: list[dict]) -> list[dict]:
    """Без отдельных знаков («—»): считать темп и сравнивать реплики надо по настоящим словам."""
    return [w for w in words if _norm(w.get("word"))]


def repeated_lines(sentences: list[dict], words: list[dict]) -> list[dict]:
    """Какие реплики повторяют слово в слово хвост более ранней фразы: ``[{first: [w0, w1], again: [w0, w1], words: n}]``
    (индексы в ``words``). Текст берётся из самих фраз: новая фраза должна быть целиком этим хвостом."""
    tokens = [(k, _norm(w.get("word"))) for k, w in enumerate(words)]
    tokens = [(k, t) for k, t in tokens if t]
    seq = [t for _, t in tokens]
    spans: list[list[str]] = [[_norm(x) for x in str(s.get("text") or "").split() if _norm(x)] for s in sentences]
    out: list[dict] = []
    pos = 0
    starts: list[int] = []
    for sp in spans:
        starts.append(pos)
        pos += len(sp)
    if pos != len(seq):
        return []          # слова не совпали со сценарием: сравнивать нечего, повтор не ищем
    for i, sp in enumerate(spans):
        if len(sp) < REPEAT_MIN_WORDS:
            continue
        for j in range(i):
            if spans[j][-len(sp):] != sp:
                continue
            a0 = starts[j] + len(spans[j]) - len(sp)
            b0 = starts[i]
            out.append({"first": [tokens[a0][0], tokens[a0 + len(sp) - 1][0]],
                        "again": [tokens[b0][0], tokens[b0 + len(sp) - 1][0]], "words": len(sp)})
            break
    return out


def decode(path: Path | str) -> Any:
    """Озвучка → моно float32 ``SR`` Гц."""
    import numpy as np

    proc = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"],
                          capture_output=True, check=True, timeout=120)
    return np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def find_silences(pcm: Any, *, frame: float = FRAME_SEC, db: float = SILENCE_DB,
                  min_sec: float = SILENCE_MIN_SEC) -> list[tuple[float, float]]:
    """Паузы по энергии: куски тише ``db`` от громкой речи (95-й процентиль), не короче ``min_sec``."""
    import numpy as np

    n = max(1, int(SR * frame))
    usable = len(pcm) // n
    if not usable:
        return []
    rms = np.sqrt((pcm[: usable * n].reshape(-1, n) ** 2).mean(axis=1))
    level = float(np.percentile(rms, 95))
    if level <= 1e-5:
        return []
    quiet = rms < level * 10 ** (db / 20.0)
    out: list[tuple[float, float]] = []
    start = None
    for i, q in enumerate(quiet):
        if q and start is None:
            start = i
        elif not q and start is not None:
            if (i - start) * frame >= min_sec:
                out.append((round(start * frame, 3), round(i * frame, 3)))
            start = None
    if start is not None and (usable - start) * frame >= min_sec:
        out.append((round(start * frame, 3), round(usable * frame, 3)))
    return out


def _pause_cap(a: float, b: float, words: list[dict]) -> float:
    """Сколько оставить от паузы: по знаку перед ближайшей границей слов (Whisper ошибается на десятые, поэтому граница
    ближайшая, а не точная)."""
    mid = (a + b) / 2
    best, cap = 0.5, PAUSE_INNER      # дальше 0.5 с от границы — пауза внутри слова, её не считаем фразовой
    for k in range(len(words) - 1):
        gap_mid = (float(words[k]["end"]) + float(words[k + 1]["start"])) / 2
        if abs(gap_mid - mid) >= best:
            continue
        best = abs(gap_mid - mid)
        tail = next((str(w["word"]) for w in reversed(words[: k + 1]) if _norm(w["word"])), "")
        head = " ".join(str(w["word"]) for w in words[max(0, k - 5): k + 1]).lower().replace("ё", "е")
        if re.search(BEAT_RX, head) and re.search(r"[.!?…]$", tail.rstrip("»”\"")):
            cap = PAUSE_BEAT
        elif re.search(r"[.!?…]$", tail.rstrip("»”\"")):
            cap = PAUSE_END
        elif re.search(r"[,:;—–-]$", tail):
            cap = PAUSE_COMMA
        else:
            cap = PAUSE_INNER
    return cap


def keep_spans(silences: list[tuple[float, float]], words: list[dict], total: float) -> list[tuple[float, float]]:
    """Какие куски записи оставить: всё, кроме середины длинных пауз."""
    keep: list[tuple[float, float]] = []
    cur = 0.0
    for a, b in silences:
        if a <= 0.0 + 1e-6:
            cur = max(cur, b - HEAD_SEC)   # тишина в самом начале
            continue
        if b >= total - 1e-3:
            keep.append((cur, min(total, a + TAIL_SEC)))
            cur = total
            break
        cap = _pause_cap(a, b, words)
        if b - a <= cap:
            continue
        keep.append((cur, a + cap / 2))
        cur = b - cap / 2
    if cur < total:
        keep.append((cur, total))
    return [(round(x, 4), round(y, 4)) for x, y in keep if y - x > 0.002]


def _edge(t: float, silences: list[tuple[float, float]], want: str) -> float:
    """Граница реплики: ближайший конец (``start``) или начало (``end``) паузы рядом с временем слова."""
    best, hit = 0.45, t
    for a, b in silences:
        v = b if want == "start" else a
        if abs(v - t) < best:
            best, hit = abs(v - t), v
    return hit


def build_pieces(keep: list[tuple[float, float]], repeats: list[dict], words: list[dict],
                 silences: list[tuple[float, float]]) -> tuple[list[dict], list[dict]]:
    """Монтажный лист: ``[{a, b, copy}]`` по порядку; ``copy`` — номер повтора, для которого кусок вставлен. Повтор
    заменяется кусками первого звучания. Второй ответ — ``repeats`` с найденными границами звука."""
    regions = []
    for n, rep in enumerate(repeats):
        regions.append({**rep, "n": n,
                        "a0": _edge(float(words[rep["first"][0]]["start"]), silences, "start"),
                        "a1": _edge(float(words[rep["first"][1]]["end"]), silences, "end"),
                        "b0": _edge(float(words[rep["again"][0]]["start"]), silences, "start"),
                        "b1": _edge(float(words[rep["again"][1]]["end"]), silences, "end")})
    regions.sort(key=lambda r: r["b0"])
    pieces: list[dict] = []

    def clip(x0: float, x1: float, copy: int | None) -> None:
        for a, b in keep:
            lo, hi = max(a, x0), min(b, x1)
            if hi - lo > 0.002:
                pieces.append({"a": round(lo, 4), "b": round(hi, 4), "copy": copy})

    cur = 0.0
    end = keep[-1][1] if keep else 0.0
    for r in regions:
        clip(cur, r["b0"], None)
        clip(r["a0"], r["a1"], r["n"])
        cur = r["b1"]
    clip(cur, end, None)
    return pieces, regions


def map_words(words: list[dict], pieces: list[dict], regions: list[dict],
              silences: list[tuple[float, float]]) -> list[dict]:
    """Слова на новом времени (до темпа). Начало слова в паузе — на её конец: Whisper часто ставит его раньше звука."""
    dst = 0.0
    laid = []
    for p in pieces:
        laid.append({**p, "t": dst})
        dst += p["b"] - p["a"]

    def snap(t: float, side: str) -> float:
        for a, b in silences:
            if a < t < b:
                return b if side == "start" else a
        return t

    def place(t: float, copy: int | None, side: str) -> float:
        own = [p for p in laid if p["copy"] == copy]
        for p in own:
            if p["a"] <= t <= p["b"]:
                return p["t"] + t - p["a"]
        if side == "start":
            nxt = [p for p in own if p["a"] > t]
            return nxt[0]["t"] if nxt else (own[-1]["t"] + own[-1]["b"] - own[-1]["a"] if own else 0.0)
        prv = [p for p in own if p["b"] < t]
        return prv[-1]["t"] + prv[-1]["b"] - prv[-1]["a"] if prv else (own[0]["t"] if own else 0.0)

    src_of: dict[int, tuple[int, int]] = {}
    for r in regions:
        for k in range(r["again"][0], r["again"][1] + 1):
            src_of[k] = (k - r["again"][0] + r["first"][0], r["n"])
    out = []
    for k, w in enumerate(words):
        src, copy = src_of.get(k, (k, None))
        s0, e0 = float(words[src]["start"]), float(words[src]["end"])
        start = place(snap(s0, "start"), copy, "start")
        end = place(snap(e0, "end"), copy, "end")
        if end <= start:
            end = start + 0.04
        out.append({**w, "start": start, "end": end})
    for k in range(1, len(out)):      # порядок слов не ломаем (знаки «—» без своего звука)
        if out[k]["start"] < out[k - 1]["start"]:
            out[k] = {**out[k], "start": out[k - 1]["start"], "end": max(out[k]["end"], out[k - 1]["start"] + 0.02)}
    return out


def choose_tempo(words: list[dict], *, target: float = TARGET_WPS, cap: float = MAX_TEMPO) -> float:
    """Ровное ускорение: сколько не хватает до ``target`` слов/с, не больше ``cap``."""
    real = _letters(words)
    if not real:
        return 1.0
    now = float(real[-1]["end"])
    want = len(real) / max(target, 0.1)
    return round(max(1.0, min(cap, now / max(want, 0.1))), 3)


def _write_wav(path: Path, pcm: Any) -> None:
    import numpy as np

    data = np.clip(pcm, -1.0, 1.0)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes((data * 32767).astype(np.int16).tobytes())


def _join(pcm: Any, pieces: list[dict]) -> Any:
    import numpy as np

    fade = max(1, int(SR * FADE_SEC))
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    parts = []
    for p in pieces:
        seg = np.array(pcm[int(p["a"] * SR): int(p["b"] * SR)], dtype=np.float32)
        if len(seg) > 2 * fade:
            seg[:fade] *= ramp
            seg[-fade:] *= ramp[::-1]
        parts.append(seg)
    return np.concatenate(parts) if parts else np.zeros(1, dtype=np.float32)


def _paste_repeats(pcm: Any, pieces: list[dict], regions: list[dict], tempo: float) -> Any:
    """Звук второй реплики — копия звука первой после темпа (края — короткий наплыв, стык с паузой не щёлкает)."""
    import numpy as np

    out = np.array(pcm, dtype=np.float32)
    fade = max(1, int(SR * FADE_SEC))
    ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
    for r in regions:
        dst = 0.0
        first_t = again_t = None
        for p in pieces:
            if p["copy"] is None and first_t is None and p["b"] > r["a0"]:
                first_t = dst + max(0.0, r["a0"] - p["a"])
            if p["copy"] == r["n"] and again_t is None:
                again_t = dst
            dst += p["b"] - p["a"]
        if first_t is None or again_t is None:
            continue
        span = sum(p["b"] - p["a"] for p in pieces if p["copy"] == r["n"]) / tempo
        n = int(span * SR)
        a0, b0 = int(first_t / tempo * SR), int(again_t / tempo * SR)
        n = min(n, len(out) - a0, len(out) - b0)
        if n <= 2 * fade:
            continue
        seg = out[a0:a0 + n].copy()
        mix = np.ones(n, dtype=np.float32)
        mix[:fade], mix[-fade:] = ramp, ramp[::-1]
        out[b0:b0 + n] = seg * mix + out[b0:b0 + n] * (1.0 - mix)
    return out


def fit_narration(path: Path | str, out_path: Path | str, sentences: list[dict], words: list[dict],
                  *, target: float = TARGET_WPS, tempo_cap: float = MAX_TEMPO) -> dict:
    """Новая озвучка в ``out_path``: паузы короче, повторы одним звуком, темп к ``target``.
    → ``{words, tempo, audio_sec, source_sec, repeats, wps_before, wps_after}``."""
    if not words:
        raise VoiceFitError("нет слов озвучки")
    pcm = decode(path)
    total = len(pcm) / SR
    if total < 1.0:
        raise VoiceFitError("озвучка слишком короткая")
    silences = find_silences(pcm)
    if not silences:
        raise VoiceFitError("в озвучке не нашлось пауз")
    keep = keep_spans(silences, words, total)
    repeats = repeated_lines(sentences, words)
    pieces, regions = build_pieces(keep, repeats, words, silences)
    timed = map_words(words, pieces, regions, silences)
    tempo = choose_tempo(timed, target=target, cap=tempo_cap)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = out_path.with_suffix(".cut.wav")
    fast = out_path.with_suffix(".tempo.wav")
    _write_wav(raw, _join(pcm, pieces))
    try:
        graph = ["-filter:a", f"atempo={tempo:.3f}"] if tempo > 1.001 else []
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), *graph, "-ar", str(SR), str(fast)], check=True,
                       capture_output=True, timeout=120)
        # atempo режет звук по своей сетке, копии после него расходятся на доли периода: кладём один и тот же звук
        # поверх второй реплики уже после темпа — совпадение до сэмпла, а не только на слух
        _write_wav(raw, _paste_repeats(decode(fast), pieces, regions, tempo))
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-c:a", "libmp3lame", "-b:a", "128k", str(out_path)],
                       check=True, capture_output=True, timeout=120)
    finally:
        raw.unlink(missing_ok=True)
        fast.unlink(missing_ok=True)
    final = [{**w, "start": round(w["start"] / tempo, 3), "end": round(w["end"] / tempo, 3)} for w in timed]
    real_before, real_after = _letters(words), _letters(final)
    audio_sec = sum(p["b"] - p["a"] for p in pieces) / tempo
    return {"v": VERSION, "words": final, "tempo": tempo, "audio_sec": round(audio_sec, 3), "source_sec": round(total, 3),
            "repeats": [{k: r[k] for k in ("first", "again", "words")} for r in regions],
            "wps_before": round(len(real_before) / max(float(real_before[-1]["end"]), 0.1), 3),
            "wps_after": round(len(real_after) / max(float(real_after[-1]["end"]), 0.1), 3),
            "speech_end": round(float(final[-1]["end"]), 3)}
