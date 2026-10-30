"""PNGtuber: аватар, который «говорит», когда звучит голос (слой ``pngtuber``, docs/motion/spec.md).

Состояние аватара (молчит / говорит / кричит) решает сервер, а не движок: перед
компоновщиком Python считает огибающую громкости дорожки голоса по кадрам (RMS в дБ по
окну 1/fps), переводит её в состояния с attack/hold и кладёт в ``comp.voice``. Движок
только читает массив — превью и рендер показывают один и тот же рот в один и тот же кадр.

Голос — отдельная дорожка ИСТОЧНИКА (запись NVIDIA: 0 — игра, 1 — микрофон) или
``"speech"`` — дорожки распознавания речи источника (``speech_tracks``; не заданы —
смесь рендера), или файл библиотеки (``voice.asset_id``). Время слоя — шкала клипа
(``clip``) или готового ролика (``final``, после вырезов); в секунды источника его
переводит ``app.timebase.ClipClock`` — вырез или наплыв не сбивает рот с голоса.

Порог по умолчанию — ``"auto"``: шум дорожки + 12 дБ, но не выше речи − 12 дБ и не ниже −55
(``auto_threshold``).

Чистые функции (``voice_states``, ``auto_threshold``, ``voice_levels``,
``frame_sources``) проверяются на синтетике без ffmpeg; чтение звука — подменяемое.
"""

from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any, Callable

from app.motion import (
    PNGTUBER_STATES,
    VOICE_ATTACK_SEC,
    VOICE_AUTO,
    VOICE_HOLD_SEC,
    VOICE_SHOUT_DB,
    VOICE_SPEECH,
    VOICE_THRESHOLD_DB,
    VOICE_THRESHOLD_DEFAULT,
    has_pngtuber,
)

SILENCE_DB = -90.0
PCM_RATE = 8000
# Огибающая подскока: спад за ~80 мс — рот не дёргается, а «дышит».
LEVEL_RELEASE_SEC = 0.08
# Сколько дБ над порогом — полный подскок (если shout_db ближе — до него).
LEVEL_SPAN_DB = 24.0
# Длиннее — дальше молчит (минуты клипа; часы — явная ошибка слоя).
MAX_VOICE_SEC = 30 * 60.0
# Авто-порог (по умолчанию): шум дорожки (15-й перцентиль) + 12 дБ, но не выше речи − 12 дБ (речь —
# 75-й перцентиль кадров, громче шума на 6+ дБ) и не ниже −55 (шум пустого микрофона рот не
# открывает). Был фиксированный потолок −40: на выровненном микрофоне (речь ≈ −26 дБFS) дыхание и
# шум выше −40, и рот был открыт, пока стример молчал. Клипы 583–585 (тихие фразы −42 дБ при пороге
# −38) относительный потолок тоже ловит: тихая речь — ниже и порог.
AUTO_OVER_FLOOR_DB = 12.0
AUTO_UNDER_SPEECH_DB = 12.0
AUTO_VOICED_OVER_FLOOR_DB = 6.0
AUTO_SPEECH_PERCENTILE = 0.75
AUTO_MIN_DB = -55.0
# Ровная дорожка без пауз (всё — «шум»): громче этого — сплошная речь, порог под ней; тише — шум.
AUTO_FLAT_SPEECH_DB = -40.0
# Речь для сверки рта с субтитрами (voice_check): слово ± столько секунд.
SPEECH_PAD_SEC = 0.15
# Склейка соседних кадров в один кусок чтения звука: разрыв больше — отдельное чтение.
_SPAN_GAP_SEC = 0.25
_PCM_CACHE: dict[tuple, bytes] = {}
_PCM_CACHE_MAX = 24
_PCM_CACHE_BYTES = 32 * 1024 * 1024
_PCM_LOCK = threading.Lock()

Reader = Callable[[Any, float, float], bytes]


# ---------------------------------------------------------------- чистые функции


def noise_floor(levels: list[float | None]) -> float | None:
    """Шум дорожки — 15-й перцентиль громкости кадров (тишина цифрового нуля не в счёт)."""
    heard = sorted(v for v in levels if v is not None and v > SILENCE_DB + 1)
    if len(heard) < 4:
        return None
    return heard[int(len(heard) * 0.15)]


def gated(levels: list[float | None]) -> bool:
    """Микрофон с шумодавом (гейтом OBS): между фразами цифровой ноль хотя бы в 15% кадров —
    шума как такового нет, «шум» по слышимым кадрам — это уже тихая речь."""
    known = [v for v in levels if v is not None]
    return bool(known) and sum(1 for v in known if v <= SILENCE_DB + 1) >= len(known) * 0.15


def speech_level(levels: list[float | None]) -> float | None:
    """Громкость речи дорожки — 75-й перцентиль «озвученных» кадров (громче шума на 6+ дБ; у
    микрофона с гейтом — всех слышимых); None — таких кадров почти нет (пустой микрофон или
    ровная дорожка без пауз)."""
    floor = noise_floor(levels)
    if floor is None:
        return None
    over = SILENCE_DB + 1 if gated(levels) else floor + AUTO_VOICED_OVER_FLOOR_DB
    voiced = sorted(v for v in levels if v is not None and v > over - 1e-9)
    if len(voiced) < 4:
        return None
    return voiced[min(len(voiced) - 1, int(len(voiced) * AUTO_SPEECH_PERCENTILE))]


def auto_threshold(levels: list[float | None]) -> float:
    """Порог «звучит голос»: шум (15-й перцентиль) + 12 дБ, но не выше речи (``speech_level``)
    − 12 дБ и не ниже −55; микрофон с гейтом — речь − 12; пустая дорожка — запасной порог (−45)."""
    floor = noise_floor(levels)
    if floor is None:
        return VOICE_THRESHOLD_DB
    speech = speech_level(levels)
    if speech is not None and gated(levels):
        want = speech - AUTO_UNDER_SPEECH_DB  # всё слышимое — голос: порог под речью
    elif speech is not None:
        want = min(floor + AUTO_OVER_FLOOR_DB, speech - AUTO_UNDER_SPEECH_DB)
        want = max(want, floor + 4.0)  # речь чуть громче шума — порог всё равно над шумом
    elif floor >= AUTO_FLAT_SPEECH_DB:
        want = floor - AUTO_UNDER_SPEECH_DB  # ровная громкая дорожка — сплошная речь (окно без пауз)
    else:
        want = floor + AUTO_OVER_FLOOR_DB
    return round(max(AUTO_MIN_DB, want), 1)


def open_outside_speech_pct(track: dict | None, words: list[dict], pad: float = SPEECH_PAD_SEC) -> float | None:
    """Доля дорожки рта (``comp.voice[id]``), %, где рот открыт, а речи нет: речь — слова
    субтитров клипа (``{t0, t1}``, время клипа) ± ``pad``. Дыхание, шум, игра в микрофоне.
    None — нет слов или кадров (не с чем сверить)."""
    states = str((track or {}).get("states") or "")
    spans = sorted((float(w["t0"]) - pad, float(w["t1"]) + pad) for w in words or []
                   if w.get("t0") is not None and w.get("t1") is not None)
    if not states or not spans:
        return None
    t0 = float(track.get("t0") or 0.0)
    fps = float(track.get("fps") or 30.0)
    outside = 0
    j = 0
    for k, state in enumerate(states):
        t = t0 + k / fps
        while j < len(spans) and spans[j][1] < t:
            j += 1
        speaking = j < len(spans) and spans[j][0] <= t  # отсортированы по началу: первый не кончившийся
        if state != "0" and not speaking:
            outside += 1
    return round(100.0 * outside / len(states), 1)


def voice_states(
    levels: list[float | None],
    fps: float,
    threshold_db: float = VOICE_THRESHOLD_DB,
    hold: float = VOICE_HOLD_SEC,
    attack: float = VOICE_ATTACK_SEC,
    shout_db: float | None = VOICE_SHOUT_DB,
) -> list[int]:
    """Громкость по кадрам (дБ; None — звука нет) → состояния 0 молчит, 1 говорит, 2 кричит.

    attack — сколько голос должен звучать подряд, чтобы открыть рот (щелчок клавиатуры
    не открывает); hold — сколько держать рот открытым после спада (между слогами рот
    не хлопает). Крик — громче ``shout_db`` с тем же attack/hold, только пока говорит."""
    fps = max(1.0, float(fps))
    attack_n = max(1, round(max(0.0, attack) * fps))
    hold_n = max(0, round(max(0.0, hold) * fps))
    out: list[int] = []
    run = shout_run = 0
    last_talk = last_shout = -10**9
    for i, db in enumerate(levels):
        loud = db is not None and db >= threshold_db
        run = run + 1 if loud else 0
        shouting = loud and shout_db is not None and db >= shout_db  # type: ignore[operator]
        shout_run = shout_run + 1 if shouting else 0
        if run >= attack_n:
            last_talk = i
        if shout_run >= attack_n:
            last_shout = i
        if i - last_shout <= hold_n and i - last_talk <= hold_n:
            out.append(2)
        elif i - last_talk <= hold_n:
            out.append(1)
        else:
            out.append(0)
    return out


def voice_levels(
    levels: list[float | None],
    states: list[int],
    fps: float,
    threshold_db: float,
    shout_db: float | None = VOICE_SHOUT_DB,
) -> list[int]:
    """Огибающая для подскока 0..100: громкость над порогом (до shout_db или +24 дБ), пока
    рот открыт; спад плавный (~80 мс) — подскок «дышит» в такт слогам."""
    top = shout_db if shout_db is not None and shout_db > threshold_db + 3 else threshold_db + LEVEL_SPAN_DB
    span = max(3.0, top - threshold_db)
    decay = math.exp(-1.0 / (max(1.0, fps) * LEVEL_RELEASE_SEC))
    out: list[int] = []
    prev = 0.0
    for db, state in zip(levels, states):
        raw = 0.0
        if state > 0 and db is not None:
            raw = min(1.0, max(0.0, (db - threshold_db) / span))
            raw = max(raw, 0.15)  # открытый рот — хоть чуть-чуть подскакивает
        prev = max(raw, prev * decay)
        out.append(int(round(prev * 100)))
    return out


def rms_db(samples: Any) -> float:
    """RMS окна сэмплов (−1..1) в дБ полной шкалы."""
    import numpy as np

    if len(samples) == 0:
        return SILENCE_DB
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    return max(SILENCE_DB, 20 * math.log10(rms)) if rms > 0 else SILENCE_DB


def pcm_frame_levels(pcm: bytes, rate: int, start: float, times: list[float], fps: float) -> list[float]:
    """Громкость кадров: RMS окна 1/fps вокруг каждого момента ``times`` (секунды той же шкалы,
    что ``start`` — начало куска PCM)."""
    import numpy as np

    samples = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype(np.float64) / 32768.0
    half = max(1, int(round(rate / max(1.0, fps) / 2)))
    out = []
    for t in times:
        center = int(round((t - start) * rate))
        a, b = max(0, center - half), min(len(samples), center + half)
        out.append(round(rms_db(samples[a:b]), 2) if b > a else SILENCE_DB)
    return out


# ---------------------------------------------------------------- время слоя → источник


def layer_frames(layer: dict, fps: float, window: tuple[float, float] | None = None) -> tuple[float, int]:
    """Первый кадр слоя (секунды шкалы слоя) и число кадров: с start по end по 1/fps;
    ``window`` — только этот кусок (кадр для агента)."""
    fps = max(1.0, float(fps))
    start = float(layer.get("start") or 0.0)
    end = min(float(layer.get("end") or start), start + MAX_VOICE_SEC)
    if window is not None:
        a = max(start, window[0])
        b = min(end, window[1])
        # Кадры остаются на сетке слоя: t0 = start + k/fps.
        start = start + math.floor((a - start) * fps + 1e-6) / fps
        end = b
    count = max(0, int(math.ceil((end - start) * fps - 1e-6)))
    return round(start, 6), count


def frame_sources(clock: Any, segments: list[dict], times: list[float], scale: str,
                  default_source: Any = None) -> list[tuple[Any, float] | None]:
    """Момент шкалы слоя (``clip`` или ``final``) → (id источника, секунда источника); вне
    кусков — None. Перевод — ``app.timebase.ClipClock``, тот же, что у инструментов агента."""
    out: list[tuple[Any, float] | None] = []
    if not segments:
        return [None for _ in times]
    total = clock.final_duration if scale == "final" else clock.clip_duration
    for t in times:
        if t < -1e-6 or t > total + 1e-6:
            out.append(None)
            continue
        plain = clock.final_to_plain(t) if scale == "final" else clock.clip_to_plain(t)
        index = clock.plain_piece(plain)
        seg = segments[index]
        local = min(max(0.0, plain - clock.pre.plain_starts[index]), clock.lengths[index])
        out.append((seg.get("source_id") or default_source, float(seg["start_sec"]) + local))
    return out


def spans_of(points: list[tuple[Any, float] | None], fps: float) -> list[tuple[Any, float, float, list[int]]]:
    """Подряд идущие кадры одного источника → куски чтения звука (источник, от, до, номера кадров)."""
    spans: list[tuple[Any, float, float, list[int]]] = []
    pad = 1.0 / max(1.0, fps)
    for i, point in enumerate(points):
        if point is None:
            continue
        sid, sec = point
        if spans:
            last_sid, a, b, idx = spans[-1]
            if last_sid == sid and a - _SPAN_GAP_SEC <= sec <= b + _SPAN_GAP_SEC:
                spans[-1] = (sid, min(a, sec), max(b, sec), idx + [i])
                continue
        spans.append((sid, sec, sec, [i]))
    return [(sid, max(0.0, a - pad), b + pad, idx) for sid, a, b, idx in spans]


def levels_for_points(points: list[tuple[Any, float] | None], fps: float, read: Reader) -> list[float | None]:
    """Громкость по кадрам: звук читается кусками (``read(источник, от, длина)`` → PCM 8 кГц)."""
    levels: list[float | None] = [None] * len(points)
    for sid, a, b, idx in spans_of(points, fps):
        pcm = read(sid, a, b - a)
        values = pcm_frame_levels(pcm, PCM_RATE, a, [points[i][1] for i in idx], fps)  # type: ignore[index]
        for i, value in zip(idx, values):
            levels[i] = value
    return levels


# ---------------------------------------------------------------- дорожка → состояния


def voice_config(props: dict) -> dict:
    """Настройки голоса слоя с умолчаниями (нормализованный слой их уже содержит)."""
    voice = props.get("voice") if isinstance(props.get("voice"), dict) else {}
    track = voice.get("track", VOICE_SPEECH)
    return {
        "track": track if track is not None else VOICE_SPEECH,
        "asset_id": voice.get("asset_id"),
        "threshold_db": voice.get("threshold_db", VOICE_THRESHOLD_DEFAULT),
        "hold": float(voice.get("hold", VOICE_HOLD_SEC)),
        "attack": float(voice.get("attack", VOICE_ATTACK_SEC)),
        "shout_db": float(voice.get("shout_db", VOICE_SHOUT_DB)) if (props.get("states") or {}).get("shout") else None,
    }


def track_from_levels(levels: list[float | None], t0: float, fps: float, cfg: dict,
                      auto_from: list[float | None] | None = None) -> dict:
    """Громкость кадров слоя → ``comp.voice[id]``: {t0, fps, states "0012…", levels [0..100], threshold_db}.

    ``auto_from`` — по какой громкости выбирать порог "auto" (кадр агента слушает окно, а порог
    берёт со всего слоя — как рендер); нет — по ``levels``."""
    threshold = cfg.get("threshold_db", VOICE_THRESHOLD_DEFAULT)
    auto = threshold == VOICE_AUTO
    basis = auto_from if auto_from is not None else levels
    threshold = auto_threshold(basis) if auto else float(threshold)
    shout = cfg.get("shout_db")
    states = voice_states(levels, fps, threshold, cfg.get("hold", VOICE_HOLD_SEC), cfg.get("attack", VOICE_ATTACK_SEC),
                          shout)
    out = {
        "t0": round(t0, 4),
        "fps": float(fps),
        "states": "".join(str(s) for s in states),
        "levels": voice_levels(levels, states, fps, threshold, shout),
        "threshold_db": threshold,
    }
    if auto:
        # Как выбран порог — агенту в voice_check (движок эти поля не читает).
        floor = noise_floor(basis)
        speech = speech_level(basis)
        out["threshold_auto"] = True
        if floor is not None:
            out["noise_db"] = round(floor, 1)
        if speech is not None:
            out["speech_db"] = round(speech, 1)
    return out


def _wav_pcm(path: Path, args: list[str]) -> bytes:
    from app.render import _run_ffmpeg
    from app.speech_onsets import wav_pcm

    try:
        _run_ffmpeg(args, timeout=60 * 5)
        return wav_pcm(path)
    finally:
        path.unlink(missing_ok=True)


def _cached(key: tuple, load: Callable[[], bytes]) -> bytes:
    """Звук голоса между кадрами агента и рендером. Кэш живёт в процессе всё время — держим
    не больше ``_PCM_CACHE_MAX`` кусков и ``_PCM_CACHE_BYTES`` байт (голос из файла библиотеки
    до 30 минут — 29 МБ на кусок; 24 таких — 0.7 ГБ, которые никто не отпускал)."""
    with _PCM_LOCK:
        if key in _PCM_CACHE:
            return _PCM_CACHE[key]
    data = load()
    if len(data) > _PCM_CACHE_BYTES:
        return data  # огромный кусок не кэшируем
    with _PCM_LOCK:
        _PCM_CACHE[key] = data
        while len(_PCM_CACHE) > _PCM_CACHE_MAX or sum(map(len, _PCM_CACHE.values())) > _PCM_CACHE_BYTES:
            _PCM_CACHE.pop(next(iter(_PCM_CACHE)))
    return data


def source_tracks_for(source: dict, track: Any) -> tuple[list[dict] | None, str | None]:
    """Какие дорожки источника слушать: номер — одна дорожка (если она есть в файле),
    "speech" — дорожки распознавания речи (speech_mix). Второе — пометка, если номер не годится."""
    from app.source_audio import audio_streams, speech_mix

    if track == VOICE_SPEECH or track is None:
        return speech_mix(source), None
    total = len(audio_streams(source, probe=False)) or None
    if total is not None and int(track) >= total:
        return speech_mix(source), f"у записи {total} звуковых дорожек — дорожки {track} нет, голос по speech"
    return [{"track": int(track), "gain_db": 0.0}], None


def source_reader(store: Any, track: Any, notes: list[str]) -> Reader:
    """Чтение звука источника (дорожки голоса) моно 8 кГц: ``read(id источника, от, длина)``."""
    from uuid import uuid4

    from app.settings import settings
    from app.source_audio import extract_args

    sources: dict[Any, dict] = {}

    def read(source_id: Any, start: float, length: float) -> bytes:
        if source_id not in sources:
            sources[source_id] = store.get_source(int(source_id))
        source = sources[source_id]
        path = Path(str(source.get("local_path") or ""))
        if not path.is_file():
            note = f"файла источника {source_id} нет — PNGtuber молчит"
            if note not in notes:
                notes.append(note)
            return b""
        tracks, note = source_tracks_for(source, track)
        if note and note not in notes:
            notes.append(note)
        key = ("src", str(path), path.stat().st_mtime, repr(tracks), round(start, 3), round(length, 3))
        out = settings.tmp_dir / f"voice-{uuid4().hex}.wav"
        return _cached(key, lambda: _wav_pcm(out, extract_args(path, out, tracks, start=start, duration=length,
                                                                rate=PCM_RATE)))

    return read


def asset_reader(store: Any, asset_id: Any, notes: list[str]) -> Reader | None:
    """Голос из файла библиотеки: секунда файла = время от start слоя."""
    from uuid import uuid4

    from app.settings import settings
    from app.source_audio import extract_args

    try:
        asset = store.get_montage_asset(int(asset_id))
    except (KeyError, TypeError, ValueError):
        notes.append(f"файла голоса {asset_id} нет — PNGtuber молчит")
        return None
    path = Path(str(asset.get("file_path") or ""))
    if not path.is_file():
        notes.append(f"файла голоса {asset_id} нет на диске — PNGtuber молчит")
        return None

    def read(_sid: Any, start: float, length: float) -> bytes:
        key = ("asset", str(path), path.stat().st_mtime, round(start, 3), round(length, 3))
        out = settings.tmp_dir / f"voice-{uuid4().hex}.wav"
        return _cached(key, lambda: _wav_pcm(out, extract_args(path, out, None, start=start, duration=length,
                                                                rate=PCM_RATE)))

    return read


def layer_voice(
    layer: dict,
    fps: float,
    *,
    points_of: Callable[[list[float]], list[tuple[Any, float] | None]],
    read: Reader | None,
    window: tuple[float, float] | None = None,
) -> dict:
    """Состояния PNGtuber по кадрам слоя. ``points_of`` — моменты шкалы слоя → (источник,
    секунда); у голоса из файла — (None, время от start слоя)."""
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    cfg = voice_config(props)
    t0, count = layer_frames(layer, fps, window)
    times = [t0 + k / fps for k in range(count)]
    if read is None or not times:
        return track_from_levels([None] * count, t0, fps, cfg)

    def points_for(moments: list[float]) -> list[tuple[Any, float] | None]:
        if cfg["asset_id"]:
            start = float(layer.get("start") or 0.0)
            return [(None, t - start) for t in moments]
        return points_of(moments)

    auto_from = None
    if window is not None and cfg["threshold_db"] == VOICE_AUTO:
        # Порог "auto" — по всему слою, как в рендере: по окну в секунду (сплошная речь или
        # сплошная пауза) он вышел бы другим, и кадр агента разошёлся бы с роликом.
        full_t0, full_count = layer_frames(layer, fps)
        whole = [full_t0 + k / fps for k in range(full_count)]
        auto_from = levels_for_points(points_for(whole), fps, read)
    return track_from_levels(levels_for_points(points_for(times), fps, read), t0, fps, cfg, auto_from)


def plan_voice(
    store: Any,
    plan: dict,
    layers: list[dict],
    fps: float,
    scale: str = "clip",
    window: tuple[float, float] | None = None,
) -> tuple[dict[str, dict], list[str]]:
    """``comp.voice`` для видимых PNGtuber клипа: {id слоя: дорожка состояний} и пометки.

    ``scale`` — в какой шкале время слоёв: ``clip`` (до вырезов — редактор, кадр агента) или
    ``final`` (готовый ролик — рендер после вырезов)."""
    from app.timebase import ClipClock

    segments = plan.get("segments") or []
    clock = ClipClock.for_plan(plan, fps)
    notes: list[str] = []
    out: dict[str, dict] = {}
    for layer in layers or []:
        if layer.get("type") != "pngtuber" or layer.get("hidden"):
            continue
        cfg = voice_config(layer.get("props") or {})
        if cfg["asset_id"]:
            read = asset_reader(store, cfg["asset_id"], notes)
        else:
            read = source_reader(store, cfg["track"], notes)
        try:
            out[str(layer["id"])] = layer_voice(
                layer, fps, read=read, window=window,
                points_of=lambda times: frame_sources(clock, segments, times, scale, plan.get("source_id")),
            )
        except Exception as exc:  # noqa: BLE001 - аватар без голоса лучше, чем упавший рендер
            notes.append(f"слой {layer['id']}: голос не разобран ({str(exc)[:160]}) — PNGtuber молчит")
    return out, notes


def attach_voice(store: Any, plan: dict, motion: dict | None, fps: float, scale: str = "clip") -> dict | None:
    """Состояния голоса — в моушн (``voice``), если есть видимый PNGtuber."""
    if not motion or not has_pngtuber(motion.get("layers")):
        return motion
    voice, notes = plan_voice(store, plan, motion.get("layers") or [], fps, scale)
    out = {**motion, "voice": voice}
    if notes:
        out["host_notes"] = [*(motion.get("host_notes") or []), *notes]
    return out


def state_at(track: dict | None, t: float) -> int:
    """Состояние в момент ``t`` — как ``voiceStateAt`` движка (для тестов и проверок)."""
    if not track or not track.get("states"):
        return 0
    k = math.floor((t - float(track.get("t0") or 0)) * float(track.get("fps") or 30) + 1e-6)
    states = str(track["states"])
    return int(states[k]) if 0 <= k < len(states) else 0


# ---------------------------------------------------------------- раскладка по умолчанию


def default_placement(fmt: Any = None) -> dict:
    """Где ставить аватар: правый нижний угол над безопасной зоной площадок, ширина ~300 px
    у 9:16 (у 16:9 — пропорционально опорному кадру)."""
    from app.formats import ref_size, settings_format
    from app.safe_zones import safe_zone

    fmt = settings_format(fmt)
    ref_w, ref_h = ref_size(fmt)
    zone = safe_zone(fmt)
    w = round(300 * min(ref_w, ref_h) / 1080)
    half_w, half_h = w / 2 / ref_w, w / 2 / ref_h
    x = min(float(zone["right"]) - half_w, 1 - half_w - 0.02)
    y = float(zone["bottom"]) - half_h - 0.01
    mark = zone.get("watermark")
    if mark and x + half_w > mark["x0"] and y + half_h > mark["y0"]:
        x = float(mark["x0"]) - half_w - 0.01  # водяной знак канала (16:9) — левее него
    return {"x": round(x, 4), "y": round(y, 4), "w": w}


__all__ = [
    "PNGTUBER_STATES",
    "attach_voice",
    "auto_threshold",
    "default_placement",
    "frame_sources",
    "layer_voice",
    "noise_floor",
    "plan_voice",
    "state_at",
    "voice_levels",
    "voice_states",
]
