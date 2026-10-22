"""Звуковые дорожки исходника: какие брать и как смешивать.

Запись NVIDIA DVR (и OBS с раздельными дорожками) несёт 2 звука: игру и микрофон.
Сервис всегда брал только первый (``0:a:0``) — голос стримера терялся целиком.
Выбор хранится у ИСТОЧНИКА (``sources.audio_tracks``, JSON ``[0]`` / ``[0, 1]``):
один раз на запись, а не на каждый клип. ``None`` — как раньше (первая дорожка,
и смешивание по пресету рендера, если оно там задано).

Громкость каждой дорожки (``[{track, gain_db}]``): amix сводит дорожки поровну, и
микрофон на 15–25 дБ тише игры в ней тонул (отзыв агента апекса). Число задаёт код
или агент по замеру (``gain_db: "auto"`` — замер громкости дорожек при выборе), а не
«на слух». Старый список номеров ``[0, 1]`` — то же с усилением 0.

Отдельная дорожка для распознавания (``sources.speech_tracks``): распознавание на смеси
игры и голоса выдумывало фразы, на одном микрофоне — чисто. Не задана — та же смесь,
что для рендера. По ней работают расшифровка исходника, субтитры клипа, listen и
сверка субтитров с речью в QC; рендер и громкость — по смеси ``audio_tracks``.

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


MIN_GAIN_DB = -30.0
MAX_GAIN_DB = 30.0
AUTO = "auto"


def _gain_db(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:  # NaN
        return 0.0
    return round(min(MAX_GAIN_DB, max(MIN_GAIN_DB, number)), 1)


def _raw_items(raw: Any) -> list | None:
    """Что угодно → список элементов (номеров или объектов); None — разобрать нельзя."""
    if raw is None or raw == "":
        return []
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
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return None
    return list(raw)


def _item_track(item: Any) -> int | None:
    value = item.get("track", item.get("index")) if isinstance(item, dict) else item
    if isinstance(value, bool):
        return None
    try:
        index = int(value)
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < MAX_TRACKS else None


def normalize_mix(raw: Any) -> list[dict] | None:
    """``[0, 1]`` / ``[{track, gain_db}]`` / ``"0,1"`` / ``1`` → ``[{track, gain_db}]`` по номеру,
    без повторов (первое упоминание дорожки); пусто → None. ``gain_db`` — −30..+30 дБ
    (``"auto"`` здесь — 0: замер делает ``set_source_audio``)."""
    items = _raw_items(raw)
    if not items:
        return None
    out: dict[int, float] = {}
    for item in items:
        track = _item_track(item)
        if track is None or track in out:
            continue
        gain = item.get("gain_db", item.get("gain", 0)) if isinstance(item, dict) else 0
        out[track] = 0.0 if gain == AUTO else _gain_db(gain)
    return [{"track": t, "gain_db": out[t]} for t in sorted(out)] or None


def normalize_tracks(raw: Any) -> list[int] | None:
    """``[0, 1]`` / ``"0,1"`` / ``1`` / ``[{track, gain_db}]`` → отсортированные номера без
    повторов; пусто → None."""
    mix = normalize_mix(raw)
    return [m["track"] for m in mix] if mix else None


def mix_storage(mix: list[dict] | None) -> list | None:
    """Как хранить смесь: без усилений — прежний список номеров ``[0, 1]``."""
    if not mix:
        return None
    if all(not m.get("gain_db") for m in mix):
        return [m["track"] for m in mix]
    return [{"track": m["track"], "gain_db": m["gain_db"]} for m in mix]


def _mix_items(tracks: Any) -> list[tuple[int, float]]:
    """Номера или ``[{track, gain_db}]`` → [(дорожка, дБ)]; пусто — первая дорожка."""
    out: list[tuple[int, float]] = []
    for item in tracks or []:
        track = _item_track(item)
        if track is None:
            continue
        gain = _gain_db(item.get("gain_db", 0)) if isinstance(item, dict) else 0.0
        out.append((track, gain))
    return out or [(0, 0.0)]


def is_default_mix(tracks: Any) -> bool:
    """Первая дорожка без усиления — прежняя команда ffmpeg (``0:a:0`` напрямую)."""
    return _mix_items(tracks) == [(0, 0.0)]


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


def _tracks_in_file(source: dict) -> int | None:
    streams = _streams_from_metadata(source.get("metadata_json") or source.get("metadata"))
    if streams is None:  # метаданных нет — верим выбору (ffprobe на каждый рендер не гоняем)
        return None
    return sum(1 for s in streams if s.get("codec_type") == "audio")


def source_mix(source: dict) -> list[dict] | None:
    """Смесь источника для рендера ``[{track, gain_db}]``, годная для этого файла; None —
    по умолчанию (первая). Номера за пределами файла отбрасываются (запись заменили —
    ffmpeg не упадёт)."""
    raw = source.get("audio_mix")
    mix = normalize_mix(raw if raw is not None else source.get("audio_tracks"))
    if not mix:
        return None
    total = _tracks_in_file(source)
    if total is not None:
        mix = [m for m in mix if m["track"] < total]
    return mix or None


def source_tracks(source: dict) -> list[int] | None:
    """Выбранные дорожки источника (номера), годные для этого файла; None — первая."""
    mix = source_mix(source)
    return [m["track"] for m in mix] if mix else None


def speech_mix(source: dict) -> list[dict] | None:
    """Звук для распознавания речи: своя дорожка (``speech_tracks``, без усилений —
    распознаванию громкость не важна), иначе та же смесь, что для рендера."""
    tracks = normalize_tracks(source.get("speech_tracks"))
    total = _tracks_in_file(source)
    if tracks and total is not None:
        tracks = [t for t in tracks if t < total]
    if tracks:
        return [{"track": t, "gain_db": 0.0} for t in tracks]
    return source_mix(source)


def separate_speech(source: dict) -> bool:
    """Речь распознаётся по своей дорожке, а не по смеси рендера."""
    speech = normalize_tracks(source.get("speech_tracks"))
    return bool(speech) and speech != (source_tracks(source) or [0])


def audio_view(source: dict) -> dict:
    """Что показать агенту: сколько дорожек, какие выбраны, с какой громкостью, как сменить."""
    streams = audio_streams(source)
    raw = source.get("audio_mix")
    mix = normalize_mix(raw if raw is not None else source.get("audio_tracks")) or [{"track": 0, "gain_db": 0.0}]
    speech = normalize_tracks(source.get("speech_tracks"))
    view: dict = {"tracks_total": len(streams), "selected": [m["track"] for m in mix], "mix": mix,
                  "speech_tracks": speech or [m["track"] for m in mix], "speech_separate": bool(speech),
                  "streams": streams}
    if len(streams) > 1:
        view["note"] = ("у записи несколько звуковых дорожек (часто 0 — игра, 1 — микрофон): выбранные смешиваются "
                        "в рендере, у каждой своя громкость gain_db (тихий микрофон — подними; gain_db \"auto\" "
                        "замерит сам). Распознавание речи (расшифровка, субтитры, listen, QC) — по speech_tracks: "
                        "на одном микрофоне оно чище, чем на смеси с игрой. Сменить — set_source_audio(project_id, "
                        "tracks, speech_tracks)")
    return view


def _ff_db(gain: float) -> str:
    return f"{gain:g}dB"


def mix_chain(input_index: int, tracks: Any, out_label: str) -> str:
    """Граф: дорожки ``tracks`` входа ``input_index`` → одна в ``[out_label]``.

    ``tracks`` — номера или ``[{track, gain_db}]``. Одна дорожка — просто она (с усилением,
    если задано); несколько — amix (нормирует на число входов: сумма игры и микрофона не
    перегружается, громкость потом выравнивает рендер), у каждой своя громкость до него."""
    items = _mix_items(tracks)
    if len(items) == 1:
        track, gain = items[0]
        chain = f"volume={_ff_db(gain)}" if gain else "anull"
        return f"[{input_index}:a:{track}]{chain}[{out_label}]"
    pre: list[str] = []
    inputs = ""
    for n, (track, gain) in enumerate(items):
        if gain:
            label = f"{out_label}_g{n}"
            pre.append(f"[{input_index}:a:{track}]volume={_ff_db(gain)}[{label}]")
            inputs += f"[{label}]"
        else:
            inputs += f"[{input_index}:a:{track}]"
    graph = f"{inputs}amix=inputs={len(items)}:duration=longest:dropout_transition=0[{out_label}]"
    return ";".join([*pre, graph])


def extract_args(source_path: Path, out_path: Path, tracks: Any, *, start: float | None = None,
                 duration: float | None = None, rate: int = 16000, fmt: list[str] | None = None) -> list[str]:
    """ffmpeg: звук окна исходника (быстрый поиск ``-ss``/``-t`` до ``-i``) моно ``rate`` Гц.

    Читает только нужные секунды: 4-часовой исходник целиком не декодируется."""
    args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start is not None:
        args += ["-ss", f"{max(0.0, float(start)):.3f}"]
    if duration is not None:
        args += ["-t", f"{max(0.01, float(duration)):.3f}"]
    args += ["-i", str(source_path)]
    if tracks and not is_default_mix(tracks):
        args += ["-filter_complex", mix_chain(0, tracks, "a"), "-map", "[a]"]
    else:
        args += ["-map", "0:a:0"]
    args += ["-vn", "-ac", "1", "-ar", str(int(rate))]
    args += fmt if fmt is not None else ["-c:a", "pcm_s16le"]
    args.append(str(out_path))
    return args


_KEEP = object()
# «Авто»: голос (дорожка с gain_db "auto") ставится так, чтобы его фразы (90-й перцентиль
# громкости) были на AUTO_OVER_DB громче медианы остальных дорожек — игра под голосом,
# а не голос под игрой. Пределы — чтобы шум пустого микрофона не задирался до небес.
AUTO_OVER_DB = 6.0
AUTO_MIN_DB = -12.0
AUTO_MAX_DB = 24.0
AUTO_WINDOWS = 4
AUTO_WINDOW_SEC = 60.0


def _auto_tracks(tracks: Any) -> list[int]:
    items = _raw_items(tracks) or []
    return [t for t in (_item_track(i) for i in items if isinstance(i, dict) and i.get("gain_db") == AUTO)
            if t is not None]


def track_levels(source: dict, track: int, *, windows: int = AUTO_WINDOWS,
                 window_sec: float = AUTO_WINDOW_SEC) -> dict:
    """Громкость одной дорожки по нескольким окнам записи: медиана и 90-й перцентиль (дБ)."""
    from app.source_loudness import SILENCE_DB, pcm_levels, read_window_pcm

    duration = float(source.get("duration_sec") or 0)
    if duration <= window_sec * windows:
        spans = [(0.0, duration or window_sec)]
    else:
        spans = [(duration * (k + 1) / (windows + 1) - window_sec / 2, window_sec) for k in range(windows)]
    levels: list[float] = []
    for start, length in spans:
        levels += pcm_levels(read_window_pcm(source, max(0.0, start), length, tracks=[track]), 8000, 0.5)
    heard = sorted(v for v in levels if v > SILENCE_DB + 20)
    if not heard:
        return {"median_db": SILENCE_DB, "p90_db": SILENCE_DB}
    return {"median_db": round(heard[len(heard) // 2], 1), "p90_db": round(heard[min(len(heard) - 1, int(len(heard) * 0.9))], 1)}


def auto_gain(levels: dict[int, dict], voice: int) -> float:
    """Усиление голоса: его фразы на AUTO_OVER_DB громче медианы остальных дорожек."""
    others = [lv["median_db"] for t, lv in levels.items() if t != voice]
    if not others or voice not in levels:
        return 0.0
    want = max(others) + AUTO_OVER_DB - levels[voice]["p90_db"]
    return round(min(AUTO_MAX_DB, max(AUTO_MIN_DB, want)) * 2) / 2


def set_source_audio(store: Any, source_id: int, tracks: Any = _KEEP, speech_tracks: Any = _KEEP,
                     measure: Any = None) -> dict:
    """Выбрать дорожки источника и их громкость (REST и MCP). ``[]``/None — вернуть первую.

    ``tracks`` — ``[0, 1]`` или ``[{track, gain_db}]`` (``gain_db: "auto"`` — замерить и поднять
    голос над игрой); ``speech_tracks`` — дорожки для распознавания речи (``[]`` — как
    ``tracks``). Номер за пределами файла — ошибка (ValueError), а не тихий «звук пропал»
    на рендере. ``measure(source, track)`` — замер громкости (тесты подменяют)."""
    source = store.get_source(int(source_id))
    total = len(audio_streams(source))
    fields: dict = {}
    out: dict = {}
    if tracks is not _KEEP:
        if tracks not in (None, [], "") and normalize_mix(tracks) is None:
            raise ValueError("tracks — номера звуковых дорожек с 0 ([0], [0, 1]) или [{track, gain_db}]")
        mix = normalize_mix(tracks)
        _check_tracks([m["track"] for m in mix] if mix else None, total, "tracks")
        auto = [t for t in _auto_tracks(tracks) if mix and any(m["track"] == t for m in mix)]
        if auto and mix and len(mix) > 1:
            measure = measure or track_levels
            levels = {m["track"]: measure(source, m["track"]) for m in mix}
            for m in mix:
                if m["track"] in auto:
                    m["gain_db"] = auto_gain(levels, m["track"])
            out["levels"] = {str(t): lv for t, lv in levels.items()}
            out["auto_gain"] = {str(m["track"]): m["gain_db"] for m in mix if m["track"] in auto}
        fields["audio_tracks"] = mix_storage(mix)
    if speech_tracks is not _KEEP:
        if speech_tracks not in (None, [], "") and normalize_tracks(speech_tracks) is None:
            raise ValueError("speech_tracks — номера дорожек с 0 для распознавания речи, например [1]")
        speech = normalize_tracks(speech_tracks)
        _check_tracks(speech, total, "speech_tracks")
        fields["speech_tracks"] = speech
    saved = store.update_source(int(source_id), **fields) if fields else source
    return {
        "ok": True,
        "project_id": saved["id"],
        "audio": audio_view(saved),
        **out,
        "note": ("рендер, громкость (source_loudness, find_noises) — по смеси tracks с громкостью gain_db каждой "
                 "дорожки; распознавание (расшифровка исходника, субтитры, listen, сверка субтитров в QC) — по "
                 "speech_tracks. Сменился звук для распознавания — расшифровка исходника сброшена "
                 "(get_source_transcript распознает заново); субтитры уже сделанных клипов — generate_subtitles"),
    }


def _check_tracks(chosen: list[int] | None, total: int, name: str) -> None:
    if chosen and total and max(chosen) >= total:
        raise ValueError(f"{name}: в записи {total} звуковых дорожек (0..{total - 1}), а выбрано {chosen}")
    if chosen and not total and chosen != [0]:
        raise ValueError("в записи не нашлось звуковых дорожек")
