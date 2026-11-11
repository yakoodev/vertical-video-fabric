"""Блоки разобранной страницы (app/webpage_layers.py) → моушн-слои клипа: фон страницы и каждый блок отдельным слоем.

Позиция блока переводится из CSS px страницы в опорный кадр клипа (1080×1920 у вертикали):
``as_page`` — страница во всю ширину кадра, как на экране телефона (область по высоте в середине, длинная — от
верха); ``fit_width`` — выбранные блоки вписываются в кадр по ширине с полями (и по высоте, если не влезают).

Вход по умолчанию — лесенкой: сначала фон, затем блоки сверху вниз через ``stagger`` (0.1 с) — кнопки, цены и
значки «выпрыгивают» (pop), остальное выезжает (slideUp), шапка — сверху. Ребёнок карточки входит после неё.

Картинка блока — «Файлы для монтажа» (как у любой картинки слоя: ``props.asset_id``), файл проекта клипа;
повторная вставка того же блока берёт уже заведённый файл. Простой текст можно поставить живым текстом
(``as: text``): шрифт из библиотеки моушна или файл шрифта страницы (регистрируется своим шрифтом —
app/user_fonts.py). Нет шрифта или кегль вне пределов — остаётся картинкой.

Групп слоёв в движке нет: слои одной вставки связаны общим началом id (``wp<page>_<метка>_<n>``) и стоят подряд
на одной дорожке в порядке рисования страницы.
"""

from __future__ import annotations

import secrets
import shutil
from typing import Any
from uuid import uuid4

from app import motion
from app import webpage_layers as L
from app import webpages as W

LAYOUTS = ("as_page", "fit_width")
DEFAULT_STAGGER = 0.1
DEFAULT_DURATION = 5.0
FIT_MARGIN = 0.06
POP_KINDS = ("button", "price", "badge")


class ClipLayersError(ValueError):
    """Слои не собрались — понятный текст для владельца и модели."""


# --------------------------------------------------------------------------- #
# Выбор блоков и раскладка
# --------------------------------------------------------------------------- #


def pick(dec: dict, blocks: Any = None) -> list[dict]:
    """Какие блоки ставить: None/[] — все; список id («b3»), номеров или видов («card», «price»)."""
    every = dec["blocks"]
    if not blocks:
        return list(every)
    wanted = {str(x).strip().lower() for x in (blocks if isinstance(blocks, (list, tuple)) else [blocks])}
    out = [b for b in every if b["id"] in wanted or str(b["i"]) in wanted or b["kind"] in wanted]
    if not out:
        raise ClipLayersError(f"нет таких блоков: {', '.join(sorted(wanted))[:200]} — id из разбора (b0, b1…) или виды "
                              f"({', '.join(sorted(L.KIND_LABEL))})")
    return out


def mapping(dec: dict, chosen: list[dict], layout: str, ref: tuple[int, int], plate: bool) -> tuple[float, float, float]:
    """(s, ox, oy): кадр_x = ox + x_страницы·s, кадр_y = oy + y_страницы·s (опорные px)."""
    rw, rh = ref
    region = dec["region"]
    if layout == "as_page":
        s = rw / float(dec.get("page_width") or region["w"])
        top, height = float(region["y"]), float(region["h"])
        if not plate and chosen:
            top = min(b["box"][1] for b in chosen)
            height = max(b["box"][1] + b["box"][3] for b in chosen) - top
        oy = (rh - height * s) / 2 - top * s if height * s <= rh else -top * s
        return s, 0.0, oy
    if plate or not chosen:
        x0, y0, x1, y1 = region["x"], region["y"], region["x"] + region["w"], region["y"] + region["h"]
    else:
        x0 = min(b["box"][0] for b in chosen)
        y0 = min(b["box"][1] for b in chosen)
        x1 = max(b["box"][0] + b["box"][2] for b in chosen)
        y1 = max(b["box"][1] + b["box"][3] for b in chosen)
    w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
    s = rw * (1 - 2 * FIT_MARGIN) / w
    if h * s > rh * (1 - FIT_MARGIN):
        s = rh * (1 - FIT_MARGIN) / h
    return s, (rw - w * s) / 2 - x0 * s, (rh - h * s) / 2 - y0 * s


def _entrance(kind: str) -> dict:
    if kind in POP_KINDS:
        return {"type": "pop", "duration": 0.35}
    if kind == "header":
        return {"type": "slideDown", "duration": 0.4}
    if kind == "section":
        return {"type": "fade", "duration": 0.35}
    return {"type": "slideUp", "duration": 0.4}


def _hex(rgba: list) -> str:
    r, g, b = (max(0, min(255, int(round(float(v))))) for v in rgba[:3])
    a = float(rgba[3]) if len(rgba) > 3 else 1.0
    return f"#{r:02x}{g:02x}{b:02x}" + ("" if a >= 0.995 else f"{max(0, min(255, int(round(a * 255)))):02x}")


# --------------------------------------------------------------------------- #
# Файлы и шрифты
# --------------------------------------------------------------------------- #


def _asset(store: Any, page_id: int, dec: dict, file: str, label: str, project_id: int | None,
           known: dict[str, dict]) -> dict:
    """PNG разбора → «Файлы для монтажа» (копия в каталог библиотеки); тот же блок второй раз — тот же файл."""
    import cv2

    from app.montage_assets import assets_dir

    original = f"webpage:{page_id}:{dec['version']}:{dec['key']}:{file}"
    hit = known.get(original)
    if hit:
        return hit
    src = L.block_file(page_id, dec["version"], dec["key"], file)
    dest = assets_dir() / f"{uuid4().hex}-page{page_id}-{file}"
    shutil.copyfile(src, dest)
    img = cv2.imread(str(dest), cv2.IMREAD_UNCHANGED)
    h, w = (img.shape[:2] if img is not None else (0, 0))
    asset = store.create_montage_asset(
        kind="image", label=label[:120], description=f"Блок страницы {page_id} (версия {dec['version']}): {label}"[:500],
        tags="страница сайта,слой", file_path=str(dest), original_filename=original, mime_type="image/png",
        size_bytes=dest.stat().st_size, width=w, height=h, project_id=project_id,
    )
    known[original] = asset
    return asset


def _known_assets(store: Any, project_id: int | None) -> dict[str, dict]:
    from pathlib import Path

    out = {}
    try:
        rows = store.list_montage_assets(project_id=project_id) if project_id is not None else store.list_montage_assets(visible_to=())
    except Exception:  # noqa: BLE001 - без списка просто заведём новые
        return out
    for a in rows:
        name = str(a.get("original_filename") or "")
        if name.startswith("webpage:") and Path(str(a.get("file_path") or "")).is_file():
            if project_id is None or a.get("project_id") in (None, project_id):
                out[name] = a
    return out


def _font_for(page_id: int, font: dict, notes: list[str]) -> str | None:
    """Семейство для живого текста: из библиотеки — сразу; файл страницы — зарегистрировать своим шрифтом."""
    if not font.get("ok"):
        return None
    if font["source"] == "library":
        return font["family"]
    from app.motion_assets import list_fonts, upload_font

    family = font["family"]
    if any(str(f.get("family") or "").lower() == family.lower() for f in list_fonts()):
        return family
    try:
        path = W.asset_file(page_id, font["file"])
        upload_font(path.read_bytes(), f"{family}{path.suffix.lower()}", family=family,
                    weight=font.get("face_weight") or font.get("weight"), style="italic" if font.get("italic") else "normal")
    except (KeyError, OSError, ValueError) as exc:
        notes.append(f"шрифт «{family}» не подключился ({exc}) — текст оставлен картинкой")
        return None
    notes.append(f"шрифт страницы «{family}» добавлен в свои шрифты")
    return family


def _text_layer(blk: dict, family: str, s: float, ox: float, oy: float, ref: tuple[int, int]) -> dict | None:
    st = blk["text_style"]
    size = float(st["size"]) * s
    if not 12 <= size <= 400:
        return None
    content = st.get("content") or st["box"]
    lines = st["box"]
    cx = ox + (content[0] + content[2] / 2) * s
    cy = oy + (lines[1] + lines[3] / 2) * s
    width = float(content[2]) * s
    props = {
        "text": str(st["text"])[: motion.MAX_TEXT], "font": family, "weight": int(st.get("weight") or 400),
        "size": round(size, 1), "color": _hex(st.get("color") or [255, 255, 255, 1]), "align": st.get("align") or "left",
        "maxWidth": round(min(2160.0, max(40.0, width * 1.03)), 1), "boxWidth": round(min(2160.0, max(40.0, width)), 1),
        "lineHeight": round(min(2.0, max(0.8, float(st.get("line_height") or size / s * 1.2) / float(st["size"]))), 3),
        "letterSpacing": round(min(100.0, max(-20.0, float(st.get("letter_spacing") or 0) * s)), 1),
        "italic": bool(st.get("italic")), "stroke": None, "bg": None,
    }
    return {"type": "text", "x": round(cx / ref[0], 4), "y": round(cy / ref[1], 4), "props": props}


# --------------------------------------------------------------------------- #
# Слои
# --------------------------------------------------------------------------- #


def build_layers(store: Any, page_id: int, dec: dict, *, project_id: int | None = None, blocks: Any = None,
                 as_text: Any = None, plate: bool = True, at_sec: float = 0.0, duration: float = DEFAULT_DURATION,
                 layout: str = "fit_width", stagger: float | None = None, track: int = 1,
                 ref: tuple[int, int] = (1080, 1920), focus: Any = None) -> dict:
    """Слои вставки: {layers, blocks: [{id, layer_id, name, kind, as}], notes, scale}.

    ``as_text`` — какие блоки живым текстом: True — все, где можно; список id; dict {id: "text"|"image"};
    None — все картинками (по умолчанию: картинка точь-в-точь как на странице).
    ``focus`` — наезд на блок вместе с его детьми (карточка товара): id блока или {block, at, zoom}; см. focus_keys."""
    layout = layout if layout in LAYOUTS else "fit_width"
    stagger = DEFAULT_STAGGER if stagger is None else max(0.0, min(1.0, float(stagger)))
    duration = max(0.5, min(120.0, float(duration or DEFAULT_DURATION)))
    at_sec = max(0.0, float(at_sec or 0))
    chosen = pick(dec, blocks)
    s, ox, oy = mapping(dec, chosen, layout, ref, plate)
    faces = dec.get("font_faces") or []
    if as_text is True:
        text_ids = {b["id"] for b in chosen}
    elif isinstance(as_text, dict):
        text_ids = {str(k) for k, v in as_text.items() if str(v) == "text"}
    elif isinstance(as_text, (list, tuple, set)):
        text_ids = {str(x) for x in as_text}
    else:
        text_ids = set()
    from app.motion_assets import load_fonts

    library = load_fonts()
    known = _known_assets(store, project_id)
    tag = secrets.token_hex(2)
    end = round(at_sec + duration, 3)
    notes: list[str] = []
    layers: list[dict] = []
    report: list[dict] = []
    if plate:
        asset = _asset(store, page_id, dec, dec["plate"], "Фон страницы", project_id, known)
        r = dec["region"]
        layers.append({
            "id": f"wp{page_id}_{tag}_bg", "type": "image", "name": "Фон страницы", "track": track,
            "start": round(at_sec, 3), "end": end,
            "x": round((ox + (r["x"] + r["w"] / 2) * s) / ref[0], 4), "y": round((oy + (r["y"] + r["h"] / 2) * s) / ref[1], 4),
            "props": {"asset_id": str(asset["id"]), "w": round(min(4000.0, r["w"] * s), 1)},
            "in": {"type": "fade", "duration": 0.3}, "out": {"type": "fade", "duration": 0.25},
        })
        report.append({"id": "plate", "layer_id": layers[-1]["id"], "name": "Фон страницы", "kind": "plate", "as": "image"})
    # Время входа: сверху вниз, ребёнок — не раньше родителя + шаг.
    first = at_sec + (0.25 if plate else 0.0)
    timing = sorted(chosen, key=lambda b: (round(b["box"][1] / 4), b["box"][0]))
    starts: dict[str, float] = {}
    for k, blk in enumerate(timing):
        starts[blk["id"]] = first + k * stagger
    by_i = {b["i"]: b for b in chosen}
    picked = {b["id"] for b in chosen}
    order = [b for b in L.paint_order(dec["blocks"]) if b["id"] in picked]
    for blk in order:
        p = by_i.get(blk["parent"])
        if p is not None:
            starts[blk["id"]] = max(starts[blk["id"]], starts[p["id"]] + max(stagger, 0.05))
    for blk in order:
        start = round(min(starts[blk["id"]], end - 0.2), 3)
        lid = f"wp{page_id}_{tag}_{blk['i']}"
        layer = None
        kind_as = "image"
        if blk["id"] in text_ids and blk.get("text_style"):
            font = L.text_font(page_id, blk, faces, library)
            family = _font_for(page_id, font, notes) if font.get("ok") else None
            if family:
                layer = _text_layer(blk, family, s, ox, oy, ref)
                kind_as = "text" if layer else "image"
            elif blk["id"] in text_ids and isinstance(as_text, (dict, list, tuple, set)):
                notes.append(f"{blk['name']}: {font.get('reason') or 'шрифт не подключить'} — картинкой")
        if layer is None:
            asset = _asset(store, page_id, dec, blk["file"], blk["name"], project_id, known)
            bx, by, bw, bh = blk["box"]
            layer = {"type": "image", "x": round((ox + (bx + bw / 2) * s) / ref[0], 4),
                     "y": round((oy + (by + bh / 2) * s) / ref[1], 4),
                     "props": {"asset_id": str(asset["id"]), "w": round(max(10.0, min(4000.0, bw * s)), 1)}}
        layer.update({"id": lid, "name": blk["name"][: motion.MAX_NAME], "track": track, "start": start, "end": end,
                      "in": _entrance(blk["kind"]), "out": {"type": "fade", "duration": 0.25}})
        layers.append(layer)
        report.append({"id": blk["id"], "layer_id": lid, "name": blk["name"], "kind": blk["kind"], "as": kind_as,
                       "parent": f"b{blk['parent']}" if blk["parent"] >= 0 else None})
    out = {"layers": layers, "blocks": report, "notes": notes, "scale": round(s, 4), "layout": layout}
    if focus not in (None, "", False):
        out["focus"] = focus_keys(layers, report, chosen, focus, ref)
    return out


def focus_keys(layers: list[dict], report: list[dict], chosen: list[dict], focus: Any,
               ref: tuple[int, int] = (1080, 1920)) -> dict:
    """Наезд на блок с детьми: блок и всё внутри него вместе едут в середину кадра и растут в ``zoom`` раз
    (ключи x, y, scale по времени слоя) дорожкой выше, остальные слои страницы притухают до 0.25. Групп нет — одни
    и те же ключи у каждого слоя блока, точка опоры — центр блока. Вернуть {block, at, zoom, layers}."""
    spec = focus if isinstance(focus, dict) else {"block": focus}
    target = str(spec.get("block") or "").strip().lower()
    by_id = {b["id"]: b for b in chosen}
    blk = by_id.get(target)
    if blk is None:
        raise ClipLayersError(f"focus: блока {target or '?'} нет среди поставленных — id из blocks")
    family = {blk["id"]}
    grew = True
    while grew:
        grew = False
        for b in chosen:
            if b["id"] not in family and f"b{b['parent']}" in family:
                family.add(b["id"])
                grew = True
    rows = {r["layer_id"]: r for r in report}
    head = next(x for x in layers if rows[x["id"]]["id"] == blk["id"])
    w, h = ref
    cx, cy = float(head["x"]) * w, float(head["y"]) * h
    bw = float(head["props"].get("w") or 400)
    bh = bw * blk["box"][3] / max(1.0, blk["box"][2])
    zoom = float(spec.get("zoom") or min(w * 0.86 / bw, h * 0.78 / bh))
    zoom = max(1.05, min(4.0, zoom))
    entered = max(float(x["start"]) + float((x.get("in") or {}).get("duration") or 0.3) for x in layers)
    at = float(spec.get("at") or entered + 0.5)
    move = max(0.2, min(2.0, float(spec.get("duration") or 0.6)))
    tx, ty = w / 2, h * 0.5
    touched = []
    for layer in layers:
        start = float(layer["start"])
        t0, t1 = round(max(0.0, at - start), 3), round(max(0.0, at - start) + move, 3)
        if t1 >= float(layer["end"]) - start:
            continue
        if rows[layer["id"]]["id"] in family:
            px, py = float(layer["x"]) * w, float(layer["y"]) * h
            nx, ny = tx + (px - cx) * zoom, ty + (py - cy) * zoom
            base = float(layer.get("scale") or 1)
            layer["keys"] = {
                "x": [{"t": t0, "v": round(px / w, 4), "ease": "inOutCubic"}, {"t": t1, "v": round(nx / w, 4)}],
                "y": [{"t": t0, "v": round(py / h, 4), "ease": "inOutCubic"}, {"t": t1, "v": round(ny / h, 4)}],
                "scale": [{"t": t0, "v": base, "ease": "inOutCubic"}, {"t": t1, "v": round(base * zoom, 4)}],
            }
            # Дорожкой выше остальных слоёв страницы: выросший блок рисуется поверх соседей, а не под ними.
            layer["track"] = min(motion.MAX_TRACK, int(layer.get("track") or 0) + 1)
            touched.append(layer["id"])
        else:
            layer["keys"] = {"opacity": [{"t": t0, "v": 1, "ease": "inOutCubic"}, {"t": t1, "v": 0.25}]}
    return {"block": blk["id"], "name": blk["name"], "at": round(at, 3), "zoom": round(zoom, 3), "layers": touched}


def insert_into_clip(store: Any, clip_id: int, layers: list[dict]) -> list[dict]:
    """Дописать слои в клип (нормализованные, со свободными id). Вернуть добавленные."""
    from app.clip_settings import normalize_render_settings

    plan = store.get_clip_plan(int(clip_id), include_segments=False)
    render = normalize_render_settings(plan.get("render_settings") or {})
    current = [dict(x) for x in render.get("layers") or []]
    if len(current) + len(layers) > motion.MAX_LAYERS:
        room = max(0, motion.MAX_LAYERS - len(current))
        raise ClipLayersError(f"в клипе уже {len(current)} слоёв, предел {motion.MAX_LAYERS} — влезет ещё {room}; "
                              "выбери меньше блоков")
    taken = {str(x.get("id")) for x in current}
    for layer in layers:
        if str(layer.get("id")) in taken:
            layer["id"] = motion.new_layer_id(taken)
        taken.add(str(layer["id"]))
    added = motion.normalize_layers(layers)
    store.set_clip_plan_render_settings(int(clip_id), {**render, "layers": current + added})
    return added


def clip_ref(store: Any, clip_id: int | None) -> tuple[int, int]:
    """Опорный кадр слоёв клипа (1080×1920 у вертикали, 1920×1080 у 16:9)."""
    if clip_id is None:
        return (1080, 1920)
    from app.clip_settings import normalize_render_settings
    from app.formats import ref_size, settings_layers_format

    plan = store.get_clip_plan(int(clip_id), include_segments=False)
    w, h = ref_size(settings_layers_format(normalize_render_settings(plan.get("render_settings") or {})))
    return int(w), int(h)


def page_to_layers(store: Any, page_id: int, version: int | None = None, clip_id: int | None = None,
                   region: Any = None, at_sec: float = 0.0, layout: str = "fit_width", stagger: float | None = None,
                   blocks: Any = None, as_text: Any = None, plate: bool = True, duration: float = DEFAULT_DURATION,
                   max_layers: int = L.DEFAULT_MAX_LAYERS, insert: bool = True, runner: W.Runner | None = None,
                   focus: Any = None) -> dict:
    """Разобрать страницу (или взять готовый разбор) и собрать слои; ``insert`` и клип — дописать в клип.

    Вернуть {layers, blocks, notes, decomposition (view), inserted?}. Файлы картинок — проекта клипа."""
    dec = L.decompose(store, page_id, version, region, max_layers, runner=runner)
    if not dec["blocks"] and not plate:
        raise ClipLayersError("на странице не нашлось блоков — возьми другую область (region)")
    project_id = None
    if clip_id is not None:
        from app.asset_scope import clip_project

        project_id = clip_project(store, int(clip_id))
    built = build_layers(store, page_id, dec, project_id=project_id, blocks=blocks, as_text=as_text, plate=plate,
                         at_sec=at_sec, duration=duration, layout=layout, stagger=stagger, ref=clip_ref(store, clip_id),
                         focus=focus)
    out = {**built, "decomposition": L.view(dec)}
    if insert and clip_id is not None:
        out["inserted"] = insert_into_clip(store, int(clip_id), built["layers"])
    return out
