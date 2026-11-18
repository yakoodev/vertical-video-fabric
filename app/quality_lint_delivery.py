"""Линт перед сдачей: кадр 0, мёртвые куски, «встало и сразу ушло», концовка (из ``app/quality_lint.py``).

* ``frame0`` — кадр 0 = обложка и крючок (владелец выкладывает с телефона, кадр 0 — обложка). По плану: на
  холсте без видео основы на кадре 0 только фон (P0) или только логотип (P1); титр-крючок первых секунд
  ещё не виден — его вход начинается с нуля (P1). По готовому рендеру: первый кадр чёрный / однотонный (P0),
  почти пустой — однотонный фон и маленький объект (P1).
* ``dead`` — «мёртвый кусок»: больше 3 с без видимой перемены. По плану — монтажные события (склейки, вход и
  выход слоёв, ключи, камера, вставки, видео-слои); холст без событий 3 с — P1. У видео основы план не знает,
  движется ли картинка: окно без событий сверяется с готовым рендером (кадр почти не меняется — P1), рендера
  нет — подсказка P2.
* ``hold`` — после того как последний элемент сцены встал, кадр держится 1–1.5 с до склейки; меньше —
  «встало и сразу ушло» (P1).
* ``outro`` — концовка (end_screen, слой ``end_banner``, слои-концовки до конца клипа) не длиннее 1.4 с и
  не больше 25 % ролика.

Время находок — шкала clip (как слои), ``final_t`` допишет общий прогон.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

from app.quality_lint_base import _finding, _num, _short

W, H = 1080.0, 1920.0  # опорный кадр 9:16; другой формат — frame_size() (promo_lint.frame_of)


def frame_size() -> tuple[float, float]:
    """Опорный кадр проверки (ширина, высота), px: 9:16 — 1080×1920, внутри ``promo_lint.frame_of`` — свой."""
    from app.promo_lint import frame

    return frame()

DEAD_SEC = 3.0              # без перемены дольше — «мёртвый кусок»
DEAD_FPS = 5                # кадров в секунду для сверки с рендером
DEAD_SIZE = 40              # сетка кадра для сверки
DEAD_MAD = 5.0              # средняя разница с первым кадром окна (0..255) ниже — картинка стоит
DEAD_MOVING_SHARE = 0.08    # доля клеток, изменившихся больше чем на DEAD_CELL, — уже движение
DEAD_CELL = 14.0
HOLD_MIN = 1.0              # держать кадр после последнего входа, с
HOLD_GOOD = 1.5
OUTRO_MAX_SEC = 1.5  # end_screen не короче 1.5 с — правило не должно срабатывать на любую концовку
OUTRO_MAX_SHARE = 0.25
FRAME0_BLACK_MEAN = 18.0
FRAME0_FLAT_STD = 6.0
FRAME0_BUSY_SHARE = 0.08    # доля «непохожих на фон» клеток меньше — почти пустой кадр

NOT_VISUAL = ("audio", "transition", "captions")
LOGO_RE = re.compile(r"logo|лого|brand|бренд|watermark|вотермарк", re.I)
OUTRO_RE = re.compile(r"outro|end_?card|endcard|end_banner|cta|subscribe|концовк|финал|подпиш|аутро", re.I)
TEXTY = ("text", "callout", "counter", "ui")


# ---------------------------------------------------------------- общее


def anim_span(layer: dict, key: str, default: float) -> float:
    """Длина входа/выхода как в движке (animate.ts animDuration): 0.05..3 с и не больше половины слоя."""
    anim = layer.get(key) if isinstance(layer.get(key), dict) else None
    if not anim or anim.get("type") in (None, "", "none"):
        return 0.0
    dur = max(0.1, _num(layer.get("end")) - _num(layer.get("start")))
    return min(min(3.0, max(0.05, _num(anim.get("duration"), default))), dur / 2)


def in_span(layer: dict) -> float:
    return anim_span(layer, "in", 0.35)


def out_span(layer: dict) -> float:
    return anim_span(layer, "out", 0.25)


def key_span(layer: dict) -> tuple[float, float] | None:
    """Где ключи слоя что-то меняют: (от, до) в секундах клипа; None — ключей с переменой нет."""
    keys = layer.get("keys") if isinstance(layer.get("keys"), dict) else {}
    start = _num(layer.get("start"))
    lo, hi = math.inf, -math.inf
    for points in keys.values():
        if not isinstance(points, list) or len(points) < 2:
            continue
        pts = sorted((p for p in points if isinstance(p, dict)), key=lambda p: _num(p.get("t")))
        values = [_num(p.get("v")) for p in pts]
        if max(values) - min(values) < 1e-3:
            continue
        lo, hi = min(lo, _num(pts[0].get("t"))), max(hi, _num(pts[-1].get("t")))
    return (start + lo, start + hi) if hi > lo else None


def settle_time(layer: dict) -> float:
    """Когда слой «встал»: конец входа и последнего ключа (в секундах клипа)."""
    t = _num(layer.get("start")) + in_span(layer)
    span = key_span(layer)
    if span:
        t = max(t, min(span[1], _num(layer.get("end"))))
    return t


def visual_layers(rs: dict) -> list[dict]:
    return [l for l in rs.get("layers") or [] if isinstance(l, dict) and not l.get("hidden")
            and l.get("type") not in NOT_VISUAL]


def layer_label(layer: dict) -> str:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    text = props.get("text") or props.get("title") or layer.get("name")
    lid = str(layer.get("id") or "")
    return f"{lid} «{_short(text, 24)}»" if text else lid


def layer_size(layer: dict) -> tuple[float, float] | None:
    """Размер слоя без scale, опорные px 1080×1920 (оценка, как у зон и промо-линта)."""
    from app.promo_lint import picture_size, text_size

    kind = layer.get("type")
    if kind == "text":
        return text_size(layer)
    if kind in ("image", "video"):
        return picture_size(layer)
    from app.aspect import layer_size as aspect_size

    try:
        return aspect_size(layer)
    except Exception:  # noqa: BLE001 - неизвестный тип — без размера
        return None


def layer_rect(layer: dict, t: float, camera: Any = None) -> tuple[float, float, float, float] | None:
    """Прямоугольник слоя в кадре 1080×1920 (px) в момент ``t``: ключи x/y/scale, камера у followCamera."""
    from app.promo_lint import _through_camera, layer_state

    size = layer_size(layer)
    if not size:
        return None
    st = layer_state(layer, t)
    w, h = size[0] * st["scale"], size[1] * st["scale"]
    fw, fh = frame_size()
    cx, cy = st["x"] * fw, st["y"] * fh
    rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    if layer.get("followCamera") and camera:
        rect = _through_camera(rect, camera, t)
    return rect


def is_full_frame(layer: dict, t: float | None = None) -> bool:
    if layer.get("type") in ("scene", "page"):
        return True
    rect = layer_rect(layer, _num(layer.get("start")) if t is None else t)
    fw, fh = frame_size()
    return bool(rect) and (rect[2] - rect[0]) >= 0.9 * fw and (rect[3] - rect[1]) >= 0.9 * fh


def is_logo(layer: dict) -> bool:
    if layer.get("type") in ("logo_reveal", "nameplate"):
        return True
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    hay = " ".join(str(v or "") for v in (layer.get("id"), layer.get("name"), props.get("src"), props.get("name")))
    return bool(LOGO_RE.search(hay))


def shown_at(layer: dict, t: float) -> bool:
    """Слой виден в момент ``t``: в своём отрезке, вход прошёл больше половины, не прозрачный."""
    from app.promo_lint import layer_state

    start, end = _num(layer.get("start")), _num(layer.get("end"))
    if not (start - 1e-6 <= t < end):
        return False
    span = in_span(layer)
    if span > 0 and t - start < span * 0.5:
        return False
    st = layer_state(layer, t)
    return st["opacity"] > 0.1 and st["scale"] > 0.05


def is_canvas_plan(store: Any, plan: dict) -> bool:
    from app.canvas import is_canvas

    try:
        return is_canvas(store.get_source(int(plan["source_id"])))
    except (KeyError, TypeError, ValueError):
        return False


def joins_clip(clock: Any) -> list[float]:
    """Склейки готового ролика (стыки кусков и вырезов) — секунды клипа."""
    try:
        starts = list(clock.post.final_starts)[1:]
    except AttributeError:
        return []
    return [clock.final_to_clip(float(s)) for s in starts]


# ---------------------------------------------------------------- кадр 0


def frame0_plan_findings(store: Any, plan: dict, rs: dict, clock: Any) -> list[dict]:
    t0 = clock.final_to_clip(0.0) if clock is not None else 0.0
    layers = visual_layers(rs)
    canvas = is_canvas_plan(store, plan)
    shown = [l for l in layers if shown_at(l, t0) and l.get("type") not in ("fx", "particles", "physics")]
    content = [l for l in shown if not is_logo(l) and not (canvas and is_full_frame(l, t0) and l.get("type") == "image")]
    out: list[dict] = []
    late = sorted((l for l in layers if l.get("type") in TEXTY and _num(l.get("start")) < t0 + 1.0
                   and not shown_at(l, t0)), key=lambda l: _num(l.get("start")))
    hook = late[0] if late else None
    hook_note = ""
    if hook is not None:
        when = max(_num(hook.get("start")) + in_span(hook) * 0.5 - t0, 0.0)
        hook_note = f"; крючок {layer_label(hook)} виден только с {when:.1f} с"
    if canvas and not content:
        if any(is_logo(l) for l in shown):
            out.append(_finding("frame0", "warn", "на кадре 0 только логотип — обложка без смысла" + hook_note,
                                "с 0.0 с поставь крупный титр-крючок или главный кадр без входа (in none); логотип — позже",
                                t=t0, priority="P1", where="кадр 0"))
        else:
            out.append(_finding("frame0", "warn", "кадр 0 пустой: только фон" + hook_note,
                                "с 0.0 с — крупный титр-крючок или главная картинка без входа (in: none): кадр 0 — обложка",
                                t=t0, priority="P0", where="кадр 0"))
        return out
    if hook is not None:
        anim = (hook.get("in") or {}).get("type") if isinstance(hook.get("in"), dict) else None
        why = f"вход {anim} начинается с нуля" if anim and _num(hook.get("start")) <= t0 + 0.05 else \
            f"появляется на {_num(hook.get('start')) - t0:.1f} с"
        out.append(_finding("frame0", "warn", f"крючок {layer_label(hook)} на кадре 0 ещё не виден ({why}) — на обложке его нет",
                            "у крючка in: none и start 0 (вход замени движением камеры) — update_layer",
                            t=t0, layer=str(hook.get("id") or ""), priority="P1", where="кадр 0"))
    return out


def first_frame_stats(path: Path) -> dict | None:
    """Первый кадр рендера сеткой 36×64: средняя яркость, разброс, доля клеток не цвета фона."""
    import subprocess

    import numpy as np

    proc = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-frames:v", "1", "-an", "-vf",
                           "scale=36:64:flags=area,format=rgb24", "-f", "rawvideo", "-"],
                          capture_output=True, timeout=60, check=False)
    if proc.returncode != 0 or len(proc.stdout) < 36 * 64 * 3:
        return None
    rgb = np.frombuffer(proc.stdout[: 36 * 64 * 3], dtype=np.uint8).reshape(64, 36, 3).astype(np.float32)
    lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    bg = np.median(rgb.reshape(-1, 3), axis=0)
    busy = float((np.abs(rgb - bg).max(axis=2) > 24).mean())
    return {"mean": float(lum.mean()), "std": float(lum.std()), "busy": busy}


def frame0_render_findings(stats: dict | None, t0: float = 0.0) -> list[dict]:
    if not stats:
        return []
    if stats["mean"] < FRAME0_BLACK_MEAN and stats["std"] < FRAME0_FLAT_STD * 1.5:
        return [_finding("frame0", "warn", "кадр 0 готового ролика чёрный — обложка и первый взгляд пустые",
                         "начни с кадра, где уже есть действие (вырез начала / сдвинь кусок), титр-крючок — без входа",
                         t=t0, priority="P0", where="кадр 0 (рендер)")]
    if stats["std"] < FRAME0_FLAT_STD:
        return [_finding("frame0", "warn", "кадр 0 готового ролика однотонный — пустая обложка",
                         "с 0.0 с — главный кадр или крупный титр-крючок без входа (in: none)",
                         t=t0, priority="P0", where="кадр 0 (рендер)")]
    if stats["busy"] < FRAME0_BUSY_SHARE:
        return [_finding("frame0", "warn", f"кадр 0 почти пустой: ровный фон и мелкий объект ({stats['busy'] * 100:.0f} % кадра) — "
                                           "похоже на один логотип",
                         "крупнее: титр-крючок или главный объект ≥ 1/3 высоты кадра на 0.0 с",
                         t=t0, priority="P1", where="кадр 0 (рендер)")]
    return []


# ---------------------------------------------------------------- мёртвые куски


def event_spans(rs: dict, clock: Any, duration: float) -> list[tuple[float, float]]:
    """Где в клипе что-то меняется (секунды клипа): входы/выходы, ключи, камера, вставки, видео-слои, склейки."""
    spans: list[tuple[float, float]] = []
    tick = 0.05
    for layer in visual_layers(rs):
        kind = layer.get("type")
        start, end = _num(layer.get("start")), min(_num(layer.get("end")), duration)
        if end <= start:
            continue
        if kind in ("video", "scene"):
            spans.append((start, end))  # играет само
            continue
        spans.append((start - tick, start + max(in_span(layer), tick)))
        if end < duration - 0.05:
            spans.append((end - max(out_span(layer), tick), end + tick))
        ks = key_span(layer)
        if ks:
            spans.append(ks)
        if kind in ("counter", "progress", "chart", "social", "ui", "odometer"):
            spans.append((start, start + min((end - start) * 0.6, 2.5)))
        elif kind == "logo_reveal":
            spans.append((start, start + min(end - start, 2.5)))
        elif kind == "text":
            hl = (layer.get("props") or {}).get("highlight")
            if isinstance(hl, dict) and _num(hl.get("draw")) > 0:
                a = start + (_num(hl.get("delay")) if hl.get("delay") is not None else in_span(layer))
                spans.append((a, a + _num(hl.get("draw"))))
    cam = sorted((k for k in rs.get("camera") or [] if isinstance(k, dict)), key=lambda k: _num(k.get("t")))
    for a, b in zip(cam, cam[1:]):
        if any(abs(_num(a.get(f), d) - _num(b.get(f), d)) > 1e-3 for f, d in (("zoom", 1.0), ("x", 0.5), ("y", 0.5),
                                                                               ("z", 0.0), ("rotX", 0.0), ("rotY", 0.0))):
            spans.append((_num(a.get("t")), _num(b.get("t"))))
    for ins in rs.get("inserts") or []:
        if isinstance(ins, dict) and ins.get("mode") != "sound":
            at = _num(ins.get("at"))
            spans.append((at, at + max(0.3, _num(ins.get("duration"), 1.0))))
    for t in joins_clip(clock):
        spans.append((t - tick, t + tick))
    return spans


def plan_gaps(spans: list[tuple[float, float]], clock: Any, final_duration: float, min_sec: float = DEAD_SEC
              ) -> list[tuple[float, float]]:
    """Окна готового ролика (секунды final) без событий дольше ``min_sec``."""
    final = sorted((max(0.0, clock.clip_to_final(max(0.0, a))), max(0.0, clock.clip_to_final(max(0.0, b))))
                   for a, b in spans if b > a)
    gaps: list[tuple[float, float]] = []
    cursor = 0.0
    for a, b in final:
        if a - cursor >= min_sec:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if final_duration - cursor >= min_sec:
        gaps.append((cursor, final_duration))
    return gaps


def static_windows(path: Path, gaps: list[tuple[float, float]], mask: Any = None) -> list[dict]:
    """Сверка окон без событий с рендером: в каждом окне — насколько кадр отходит от первого кадра окна.

    ``mask`` (bool ``S×S``, True — не считать: субтитры) — общий на ролик. Ответ: [{start, end, static,
    mad, moving}], секунды файла."""
    import numpy as np

    from app.quality_lint_freeze import gray_cells
    from app.quality_lint_media import read_frames

    if not gaps:
        return []
    frames: dict[int, Any] = {}
    index = 0
    wanted = [(int(a * DEAD_FPS), int(math.ceil(b * DEAD_FPS))) for a, b in gaps]
    for chunk in read_frames(Path(path), DEAD_FPS, DEAD_SIZE):
        gray = gray_cells(chunk)
        for frame in gray:
            if any(lo <= index <= hi for lo, hi in wanted):
                frames[index] = frame
            index += 1
    keep = None if mask is None else ~np.asarray(mask, dtype=bool)
    out = []
    for (a, b), (lo, hi) in zip(gaps, wanted):
        seq = [frames[i] for i in range(lo, hi + 1) if i in frames]
        if len(seq) < 3:
            continue
        base = seq[0]
        mad = moving = 0.0
        for f in seq[1:]:
            diff = np.abs(f - base)
            if keep is not None and keep.any():
                diff = diff[keep]
            mad = max(mad, float(diff.mean()))
            moving = max(moving, float((diff > DEAD_CELL).mean()))
        out.append({"start": a, "end": b, "mad": round(mad, 2), "moving": round(moving, 3),
                    "static": mad < DEAD_MAD and moving < DEAD_MOVING_SHARE})
    return out


def dead_findings(store: Any, plan: dict, rs: dict, clock: Any, path: Path | None, *, caption_mask: Any = None,
                  out: dict | None = None) -> list[dict]:
    duration = clock.clip_duration
    final = clock.final_duration
    gaps = plan_gaps(event_spans(rs, clock, duration), clock, final)
    canvas = is_canvas_plan(store, plan)
    info: dict[str, Any] = {"plan_gaps": len(gaps), "min_sec": DEAD_SEC}
    items: list[dict] = []
    fix = ("каждые 2–3 с что-то новое: склейка, наезд камеры (camera_preset), вставка, титр или смена позы; "
           "или вырежи паузу")
    if canvas:
        for a, b in gaps:
            items.append(_finding("dead", "warn", f"мёртвый кусок {b - a:.1f} с: на холсте ничего не меняется",
                                  fix, t=clock.final_to_clip(a), until=clock.final_to_clip(b), priority="P1",
                                  where="сцена"))
    elif gaps:
        checked = None
        freezes = rs.get("freezes") or []
        if path is not None and not freezes:
            try:
                checked = static_windows(path, gaps, caption_mask)
            except Exception as exc:  # noqa: BLE001 - не прочитали — по плану
                info["render_error"] = str(exc)[:200]
        if checked is None:
            for a, b in gaps:
                items.append(_finding("dead", "info", f"{b - a:.1f} с без монтажных событий (ни склейки, ни титра, "
                                                      "ни движения камеры) — проверь, не стоит ли картинка",
                                      fix + " — точнее покажет lint_clip после рендера", t=clock.final_to_clip(a),
                                      until=clock.final_to_clip(b), priority="P2", where="монтаж"))
        else:
            info["by_render"] = True
            for win in checked:
                if not win["static"]:
                    continue
                items.append(_finding("dead", "warn", f"мёртвый кусок {win['end'] - win['start']:.1f} с: картинка почти "
                                                      "не меняется и монтажных событий нет",
                                      fix, t=clock.final_to_clip(win["start"]), until=clock.final_to_clip(win["end"]),
                                      priority="P1", where="кадр", mad=win["mad"]))
    if out is not None:
        out["dead"] = {**info, "found": len(items)}
    return items


# ---------------------------------------------------------------- держать кадр


def scene_bounds(rs: dict, clock: Any, duration: float) -> list[float]:
    marks = {0.0, round(duration, 3)}
    marks.update(round(t, 3) for t in joins_clip(clock) if 0 < t < duration)
    for layer in visual_layers(rs):
        if layer.get("type") in ("image", "video", "scene", "page") and is_full_frame(layer):
            start = _num(layer.get("start"))
            if 0.05 < start < duration - 0.05:
                marks.add(round(start, 3))
    return sorted(marks)


def hold_findings(rs: dict, clock: Any, end_hold: float = 0.0) -> list[dict]:
    duration = clock.clip_duration
    bounds = scene_bounds(rs, clock, duration)
    layers = [l for l in visual_layers(rs) if l.get("type") not in ("fx", "particles", "physics", "audio") and not is_full_frame(l)]
    out: list[dict] = []
    for k, (s, e) in enumerate(zip(bounds, bounds[1:])):
        if e - s < 0.5:
            continue
        leaving = [l for l in layers if s - 1e-6 <= _num(l.get("start")) < e and _num(l.get("end")) <= e + 0.05]
        if not leaving:
            continue
        last = max(leaving, key=settle_time)
        settled = settle_time(last)
        gone = min(_num(l.get("end")) - out_span(l) for l in leaving if _num(l.get("end")) >= settled - 1e-6) \
            if any(_num(l.get("end")) >= settled - 1e-6 for l in leaving) else e
        gone = min(gone, e)
        hold = gone - settled + (end_hold if e >= duration - 0.05 else 0.0)
        if hold >= HOLD_MIN:
            continue
        where = f"сцена {k + 1}" if len(bounds) > 2 else "клип"
        if hold < 0.05:
            msg = f"{layer_label(last)} не успел встать — склейка/уход на {gone:.1f} с посреди входа"
        else:
            msg = f"встало и сразу ушло: {layer_label(last)} встал на {settled:.1f} с, кадр держится {hold:.1f} с"
        out.append(_finding("hold", "warn", msg,
                            f"держи кадр {HOLD_MIN:g}–{HOLD_GOOD:g} с после последнего входа: вход раньше (start/in "
                            "короче) или склейку/конец слоя позже", t=settled, until=gone,
                            layer=str(last.get("id") or ""), priority="P1", where=where, hold=round(hold, 2)))
    return out


# ---------------------------------------------------------------- концовка


def outro_findings(rs: dict, clock: Any, end_hold: float = 0.0) -> list[dict]:
    from app.end_screen import END_LAYER_ID, normalize_end_screen

    final = clock.final_duration
    duration = clock.clip_duration
    if final <= 0:
        return []
    starts: list[tuple[float, str]] = []
    es = normalize_end_screen(rs.get("end_screen"))
    if es:
        starts.append((max(0.0, final - es["duration"]), "концовка-баннер"))
    for layer in visual_layers(rs):
        hay = " ".join(str(v or "") for v in (layer.get("id"), layer.get("name")))
        if (layer.get("id") == END_LAYER_ID or OUTRO_RE.search(hay)) and _num(layer.get("end")) >= duration - 0.05:
            starts.append((clock.clip_to_final(_num(layer.get("start"))), f"слой {layer.get('id')}"))
    if not starts:
        return []
    at, what = min(starts)
    card = final - at + max(0.0, end_hold)
    total = final + max(0.0, end_hold)
    if card <= OUTRO_MAX_SEC and card <= OUTRO_MAX_SHARE * total:
        return []
    share = card / max(total, 1e-6)
    return [_finding("outro", "warn", f"концовка {card:.1f} с ({share * 100:.0f} % ролика; {what}"
                                      f"{', со стоп-кадром ' + format(end_hold, '.1f') + ' с' if end_hold > 0 else ''}) — "
                                      "зритель уходит до конца, луп рвётся",
                     f"концовка ≤ {OUTRO_MAX_SEC:g} с и ≤ {OUTRO_MAX_SHARE * 100:.0f} % ролика: короче баннер "
                     "(set_end_screen duration) / слой концовки, set_end_hold(0)",
                     t=clock.final_to_clip(at), until=duration, priority="P1", where="концовка",
                     seconds=round(card, 2))]
