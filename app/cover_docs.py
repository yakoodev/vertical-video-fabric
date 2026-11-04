"""Документ обложки: превью, собранное руками (слои на холсте), и ссылка на редактирование.

В отличие от вариантов (app/cover_variants.py), где сервис сам кладёт хук на сильный кадр, здесь
человек или агент раскладывает слои: кадр из ролика или исходника, загруженная картинка, PNG аватара,
текст, фигуры. Без ИИ. Рисует один код на холсте редактора и на сервере
(frontend/src/motion/coverdoc.ts, компоновщик ``--cover-doc``).

Файлы документа — только в его папке ``data/cover_docs/<id>``, с именами от сервиса. Слой ссылается
на файл по имени, поэтому путей снаружи нет. Ссылка /c/<token> даёт доступ к одному документу: токен
хранится только хэшем, у него есть право (edit/view), срок и отзыв. См. app/cover_docs_api.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import cover_variants as V
from app.settings import settings

_log = logging.getLogger(__name__)

SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS cover_docs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        clip_id INTEGER REFERENCES clips(id) ON DELETE SET NULL,
        title TEXT NOT NULL DEFAULT '',
        format TEXT NOT NULL DEFAULT '9:16',
        doc TEXT NOT NULL,
        rev INTEGER NOT NULL DEFAULT 1,
        rendered_rev INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cover_docs_clip ON cover_docs(clip_id, id)",
    """
    CREATE TABLE IF NOT EXISTS cover_doc_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id INTEGER NOT NULL REFERENCES cover_docs(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        perm TEXT NOT NULL DEFAULT 'edit',
        expires_at TEXT NOT NULL,
        revoked_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cover_doc_links_doc ON cover_doc_links(doc_id, id)",
]


def ensure_cover_docs_schema(conn) -> None:
    for sql in SCHEMA_SQL:
        conn.execute(sql)


# --------------------------------------------------------------------------- #
# Проверка документа
# --------------------------------------------------------------------------- #

MAX_LAYERS = 60
MAX_TEXT = 400
MAX_NAME = 60
MAX_TITLE = 120
MAX_DOC_BYTES = 256 * 1024
MAX_SIDE_PX = 8000
ASSET_RE = re.compile(r"^[0-9a-f]{32}\.(?:png|jpg)$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$")
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _num(value: Any, default: float, low: float, high: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(num):
        return default
    return round(min(high, max(low, num)), 3)


def _color(value: Any, default: str) -> str:
    return value if isinstance(value, str) and COLOR_RE.match(value) else default


def _choice(value: Any, options: tuple[str, ...]) -> str:
    return value if value in options else options[0]


def _text(value: Any, limit: int) -> str:
    # Только данные: управляющие символы (кроме переноса) вон, длина ограничена.
    raw = value if isinstance(value, str) else ""
    return "".join(ch for ch in raw if ch == "\n" or ch >= " ")[:limit]


def _stroke(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"color": _color(raw.get("color"), "#000000"), "width": _num(raw.get("width"), 4, 0, 120)}


def _shadow(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"color": _color(raw.get("color"), "#000000b3"), "blur": _num(raw.get("blur"), 24, 0, 300),
            "x": _num(raw.get("x"), 0, -300, 300), "y": _num(raw.get("y"), 8, -300, 300)}


def _glow(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"color": _color(raw.get("color"), "#ffe600"), "blur": _num(raw.get("blur"), 40, 0, 300)}


def _gradient(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    return {"from": _color(raw.get("from"), "#000000"), "to": _color(raw.get("to"), "#ffffff"),
            "angle": _num(raw.get("angle"), 90, -360, 360)}


def _crop(raw: Any) -> dict | None:
    if not isinstance(raw, dict):
        return None
    x, y = _num(raw.get("x"), 0, 0, 0.99), _num(raw.get("y"), 0, 0, 0.99)
    return {"x": x, "y": y, "w": _num(raw.get("w"), 1, 0.01, 1 - x), "h": _num(raw.get("h"), 1, 0.01, 1 - y)}


def _font(value: Any) -> str:
    from app.motion_assets import load_fonts

    family = value if isinstance(value, str) else ""
    return family if family in {str(f.get("family") or "") for f in load_fonts()} else ""


def _layer(raw: Any, width: int, height: int, assets: set[str], seen: set[str]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in ("image", "text", "shape"):
        return None
    layer_id = raw.get("id")
    if not isinstance(layer_id, str) or not ID_RE.match(layer_id) or layer_id in seen:
        layer_id = f"l{uuid4().hex[:10]}"
    seen.add(layer_id)
    limit = max(width, height) * 6
    base = {
        "id": layer_id, "type": kind, "name": _text(raw.get("name"), MAX_NAME),
        "x": _num(raw.get("x"), 0, -limit, limit), "y": _num(raw.get("y"), 0, -limit, limit),
        "w": _num(raw.get("w"), width / 2, 1, MAX_SIDE_PX), "h": _num(raw.get("h"), height / 4, 1, MAX_SIDE_PX),
        "rotation": _num(raw.get("rotation"), 0, -3600, 3600), "opacity": _num(raw.get("opacity"), 1, 0, 1),
        "locked": raw.get("locked") is True, "hidden": raw.get("hidden") is True,
    }
    if kind == "image":
        src = raw.get("src")
        return {**base, "src": src if isinstance(src, str) and src in assets else "",
                "fit": _choice(raw.get("fit"), ("cover", "contain")), "crop": _crop(raw.get("crop")),
                "mask": _choice(raw.get("mask"), ("none", "round", "ellipse")),
                "radius": _num(raw.get("radius"), 0, 0, 2000), "flipX": raw.get("flipX") is True,
                "stroke": _stroke(raw.get("stroke")), "shadow": _shadow(raw.get("shadow"))}
    if kind == "shape":
        fill = raw.get("fill")
        return {**base, "shape": _choice(raw.get("shape"), ("rect", "ellipse")),
                "radius": _num(raw.get("radius"), 0, 0, 2000),
                "fill": fill if isinstance(fill, str) and COLOR_RE.match(fill) else None,
                "gradient": _gradient(raw.get("gradient")), "stroke": _stroke(raw.get("stroke")),
                "shadow": _shadow(raw.get("shadow"))}
    plate = raw.get("plate")
    return {**base, "text": _text(raw.get("text"), MAX_TEXT), "font": _font(raw.get("font")),
            "weight": int(_num(raw.get("weight"), 800, 100, 900)), "italic": raw.get("italic") is True,
            "size": _num(raw.get("size"), 96, 8, 800), "color": _color(raw.get("color"), "#ffffff"),
            "gradient": _gradient(raw.get("gradient")), "stroke": _stroke(raw.get("stroke")),
            "shadow": _shadow(raw.get("shadow")), "glow": _glow(raw.get("glow")),
            "plate": {"color": _color(plate.get("color"), "#000000"), "padX": _num(plate.get("padX"), 24, 0, 300),
                      "padY": _num(plate.get("padY"), 8, 0, 300), "radius": _num(plate.get("radius"), 12, 0, 300),
                      "perLine": plate.get("perLine") is True} if isinstance(plate, dict) else None,
            "align": _choice(raw.get("align"), ("center", "left", "right")),
            "valign": _choice(raw.get("valign"), ("middle", "top", "bottom")),
            "lineHeight": _num(raw.get("lineHeight"), 1.1, 0.6, 3), "letterSpacing": _num(raw.get("letterSpacing"), 0, -40, 200),
            "uppercase": raw.get("uppercase") is True}


def sanitize_doc(raw: Any, width: int, height: int, assets: set[str]) -> dict:
    """Документ из чужих рук → только известные поля в разумных границах. Файл слоя — только из
    ``assets``, размер кадра задаёт сервер, иначе ссылка могла бы заказать гигантский рендер."""
    if not isinstance(raw, dict) or not isinstance(raw.get("layers"), list):
        raise ValueError("документ обложки: нужен объект со списком layers")
    if len(raw["layers"]) > MAX_LAYERS:
        raise ValueError(f"слоёв больше {MAX_LAYERS}")
    seen: set[str] = set()
    layers = [layer for layer in (_layer(item, width, height, assets, seen) for item in raw["layers"]) if layer]
    return {"version": 1, "width": width, "height": height, "bg": _color(raw.get("bg"), "#101018"), "layers": layers}


def blank_doc(width: int, height: int) -> dict:
    return {"version": 1, "width": width, "height": height, "bg": "#101018", "layers": []}


# --------------------------------------------------------------------------- #
# Шаблоны: раскладки для стрим-вертикалок, чистые данные
# --------------------------------------------------------------------------- #

BLACK_STROKE = {"color": "#000000", "width": 14}
SOFT_SHADOW = {"color": "#000000b3", "blur": 36, "x": 0, "y": 14}
BOTTOM_FADE = {"from": "#00000000", "to": "#000000e6", "angle": 90}


def _hook(y: int, h: int = 420, **over: Any) -> dict:
    return {"type": "text", "name": "Главный текст", "slot": "hook", "x": 60, "y": y, "w": 960, "h": h, "size": 124,
            "weight": 900, "color": "#ffe600", "stroke": BLACK_STROKE, "shadow": SOFT_SHADOW, "lineHeight": 1.04,
            "uppercase": True, **over}


TEMPLATES: list[dict] = [
    {"id": "avatar-bottom", "name": "Аватар снизу", "note": "Кадр на весь экран, аватар крупно, текст сверху", "layers": [
        {"type": "image", "name": "Кадр", "slot": "frame", "x": 0, "y": 0, "w": 1080, "h": 1920},
        {"type": "shape", "name": "Затемнение", "x": 0, "y": 960, "w": 1080, "h": 960, "gradient": BOTTOM_FADE},
        {"type": "image", "name": "Аватар", "slot": "avatar", "x": 150, "y": 800, "w": 930, "h": 1120, "fit": "contain",
         "shadow": SOFT_SHADOW},
        _hook(210, rotation=-2),
    ]},
    {"id": "split", "name": "Игра и реакция", "note": "Сверху игра, снизу аватар, текст на стыке", "layers": [
        {"type": "image", "name": "Кадр игры", "slot": "frame", "x": 0, "y": 0, "w": 1080, "h": 980},
        {"type": "shape", "name": "Фон снизу", "x": 0, "y": 980, "w": 1080, "h": 940,
         "gradient": {"from": "#2a1650", "to": "#0d0a18", "angle": 90}},
        {"type": "image", "name": "Аватар", "slot": "avatar", "x": 90, "y": 1000, "w": 900, "h": 920, "fit": "contain"},
        _hook(800, 300, color="#000000", stroke=None, shadow=None, size=108, rotation=-2,
              plate={"color": "#ffe600", "padX": 34, "padY": 10, "radius": 14, "perLine": True}),
    ]},
    {"id": "card", "name": "Вырезка", "note": "Кадр игры карточкой на цветном фоне", "layers": [
        {"type": "shape", "name": "Фон", "x": 0, "y": 0, "w": 1080, "h": 1920,
         "gradient": {"from": "#7b2cff", "to": "#12081f", "angle": 70}},
        {"type": "image", "name": "Кадр игры", "slot": "frame", "x": 70, "y": 560, "w": 940, "h": 640, "mask": "round",
         "radius": 36, "rotation": 2, "stroke": {"color": "#ffffff", "width": 12}, "shadow": SOFT_SHADOW},
        {"type": "image", "name": "Аватар", "slot": "avatar", "x": 300, "y": 900, "w": 780, "h": 1020, "fit": "contain",
         "shadow": SOFT_SHADOW},
        _hook(190, 340, color="#ffffff", stroke=None, shadow=None, size=112, rotation=-1,
              plate={"color": "#000000", "padX": 30, "padY": 12, "radius": 12, "perLine": True}),
    ]},
    {"id": "portrait", "name": "Крупная эмоция", "note": "Аватар на весь экран, игра в круге", "layers": [
        {"type": "shape", "name": "Фон", "x": 0, "y": 0, "w": 1080, "h": 1920,
         "gradient": {"from": "#ff2e63", "to": "#2b0a3d", "angle": 80}},
        {"type": "image", "name": "Аватар", "slot": "avatar", "x": 0, "y": 300, "w": 1080, "h": 1300, "fit": "contain",
         "shadow": SOFT_SHADOW},
        {"type": "image", "name": "Кадр игры", "slot": "frame", "x": 620, "y": 210, "w": 400, "h": 400, "mask": "ellipse",
         "stroke": {"color": "#ffffff", "width": 12}, "shadow": SOFT_SHADOW},
        _hook(1160, 370),
    ]},
    {"id": "stickers", "name": "Наклейки", "note": "Кадр на весь экран, две реплики наклейками", "layers": [
        {"type": "image", "name": "Кадр", "slot": "frame", "x": 0, "y": 0, "w": 1080, "h": 1920},
        _hook(250, 360, color="#000000", stroke=None, shadow=SOFT_SHADOW, size=112, rotation=-4,
              plate={"color": "#ffffff", "padX": 30, "padY": 12, "radius": 10, "perLine": True}),
        {"type": "text", "name": "Вторая реплика", "text": "А дальше?", "x": 240, "y": 1270, "w": 780, "h": 180, "size": 92,
         "weight": 900, "color": "#ffffff", "rotation": 3, "uppercase": True, "shadow": SOFT_SHADOW,
         "plate": {"color": "#ff2e63", "padX": 28, "padY": 10, "radius": 10, "perLine": True}},
    ]},
    {"id": "clean", "name": "Просто кадр", "note": "Кадр, затемнение снизу, белый текст", "layers": [
        {"type": "image", "name": "Кадр", "slot": "frame", "x": 0, "y": 0, "w": 1080, "h": 1920},
        {"type": "shape", "name": "Затемнение", "x": 0, "y": 900, "w": 1080, "h": 1020, "gradient": BOTTOM_FADE},
        _hook(1080, 440, color="#ffffff", stroke=None, valign="bottom"),
    ]},
]


def template_briefs(width: int = 1080, height: int = 1920) -> list[dict]:
    """Шаблоны с пустыми картинками: редактор рисует по ним миниатюры."""
    return [{"id": t["id"], "name": t["name"], "note": t["note"],
             "layers": template_layers(t["id"], width, height, set(), text="Текст обложки")} for t in TEMPLATES]


def template_layers(template_id: str, width: int, height: int, assets: set[str], *, frame: str = "", avatar: str = "",
                    text: str = "", font: str = "") -> list[dict]:
    """Слои шаблона под кадр документа: координаты заданы на 1080×1920, другой формат — в тех же долях."""
    template = next((t for t in TEMPLATES if t["id"] == template_id), None)
    if template is None:
        raise ValueError(f"нет шаблона «{template_id}»: {', '.join(t['id'] for t in TEMPLATES)}")
    sx, sy = width / 1080, height / 1920
    scale = min(sx, sy)
    out = []
    for raw in template["layers"]:
        item = {k: v for k, v in raw.items() if k != "slot"}
        item.update(x=raw["x"] * sx, y=raw["y"] * sy, w=raw["w"] * sx, h=raw["h"] * sy,
                    id=f"l{uuid4().hex[:10]}")
        slot = raw.get("slot")
        if slot == "frame":
            item["src"] = frame
        elif slot == "avatar":
            item["src"] = avatar
        elif slot == "hook":
            item["text"] = text or "Текст обложки"
        if raw["type"] == "text":
            item["size"] = raw["size"] * scale
            if font:
                item["font"] = font
        if isinstance(item.get("radius"), (int, float)):
            item["radius"] = item["radius"] * scale
        out.append(item)
    return sanitize_doc({"layers": out}, width, height, assets)["layers"]


# --------------------------------------------------------------------------- #
# Хранение
# --------------------------------------------------------------------------- #


def _row(row: Any) -> dict:
    return {"id": int(row["id"]), "clip_id": row["clip_id"], "title": row["title"], "format": row["format"],
            "doc": json.loads(row["doc"]), "rev": int(row["rev"]), "rendered_rev": int(row["rendered_rev"]),
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


def get_doc(store: Any, doc_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM cover_docs WHERE id = ?", (int(doc_id),))
    if row is None:
        raise KeyError(f"cover doc not found: {doc_id}")
    return _row(row)


def list_docs(store: Any, clip_id: int | None = None) -> list[dict]:
    if clip_id is None:
        rows = store.db.query_all("SELECT * FROM cover_docs ORDER BY id DESC LIMIT 200", ())
    else:
        rows = store.db.query_all("SELECT * FROM cover_docs WHERE clip_id = ? ORDER BY id DESC", (int(clip_id),))
    return [_row(row) for row in rows]


def doc_dir(doc_id: int) -> Path:
    return settings.data_dir / "cover_docs" / str(int(doc_id))


def doc_assets(doc_id: int) -> set[str]:
    folder = doc_dir(doc_id)
    if not folder.is_dir():
        return set()
    return {p.name for p in folder.iterdir() if p.is_file() and ASSET_RE.match(p.name)}


def asset_path(doc_id: int, name: str) -> Path | None:
    """Файл документа по имени: только формат имён сервиса и только эта папка."""
    if not isinstance(name, str) or not ASSET_RE.match(name):
        return None
    path = doc_dir(doc_id) / name
    return path if path.is_file() else None


def create_doc(store: Any, clip_id: int | None = None, title: str = "", fmt: str | None = None,
               template: str | None = None, text: str = "") -> dict:
    """Новый документ. У клипа формат и заголовок — его; ``template`` — сразу разложить шаблон."""
    clip = store.get_clip(int(clip_id)) if clip_id else None
    if clip:
        from app.publish_format import clip_format

        fmt = clip_format(store, clip) or fmt or "9:16"
    fmt = fmt if fmt in V.COVER_SIZES else "9:16"
    width, height = V.COVER_SIZES[fmt]
    name = _text(title, MAX_TITLE) or (str(clip.get("title") or "") if clip else "") or "Обложка"
    cur = store.db.execute(
        "INSERT INTO cover_docs (clip_id, title, format, doc) VALUES (?, ?, ?, ?)",
        (int(clip_id) if clip_id else None, name[:MAX_TITLE], fmt, json.dumps(blank_doc(width, height))),
    )
    doc_id = int(cur.lastrowid)
    doc_dir(doc_id).mkdir(parents=True, exist_ok=True)
    if template:
        layers = apply_template(store, doc_id, template, text=text)
        save_doc(store, doc_id, {"bg": "#101018", "layers": layers})
    return get_doc(store, doc_id)


def save_doc(store: Any, doc_id: int, raw: Any, base_rev: int | None = None, title: str | None = None) -> dict:
    current = get_doc(store, doc_id)
    if base_rev is not None and int(base_rev) != current["rev"]:
        raise RevisionConflict(current)
    clean = sanitize_doc(raw, current["doc"]["width"], current["doc"]["height"], doc_assets(doc_id))
    text = json.dumps(clean, ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) > MAX_DOC_BYTES:
        raise ValueError("документ обложки слишком большой")
    name = current["title"] if title is None else (_text(title, MAX_TITLE) or current["title"])
    store.db.execute(
        "UPDATE cover_docs SET doc = ?, title = ?, rev = rev + 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (text, name, int(doc_id)),
    )
    return get_doc(store, doc_id)


class RevisionConflict(Exception):
    """Документ уже изменили с другой вкладки или по ссылке: ``current`` — свежая версия."""

    def __init__(self, current: dict) -> None:
        super().__init__("документ изменён в другом окне")
        self.current = current


def delete_doc(store: Any, doc_id: int) -> None:
    store.db.execute("DELETE FROM cover_docs WHERE id = ?", (int(doc_id),))
    shutil.rmtree(doc_dir(doc_id), ignore_errors=True)


# --------------------------------------------------------------------------- #
# Файлы: загрузка, кадры, аватары клипа
# --------------------------------------------------------------------------- #

MAX_UPLOAD_BYTES = 12 * 1024 * 1024
MAX_ASSETS = 80
MAX_INPUT_SIDE = 12000
MAX_STORED_SIDE = 4096
THUMB_WIDTH = 360
MAX_THUMBS = 200
ALPHA_PIX_FMTS = ("rgba", "bgra", "argb", "abgr", "ya", "pal", "yuva", "gbrap")
_FORMAT_BY_MAGIC = ((b"\x89PNG\r\n\x1a\n", "png_pipe"), (b"\xff\xd8\xff", "jpeg_pipe"))


def _image_demuxer(data: bytes) -> str | None:
    """Тип определяем только по содержимому; SVG, HTML и всё остальное не картинка."""
    for magic, demuxer in _FORMAT_BY_MAGIC:
        if data.startswith(magic):
            return demuxer
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp_pipe"
    return None


def _check_room(doc_id: int) -> None:
    if len(doc_assets(doc_id)) >= MAX_ASSETS:
        raise ValueError(f"в документе уже {MAX_ASSETS} файлов")


def store_upload(doc_id: int, data: bytes) -> dict:
    """Картинка от пользователя → перекодированный PNG/JPG: метаданные и всё чужое из файла уходит."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError(f"файл больше {MAX_UPLOAD_BYTES // (1024 * 1024)} МБ")
    demuxer = _image_demuxer(data)
    if not demuxer:
        raise ValueError("это не картинка: подходят PNG, JPG и WebP")
    _check_room(doc_id)
    folder = doc_dir(doc_id)
    folder.mkdir(parents=True, exist_ok=True)
    raw = settings.tmp_dir / f"cover-doc-{uuid4().hex}.bin"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(data)
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-f", demuxer, "-select_streams", "v:0", "-show_entries",
             "stream=width,height,pix_fmt", "-of", "json", str(raw)],
            capture_output=True, text=True, timeout=30, check=False,
        )
        streams = (json.loads(probe.stdout or "{}").get("streams") or [{}]) if probe.returncode == 0 else [{}]
        info = streams[0]
        width, height = int(info.get("width") or 0), int(info.get("height") or 0)
        if not width or not height:
            raise ValueError("это не картинка: файл не читается")
        if max(width, height) > MAX_INPUT_SIDE:
            raise ValueError(f"картинка больше {MAX_INPUT_SIDE} px по стороне")
        ext = "png" if str(info.get("pix_fmt") or "").startswith(ALPHA_PIX_FMTS) else "jpg"
        name = f"{uuid4().hex}.{ext}"
        out = folder / name
        size = f"w='min({MAX_STORED_SIDE},iw)':h='min({MAX_STORED_SIDE},ih)':force_original_aspect_ratio=decrease"
        args = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", demuxer, "-i", str(raw),
                "-vf", f"scale={size}", "-frames:v", "1", "-map_metadata", "-1"]
        args += ["-q:v", "2"] if ext == "jpg" else []
        proc = subprocess.run(args + [str(out)], capture_output=True, text=True, timeout=60, check=False)
        if proc.returncode != 0 or not out.is_file():
            out.unlink(missing_ok=True)
            raise ValueError("это не картинка: файл не читается")
    finally:
        raw.unlink(missing_ok=True)
    return describe_asset(doc_id, name)


def describe_asset(doc_id: int, name: str) -> dict:
    from app.publish_format import image_size

    # Размер только для рамки; странный файл — 0, редактор узнает размер сам при загрузке.
    width, height = image_size(doc_dir(doc_id) / name) or (0, 0)
    return {"name": name, "width": width, "height": height}


def _video_of(store: Any, doc: dict) -> tuple[dict, Path]:
    if not doc.get("clip_id"):
        raise ValueError("документ не привязан к клипу: кадры брать неоткуда")
    clip = store.get_clip(int(doc["clip_id"]))
    video = Path(clip.get("output_path") or "")
    if clip.get("status") != "succeeded" or not video.is_file():
        raise ValueError("файла готового клипа нет")
    return clip, video


def _source_moment(store: Any, clip: dict, t: float) -> tuple[Path, float]:
    """Тот же момент ролика в исходнике: без раскладки и слоёв клипа."""
    from app.agent_tools import plan_clock
    from app.cuts import seg_length

    plan_id = clip.get("clip_plan_id")
    if not plan_id:
        raise ValueError("у этого клипа нет плана: момент в исходнике неизвестен")
    plan = store.get_clip_plan(int(plan_id))
    at = plan_clock(store, plan).final_to_clip(t)
    source = store.get_source(int(plan["source_id"]))
    path = Path(source.get("local_path") or "")
    if not path.is_file():
        raise ValueError("файла исходника уже нет: возьмите кадр из готового ролика")
    segments = plan.get("segments") or []
    for seg in segments:
        length = seg_length(seg)
        if at <= length:
            return path, float(seg["start_sec"]) + max(0.0, at)
        at -= length
    return path, float(segments[-1]["end_sec"]) if segments else 0.0


def add_frame(store: Any, doc_id: int, t: float, origin: str = "clean") -> dict:
    """Кадр в файлы документа. ``clean`` — вид клипа без текста и субтитров (если есть компоновщик),
    ``render`` — кадр готового файла как есть, ``source`` — тот же момент в исходнике."""
    doc = get_doc(store, doc_id)
    clip, video = _video_of(store, doc)
    _check_room(doc_id)
    duration = float(clip.get("duration_sec") or 0)
    moment = _num(t, 0, 0, max(0.0, duration - 0.05) if duration else 36000)
    folder = doc_dir(doc_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid4().hex}.jpg"
    out = folder / name
    work = settings.tmp_dir / f"cover-doc-frame-{uuid4().hex}.png"
    work.parent.mkdir(parents=True, exist_ok=True)
    try:
        if origin == "source":
            path, at = _source_moment(store, clip, moment)
            V._run(V._frame_args(path, at, work))
        elif origin == "render":
            V._run(V._frame_args(video, moment, work))
        else:
            V._base_frame(store, clip, video, moment, work)
        V._run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(work), "-frames:v", "1", "-q:v", "2",
                str(out)])
    finally:
        work.unlink(missing_ok=True)
    return {**describe_asset(doc_id, name), "t": moment, "origin": origin}


def thumb_path(store: Any, doc_id: int, t: float) -> Path:
    """Маленький кадр для ползунка: готовый файл, полсекунды точности, кэш в папке документа."""
    doc = get_doc(store, doc_id)
    clip, video = _video_of(store, doc)
    duration = float(clip.get("duration_sec") or 0)
    moment = _num(t, 0, 0, max(0.0, duration - 0.05) if duration else 36000)
    step = int(moment * 2)
    folder = doc_dir(doc_id) / "thumbs"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{step}.jpg"
    if not out.is_file():
        saved = sorted(folder.iterdir(), key=lambda p: p.stat().st_mtime)
        for old in saved[:max(0, len(saved) - MAX_THUMBS)]:
            old.unlink(missing_ok=True)
        V._run(V._frame_args(video, step / 2, out, THUMB_WIDTH))
    return out


def best_moment(clip: dict, video: Path) -> float:
    """Сильный кадр без ИИ: те же метрики, что у вариантов обложки (лицо, резкость, цвет)."""
    duration = float(clip.get("duration_sec") or 0)
    try:
        scan = V.small_size(int(clip.get("width") or 1080), int(clip.get("height") or 1920))
        candidates = []
        for t in V.sample_times(duration):
            img = V.read_small(video, scan, t)
            if img is not None:
                candidates.append({"t": t, **V.frame_metrics(img)})
        return float(V.pick_moments(candidates, 1, duration)[0]["t"])
    except Exception as exc:  # noqa: BLE001 - без оценки — середина ролика
        _log.info("cover doc best moment failed: %s", str(exc)[:200])
        return duration / 2


def _clip_picture_refs(store: Any, clip: dict) -> list[tuple[str, Any]]:
    """Картинки самого клипа: аватар (PNGtuber), его картинки-слои, логотип бренда."""
    refs: list[tuple[str, Any]] = []
    plan_id = clip.get("clip_plan_id")
    if plan_id:
        render = store.get_clip_plan(int(plan_id)).get("render_settings") or {}
        for layer in render.get("layers") or []:
            props = layer.get("props") or {}
            if layer.get("type") == "pngtuber":
                states = props.get("states") or {}
                refs += [("Аватар", states.get("idle")), ("Аватар говорит", states.get("talk"))]
            elif layer.get("type") == "image":
                refs.append(("Картинка клипа", props.get("asset_id") or props.get("src")))
    brand = V.clip_brand(store, clip)
    if brand and brand.get("logo_asset_id"):
        refs.append(("Логотип", int(brand["logo_asset_id"])))
    return [(label, ref) for label, ref in refs if ref not in (None, "", 0)]


def library_pictures(store: Any, doc_id: int) -> list[dict]:
    """Копирует картинки клипа в файлы документа: дальше редактор работает только с ними, а по ссылке
    нельзя запросить произвольный файл библиотеки сервиса."""
    doc = get_doc(store, doc_id)
    if not doc.get("clip_id"):
        return []
    try:
        clip = store.get_clip(int(doc["clip_id"]))
    except KeyError:
        return []
    from app.motion_render import _resolve_ref

    folder = doc_dir(doc_id)
    folder.mkdir(parents=True, exist_ok=True)
    out: list[dict] = []
    seen: set[str] = set()
    for label, ref in _clip_picture_refs(store, clip):
        try:
            path = _resolve_ref(store, ref, "pngtuber")
        except Exception:  # noqa: BLE001 - ссылка на удалённый файл просто не попадает в список
            path = None
        if not path or not path.is_file() or path.stat().st_size > MAX_UPLOAD_BYTES:
            continue
        data = path.read_bytes()
        key = hashlib.sha256(data).hexdigest()[:32]
        if key in seen or not _image_demuxer(data):
            continue
        seen.add(key)
        # Имя — хэш содержимого: повторный запрос не плодит копий.
        existing = next((folder / f"{key}.{e}" for e in ("png", "jpg") if (folder / f"{key}.{e}").is_file()), None)
        if existing is None:
            try:
                made = store_upload(doc_id, data)
            except ValueError as exc:  # файл клипа не картинка или папка заполнена — просто не показываем
                _log.info("cover doc picture skipped: %s", str(exc)[:200])
                continue
            existing = folder / f"{key}{Path(made['name']).suffix}"
            (folder / made["name"]).replace(existing)
        out.append({**describe_asset(doc_id, existing.name), "label": label})
    return out


def apply_template(store: Any, doc_id: int, template_id: str, text: str = "") -> list[dict]:
    """Слои шаблона для этого документа: первый кадр ролика, аватар клипа, текст из заголовка."""
    doc = get_doc(store, doc_id)
    width, height = doc["doc"]["width"], doc["doc"]["height"]
    frame = next((layer["src"] for layer in doc["doc"]["layers"] if layer["type"] == "image" and layer["src"]), "")
    font = ""
    hook = text
    if doc.get("clip_id"):
        clip, video = _video_of(store, doc)
        if not frame:
            frame = add_frame(store, doc_id, best_moment(clip, video))["name"]
        brand = V.clip_brand(store, clip)
        font = str((brand or {}).get("font") or "")
        hook = hook or V.clean_hook(doc["title"] or clip.get("title"))
    pictures = library_pictures(store, doc_id)
    avatar = pictures[0]["name"] if pictures else ""
    return template_layers(template_id, width, height, doc_assets(doc_id), frame=frame, avatar=avatar, text=hook, font=font)


def empty_slots(doc: dict) -> list[str]:
    return [layer["name"] or "картинка" for layer in doc["layers"] if layer["type"] == "image" and not layer["src"]
            and not layer["hidden"]]


# --------------------------------------------------------------------------- #
# Экспорт
# --------------------------------------------------------------------------- #

_render_lock = threading.Lock()


def render_doc(store: Any, doc_id: int) -> Path:
    """PNG документа в полном размере, тем же кодом, что холст. Свежий файл не перерисовывается."""
    from app.heavy_queue import frame_slot
    from app.motion_render import compositor_command
    from app.user_fonts import registry_path

    doc = get_doc(store, doc_id)
    folder = doc_dir(doc_id)
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"out-{doc['rev']}.png"
    if doc["rendered_rev"] == doc["rev"] and out.is_file():
        return out
    job = folder / f"job-{uuid4().hex[:8]}.json"
    job.write_text(json.dumps({"doc": doc["doc"], "assets_dir": str(folder)}, ensure_ascii=False), encoding="utf-8")
    args = [*compositor_command(), "--cover-doc", str(job), "--out", str(out),
            "--fonts-dir", str(settings.motion_fonts_dir)]
    if registry_path().is_file():
        args += ["--user-fonts-dir", str(registry_path().parent)]
    try:
        with frame_slot(f"обложка {doc_id}"), _render_lock:
            proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
                                  check=False)
        if proc.returncode != 0 or not out.is_file():
            raise RuntimeError((proc.stderr or proc.stdout or "компоновщик упал").strip()[-300:])
    finally:
        job.unlink(missing_ok=True)
    for old in folder.glob("out-*.png"):
        if old != out:
            old.unlink(missing_ok=True)
    store.db.execute("UPDATE cover_docs SET rendered_rev = ? WHERE id = ?", (doc["rev"], int(doc_id)))
    return out


def export_jpeg(store: Any, doc_id: int) -> Path:
    png = render_doc(store, doc_id)
    jpg = png.with_suffix(".jpeg")
    if not jpg.is_file():
        for old in png.parent.glob("out-*.jpeg"):
            old.unlink(missing_ok=True)
        V._run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(png), "-frames:v", "1", "-q:v", "2", str(jpg)])
    return jpg


def apply_to_clip(store: Any, doc_id: int, burn: bool = True) -> dict:
    """Собранное превью → обложка клипа: та же логика, что у вариантов (app/cover_variants.choose_cover)."""
    doc = get_doc(store, doc_id)
    if not doc.get("clip_id"):
        raise ValueError("документ не привязан к клипу")
    clip_id = int(doc["clip_id"])
    jpg = export_jpeg(store, doc_id)
    out_dir = settings.clip_dir / "covers"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{clip_id}-{uuid4().hex[:12]}.jpg"
    shutil.copyfile(jpg, target)
    text = next((layer["text"] for layer in doc["doc"]["layers"] if layer["type"] == "text" and layer["text"]), "")
    previous = store.db.query_all(
        "SELECT id, path FROM clip_covers WHERE clip_id = ? AND renderer = 'cover_doc'", (clip_id,)
    )
    cur = store.db.execute(
        "INSERT INTO clip_covers (clip_id, path, width, height, format, t, text, style, text_y, faces, score, renderer) "
        "VALUES (?, ?, ?, ?, ?, 0, ?, 'manual', 0, 0, 0, 'cover_doc')",
        (clip_id, str(target), doc["doc"]["width"], doc["doc"]["height"], doc["format"], text[:MAX_TEXT]),
    )
    result = V.choose_cover(store, int(cur.lastrowid), apply_to_render=bool(burn), clip_id=clip_id)
    for row in previous:
        store.db.execute("DELETE FROM clip_covers WHERE id = ?", (int(row["id"]),))
        Path(row["path"]).unlink(missing_ok=True)
    return result


# --------------------------------------------------------------------------- #
# Ссылка на редактирование
# --------------------------------------------------------------------------- #

PERMS = ("edit", "view")
DEFAULT_LINK_DAYS = 30
MAX_LINK_DAYS = 365
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _link_brief(row: Any) -> dict:
    return {"id": int(row["id"]), "perm": row["perm"], "expires_at": row["expires_at"], "revoked_at": row["revoked_at"],
            "created_at": row["created_at"]}


def link_url(store: Any, token: str) -> tuple[str, str | None]:
    from app import notify_tg

    base = str(notify_tg.config(store).get("public_url") or "").rstrip("/")
    if base:
        return f"{base}/c/{token}", None
    return f"/c/{token}", ("Публичный адрес сервиса не задан (настройка public_url): ссылка откроется только там, "
                           "откуда виден этот сервис.")


def create_link(store: Any, doc_id: int, perm: str = "edit", days: Any = DEFAULT_LINK_DAYS) -> dict:
    get_doc(store, doc_id)
    if perm not in PERMS:
        raise ValueError("право ссылки: edit или view")
    span = int(_num(days, DEFAULT_LINK_DAYS, 1, MAX_LINK_DAYS))
    token = secrets.token_urlsafe(24)  # 192 бита; наружу только один раз, хранится хэш
    expires = (_now() + timedelta(days=span)).isoformat(timespec="seconds")
    cur = store.db.execute(
        "INSERT INTO cover_doc_links (doc_id, token_hash, perm, expires_at) VALUES (?, ?, ?, ?)",
        (int(doc_id), token_hash(token), perm, expires),
    )
    url, warning = link_url(store, token)
    out = {"id": int(cur.lastrowid), "url": url, "perm": perm, "expires_at": expires}
    return {**out, "warning": warning} if warning else out


def list_links(store: Any, doc_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT * FROM cover_doc_links WHERE doc_id = ? ORDER BY id DESC", (int(doc_id),))
    return [_link_brief(row) for row in rows]


def revoke_link(store: Any, doc_id: int, link_id: int) -> None:
    cur = store.db.execute(
        "UPDATE cover_doc_links SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ? AND doc_id = ?",
        (_now().isoformat(timespec="seconds"), int(link_id), int(doc_id)),
    )
    if cur.rowcount == 0:
        raise KeyError(f"link not found: {link_id}")


def resolve_link(store: Any, token: str) -> dict | None:
    """Действующая ссылка по токену или None: чужой, отозванный и просроченный не различаются."""
    if not isinstance(token, str) or not TOKEN_RE.match(token):
        return None
    row = store.db.query_one("SELECT * FROM cover_doc_links WHERE token_hash = ?", (token_hash(token),))
    if row is None or row["revoked_at"]:
        return None
    if datetime.fromisoformat(row["expires_at"]) <= _now():
        return None
    return {"id": int(row["id"]), "doc_id": int(row["doc_id"]), "perm": row["perm"], "key": row["token_hash"][:16]}


# --------------------------------------------------------------------------- #
# Частота запросов по ссылке
# --------------------------------------------------------------------------- #

LIMITS = {"read": 600, "save": 240, "thumb": 240, "frame": 30, "upload": 30, "export": 30, "bad_token": 30}
WINDOW_SEC = 60.0


class RateLimiter:
    """Скользящее окно в памяти процесса: одна копия сервиса, после перезапуска счёт с нуля."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def allow(self, key: str, bucket: str, now: float | None = None) -> bool:
        at = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits[(key, bucket)]
            while hits and at - hits[0] > WINDOW_SEC:
                hits.popleft()
            if len(hits) >= LIMITS[bucket]:
                return False
            hits.append(at)
            if len(self._hits) > 10000:
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v and at - v[-1] <= WINDOW_SEC})
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
