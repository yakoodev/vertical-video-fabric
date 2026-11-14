"""Мем «только удар»: из файла видеомема — его короткая часть (0.8–2.2 с) без пауз внутри, файлом в кэше.

Владелец (14.11): «мемы вставляй недолго, обрезай на части и вырезай тишину — это темп портит». Вставка мема
(``render.inserts``) может нести ``parts: [[a, b], …]`` — куски файла мема (секунды файла) по порядку; рендер
(app/montage_assets.build_inserts_args) берёт тогда не сам файл библиотеки, а склейку этих кусков из кэша
``<runtime>/meme_trims`` длиной ровно ``duration`` вставки (короче — стоп-кадр последнего кадра и тишина). Файл
библиотеки не меняется, новых файлов в библиотеке не появляется.

* ``voiced_spans(path)`` — где в мемe звучит голос/звук (громкость кадров 20 мс, порог от пика), кэш по файлу;
* ``punch_parts(words, voiced, duration, focus)`` — какие куски взять: фраза с «эхо»-словом игрока и самый громкий
  кусок, паузы > 0.2 с вырезаны, всего 0.8–2.2 с (чистая функция, её проверяют тесты);
* ``trimmed_file(path, parts, length)`` — склейка в кэше (ffmpeg, 30 к/с, звук 48 кГц).
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import threading
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

PUNCH_MIN_SEC = 0.8
PUNCH_MAX_SEC = 2.2
MAX_PARTS = 6
FRAME_SEC = 0.02
GAP_CUT_SEC = 0.2      # пауза длиннее — вырезается
PAD_SEC = 0.06         # запас вокруг звучащего куска
SILENCE_UNDER_PEAK_DB = 30.0
FPS = 30
_LOCK = threading.Lock()


def _cache_dir() -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "meme_trims"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _stamp(path: Path) -> str:
    st = path.stat()
    return f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}"


# --------------------------------------------------------------------------- #
# Где мем звучит
# --------------------------------------------------------------------------- #


def spans_from_levels(levels: list[float], step: float = FRAME_SEC, floor_db: float | None = None) -> list[list[float]]:
    """Звучащие отрезки по громкости кадров (дБ): громче ``пик − 30`` (не тише −50), паузы ≤ 0.15 с склеены."""
    if not levels:
        return []
    peak = max(levels)
    thr = max(-50.0, peak - SILENCE_UNDER_PEAK_DB) if floor_db is None else floor_db
    spans: list[list[float]] = []
    for i, db in enumerate(levels):
        if db < thr:
            continue
        a, b = i * step, (i + 1) * step
        if spans and a - spans[-1][1] <= 0.15:
            spans[-1][1] = b
        else:
            spans.append([a, b])
    return [[round(a, 3), round(b, 3)] for a, b in spans if b - a >= 0.06]


def voiced_spans(path: str | Path) -> list[list[float]]:
    """Звучащие отрезки файла мема (секунды файла); кэш по пути, размеру и времени правки."""
    import numpy as np

    src = Path(path)
    if not src.is_file():
        return []
    key = hashlib.sha1(_stamp(src).encode("utf-8")).hexdigest()[:20]
    cache = _cache_dir() / f"voiced-{key}.json"
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
                           "-f", "s16le", "-"], capture_output=True, timeout=120, check=False)
    raw = proc.stdout or b""
    x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    step = int(16000 * FRAME_SEC)
    n = len(x) // step
    if n == 0:
        spans: list[list[float]] = []
    else:
        rms = np.sqrt(np.mean(x[: n * step].reshape(n, step) ** 2, axis=1) + 1e-12)
        spans = spans_from_levels([float(v) for v in 20 * np.log10(rms + 1e-9)])
    try:
        cache.write_text(json.dumps(spans), encoding="utf-8")
    except OSError:
        pass
    return spans


# --------------------------------------------------------------------------- #
# Какие куски взять (чистые функции)
# --------------------------------------------------------------------------- #


def _norm(word: Any) -> str:
    import re

    return re.sub(r"[^a-zа-я0-9]", "", str(word).lower().replace("ё", "е"))


def _merge(spans: list[list[float]], gap: float) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if out and a - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def _clip(spans: list[list[float]], lo: float, hi: float) -> list[list[float]]:
    return [[max(lo, a), min(hi, b)] for a, b in spans if min(hi, b) - max(lo, a) > 0.02]


def total(parts: list[list[float]]) -> float:
    return round(sum(b - a for a, b in parts), 3)


def punch_parts(words: list[dict] | None, voiced: list[list[float]] | None, duration: float,
                focus: Any = (), max_sec: float = PUNCH_MAX_SEC, min_sec: float = PUNCH_MIN_SEC) -> list[list[float]]:
    """Куски мема ``[[a, b], …]`` (секунды файла, по порядку) — его удар длиной ``min_sec``…``max_sec``.

    Основа — звучащие отрезки (``voiced``: громкость), уточнённые словами расшифровки (``words`` с временем): паузы
    длиннее 0.2 с вырезаны, вокруг звука запас 0.06 с. Всё звучащее короче ``max_sec`` — берётся целиком. Длиннее —
    берётся «эхо» (фраза со словом из ``focus`` — словом игрока, на которое мем отвечает) или, если эха нет,
    последняя фраза (панчлайн мема обычно в конце), и к ним добавляются соседние фразы, пока влезают. Звука нет —
    середина файла длиной ``max_sec``. Короче ``min_sec`` после вырезов — паузы не вырезаются (кусок целиком)."""
    duration = max(0.0, float(duration or 0.0))
    if duration <= 0:
        return []
    voiced = [list(map(float, s)) for s in (voiced or []) if len(s) == 2 and float(s[1]) > float(s[0])]
    words = [w for w in (words or []) if isinstance(w, dict) and w.get("end") is not None]
    sound = _merge([[a - PAD_SEC, b + PAD_SEC] for a, b in voiced], GAP_CUT_SEC)
    if words:
        speech = _merge([[float(w["start"]) - PAD_SEC, float(w["end"]) + PAD_SEC] for w in words], GAP_CUT_SEC)
        # слова главнее громкости: звук вне слов (вдох, шорох) — только если он рядом со словами
        sound = _merge(speech + [s for s in sound if any(s[0] < b + 0.3 and s[1] > a - 0.3 for a, b in speech)],
                       GAP_CUT_SEC)
    sound = _clip(sound, 0.0, duration)
    if not sound:
        mid = max(0.0, duration / 2 - max_sec / 2)
        return [[round(mid, 3), round(min(duration, mid + max_sec), 3)]]
    if total(sound) <= max_sec:
        parts = sound
    else:
        # фразы — звучащие отрезки; «эхо»-фраза или последняя — ядро, соседи добавляются, пока влезают
        want = {_norm(f) for f in focus or () if len(_norm(f)) >= 3}
        stems = {f[:5] for f in want}

        def echo(span: list[float]) -> bool:
            return any(_norm(w.get("word"))[:5] in stems and span[0] - 0.05 <= float(w["start"]) <= span[1]
                       for w in words) if stems else False

        core = next((i for i, s in enumerate(sound) if echo(s)), None)
        if core is None:
            core = len(sound) - 1
        chosen = {core}
        length = sound[core][1] - sound[core][0]
        if length > max_sec:  # одна фраза длиннее удара — её конец (там слово-эхо или панчлайн)
            a, b = sound[core]
            hit = [float(w["end"]) for w in words if echo([a, b]) and _norm(w.get("word"))[:5] in stems]
            end = min(b, (max(hit) + PAD_SEC) if hit else b)
            return [[round(max(a, end - max_sec), 3), round(end, 3)]]
        left, right = core - 1, core + 1
        while left >= 0 or right < len(sound):
            for i in (right, left):
                if 0 <= i < len(sound) and i not in chosen and length + sound[i][1] - sound[i][0] <= max_sec:
                    chosen.add(i)
                    length += sound[i][1] - sound[i][0]
            left, right = left - 1, right + 1
        parts = [sound[i] for i in sorted(chosen)]
    if total(parts) < min_sec:
        a = min(p[0] for p in parts)
        b = max(p[1] for p in parts)
        grow = max(0.0, min_sec - (b - a))
        a, b = max(0.0, a - grow / 2), min(duration, b + grow / 2)
        if b - a < min_sec:
            a, b = max(0.0, b - min_sec), min(duration, max(b, a + min_sec))
        parts = [[a, b]]
    return [[round(a, 3), round(b, 3)] for a, b in parts[:MAX_PARTS]]


def normalize_parts(raw: Any, max_total: float = 8.0) -> list[list[float]] | None:
    """Куски вставки в безопасный вид (по порядку, без пересечений, ≤ MAX_PARTS, вместе ≤ ``max_total``); мусор — None."""
    if not isinstance(raw, list) or not raw:
        return None
    out: list[list[float]] = []
    for item in raw[:MAX_PARTS]:
        try:
            a, b = float(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError, KeyError):
            return None
        if not (a == a and b == b) or a < 0 or b - a < 0.04:
            return None
        if out and a < out[-1][1] - 1e-6:
            return None
        out.append([round(a, 3), round(b, 3)])
    return out if total(out) <= max_total + 1e-6 else None


# --------------------------------------------------------------------------- #
# Файл в кэше
# --------------------------------------------------------------------------- #


def trim_args(src: Path, dest: Path, parts: list[list[float]], length: float, has_audio: bool = True) -> list[str]:
    """ffmpeg: куски по порядку, стыки звука 15 мс, длина ровно ``length`` (стоп-кадр и тишина в хвосте)."""
    graph, labels = [], []
    for i, (a, b) in enumerate(parts):
        d = b - a
        graph.append(f"[0:v]trim={a:.3f}:{b:.3f},setpts=PTS-STARTPTS,fps={FPS},scale=trunc(iw/2)*2:trunc(ih/2)*2,"
                     f"setsar=1,format=yuv420p[v{i}]")
        if has_audio:
            graph.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,aresample=48000,"
                         f"aformat=channel_layouts=stereo,afade=t=in:d=0.015,afade=t=out:st={max(0.0, d - 0.015):.3f}:"
                         f"d=0.015[a{i}]")
        labels.append(f"[v{i}]" + (f"[a{i}]" if has_audio else ""))
    n = len(parts)
    graph.append("".join(labels) + f"concat=n={n}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))
    pad = max(0.0, length - total(parts)) + 0.1
    graph.append(f"[vc]tpad=stop_mode=clone:stop_duration={pad:.3f},trim=0:{length:.3f}[v]")
    if has_audio:
        graph.append(f"[ac]apad=pad_dur={pad:.3f},atrim=0:{length:.3f}[a]")
    args = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", str(src), "-filter_complex", ";".join(graph),
            "-map", "[v]"]
    if has_audio:
        args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ac", "2"]
    return args + ["-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-r", str(FPS), "-movflags", "+faststart",
                   str(dest)]


def trimmed_file(path: str | Path, parts: list[list[float]], length: float, has_audio: bool = True) -> Path:
    """Склейка кусков мема длиной ``length`` — файл в кэше (готовый — сразу); сбой — исходный файл как есть."""
    src = Path(path)
    if not src.is_file() or not parts:
        return src
    key = hashlib.sha1(json.dumps([_stamp(src), parts, round(float(length), 3), has_audio]).encode("utf-8"))
    dest = _cache_dir() / f"trim-{key.hexdigest()[:24]}.mp4"
    with _LOCK:
        if dest.is_file() and dest.stat().st_size > 0:
            return dest
        tmp = dest.with_suffix(".part.mp4")
        proc = subprocess.run(trim_args(src, tmp, parts, float(length), has_audio), capture_output=True, text=True,
                              timeout=300, check=False)
        if proc.returncode != 0 or not tmp.is_file():
            _log.warning("meme trim failed for %s: %s", src, (proc.stderr or "")[-400:])
            tmp.unlink(missing_ok=True)
            return src
        tmp.replace(dest)
    return dest
