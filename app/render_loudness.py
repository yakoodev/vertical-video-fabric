"""Рендер: расчёт громкости, лимитер, хвост звука. Чистые функции и замер (``app/render.py``)."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Any, Callable


# Тише этого (LUFS) — тишина: loudnorm намерит -inf и второй проход упадёт.
LOUDNORM_SILENCE_LUFS = -70.0


# Тихая запись голоса (втубер на зелёнке, −32 LUFS) с потолком +12 выходила на −21.
MAX_LOUDNESS_BOOST_DB = 20.0
# Тише этого — почти тишина или шум: поднимать нечего, не превращаем её в шипение.
SILENT_LUFS = -45.0


def linear_gain_db(measured: dict, target: float) -> float:
    """Одна поправка громкости на весь клип: к цели по LUFS, но не больше потолка
    (+20 дБ; почти тишину не поднимаем вовсе) — пики дальше держит лимитер."""
    measured_i = float(measured["input_i"])
    gain = target - measured_i
    if measured_i < SILENT_LUFS:
        gain = min(gain, 0.0)
    return max(-20.0, min(MAX_LOUDNESS_BOOST_DB, gain))


LOUDNESS_PEAK_DB = -1.5  # потолок true peak готового файла (dBTP)
# alimiter смотрит только на сэмплы: межвыборочные пики (true peak) он пропускает, а
# пересэмплирование и AAC добавляют сверху — QC видел +1.3 dBTP при потолке −1.5 (удар
# эффекта на дропе музыки). Поэтому лимитер работает на ×4 частоте (как измеритель
# true peak по BS.1770) и с запасом на кодек: −2 dBTP.
TRUE_PEAK_LIMIT_DB = -2.0
TRUE_PEAK_OVERSAMPLE = 4
OUTPUT_SAMPLE_RATE = 48000
# Проверка готового файла: пик всё равно выше потолка — второй проход с потолком
# ниже на перебор и ещё на столько (дБ), но не ниже TRUE_PEAK_FLOOR_DB.
TRUE_PEAK_RETRY_MARGIN_DB = 0.3
TRUE_PEAK_FLOOR_DB = -6.0
TRUE_PEAK_TOLERANCE_DB = 0.05


def true_peak_limiter(ceiling_db: float = TRUE_PEAK_LIMIT_DB) -> str:
    """Лимитер по true peak: ×4 апсемплинг → alimiter с потолком ``ceiling_db`` → 48 кГц.

    На ×4 частоте сэмплы ложатся между исходными — лимитер видит те пики, что
    измеритель true peak (и ЦАП плеера) восстановит между сэмплами."""
    limit = 10 ** (max(TRUE_PEAK_FLOOR_DB, min(0.0, float(ceiling_db))) / 20)
    return (
        f"aresample={OUTPUT_SAMPLE_RATE * TRUE_PEAK_OVERSAMPLE},"
        f"alimiter=limit={limit:.4f}:attack=5:release=80:level=disabled,"
        f"aresample={OUTPUT_SAMPLE_RATE}"
    )


LOUDNESS_LIMITER = true_peak_limiter()


def true_peak_retry_ceiling(measured_tp: Any, ceiling_db: float = TRUE_PEAK_LIMIT_DB) -> float | None:
    """Потолок лимитера для повторного прохода, если готовый файл всё-таки пробил
    ``LOUDNESS_PEAK_DB`` (замер true peak после кодека); None — пик в норме."""
    try:
        peak = float(measured_tp)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(peak) or peak <= LOUDNESS_PEAK_DB + TRUE_PEAK_TOLERANCE_DB:
        return None
    lowered = float(ceiling_db) - (peak - LOUDNESS_PEAK_DB) - TRUE_PEAK_RETRY_MARGIN_DB
    lowered = round(max(TRUE_PEAK_FLOOR_DB, lowered), 2)
    return lowered if lowered < float(ceiling_db) else None
# Сколько дБ пиков лимитер срезает без заметной потери громкости (≤ ~1 LU). Речь
# почти всегда укладывается — её цепочка прежняя: volume + лимитер.
LIMITER_ROOM_DB = 3.0
# Музыка с резкими ударами без голоса: пики на 9 дБ выше потолка — лимитер срезал удары
# и терял ~3 LU (−17.2 вместо −14). Тогда перед лимитером — компрессор с порогом чуть
# выше цели (тело трека не трогает), а потерю громкости возвращает подъём после него
# по контрольному замеру.
COMPRESSOR_ABOVE_TARGET_DB = 4.0
# Подъём после компрессора доходит до громкости не целиком (часть снова срежет лимитер):
# на ударных +5 дБ подъёма дали +4.5 LU.
MAKEUP_EFFICIENCY = 0.85
MAX_MAKEUP_DB = 12.0
# Сверх потолка +20 дБ — ещё не больше стольких, и только в запас пиков.
MAX_EXTRA_BOOST_DB = 10.0
# Недобор громкости после контрольного замера, который ещё терпим (LU).
LOUDNESS_TOLERANCE_LU = 0.5
# Перебор пиков, на котором лимитер почти не работает, — контрольный замер не нужен (дБ).
LIMITER_IDLE_DB = 1.0
# Сколько громкости лимитер без компрессора может потерять, пока это терпимо (LU).
LIMITER_LOSS_LU = 1.0


def loudness_plan(measured: dict, target: float) -> dict:
    """Как довести клип до цели: ``{gain_db, compressor, makeup_db, overshoot_db, aim_lufs}``.

    Усиление линейное (``linear_gain_db``, потолок +20 дБ). Очень тихий клип (музыка
    без голоса, приглушённая под речь, которой нет) упирался в потолок: +20 дБ — и
    −16.8 LUFS при пике −4 dBTP, хотя запас по пикам был. Сверх потолка поднимаем
    ещё до ``MAX_EXTRA_BOOST_DB``, но только в пределах запаса пиков (лимитер срежет
    не больше ``LIMITER_ROOM_DB``). Если пики выходят за потолок больше чем на
    ``LIMITER_ROOM_DB`` — одному лимитеру не справиться без потери громкости, и перед
    ним встаёт мягкий компрессор (порог — цель + 4 дБ, чем больше перебор, тем круче).
    Иначе компрессора нет — у обычного клипа с речью цепочка ровно прежняя."""
    gain = linear_gain_db(measured, target)
    plan: dict = {"gain_db": round(gain, 2), "compressor": None, "makeup_db": 0.0, "overshoot_db": 0.0}
    try:
        peak = float(measured["input_tp"])
        current = float(measured["input_i"])
    except (KeyError, TypeError, ValueError):
        return plan
    if not (math.isfinite(peak) and math.isfinite(current)):
        return plan
    if current >= SILENT_LUFS and gain >= MAX_LOUDNESS_BOOST_DB:
        room = LOUDNESS_PEAK_DB + LIMITER_ROOM_DB - (peak + gain)
        gain += max(0.0, min(target - current - gain, room, MAX_EXTRA_BOOST_DB))
        plan["gain_db"] = round(gain, 2)
    overshoot = peak + gain - LOUDNESS_PEAK_DB
    plan["overshoot_db"] = round(overshoot, 2)
    # Куда целится линейная поправка: подъём после компрессора её не превышает
    # (тихую шумную запись сверх потолка усиления не тянем).
    plan["aim_lufs"] = round(min(target, current + gain), 2)
    if overshoot > LIMITER_ROOM_DB:
        plan["compressor"] = _compressor(target, overshoot)
    return plan


def _compressor(target: float, overshoot: float) -> dict:
    """Мягкий компрессор перед лимитером: порог чуть выше цели, круче при большом переборе."""
    return {
        "threshold_db": round(target + COMPRESSOR_ABOVE_TARGET_DB, 2),
        "ratio": round(min(6.0, max(2.0, overshoot / 2)), 2),
        "attack_ms": 1.0,
        "release_ms": 200.0,
        "knee_db": 4.0,
    }


def fit_loudness(measured: dict, target: float, measure: Callable[[list[str]], dict]) -> dict:
    """План громкости с контрольным замером (``measure(filters)`` — loudnorm-замер звука
    после цепочки).

    Пики не выходят за потолок больше чем на ``LIMITER_IDLE_DB`` — лимитеру делать почти
    нечего, замер не нужен: цепочка прежняя. Иначе меряем, что вышло: без компрессора
    недобор меньше ``LIMITER_LOSS_LU`` — оставляем как есть (обычная речь), больше —
    добавляем компрессор и меряем снова; с компрессором недобор возвращает подъём."""
    plan = loudness_plan(measured, target)
    if float(plan.get("overshoot_db") or 0.0) <= LIMITER_IDLE_DB:
        return plan
    after = measure(loudness_filters(plan))
    if not plan.get("compressor"):
        try:
            shortfall = float(plan.get("aim_lufs", target)) - float(after["input_i"])
        except (KeyError, TypeError, ValueError):
            return plan
        if not math.isfinite(shortfall) or shortfall < LIMITER_LOSS_LU:
            return plan
        plan = {**plan, "compressor": _compressor(target, float(plan["overshoot_db"]))}
        after = measure(loudness_filters(plan))
    return correct_loudness_plan(plan, after, target)


def loudness_filters(plan: dict) -> list[str]:
    """Фильтры ffmpeg плана громкости: volume → [компрессор → подъём] → лимитер."""
    filters = [f"volume={float(plan['gain_db']):.2f}dB"]
    comp = plan.get("compressor")
    if comp:
        threshold = 10 ** (float(comp["threshold_db"]) / 20)  # acompressor ждёт линейный порог
        filters.append(
            f"acompressor=threshold={threshold:.5f}:ratio={float(comp['ratio']):g}:attack={float(comp['attack_ms']):g}"
            f":release={float(comp['release_ms']):g}:knee={float(comp['knee_db']):g}:detection=peak"
        )
    makeup = float(plan.get("makeup_db") or 0.0)
    if makeup > 0:
        filters.append(f"volume={makeup:.2f}dB")
    ceiling = plan.get("ceiling_db")
    filters.append(LOUDNESS_LIMITER if ceiling is None else true_peak_limiter(float(ceiling)))
    lift = float(plan.get("post_gain_db") or 0.0)
    if lift > 0:
        # Подъём после лимитера по замеру готового файла (finished_loudness_lift): пики уже
        # известны и поднимаются ровно на столько же — лимитер его не съест.
        filters.append(f"volume={lift:.2f}dB")
    return filters


# Подъём готового файла — до потолка пика минус этот запас (дБ).
LOUDNESS_LIFT_MARGIN_DB = 0.3
# Битрейт звука финального прохода. На плотной яркой музыке AAC 192k добавлял к пику
# +2.3 дБ сверх лимитера (−2.0 → +0.3 dBTP), повтор опускал потолок до −5 и терял ~1.3 LU
# (синтетика клипа 575: −15.2 LUFS); на 256k перебор ~0.2 дБ — повтор не нужен.
FINAL_AUDIO_BITRATE = "256k"


def finished_loudness_lift(after: dict, plan: dict | None, target: float) -> float:
    """На сколько дБ поднять готовый файл линейно (после лимитера), если он тише цели, а пики с запасом.

    Клип 575 (промо на холсте под музыку): −16.2 LUFS при пике −7.2 dBTP — недобор 2.2 LU при
    запасе 5.7 дБ. Кодек добавил к пику сверх лимитера, повтор опустил потолок лимитера (пики
    файла скопились на −7.4 dBFS), громкость ушла, а готовый файл проверялся только на перебор
    пика. Теперь и на недобор: подъём — на недобор, но не выше потолка пика. Без плана (громкость
    была в норме) и для тихой записи сверх её потолка усиления (aim_lufs) — не поднимаем."""
    if not plan:
        return 0.0
    try:
        got = float(after["input_i"])
        peak = float(after["input_tp"])
    except (KeyError, TypeError, ValueError):
        return 0.0
    if not (math.isfinite(got) and math.isfinite(peak)) or got < LOUDNORM_SILENCE_LUFS:
        return 0.0
    shortfall = min(target, float(plan.get("aim_lufs", target))) - got
    room = LOUDNESS_PEAK_DB - LOUDNESS_LIFT_MARGIN_DB - peak
    lift = min(shortfall, room)
    return round(lift, 2) if shortfall >= LOUDNESS_TOLERANCE_LU and lift >= 0.1 else 0.0


def correct_loudness_plan(plan: dict, after: dict, target: float) -> dict:
    """Поправка по контрольному замеру цепочки ``plan``: недобор громкости — подъём
    после компрессора (с запасом на лимитер, не больше ``MAX_MAKEUP_DB``)."""
    if not plan.get("compressor"):
        return plan
    try:
        got = float(after["input_i"])
    except (KeyError, TypeError, ValueError):
        return plan
    shortfall = min(target, float(plan.get("aim_lufs", target))) - got
    if not math.isfinite(shortfall) or shortfall < LOUDNESS_TOLERANCE_LU:
        return plan
    makeup = min(MAX_MAKEUP_DB, float(plan.get("makeup_db") or 0.0) + shortfall / MAKEUP_EFFICIENCY)
    return {**plan, "makeup_db": round(makeup, 2)}


def _measure_loudness(path: Path, target: float, filters: list[str] | None = None) -> dict:
    """Замер loudnorm (первый проход) звука файла — как есть или после ``filters``."""
    chain = ",".join([*(filters or []), f"loudnorm=I={target}:TP={LOUDNESS_PEAK_DB}:LRA=11:print_format=json"])
    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", chain, "-f", "null", "-"],
        capture_output=True, text=True, timeout=60 * 10,
    )
    tail = probe.stderr[probe.stderr.rfind("{"):]
    return json.loads(tail[: tail.find("}") + 1])


def loudnorm_needed(measured: dict, target: float) -> bool:
    """Нужен ли второй проход loudnorm по замеру первого.

    Тихий/немой клип: input_i = "-inf" (или ниже -70) — поднимать нечего, а
    ffmpeg с measured_I=-inf падает. Тогда звук оставляем как есть.
    """
    try:
        current = float(measured["input_i"])
        peak = float(measured["input_tp"])
        lra = float(measured["input_lra"])
        thresh = float(measured["input_thresh"])
        offset = float(measured["target_offset"])
    except (KeyError, TypeError, ValueError):
        return False
    if not all(math.isfinite(v) for v in (current, peak, lra, thresh, offset)):
        return False
    if current < LOUDNORM_SILENCE_LUFS:
        return False
    return not (abs(current - target) < 1.0 and peak <= LOUDNESS_PEAK_DB)


# Короткое затухание звука в самом конце ролика: финал не «щёлкает» обрывом
# слова, даже если последний кусок резали вплотную к речи.
END_AUDIO_FADE_SEC = 0.35  # затухание кончается ровно на последнем звуке клипа; без музыки стоп-кадр — тишина
# Звук длиннее картинки на столько (с) и больше — это хвост музыки на стоп-кадр
# (_music_filter с hold_sec), а не погрешность контейнера (у AAC — до ~0.03 с).
MUSIC_TAIL_MIN_SEC = 0.1


def audible(measured: dict) -> bool:
    """Есть ли в клипе слышимый звук (по замеру loudnorm)."""
    try:
        current = float(measured["input_i"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(current) and current >= LOUDNORM_SILENCE_LUFS


def end_fade_filter(duration: float, fade: float = END_AUDIO_FADE_SEC) -> str:
    """afade на последние ``fade`` секунд (пусто, если ролик слишком короткий)."""
    if duration <= fade * 4 or fade <= 0:
        return ""
    return f"afade=t=out:st={duration - fade:.3f}:d={fade:.3f}"


def stream_durations(meta: Any) -> tuple[float | None, float | None]:
    """Длины видео- и аудиопотока файла (ffprobe ``streams[].duration``); нет — None."""
    raw = getattr(meta, "raw", None) or {}
    found: dict[str, float] = {}
    for stream in raw.get("streams") or []:
        kind = stream.get("codec_type")
        if kind in ("video", "audio") and kind not in found:
            try:
                value = float(stream.get("duration"))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                found[kind] = value
    return found.get("video"), found.get("audio")


def music_tail_sec(meta: Any) -> float:
    """Насколько звук длиннее картинки: хвост музыки на стоп-кадр (0 — хвоста нет)."""
    video, audio = stream_durations(meta)
    if video is None or audio is None:
        return 0.0
    tail = audio - video
    return round(tail, 3) if tail >= MUSIC_TAIL_MIN_SEC else 0.0
