"""Признак момента «немая реакция»: плотная речь, затем 3–8 с без слов, но громко (крик, смех).

Разбор каналов нарезок (G:\\vvf-hot\\yt\\related\\stream-clips\\findings.md, п. 47, 50, 52): пересматривают не
реплику, а немую реакцию сразу после неё. На подборке 47 пересмотры коррелируют с самой длинной паузой речи
(r = +0,49) и обратно — с плотностью слов (r = −0,43); на пиках 50 и 52 в 6–8 с нет ни одного слова.

Чистая часть (``find_silent_reactions``, ``reaction_signal``, ``check_reaction``) работает на словах
транскрипта и метках ``audio_map.label_spans`` и проверяется на синтетике. Источник целиком
(``compute_source_reactions``) считается кусками по 5 минут (память: 5 мин × 16 кГц ≈ 19 МБ) в очереди
тяжёлых задач и кешируется по файлу и транскрипту; ``chat_signal.rerank`` берёт только готовый кеш.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

BUCKET = 10.0
DENSE_WINDOW = 6.0      # сколько секунд речи до паузы смотрим
DENSE_WPS = 1.5         # «плотная речь»: слов в секунду (90/мин; у стримеров 100–200/мин)
GAP_MIN, GAP_MAX = 3.0, 8.0
LOUD_LABELS = ("громко", "крик", "смех?")
MIN_LOUD_SHARE = 0.25   # доля паузы, где громко/крик/смех
CHUNK_SEC = 300.0
VERSION = 1


def words_from_cues(cues: Iterable[dict]) -> list[tuple[float, float]]:
    """Слова (start, end) из реплик транскрипта: свои ``words`` у реплики, иначе текст поровну по реплике."""
    out: list[tuple[float, float]] = []
    for cue in cues or []:
        if not isinstance(cue, dict):
            continue
        words = cue.get("words")
        if isinstance(words, list) and words:
            for w in words:
                try:
                    a, b = float(w["start"]), float(w["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if b >= a:
                    out.append((a, b))
            continue
        try:
            a, b = float(cue.get("start", 0)), float(cue.get("end", 0))
        except (TypeError, ValueError):
            continue
        tokens = str(cue.get("text") or "").split()
        if not tokens or b <= a:
            continue
        step = (b - a) / len(tokens)
        out += [(a + i * step, a + (i + 1) * step) for i in range(len(tokens))]
    return sorted(out)


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def find_silent_reactions(words: list[tuple[float, float]], spans: list[tuple[float, float, str]], *,
                          dense_wps: float = DENSE_WPS, gap: tuple[float, float] = (GAP_MIN, GAP_MAX),
                          min_loud_share: float = MIN_LOUD_SHARE, loud_labels: Iterable[str] = LOUD_LABELS) -> list[dict]:
    """Немые реакции: пауза между словами ``gap`` (3–8 с) после плотной речи, внутри паузы громко.

    ``words`` — (start, end) по возрастанию, ``spans`` — (start, end, метка) ``audio_map.label_spans`` в той же
    шкале. Ответ — ``{start, end, len, density, loud_share, labels, score}``, score 0..1."""
    loud_set = set(loud_labels)
    loud = sorted((float(a), float(b), str(lab)) for a, b, lab in spans or [] if str(lab) in loud_set and b > a)
    ws = sorted((float(a), float(b)) for a, b in words or [])
    out: list[dict] = []
    for i in range(len(ws) - 1):
        g0, g1 = ws[i][1], ws[i + 1][0]
        length = g1 - g0
        if not gap[0] <= length <= gap[1]:
            continue
        lo = g0 - DENSE_WINDOW
        density = sum(1 for a, b in ws[: i + 1] if b > lo) / DENSE_WINDOW
        if density < dense_wps:
            continue
        cover: dict[str, float] = {}
        for a, b, lab in loud:
            if a >= g1:
                break
            part = _overlap(a, b, g0, g1)
            if part > 0:
                cover[lab] = cover.get(lab, 0.0) + part
        share = min(1.0, sum(cover.values()) / length)
        if share < min_loud_share:
            continue
        score = min(1.0, share / 0.5) * (0.6 + 0.4 * min(1.0, density / 3.0))
        out.append({"start": round(g0, 2), "end": round(g1, 2), "len": round(length, 2), "density": round(density, 2),
                    "loud_share": round(share, 2), "labels": sorted(cover, key=lambda k: -cover[k]),
                    "score": round(score, 3)})
    return out


def check_reaction(item: dict, words: list[tuple[float, float]], spans: list[tuple[float, float, str]]) -> list[str]:
    """Автопроверка найденной реакции по сырым данным: пустой список — правило выполнено."""
    problems = []
    a, b = float(item["start"]), float(item["end"])
    if not GAP_MIN <= b - a <= GAP_MAX:
        problems.append(f"пауза {b - a:.1f} с вне {GAP_MIN:g}–{GAP_MAX:g} с")
    inside = [w for w in words if _overlap(w[0], w[1], a + 0.05, b - 0.05) > 0]
    if inside:
        problems.append(f"внутри паузы {len(inside)} слов")
    before = sum(1 for w0, w1 in words if a - DENSE_WINDOW < w1 <= a + 0.05) / DENSE_WINDOW
    if before < DENSE_WPS:
        problems.append(f"речь до паузы редкая: {before:.2f} слов/с < {DENSE_WPS:g}")
    loud = sum(_overlap(s0, s1, a, b) for s0, s1, lab in spans if lab in LOUD_LABELS)
    if loud / max(1e-6, b - a) < MIN_LOUD_SHARE:
        problems.append(f"громко лишь {loud / max(1e-6, b - a):.0%} паузы")
    return problems


def reaction_signal(reactions: list[dict], duration: float | None = None, bucket: float = BUCKET) -> dict | None:
    """Корзины по 10 с, как у чата: в корзине — лучшая реакция, задевшая её. Нет реакций — None."""
    if not reactions:
        return None
    end = max(float(duration or 0), max(float(r["end"]) for r in reactions))
    n = int(end // bucket) + 1
    curve = [0.0] * n
    for r in reactions:
        for i in range(int(float(r["start"]) // bucket), min(n - 1, int(float(r["end"]) // bucket)) + 1):
            curve[i] = max(curve[i], float(r["score"]))
    return {"bucket": bucket, "curve": [round(v, 4) for v in curve]}


# --------------------------------------------------------------------------- #
# Источник целиком (тяжёлое) и кеш
# --------------------------------------------------------------------------- #


def _cache_path(source_id: int) -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "moment_signals"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"reactions_{int(source_id)}.json"


def _stamp(source: dict, words: list) -> list:
    path = Path(str(source.get("local_path") or ""))
    try:
        st = path.stat()
        return [VERSION, str(path), st.st_size, int(st.st_mtime), len(words)]
    except OSError:
        return [VERSION, str(path), len(words)]


def cached_reactions(store: Any, source_id: int) -> dict | None:
    """Готовый кеш реакций источника (без пересчёта) или None."""
    path = _cache_path(source_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        source = store.get_source(int(source_id))
        words = words_from_cues(store.get_source_transcript(int(source_id)))
    except (OSError, ValueError, KeyError):
        return None
    return data if data.get("stamp") == _stamp(source, words) else None


def chunk_spans(feat_fn: Any, label_fn: Any, x: Any, words: list[tuple[float, float]], t0: float,
                hop: float = 0.05) -> list[tuple[float, float, str]]:
    """Метки куска звука ``x`` (начало ``t0`` в шкале источника); речь — по словам транскрипта."""
    import numpy as np

    feat = feat_fn(x)
    n = len(feat["rms_db"])
    mask = np.zeros(n, bool)
    for a, b in words:
        i0, i1 = int((a - t0) / hop), int((b - t0) / hop) + 1
        if i1 > 0 and i0 < n:
            mask[max(0, i0): min(n, i1)] = True
    return [(round(t0 + a, 2), round(t0 + b, 2), lab) for a, b, lab in label_fn(feat, mask) if lab in LOUD_LABELS]


def compute_source_reactions(store: Any, source_id: int, max_hours: float = 8.0) -> dict:
    """Реакции источника целиком (кусками по 5 мин, в очереди тяжёлых задач) → кеш; повтор — из кеша."""
    from app import audio_map as am
    from app.heavy_queue import heavy_slot

    hit = cached_reactions(store, source_id)
    if hit is not None:
        return hit
    source = store.get_source(int(source_id))
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ValueError("файла видео нет")
    words = words_from_cues(store.get_source_transcript(int(source_id)))
    if not words:
        raise ValueError("нет расшифровки источника — немую реакцию не по чему искать")
    total = float(source.get("duration_sec") or 0) or words[-1][1]
    total = min(total, max_hours * 3600)
    mix = am._mix_tracks(source)
    spans: list[tuple[float, float, str]] = []
    with heavy_slot("moment_signals", f"немые реакции исходника {source_id}"):
        lo = 0.0
        while lo < total:
            hi = min(total, lo + CHUNK_SEC)
            x = am._extract(path, [(lo, hi)], mix, [])
            part = [w for w in words if w[1] > lo and w[0] < hi]
            spans += chunk_spans(am.frame_features, am.label_spans, x, part, lo)
            del x
            lo = hi
    reactions = find_silent_reactions(words, spans)
    data = {"stamp": _stamp(source, words), "reactions": reactions, "signal": reaction_signal(reactions, total)}
    try:
        _cache_path(source_id).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return data
