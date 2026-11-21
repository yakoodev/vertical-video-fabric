"""Пресет зума «дыхание» (findings.md п. 54): на склейке 100 %, внутри куска плавно до 120 % с замедлением в
конце (ease-out), на следующей склейке снова 100 %, в самом конце 115 %.

Ключи — формат камеры клипа (``render_settings.camera``: ``{t, zoom, ease}``, ease на левом ключе ведёт
отрезок до следующего, как в ``layer_tools_camera.punch_keys``). Сброс на склейке — пара ключей
(конец куска на его зуме за 1 мс до склейки → 1,0 на склейке). ``check_breathing`` — замер: зум в [1,0; 1,2],
внутри куска монотонно (без рывков), на склейке 1,0, в конце 1,15.
"""

from __future__ import annotations

ZOOM_MIN, ZOOM_PEAK, ZOOM_FINAL = 1.0, 1.2, 1.15
EASE = "outCubic"
RESET_EPS = 0.001
MIN_PIECE = 0.4


def breathing_keys(cuts: list[float], duration: float, peak: float = ZOOM_PEAK, final: float = ZOOM_FINAL) -> list[dict]:
    """Ключи камеры для склеек ``cuts`` (секунды ролика) и длины ``duration``."""
    bounds = [0.0] + sorted(float(t) for t in cuts if MIN_PIECE <= float(t) <= duration - MIN_PIECE) + [float(duration)]
    keys: list[dict] = []
    last = len(bounds) - 2
    for i, (a, b) in enumerate(zip(bounds, bounds[1:])):
        target = final if i == last else peak
        if keys:
            keys.append({"t": round(a - RESET_EPS, 3), "zoom": keys[-1]["zoom"], "ease": "linear"})
        keys.append({"t": round(a, 3), "zoom": ZOOM_MIN, "ease": EASE})
        keys.append({"t": round(b if i == last else b - RESET_EPS * 2, 3), "zoom": target, "ease": "linear"})
    # Пара «конец куска» дублирует ключ цели: оставляем по одному ключу на момент.
    out: list[dict] = []
    for k in keys:
        if out and abs(out[-1]["t"] - k["t"]) < 1e-9:
            out[-1] = k
        else:
            out.append(k)
    return out


def zoom_at(keys: list[dict], t: float) -> float:
    """Зум в момент t: линейно или ease-out (outCubic) по ease левого ключа — как движок."""
    if not keys:
        return 1.0
    if t <= keys[0]["t"]:
        return float(keys[0]["zoom"])
    for a, b in zip(keys, keys[1:]):
        if a["t"] <= t <= b["t"]:
            span = b["t"] - a["t"]
            p = 0.0 if span <= 0 else (t - a["t"]) / span
            if a.get("ease") == "outCubic":
                p = 1 - (1 - p) ** 3
            return float(a["zoom"]) + (float(b["zoom"]) - float(a["zoom"])) * p
    return float(keys[-1]["zoom"])


def check_breathing(keys: list[dict], cuts: list[float], duration: float, step: float = 1 / 30) -> dict:
    """Замер по кадрам: диапазон, монотонность внутри куска (рывок — шаг назад или скачок > 2 %/кадр),
    сброс на склейке и финал."""
    problems: list[str] = []
    bounds = [0.0] + sorted(float(t) for t in cuts if 0 < float(t) < duration) + [float(duration)]
    values = []
    jerks = 0
    for a, b in zip(bounds, bounds[1:]):
        t = a + step
        prev = zoom_at(keys, a + 1e-4)
        if abs(prev - ZOOM_MIN) > 0.01:
            problems.append(f"на склейке {a:.2f} с зум {prev:.3f}, а не 1,0")
        while t < b - step:
            z = zoom_at(keys, t)
            values.append(z)
            if z < prev - 1e-6 or z - prev > 0.02:
                jerks += 1
            prev = z
            t += step
    lo, hi = min(values, default=1.0), max(values, default=1.0)
    if lo < ZOOM_MIN - 1e-6 or hi > ZOOM_PEAK + 1e-6:
        problems.append(f"зум {lo:.3f}–{hi:.3f} вне 1,0–1,2")
    if jerks:
        problems.append(f"рывков внутри кусков: {jerks}")
    end = zoom_at(keys, duration)
    if abs(end - ZOOM_FINAL) > 0.005:
        problems.append(f"в конце зум {end:.3f}, а не 1,15")
    return {"ok": not problems, "problems": problems, "range": [round(lo, 3), round(hi, 3)], "jerks": jerks,
            "final": round(end, 3)}
