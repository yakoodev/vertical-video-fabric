"""Цветокоррекция по кускам основы: экспозиция, контраст, насыщенность, баланс белого и подгонка
кусков друг к другу («auto match»).

Компиляции и сериалы из разных сцен прыгают по цвету: один кусок тёмный и тёплый, следующий —
светлый и холодный. Лук пресета один на весь клип и этого не лечит.

Хранится в ``render_settings.color_fix`` клипа — ``[{piece, exposure?, contrast?, saturation?,
temperature?, tint?}]``, ``piece`` — id куска (segment id), как у скорости (app/piece_speed.py):
кусок подрезали или переставили — коррекция едет с ним. Читая клип, хранилище кладёт поправку в
кусок полем ``color_fix`` (``attach``) — рендер видит её в ``plan["segments"]``.

Поля (всё необязательно, нет — нейтрально):

* ``exposure`` −2..2 — ступени (EV): яркость ×2^exposure (по яркости Y, от уровня чёрного 16);
* ``contrast`` 0.5..1.5 — контраст (eq);
* ``saturation`` 0..2 — насыщенность (eq), 0 — ч/б;
* ``temperature`` −1..1 — теплее (+, к жёлтому/красному) / холоднее (−, к синему);
* ``tint`` −1..1 — к пурпурному (+) / к зелёному (−).

Фильтр куска (``video_filter``): ``format=yuv420p,lutyuv=…,eq=…`` — до лука пресета, на кадре
уже в размере выхода (дёшево). Сдвиг баланса — прямо в каналах U/V: тёплое = V выше, U ниже.

Подгонка (``match``): по нескольким кадрам каждого куска ffmpeg ``signalstats`` меряет среднюю
яркость (YAVG), средние U/V и насыщенность (SATAVG); кускам ставится такая поправка, чтобы их
средние совпали со средними куска-образца (с учётом его собственной поправки). ``strength`` —
доля пути (0..1).

Превью редактора — CSS-фильтр (приближённо): ``brightness``, ``contrast``, ``saturate``, сепия /
поворот тона на баланс.
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any

FIELDS = {
    # имя: (нейтраль, минимум, максимум)
    "exposure": (0.0, -2.0, 2.0),
    "contrast": (1.0, 0.5, 1.5),
    "saturation": (1.0, 0.0, 2.0),
    "temperature": (0.0, -1.0, 1.0),
    "tint": (0.0, -1.0, 1.0),
}
# Сдвиг каналов U/V (8 бит) на единицу temperature / tint.
TEMP_UV = 16.0
TINT_UV = 12.0
# Подгонка: насколько далеко можно уйти (дальше — кадры просто разные по смыслу: ночь и день).
MATCH_MAX_EXPOSURE = 1.5
MATCH_SATURATION = (0.4, 1.8)
SAMPLES = 5
BLACK = 16.0


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_fix(raw: Any) -> dict:
    """Поля поправки: зажаты в рамки, нейтральные выброшены (``{}`` — поправки нет)."""
    out: dict = {}
    if not isinstance(raw, dict):
        return out
    for name, (neutral, lo, hi) in FIELDS.items():
        value = _num(raw.get(name))
        if value is None:
            continue
        value = round(min(hi, max(lo, value)), 3)
        if abs(value - neutral) > 1e-3:
            out[name] = value
    return out


def normalize_color_fix(raw: Any) -> list[dict]:
    """``[{piece, …поля}]``: piece — целый id куска; пустые записи выброшены, повтор — последний."""
    out: dict[int, dict] = {}
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        piece = _num(item.get("piece"))
        if piece is None or piece != int(piece):
            continue
        fix = normalize_fix(item)
        if fix:
            out[int(piece)] = {"piece": int(piece), **fix}
        else:
            out.pop(int(piece), None)
    return list(out.values())


def attach(segments: list[dict], render: Any) -> list[dict]:
    """Куски с полем ``color_fix`` из ``render_settings.color_fix`` (у прочих — как были)."""
    raw = render.get("color_fix") if isinstance(render, dict) else None
    entries = {e["piece"]: e for e in normalize_color_fix(raw)}
    if not entries:
        return segments
    out = []
    for seg in segments:
        entry = entries.get(seg.get("id"))
        if entry:
            seg = {**seg, "color_fix": {k: v for k, v in entry.items() if k != "piece"}}
        out.append(seg)
    return out


def has_fix(seg: Any) -> bool:
    return isinstance(seg, dict) and bool(normalize_fix(seg.get("color_fix")))


def _uv_shift(fix: dict) -> tuple[float, float]:
    t, m = fix.get("temperature", 0.0), fix.get("tint", 0.0)
    return -TEMP_UV * t + TINT_UV * m, TEMP_UV * t + TINT_UV * m


def video_filter(fix: Any) -> str:
    """Цепочка ffmpeg для поправки куска ('' — поправки нет)."""
    fix = normalize_fix(fix)
    if not fix:
        return ""
    parts = ["format=yuv420p"]
    lut = []
    gain = 2.0 ** fix.get("exposure", 0.0)
    if abs(gain - 1.0) > 1e-4:
        lut.append(f"y='clip((val-{BLACK:g})*{gain:.5f}+{BLACK:g},0,255)'")
    du, dv = _uv_shift(fix)
    if abs(du) > 1e-3:
        lut.append(f"u='clip(val+{du:.3f},0,255)'")
    if abs(dv) > 1e-3:
        lut.append(f"v='clip(val+{dv:.3f},0,255)'")
    if lut:
        parts.append("lutyuv=" + ":".join(lut))
    eq = []
    if "contrast" in fix:
        eq.append(f"contrast={fix['contrast']:.3f}")
    if "saturation" in fix:
        eq.append(f"saturation={fix['saturation']:.3f}")
    if eq:
        parts.append("eq=" + ":".join(eq))
    return ",".join(parts)


def preview_css(fix: Any) -> str:
    """CSS-фильтр для превью (приближённо, как LOOK_CSS редактора)."""
    fix = normalize_fix(fix)
    bits = []
    if "exposure" in fix:
        bits.append(f"brightness({2.0 ** fix['exposure']:.3f})")
    if "contrast" in fix:
        bits.append(f"contrast({fix['contrast']:.3f})")
    if "saturation" in fix:
        bits.append(f"saturate({fix['saturation']:.3f})")
    t = fix.get("temperature", 0.0)
    if t > 0:
        bits.append(f"sepia({0.3 * t:.3f})")
    elif t < 0:
        bits.append(f"hue-rotate({12 * t:.1f}deg)")
    if "tint" in fix:
        bits.append(f"hue-rotate({-10 * fix['tint']:.1f}deg)")
    return " ".join(bits)


def describe(fix: Any) -> str:
    fix = normalize_fix(fix)
    names = {"exposure": "эксп", "contrast": "контр", "saturation": "насыщ", "temperature": "темп", "tint": "оттен"}
    return " ".join(f"{names[k]} {v:+g}" if k in ("exposure", "temperature", "tint") else f"{names[k]} ×{v:g}"
                    for k, v in fix.items())


# --------------------------------------------------------------------------- #
# Замер и подгонка
# --------------------------------------------------------------------------- #

_STAT = re.compile(r"lavfi\.signalstats\.(YAVG|UAVG|VAVG|SATAVG)=([0-9.]+)")


def sample_times(start: float, end: float, count: int = SAMPLES) -> list[float]:
    """Моменты замера внутри куска: равномерно, без самых краёв (склейка, затемнение)."""
    length = max(0.0, end - start)
    if length <= 0.2:
        return [start + length / 2]
    count = max(1, min(count, int(length / 0.2)))
    return [round(start + length * (k + 0.5) / count, 3) for k in range(count)]


def measure(source_path: Path, start: float, end: float, crop: dict | None = None,
            count: int = SAMPLES, timeout: float = 60.0) -> dict:
    """Средние YAVG/UAVG/VAVG/SATAVG куска исходника по ``count`` кадрам. ``crop`` — доли
    кадра без чёрных полос (``content_crop`` источника), чтобы полосы не тянули яркость вниз."""
    chain = []
    if crop and all(k in crop for k in ("x", "y", "w", "h")):
        chain.append(f"crop=iw*{float(crop['w']):.5f}:ih*{float(crop['h']):.5f}:"
                     f"iw*{float(crop['x']):.5f}:ih*{float(crop['y']):.5f}")
    chain += ["scale=320:-2", "format=yuv420p", "signalstats", "metadata=print:file=-"]
    sums = {"YAVG": 0.0, "UAVG": 0.0, "VAVG": 0.0, "SATAVG": 0.0}
    got = 0
    for t in sample_times(start, end, count):
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", f"{t:.3f}", "-i", str(source_path),
             "-frames:v", "1", "-vf", ",".join(chain), "-f", "null", "-"],
            capture_output=True, text=True, timeout=timeout,
        )
        found = dict(_STAT.findall(proc.stdout + proc.stderr))
        if "YAVG" not in found:
            continue
        for key in sums:
            sums[key] += float(found.get(key, 0.0))
        got += 1
    if not got:
        raise ValueError("не удалось замерить кадры куска (ffmpeg signalstats)")
    return {"y": round(sums["YAVG"] / got, 2), "u": round(sums["UAVG"] / got, 2), "v": round(sums["VAVG"] / got, 2),
            "sat": round(sums["SATAVG"] / got, 2), "frames": got}


def predict(stats: dict, fix: Any) -> dict:
    """Какими станут средние куска после поправки (та же модель, что ``video_filter``)."""
    fix = normalize_fix(fix)
    gain = 2.0 ** fix.get("exposure", 0.0)
    y = min(255.0, max(0.0, (stats["y"] - BLACK) * gain + BLACK))
    contrast = fix.get("contrast", 1.0)
    y = min(255.0, max(0.0, (y - 128.0) * contrast + 128.0))
    du, dv = _uv_shift(fix)
    s = fix.get("saturation", 1.0)
    u = 128.0 + (stats["u"] + du - 128.0) * s
    v = 128.0 + (stats["v"] + dv - 128.0) * s
    return {"y": round(y, 2), "u": round(u, 2), "v": round(v, 2), "sat": round(stats["sat"] * s, 2)}


def match_fix(stats: dict, target: dict, keep: Any = None, strength: float = 1.0) -> dict:
    """Поправка, после которой средние куска ``stats`` совпадут с ``target`` (``predict`` образца).
    ``keep`` — прежняя поправка куска: её контраст сохраняется."""
    keep = normalize_fix(keep)
    strength = min(1.0, max(0.0, float(strength)))
    contrast = keep.get("contrast", 1.0)
    # Яркость: целевая Y до контраста (контраст куска остаётся его), потом экспозиция.
    want_y = (target["y"] - 128.0) / contrast + 128.0
    exposure = 0.0
    if stats["y"] - BLACK > 1.0 and want_y - BLACK > 1.0:
        exposure = math.log2((want_y - BLACK) / (stats["y"] - BLACK))
    exposure = max(-MATCH_MAX_EXPOSURE, min(MATCH_MAX_EXPOSURE, exposure)) * strength
    saturation = 1.0
    if stats["sat"] > 0.5:
        saturation = max(MATCH_SATURATION[0], min(MATCH_SATURATION[1], target["sat"] / stats["sat"]))
    saturation = 1.0 + (saturation - 1.0) * strength
    # Баланс: средние U/V после насыщенности — как у образца.
    du = ((target["u"] - 128.0) / saturation + 128.0 - stats["u"]) * strength
    dv = ((target["v"] - 128.0) / saturation + 128.0 - stats["v"]) * strength
    temperature = (dv - du) / (2 * TEMP_UV)
    tint = (du + dv) / (2 * TINT_UV)
    return normalize_fix({"exposure": exposure, "contrast": contrast, "saturation": saturation,
                          "temperature": temperature, "tint": tint})
