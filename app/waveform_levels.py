"""Огибающие слоёв волны голоса (``waveform``) со своим звуком — для ``comp.levels["wave:<id слоя>"]``.

Волна с ``source`` auto (по умолчанию) и ``audio_layer`` (id звукового слоя клипа) или ``asset_id`` (файл
из «Файлов для монтажа») двигается громкостью этого звука. Сервер читает его так же, как голос PNGtuber и
``react`` (app/pngtuber.py, app/audio_react.py): моно 8 кГц, громкость кадра — RMS окна 1/fps в дБ, затем
огибающая 0..100 (``audio_react.envelope``). Кадры — по длине слоя волны, ``t0`` — его начало; секунда
файла: у звукового слоя — ``offset + (t − start)`` внутри его ``start..end``, у файла — ``from + (t − start
волны)``. Время слоёв — в той же шкале, что пришли слои (clip у превью и кадра агента, final у рендера).
Движок (frontend/src/motion/layers/waveform.ts) только читает массив: превью = рендер.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

WAVE_PREFIX = "wave:"
# Дольше — тишина (слой волны на десятки минут — ошибка в данных).
MAX_WAVE_SEC = 20 * 60.0


def wave_key(layer_id: Any) -> str:
    return f"{WAVE_PREFIX}{layer_id}"


def own_sound(layer: dict) -> bool:
    """Волна слушает свой звук (слой audio или файл), а не огибающую клипа."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    return (layer.get("type") == "waveform" and not layer.get("hidden") and props.get("source", "auto") == "auto"
            and bool(props.get("audio_layer") or props.get("asset_id")))


def wave_layers(layers: Any) -> list[dict]:
    return [layer for layer in layers or [] if isinstance(layer, dict) and own_sound(layer)]


def _file_of(store: Any, ref: Any, notes: list[str], who: str) -> Path | None:
    """Путь к звуку: id файла для монтажа или ``sfx:<pack>/<file>``."""
    from app.motion_render import resolve_pack_ref

    if isinstance(ref, str) and not ref.isdigit():
        path = resolve_pack_ref(ref)
    else:
        try:
            asset = store.get_montage_asset(int(ref))
        except (KeyError, TypeError, ValueError):
            asset = None
        path = Path(str(asset.get("file_path") or "")) if asset else None
    if path is None or not path.is_file():
        notes.append(f"{who}: звука {ref} нет — волна по образцу")
        return None
    return path


def _reader(path: Path) -> Callable[[Any, float, float], bytes]:
    from uuid import uuid4

    from app.pngtuber import PCM_RATE, _cached, _wav_pcm
    from app.settings import settings
    from app.source_audio import extract_args

    def read(_sid: Any, start: float, length: float) -> bytes:
        key = ("wave", str(path), path.stat().st_mtime, round(start, 3), round(length, 3))
        out = settings.tmp_dir / f"wave-{uuid4().hex}.wav"
        return _cached(key, lambda: _wav_pcm(out, extract_args(path, out, None, start=start, duration=length,
                                                                rate=PCM_RATE)))

    return read


def wave_tracks(store: Any, layers: list[dict], fps: float) -> tuple[dict[str, dict], list[str]]:
    """{"wave:<id>": {t0, fps, v [0..100]}} для волн со своим звуком и пометки."""
    from app.audio_react import envelope
    from app.pngtuber import levels_for_points

    fps = max(1.0, float(fps))
    by_id = {str(layer.get("id")): layer for layer in layers or [] if isinstance(layer, dict)}
    out: dict[str, dict] = {}
    notes: list[str] = []
    for wave in wave_layers(layers):
        props = wave.get("props") or {}
        who = f"волна {wave.get('id')}"
        start = float(wave.get("start") or 0.0)
        end = min(float(wave.get("end") or start), start + MAX_WAVE_SEC)
        count = max(0, int(math.ceil((end - start) * fps - 1e-6)))
        times = [start + k / fps for k in range(count)]
        if props.get("audio_layer"):
            src = by_id.get(str(props["audio_layer"]))
            if not src or src.get("type") != "audio":
                notes.append(f"{who}: звукового слоя {props['audio_layer']} нет — волна по образцу")
                continue
            sp = src.get("props") or {}
            ref = sp.get("asset_id")
            a0 = float(src.get("start") or 0.0)
            a1 = float(src.get("end") or a0)
            offset = float(sp.get("offset") or 0.0)
            points = [(None, offset + t - a0) if a0 <= t < a1 else None for t in times]
        else:
            ref = props.get("asset_id")
            base = float(props.get("from") or 0.0)
            points = [(None, base + t - start) for t in times]
        path = _file_of(store, ref, notes, who)
        if path is None or not times:
            continue
        try:
            db = levels_for_points(points, fps, _reader(path))
        except Exception as exc:  # noqa: BLE001 - волна по образцу лучше упавшего рендера
            notes.append(f"{who}: звук не разобран ({str(exc)[:160]}) — волна по образцу")
            continue
        out[wave_key(wave.get("id"))] = {"t0": round(start, 6), "fps": fps, "v": envelope(db, fps)}
    return out, notes


__all__ = ["MAX_WAVE_SEC", "WAVE_PREFIX", "own_sound", "wave_key", "wave_layers", "wave_tracks"]
