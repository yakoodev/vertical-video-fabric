"""События игры для мем-эдита (app/meme_edit.py): что произошло в записи и когда — нок, килл, смерть, реакция.

Контракт — ``app.game_events.detect_game_events(video_path, game="apex") -> [{t, kind, score, detail}]`` и
``fight_spans(events) -> [(start, end)]`` (секунды исходника). Модуль ``app.game_events`` делает другой агент; пока его
нет в сборке, здесь запасной путь по звуку:

* дорожка игры (0) — удары ``audio_map.find_hits`` (атака + скачок громкости): обычный удар — ``shot_burst``, самый
  сильный удар каждой перестрелки — ``knock`` (догадка по звуку, в ``detail`` так и написано);
* дорожка микрофона (1, если она есть) — громкие всплески голоса: ``reaction`` (крик, смех, «да ладно!»).

События можно передать и явно (``events``) — тогда ничего не ищется. Время — секунды исходника.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

_log = logging.getLogger(__name__)

KINDS = ("knock", "kill", "death", "squad_wipe", "win", "level_up", "max_level", "shot_burst", "reaction", "ult",
         "teammate_fail")
CHUNK_SEC = 300.0  # звук читаем окнами: 4-часовая запись целиком в память не грузится
MAX_ANALYZE_SEC = 3 * 3600.0
HITS_PER_CHUNK = 60
FIGHT_GAP_SEC = 6.0  # удары ближе — одна перестрелка
FIGHT_PAD_SEC = 2.0
MIN_FIGHT_HITS = 2
REACTION_OVER_DB = 14.0  # голос громче своей медианы — всплеск (крик, смех)
REACTION_MIN_SEC = 0.35
REACTION_SEP_SEC = 4.0


def normalize_events(raw: Any, duration: float | None = None) -> list[dict]:
    """События в общий вид ``{t, kind, score, detail}``, по времени; мусор и время за концом записи — прочь."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            t = float(item.get("t"))
        except (TypeError, ValueError):
            continue
        if t != t or t < 0 or (duration and t > duration):
            continue
        kind = str(item.get("kind") or "shot_burst").strip().lower() or "shot_burst"
        try:
            score = float(item.get("score", 0.5))
        except (TypeError, ValueError):
            score = 0.5
        out.append({"t": round(t, 3), "kind": kind[:32], "score": round(min(1.0, max(0.0, score)), 3),
                    "detail": str(item.get("detail") or "")[:200]})
    return sorted(out, key=lambda e: e["t"])


def cluster_spans(events: list[dict], gap: float = FIGHT_GAP_SEC, pad: float = FIGHT_PAD_SEC,
                  duration: float | None = None) -> list[tuple[float, float]]:
    """Перестрелки без app.game_events: события ближе ``gap`` — одна; края с запасом ``pad``."""
    spans: list[list[float]] = []
    for ev in sorted(events, key=lambda e: e["t"]):
        t = float(ev["t"])
        if spans and t - spans[-1][1] <= gap:
            spans[-1][1] = t
        else:
            spans.append([t, t])
    hi = duration if duration else float("inf")
    return [(round(max(0.0, a - pad), 3), round(min(hi, b + pad), 3)) for a, b in spans]


def _game_events_module() -> Any:
    import importlib  # noqa: PLC0415

    try:  # через import_module: подмена в sys.modules (тесты) видна, а атрибут пакета app — нет
        game_events = importlib.import_module("app.game_events")
    except ImportError:
        return None
    return game_events if hasattr(game_events, "detect_game_events") else None


def _span_pair(span: Any) -> tuple[float, float]:
    """Схватка app.game_events — ``{start, end, score}``; старый контракт — пара ``(start, end)``."""
    if isinstance(span, dict):
        return float(span["start"]), float(span["end"])
    a, b = span[0], span[1]
    return float(a), float(b)


def fight_spans(events: list[dict], duration: float | None = None) -> list[tuple[float, float]]:
    """Перестрелки: ``app.game_events.fight_spans``, если модуль есть, иначе склейка близких событий."""
    module = _game_events_module()
    if module is not None and hasattr(module, "fight_spans"):
        try:
            return [_span_pair(span) for span in module.fight_spans(events)]
        except Exception as exc:  # noqa: BLE001 - чужой модуль не должен ронять эдит
            _log.warning("game_events.fight_spans failed: %s", exc)
    return cluster_spans(events, duration=duration)


# --------------------------------------------------------------------------- #
# Запасной путь: по звуку
# --------------------------------------------------------------------------- #


def _track_pcm(path: Path, track: int, start: float, length: float) -> Any:
    """Моно 16 кГц одной дорожки окна записи → numpy float32 (пусто, если дорожки нет)."""
    import numpy as np

    from app.settings import settings
    from app.speech_onsets import wav_pcm

    out = Path(settings.tmp_dir) / f"memeedit-{uuid4().hex}.wav"
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}",
             "-i", str(path), "-map", f"0:a:{int(track)}", "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
             str(out)], capture_output=True, timeout=600, check=False)
        if proc.returncode != 0 or not out.is_file():
            return np.zeros(0, dtype=np.float32)
        raw = wav_pcm(out)
        return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    finally:
        out.unlink(missing_ok=True)


def hits_to_events(hits: list[dict], t0: float = 0.0) -> list[dict]:
    """Удары окна игры → события: ``shot_burst``; сила — по скачку и самой громкости удара."""
    out = []
    for hit in hits:
        jump = float(hit.get("jump_db") or 0.0)
        level = float(hit.get("db") if hit.get("db") is not None else -30.0)
        strength = 0.5 * min(1.0, max(0.0, (jump - 4.0) / 24.0)) + 0.5 * min(1.0, max(0.0, (level + 40.0) / 34.0))
        out.append({"t": round(t0 + float(hit["t"]), 3), "kind": "shot_burst",
                    "score": round(max(0.1, strength), 3), "detail": f"удар +{jump:g} дБ, {level:g} дБ"})
    return out


def mark_fight_peaks(events: list[dict]) -> list[dict]:
    """Самый сильный удар перестрелки (≥ ``MIN_FIGHT_HITS`` ударов) → ``knock`` (догадка по звуку)."""
    shots = [e for e in events if e["kind"] == "shot_burst"]
    out = [dict(e) for e in events]
    for a, b in cluster_spans(shots, pad=0.0):
        inside = [i for i, e in enumerate(out) if e["kind"] == "shot_burst" and a <= e["t"] <= b]
        if len(inside) < MIN_FIGHT_HITS:
            continue
        best = max(inside, key=lambda i: out[i]["score"])
        out[best] = {**out[best], "kind": "knock", "score": round(min(1.0, out[best]["score"] + 0.2), 3),
                     "detail": "пик перестрелки по звуку (догадка: нок или килл)"}
    return out


def voice_reactions(rms_db: Any, hop: float, t0: float = 0.0) -> list[dict]:
    """Всплески микрофона: громче медианы дорожки на ``REACTION_OVER_DB`` не меньше ``REACTION_MIN_SEC`` подряд."""
    import numpy as np

    rms = np.asarray(rms_db, dtype=float)
    if len(rms) < 10:
        return []
    voiced = rms[rms > -60]
    if len(voiced) < 10:
        return []
    floor = float(np.median(voiced))
    loud = rms > floor + REACTION_OVER_DB
    out: list[dict] = []
    i, n, need = 0, len(rms), max(1, int(round(REACTION_MIN_SEC / hop)))
    while i < n:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < n and loud[j]:
            j += 1
        if j - i >= need:
            peak = float(rms[i:j].max())
            t = t0 + i * hop
            if not out or t - out[-1]["t"] >= REACTION_SEP_SEC:
                out.append({"t": round(t, 3), "kind": "reaction",
                            "score": round(min(1.0, (peak - floor - REACTION_OVER_DB) / 14.0 + 0.4), 3),
                            "detail": f"голос громче обычного на {peak - floor:.0f} дБ"})
        i = j
    return out


def audio_events(path: Path, duration: float, mic: int | None, *,
                 pcm: Callable[[Path, int, float, float], Any] = _track_pcm) -> list[dict]:
    """События по звуку записи: удары игры (дорожка 0) и всплески голоса (дорожка ``mic``)."""
    from app.audio_map import HOP, find_hits, frame_features

    events: list[dict] = []
    t, end = 0.0, min(float(duration or 0.0), MAX_ANALYZE_SEC)
    while t < end - 0.5:
        length = min(CHUNK_SEC, end - t)
        game = pcm(path, 0, t, length)
        if len(game):
            events += hits_to_events(find_hits(frame_features(game), top=HITS_PER_CHUNK), t)
        if mic is not None:
            voice = pcm(path, mic, t, length)
            if len(voice):
                events += voice_reactions(frame_features(voice)["rms_db"], HOP, t)
        t += length
    return mark_fight_peaks(normalize_events(events, duration))


def detect_events(source: dict, *, game: str = "apex", mic: int | None = None) -> tuple[list[dict], str]:
    """События записи: ``app.game_events`` (если есть в сборке), иначе по звуку. Ответ — (события, откуда)."""
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ValueError("файла записи нет — события искать не по чему")
    duration = float(source.get("duration_sec") or 0.0)
    module = _game_events_module()
    if module is not None:
        try:
            return normalize_events(module.detect_game_events(str(path), game=game), duration), "game_events"
        except Exception as exc:  # noqa: BLE001 - чужой детектор упал — едем по звуку
            _log.warning("game_events.detect_game_events failed, falling back to audio: %s", exc)
    return audio_events(path, duration, mic), "audio"
