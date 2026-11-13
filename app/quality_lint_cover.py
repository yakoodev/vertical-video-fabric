"""Линт: что зритель увидит, а глазами агент не поймал (повтор эксперимента 13.11, Gemini в чате).

* ``subtitle_cover_findings`` (проверка ``reading``) — субтитры под непрозрачным слоем. RC1: телефон со страницей
  сайта (слой ``page``) лежал выше субтитров, на кадрах «ХО…ТЬ», «Н…К GIRL»; lint и QC молчали, агент пять раз
  смотрел view_frame и не поправил. Порядок слоёв — как у компоновщика (frontend/src/motion/render.ts): выше тот, у
  кого больше ``track``, при равном — кто позже в списке. Прожигаемые субтитры (ASS) лежат под всеми слоями.
* ``stream_overlay_findings`` (проверка ``montage``) — оверлей стрима (донат-бар, сборы, алерты) в игровой половине
  split. RC2: полоса «332 / 500 · 250 / 20 000 RUB» весь ролик внизу игры. Источник рамок — окна интерфейса, которые
  нашёл ``detect_avatar`` (кэш, без вызова модели), иначе — неподвижная полоса поперёк окна игры по кадрам исходника
  (пиксели не меняются от кадра к кадру — оверлей, а не игра). Совет — наезд камеры в окне игры или game_x.
* ``subtitle_coverage`` (проверка ``sub_sync``) — субтитров мало для речи: доля секунд речи (VAD), на которых есть
  слова субтитров. RC2: в половине кадров нет текста. Голоса игры, убранные ``separate_voices``, речью не считаются.
"""

from __future__ import annotations

import math
from typing import Any

from app.quality_lint_base import _finding, _num, _short

W, H = 1080.0, 1920.0

# --------------------------------------------------------------------------- субтитры под слоем

OCCLUDER_TYPES = ("page", "video", "image", "ui", "social", "callout", "carousel", "screens", "scene", "shape", "chart",
                  "pngtuber")
MIN_OCCLUDER_OPACITY = 0.6
COVER_SHARE = 0.2  # закрыта такая доля рамки страницы субтитров — читать уже нельзя


def _track(layer: dict) -> float:
    return _num(layer.get("track"), 0.0)


def occluder_rect(layer: dict, t: float, camera: Any = None) -> tuple | None:
    """Рамка непрозрачного слоя в кадре 1080×1920 в момент ``t``; не перекрывает (звук, наклейка, прозрачный) — None."""
    from app.aspect import layer_size
    from app.promo_lint import _through_camera, layer_state
    from app.quality_lint_zones import is_sticker

    kind = layer.get("type")
    if kind not in OCCLUDER_TYPES or layer.get("hidden"):
        return None
    if kind == "image" and is_sticker(layer):
        return None  # наклейка/мем поверх — чаще PNG с прозрачностью
    start, end = _num(layer.get("start")), _num(layer.get("end"), math.inf)
    if not start <= t < end:
        return None
    st = layer_state(layer, t)
    if st["opacity"] < MIN_OCCLUDER_OPACITY or st["scale"] <= 0:
        return None
    size = layer_size(layer)
    if not size:
        return None
    w, h = size[0] * st["scale"], size[1] * st["scale"]
    cx, cy = st["x"] * W, st["y"] * H
    rect = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    if layer.get("followCamera") and camera:
        rect = _through_camera(rect, camera, t)
    return rect


def _overlap_share(a: tuple, b: tuple) -> float:
    """Доля площади ``a``, закрытая ``b``."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    area = max(1.0, (a[2] - a[0]) * (a[3] - a[1]))
    return ix * iy / area


def _caption_boxes(layer: dict, pages: list[dict]) -> list[tuple[float, float, str, tuple]]:
    from app.promo_lint import layer_state

    out = []
    for page in pages:
        st = layer_state(layer, page["t0"])
        w, h = page["width"] * st["scale"], page["height"] * st["scale"]
        cx, cy = st["x"] * W, st["y"] * H
        out.append((page["t0"], page["t1"], page.get("text") or "", (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)))
    return out


def _ass_boxes(ass_block: dict) -> list[tuple[float, float, str, tuple]]:
    bottom_pct = _num(ass_block.get("bottom_pct"), 12.0)
    align = int(_num(ass_block.get("align"), 2))
    out = []
    for page in ass_block.get("pages") or []:
        w, h = page["width"], page["height"]
        if align in (7, 8, 9):
            y0 = H * bottom_pct / 100
            rect = (W / 2 - w / 2, y0, W / 2 + w / 2, y0 + h)
        elif align in (4, 5, 6):
            rect = (W / 2 - w / 2, H / 2 - h / 2, W / 2 + w / 2, H / 2 + h / 2)
        else:
            bottom = H * (1 - bottom_pct / 100)
            rect = (W / 2 - w / 2, bottom - h, W / 2 + w / 2, bottom)
        out.append((page["t0"], page["t1"], page.get("text") or "", rect))
    return out


def _what_layer(layer: dict) -> str:
    kind = str(layer.get("type") or "")
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    if kind == "page":
        return "телефон со страницей" if props.get("device") == "phone" else "страница сайта"
    return {"video": "видео", "image": "картинка", "ui": "карточка интерфейса", "social": "карточка соцсети",
            "callout": "плашка", "carousel": "карусель", "screens": "экраны", "scene": "3D-сцена", "shape": "фигура",
            "chart": "график", "pngtuber": "аватар"}.get(kind, kind)


def subtitle_cover_findings(layers: list[dict], caption_pages: dict[str, list[dict]] | None,
                            ass_block: dict | None = None, camera: Any = None) -> list[dict]:
    """Страницы субтитров, которые закрывает непрозрачный слой выше них (по порядку компоновщика)."""
    out: list[dict] = []
    indexed = list(enumerate(layers))
    targets: list[tuple[str, list, Any]] = []
    for index, layer in indexed:
        if layer.get("type") == "captions" and not layer.get("hidden"):
            lid = str(layer.get("id") or "captions")
            boxes = _caption_boxes(layer, (caption_pages or {}).get(lid) or [])
            above = [lay for i, lay in indexed if lay is not layer and
                     (_track(lay) > _track(layer) or (_track(lay) == _track(layer) and i > index))]
            targets.append((lid, boxes, above))
    if ass_block and ass_block.get("pages"):
        # Прожигаемые субтитры — в основе кадра: любой слой поверх.
        targets.append(("", _ass_boxes(ass_block), [lay for _i, lay in indexed]))
    for lid, boxes, above in targets:
        hits: dict[str, dict] = {}
        for t0, t1, text, rect in boxes:
            mid = (t0 + t1) / 2
            for occ in above:
                orect = occluder_rect(occ, mid, camera)
                if orect is None:
                    continue
                share = _overlap_share(rect, orect)
                if share < COVER_SHARE:
                    continue
                key = str(occ.get("id") or occ.get("type"))
                hit = hits.setdefault(key, {"layer": occ, "pages": 0, "t": t0, "until": t1, "text": text,
                                            "share": share, "rect": orect})
                hit["pages"] += 1
                hit["until"] = max(hit["until"], t1)
                if share > hit["share"]:
                    hit.update(share=share, text=text, t=t0, rect=orect)
        for key, hit in hits.items():
            what = "прожигаемые субтитры" if not lid else "субтитры"
            occ = hit["layer"]
            orect = hit["rect"]
            free_above = orect[1] / H
            free_below = 1 - orect[3] / H
            move = (f"y≈{max(0.06, orect[1] / H - 0.06):.2f} (над слоем)" if free_above >= free_below
                    else f"y≈{min(0.94, orect[3] / H + 0.06):.2f} (под слоем)")
            fix = (f"подними субтитры над слоем: update_layer {lid or 'captions'} track {int(_track(occ)) + 1}; или "
                   f"сдвинь их на {move}, или слой «{key}» в сторону" if lid else
                   f"сдвинь прожигаемые субтитры (set_subtitles position_pct) или слой «{key}» — сейчас он поверх них")
            out.append(_finding(
                "reading", "warn",
                f"{what} закрыты слоем «{key}» ({_what_layer(occ)}) на {hit['pages']} стр.: до {round(hit['share'] * 100)}% "
                f"строки «{_short(hit['text'], 30)}» под ним — зритель видит обрывки",
                fix, t=hit["t"], until=hit["until"], layer=lid or None, covered_by=key))
    return out


# --------------------------------------------------------------------------- оверлей стрима в игре

OVERLAY_MIN_SHARE = 0.25  # такая доля рамки оверлея в окне игры — видно
BAND_STATIC_STD = 6.0  # яркость пикселя меняется меньше — неподвижен
BAND_MIN_ROW_SHARE = 0.45  # неподвижно столько ширины окна игры — полоса поперёк, а не HUD в углу
BAND_EDGE = 0.22  # полосу ищем в верхней и нижней доле окна игры
BAND_TEXT_EDGE = 4.0  # средний перепад яркости по строке полосы: выше — надписи и цифры
SAMPLE_FRAMES = 8
SAMPLE_W = 320
_STATIC_CACHE: dict[tuple, list[dict]] = {}


def game_window(plan: dict, rs: dict, source: dict, out_w: float = W, out_h: float = H) -> dict | None:
    """Что из исходника видно в окне игры split (доли кадра исходника): {x0, x1, y0, y1} по всем кускам (фокус
    game_x на кусках — объединение), без наездов камеры. Не split — None."""
    from app.stream_layout import normalize_layout, split_geometry

    layout = normalize_layout(rs.get("layout"))
    if layout["mode"] != "split":
        return None
    sw, sh = _num(source.get("width"), 1920), _num(source.get("height"), 1080)
    crop = source.get("content_crop") if isinstance(source.get("content_crop"), dict) else None
    c = {"x": _num((crop or {}).get("x"), 0), "y": _num((crop or {}).get("y"), 0),
         "w": _num((crop or {}).get("w"), 1), "h": _num((crop or {}).get("h"), 1)}
    _cam_h, _gap, game_h = split_geometry(layout, int(out_h), int(out_w))
    panel = out_w / max(1.0, game_h)
    src = (c["w"] * sw) / max(1.0, c["h"] * sh)
    if src >= panel:  # шире окна: видна полоса по ширине, высота целиком
        wfrac = c["w"] * panel / src
        xs = []
        for seg in plan.get("segments") or [{}]:
            focus = [f for f in (seg.get("focus") or []) if isinstance(f, dict)]
            xs += [_num(f.get("x"), 0.5) for f in focus] or [c["x"] + c["w"] / 2]
        lo = min(min(max(x - wfrac / 2, c["x"]), c["x"] + c["w"] - wfrac) for x in xs)
        hi = max(max(min(x + wfrac / 2, c["x"] + c["w"]), c["x"] + wfrac) for x in xs)
        return {"x0": lo, "x1": hi, "y0": c["y"], "y1": c["y"] + c["h"], "panel_h_px": game_h}
    hfrac = c["h"] * src / panel
    y0 = c["y"] + (c["h"] - hfrac) / 2
    return {"x0": c["x"], "x1": c["x"] + c["w"], "y0": y0, "y1": y0 + hfrac, "panel_h_px": game_h}


def _cached_ui_boxes(store: Any, plan_id: int) -> list[dict]:
    """Окна интерфейса стрима (донаты, чат, подписи) из кэша detect_avatar — без вызова модели."""
    try:
        from app.ai.avatar_box import cached_avatar

        data = cached_avatar(store, int(plan_id)) or {}
    except Exception:  # noqa: BLE001
        return []
    out = []
    for frame in data.get("per_frame") or []:
        for box in (frame or {}).get("ui") or [] if isinstance(frame, dict) else []:
            if isinstance(box, dict) and all(k in box for k in ("x0", "y0", "x1", "y1")):
                out.append({k: _num(box[k]) for k in ("x0", "y0", "x1", "y1")} | {"from": "detect_avatar"})
    return out


def _frames(path: str, times: list[float], width: int = SAMPLE_W) -> Any:
    """Серые кадры исходника (numpy N×h×w) в моменты ``times``."""
    import subprocess

    import numpy as np

    frames = []
    shape = None
    for t in times:
        proc = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", path, "-frames:v", "1", "-vf",
                               f"scale={width}:-2,format=gray", "-f", "rawvideo", "-"], capture_output=True, timeout=60,
                              check=False, stdin=subprocess.DEVNULL)
        if proc.returncode != 0 or not proc.stdout:
            continue
        data = np.frombuffer(proc.stdout, dtype=np.uint8)
        if shape is None:
            rows = data.size // width
            shape = (rows, width)
        if data.size != shape[0] * shape[1]:
            continue
        frames.append(data.reshape(shape).astype(np.float32))
    return np.stack(frames) if len(frames) >= 4 else None


def static_bands(frames: Any, window: dict, exclude: list[dict] | None = None) -> list[dict]:
    """Неподвижные полосы поперёк окна игры у верхнего и нижнего края: строки, где не меняется больше
    ``BAND_MIN_ROW_SHARE`` ширины окна (донат-бар, сборы, бегущая строка). Доли кадра исходника."""
    import numpy as np

    _n, h, w = frames.shape
    std = frames.std(axis=0)
    static = std < BAND_STATIC_STD
    for box in exclude or []:  # вебка/аватар — не оверлей
        static[int(box["y0"] * h):int(math.ceil(box["y1"] * h)), int(box["x0"] * w):int(math.ceil(box["x1"] * w))] = False
    x0, x1 = int(window["x0"] * w), max(int(window["x0"] * w) + 1, int(math.ceil(window["x1"] * w)))
    y0, y1 = int(window["y0"] * h), max(int(window["y0"] * h) + 1, int(math.ceil(window["y1"] * h)))
    band_h = max(1, int((y1 - y0) * BAND_EDGE))
    share = static[:, x0:x1].mean(axis=1)
    # Кадр целиком почти неподвижен (пауза, меню) — о полосах не судим.
    if float(static[y0:y1, x0:x1].mean()) > 0.6:
        return []
    out = []
    for lo, hi in ((y0, y0 + band_h), (y1 - band_h, y1)):
        rows = [r for r in range(lo, hi) if share[r] >= BAND_MIN_ROW_SHARE]
        if len(rows) < max(3, int(0.012 * h)):
            continue
        cols = np.where(static[rows[0]:rows[-1] + 1, x0:x1].mean(axis=0) >= 0.5)[0]
        if not cols.size:
            continue
        # Текст и цифры (донат-бар «332 / 500», сборы) — резкие перепады по строке; сплошная рамка — нет.
        mean = frames.mean(axis=0)[rows[0]:rows[-1] + 1, x0:x1]
        texty = float(np.abs(np.diff(mean, axis=1)).mean()) > BAND_TEXT_EDGE
        out.append({"x0": (x0 + int(cols[0])) / w, "x1": (x0 + int(cols[-1]) + 1) / w, "y0": rows[0] / h,
                    "y1": (rows[-1] + 1) / h, "share": round(float(share[rows].mean()), 2),
                    "from": "неподвижная полоса с надписями" if texty else "неподвижная сплошная полоса (рамка оверлея)"})
    return out


def _source_static_bands(store: Any, plan: dict, source: dict, window: dict, cam: dict | None) -> list[dict]:
    from app.ai.avatar_box import source_times

    path = str(source.get("local_path") or "")
    times = source_times(plan, SAMPLE_FRAMES)
    key = (int(source.get("id") or 0), tuple(round(t, 1) for t in times), tuple(round(v, 3) for v in window.values()
                                                                               if isinstance(v, float)))
    if key in _STATIC_CACHE:
        return _STATIC_CACHE[key]
    from pathlib import Path

    if not path or not Path(path).is_file() or len(times) < 4:
        return []
    frames = _frames(path, times)
    found = static_bands(frames, window, [cam] if cam else None) if frames is not None else []
    if len(_STATIC_CACHE) > 64:
        _STATIC_CACHE.clear()
    _STATIC_CACHE[key] = found
    return found


def _inside_share(box: dict, win: dict) -> float:
    ix = max(0.0, min(box["x1"], win["x1"]) - max(box["x0"], win["x0"]))
    iy = max(0.0, min(box["y1"], win["y1"]) - max(box["y0"], win["y0"]))
    area = max(1e-6, (box["x1"] - box["x0"]) * (box["y1"] - box["y0"]))
    return ix * iy / area


def overlay_fix(box: dict, win: dict) -> str:
    """Совет: наезд камеры в окне игры (центр по высоте) срезает край, где полоса; сбоку — сдвиг game_x."""
    height = max(1e-6, win["y1"] - win["y0"])
    top = (box["y0"] - win["y0"]) / height
    bottom = (box["y1"] - win["y0"]) / height
    # Наезд z по центру показывает середину окна высотой 1/z: полоса уходит, когда 0.5 − 0.5/z ≥ её край.
    if bottom <= 0.5:  # полоса сверху окна
        need = 1 / max(1e-3, 1 - 2 * bottom)
    elif top >= 0.5:  # снизу
        need = 1 / max(1e-3, 2 * top - 1)
    else:
        need = math.inf
    parts = []
    if math.isfinite(need) and need <= 2.0:
        parts.append(f"обрежь край окна игры наездом: set_camera zoom ≥ {math.ceil(need * 100) / 100:.2f} на весь клип "
                     "(в split камера двигает только игру)")
    width = box["x1"] - box["x0"]
    if width < (win["x1"] - win["x0"]) * 0.5:
        parts.append("или сдвинь окно игры game_x (set_layout) так, чтобы оверлей ушёл за край")
    if not parts:
        parts.append("оверлей посреди окна игры: выбери другой момент или перекрой его своим слоем")
    return "; ".join(parts) + ". Плейбук stream-shorts: донат-бары и алерты в игре не оставляем"


def stream_overlay_findings(store: Any, plan: dict, rs: dict, media: bool = True) -> list[dict]:
    """Донат-бар, сборы и алерты стрима в окне игры split: рамки detect_avatar (кэш) или неподвижные полосы."""
    try:
        source = store.get_source(int(plan["source_id"]))
    except Exception:  # noqa: BLE001
        return []
    if str(source.get("source_type") or "") in ("canvas", "clip_upload"):
        return []
    win = game_window(plan, rs, source)
    if win is None:
        return []
    from app.stream_layout import normalize_layout

    cam = normalize_layout(rs.get("layout")).get("cam")
    boxes = [b for b in _cached_ui_boxes(store, int(plan["id"]))
             if not (cam and _inside_share(b, {"x0": cam["x"], "x1": cam["x"] + cam["w"], "y0": cam["y"],
                                                  "y1": cam["y"] + cam["h"]}) > 0.5)]
    if not boxes and media:
        try:
            cam_box = {"x0": cam["x"], "x1": cam["x"] + cam["w"], "y0": cam["y"], "y1": cam["y"] + cam["h"]} if cam else None
            boxes = _source_static_bands(store, plan, source, win, cam_box)
        except Exception:  # noqa: BLE001 - нет ffmpeg/numpy — проверка молчит
            boxes = []
    seen: list[dict] = []
    out = []
    for box in boxes:
        if _inside_share(box, win) < OVERLAY_MIN_SHARE:
            continue
        if any(_inside_share(box, s) > 0.6 for s in seen):
            continue  # тот же оверлей с другого кадра
        seen.append(box)
        where = ("внизу" if (box["y0"] + box["y1"]) / 2 > (win["y0"] + win["y1"]) / 2 else "вверху")
        out.append(_finding(
            "montage", "warn",
            f"оверлей стрима в окне игры {where} ({box['from']}): исходник x {box['x0']:.2f}–{box['x1']:.2f}, "
            f"y {box['y0']:.2f}–{box['y1']:.2f} — донат-бар, сборы или алерт будут в кадре весь ролик",
            overlay_fix(box, win), box={k: round(box[k], 3) for k in ("x0", "y0", "x1", "y1")}))
        if len(out) >= 3:
            break
    return out


# --------------------------------------------------------------------------- покрытие речи субтитрами

COVERAGE_MIN = 0.6
COVERAGE_MIN_SPEECH_SEC = 6.0
WORD_PAD = (0.15, 0.35)  # слово видно чуть раньше и дольше своего звука


def _merge(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out: list[list[float]] = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def _subtract(spans: list[tuple[float, float]], holes: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for a, b in spans:
        parts = [(a, b)]
        for ha, hb in holes:
            nxt = []
            for pa, pb in parts:
                if hb <= pa or ha >= pb:
                    nxt.append((pa, pb))
                    continue
                if ha > pa:
                    nxt.append((pa, ha))
                if hb < pb:
                    nxt.append((hb, pb))
            parts = nxt
        out += parts
    return out


def _total(spans: list[tuple[float, float]]) -> float:
    return sum(b - a for a, b in spans)


def subtitle_coverage(speech: list[tuple[float, float]], words: list[dict], cuts: list[dict] | None = None,
                      ignore: list[tuple[float, float]] | None = None) -> dict:
    """Доля секунд речи, на которых есть слова субтитров: {speech_sec, covered_sec, ratio}. Вырезанное и
    ``ignore`` (голоса игры, убранные из субтитров) не считаются."""
    holes = [(_num(c.get("start")), _num(c.get("end"))) for c in cuts or [] if isinstance(c, dict)]
    holes += list(ignore or [])
    talk = _subtract(_merge([(float(a), float(b)) for a, b in speech]), _merge(holes))
    shown = _merge([(_num(w.get("start")) - WORD_PAD[0], _num(w.get("end")) + WORD_PAD[1])
                    for w in words if isinstance(w, dict) and str(w.get("word") or "").strip()])
    covered = _total(talk) - _total(_subtract(talk, shown))
    total = _total(talk)
    return {"speech_sec": round(total, 1), "covered_sec": round(covered, 1),
            "ratio": round(covered / total, 2) if total > 0 else None}


def _to_clip(clock: Any, spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    out = []
    for a, b in spans:
        try:
            out += [(float(p["start"]), float(p["end"])) for p in clock.plain_range_to_clip(a, b)]
        except Exception:  # noqa: BLE001 - вне кусков — не считаем
            continue
    return out


def coverage_findings(store: Any, plan: dict, rs: dict, clock: Any,
                      shown: list[tuple[float, float]] | None = None) -> tuple[list[dict], dict]:
    """Субтитров мало для речи клипа (VAD по звуку речи): доля секунд речи, на которых зритель видит страницу
    субтитров (``shown`` — страницы слоёв captions и прожигаемых, шкала clip; не даны — слова субтитров), ниже
    ``COVERAGE_MIN`` — предупреждение."""
    from app.speech_activity import plan_speech_spans

    payload = store.get_clip_plan_subtitles(int(plan["id"])) or {}
    shows = bool(rs.get("subs_on")) or any(layer.get("type") == "captions" and not layer.get("hidden")
                                           for layer in rs.get("layers") or [])
    if not shows:
        return [], {}
    spans = [(float(s[0]), float(s[1])) if isinstance(s, (list, tuple)) else (_num(s.get("start")), _num(s.get("end")))
             for s in plan_speech_spans(store, int(plan["id"])) or []]
    dropped = [(_num(w.get("start")) - 0.1, _num(w.get("end")) + 0.1) for w in payload.get("dropped_words") or []
               if isinstance(w, dict)]
    words = payload.get("words") or []
    if shown is not None:
        # Всё на шкале clip: речь и убранные голоса — из plain, вырезы — уже clip.
        info = subtitle_coverage(_to_clip(clock, spans), [{"word": "x", "start": a + WORD_PAD[0], "end": b - WORD_PAD[1]}
                                                           for a, b in shown],
                                 rs.get("cuts") or [], _to_clip(clock, dropped))
    else:
        info = subtitle_coverage(spans, words, rs.get("cuts") or [], dropped)
    if info["ratio"] is None or info["speech_sec"] < COVERAGE_MIN_SPEECH_SEC or info["ratio"] >= COVERAGE_MIN:
        return [], info
    return [_finding(
        "sub_sync", "warn",
        f"субтитров мало для речи: на экране они только на {round(info['ratio'] * 100)}% секунд речи "
        f"({info['covered_sec']} из {info['speech_sec']} с), слов в субтитрах {len(words)}",
        "распознай речь заново (generate_subtitles) и раздели голоса (separate_voices) — вернутся потерянные фразы; "
        "проверь, что слой субтитров идёт весь клип и не отфильтрован по голосам (props.speakers)", t=0.0)], info
