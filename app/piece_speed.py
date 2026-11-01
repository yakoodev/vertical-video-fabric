"""Скорость и реверс кусков основы: slow-mo, ускорение, «перемотка назад».

Хранится в ``render_settings.speed`` клипа — ``[{piece, speed?, reverse?, audio?}]``, ``piece`` —
id куска (segment id), как у J/L: кусок подрезали или переставили — скорость едет с ним. Читая
клип, хранилище кладёт поля прямо в кусок (``attach``): ``speed`` (≠ 1), ``reverse`` (True) и
``speed_audio`` — дальше любой код видит их в ``plan["segments"]``.

Шкалы:

* длина куска на шкале клипа — ``(end_sec − start_sec) / speed`` (``seg_length``): слои, камера,
  субтитры, вырезы, переходы и длина ролика считают так;
* момент внутри куска на шкале клипа ``u`` → исходник: вперёд ``start + u·speed``, реверс —
  ``end − u·speed`` (``to_source``);
* кадрирование куска (``focus``) остаётся в секундах ИСХОДНИКА от начала куска: рендер
  ускоряет/разворачивает уже кадрированную картинку.

Звук куска (``audio``): ``tempo`` — та же речь быстрее/медленнее без смены тона (atempo,
по умолчанию у скорости), ``pitch`` — как плёнка (тон ниже на slow-mo), ``mute`` — тишина
(по умолчанию у реверса), ``reverse`` — звук задом наперёд (только с реверсом).

Субтитры: слова куска со скоростью — те же слова с таймингом, растянутым ровно в ``1/speed``
(``app/clip_subtitles.remap_to_pieces`` по штампу кусков), у реверса — не показываются
(``drop_reversed``): речь задом не читается.

Чистые функции (нормализация, длины, фильтры ffmpeg) — без медиа, их проверяют тесты.
"""

from __future__ import annotations

import math
from typing import Any

MIN_SPEED = 0.25
MAX_SPEED = 4.0
# reverse у ffmpeg держит в памяти ВСЕ кадры куска (1080×1920 — ~3 МБ на кадр): длиннее нельзя.
MAX_REVERSE_SEC = 10.0
AUDIO_MODES = ("tempo", "pitch", "mute", "reverse")
DEFAULT_AUDIO = {"speed": "tempo", "reverse": "mute"}


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_speed(raw: Any) -> list[dict]:
    """``[{piece, speed?, reverse?, audio?}]``: speed 0.25..4 (1 не храним), reverse — флаг,
    audio — из ``AUDIO_MODES`` (только если не по умолчанию); пустые записи выброшены,
    повтор куска — последний."""
    out: dict[int, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        piece = _num(item.get("piece"))
        if piece is None or piece != int(piece):
            continue
        entry: dict = {"piece": int(piece)}
        speed = _num(item.get("speed"))
        if speed is not None and abs(speed - 1.0) > 1e-3:
            entry["speed"] = round(min(MAX_SPEED, max(MIN_SPEED, speed)), 3)
        if item.get("reverse") is True:
            entry["reverse"] = True
        audio = str(item.get("audio") or "")
        if audio in AUDIO_MODES and len(entry) > 1:
            if audio == "reverse" and not entry.get("reverse"):
                audio = ""
            if audio and audio != default_audio(entry):
                entry["audio"] = audio
        if len(entry) > 1:
            out[entry["piece"]] = entry
        else:
            out.pop(entry["piece"], None)
    return list(out.values())


def default_audio(entry: dict) -> str:
    return DEFAULT_AUDIO["reverse"] if entry.get("reverse") else DEFAULT_AUDIO["speed"]


def attach(segments: list[dict], render: Any) -> list[dict]:
    """Куски клипа с полями скорости из ``render_settings.speed`` (у кусков без неё — как были)."""
    raw = render.get("speed") if isinstance(render, dict) else None
    entries = {e["piece"]: e for e in normalize_speed(raw)}
    if not entries:
        return segments
    out = []
    for seg in segments:
        entry = entries.get(seg.get("id"))
        if entry:
            seg = {**seg, **({"speed": entry["speed"]} if "speed" in entry else {}),
                   **({"reverse": True} if entry.get("reverse") else {}),
                   "speed_audio": entry.get("audio") or default_audio(entry)}
        out.append(seg)
    return out


def seg_speed(seg: Any) -> float:
    value = _num(seg.get("speed")) if isinstance(seg, dict) else None
    if value is None or value <= 0:
        return 1.0
    return min(MAX_SPEED, max(MIN_SPEED, value))


def is_reversed(seg: Any) -> bool:
    return isinstance(seg, dict) and seg.get("reverse") is True


def is_retimed(seg: Any) -> bool:
    """Кусок ускорен/замедлен или развёрнут — рендерится не как прежде."""
    return seg_speed(seg) != 1.0 or is_reversed(seg)


def seg_length(seg: dict) -> float:
    """Длина куска на шкале клипа, с."""
    return max(0.0, float(seg["end_sec"]) - float(seg["start_sec"])) / seg_speed(seg)


def spans(segments: list[dict]) -> list[tuple[float, float, float]]:
    """``[(start_sec, end_sec, speed)]`` — для ``motion.clip_timeline`` и подобных."""
    return [(float(s["start_sec"]), float(s["end_sec"]), seg_speed(s)) for s in segments]


def to_source(seg: dict, u: float) -> float:
    """Момент куска на шкале клипа (секунды от его начала) → секунда исходника."""
    speed = seg_speed(seg)
    if is_reversed(seg):
        return float(seg["end_sec"]) - float(u) * speed
    return float(seg["start_sec"]) + float(u) * speed


def audio_mode(seg: dict) -> str | None:
    """Что со звуком куска: None — как всегда, иначе из ``AUDIO_MODES``."""
    if not is_retimed(seg):
        return None
    mode = str(seg.get("speed_audio") or "")
    if mode not in AUDIO_MODES or (mode == "reverse" and not is_reversed(seg)):
        mode = default_audio({"reverse": is_reversed(seg)})
    return mode


def atempo_chain(speed: float) -> str:
    """atempo понимает 0.5..2 за раз (у старых ffmpeg) — цепочка для 0.25..4."""
    parts = []
    rest = float(speed)
    while rest < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        rest /= 0.5
    while rest > 2.0 + 1e-9:
        parts.append("atempo=2")
        rest /= 2.0
    if abs(rest - 1.0) > 1e-6:
        parts.append(f"atempo={rest:.6g}")
    return ",".join(parts) or "anull"


def video_chain(seg: dict, fps: float) -> str | None:
    """Хвост видеографа куска: реверс и/или скорость (кадр уже кадрирован и в размере выхода),
    кадров — ровно ``fps`` (slow-mo повторяет кадры, ускорение их пропускает)."""
    if not is_retimed(seg):
        return None
    speed = seg_speed(seg)
    chain = ["reverse"] if is_reversed(seg) else []
    chain.append(f"setpts=(PTS-STARTPTS)/{speed:.6g}" if speed != 1.0 else "setpts=PTS-STARTPTS")
    chain.append(f"fps={fps:.6g}")
    # fps на конце потока может не дотянуть последний кадр: добиваем парой повторов последнего
    # (бесконечный tpad вешал ffmpeg с -shortest), длину режет -frames:v рендера.
    chain.append("tpad=stop_mode=clone:stop=3")
    return ",".join(chain)


def audio_chain(seg: dict, sample_rate: int = 48000) -> str | None:
    """Хвост звукового графа куска (вход — звук куска как всегда, выход — нужной длины)."""
    mode = audio_mode(seg)
    if mode is None:
        return None
    speed = seg_speed(seg)
    chain = []
    if mode == "mute":
        chain.append("volume=0")
        if speed != 1.0:
            chain.append(atempo_chain(speed))
    elif mode == "reverse":
        chain.append("areverse")
        if speed != 1.0:
            chain.append(atempo_chain(speed))
    elif mode == "pitch" and speed != 1.0:
        chain.append(f"asetrate={sample_rate}*{speed:.6g},aresample={sample_rate}")
    elif speed != 1.0:
        chain.append(atempo_chain(speed))
    # Звук исходника бывает короче картинки на кадр (aac), а с -shortest он и резал кусок:
    # добиваем тишиной ровно до длины куска на шкале клипа.
    chain.append(f"asetpts=PTS-STARTPTS,apad=whole_dur={seg_length(seg):.3f}")
    return ",".join(chain)


def drop_reversed(payload: Any, parts: list[dict]) -> Any:
    """Слова и строки субтитров на развёрнутых частях (шкала «части подряд») — прочь."""
    if not isinstance(payload, dict) or not any(is_reversed(p) for p in parts):
        return payload
    windows, acc = [], 0.0
    for part in parts:
        length = seg_length(part)
        if is_reversed(part):
            windows.append((acc, acc + length))
        acc += length

    def keep(item: Any) -> bool:
        if not isinstance(item, dict):
            return True
        a, b = _num(item.get("start")), _num(item.get("end"))
        if a is None or b is None:
            return True
        mid = (a + b) / 2
        return not any(lo <= mid < hi for lo, hi in windows)

    return {**payload, "words": [w for w in payload.get("words") or [] if keep(w)],
            "lines": [line for line in payload.get("lines") or [] if keep(line)]}


def describe(seg: dict) -> str:
    """«×0.5», «реверс ×2», «» — подпись куска."""
    bits = []
    if is_reversed(seg):
        bits.append("реверс")
    if seg_speed(seg) != 1.0:
        bits.append(f"×{seg_speed(seg):g}")
    return " ".join(bits)
