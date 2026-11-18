"""Линт перед сдачей: ремесло моушн-графики (из ``app/quality_lint.py``).

* ``subject`` — в графической сцене (холст без видео основы) главный объект не меньше 1/3 высоты кадра;
  геймплей и видео основа не проверяются, сцена из одного текста — типографика, тоже нет.
* ``slide_chrome`` — «слайдовая обвязка»: счётчики «02/05», полосы прогресса, подписи в углах, печатная
  машинка как украшение — ролик выглядит презентацией.
* ``zero_scale`` — «ничего не растёт из нуля»: вход pop/spin/words или ключ scale с нуля. Лучше въезд от
  края (slideUp, rise) или «оседание» из чуть меньшего/большего (ключи scale 0.94 → 1 или 1.06 → 1).
* ``entrances`` — в одном ролике один и тот же заметный вход слов не повторять (focus, colorize, slot,
  letters, scramble, glitch, rise).
"""

from __future__ import annotations

import re
from typing import Any

from app.quality_lint_base import _finding, _num, _short
from app.quality_lint_delivery import frame_size, is_full_frame, is_logo, layer_label, layer_rect, visual_layers

SUBJECT_MIN_SHARE = 1 / 3
SUBJECT_STEP = 0.5
SUBJECT_MIN_RUN = 1.0
ZERO_SCALE_INS = ("pop", "spin", "words")
NOTABLE_ENTRANCES = ("focus", "colorize", "slot", "letters", "scramble", "glitch", "rise")
ENTRANCE_NAMES = {"focus": "из размытия", "colorize": "из серого в цвет", "slot": "слот-машина",
                  "letters": "по буквам", "scramble": "расшифровка", "glitch": "глитч", "rise": "из-под маски"}
COUNTER_RE = re.compile(r"^\s*(?:(?:шаг|step|часть|part|слайд|slide|глава|№)\s*)?\d{1,2}\s*(?:/|из|of)\s*\d{1,2}\s*$", re.I)
CORNER_X, CORNER_Y = 0.22, 0.14
MAX_ZERO_FINDINGS = 8


def _props(layer: dict) -> dict:
    return layer.get("props") if isinstance(layer.get("props"), dict) else {}


def _in_type(layer: dict) -> str | None:
    anim = layer.get("in") if isinstance(layer.get("in"), dict) else None
    kind = anim.get("type") if anim else None
    return kind if kind not in (None, "", "none") else None


# ---------------------------------------------------------------- главный объект


def subject_findings(layers_all: list[dict], duration: float, canvas: bool, camera: Any = None) -> list[dict]:
    if not canvas or duration <= 0:
        return []
    layers = [l for l in layers_all if l.get("type") not in ("fx", "particles", "physics", "nameplate")]
    H = frame_size()[1]
    runs: list[dict] = []
    cur: dict | None = None
    t = 0.05
    while t < duration:
        shown = [l for l in layers if _num(l.get("start")) <= t < _num(l.get("end"))]
        objects = [l for l in shown if l.get("type") != "text" and not is_logo(l)]
        if any(is_full_frame(l, t) and l.get("type") in ("scene", "video", "page") for l in objects):
            objects = []  # 3D-сцена / видео во весь кадр: предмет внутри, отсюда не измерить
            shown = []
        objects = [l for l in objects if not is_full_frame(l, t)]
        best, best_h = None, 0.0
        for layer in objects:
            rect = layer_rect(layer, t, camera)
            if rect and rect[3] - rect[1] > best_h:
                best, best_h = layer, rect[3] - rect[1]
        small = bool(shown) and best is not None and best_h < SUBJECT_MIN_SHARE * H
        if small:
            if cur and cur["layer"] is best:
                cur["end"] = t + SUBJECT_STEP
                cur["h"] = max(cur["h"], best_h)
            else:
                cur = {"start": t, "end": t + SUBJECT_STEP, "layer": best, "h": best_h}
                runs.append(cur)
        else:
            cur = None
        t += SUBJECT_STEP
    out = []
    for run in runs:
        if run["end"] - run["start"] < SUBJECT_MIN_RUN:
            continue
        layer = run["layer"]
        share = run["h"] / H
        need = SUBJECT_MIN_SHARE / max(share, 1e-3) * _num(layer.get("scale"), 1.0)
        out.append(_finding("subject", "warn", f"главный объект мелкий: самый крупный {layer_label(layer)} — "
                                               f"{share * 100:.0f} % высоты кадра",
                            f"главный объект ≥ 1/3 высоты кадра: scale ≈ {need:.2f} (update_layer) или ближе камерой",
                            t=run["start"], until=min(run["end"], duration), layer=str(layer.get("id") or ""),
                            priority="P1", where="сцена"))
    return out


# ---------------------------------------------------------------- слайдовая обвязка


def chrome_findings(layers_all: list[dict], duration: float) -> list[dict]:
    W = frame_size()[0]
    out: list[dict] = []
    typewriters = [l for l in layers_all if l.get("type") == "text" and _in_type(l) == "typewriter"]
    for layer in layers_all:
        kind = layer.get("type")
        props = _props(layer)
        text = str(props.get("text") or "")
        start, end = _num(layer.get("start")), _num(layer.get("end"))
        lid = str(layer.get("id") or "")
        if kind == "text" and COUNTER_RE.match(text):
            out.append(_finding("slide_chrome", "warn", f"счётчик «{_short(text, 12)}» — слайдовая обвязка: ролик "
                                                        "выглядит презентацией",
                                "убери счётчик: порядок и так видно по монтажу", t=start, layer=lid,
                                priority="P1", where=layer_label(layer)))
            continue
        if kind == "progress" or (kind == "shape" and props.get("kind") in ("rect", None)
                                  and 0 < _num(props.get("h"), 999) <= 30 and _num(props.get("w")) >= 0.5 * W
                                  and (_num(layer.get("y"), 0.5) <= 0.1 or _num(layer.get("y"), 0.5) >= 0.9)):
            out.append(_finding("slide_chrome", "warn", "полоса прогресса — слайдовая обвязка (у площадки своя полоса)",
                                "убери полосу прогресса", t=start, layer=lid, priority="P1", where=layer_label(layer)))
            continue
        if kind == "text" and duration > 0:
            x, y = _num(layer.get("x"), 0.5), _num(layer.get("y"), 0.5)
            corner = (x <= CORNER_X or x >= 1 - CORNER_X) and (y <= CORNER_Y or y >= 1 - CORNER_Y)
            rect = layer_rect(layer, start)
            small = _num(props.get("size"), 96) <= 56 or (rect is not None and rect[3] - rect[1] <= 90)
            if corner and small and (end - start >= 0.4 * duration or end - start >= 4.0):
                out.append(_finding("slide_chrome", "warn", f"подпись в углу {layer_label(layer)} весь ролик — "
                                                            "слайдовая обвязка",
                                    "убери угловую подпись; нужное — крупно и по делу, в нужный момент", t=start,
                                    until=end, layer=lid, priority="P2", where="угол кадра"))
                continue
        if kind == "text" and _in_type(layer) == "letters" and len(text.replace(" ", "")) > 20:
            out.append(_finding("slide_chrome", "warn", f"«по буквам» на фразе {layer_label(layer)} — вход для имени, "
                                                        "фраза тянется",
                                "по буквам — только имя/ник (до ~16 букв); фразе — focus, rise или words", t=start,
                                layer=lid, priority="P2", where=layer_label(layer)))
    if typewriters:
        long = [l for l in typewriters if len(str(_props(l).get("text") or "")) > 30]
        if len(typewriters) >= 2 or long:
            first = (long or typewriters)[0]
            names = ", ".join(str(l.get("id")) for l in typewriters[:5])
            out.append(_finding("slide_chrome", "warn", f"печатная машинка как украшение ({len(typewriters)}: {names})",
                                "печать — только там, где на экране правда печатают (чат, поиск, код); остальным — "
                                "focus, rise или без входа", t=_num(first.get("start")), layer=str(first.get("id") or ""),
                                priority="P2", where="титры"))
    return out


# ---------------------------------------------------------------- рост из нуля


def zero_scale_findings(layers_all: list[dict]) -> list[dict]:
    out: list[dict] = []
    for layer in layers_all:
        kind = _in_type(layer)
        keys = layer.get("keys") if isinstance(layer.get("keys"), dict) else {}
        scale_keys = sorted((p for p in keys.get("scale") or [] if isinstance(p, dict)), key=lambda p: _num(p.get("t")))
        from_zero = bool(scale_keys) and _num(scale_keys[0].get("v"), 1.0) <= 0.05 and \
            max(_num(p.get("v")) for p in scale_keys) > 0.3
        why = f"вход {kind}" if kind in ZERO_SCALE_INS else "ключ scale с 0" if from_zero else None
        if why is None or layer.get("type") in ("captions", "fx", "particles", "physics"):
            continue
        out.append(_finding("zero_scale", "warn", f"{layer_label(layer)} вырастает из нуля ({why}) — дёшево выглядит",
                            "въезд от края (slideUp / rise) или «оседание»: fade + ключи scale 0.94 → 1 (или 1.06 → 1)",
                            t=_num(layer.get("start")), layer=str(layer.get("id") or ""), priority="P2",
                            where=layer_label(layer)))
    if len(out) > MAX_ZERO_FINDINGS:
        rest = len(out) - MAX_ZERO_FINDINGS
        out = out[:MAX_ZERO_FINDINGS]
        out[-1]["message"] += f" (и ещё {rest})"
    return out


# ---------------------------------------------------------------- повтор входов


def entrance_findings(layers_all: list[dict]) -> list[dict]:
    used: dict[str, list[dict]] = {}
    for layer in sorted(layers_all, key=lambda l: _num(l.get("start"))):
        kind = _in_type(layer)
        if layer.get("type") == "text" and kind in NOTABLE_ENTRANCES:
            used.setdefault(kind, []).append(layer)
    free = [k for k in ("focus", "colorize", "slot", "letters", "rise") if k not in used]
    out = []
    for kind, items in used.items():
        if len(items) < 2:
            continue
        where = ", ".join(f"{l.get('id')} ({_num(l.get('start')):.1f} с)" for l in items[:5])
        out.append(_finding("entrances", "warn", f"вход «{ENTRANCE_NAMES[kind]}» ({kind}) повторяется {len(items)} раза: "
                                                 f"{where} — в одном ролике один вход один раз",
                            f"у второго и дальше — другой вход: {', '.join(free) or 'fade, slideUp'} (update_layer in)",
                            t=_num(items[1].get("start")), layer=str(items[1].get("id") or ""), priority="P2",
                            where="титры"))
    return out
