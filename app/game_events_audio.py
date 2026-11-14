"""Звуковые события игровой записи (app/game_events.py): очереди выстрелов и реакции в микрофон.

* ``shot_burst`` — звук игры (дорожка 0): подряд ≥ 3 атаки (spectral flux выше медианы + 3·MAD со скачком
  громкости ≥ 4 дБ) с шагом ≤ 0.45 с, громче фона. Очереди ближе 1.5 с сливаются.
* ``reaction`` — микрофон (дорожка 1 в записях NVIDIA; её нет — реакций нет): громкий голос на 8+ дБ
  выше обычной речи игрока, держится ≥ 0.3 с; деталь — «смех?» (пульсация громкости 3.5–6.5 Гц)
  или «крик».

Признаки — ``app.audio_map.frame_features`` (окно 0.1 с, шаг 0.05 с). Звук читается кусками по 120 с:
4-часовой VOD целиком в память не ложится.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Iterator

RATE = 16000
CHUNK_SEC = 120.0
OVERLAP_SEC = 2.0
ONSET_JUMP_DB = 4.0
BURST_MIN_SHOTS = 3
BURST_MAX_GAP = 0.45
BURST_MERGE = 1.5
REACT_OVER_DB = 8.0
REACT_MIN_SEC = 0.3
REACT_MERGE = 1.5


def _pcm_cmd(path: Path, tracks: list[int], start: float, end: float) -> list[str]:
    """ffmpeg: дорожки ``tracks`` моно 16 кГц, переплетённые каналами одного потока s16le (один проход файла)."""
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{max(0.0, end - start):.3f}",
           "-i", str(path), "-vn", "-sn", "-dn"]
    if len(tracks) == 1:
        cmd += ["-map", f"0:a:{tracks[0]}", "-ac", "1"]
    else:
        parts = "".join(f"[0:a:{k}]aresample={RATE},aformat=channel_layouts=mono[a{i}];" for i, k in enumerate(tracks))
        ins = "".join(f"[a{i}]" for i in range(len(tracks)))
        cmd += ["-filter_complex", f"{parts}{ins}amerge=inputs={len(tracks)}[out]", "-map", "[out]"]
    return cmd + ["-ar", str(RATE), "-f", "s16le", "-"]


def _pcm_chunks(path: Path, tracks: list[int], start: float, end: float) -> Iterator[tuple[float, Any]]:
    """(t0, float32 [samples, len(tracks)]) кусками CHUNK_SEC с перекрытием OVERLAP_SEC — потоком из ffmpeg."""
    import numpy as np

    ch = len(tracks)
    proc = subprocess.Popen(_pcm_cmd(path, tracks, start, end), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    step = int(CHUNK_SEC * RATE)
    tail = np.zeros((0, ch), dtype=np.float32)
    t0 = start - 0.0
    try:
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.read(step * ch * 2)
            if not raw:
                break
            x = np.frombuffer(raw[: len(raw) // (2 * ch) * 2 * ch], dtype="<i2").astype(np.float32).reshape(-1, ch) / 32768.0
            block = np.concatenate([tail, x])
            yield t0 - len(tail) / RATE, block
            t0 += len(x) / RATE
            tail = block[-int(OVERLAP_SEC * RATE):]
    finally:
        proc.kill()
        proc.wait()


def find_bursts(feat: dict, t0: float = 0.0) -> list[dict]:
    """Очереди выстрелов по признакам звука игры (чистая функция)."""
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
    onsets: list[int] = []
    for i in range(1, n - 1):
        if flux[i] < thr or flux[i] < flux[i - 1] or flux[i] < flux[i + 1] or rms[i + 1] < bg + 3:
            continue
        jump = float(rms[i + 1] - rms[max(0, i - back):i].min())
        if jump >= ONSET_JUMP_DB and (not onsets or i - onsets[-1] >= int(0.06 / hop)):
            onsets.append(i)
    bursts: list[list[int]] = []
    for i in onsets:
        if bursts and (i - bursts[-1][-1]) * hop <= BURST_MAX_GAP:
            bursts[-1].append(i)
        else:
            bursts.append([i])
    out = []
    for b in bursts:
        if len(b) < BURST_MIN_SHOTS:
            continue
        a, z = b[0] * hop, b[-1] * hop
        loud = float(np.max(rms[b[0]:b[-1] + 2])) - bg
        score = min(1.0, 0.35 + 0.05 * len(b) + 0.015 * max(0.0, loud))
        out.append({"t": round(t0 + a, 2), "end": round(t0 + z, 2), "kind": "shot_burst", "score": round(score, 3),
                    "detail": f"{len(b)} выстр. за {z - a:.1f} с"})
    return out


def find_reactions(feat: dict, t0: float = 0.0, ref_db: float | None = None) -> list[dict]:
    """Громкие реакции в микрофон (чистая функция). ``ref_db`` — обычная громкость речи игрока
    (медиана LUFS звучащих кадров по всей записи); нет — по этому куску."""
    import numpy as np

    from app.audio_map import SILENCE_DB

    lufs, rms, hop = feat["lufs"], feat["rms_db"], feat["hop"]
    n = len(lufs)
    if n < 8:
        return []
    voiced = rms > max(SILENCE_DB, -55.0)
    if voiced.mean() < 0.01:
        return []
    ref = float(np.median(lufs[voiced])) if ref_db is None else ref_db
    loud = voiced & (lufs > ref + REACT_OVER_DB)
    out = []
    i = 0
    while i < n:
        if not loud[i]:
            i += 1
            continue
        j = i
        while j < n and loud[j]:
            j += 1
        if (j - i) * hop >= REACT_MIN_SEC:
            seg = rms[max(0, i - 5):min(n, j + 5)]
            detail = "смех?" if _pulsing(seg, hop) else "крик"
            peak = float(np.max(lufs[i:j])) - ref
            score = min(1.0, 0.45 + 0.03 * peak + 0.1 * min(2.0, (j - i) * hop))
            out.append({"t": round(t0 + i * hop, 2), "end": round(t0 + j * hop, 2), "kind": "reaction",
                        "score": round(score, 3), "detail": f"{detail} +{peak:.0f} дБ к речи"})
        i = j
    return out


def _pulsing(seg: Any, hop: float) -> bool:
    import numpy as np

    if len(seg) < int(0.6 / hop):
        return False
    env = seg - seg.mean()
    spec = np.abs(np.fft.rfft(env)) ** 2
    freqs = np.fft.rfftfreq(len(env), hop)
    total = spec[1:].sum() + 1e-9
    return float(spec[(freqs >= 3.5) & (freqs <= 6.5)].sum() / total) > 0.35


def _merge(events: list[dict], gap: float) -> list[dict]:
    out: list[dict] = []
    for e in sorted(events, key=lambda e: e["t"]):
        if out and e["t"] - out[-1]["end"] <= gap:
            last = out[-1]
            last["end"] = max(last["end"], e["end"])
            if e["score"] > last["score"]:
                last.update(score=e["score"], detail=e["detail"])
        else:
            out.append(dict(e))
    return out


def audio_events(path: Path, start: float, end: float, audio_tracks: int, *, mic_track: int = 1) -> list[dict]:
    """Выстрелы по дорожке 0, реакции по ``mic_track`` (если такая дорожка есть)."""
    from app.audio_map import frame_features

    if audio_tracks <= 0:
        return []
    tracks = [0, mic_track] if audio_tracks > mic_track else [0]
    bursts: list[dict] = []
    mic_feats: list[tuple[float, float, dict]] = []
    for t0, block in _pcm_chunks(path, tracks, start, end):
        # Начало куска — перекрытие с прошлым: найденное там уже нашёл прошлый кусок.
        fresh = t0 + (OVERLAP_SEC if t0 > start + 1e-6 else 0.0)
        bursts.extend(e for e in find_bursts(frame_features(block[:, 0], RATE), t0) if e["t"] >= fresh - 1e-6)
        if len(tracks) > 1:
            mic_feats.append((t0, fresh, frame_features(block[:, 1], RATE)))
    events = _merge(bursts, BURST_MERGE)
    if mic_feats:
        ref = _speech_ref([(t0, f) for t0, _fresh, f in mic_feats])
        reacts: list[dict] = []
        for t0, fresh, feat in mic_feats:
            reacts.extend(e for e in find_reactions(feat, t0, ref) if e["t"] >= fresh - 1e-6)
        events.extend(_merge(reacts, REACT_MERGE))
    return [{k: e[k] for k in ("t", "kind", "score", "detail")} for e in events]


def _speech_ref(feats: list[tuple[float, dict]]) -> float | None:
    """Обычная громкость голоса по всей записи (медиана LUFS звучащих кадров микрофона)."""
    import numpy as np

    from app.audio_map import SILENCE_DB

    vals = [f["lufs"][f["rms_db"] > max(SILENCE_DB, -55.0)] for _, f in feats]
    vals = [v for v in vals if len(v)]
    if not vals:
        return None
    return float(np.median(np.concatenate(vals)))
