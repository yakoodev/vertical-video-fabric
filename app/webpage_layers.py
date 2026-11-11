"""Разбор снимка страницы на слои: каждый осмысленный блок — своя прозрачная PNG, чтобы его анимировать отдельно.

``decompose`` открывает копию версии (``v<n>/index.html``, без скриптов и без сети) в headless Chromium тем же экраном
и DPR, что при снимке (``node/webpage/layers.mjs``), и находит блоки: шапку, заголовки, абзацы, кнопки, цены, значки,
картинки, карточки товара (с детьми — картинкой, названием, ценой, когда карточка в фокусе) и фоны секций. Каждый блок
снимается отдельно — всё прочее на странице скрыто, окно прозрачное: свой фон, скругление и тень блока остаются.
Плюс «фон страницы» — та же область без блоков — и полный кадр области: фон и блоки, сложенные на своих местах,
совпадают с ним (``recompose``; проверка — tests/test_webpage_layers.py).

Простой текст (один стиль, без картинок и своей подложки) дополнительно описан стилем (``text_style``): его можно
поставить живым текстом, если шрифт есть в библиотеке моушна или подключается файлом страницы (``text_font``).

Результат лежит рядом с версией: ``data/web_pages/<id>/v<n>/layers/<key>/`` — ``blocks.json``, ``full.png``,
``plate.png``, ``b<i>.png``; повторный разбор с теми же параметрами браузер не поднимает. Слои клипа из него
собирает app/webpage_layers_clip.py.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

from app import webpages as W
from app.settings import settings

LAYERS_SCRIPT = Path(settings.root_dir) / "node" / "webpage" / "layers.mjs"
# Версия алгоритма разбора: меняется — старые разборы не берутся из кэша.
ALGO = 2
DEFAULT_MAX_LAYERS = 40
MAX_LAYERS_LIMIT = 80
# Область разбора не выше (CSS px): кадр фона в DPR 3 — до ~9000 px в высоту.
MAX_REGION_H = 3000
KIND_LABEL = {
    "header": "Шапка", "heading": "Заголовок", "text": "Текст", "button": "Кнопка", "price": "Цена",
    "badge": "Значок", "image": "Картинка", "card": "Карточка", "section": "Фон блока",
}
TEXT_KINDS = ("heading", "text", "price", "button", "badge")
FONT_EXTS = (".ttf", ".otf", ".woff2")
GENERIC_FAMILIES = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui", "ui-sans-serif",
                    "ui-serif", "ui-monospace", "-apple-system", "blinkmacsystemfont", "emoji", "math"}


class LayersError(W.WebPageError):
    """Разбор не получился — текст для владельца и модели."""


# --------------------------------------------------------------------------- #
# Область
# --------------------------------------------------------------------------- #


def region_spec(meta: dict, region: Any) -> dict:
    """Что разбирать: {"region": {x,y,w,h}} | {"selector": css} | {"screen": true} | {} (вся страница).

    ``region``: None — окно crop_to версии (или вся страница); dict x/y/w/h (CSS px); строка-селектор;
    "screen" — первый экран; "page" — вся страница; {"selector": …}."""
    if isinstance(region, str):
        text = region.strip()
        if text in ("", "auto"):
            region = None
        elif text == "page":
            return {}
        elif text == "screen":
            return {"screen": True}
        else:
            return {"selector": text[:300]}
    if isinstance(region, dict) and region.get("selector"):
        return {"selector": str(region["selector"])[:300]}
    if isinstance(region, dict):
        try:
            box = {k: round(float(region[k]), 1) for k in ("x", "y", "w", "h")}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("region — {x, y, w, h} в CSS px страницы, селектор, \"screen\" или \"page\"") from exc
        if box["w"] <= 0 or box["h"] <= 0:
            raise ValueError("region: w и h должны быть больше 0")
        return {"region": box}
    saved = meta.get("region")
    if isinstance(saved, dict) and saved.get("w") and saved.get("h"):
        return {"region": {k: round(float(saved[k]), 1) for k in ("x", "y", "w", "h")}}
    return {}


def _browser_region(spec: dict, meta: dict, viewport: str) -> tuple[dict | None, str | None]:
    if spec.get("selector"):
        return None, spec["selector"]
    page_w = float(meta.get("page_width") or W.VIEWPORT_WIDTH.get(viewport, 390))
    if spec.get("screen"):
        return {"x": 0, "y": 0, "w": page_w, "h": round(page_w * (19.5 / 9 if viewport == "mobile" else 10 / 16))}, None
    if spec.get("region"):
        return dict(spec["region"]), None
    shot_h = float(meta.get("shot_height") or meta.get("page_height") or page_w * 2)
    return {"x": 0, "y": 0, "w": page_w, "h": min(shot_h, MAX_REGION_H)}, None


# --------------------------------------------------------------------------- #
# Имена слоёв
# --------------------------------------------------------------------------- #


def _snippet(text: str, limit: int = 34) -> str:
    text = re.sub(r"\s+", " ", str(text or "").replace(" ", " ")).strip(" «»\"'·—-")
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def block_name(block: dict) -> str:
    """Разговорное имя слоя: «Кнопка: Смотреть каталог», «Карточка: Ferret», «Цена: 1 290 ₽»."""
    kind = block.get("kind")
    label = KIND_LABEL.get(kind, "Блок")
    if kind == "header":
        return "Шапка сайта"
    if kind in ("card", "section"):
        tail = _snippet(block.get("title") or block.get("alt") or "")
    elif kind == "image":
        tail = _snippet(block.get("alt") or "")
    else:
        tail = _snippet(block.get("text") or block.get("alt") or "")
    return f"{label}: {tail}" if tail else label


# --------------------------------------------------------------------------- #
# Разбор
# --------------------------------------------------------------------------- #


def _key(n: int, spec: dict, max_layers: int) -> str:
    raw = json.dumps({"algo": ALGO, "n": n, "spec": spec, "max": max_layers}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def layers_dir(page_id: int, n: int, key: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{12}", str(key or "")):
        raise KeyError("нет такого разбора")
    return W.page_dir(page_id) / f"v{int(n)}" / "layers" / key


def _stitch(folder: Path, tiles: list[dict], out_name: str) -> Any:
    """Полосы → один BGRA-кадр (numpy), файлы полос удаляются."""
    import cv2
    import numpy as np

    parts = []
    for tile in tiles:
        path = folder / tile["file"]
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        path.unlink(missing_ok=True)
        if img is None:
            raise LayersError("кадр блока не прочитался")
        if img.ndim == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
        elif img.shape[2] == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
        parts.append(img)
    if not parts:
        raise LayersError("кадр блока пустой")
    width = min(p.shape[1] for p in parts)
    full = np.vstack([p[:, :width] for p in parts]) if len(parts) > 1 else parts[0]
    if out_name:
        cv2.imwrite(str(folder / out_name), full, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    return full


def _trim(img: Any) -> tuple[int, int, int, int] | None:
    """Рамка непрозрачного (x, y, w, h) в пикселях кадра или None — блок пустой."""
    import numpy as np

    alpha = img[:, :, 3]
    rows = np.flatnonzero(alpha.max(axis=1) > 0)
    if not rows.size:
        return None
    cols = np.flatnonzero(alpha.max(axis=0) > 0)
    return int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1), int(rows[-1] - rows[0] + 1)


def decompose(store: Any, page_id: int, version: int | None = None, region: Any = None,
              max_layers: int = DEFAULT_MAX_LAYERS, runner: W.Runner | None = None, fresh: bool = False) -> dict:
    """Разобрать версию страницы на блоки (кэш по параметрам). Ошибка — LayersError / KeyError / ValueError."""
    page = W.get_page(store, page_id)
    if page["status"] != "ok":
        raise LayersError("у страницы нет снимка (капча или ошибка) — разбирать нечего")
    meta = W.version_meta(store, page_id, version)
    n = int(meta["n"])
    max_layers = max(1, min(MAX_LAYERS_LIMIT, int(max_layers or DEFAULT_MAX_LAYERS)))
    spec = region_spec(meta, region)
    key = _key(n, spec, max_layers)
    folder = layers_dir(page_id, n, key)
    cached = folder / "blocks.json"
    if cached.is_file() and not fresh:
        return json.loads(cached.read_text(encoding="utf-8"))
    if runner is None and not W.chromium_available():
        raise LayersError("нет headless Chromium для разбора страницы (в образе — /usr/bin/chromium)")
    box, selector = _browser_region(spec, meta, page["viewport"])
    work = folder.with_name(key + ".tmp")
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    args = ["--root", str(W.page_dir(page_id)), "--from", f"v{n}", "--out", str(work), "--viewport", page["viewport"],
            "--max-layers", str(max_layers), "--max-height", str(W.MAX_PAGE_HEIGHT.get(page["viewport"], 6000))]
    if selector:
        args += ["--selector", selector]
    elif box:
        args += ["--region", json.dumps(box)]
    try:
        res = W._browser(args, f"разбор страницы {page_id}", timeout=240, runner=runner, script=LAYERS_SCRIPT)
        if not res.get("ok"):
            raise LayersError(str(res.get("reason") or "разбор не получился"))
        out = _collect(work, res, page_id, n, key, spec)
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise
    (work / "blocks.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    shutil.rmtree(folder, ignore_errors=True)
    work.rename(folder)
    return out


def _collect(work: Path, res: dict, page_id: int, n: int, key: str, spec: dict) -> dict:
    """Склеить полосы, обрезать блоки по непрозрачному, записать рамки (CSS px страницы и px кадра области)."""
    import cv2

    dpr = float(res.get("dpr") or 1)
    region = res["region"]
    if float(region["h"]) > MAX_REGION_H:
        raise LayersError(f"область выше {MAX_REGION_H} px страницы — возьми окно поменьше (region)")
    full = _stitch(work, res["full"]["tiles"], "full.png")
    size = [int(full.shape[1]), int(full.shape[0])]
    del full
    _stitch(work, res["plate"]["tiles"], "plate.png")
    blocks = []
    dropped = set()
    for blk in res.get("blocks") or []:
        img = _stitch(work, blk["tiles"], "")
        trim = _trim(img)
        if trim is None:
            dropped.add(blk["i"])
            continue
        x0, y0, w, h = trim
        name = f"b{blk['i']}.png"
        cv2.imwrite(str(work / name), img[y0:y0 + h, x0:x0 + w], [cv2.IMWRITE_PNG_COMPRESSION, 3])
        clip = blk["clip"]
        px = [round((clip["x"] - region["x"]) * dpr) + x0, round((clip["y"] - region["y"]) * dpr) + y0, w, h]
        rec = {k: blk.get(k) for k in ("i", "parent", "kind", "tag", "text", "title", "alt", "sel", "paint")}
        rec.update({
            "id": f"b{blk['i']}", "file": name, "px": px,
            "box": [round(region["x"] + px[0] / dpr, 2), round(region["y"] + px[1] / dpr, 2),
                    round(w / dpr, 2), round(h / dpr, 2)],
            "dom_box": blk.get("box"),
        })
        if blk.get("text_style"):
            rec["text_style"] = blk["text_style"]
        rec["name"] = block_name(rec)
        blocks.append(rec)
    # Пустой блок выпал — его дети теперь дети его родителя (или верхние).
    parents = {b["i"]: b["parent"] for b in res.get("blocks") or []}
    for rec in blocks:
        p = rec["parent"]
        while p in dropped:
            p = parents.get(p, -1)
        rec["parent"] = p
    return {
        "page_id": int(page_id), "version": int(n), "key": key, "algo": ALGO, "spec": spec, "dpr": dpr,
        "viewport": res.get("viewport"), "page_width": res.get("page_width"), "page_height": res.get("page_height"),
        "region": region, "size": size, "plate": "plate.png", "full": "full.png", "blocks": blocks,
        "font_faces": res.get("font_faces") or [],
    }


def block_file(page_id: int, n: int, key: str, name: str) -> Path:
    """Файл разбора (plate.png, full.png, b<i>.png) — только из его папки."""
    if not re.fullmatch(r"(plate|full|b\d{1,3})\.png", str(name or "")):
        raise KeyError("нет такого файла")
    path = layers_dir(page_id, n, key) / name
    if not path.is_file():
        raise KeyError("нет такого файла")
    return path


def recompose(page_id: int, dec: dict, only: set[str] | None = None) -> Any:
    """Фон + блоки на своих местах (порядок — родитель раньше детей, как в документе) → BGR-кадр области.

    ``only`` — какие блоки класть (id «b<i>»); нет — все. Для сверки с полным кадром и для превью."""
    import cv2
    import numpy as np

    folder = layers_dir(page_id, dec["version"], dec["key"])
    canvas = cv2.imread(str(folder / dec["plate"]), cv2.IMREAD_UNCHANGED)
    if canvas is None:
        raise LayersError("нет фона страницы")
    if canvas.shape[2] == 4:
        canvas = canvas[:, :, :3]
    canvas = canvas.astype(np.float32)
    for blk in paint_order(dec["blocks"]):
        if only is not None and blk["id"] not in only:
            continue
        img = cv2.imread(str(folder / blk["file"]), cv2.IMREAD_UNCHANGED)
        if img is None:
            continue
        x, y, w, h = blk["px"]
        x1, y1 = min(canvas.shape[1], x + w), min(canvas.shape[0], y + h)
        if x1 <= x or y1 <= y or x < 0 or y < 0:
            continue
        part = img[: y1 - y, : x1 - x].astype(np.float32)
        a = part[:, :, 3:4] / 255.0
        canvas[y:y1, x:x1] = part[:, :, :3] * a + canvas[y:y1, x:x1] * (1 - a)
    return np.clip(canvas + 0.5, 0, 255).astype(np.uint8)


def paint_order(blocks: list[dict]) -> list[dict]:
    """Порядок рисования: родитель, затем его дети; соседи — обычные по документу, позиционированные поверх них
    по z-index (ключ ``paint`` из браузера), отрицательный z-index — под обычными."""
    present = {int(b["i"]) for b in blocks}
    kids: dict[int, list[dict]] = {}
    for b in blocks:
        # Родителя в наборе нет (выбрали только кнопки) — блок рисуется как верхний.
        kids.setdefault(int(b["parent"]) if int(b["parent"]) in present else -1, []).append(b)
    out: list[dict] = []

    def walk(parent: int) -> None:
        for b in sorted(kids.get(parent, []), key=lambda x: (*(x.get("paint") or [0, 0]), int(x["i"]))):
            out.append(b)
            walk(int(b["i"]))

    walk(-1)
    return out


# --------------------------------------------------------------------------- #
# Живой текст: шрифт
# --------------------------------------------------------------------------- #


def _weight(value: Any) -> tuple[int, int]:
    text = str(value or "400").strip().lower()
    named = {"normal": 400, "bold": 700, "lighter": 300, "bolder": 800}
    nums = [int(float(x)) for x in re.findall(r"\d+(?:\.\d+)?", text)] or [named.get(text, 400)]
    return (nums[0], nums[-1])


def _ranges(text: str) -> list[tuple[int, int]]:
    out = []
    for part in str(text or "").split(","):
        part = part.strip().upper().removeprefix("U+")
        if not part:
            continue
        try:
            if "?" in part:
                out.append((int(part.replace("?", "0"), 16), int(part.replace("?", "F"), 16)))
            elif "-" in part:
                lo, hi = part.split("-", 1)
                out.append((int(lo, 16), int(hi.strip().removeprefix("U+"), 16)))
            else:
                out.append((int(part, 16), int(part, 16)))
        except ValueError:
            continue
    return out


def _covers(ranges: list[tuple[int, int]], text: str) -> bool:
    if not ranges:
        return True
    return all(any(lo <= ord(ch) <= hi for lo, hi in ranges) for ch in text if not ch.isspace())


def text_font(page_id: int, block: dict, faces: list[dict], library: list[dict] | None = None) -> dict:
    """Можно ли поставить блок живым текстом и каким шрифтом: {ok, family, source library|page, file?, reason?}.

    Только если на странице текст и правда нарисован подключённым шрифтом (@font-face загружен): системный
    шрифт браузера не повторить. Семейство есть в библиотеке моушна (толщина ±100) — оно; нет — файл шрифта
    страницы (TTF/OTF/WOFF2, один файл на весь текст по unicode-range) зарегистрируется при вставке."""
    style = block.get("text_style")
    if not style:
        return {"ok": False, "reason": "не простой текст"}
    family = str(style.get("family") or "").strip()
    if not family or family.lower() in GENERIC_FAMILIES:
        return {"ok": False, "reason": "системный шрифт браузера"}
    if not style.get("web_font"):
        return {"ok": False, "reason": f"шрифт «{family}» не подключён страницей — оставляю картинкой"}
    weight = int(style.get("weight") or 400)
    if library is None:
        from app.motion_assets import load_fonts

        library = load_fonts()
    same = [f for f in library if str(f.get("family") or "").lower() == family.lower()]
    if same:
        for f in same:
            lo, hi = (f.get("weight_range") or [f.get("weight") or 400, f.get("weight") or 400])[:2]
            if int(lo) - 100 <= weight <= int(hi) + 100:
                return {"ok": True, "family": f["family"], "source": "library", "weight": weight}
        return {"ok": False, "reason": f"у «{family}» в библиотеке нет толщины {weight}"}
    text = str(style.get("text") or "")
    want_italic = bool(style.get("italic"))
    picks = []
    for face in faces:
        if str(face.get("family") or "").lower() != family.lower():
            continue
        if (str(face.get("style") or "normal").lower() == "italic") != want_italic:
            continue
        lo, hi = _weight(face.get("weight"))
        if not (lo - 100 <= weight <= hi + 100):
            continue
        if not _covers(_ranges(face.get("range") or ""), text):
            continue
        for src in face.get("src") or []:
            name = src.split("?")[0].split("#")[0].rsplit("/", 1)[-1]
            if Path(name).suffix.lower() not in FONT_EXTS:
                continue
            try:
                W.asset_file(page_id, name)
            except KeyError:
                continue
            picks.append((abs((lo + hi) / 2 - weight), name, lo, hi))
            break
    if not picks:
        return {"ok": False, "reason": f"файла шрифта «{family}» (TTF/OTF/WOFF2) в копии нет — оставляю картинкой"}
    picks.sort()
    _d, name, lo, hi = picks[0]
    return {"ok": True, "family": family, "source": "page", "file": name, "weight": weight,
            "face_weight": lo if lo == hi else None, "italic": want_italic}


def view(dec: dict, library: list[dict] | None = None) -> dict:
    """Разбор для API и агента: ссылки на кадры, имена, «можно текстом»."""
    page_id, n, key = dec["page_id"], dec["version"], dec["key"]
    base = f"/api/web-pages/{page_id}/versions/{n}/layers/{key}"
    if library is None:
        from app.motion_assets import load_fonts

        library = load_fonts()
    blocks = []
    for blk in dec["blocks"]:
        font = text_font(page_id, blk, dec.get("font_faces") or [], library) if blk["kind"] in TEXT_KINDS else None
        blocks.append({
            "id": blk["id"], "kind": blk["kind"], "name": blk["name"], "parent": f"b{blk['parent']}" if blk["parent"] >= 0 else None,
            "text": blk.get("text") or "", "box": blk["box"], "selector": blk.get("sel"),
            "url": f"{base}/{blk['file']}", "can_text": bool(font and font.get("ok")),
            **({"text_note": font.get("reason")} if font and not font.get("ok") and blk.get("text_style") else {}),
        })
    return {
        "page_id": page_id, "version": n, "key": key, "region": dec["region"], "page_width": dec.get("page_width"),
        "dpr": dec.get("dpr"), "plate_url": f"{base}/plate.png", "full_url": f"{base}/full.png", "blocks": blocks,
    }
