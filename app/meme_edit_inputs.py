"""Мем-эдит: что берётся из записей, мемов и музыки (медиа и кэш; чистая раскладка — app/meme_edit_plan.py).

* ``source_events`` — события игры (app/game_events_job: скан в очереди тяжёлых, кэш на источник);
* ``shot_spans`` — где стреляют (звук игры, дорожка 0): очереди и одиночные громкие выстрелы (снайперка, крабер) —
  по ним начало боя «с первых выстрелов»; детектор событий отдаёт только редкие очереди;
* ``track_words`` — слова дорожки (Whisper large-v3-turbo по окну записи): микрофон — игрок (голос A), звук игры —
  тиммейты (голос B); кэш по файлу, дорожке и окну;
* ``meme_library`` — одобренные видеомемы с файлом, словами и звучащими отрезками (app/meme_trim.voiced_spans);
* ``music_file`` / ``song_grid`` — трек и его сетка долей по всей песне (темп уточнён по атакам всего трека, иначе
  за 5 минут сетка уезжает), рабочая часть (с энергичного начала до затухания).
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

_log = logging.getLogger(__name__)

# Подмена в тестах: (wav_path) -> [{word, start, end}]. None — настоящий Whisper.
TRANSCRIBE: Callable[[Path], list[dict]] | None = None
SINGLE_JUMP_DB = 12.0   # одиночный выстрел: скачок громкости не меньше …
SINGLE_OVER_BG_DB = 14.0  # … и громче фона дорожки на столько
SHOT_GAP = 0.6
VERSION = 1


def _cache_dir() -> Path:
    from app.settings import settings

    folder = settings.runtime_dir / "meme_edit"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _key(*parts: Any) -> str:
    return hashlib.sha1(json.dumps(parts, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:24]


def _stamp(path: Path) -> list:
    st = path.stat()
    return [str(path.resolve()), st.st_size, int(st.st_mtime)]


def _cached(name: str, build: Callable[[], Any]) -> Any:
    file = _cache_dir() / name
    if file.is_file():
        try:
            return json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
    value = build()
    try:
        file.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return value


def source_path(source: dict) -> Path:
    path = Path(str(source.get("local_path") or ""))
    if not str(source.get("local_path") or "") or not path.is_file():
        raise ValueError(f"у записи {source.get('id')} нет файла видео (не скачана или удалена)")
    return path


# --------------------------------------------------------------------------- #
# События и выстрелы
# --------------------------------------------------------------------------- #


def source_events(store: Any, source_id: int, game: str = "apex") -> list[dict]:
    """События игры записи (ждёт скан, если его ещё не было)."""
    from app.game_events_job import game_events_job

    out = game_events_job(store, int(source_id), game=game, wait=6 * 3600)
    if out.get("status") != "done":
        raise ValueError(f"события записи {source_id} не найдены: {out.get('note') or out.get('status')}")
    return list(out.get("events") or [])


def find_shots(feat: dict, t0: float = 0.0) -> list[list[float]]:
    """Отрезки стрельбы по признакам звука игры (чистая функция): атаки (spectral flux выше медианы + 3·MAD со
    скачком ≥ 4 дБ) ближе SHOT_GAP — одна очередь (≥ 2 атак); одиночная атака — выстрел, только если громкая
    (скачок ≥ SINGLE_JUMP_DB и громче фона на SINGLE_OVER_BG_DB): снайперка и крабер — тоже первый контакт."""
    import numpy as np

    flux, rms, hop = feat["flux"], feat["rms_db"], feat["hop"]
    n = len(flux)
    if n < 8:
        return []
    med = float(np.median(flux))
    mad = float(np.median(np.abs(flux - med))) * 1.4826 + 1e-6
    thr = med + 3 * mad
    back = max(1, int(round(0.15 / hop)))
    bg = float(np.percentile(rms, 40))
    onsets: list[tuple[int, float, float]] = []
    for i in range(1, n - 1):
        if flux[i] < thr or flux[i] < flux[i - 1] or flux[i] < flux[i + 1] or rms[i + 1] < bg + 3:
            continue
        jump = float(rms[i + 1] - rms[max(0, i - back):i].min())
        if jump >= 4.0 and (not onsets or i - onsets[-1][0] >= int(0.06 / hop)):
            onsets.append((i, jump, float(rms[i + 1]) - bg))
    groups: list[list[tuple[int, float, float]]] = []
    for o in onsets:
        if groups and (o[0] - groups[-1][-1][0]) * hop <= SHOT_GAP:
            groups[-1].append(o)
        else:
            groups.append([o])
    out = []
    for g in groups:
        if len(g) == 1 and not (g[0][1] >= SINGLE_JUMP_DB and g[0][2] >= SINGLE_OVER_BG_DB):
            continue
        a, z = g[0][0] * hop, g[-1][0] * hop + 0.25
        out.append([round(t0 + a, 2), round(t0 + z, 2)])
    return out


def shot_spans(source: dict) -> list[list[float]]:
    """Стрельба записи (звук игры, дорожка 0), кэш по файлу."""
    from app.audio_map import frame_features
    from app.game_events_audio import OVERLAP_SEC, RATE, _pcm_chunks

    path = source_path(source)
    duration = float(source.get("duration_sec") or 0.0)

    def build() -> list[list[float]]:
        spans: list[list[float]] = []
        for t0, block in _pcm_chunks(path, [0], 0.0, duration):
            fresh = t0 + (OVERLAP_SEC if t0 > 1e-6 else 0.0)
            spans += [s for s in find_shots(frame_features(block[:, 0], RATE), t0) if s[0] >= fresh - 1e-6]
        merged: list[list[float]] = []
        for a, b in sorted(spans):
            if merged and a - merged[-1][1] <= SHOT_GAP:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return merged

    return _cached(f"shots-{_key(_stamp(path), VERSION)}.json", build)


# --------------------------------------------------------------------------- #
# Слова дорожек
# --------------------------------------------------------------------------- #


def _whisper(path: Path) -> list[dict]:
    from app.subtitles.registry import QUALITY_WHISPER_MODEL
    from app.subtitles.whisper_local import WhisperSubtitleProvider

    try:
        result = WhisperSubtitleProvider().transcribe(path, {"language": "ru"}, QUALITY_WHISPER_MODEL)
    except RuntimeError:  # «нет слов» — речи нет
        return []
    return [{"word": w.word.strip(), "start": round(float(w.start), 3), "end": round(float(w.end), 3)}
            for w in result.words if str(w.word or "").strip()]


def track_words(source: dict, track: int, lo: float, hi: float) -> list[dict]:
    """Слова дорожки ``track`` на окне [lo, hi] записи (секунды записи); фантомы Whisper («Продолжение следует…»)
    выброшены. Кэш по файлу, дорожке и окну."""
    from app.settings import settings
    from app.subtitles.phantoms import is_phantom_text

    path = source_path(source)
    lo, hi = max(0.0, float(lo)), float(hi)

    def build() -> list[dict]:
        wav = Path(settings.tmp_dir) / f"memeedit-{uuid4().hex}.wav"
        wav.parent.mkdir(parents=True, exist_ok=True)
        try:
            proc = subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-ss", f"{lo:.3f}", "-t", f"{hi - lo:.3f}",
                                   "-i", str(path), "-map", f"0:a:{int(track)}", "-vn", "-ac", "1", "-ar", "16000",
                                   str(wav)], capture_output=True, timeout=600, check=False)
            if proc.returncode != 0 or not wav.is_file():
                return []
            words = (TRANSCRIBE or _whisper)(wav)
        finally:
            wav.unlink(missing_ok=True)
        return [{"word": str(w["word"]).strip(), "start": round(lo + float(w["start"]), 3),
                 "end": round(lo + float(w["end"]), 3)} for w in words if str(w.get("word") or "").strip()]

    if TRANSCRIBE is not None:
        words = build()
    else:
        words = _cached(f"words-{_key(_stamp(path), track, round(lo, 2), round(hi, 2), VERSION)}.json", build)
    return [w for w in words if not is_phantom_text(w["word"])]


def drop_bleed(team: list[dict], owner: list[dict]) -> list[dict]:
    """Слова звука игры, совпавшие со словами микрофона по времени и тексту, — это голос игрока, протёкший в игру
    (или эхо), а не тиммейт."""
    import re

    def norm(w: Any) -> str:
        return re.sub(r"[^a-zа-я0-9]", "", str(w).lower().replace("ё", "е"))

    out = []
    for w in team:
        a, b, t = float(w["start"]), float(w["end"]), norm(w["word"])
        if any(float(o["start"]) < b + 0.15 and float(o["end"]) > a - 0.15 and (norm(o["word"]) == t or not t)
               for o in owner):
            continue
        out.append(w)
    return out


def spans_union(spans: list[list[float]], pad_before: float, pad_after: float, duration: float) -> list[list[float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        a, b = max(0.0, a - pad_before), min(duration, b + pad_after)
        if out and a <= out[-1][1] + 2.0:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


# --------------------------------------------------------------------------- #
# Мемы
# --------------------------------------------------------------------------- #


def meme_library(store: Any) -> list[dict]:
    """Одобренные видеомемы с файлом (только их можно ставить) + звучащие отрезки для подрезки удара."""
    from app import meme_trim
    from app import video_memes as VM

    out = []
    for meme in VM.list_memes(store, approved=True):
        path = Path(str(meme.get("file_path") or ""))
        if not path.is_file() or not meme.get("duration_sec"):
            continue
        out.append({**meme, "meme_id": int(meme["id"]), "voiced": meme_trim.voiced_spans(path)})
    return out


# --------------------------------------------------------------------------- #
# Музыка
# --------------------------------------------------------------------------- #


def music_file(store: Any, music_track_id: Any, music_asset_id: Any) -> tuple[dict | None, list[str]]:
    """Трек фона: ``music_track_id`` (list_music), звук библиотеки ``music_asset_id`` или сам — трек с темпом ближе к
    128 BPM. Ответ — ({kind, id, path, label, start_hint, length} | None, заметки)."""
    notes: list[str] = []
    if music_asset_id:
        asset = store.get_montage_asset(int(music_asset_id))
        if asset.get("kind") != "audio":
            raise ValueError(f"файл {music_asset_id} — не звук (kind {asset.get('kind')})")
        return {"kind": "asset", "id": int(asset["id"]), "path": asset["file_path"], "label": asset.get("label"),
                "start_hint": 0.0, "length": float(asset.get("duration_sec") or 0)}, notes
    tracks = store.list_audio_tracks()
    if music_track_id:
        track = store.get_audio_track(int(music_track_id))
    elif tracks:
        from app.music_energy import ensure_energy

        tracks = ensure_energy(store, tracks)
        track = min(tracks, key=lambda t: abs(float(t.get("bpm") or 60) - 128.0))
        notes.append(f"музыка не задана — взял «{track.get('label')}» (темп ближе к 128 BPM); своя — music_track_id")
    else:
        notes.append("музыки в библиотеке нет — эдит без подложки, склейки по сетке 120 BPM")
        return None, notes
    hint = 0.0
    for key in ("energy_start_sec", "drop_sec"):
        value = track.get(key)
        if value is not None and float(value) < float(track.get("duration_sec") or 0) - 30:
            hint = float(value)
            break
    return {"kind": "track", "id": int(track["id"]), "path": track["file_path"], "label": track.get("label"),
            "start_hint": hint, "length": float(track.get("duration_sec") or 0)}, notes


def refine_grid(onset: Any, hop: float, period: float, lo: float = 0.0, hi: float | None = None) -> tuple[float, float]:
    """Темп и фаза по атакам всего трека (чистая функция): период ±1 % от ``period`` и фаза, при которых сумма
    атак на долях на [lo, hi] больше всего. Ответ — (период, секунда первой доли ≥ lo)."""
    import numpy as np

    onset = np.asarray(onset, dtype=np.float64)
    n = len(onset)
    hi = n * hop if hi is None else min(hi, n * hop)
    best = (-1.0, period, lo)
    for p in np.linspace(period * 0.99, period * 1.01, 161):
        for phase in np.arange(0.0, p, 0.005):
            idx = np.round((lo + phase + np.arange(0, (hi - lo - phase) / p) * p) / hop).astype(int)
            idx = idx[(idx >= 0) & (idx < n)]
            if not len(idx):
                continue
            s = float(onset[idx].mean())
            if s > best[0]:
                best = (s, float(p), float(lo + phase))
    return round(best[1], 5), round(best[2], 3)


def song_grid(path: str | Path, start_hint: float = 0.0) -> dict | None:
    """Сетка долей всего трека: ``{period, bpm, downbeat, usable_sec, end, confidence}`` — ``downbeat`` — сильная
    доля начала рабочей части (секунды трека), ``usable_sec`` — сколько трека от неё до затухания (на целое число
    тактов). None — пульса не слышно."""
    import numpy as np

    from app.audio_map import beat_grid

    src = Path(path)

    def pcm(af: str) -> Any:
        proc = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(src), "-t", "900", "-vn", "-af", af,
                               "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, timeout=300,
                              check=False)
        raw = proc.stdout or b""
        return np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0

    def envelope(x: Any, step: int) -> Any:
        count = len(x) // step
        return np.log10(np.mean(x[: count * step].astype(np.float64).reshape(count, step) ** 2, axis=1) + 1e-10)

    def build() -> dict | None:
        x = pcm("anull")
        rate = 16000
        if len(x) < rate * 10:
            return None
        s0 = int(max(0.0, start_hint) * rate)
        base = beat_grid(x[s0:s0 + rate * 70], rate)
        if not base:
            return None
        hop = 0.01
        step = int(rate * hop)
        count = len(x) // step
        energy = envelope(x, step)
        # доли — по бочке (низ до 150 Гц): по всему спектру сетка цепляется за слабую долю (хэт, малый)
        kick = envelope(pcm("lowpass=f=150,lowpass=f=150"), step)[:count]
        onset = np.maximum(0.0, np.diff(kick, prepend=kick[0]))
        count = min(count, len(onset))
        dur = count * hop
        # конец рабочей части: дальше громкость (окно 2 с) ниже медианы на 10 дБ до самого конца — затухание
        win = int(2.0 / hop)
        level = np.convolve(energy, np.ones(win) / win, mode="same")
        med = float(np.median(level[int(start_hint / hop):])) if count > int(start_hint / hop) + win else float(np.median(level))
        loud = np.where(level >= med - 1.0)[0]  # log10-энергия: 1.0 = 10 дБ
        end = min(dur - 2.0, float(loud[-1]) * hop if len(loud) else dur - 2.0)
        lo = max(0.0, start_hint)
        period, first = refine_grid(onset, hop, float(base["period"]), lo, end)
        # сильная доля — каждая 4-я: где атаки и громкость сильнее
        beats = np.arange(first, end, period)
        strong, strong_k = -1.0, 0
        for k in range(4):
            idx = np.round(beats[k::4] / hop).astype(int)
            idx = idx[idx < count]
            score = float(onset[idx].sum() + energy[idx].sum() * 0.01) / max(1, len(idx))
            if score > strong:
                strong, strong_k = score, k
        downbeat = float(first + strong_k * period)
        bars = int((end - downbeat) // (4 * period))
        return {"period": round(period, 5), "bpm": round(60.0 / period, 2), "downbeat": round(downbeat, 3),
                "usable_sec": round(bars * 4 * period, 3), "end": round(end, 2), "duration": round(dur, 2),
                "confidence": base.get("confidence")}

    if not src.is_file():
        return None
    return _cached(f"grid-{_key(_stamp(src), round(start_hint, 2), VERSION)}.json", build)
