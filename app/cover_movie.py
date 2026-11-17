"""Превью «как в ролике»: кадр + надписи в стиле самого видео (эталон — превью мувика BURN, одобрено 17.11).

Раскладка на 16:9 (1280×720, YouTube; другие размеры — в тех же долях):

* кадр на весь холст: без чёрных полос, интерфейс игры закрашен (``hud``), окно кадра — ``zoom`` и центр;
* вид кадра (``style``): ``movie`` — ЧБ, остаются только оранжевый и красный (огонь, хитмаркер), тёмная сторона
  под текст, полоса сверху, виньетка; ``color`` — свои цвета; ``clean`` — без текста;
* надписи на стороне ``side``: мелкая разреженная строка (``top_line``, Montserrat Bold), огромное слово
  (``big_word``, Montserrat ExtraBold, белое, кромка двумя цветами, свечение, тонкая черта), строка под ним
  (``subline``); цвета акцента — ``accents`` → цвета бренда проекта (акцент и подсветка) → фиолетовый/оранжевый;
* логотип бренда проекта внизу на стороне текста (``logo``).

Вкус владельца (17.11): жирный жёлтый слоган с толстой обводкой — «колхоз», радужный градиент — нет; надписи как
в самом ролике («ЧБ с акцентами»), название канала текстом не повторять, если стоит логотип; слоганы не придумывать.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import cover_doc_fx as FX
from app.settings import settings

STYLES = ("movie", "color", "clean")
SIDES = ("left", "right")
REF_W, REF_H = 1280, 720
MARGIN = 64
LOGO_W = 230
MAX_WORD_SIZE = 190
MIN_WORD_SIZE = 56


def _font_file(weight: int) -> Path:
    name = {700: "Montserrat-Bold.ttf", 800: "Montserrat-ExtraBold.ttf"}.get(weight, "Montserrat-Black.ttf")
    return Path(settings.motion_fonts_dir) / name


def tracked_width(text: str, size: float, spacing: float, weight: int) -> float:
    """Ширина строки с трекингом — как measureLine в coverdoc.ts (сумма ширин букв + промежутки)."""
    from PIL import ImageFont

    try:
        font = ImageFont.truetype(str(_font_file(weight)), max(1, int(round(size))))
    except OSError:
        return len(text) * size * 0.75 + spacing * max(0, len(text) - 1)
    letters = list(text.upper())
    return sum(font.getlength(ch) for ch in letters) + spacing * max(0, len(letters) - 1)


def word_size(word: str, max_width: float, scale: float = 1.0) -> float:
    """Кегль ударного слова: как у BURN (190 на 1280), но так, чтобы слово влезло в ``max_width``."""
    size = MAX_WORD_SIZE * scale
    while size > MIN_WORD_SIZE * scale:
        if tracked_width(word, size, 14 * size / 190, 800) <= max_width:
            break
        size -= 4 * scale
    return round(size, 1)


def brand_accents(store: Any, project_id: int | None) -> tuple[str, str] | None:
    """Акцент и подсветка бренда проекта (оба заданы) — цвета кромки и черты."""
    if project_id is None:
        return None
    try:
        from app import projects as P

        brand_id = P.get_project(store, int(project_id), include_sources=False).get("brand_id")
        brand = store.get_brand(int(brand_id)) if brand_id else None
    except Exception:  # noqa: BLE001
        return None
    colors = (brand or {}).get("colors") or {}
    if colors.get("accent") and colors.get("highlight"):
        return str(colors["accent"])[:7], str(colors["highlight"])[:7]
    return None


def brand_logo(store: Any, doc_id: int, project_id: int | None) -> dict | None:
    """Логотип бренда проекта копией в файлы документа: {name, width, height} или None."""
    if project_id is None:
        return None
    try:
        from app import cover_docs as D
        from app import projects as P

        brand_id = P.get_project(store, int(project_id), include_sources=False).get("brand_id")
        brand = store.get_brand(int(brand_id)) if brand_id else None
        if not brand or not brand.get("logo_asset_id"):
            return None
        asset = store.get_montage_asset(int(brand["logo_asset_id"]))
        data = Path(asset["file_path"]).read_bytes()
        return D.store_upload(doc_id, data)
    except Exception:  # noqa: BLE001 - без логотипа превью всё равно собирается
        return None


def crop_window(zoom: Any, cx: Any, cy: Any, img_w: int, img_h: int, box_w: int, box_h: int) -> dict | None:
    """Окно кадра (доли картинки) под приближение ``zoom`` ≥ 1 с центром (cx, cy); 1 и центр — None (весь кадр
    по «заполнить рамку»)."""
    try:
        z = max(1.0, min(4.0, float(zoom or 1)))
        x, y = float(0.5 if cx is None else cx), float(0.5 if cy is None else cy)
    except (TypeError, ValueError):
        return None
    if not img_w or not img_h:
        return None
    # Сколько картинки помещается в рамку при «заполнить» — и ещё в z раз меньше.
    box, src = box_w / box_h, img_w / img_h
    w, h = (box / src, 1.0) if src > box else (1.0, src / box)
    w, h = w / z, h / z
    if w >= 0.999 and h >= 0.999:
        return None
    x0 = min(max(0.0, x - w / 2), 1 - w)
    y0 = min(max(0.0, y - h / 2), 1 - h)
    return {"x": round(x0, 4), "y": round(y0, 4), "w": round(w, 4), "h": round(h, 4)}


def _text_layer(name: str, text: str, x: float, baseline: float, width: float, size: float, align: str,
                style: str, accents: tuple[str, str]) -> dict:
    # coverdoc.ts: базовая линия = верх + lineHeight·size/2 + 0.36·size; у нас lineHeight 1 и valign top.
    top = baseline - 0.86 * size
    return {"type": "text", "name": name, "text": text, "x": round(x, 2), "y": round(top, 2), "w": round(width, 2),
            "h": round(size, 2), "size": size, "align": align, "valign": "top",
            **FX.text_style(style, size, accents)}


def movie_layers(*, width: int, height: int, frame: dict, big_word: str, top_line: str = "", subline: str = "",
                 style: str = "movie", side: str = "left", logo: dict | None = None,
                 accents: tuple[str, str] = FX.DEFAULT_ACCENTS, zoom: Any = 1, center: tuple[Any, Any] = (None, None)) -> list[dict]:
    """Слои превью (сырые, до sanitize_doc): кадр, логотип, строки. ``frame`` — {name, width, height} файла."""
    style = style if style in STYLES else "movie"
    side = side if side in SIDES else "left"
    k = min(width / REF_W, height / REF_H)
    image = {"type": "image", "name": "Кадр", "src": frame["name"], "x": 0, "y": 0, "w": width, "h": height,
             "fit": "cover", "locked": True,
             "crop": crop_window(zoom, center[0], center[1], int(frame.get("width") or 0), int(frame.get("height") or 0),
                                 width, height),
             "filters": FX.look_filters("color" if style == "clean" else style, side)}
    layers: list[dict] = [image]
    margin = MARGIN * k
    text_w = width - 2 * margin
    align = "left" if side == "left" else "right"
    if style != "clean":
        word = big_word.strip()
        size = word_size(word, width * 0.62, k) if word else MAX_WORD_SIZE * k
        base = height * 420 / REF_H
        small, mid = round(26 * k, 1), round(38 * k, 1)
        # Небольшой отступ у разреженных строк: буква слова шире своей рамки на трекинг.
        nudge = 4 * k if side == "left" else -4 * k
        if top_line.strip():
            layers.append(_text_layer("Строка сверху", top_line.strip(), margin + nudge, base - 196 * size / MAX_WORD_SIZE,
                                      text_w, small, align, "movie-caps", accents))
        if word:
            layers.append(_text_layer("Главное слово", word, margin, base, text_w, size, align, "movie-word", accents))
        if subline.strip():
            # Под чертой слова (её низ — base + 24 при кегле 190) и ещё воздух на строку.
            layers.append(_text_layer("Строка снизу", subline.strip(), margin + nudge, base + 24 * size / MAX_WORD_SIZE + 60 * k,
                                      text_w, mid, align, "movie-caps", accents))
    if logo and logo.get("width") and logo.get("height"):
        lw = LOGO_W * k
        lh = lw * logo["height"] / logo["width"]
        lx = margin if side == "left" else width - margin - lw
        layers.append({"type": "image", "name": "Логотип", "src": logo["name"], "x": round(lx, 2),
                       "y": round(height - lh - 40 * k, 2), "w": round(lw, 2), "h": round(lh, 2), "fit": "contain"})
    return layers


def _accents(raw: Any) -> tuple[str, str] | None:
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        a, b = (str(v)[:7] for v in raw[:2])
        if FX.COLOR_RE.match(a) and FX.COLOR_RE.match(b):
            return a, b
    return None


def build_movie_doc(store: Any, doc_id: int, *, big_word: str, top_line: str = "", subline: str = "",
                    style: str = "movie", side: str = "left", logo: Any = True, accents: Any = None, zoom: Any = 1,
                    center_x: Any = None, center_y: Any = None, t: Any = None, frame: str = "", hud: str = "auto",
                    hud_rects: list | None = None) -> dict:
    """Слои превью «как в ролике» для документа (не сохраняет): кадр ``frame`` или кадр ролика на ``t`` (нет —
    сильнейший из кандидатов), интерфейс игры закрашен (``hud`` auto — найденный + ``hud_rects``; off — только
    ``hud_rects``). Ответ: {layers, assets, frame, t, hud_boxes, warnings}."""
    from app import cover_docs as D
    from app import cover_photo as PH

    doc = D.get_doc(store, doc_id)
    width, height = doc["doc"]["width"], doc["doc"]["height"]
    warnings: list[str] = []
    if style != "clean" and not str(big_word or "").strip():
        raise ValueError("нужно главное слово (big_word) — или style clean без текста")
    picked_t = None
    if frame:
        if D.asset_path(doc_id, frame) is None:
            raise ValueError("нет такого файла в документе")
        made = D.describe_asset(doc_id, frame)
    else:
        if t in (None, ""):
            cands = PH.frame_candidates(store, doc_id)
            t = cands[0]["t"] if cands else 0
        made = PH.add_frame(store, doc_id, t, "render")
        picked_t = made["t"]
    boxes: list[dict] = []
    rects = [r for r in (hud_rects or []) if isinstance(r, dict)]
    if hud == "auto" or rects:
        try:
            cleaned = PH.clean_frame(store, doc_id, made["name"], rects, auto=hud == "auto")
            boxes = cleaned["rects"]
            made = cleaned
        except ValueError as exc:
            if "нечего убирать" not in str(exc):
                warnings.append(f"интерфейс не убран: {exc}")
        except RuntimeError as exc:
            warnings.append(f"интерфейс не убран: {exc}")
    project_id = doc.get("project_id")
    pair = _accents(accents) or brand_accents(store, project_id) or FX.DEFAULT_ACCENTS
    logo_file = None
    if isinstance(logo, str) and logo:
        logo_file = D.describe_asset(doc_id, logo) if D.asset_path(doc_id, logo) else None
    elif logo:
        logo_file = brand_logo(store, doc_id, project_id)
        if logo_file is None:
            warnings.append("у бренда проекта нет логотипа — превью без него (логотип: бренд проекта в «Оформлении»)")
    layers = movie_layers(width=width, height=height, frame=made, big_word=str(big_word or ""),
                          top_line=str(top_line or ""), subline=str(subline or ""), style=style, side=side,
                          logo=logo_file, accents=pair, zoom=zoom, center=(center_x, center_y))
    clean = D.sanitize_doc({"layers": layers}, width, height, D.doc_assets(doc_id))["layers"]
    return {"layers": clean, "bg": "#050507", "assets": sorted(D.doc_assets(doc_id)), "frame": made["name"],
            "t": picked_t, "hud_boxes": boxes, "accents": list(pair), "warnings": warnings}
