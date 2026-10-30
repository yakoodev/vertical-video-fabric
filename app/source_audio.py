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
# «Авто»: голос (дорожка с gain_db "auto") — на AUTO_OVER_DB громче остальных дорожек смеси по
# интегральной громкости В МОМЕНТЫ РЕЧИ: игру меряем в те же окна, где звучит голос. Прежний
# расчёт (90-й перцентиль голоса против медианы игры за всё время) на апексе дал микрофону
# +12…+16 дБ, а голос вышел на 2–3 дБ над игрой или ниже: когда стример говорит, в игре стреляют.
AUTO_OVER_DB = 9.0
AUTO_MIN_DB = -12.0
AUTO_MAX_DB = 24.0
AUTO_WINDOWS = 4
AUTO_WINDOW_SEC = 60.0
AUTO_STEP_SEC = 0.25
# Окно — речь, если голос громче шума своей дорожки (20-й перцентиль) на столько и не тише пола.
AUTO_SPEECH_OVER_FLOOR_DB = 12.0
AUTO_SPEECH_MIN_DB = -60.0
AUTO_MIN_SPEECH_SEC = 1.0
# Бюджет пика голоса: пик речи (99-й перцентиль окон речи) после усиления — не выше потолка
# пика готового файла (app/render.LOUDNESS_PEAK_DB). Голосу — сколько позволяет бюджет (не больше
# нужного); недостающий перевес — тише остальные дорожки (их gain_db < 0). Прежде при нехватке
# запаса снижали усиление голоса до перевеса +4 дБ, а игра оставалась на 0 — на записях «игра 0 +
# микрофон 1» голос выходил на −6…+5 дБ к игре, владелец не слышал микрофон. Голос ниже
# AUTO_OVER_DB над игрой — не соглашаемся.
AUTO_VOICE_PEAK_CEIL_DB = -1.5
# Ролик потом доводится до −14 LUFS (settings.render_loudnorm_target) с потолком пика −1.5 dBTP,
# лимитер без потери громкости срезает ~3 дБ (app/render: LOUDNESS_PEAK_DB, LIMITER_ROOM_DB). Пик
# смеси выше её громкости больше чем на столько — нормализация не дотянет −14 одним лимитером.
# Балансом дорожек это не лечится (сдвиг всех на одно и то же этот перевес не меняет), сожмёт
# компрессор нормализации — только пометка в ответе.
AUTO_PEAK_ROOM_DB = -1.5 + 3.0 - (-14.0)


def _auto_tracks(tracks: Any) -> list[int]:
    items = _raw_items(tracks) or []
    return [t for t in (_item_track(i) for i in items if isinstance(i, dict) and i.get("gain_db") == AUTO)
            if t is not None]


def _power(db: float) -> float:
    return 10 ** (float(db) / 10)


def _power_db(power: float) -> float:
    import math

    return 10 * math.log10(power) if power > 0 else -90.0


def _energy_mean_db(values: list[float]) -> float:
    """Интегральная громкость окон (среднее мощности, а не среднее дБ — громкие места весят больше)."""
    return _power_db(sum(_power(v) for v in values) / len(values)) if values else -90.0


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(len(ordered) * q)))] if ordered else -90.0


def auto_voice_gain(
    voice: list[float],
    others: list[list[float]],
    voice_peaks: list[float] | None = None,
    *,
    others_peaks: list[list[float]] | None = None,
    step: float = AUTO_STEP_SEC,
    over_db: float = AUTO_OVER_DB,
) -> dict:
    """Усиление голоса и остальных дорожек (чистая функция, дБ по окнам ``step`` одних и тех же
    моментов записи).

    ``voice`` — RMS голоса по окнам, ``others`` — RMS остальных дорожек смеси в те же окна,
    ``voice_peaks`` / ``others_peaks`` — пики в окне (max |сэмпл|, дБ). Речь — окна, где голос громче
    шума своей дорожки на AUTO_SPEECH_OVER_FLOOR_DB. Цель: голос в моменты речи на ``over_db`` громче
    суммы остальных дорожек в те же моменты. Голосу — нужное усиление, но не выше бюджета пика
    (пик речи после усиления ≤ AUTO_VOICE_PEAK_CEIL_DB) и в AUTO_MIN_DB..AUTO_MAX_DB; чего не хватило —
    остальные дорожки тише (``others_gain_db`` ≤ 0). Перевес ниже ``over_db`` не принимаем.

    Ответ: ``gain_db`` голоса и ``others_gain_db`` остальных (шаг 0.5), замер ``voice_db``/``others_db``
    (в моменты речи), ``over_db`` (перевес голоса после обоих), ``speech_sec``, ``peak_limited``
    (голос упёрся в бюджет пика — перевес добран тишиной остальных)."""
    import math

    n = min([len(voice), *(len(o) for o in others)]) if others else 0
    if n == 0:
        return {"gain_db": 0.0, "others_gain_db": 0.0,
                "note": "нечего сравнивать: нужна дорожка голоса и хотя бы одна другая"}
    v = [float(x) for x in voice[:n]]
    floor = _percentile(v, 0.2)
    bar = max(floor + AUTO_SPEECH_OVER_FLOOR_DB, AUTO_SPEECH_MIN_DB)
    speech = [i for i in range(n) if v[i] >= bar]
    speech_sec = round(len(speech) * step, 2)
    if speech_sec < AUTO_MIN_SPEECH_SEC:
        return {"gain_db": 0.0, "others_gain_db": 0.0, "speech_sec": speech_sec,
                "note": "в дорожке голоса не нашлось речи (громче шума на 12 дБ) — усиление не менял"}
    other_power = [sum(_power(o[i]) for o in others) for i in range(n)]
    voice_db = _energy_mean_db([v[i] for i in speech])
    others_db = _power_db(sum(other_power[i] for i in speech) / len(speech))
    want = others_db + over_db - voice_db
    out: dict = {"voice_db": round(voice_db, 1), "others_db": round(others_db, 1), "speech_sec": speech_sec,
                 "noise_db": round(floor, 1), "peak_limited": False}
    peaks = [float(voice_peaks[i]) for i in speech if i < len(voice_peaks)] if voice_peaks else []
    top = _percentile(peaks, 0.99) if peaks else None
    budget = AUTO_MAX_DB if top is None else AUTO_VOICE_PEAK_CEIL_DB - top
    if top is not None:
        out["peak_db"] = round(top, 1)
    notes: list[str] = []
    if budget < min(want, AUTO_MAX_DB):
        # Бюджет пика меньше нужного: голосу — сколько можно (вниз до шага 0.5, чтобы не перелезть).
        gain = max(AUTO_MIN_DB, math.floor(budget * 2) / 2)
        out["peak_limited"] = True
        notes.append(f"пик речи {top:.1f} дБ не даёт голосу больше {gain:g} дБ (нужно {want:.1f}) — "
                     "остальные дорожки тише")
    else:
        gain = round(min(AUTO_MAX_DB, max(AUTO_MIN_DB, want)) * 2) / 2
        if want > AUTO_MAX_DB:
            notes.append(f"голосу нужно {want:.1f} дБ, больше {AUTO_MAX_DB:g} не даю — остальные дорожки тише")
    # Недобор перевеса (после шага 0.5 до четверти дБ терпим) — тишиной остальных, с округлением вниз.
    deficit = others_db + over_db - (voice_db + gain)
    others_gain = -math.ceil(deficit * 2 - 1e-9) / 2 if deficit > 0.25 else 0.0
    if others_gain < MIN_GAIN_DB:
        others_gain = MIN_GAIN_DB
        notes.append(f"остальные тише {MIN_GAIN_DB:g} дБ не опускаю — перевес голоса меньше цели")
    others_gain += 0.0  # без «-0.0» в ответе
    if top is not None:
        top_others = (_percentile([max(float(o[i]) for o in others_peaks) for i in range(n)], 0.99)
                      if others_peaks and all(len(o) >= n for o in others_peaks) else None)
        mix = _power_db(sum(_power(v[i] + gain) + other_power[i] * _power(others_gain) for i in range(n)) / n)
        peak = top + gain if top_others is None else max(top + gain, top_others + others_gain)
        out["peak_over_mix_db"] = round(peak - mix, 1)
        if peak - mix > AUTO_PEAK_ROOM_DB:
            notes.append(f"пики смеси выше её громкости на {peak - mix:.1f} дБ — балансом дорожек не лечится, "
                         "сожмёт компрессор нормализации")
    if notes:
        out["note"] = "; ".join(notes)
    out["gain_db"] = gain
    out["others_gain_db"] = others_gain
    out["over_db"] = round(voice_db + gain - (others_db + others_gain), 1)
    return out


def track_profile(source: dict, track: int, *, windows: int = AUTO_WINDOWS,
                  window_sec: float = AUTO_WINDOW_SEC, step: float = AUTO_STEP_SEC) -> dict:
    """Громкость одной дорожки по окнам ``step`` в нескольких местах записи (одних и тех же для
    всех дорожек — моменты совпадают): ``{db: [RMS дБ], peak_db: [пик дБ], step}``."""
    from app.source_loudness import pcm_levels, pcm_peaks, read_window_pcm

    duration = float(source.get("duration_sec") or 0)
    if duration <= window_sec * windows:
        spans = [(0.0, duration or window_sec)]
    else:
        spans = [(duration * (k + 1) / (windows + 1) - window_sec / 2, window_sec) for k in range(windows)]
    levels: list[float] = []
    peaks: list[float] = []
    for start, length in spans:
        raw = read_window_pcm(source, max(0.0, start), length, tracks=[track])
        got = pcm_levels(raw, 8000, step)
        levels += got
        peaks += pcm_peaks(raw, 8000, step)[: len(got)]
    return {"db": levels, "peak_db": peaks, "step": step}


def profile_summary(profile: dict) -> dict:
    """Медиана и 90-й перцентиль громкости дорожки (дБ) — показать агенту рядом с auto_gain."""
    from app.source_loudness import SILENCE_DB

    heard = sorted(v for v in profile.get("db") or [] if v > SILENCE_DB + 20)
    if not heard:
        return {"median_db": SILENCE_DB, "p90_db": SILENCE_DB}
    return {"median_db": round(heard[len(heard) // 2], 1), "p90_db": round(_percentile(heard, 0.9), 1)}


def set_source_audio(store: Any, source_id: int, tracks: Any = _KEEP, speech_tracks: Any = _KEEP,
                     measure: Any = None) -> dict:
    """Выбрать дорожки источника и их громкость (REST и MCP). ``[]``/None — вернуть первую.

    ``tracks`` — ``[0, 1]`` или ``[{track, gain_db}]`` (``gain_db: "auto"`` — замерить и поставить
    голос на ~9 дБ над игрой в моменты речи, ``auto_voice_gain``: голосу — сколько позволяет пик,
    остальным, если не хватило, — отрицательный gain_db); ``speech_tracks`` — дорожки для распознавания речи (``[]`` — как
    ``tracks``). Номер за пределами файла — ошибка (ValueError), а не тихий «звук пропал»
    на рендере. ``measure(source, track)`` — замер дорожки ``{db, peak_db, step}`` (тесты подменяют)."""
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
            measure = measure or track_profile
            profiles = {m["track"]: measure(source, m["track"]) for m in mix}
            # Остальные — с их собственной громкостью (gain_db владельца), голос — сырой.
            own = {m["track"]: (0.0 if m["track"] in auto else float(m["gain_db"])) for m in mix}

            def shifted(track: int, key: str) -> list[float]:
                return [float(x) + own[track] for x in profiles[track].get(key) or []]

            details = {}
            lower = 0.0
            for m in mix:
                if m["track"] in auto:
                    rest = [t for t in profiles if t != m["track"] and t not in auto] or \
                        [t for t in profiles if t != m["track"]]
                    got = auto_voice_gain(profiles[m["track"]]["db"], [shifted(t, "db") for t in rest],
                                          profiles[m["track"]].get("peak_db"),
                                          others_peaks=[shifted(t, "peak_db") for t in rest],
                                          step=float(profiles[m["track"]].get("step") or AUTO_STEP_SEC))
                    m["gain_db"] = _gain_db(got["gain_db"])
                    # Голосов несколько — остальные тише на самое большое из нужного каждому.
                    lower = min(lower, float(got.get("others_gain_db") or 0.0))
                    details[str(m["track"])] = got
            if lower < 0:
                for m in mix:
                    if m["track"] not in auto:
                        m["gain_db"] = _gain_db(own[m["track"]] + lower)
            out["levels"] = {str(t): profile_summary(p) for t, p in profiles.items()}
            # Громкость ВСЕХ дорожек смеси: голос поднят, остальные (если голосу не хватило пика) тише.
            out["auto_gain"] = {str(m["track"]): m["gain_db"] for m in mix}
            out["auto"] = details
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
