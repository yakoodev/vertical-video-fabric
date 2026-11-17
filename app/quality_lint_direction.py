"""Линт перед сдачей: направление движения на склейках (проверка ``direction``, из ``app/quality_lint.py``).

Правило монтажа «одно основное направление на ролик» (одобрено владельцем 17.11): если перед склейкой картинка
едет вправо, а после неё — влево (наезд камеры, хлыст, въезд слоя), глаз дёргается — склейка читается как
ошибка, а не как продолжение. Смена нарочно (флешбек, ответ собеседника) — ``reason`` у перехода стыка
(``set_transition join reason``), тогда линт молчит.

Что считается движением картинки (направление — куда едет то, что видит зритель):

* камера (``render.camera``): центр окна уехал вправо — картинка едет влево (и наоборот), по ключам
  с заметным сдвигом (≥ ``CAMERA_MIN`` кадра);
* переход стыка: хлыст ``whippan`` и заливка ``colorfill`` с заданным ``direction``, сдвиговые ``whip``/
  ``slide``/``smooth`` (влево), срез ``slash`` с углом почти по оси;
* слои: вход ``slideLeft`` — едет влево (выход — тот же вход назад: вправо), ключи ``x``/``y`` со сдвигом
  ≥ ``LAYER_MIN`` кадра.

Находки:

* P1 — направление перехода на стыке спорит с движением перед ним или после него (хлыст влево, а камера
  до склейки едет вправо);
* P2 — движение до склейки и после неё в разные стороны по одной оси;
* P2 — хлысты/заливки ролика в разные стороны: «основное направление — влево (3 из 4)».

Время находок — шкала clip (как слои).
"""

from __future__ import annotations

import math
from typing import Any

from app.quality_lint_base import _finding, _num

WINDOW_SEC = 0.6        # движение «у склейки» — столько секунд до и после неё
CAMERA_MIN = 0.02       # сдвиг центра камеры меньше — не движение (доля кадра)
LAYER_MIN = 0.05        # сдвиг слоя по ключам меньше — не движение
MAX_FINDINGS = 10
AXIS_OF = {"left": ("h", -1), "right": ("h", 1), "up": ("v", -1), "down": ("v", 1)}
NAMES = {("h", -1): "влево", ("h", 1): "вправо", ("v", -1): "вверх", ("v", 1): "вниз"}
KEYS_OF = {("h", -1): "left", ("h", 1): "right", ("v", -1): "up", ("v", 1): "down"}
SLIDE_IN = {"slideLeft": ("h", -1), "slideRight": ("h", 1), "slideUp": ("v", -1), "slideDown": ("v", 1)}
# Сдвиговые xfade — картинка уезжает влево.
LEFT_XFADE_KINDS = ("whip", "slide", "smooth")


def _event(t0: float, t1: float, axis: str, sign: int, what: str, weight: float = 1.0) -> dict:
    return {"t0": float(t0), "t1": float(t1), "axis": axis, "sign": int(sign), "what": what, "weight": weight}


def camera_events(camera: Any) -> list[dict]:
    """Отрезки камеры со сдвигом центра: центр едет вправо → картинка влево."""
    keys = sorted((k for k in camera or [] if isinstance(k, dict)), key=lambda k: _num(k.get("t")))
    out = []
    for a, b in zip(keys, keys[1:]):
        t0, t1 = _num(a.get("t")), _num(b.get("t"))
        if t1 <= t0:
            continue
        dx = _num(b.get("x"), 0.5) - _num(a.get("x"), 0.5)
        dy = _num(b.get("y"), 0.5) - _num(a.get("y"), 0.5)
        if max(abs(dx), abs(dy)) < CAMERA_MIN:
            continue
        axis, d = ("h", dx) if abs(dx) >= abs(dy) else ("v", dy)
        sign = -1 if d > 0 else 1
        out.append(_event(t0, t1, axis, sign, f"камера ведёт картинку {NAMES[(axis, sign)]}"))
    return out


def _anim(layer: dict, key: str) -> tuple[str | None, float]:
    anim = layer.get(key) if isinstance(layer.get(key), dict) else None
    if not anim:
        return None, 0.0
    return anim.get("type"), min(3.0, max(0.05, _num(anim.get("duration"), 0.35 if key == "in" else 0.25)))


def layer_events(layers: Any) -> list[dict]:
    """Въезды/выезды слоёв и сдвиги по ключам x/y."""
    out = []
    for layer in layers or []:
        if not isinstance(layer, dict) or layer.get("hidden") or layer.get("type") in ("audio", "captions"):
            continue
        name = str(layer.get("name") or layer.get("id") or "слой")
        start, end = _num(layer.get("start")), _num(layer.get("end"))
        kind, dur = _anim(layer, "in")
        if kind in SLIDE_IN:
            axis, sign = SLIDE_IN[kind]
            out.append(_event(start, start + dur, axis, sign, f"слой {name} въезжает {NAMES[(axis, sign)]}"))
        kind, dur = _anim(layer, "out")
        if kind in SLIDE_IN:
            axis, sign = SLIDE_IN[kind]
            # Выход — тот же вход назад: slideLeft уезжает вправо.
            out.append(_event(end - dur, end, axis, -sign, f"слой {name} уезжает {NAMES[(axis, -sign)]}"))
        keys = layer.get("keys") if isinstance(layer.get("keys"), dict) else {}
        for prop, axis in (("x", "h"), ("y", "v")):
            points = sorted((k for k in keys.get(prop) or [] if isinstance(k, dict)), key=lambda k: _num(k.get("t")))
            for a, b in zip(points, points[1:]):
                d = _num(b.get("v")) - _num(a.get("v"))
                if abs(d) < LAYER_MIN:
                    continue
                sign = 1 if d > 0 else -1
                # Ключи слоя — время от начала слоя.
                out.append(_event(start + _num(a.get("t")), start + _num(b.get("t")), axis, sign,
                                  f"слой {name} едет {NAMES[(axis, sign)]}"))
    return out


def join_motion(transition: dict, index: int) -> dict | None:
    """Направление перехода на стыке кусков ``index`` (None — у перехода нет направления или оно по камере)."""
    from app.transitions import join_kind, join_param, normalize_direction

    kind = join_kind(transition, index)
    direction = normalize_direction(join_param(transition, index, "direction"))
    label = {"whippan": "хлыст", "colorfill": "заливка"}.get(kind, kind)
    if kind in ("whippan", "colorfill") and direction:
        axis, sign = AXIS_OF[direction]
        return {"axis": axis, "sign": sign, "what": f"{label} {NAMES[(axis, sign)]}", "kind": kind}
    if kind in LEFT_XFADE_KINDS:
        return {"axis": "h", "sign": -1, "what": f"{kind} влево", "kind": kind}
    angle = join_param(transition, index, "angle")
    if kind == "slash" and angle is not None:
        a = math.radians(_num(angle))
        x, y = math.cos(a), math.sin(a)
        if max(abs(x), abs(y)) >= 0.8:
            axis, d = ("h", x) if abs(x) >= abs(y) else ("v", y)
            sign = 1 if d > 0 else -1
            return {"axis": axis, "sign": sign, "what": f"срез {NAMES[(axis, sign)]}", "kind": kind}
    return None


def _near(events: list[dict], a: float, b: float) -> list[dict]:
    return [e for e in events if e["t1"] > a and e["t0"] < b]


def _against(events: list[dict], axis: str, sign: int) -> dict | None:
    return next((e for e in events if e["axis"] == axis and e["sign"] == -sign), None)


def direction_findings(rs: dict, clock: Any) -> list[dict]:
    """Смена направления на склейках без причины и разнобой направлений переходов по ролику."""
    from app.transitions import join_override, normalize_transition

    transition = normalize_transition(rs.get("transition"))
    starts = list(getattr(clock, "clip_starts", []) or [])[1:]
    events = camera_events(rs.get("camera")) + layer_events(rs.get("layers"))
    findings: list[dict] = []
    joins: list[tuple[int, float, dict]] = []
    for index, t in enumerate(starts):
        own = join_override(transition, index)
        if own is not None and own.get("reason"):
            continue
        where = f"стык {index + 1}→{index + 2}"
        motion = join_motion(transition, index)
        before = _near(events, t - WINDOW_SEC, t)
        after = _near(events, t, t + WINDOW_SEC)
        if motion:
            joins.append((index, t, motion))
            clash = _against(before, motion["axis"], motion["sign"]) or _against(after, motion["axis"], motion["sign"])
            if clash:
                other = NAMES[(clash["axis"], clash["sign"])]
                findings.append(_finding(
                    "direction", "warn",
                    f"{motion['what']}, а {clash['what']} — направление ломается на склейке",
                    f"переход в ту же сторону: direction {KEYS_OF[(clash['axis'], clash['sign'])]} ({other}) — "
                    f"или поверни движение под переход; смена нарочно — reason у стыка "
                    f"— set_transition(join: {index}, direction: {KEYS_OF[(clash['axis'], clash['sign'])]})",
                    t=t, priority="P1", where=where, join=index))
                continue
        for e in before:
            clash = _against(after, e["axis"], e["sign"])
            if clash:
                findings.append(_finding(
                    "direction", "info",
                    f"до склейки {e['what']}, после — {clash['what']}",
                    f"одно основное направление на ролик: поверни одно из движений в ту же сторону "
                    f"({NAMES[(e['axis'], e['sign'])]}) или разведи их дальше чем на {WINDOW_SEC:g} с от склейки; "
                    f"нарочно — reason у стыка — set_transition(join: {index}, reason: …)",
                    t=t, priority="P2", where=where, join=index))
                break
    findings += _main_direction(joins, {f.get("join") for f in findings})
    return findings[:MAX_FINDINGS]


def _main_direction(joins: list[tuple[int, float, dict]], flagged: set) -> list[dict]:
    """Хлысты и заливки ролика в разные стороны по одной оси — P2 на тех, что против большинства."""
    out = []
    for axis in ("h", "v"):
        items = [(i, t, m) for i, t, m in joins if m["axis"] == axis and m["kind"] in ("whippan", "colorfill")]
        plus = [x for x in items if x[2]["sign"] > 0]
        minus = [x for x in items if x[2]["sign"] < 0]
        if not plus or not minus:
            continue
        main, odd = (plus, minus) if len(plus) >= len(minus) else (minus, plus)
        sign = main[0][2]["sign"]
        for index, t, motion in odd:
            if index in flagged:
                continue
            out.append(_finding(
                "direction", "info",
                f"{motion['what']} против основного направления ролика — {NAMES[(axis, sign)]} "
                f"({len(main)} из {len(items)})",
                f"одно основное направление на ролик: direction {KEYS_OF[(axis, sign)]}; нарочно — reason у стыка "
                f"— set_transition(join: {index}, direction: {KEYS_OF[(axis, sign)]})",
                t=t, priority="P2", where=f"стык {index + 1}→{index + 2}", join=index))
    return out
