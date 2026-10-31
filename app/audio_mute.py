"""Приглушить звук исходника на отрезках, не трогая картинку.

Старый монтаж вшивает эффекты в звук: агент-монтажёр вырезал их вместе с
картинкой (find_noises → apply_cuts), даже когда кадр был нужен. Теперь
``render_settings.mute`` — отрезки клипа (секунды ДО вырезов, как у слоёв) с
тишиной на месте звука исходника; звуки слоёв и музыка ложатся позже и играют.
Края — короткие рампы, без щелчка.

Отрезок может нести метку того, кто его поставил: ``{start, end, by: "censor", level: 2}``
— глушение мата (app/censor.py). Помеченные не сливаются ни с чем (повторный запуск цензуры
заменяет только свои, по метке), ручные — сливаются между собой, как раньше. Пересечение
ручного с помеченным рендеру не мешает: множители громкости просто перемножаются.

``track`` (номер звуковой дорожки исходника, 0..7) — тишина/громкость только этой дорожки: мат
глушится на микрофоне, игра звучит дальше. Такие отрезки рендер накладывает на дорожку ДО
смешивания (app/audio_edit.py, по секундам исходника), а не на готовый звук; сливаются они
только с отрезками той же дорожки. Без ``track`` — весь звук исходника, как раньше.

``style`` (только у помеченных) — чем заполнена заглушённая середина слова (``MUTE_STYLES``):
``silence`` — тишина (отрезок без стиля — тоже тишина: старые клипы звучат как раньше), ``duck`` —
приглушить на ``DUCK_DB``, не в ноль, с плавными краями ``DUCK_RAMP_SEC``; это громкость, как у тишины. ``beep``, ``noise``, ``reverse``,
``lowpass``, ``stretch`` — эффекты на отрезке звукового монтажа (app/audio_edit.py, ``censor_fx``):
рендер куска накладывает их на дорожку ``track`` или на весь звук исходника, финальный проход
тишины такие отрезки пропускает.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

# Мат глушится пословно (app/censor.py): у клипа на минуту бывает 20–30 таких слов.
MAX_MUTES = 60
MIN_MUTE_SEC = 0.05
RAMP_SEC = 0.03
MUTE_TAGS = ("censor",)
# volume с eval=frame считает множитель раз на кадр звука: у aac кадр 21 мс, у wav — до 85 мс, и
# края тишины/приглушения (рампы 30–50 мс) выходили ступенькой и сдвигались на кадр. Кадры по
# 256 сэмплов (5 мс) перед volume — края плавные и на своём месте.
ENVELOPE_FRAMES = "asetnsamples=n=256:p=0"

# Чем заполнить заглушённую середину слова мата (см. вверху). Порядок — для подсказок и UI.
MUTE_STYLES: dict[str, str] = {
    "silence": "тишина",
    "beep": "тихий писк (~900 Гц, −18 dBFS)",
    "noise": "тихий розовый шум",
    "reverse": "середина задом наперёд",
    "lowpass": "«под водой» (lowpass ~400 Гц)",
    "stretch": "растянута с понижением тона",
    "duck": "приглушена на −35 дБ, не в ноль",
}
# Было −25 дБ: владелец приглушения не услышал («у 7 нет утки»). Замер: внутри отрезка −25 дБ ровно,
# но огибающая считалась раз на кадр звука (см. ENVELOPE_FRAMES) — громкая часть середины слова
# оставалась целой, в среднем выходило −8…−20 дБ; а ослабленная на 25 дБ речь всё ещё громче фона
# стрима. −35 дБ и края 50 мс по smoothstep — провал слышен, середина не обрывается щелчком.
DUCK_DB = -35.0
DUCK_RAMP_SEC = 0.05
# Громкость середины у стилей-громкостей; остальные стили — эффекты (app/audio_edit.censor_fx).
VOLUME_STYLES: dict[str, float] = {"silence": 0.0, "duck": round(10 ** (DUCK_DB / 20), 5)}


def span_style(item: Any) -> str:
    """Стиль отрезка (``style`` из MUTE_STYLES); нет или неизвестный — ``silence``, как раньше."""
    style = item.get("style") if isinstance(item, dict) else None
    return style if style in MUTE_STYLES else "silence"


def is_fx_style(item: Any) -> bool:
    """Отрезок заполняется эффектом звукового монтажа (рендер куска), а не громкостью."""
    return span_style(item) not in VOLUME_STYLES


def span_floor(item: Any) -> float:
    """Громкость внутри отрезка-громкости: 0 — тишина, у ``duck`` — −25 дБ."""
    return VOLUME_STYLES.get(span_style(item), 0.0)


def _mute_tag(item: dict) -> dict:
    """Метка отрезка (``by`` из MUTE_TAGS, ``level`` 1–3, ``style``) или {} — ручной отрезок."""
    by = item.get("by")
    if by not in MUTE_TAGS:
        return {}
    tag: dict = {"by": by}
    try:
        level = int(item.get("level"))
    except (TypeError, ValueError):
        level = 0
    if 1 <= level <= 3:
        tag["level"] = level
    if item.get("style") in MUTE_STYLES:
        tag["style"] = item["style"]
    return tag


MAX_TRACK = 7


def span_track(item: Any) -> int | None:
    """Дорожка отрезка (``track`` 0..7) или None — весь звук исходника."""
    value = item.get("track") if isinstance(item, dict) else None
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        track = int(value)
    except (TypeError, ValueError):
        return None
    return track if 0 <= track <= MAX_TRACK else None


def _with_track(track: int | None) -> dict:
    return {} if track is None else {"track": track}


def normalize_mute(raw: Any) -> list[dict]:
    """``[{start, end}]`` по порядку, ручные слиты, каждый ≥ MIN_MUTE_SEC, не больше MAX_MUTES.

    Помеченные (``by``) остаются отдельными отрезками со своей меткой; точный повтор — один.
    Ручные сливаются только с отрезками той же дорожки (``track``; без неё — весь звук)."""
    spans = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            a, b = float(item.get("start")), float(item.get("end"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(b)):
            continue
        a, b = max(0.0, a), max(0.0, b)
        if b - a >= MIN_MUTE_SEC:
            spans.append((a, b, _mute_tag(item), span_track(item)))
    merged: list[list] = []
    last_plain: dict = {}
    seen: set = set()
    for a, b, tag, track in sorted(spans, key=lambda s: (s[0], s[1], -1 if s[3] is None else s[3])):
        if tag:
            key = (round(a, 3), round(b, 3), tag.get("by"), track)
            if key not in seen:
                seen.add(key)
                merged.append([a, b, tag, track])
        elif track in last_plain and a <= merged[last_plain[track]][1] + 1e-9:
            merged[last_plain[track]][1] = max(merged[last_plain[track]][1], b)
        else:
            merged.append([a, b, tag, track])
            last_plain[track] = len(merged) - 1
    return [{"start": round(a, 3), "end": round(b, 3), **tag, **_with_track(track)}
            for a, b, tag, track in merged[:MAX_MUTES]]


def mute_volume_expr(spans: list[tuple]) -> str:
    """Выражение громкости ffmpeg: 0 внутри отрезков, 1 вне, рампы RAMP_SEC снаружи краёв.
    ``(a, b, floor)`` с ``floor`` > 0 — приглушение (duck): внутри ``floor``, рампы DUCK_RAMP_SEC
    по smoothstep."""
    factors = []
    for span in spans:
        a, b = span[0], span[1]
        floor = float(span[2]) if len(span) > 2 else 0.0
        if floor <= 0:
            factors.append(f"max(clip(({a:.3f}-t)/{RAMP_SEC},0,1),clip((t-{b:.3f})/{RAMP_SEC},0,1))")
            continue
        u = f"max(clip(({a:.3f}-t)/{DUCK_RAMP_SEC},0,1),clip((t-{b:.3f})/{DUCK_RAMP_SEC},0,1))"
        factors.append(f"({floor:.5f}+{1 - floor:.5f}*{u}*{u}*(3-2*{u}))")
    return "*".join(factors) or "1"


def final_pass_mutes(mute: Any, fx_rendered: bool = True) -> list[dict]:
    """Отрезки для финального прохода тишины (весь звук готового клипа): без ``track`` и не
    эффекты. ``fx_rendered`` False — эффекты звукового монтажа к клипу не применялись (холст):
    тогда их отрезки — тишиной, чтобы мат всё равно не прозвучал."""
    out = []
    for m in normalize_mute(mute):
        if m.get("track") is not None:
            continue
        if is_fx_style(m):
            if fx_rendered:
                continue
            m = {**m, "style": "silence"}
        out.append(m)
    return out


def build_mute_args(clip_path: Path, output_path: Path, spans: list[tuple[float, float]]) -> list[str] | None:
    """ffmpeg: звук клипа с тишиной на ``spans`` (секунды готового клипа), видео копией."""
    if not spans:
        return None
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path),
        "-map", "0:v:0", "-map", "0:a:0",
        "-af", f"{ENVELOPE_FRAMES},volume='{mute_volume_expr(spans)}':eval=frame",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path),
    ]


# --------------------------------------------------------------------------- #
# Громкость исходника по отрезкам: поднять тихое, приглушить громкое
# --------------------------------------------------------------------------- #

# Агент стримов: «поднять тихий кусок нечем — только mute». ``render_settings.gain`` —
# отрезки ``{start, end, gain_db}`` (шкала clip: секунды ДО вырезов, с наплывами, как у
# mute и слоёв). Громкость задаёт число в дБ (код), края — рампы, как у тишины.
# Потом рендер выравнивает громкость всего ролика (−14 LUFS), так что подъём куска
# меняет баланс внутри клипа, а не громкость ролика целиком.
MAX_GAINS = 20
MIN_GAIN_SEC = 0.1
GAIN_MIN_DB = -30.0
GAIN_MAX_DB = 20.0
GAIN_RAMP_SEC = 0.05


def normalize_gain(raw: Any) -> list[dict]:
    """``[{start, end, gain_db}]`` по порядку, без перекрытий (позднее начало подрезается
    концом прежнего той же дорожки), каждый ≥ MIN_GAIN_SEC, gain_db в пределах и не 0, не
    больше MAX_GAINS. ``track`` — громкость одной дорожки исходника (см. вверху)."""
    spans = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            a, b, g = float(item.get("start")), float(item.get("end")), float(item.get("gain_db"))
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(a) and math.isfinite(b) and math.isfinite(g)):
            continue
        g = round(min(GAIN_MAX_DB, max(GAIN_MIN_DB, g)), 1)
        a, b = max(0.0, a), max(0.0, b)
        if b - a >= MIN_GAIN_SEC and g != 0:
            spans.append((a, b, g, span_track(item)))
    out: list[dict] = []
    last_end: dict = {}
    for a, b, g, track in sorted(spans, key=lambda s: (s[0], s[1], s[2], -1 if s[3] is None else s[3])):
        if track in last_end and a < last_end[track]:
            a = last_end[track]
        if b - a >= MIN_GAIN_SEC:
            out.append({"start": round(a, 3), "end": round(b, 3), "gain_db": g, **_with_track(track)})
            last_end[track] = b
    return out[:MAX_GAINS]


def gain_volume_expr(spans: list[tuple[float, float, float]]) -> str:
    """Выражение громкости ffmpeg: 10^(g/20) внутри отрезков, 1 вне, рампы GAIN_RAMP_SEC по краям."""
    factors = []
    for a, b, g in spans:
        k = 10 ** (g / 20)
        inside = f"min(clip((t-{a:.3f})/{GAIN_RAMP_SEC},0,1),clip(({b:.3f}-t)/{GAIN_RAMP_SEC},0,1))"
        factors.append(f"(1+{k - 1:.5f}*{inside})")
    return "*".join(factors) or "1"


def build_source_volume_args(clip_path: Path, output_path: Path, mutes: list[tuple[float, float]],
                             gains: list[tuple[float, float, float]]) -> list[str] | None:
    """ffmpeg: звук клипа с тишиной на ``mutes`` и громкостью на ``gains`` (секунды готового
    клипа), видео копией. Подъём громче 0 дБ — с лимитером, чтобы пики не хрипели."""
    if not mutes and not gains:
        return None
    parts = [mute_volume_expr(mutes)] if mutes else []
    if gains:
        parts.append(gain_volume_expr(gains))
    chain = f"{ENVELOPE_FRAMES},volume='{'*'.join(parts)}':eval=frame"
    if any(g > 0 for _a, _b, g in gains):
        chain += ",alimiter=limit=0.95:level=disabled"
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(clip_path),
        "-map", "0:v:0", "-map", "0:a:0",
        "-af", chain,
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output_path),
    ]
