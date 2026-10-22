"""Звуковые дорожки исходника: какие брать и как смешивать.

Запись NVIDIA DVR (и OBS с раздельными дорожками) несёт 2 звука: игру и микрофон.
Сервис всегда брал только первый (``0:a:0``) — голос стримера терялся целиком.
Выбор хранится у ИСТОЧНИКА (``sources.audio_tracks``, JSON ``[0]`` / ``[0, 1]``):
один раз на запись, а не на каждый клип. ``None`` — как раньше (первая дорожка,
и смешивание по пресету рендера, если оно там задано).

Здесь — нормализация, разбор дорожек из метаданных и куски графа ffmpeg; их
используют рендер (``app.render._audio_filter``), звук для распознавания
(``app.clip_subtitles.build_clip_audio_args``), расшифровка исходника и профиль
громкости.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

MAX_TRACKS = 8


def normalize_tracks(raw: Any) -> list[int] | None:
    """``[0, 1]`` / ``"0,1"`` / ``1`` → отсортированный список без повторов; пусто → None."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except ValueError:
                return None
        else:
            raw = [part for part in text.replace(";", ",").split(",") if part.strip()]
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return None
    out: list[int] = []
    for item in raw:
        try:
            index = int(item)
        except (TypeError, ValueError):
            continue
        if 0 <= index < MAX_TRACKS and index not in out:
            out.append(index)
    return sorted(out) or None


def audio_streams(source: dict, probe: bool = True) -> list[dict]:
    """Звуковые дорожки исходника ``[{index, title, channels, language}]`` (index — номер
    среди звуковых, как в ``0:a:N``). Из метаданных загрузки, иначе — ffprobe по файлу."""
    streams = _streams_from_metadata(source.get("metadata_json") or source.get("metadata"))
    if streams is None and probe:
        streams = _probe_streams(source.get("local_path"))
    out = []
    for n, stream in enumerate(s for s in streams or [] if s.get("codec_type") == "audio"):
        tags = stream.get("tags") if isinstance(stream.get("tags"), dict) else {}
        title = str(tags.get("title") or tags.get("handler_name") or "").strip()
        out.append({"index": n, "title": title[:60], "channels": stream.get("channels"),
                    "language": str(tags.get("language") or "")[:8]})
    return out


def _streams_from_metadata(raw: Any) -> list[dict] | None:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw) if raw.strip() else None
        except ValueError:
            raw = None
    if isinstance(raw, dict) and isinstance(raw.get("streams"), list):
        return [s for s in raw["streams"] if isinstance(s, dict)]
    return None


def _probe_streams(local_path: Any) -> list[dict] | None:
    if not local_path or not Path(str(local_path)).is_file():
        return None
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(local_path)],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return _streams_from_metadata(proc.stdout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def source_tracks(source: dict) -> list[int] | None:
    """Выбранные дорожки источника, годные для этого файла; None — по умолчанию (первая).

    Номера за пределами файла отбрасываются (запись заменили — ffmpeg не упадёт)."""
    tracks = normalize_tracks(source.get("audio_tracks"))
    if not tracks:
        return None
    streams = _streams_from_metadata(source.get("metadata_json") or source.get("metadata"))
    if streams is not None:  # метаданных нет — верим выбору (ffprobe на каждый рендер не гоняем)
        total = sum(1 for s in streams if s.get("codec_type") == "audio")
        tracks = [t for t in tracks if t < total]
    return tracks or None


def audio_view(source: dict) -> dict:
    """Что показать агенту: сколько дорожек, какие выбраны, как их сменить."""
    streams = audio_streams(source)
    chosen = normalize_tracks(source.get("audio_tracks"))
    view: dict = {"tracks_total": len(streams), "selected": chosen or [0], "streams": streams}
    if len(streams) > 1:
        view["note"] = ("у записи несколько звуковых дорожек (часто игра и микрофон): выбранные смешиваются "
                        "везде — рендер, субтитры, listen, громкость. Сменить — set_source_audio(project_id, tracks)")
    return view


def mix_chain(input_index: int, tracks: list[int] | None, out_label: str) -> str:
    """Граф: дорожки ``tracks`` входа ``input_index`` → одна в ``[out_label]``.

    Одна дорожка — просто она; несколько — amix (нормирует на число входов: сумма
    игры и микрофона не перегружается, громкость потом выравнивает рендер)."""
    tracks = tracks or [0]
    if len(tracks) == 1:
        return f"[{input_index}:a:{tracks[0]}]anull[{out_label}]"
    inputs = "".join(f"[{input_index}:a:{t}]" for t in tracks)
    return f"{inputs}amix=inputs={len(tracks)}:duration=longest:dropout_transition=0[{out_label}]"


def extract_args(source_path: Path, out_path: Path, tracks: list[int] | None, *, start: float | None = None,
                 duration: float | None = None, rate: int = 16000, fmt: list[str] | None = None) -> list[str]:
    """ffmpeg: звук окна исходника (быстрый поиск ``-ss``/``-t`` до ``-i``) моно ``rate`` Гц.

    Читает только нужные секунды: 4-часовой исходник целиком не декодируется."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start is not None:
        args += ["-ss", f"{max(0.0, float(start)):.3f}"]
    if duration is not None:
        args += ["-t", f"{max(0.01, float(duration)):.3f}"]
    args += ["-i", str(source_path)]
    if tracks and tracks != [0]:
        args += ["-filter_complex", mix_chain(0, tracks, "a"), "-map", "[a]"]
    else:
        args += ["-map", "0:a:0"]
    args += ["-vn", "-ac", "1", "-ar", str(int(rate))]
    args += fmt if fmt is not None else ["-c:a", "pcm_s16le"]
    args.append(str(out_path))
    return args


def set_source_audio(store: Any, source_id: int, tracks: Any) -> dict:
    """Выбрать дорожки источника (REST и MCP). ``[]``/None — вернуть первую.

    Номер за пределами файла — ошибка (ValueError), а не тихий «звук пропал» на рендере."""
    source = store.get_source(int(source_id))
    if tracks not in (None, [], "") and normalize_tracks(tracks) is None:
        raise ValueError("tracks — список номеров звуковых дорожек с 0, например [0] или [0, 1]")
    chosen = normalize_tracks(tracks)
    total = len(audio_streams(source))
    if chosen and total and max(chosen) >= total:
        raise ValueError(f"в записи {total} звуковых дорожек (0..{total - 1}), а выбрано {chosen}")
    if chosen and not total and chosen != [0]:
        raise ValueError("в записи не нашлось звуковых дорожек")
    saved = store.update_source(int(source_id), audio_tracks=chosen)
    return {
        "ok": True,
        "project_id": saved["id"],
        "audio": audio_view(saved),
        "note": ("выбранные дорожки берут рендер, субтитры, listen, find_noises и source_loudness; "
                 "расшифровка исходника сброшена — get_source_transcript распознает новый звук заново. "
                 "Субтитры уже сделанных клипов — generate_subtitles"),
    }
