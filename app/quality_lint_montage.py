"""Линт качества: монтаж — эффекты, вставки, карточки, обрывки, сверка субтитров (из ``app/quality_lint.py``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.quality_lint_base import _num, _short, _finding
from app.quality_lint_zones import W, H

MAX_FRAGMENT_FINDINGS = 12
MAX_SYNC_FINDINGS = 12


# =============================================================== 6) монтаж: эффекты и вставки


def _media_ref(layer: dict) -> Any:
    props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
    return props.get("asset_id") or props.get("src")


def montage_findings(rs: dict, duration: float | None = None) -> list[dict]:
    """Ремесло монтажа, которое QA 02.11 (клип 892) нашёл глазами: ч/б до конца клипа (цвет не вернулся) и
    одна и та же вставка дважды (гифка «LIVE MEAL CAM» на 6.7 и 13.5 с)."""
    from app.motion import fx_grayscale_long

    out: list[dict] = []
    layers = [l for l in rs.get("layers") or [] if isinstance(l, dict) and not l.get("hidden")]
    for layer in layers:
        found = fx_grayscale_long([layer], duration)
        if not found:
            continue
        message = found[0]
        to_end = "до конца клипа" in message
        out.append(_finding("montage", "warn" if to_end else "info", message,
                            "fx grayscale: end = слово панчлайна + 1.5–2 с (update_layer end), out fade 0.25",
                            t=_num(layer.get("start")), until=_num(layer.get("end"), duration or 0.0),
                            layer=str(layer.get("id") or "")))
    uses: dict[str, list[tuple[float, str]]] = {}
    for ins in rs.get("inserts") or []:
        if isinstance(ins, dict) and ins.get("mode") != "sound" and ins.get("asset_id") is not None:
            uses.setdefault(f"asset:{ins['asset_id']}", []).append((_num(ins.get("at")), f"вставка на {_num(ins.get('at')):g} с"))
    for layer in layers:
        ref = _media_ref(layer) if layer.get("type") in ("image", "video") else None
        if ref not in (None, ""):
            key = f"asset:{ref}" if str(ref).isdigit() else f"ref:{ref}"
            uses.setdefault(key, []).append((_num(layer.get("start")), f"слой {layer.get('id')} на {_num(layer.get('start')):g} с"))
    for key, items in uses.items():
        if len(items) < 2:
            continue
        items.sort()
        where = ", ".join(label for _t, label in items)
        out.append(_finding("montage", "warn", f"одна и та же вставка ({key.split(':', 1)[1]}) {'дважды' if len(items) == 2 else f'{len(items)} раз'}: {where} — "
                                               "повтор выглядит как нехватка материала",
                            "на второе место — другая картинка/гифка под смысл слов (search_images / add_image_from_url) "
                            "или убери повтор", t=items[1][0]))
    return out


def people_findings(store: Any, rs: dict) -> list[dict]:
    """Вставки/картинки, где проверка Gemini-зрением нашла реального человека (кэш ``people_check`` ассета,
    app/ai/people_check.py). QA 4 (клип 896): гифка с офисным работником и фото студента при заказе «без фото
    реальных людей» — lint молчал. Здесь не знаем заказа — замечание (info), запрет ставят add_insert и
    add_image_from_url по плейбуку/заказу."""
    out: list[dict] = []
    refs: list[tuple[Any, float, str]] = []
    for ins in rs.get("inserts") or []:
        if isinstance(ins, dict) and ins.get("mode") != "sound" and ins.get("asset_id") is not None:
            refs.append((ins["asset_id"], _num(ins.get("at")), f"вставка на {_num(ins.get('at')):g} с"))
    for layer in rs.get("layers") or []:
        if isinstance(layer, dict) and not layer.get("hidden") and layer.get("type") in ("image", "video"):
            ref = _media_ref(layer)
            if str(ref or "").isdigit():
                refs.append((ref, _num(layer.get("start")), f"слой {layer.get('id')} на {_num(layer.get('start')):g} с"))
    for ref, t, label in refs:
        try:
            check = store.get_montage_asset(int(ref)).get("people_check")
        except (KeyError, TypeError, ValueError):
            continue
        if isinstance(check, dict) and check.get("real_person") is True:
            what = str(check.get("what") or "").strip()
            out.append(_finding("montage", "info", f"{label}: на ней реальный человек" + (f" ({what})" if what else ""),
                                "заказ «без фото реальных людей» (vtuber-story, stream-shorts) — замени на рисованную/"
                                "мультяшную картинку или животных", t=t))
    return out


IMAGE_MIN_VISIBLE_SHARE = 0.35  # картинка-иллюстрация видна уже этой доли ширины кадра — не читается
IMAGE_FULL_SHARE = 0.9          # шире — полнокадровая вставка, не карточка
_VISIBLE_CACHE: dict[str, float] = {}


def visible_width_share(path: str | Path) -> float:
    """Доля ширины картинки без прозрачных полей по бокам (PNG/WebP с альфой); нет альфы/не прочитать — 1."""
    key = str(path)
    if key in _VISIBLE_CACHE:
        return _VISIBLE_CACHE[key]
    share = 1.0
    try:
        import cv2

        img = cv2.imread(key, cv2.IMREAD_UNCHANGED)
        if img is not None and img.ndim == 3 and img.shape[2] == 4:
            cols = (img[:, :, 3] > 16).any(axis=0).nonzero()[0]
            share = (int(cols[-1]) - int(cols[0]) + 1) / img.shape[1] if len(cols) else 0.0
    except Exception:  # noqa: BLE001 - не картинка/нет cv2 — считаем без полей
        share = 1.0
    _VISIBLE_CACHE[key] = share
    return share


def _hook_layers(layers: list[dict]) -> list[dict]:
    """Заголовок-хук: текст с ``hook`` в id или текст сверху кадра с первой секунды."""
    return [l for l in layers if l.get("type") == "text" and (
        "hook" in str(l.get("id") or "").lower() or (_num(l.get("start")) < 1.0 and _num(l.get("y"), 0.5) < 0.32))]


FACE_COVER_SHARE = 0.15      # картинка закрывает столько лица аватара — уже брак
FULL_INSERT_MAX_SEC = 3.0    # вставка во весь кадр поверх аватара — не дольше
CARD_MAX_SHARE = 0.4         # карточка рядом с лицом — не шире этой доли кадра
CARD_MARGIN = 40.0           # px от краёв кадра, лица, хука и субтитров


def _rect_overlap(a: tuple, b: tuple) -> float:
    ix, iy = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    return ix * iy if ix > 0 and iy > 0 else 0.0


def _captions_band(layers: list[dict], a: float, b: float) -> tuple | None:
    """Полоса субтитров-слоя (px) на отрезке a..b: y слоя ± полторы строки."""
    for layer in layers:
        if layer.get("type") != "captions" or layer.get("hidden"):
            continue
        if min(b, _num(layer.get("end"), b)) - max(a, _num(layer.get("start"))) <= 0:
            continue
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        half = 1.5 * _num(props.get("size"), 80.0)
        cy = _num(layer.get("y"), 0.72) * H
        return (0.0, cy - half, W, cy + half)
    return None


CAPTION_TEXT_SHARE = 0.6     # строка субтитров по 1–3 слова — средние 60 % ширины кадра
CAPTION_COVER_SHARE = 0.15   # картинка поверх субтитров закрывает столько их полосы — уже не читаются


def _layer_order(layers: list[dict], layer: dict) -> tuple[int, int]:
    """Порядок отрисовки слоя (как компоновщик: track, потом место в списке): больше — поверх."""
    index = next((i for i, l in enumerate(layers) if l is layer or (layer.get("id") and l.get("id") == layer.get("id"))),
                 len(layers))
    return int(_num(layer.get("track"), 0.0)), index


def _covered_captions(layers: list[dict], layer: dict, start: float, end: float, faces: list[dict] | None,
                      hooks: list[dict], duration: float | None) -> dict | None:
    """Картинка/видео ПОВЕРХ субтитров (QA 5, клип 897: стикер-таракан во весь кадр закрыл «какая-то лезет»):
    находка с подсказкой — субтитры выше картинкой (track) или место карточки мимо субтитров, лица и хука."""
    from app.promo_lint import layer_rect

    me = _layer_order(layers, layer)
    for caps in layers:
        if caps.get("type") != "captions" or caps.get("hidden") or _layer_order(layers, caps) > me:
            continue
        a, b = max(start, _num(caps.get("start"))), min(end, _num(caps.get("end"), duration or end))
        if b - a < 0.1:
            continue
        props = caps.get("props") if isinstance(caps.get("props"), dict) else {}
        half = 1.5 * _num(props.get("size"), 80.0)
        cy = _num(caps.get("y"), 0.72) * H
        band = ((1 - CAPTION_TEXT_SHARE) / 2 * W, cy - half, (1 + CAPTION_TEXT_SHARE) / 2 * W, cy + half)
        box = layer_rect(layer, (a + b) / 2)
        if not box:
            continue
        share = _rect_overlap(box, band) / max(1.0, (band[2] - band[0]) * (band[3] - band[1]))
        if share < CAPTION_COVER_SHARE:
            continue
        lid = str(layer.get("id") or layer.get("type"))
        what = "картинка" if layer.get("type") == "image" else "видео"
        above = int(_num(layer.get("track"), 0.0)) + 1
        fix = f"субтитры поверх неё: update_layer {caps.get('id')} patch {{\"track\": {above}}}"
        if box[2] - box[0] < IMAGE_FULL_SHARE * W:
            avoid = [band, *[tuple(z["rect"]) for z in faces or []
                             if min(b, float(z["t1"])) - max(a, float(z["t0"])) > 0]]
            avoid += [hb for hb in (layer_rect(h, (a + b) / 2) for h in hooks
                                    if min(b, _num(h.get("end"), b)) - max(a, _num(h.get("start"))) > 0) if hb]
            spot = card_spot(box, avoid)
            if spot:
                fix = (f"поставь мимо субтитров, лица и хука: x={spot['x']} y={spot['y']}, props.w ≤ {spot['w_px']}; "
                       f"или {fix}")
        return _finding("montage", "warn", f"{what} {lid} на {a:.1f}–{b:.1f} с закрывает субтитры {caps.get('id')} "
                                           f"({share:.0%} их полосы) — слова не читаются", fix, t=a, until=b, layer=lid)
    return None


def card_spot(rect: tuple, avoid: list[tuple]) -> dict | None:
    """Место карточки ``rect`` (px) рядом с текущим, где она не трогает ``avoid`` (лицо, хук, субтитры):
    ``{x, y, w_px}`` (центр — доли кадра, ширина ≤ 40 % кадра) или None — места нет."""
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w <= 0 or h <= 0:
        return None
    k = min(1.0, CARD_MAX_SHARE * W / w)
    w, h = w * k, h * k
    cx0, cy0 = (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2
    best = None
    for shrink in (1.0, 0.8, 0.65):
        cw, ch = w * shrink, h * shrink
        if cw < IMAGE_MIN_VISIBLE_SHARE * W * 0.8:
            break
        for ix in range(41):
            cx = cw / 2 + CARD_MARGIN + (W - cw - 2 * CARD_MARGIN) * ix / 40
            for iy in range(61):
                cy = ch / 2 + 0.1 * H + (0.8 * H - ch) * iy / 60
                box = (cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2)
                if any(_rect_overlap(box, (z[0] - CARD_MARGIN / 2, z[1] - CARD_MARGIN / 2, z[2] + CARD_MARGIN / 2,
                                           z[3] + CARD_MARGIN / 2)) > 0 for z in avoid):
                    continue
                d = (cx - cx0) ** 2 + (cy - cy0) ** 2
                if best is None or d < best[0]:
                    best = (d, cx, cy, cw)
        if best:
            break
    if not best:
        return None
    return {"x": round(best[1] / W, 2), "y": round(best[2] / H, 2), "w_px": int(best[3])}


def _natural_size(store: Any, ref: Any) -> tuple | None:
    if store is None or not str(ref or "").isdigit():
        return None
    try:
        asset = store.get_montage_asset(int(ref))
    except Exception:  # noqa: BLE001
        return None
    w, h = asset.get("width"), asset.get("height")
    return (int(w), int(h)) if w and h else None


def image_card_findings(store: Any, layers: list[dict], duration: float | None = None,
                        only_ids: set[str] | None = None, faces: list[dict] | None = None) -> list[dict]:
    """Картинка-карточка под хуком, на лице аватара и мелкая картинка.

    QA 02.11 (клип 892): кровать ≈300 px под самым хуком, почти не читается. QA 3 (клип 895): карточки
    500 px на y 0.40 закрыли лицо Муры, первая — ещё и хук; проверка считала картинку квадратом (размер
    файла не брала), а лицо не знала. ``faces`` — где лицо аватара (``agent_tools.avatar_face_zones``,
    ``[{t0, t1, rect}]`` px): картинка/видео не должны закрывать его больше ``FACE_COVER_SHARE``; вставка
    во весь кадр — не дольше 3 с. Подсказка — место у края кадра (≤ 40 % ширины) мимо лица, хука и
    субтитров. Наклейки из паков и картинки за видео не проверяем."""
    from app.promo_lint import layer_rect

    out: list[dict] = []
    visible = [l for l in layers if isinstance(l, dict) and not l.get("hidden")]
    hooks = _hook_layers(visible)
    for layer in visible:
        kind = layer.get("type")
        if kind not in ("image", "video") or layer.get("behind") or only_ids is not None and str(layer.get("id")) not in only_ids:
            continue
        props = layer.get("props") if isinstance(layer.get("props"), dict) else {}
        ref = props.get("asset_id") or props.get("src")
        if props.get("device") or str(ref or "").startswith(("sticker:", "emoji:", "meme:")):
            continue
        start, end = _num(layer.get("start")), _num(layer.get("end"), duration or 0.0)
        if end <= start:
            continue
        natural = _natural_size(store, props.get("asset_id"))
        if natural:
            layer = {**layer, "_natural": natural}  # пропорция файла: вертикальная карточка — не квадрат
        mid = (start + end) / 2
        rect = layer_rect(layer, mid)
        if not rect:
            continue
        width = rect[2] - rect[0]
        lid = str(layer.get("id") or kind)
        what = "картинка" if kind == "image" else "видео"
        full = width >= IMAGE_FULL_SHARE * W
        covered = _covered_captions(visible, layer, start, end, faces, hooks, duration)
        if covered:
            out.append(covered)
        hook_boxes: list[tuple] = []
        for hook in hooks:
            a, b = max(start, _num(hook.get("start"))), min(end, _num(hook.get("end"), duration or 0.0))
            if b - a >= 0.1:
                box = layer_rect(hook, (a + b) / 2)
                if box:
                    hook_boxes.append(box)
        for zone in faces or []:
            a, b = max(start, float(zone["t0"])), min(end, float(zone["t1"]))
            if b - a < 0.1:
                continue
            face = tuple(zone["rect"])
            if full:
                if end - start > FULL_INSERT_MAX_SEC + 0.05:
                    out.append(_finding("montage", "warn",
                                        f"{what} {lid} во весь кадр {end - start:.1f} с закрывает аватара",
                                        f"вставка во весь кадр — не дольше {FULL_INSERT_MAX_SEC:g} с (update_layer end) "
                                        "или карточкой у края кадра (≤ 40 % ширины)", t=start, until=end, layer=lid))
                break
            box = layer_rect(layer, (a + b) / 2)
            face_area = max(1.0, (face[2] - face[0]) * (face[3] - face[1]))
            if not box or _rect_overlap(box, face) / face_area < FACE_COVER_SHARE:
                continue
            avoid = [face, *hook_boxes]
            band = _captions_band(visible, a, b)
            if band:
                avoid.append(band)
            spot = card_spot(box, avoid)
            full_fix = (f"вставка во весь кадр не дольше {FULL_INSERT_MAX_SEC:g} с (props.w 1080 или add_insert "
                        "full — на размытой подложке)")
            fix = (f"поставь мимо лица, хука и субтитров: x={spot['x']} y={spot['y']}, props.w ≤ {spot['w_px']} "
                   f"(≤ 40 % кадра); или {full_fix}" if spot else
                   f"аватар во весь кадр — у края места мимо лица нет: {full_fix}, или картинка шире и ниже "
                   "(между лицом и субтитрами)")
            out.append(_finding("montage", "warn",
                                f"{what} {lid} на {a:.1f}–{b:.1f} с закрывает лицо аватара "
                                f"({_rect_overlap(box, face) / face_area:.0%} лица)", fix, t=a, until=b, layer=lid))
            break
        if full:
            continue
        if kind != "image":
            seen = None
        else:
            share = 1.0
            if store is not None and str(ref or "").isdigit():
                try:
                    share = visible_width_share(store.get_montage_asset(int(ref))["file_path"])
                except Exception:  # noqa: BLE001
                    share = 1.0
            seen = width * share
        if seen is not None and seen < IMAGE_MIN_VISIBLE_SHARE * W:
            need = IMAGE_MIN_VISIBLE_SHARE * W / max(1.0, seen) * _num(layer.get("scale"), 1.0)
            out.append(_finding("montage", "warn",
                                f"картинка {lid} видна ≈{seen:.0f} px ({seen / W:.0%} ширины кадра"
                                + (", без прозрачных полей" if share < 0.98 else "") + ") — мелко, не читается",
                                f"крупнее: scale ≈{need:.2f} или props.w больше (карточка — 40–60 % ширины)",
                                t=start, until=end, layer=lid))
        for hook in hooks:
            a, b = max(start, _num(hook.get("start"))), min(end, _num(hook.get("end"), duration or 0.0))
            if b - a < 0.1:
                continue
            t = (a + b) / 2
            box, hook_box = layer_rect(layer, t), layer_rect(hook, t)
            if not box or not hook_box:
                continue
            if box[0] < hook_box[2] and hook_box[0] < box[2] and box[1] < hook_box[3] and hook_box[1] < box[3]:
                fix = (f"опусти картинку ниже хука (y ≥ {(hook_box[3] + (box[3] - box[1]) / 2 + 30) / H:.2f}) "
                       "или поставь её после ухода хука")
                face_now = [tuple(z["rect"]) for z in faces or [] if min(b, float(z["t1"])) - max(a, float(z["t0"])) > 0]
                if face_now:
                    # Ниже хука у аватара — лицо: место мимо хука, лица и субтитров.
                    band = _captions_band(visible, a, b)
                    spot = card_spot(box, [hook_box, *face_now, *([band] if band else [])])
                    fix = (f"поставь мимо хука и лица: x={spot['x']} y={spot['y']}, props.w ≤ {spot['w_px']}, или после "
                           "ухода хука" if spot else "мимо хука и лица места нет — поставь после ухода хука")
                out.append(_finding("montage", "warn",
                                    f"{what} {lid} на {a:.1f}–{b:.1f} с лежит под хуком {hook.get('id')} — "
                                    "хук закрыт картинкой, картинка — текстом", fix, t=a, until=b, layer=lid))
                break
    return out


def avatar_face_findings(store: Any, clip_plan_id: int, rs: dict) -> list[dict]:
    """avatar_full: лицо аватара (бокс detect_avatar) выходит за кадр — рамка или крупность режут лицо (QA 5,
    клип 897: правый глаз за кадром 20 с, lint молчал)."""
    try:
        from app.agent_tools import _SIDE_RU, avatar_face_report

        rows = avatar_face_report(store, clip_plan_id, rs)
    except Exception:  # noqa: BLE001 - без лица — проверки нет
        return []
    return [_finding("montage", "warn", f"лицо аватара срезано краем кадра на {r['from_t']:.1f}–{r['to_t']:.1f} с "
                                        f"({', '.join(_SIDE_RU.get(s, s) for s in r['cut'])})",
                     "set_layout mode avatar_full cams \"auto\" — рамка и крупность встанут по лицу; рамку руками — "
                     "шире к лицу или video_scale меньше", t=r["from_t"], until=r["to_t"])
            for r in rows if not r["face_whole"]]


def avatar_presence_findings(store: Any, clip_plan_id: int, rs: dict) -> list[dict]:
    """avatar_full: аватара в кадре нет — рамка не накрывает ни одно место из detect_avatar или проверка кропа
    Gemini-зрением сказала «не аватар» (кэш app/ai/avatar_check.py). QA 6 (клип 898): 23 с плеера YouTube вместо
    Муры, lint — 0 предупреждений."""
    try:
        from app.agent_tools import _absent_text, avatar_absent_spans

        rows = avatar_absent_spans(store, clip_plan_id, rs)
    except Exception:  # noqa: BLE001 - нет кэша/источника — проверки нет
        return []
    return [_finding("montage", "warn", "аватара в кадре нет на " + _absent_text(r),
                     "set_layout mode avatar_full cams \"auto\" (рамки по detect_avatar) или рамка руками на сам аватар "
                     "(source_contact_sheet покажет, где он); не выходит — спроси владельца",
                     t=r["from_t"], until=r["to_t"])
            for r in rows]


def tail_fragment_findings(store: Any, plan: dict, rs: dict) -> list[dict]:
    """Клип кончается обрывком новой фразы, и вырезы его не убрали (QA 6, клип 898: «Седьмой стрель.» после
    панчлайна). Слова — субтитры клипа (шкала clip до вырезов, как cuts)."""
    from app.cuts import plan_duration, tail_fragment

    try:
        words = (store.get_clip_plan_subtitles(int(plan["id"])) or {}).get("words") or []
        tail = tail_fragment(words, plan_duration(plan))
    except Exception:  # noqa: BLE001 - нет субтитров — проверки нет
        return []
    if not tail:
        return []
    cuts = [(_num(c.get("start")), _num(c.get("end"))) for c in rs.get("cuts") or [] if isinstance(c, dict)]
    if any(s <= tail["start"] + 0.05 and e >= tail["end"] - 0.05 for s, e in cuts):
        return []
    return [_finding("montage", "warn", f"клип кончается оборванной фразой «{tail['words']}» "
                                        f"({tail['start']:.2f}–{tail['end']:.2f} с) — конец на полуслове",
                     f"вырез {{start: {tail['start']:.2f}, end: {tail['end']:.2f}}} в apply_cuts (suggest_cuts его "
                     "предлагает) или trim_piece конца куска", t=tail["start"], until=tail["end"])]


def fragment_findings(store: Any, plan: dict, rs: dict, clock: Any) -> tuple[list[dict], dict]:
    """Обрывки по текущему плану. Вырез оставил кусок — warn; короткий кусок целиком — info:
    его могли создать таким нарочно (кадр под баннер в промо)."""
    from app.cuts import kept_islands, short_islands

    try:
        words = (store.get_clip_plan_subtitles(int(plan["id"])) or {}).get("words") or []
    except Exception:  # noqa: BLE001 - нет субтитров — только по длине
        words = []
    islands = kept_islands(rs.get("cuts") or [], words, plan.get("segments") or [])
    short = short_islands(islands, bool(words), by_cut_only=False)
    out = []
    for island in short[:MAX_FRAGMENT_FINDINGS]:
        parts = clock.plain_range_to_clip(island["start"], island["end"])
        a, b = (parts[0]["start"], parts[-1]["end"]) if parts else (island["start"], island["end"])
        heard = f"«{_short(island['text'], 40)}»" if island["text"] else "кусок без речи"
        if island["by_cut"]:
            out.append(_finding("fragments", "warn",
                                f"обрывок {heard}: {island['duration']:.1f} с, слов {island['words']} — вспышка без начала и конца",
                                "сдвинь край соседнего выреза, чтобы фраза прозвучала целиком, или вырежи её "
                                "(apply_cuts с merge_short_islands: true)", t=a, until=b))
        else:
            out.append(_finding("fragments", "info",
                                f"короткий кусок {heard}: {island['duration']:.1f} с, слов {island['words']}",
                                "кусок нужен как кадр под титр — оставь; это момент — расширь (trim_piece) до законченной фразы",
                                t=a, until=b))
    return out, {"pieces": len(islands), "short": len(short)}


def sub_sync_findings(store: Any, plan: dict, clock: Any) -> tuple[list[dict], dict]:
    """Субтитры против звука. Главное — сверка показанного со сказанным (app/speech_heard.py): VAD знает только,
    что кто-то говорит, и пропускал страницы на секунды раньше своей фразы. Вырезанное не считается: зритель
    его не увидит. Распознать нечем — остаётся VAD, и ответ говорит, что текст не сверен (``by_text``)."""
    from app.piece_speed import seg_speed
    from app.speech_activity import line_sync, plan_speech_spans
    from app.speech_heard import plan_groups, plan_heard_words, text_sync

    payload = store.get_clip_plan_subtitles(int(plan["id"])) or {}
    lines, words = payload.get("lines") or [], payload.get("words") or []
    if not lines or not words:
        return [], {}
    if any(abs(seg_speed(s) - 1.0) > 1e-3 for s in plan.get("segments") or []):
        return [], {"skipped": "у кусков своя скорость — звук клипа не совпадает со шкалой слов"}
    labels = {"early": "раньше голоса на", "late": "позже голоса на"}
    counts: dict[str, Any] = {"lines": len(lines), "early": 0, "late": 0, "silent": 0, "lost": 0, "drift": 0, "unheard": 0}
    cuts = (plan.get("render_settings") or {}).get("cuts") or []
    spans = plan_speech_spans(store, int(plan["id"]))
    issues: list[dict] = []
    try:
        issues, counts["pages_checked"] = text_sync(plan_groups(plan, lines, words), words,
                                                    plan_heard_words(store, int(plan["id"])), cuts)
        counts["by_text"] = True
    except Exception as exc:  # noqa: BLE001 - нет модели или выключено распознавание: скажем честно, VAD останется
        counts["by_text"] = False
        counts["by_text_note"] = f"текст со сказанным не сверен: {str(exc)[:160]}"
    by_text = [float(i["start"]) for i in issues]
    for issue in line_sync(lines, words, spans):
        # Та же строка уже сверена по тексту — это точнее: VAD не отличает её голос от чужого.
        if not (issue["kind"] in labels and any(abs(float(issue["start"]) - t) <= 0.3 for t in by_text)):
            issues.append(issue)
    order = {"early": 0, "late": 0, "drift": 0, "silent": 1, "lost": 2, "unheard": 3}
    issues.sort(key=lambda i: (order[i["kind"]], -abs(float(i.get("by") or 0))))
    out: list[dict] = []
    for issue in issues:
        parts = clock.plain_range_to_clip(issue["start"], issue["end"])
        if not parts or any(float(c["start"]) <= issue["start"] < float(c["end"]) for c in cuts):
            continue
        counts[issue["kind"]] += 1
        if len(out) >= MAX_SYNC_FINDINGS:
            continue
        t = parts[0]["start"]
        said = f"«{_short(issue['text'], 40)}»"
        if issue["kind"] in labels:
            out.append(_finding("sub_sync", "warn", f"строка {said} {labels[issue['kind']]} {issue['by']:.2f} с",
                                "snap_subtitles_to_speech поставит слова на голос", t=t, until=parts[-1]["end"]))
        elif issue["kind"] == "drift":
            out.append(_finding("sub_sync", "warn", f"строка {said} уплывает: к концу расходится с голосом на {issue['by']:+.2f} с",
                                "snap_subtitles_to_speech поставит слова на голос", t=t, until=parts[-1]["end"]))
        elif issue["kind"] == "silent":
            out.append(_finding("sub_sync", "warn", f"строка {said}: голоса на этом месте нет",
                                "проверь listen: текст не отсюда или время уехало", t=t, until=parts[-1]["end"]))
        elif issue["kind"] == "unheard":
            out.append(_finding("sub_sync", "info", f"строка {said}: распознавание не слышит этих слов",
                                "послушай listen: текст не отсюда или он под игрой", t=t, until=parts[-1]["end"]))
        else:
            out.append(_finding("sub_sync", "info", f"перед строкой {said} {issue['by']:.1f} с речи без субтитров",
                                "послушай listen: потерянная фраза или чужой голос (separate_voices)", t=t, until=parts[-1]["end"]))
    return out, counts
