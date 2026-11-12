"""Звук холста для распознавания и карты звука: озвучка и звуки слоёв, сведённые в один wav.

У холста (app/canvas.py) звук основы — тишина: вся речь — в слоях ``audio`` (озвучка TTS, реплики) и звуке
видео-слоёв. Распознавание резало звук из основы и ничего не слышало (эксперимент 12.11): generate_subtitles —
«Whisper returned no word-level timestamps», listen — «речи нет», lint_clip — ложное «голоса на этом месте нет» на
каждой строке, audio_map — тишина.

Здесь звук слоёв сводится тем же путём, что у сведения и рендера (``audio_mix_model.build_model`` →
``motion_render.audio_layer_items``: громкость, offset, фейды, цепочки), и кладётся на шкалу основы: кусок клипа
``[clip_start, clip_start + длина]`` встаёт на секунды основы ``[start_sec, end_sec]``. Дальше вызывающий режет
куски из этого wav, как из исходника (``build_clip_audio_args``): вырезы, наплывы и шкалы работают как у клипа.

* ``speech`` — для распознавания: звуки слоёв без музыки (слой с ролью или именем «музыка»; трек set_music
  не входит);
* ``mix`` — всё, что прозвучит: слои и трек set_music (громкость listen, карта звука).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from uuid import uuid4

from app.canvas import is_canvas
from app.settings import settings

NO_SOUND = ("на холсте нет звука: ни озвучки, ни звуков в слоях. Добавь слой audio с озвучкой "
            "(tts_preview → asset_id → add_layers), потом распознавай")


def _sound_layers(plan: dict) -> list[dict]:
    from app.motion import has_layer_sound

    layers = (plan.get("render_settings") or {}).get("layers") or []
    return [layer for layer in layers if isinstance(layer, dict) and _safe(has_layer_sound, layer)]


def _safe(fn: Any, *args: Any) -> bool:
    try:
        return bool(fn(*args))
    except Exception:  # noqa: BLE001 - кривой слой — не звук
        return False


def signature(store: Any, plan: dict) -> str:
    """Отпечаток звука холста: слои со звуком (и их файлы), сведение, музыка, куски. Для кэшей распознавания."""
    rs = plan.get("render_settings") or {}
    stamps = []
    for layer in _sound_layers(plan):
        asset = (layer.get("props") or {}).get("asset_id")
        try:
            row = store.get_montage_asset(int(asset)) if str(asset).isdigit() else None
            path = Path(str((row or {}).get("file_path") or ""))
            stat = path.stat() if path.is_file() else None
            stamps.append([str(asset), stat.st_size if stat else 0, int(stat.st_mtime) if stat else 0])
        except Exception:  # noqa: BLE001
            stamps.append([str(asset)])
    data = {"layers": _sound_layers(plan), "files": stamps, "mix": rs.get("mix"),
            "music": [rs.get("music_on"), rs.get("track_id"), rs.get("music_start_sec")],
            "pieces": [[float(s["start_sec"]), float(s["end_sec"])] for s in plan.get("segments") or []]}
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


def _music_layer_ids(plan: dict) -> set[str]:
    """Слои, которые сами названы музыкой (роль или имя): в распознавание они не идут."""
    from app.audio_mix_model import MUSIC_WORDS

    out = set()
    for layer in _sound_layers(plan):
        props = layer.get("props") or {}
        text = f"{layer.get('id') or ''} {layer.get('name') or ''}".lower()
        if props.get("role") == "music" or (not props.get("role") and any(w in text for w in MUSIC_WORDS)):
            out.add(str(layer.get("id") or ""))
    return out


def _on_base(stem: Path, plan: dict, dst: Path) -> Path:
    """Стем на шкале клипа → wav на шкале основы: кусок i клипа встаёт на свои секунды основы."""
    from app.audio_mix_model import _run
    from app.timebase import ClipClock

    segments = plan.get("segments") or []
    clock = ClipClock.for_plan(plan)
    spans = [(clock.clip_starts[i], clock.lengths[i], float(s["start_sec"])) for i, s in enumerate(segments)]
    if len(spans) == 1 and abs(spans[0][0]) < 1e-3 and abs(spans[0][2]) < 1e-3:
        return stem  # холст как его создаёт сервис: один кусок с нуля — шкалы совпадают
    graph = [f"[0:a]asplit={len(spans)}" + "".join(f"[i{n}]" for n in range(len(spans)))]
    for n, (clip_start, length, base_start) in enumerate(spans):
        delay = max(0, int(round(base_start * 1000)))
        graph.append(f"[i{n}]atrim=start={clip_start:.3f}:end={clip_start + length:.3f},asetpts=PTS-STARTPTS,"
                     f"adelay=delays={delay}:all=1[s{n}]")
    graph.append("".join(f"[s{n}]" for n in range(len(spans)))
                 + f"amix=inputs={len(spans)}:duration=longest:normalize=0:dropout_transition=0[out]")
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(stem), "-filter_complex", ";".join(graph),
          "-map", "[out]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(dst)])
    return dst


@contextmanager
def canvas_audio(store: Any, plan: dict, kind: str = "speech") -> Iterator[Path]:
    """Временный wav звука холста на шкале основы (``speech`` — без музыки, ``mix`` — всё). Звука нет — ValueError."""
    from app.audio_mix_model import _items_stem, build_model, mix_wavs

    rs = plan.get("render_settings") or {}
    if not _sound_layers(plan) and (kind == "speech" or not (rs.get("music_on") and rs.get("track_id"))):
        raise ValueError(NO_SOUND)
    work = Path(settings.tmp_dir) / f"canvas-audio-{uuid4().hex[:12]}"
    try:
        model = build_model(store, int(plan["id"]), work)
        duration = float(model["duration"])
        if kind == "speech":
            music = _music_layer_ids(plan)
            items = [it for it in model["items"] if str(it.get("layer_id") or "") not in music] or model["items"]
            if not items:
                raise ValueError(NO_SOUND)
            stem = _items_stem(items, duration, work / "speech.wav")
        else:
            stems = [p for p in model["stems"].values() if p]
            if not stems:
                raise ValueError(NO_SOUND)
            stem = mix_wavs(stems, work / "mix.wav", duration)
        yield _on_base(stem, plan, work / f"{kind}.base.wav")
    finally:
        shutil.rmtree(work, ignore_errors=True)


@contextmanager
def clip_audio_input(store: Any, plan: dict, source: dict, kind: str = "speech") -> Iterator[tuple[Path, Any, bool]]:
    """Откуда резать звук клипа: ``(файл, дорожки для build_clip_audio_args, холст ли)``.

    Обычный клип — исходник и его дорожки (``speech_mix`` для распознавания, ``source_mix`` — смесь рендера).
    Холст — сведённые слои (``canvas_audio``), одна дорожка."""
    from app.source_audio import source_mix, speech_mix

    if is_canvas(source):
        with canvas_audio(store, plan, kind) as path:
            yield path, None, True
        return
    path = Path(str(source.get("local_path") or ""))
    if not path.is_file():
        raise ValueError("видео недоступно")
    yield path, (speech_mix(source) if kind == "speech" else source_mix(source)), False
