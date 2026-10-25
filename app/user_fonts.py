"""Свои шрифты: загрузка TTF/OTF/WOFF2 владельцем (редактор, POST /api/motion/fonts) или
агентом (инструмент ``upload_font``).

Хранение — в данных, не в репозитории: ``data/fonts/`` (``USER_FONTS_DIR``) — файлы и реестр
``fonts.json`` той же схемы, что ``vendor/fonts/fonts.json`` (family, file, weight, style,
cyrillic, label, hint, tags) плюс ``user: true``, ``variable``, ``weight_range``, ``axes``.
app/motion_assets.py сливает оба реестра (список ``/api/motion/fonts``, файлы
``/api/motion/fonts/{file}``), превью подключает их через ``@font-face``, компоновщик —
``GlobalFonts.registerFromPath`` (``--user-fonts-dir``).

Проверка — по самому файлу, без сторонних библиотек: сигнатура sfnt (TrueType ``00010000`` /
``true``, CFF ``OTTO``, ``wOF2``), каталог таблиц, из TTF/OTF — имя семейства (``name``),
толщина (``OS/2``), курсив, оси ``fvar`` и кириллица (``cmap``). У WOFF2 таблицы сжаты Brotli
(в зависимостях его нет) — читается только каталог: семейство и толщина — из аргументов или
имени файла, вариативность — по наличию ``fvar``.

Вариативные шрифты (оси ``wght``…): статические начертания не генерируются — fontTools
(instancer) нет в requirements.txt. Вес выбирается на лету: браузер берёт его из
``font-weight`` (FontFace с диапазоном ``weight_range``), компоновщик ставит
``fontVariationSettings`` (``"wght" N``, ``"opsz"`` по кеглю — как ``font-optical-sizing: auto``
браузера) — node/motion-render.ts. Лицензия шрифта — на совести загрузившего.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import threading
import time
from pathlib import Path
from typing import Any

from app.settings import settings

FONT_EXTS = {".ttf": "ttf", ".otf": "otf", ".woff2": "woff2"}
MAX_FONT_BYTES = 20 * 1024 * 1024
FAMILY_RE = re.compile(r"^[\w][\w .&+'-]{0,63}$", re.UNICODE)
# Толщина по слову в имени начертания/файла (если в файле её не прочитать — WOFF2).
WEIGHT_WORDS = (
    ("extralight", 200), ("ultralight", 200), ("semibold", 600), ("demibold", 600), ("extrabold", 800),
    ("ultrabold", 800), ("hairline", 100), ("thin", 100), ("light", 300), ("regular", 400), ("book", 400),
    ("normal", 400), ("medium", 500), ("bold", 700), ("heavy", 900), ("black", 900),
)
# Кириллица — эти буквы должны быть в cmap (А Я а я Ё ё).
CYRILLIC_PROBE = (0x0410, 0x042F, 0x0430, 0x044F, 0x0401, 0x0451)
# WOFF2: индекс известных таблиц (спецификация W3C, 5.1) — нужен только fvar.
WOFF2_FVAR_INDEX = 47

_lock = threading.Lock()


class FontError(ValueError):
    """Файл не годится в шрифты — понятное сообщение для человека и агента."""


def user_fonts_dir() -> Path:
    """Папка своих шрифтов: ``USER_FONTS_DIR`` или ``data/fonts``."""
    custom = getattr(settings, "user_fonts_dir", None)
    return Path(custom) if custom else Path(settings.data_dir) / "fonts"


def registry_path() -> Path:
    return user_fonts_dir() / "fonts.json"


def load_user_fonts() -> list[dict]:
    """Реестр своих шрифтов (нет — пусто)."""
    try:
        data = json.loads(registry_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f for f in data if isinstance(f, dict) and isinstance(f.get("file"), str)] if isinstance(data, list) else []


def _save_registry(fonts: list[dict]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(fonts, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# Разбор файла
# --------------------------------------------------------------------------- #


def font_kind(data: bytes) -> str:
    """'ttf' | 'otf' | 'woff2' по сигнатуре; другое — FontError."""
    head = data[:4]
    if head in (b"\x00\x01\x00\x00", b"true"):
        return "ttf"
    if head == b"OTTO":
        return "otf"
    if head == b"wOF2":
        return "woff2"
    if head == b"wOFF":
        raise FontError("WOFF (первая версия) не поддерживается — нужен TTF, OTF или WOFF2")
    if head == b"ttcf":
        raise FontError("коллекция шрифтов (TTC) — загрузи начертания отдельными TTF/OTF")
    raise FontError("это не шрифт: нужен TTF, OTF или WOFF2")


def _tables(data: bytes) -> dict[str, bytes]:
    """Таблицы sfnt (TTF/OTF) по тегу."""
    try:
        (num,) = struct.unpack_from(">H", data, 4)
        out = {}
        for i in range(num):
            tag, _sum, offset, length = struct.unpack_from(">4sIII", data, 12 + 16 * i)
            if offset + length > len(data):
                raise FontError("файл шрифта обрезан (таблица за концом файла)")
            out[tag.decode("latin-1")] = data[offset:offset + length]
    except struct.error as exc:
        raise FontError("файл шрифта повреждён (каталог таблиц не читается)") from exc
    for need in ("name", "cmap"):
        if need not in out:
            raise FontError(f"в шрифте нет таблицы {need} — файл повреждён")
    return out


def _names(table: bytes) -> dict[int, str]:
    """Строки таблицы name: Windows (UTF-16BE, английский — первым), иначе Mac Roman."""
    out: dict[int, tuple[int, str]] = {}
    try:
        _fmt, count, start = struct.unpack_from(">HHH", table, 0)
        for i in range(count):
            platform, encoding, lang, name_id, length, offset = struct.unpack_from(">HHHHHH", table, 6 + 12 * i)
            raw = table[start + offset:start + offset + length]
            if platform == 3 and encoding in (0, 1, 10):
                text, rank = raw.decode("utf-16-be", "replace"), 0 if lang == 0x0409 else 1
            elif platform == 1 and encoding == 0:
                text, rank = raw.decode("latin-1", "replace"), 2
            elif platform == 0:
                text, rank = raw.decode("utf-16-be", "replace"), 1
            else:
                continue
            text = text.replace("\x00", "").strip()
            if text and (name_id not in out or rank < out[name_id][0]):
                out[name_id] = (rank, text)
    except struct.error:
        pass
    return {k: v for k, (_r, v) in out.items()}


def _has_cyrillic(cmap: bytes) -> bool:
    """Есть ли в cmap (формат 4 или 12, Unicode) все буквы CYRILLIC_PROBE."""
    ranges: list[tuple[int, int]] = []
    try:
        _ver, num = struct.unpack_from(">HH", cmap, 0)
        for i in range(num):
            platform, encoding, offset = struct.unpack_from(">HHI", cmap, 4 + 8 * i)
            if not (platform == 0 or (platform == 3 and encoding in (1, 10))):
                continue
            (fmt,) = struct.unpack_from(">H", cmap, offset)
            if fmt == 4:
                (seg2,) = struct.unpack_from(">H", cmap, offset + 6)
                ends = struct.unpack_from(f">{seg2 // 2}H", cmap, offset + 14)
                starts = struct.unpack_from(f">{seg2 // 2}H", cmap, offset + 16 + seg2)
                ranges.extend(zip(starts, ends))
            elif fmt == 12:
                (groups,) = struct.unpack_from(">I", cmap, offset + 12)
                for g in range(groups):
                    start, end, _gid = struct.unpack_from(">III", cmap, offset + 16 + 12 * g)
                    ranges.append((start, end))
    except struct.error:
        return False
    return all(any(a <= code <= b for a, b in ranges) for code in CYRILLIC_PROBE)


def _axes(fvar: bytes) -> dict[str, list[float]]:
    """Оси вариативного шрифта: {tag: [min, default, max]}."""
    out: dict[str, list[float]] = {}
    try:
        _maj, _min, offset, _res, count, size = struct.unpack_from(">HHHHHH", fvar, 0)
        for i in range(count):
            tag, lo, default, hi = struct.unpack_from(">4siii", fvar, offset + size * i)
            out[tag.decode("latin-1").strip()] = [round(v / 65536, 2) for v in (lo, default, hi)]
    except struct.error:
        return {}
    return out


def _uint_base128(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for i in range(5):
        byte = data[pos + i]
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, pos + i + 1
    raise FontError("WOFF2 повреждён (каталог таблиц)")


def _woff2_has_fvar(data: bytes) -> bool:
    """Каталог таблиц WOFF2 (не сжат): есть ли fvar."""
    try:
        (num,) = struct.unpack_from(">H", data, 12)
        pos = 48
        found = False
        for _ in range(num):
            flags = data[pos]
            pos += 1
            index = flags & 0x3F
            if index == 63:
                tag = data[pos:pos + 4]
                pos += 4
            else:
                tag = b""
            found = found or index == WOFF2_FVAR_INDEX or tag == b"fvar"
            _length, pos = _uint_base128(data, pos)
            version = flags >> 6
            glyf_loca = index in (10, 11) or tag in (b"glyf", b"loca")
            if (glyf_loca and version == 0) or (not glyf_loca and version != 0):
                _transformed, pos = _uint_base128(data, pos)
        return found
    except (IndexError, struct.error) as exc:
        raise FontError("WOFF2 повреждён (каталог таблиц не читается)") from exc


def _weight_from_words(*texts: str) -> int | None:
    for text in texts:
        number = re.search(r"(?<![\d.])([1-9]00)(?!\d)", str(text or ""))
        if number:
            return int(number.group(1))  # «inter-600-normal»
        low = re.sub(r"[\s_-]", "", str(text or "").lower())
        for word, weight in WEIGHT_WORDS:
            if word in low:
                return weight
    return None


def inspect_font(data: bytes, filename: str = "") -> dict:
    """Что за шрифт: kind, family (None — не прочитать), subfamily, weight, italic, axes,
    cyrillic (None — не проверить: WOFF2)."""
    if len(data) < 64:
        raise FontError("файл слишком мал для шрифта")
    kind = font_kind(data)
    stem = Path(filename).stem
    if kind == "woff2":
        variable = _woff2_has_fvar(data)
        return {"kind": kind, "family": None, "subfamily": None, "weight": _weight_from_words(stem),
                "italic": "italic" in stem.lower(), "axes": {"wght": []} if variable else {}, "cyrillic": None}
    tables = _tables(data)
    names = _names(tables["name"])
    family = names.get(16) or names.get(1)
    subfamily = names.get(17) or names.get(2) or ""
    weight = None
    italic = "italic" in subfamily.lower()
    os2 = tables.get("OS/2")
    if os2 and len(os2) >= 64:
        (weight,) = struct.unpack_from(">H", os2, 4)
        (selection,) = struct.unpack_from(">H", os2, 62)
        italic = italic or bool(selection & 1)
    if not weight or not 1 <= weight <= 1000:
        weight = _weight_from_words(subfamily, stem)
    axes = _axes(tables["fvar"]) if "fvar" in tables else {}
    if "wght" in axes:
        weight = int(round(axes["wght"][1]))
    return {"kind": kind, "family": family, "subfamily": subfamily, "weight": weight, "italic": italic,
            "axes": axes, "cyrillic": _has_cyrillic(tables["cmap"])}


# --------------------------------------------------------------------------- #
# Загрузка и удаление
# --------------------------------------------------------------------------- #


def _family_from_stem(stem: str) -> str:
    """«BricolageGrotesque-Variable» → «Bricolage Grotesque»: без хвоста начертания, CamelCase врозь."""
    base = re.split(r"[-_]", stem, maxsplit=1)[0] or stem
    return re.sub(r"(?<=[a-zа-яё])(?=[A-ZА-ЯЁ])", " ", base).strip()


def _slug(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "", text)[:40]
    return slug or "font"


def _clean_family(value: Any) -> str:
    family = re.sub(r"\s+", " ", str(value or "")).strip().strip("\"'")
    if not family or not FAMILY_RE.match(family):
        raise FontError("family — буквы, цифры, пробел, дефис (до 64 знаков), без кавычек")
    return family


def add_font(data: bytes, filename: str, family: Any = None, weight: Any = None, style: Any = None,
             label: Any = None, vendor_families: set[str] | None = None) -> dict:
    """Проверить и положить шрифт в data/fonts, записать в реестр. Возвращает запись реестра.

    ``family``/``weight``/``style`` — переопределить прочитанное из файла (у WOFF2 семейство
    иначе берётся из имени файла). Тот же файл второй раз — прежняя запись; то же семейство,
    толщина и стиль — заменяет прежний файл. Семейство из комплекта (``vendor_families``) —
    отказ: свой шрифт подменил бы его во всех клипах."""
    ext = Path(str(filename or "")).suffix.lower()
    if ext not in FONT_EXTS:
        raise FontError("filename — шрифт .ttf, .otf или .woff2")
    if not data:
        raise FontError("пустой файл")
    if len(data) > MAX_FONT_BYTES:
        raise FontError(f"шрифт больше {MAX_FONT_BYTES // (1024 * 1024)} МБ")
    info = inspect_font(data, filename)
    if FONT_EXTS[ext] != info["kind"] and {FONT_EXTS[ext], info["kind"]} != {"ttf", "otf"}:
        raise FontError(f"расширение {ext}, а внутри {info['kind'].upper()} — переименуй файл")
    stem = Path(filename).stem
    family = _clean_family(family or info["family"] or _family_from_stem(stem))
    if vendor_families and family.lower() in {f.lower() for f in vendor_families}:
        raise FontError(f"семейство «{family}» уже есть в комплекте — задай своё имя (family)")
    try:
        weight = int(weight) if weight not in (None, "") else int(info["weight"] or 400)
    except (TypeError, ValueError) as exc:
        raise FontError("weight — число 100..900") from exc
    weight = min(900, max(100, weight))
    style = str(style or ("italic" if info["italic"] else "normal")).lower()
    if style not in ("normal", "italic"):
        raise FontError("style — normal или italic")
    axes = info["axes"]
    variable = bool(axes)
    wght = axes.get("wght") or []
    weight_range = [int(round(wght[0])), int(round(wght[2]))] if len(wght) == 3 else None
    digest = hashlib.sha256(data).hexdigest()
    file = f"u-{_slug(family)}-{weight}{'i' if style == 'italic' else ''}-{digest[:10]}{ext}"
    entry = {
        "family": family, "file": file, "weight": weight, "style": style,
        "cyrillic": info["cyrillic"], "tags": ["user"],
        "label": f"{family} · {'вариативный' if variable else weight}",
        "hint": "свой шрифт (загружен)", "user": True, "variable": variable,
        "kind": info["kind"], "sha256": digest, "original_filename": Path(filename).name[:160],
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "license": "лицензия — на совести загрузившего",
    }
    if variable:
        entry["axes"] = {k: v for k, v in axes.items() if v}
        if weight_range:
            entry["weight_range"] = weight_range
    folder = user_fonts_dir()
    with _lock:
        fonts = load_user_fonts()
        same = next((f for f in fonts if f.get("sha256") == digest and f.get("family") == family), None)
        if same:
            return same
        kept = []
        for f in fonts:
            if (f.get("family"), f.get("weight"), f.get("style")) == (family, weight, style):
                (folder / f["file"]).unlink(missing_ok=True)  # то же начертание — заменяем
                continue
            kept.append(f)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / file).write_bytes(data)
        _save_registry([*kept, entry])
    return entry


def delete_font(file: str) -> bool:
    """Убрать свой шрифт (файл и запись). Шрифты комплекта так не удалить."""
    with _lock:
        fonts = load_user_fonts()
        kept = [f for f in fonts if f.get("file") != file]
        if len(kept) == len(fonts):
            return False
        path = user_fonts_dir() / file
        if path.parent == user_fonts_dir() and Path(file).name == file:
            path.unlink(missing_ok=True)
        _save_registry(kept)
    return True


def font_weights(entry: dict) -> list[int]:
    """Толщины, которые можно выбрать: у вариативного — сотни из диапазона wght, иначе одна."""
    rng = entry.get("weight_range")
    if entry.get("variable") and isinstance(rng, list) and len(rng) == 2:
        lo, hi = int(rng[0]), int(rng[1])
        return [w for w in range(100, 1000, 100) if lo <= w <= hi] or [int(entry.get("weight") or 400)]
    if entry.get("variable"):
        return list(range(100, 1000, 100))  # WOFF2: диапазон не прочитать — все, движок сам зажмёт
    return [int(entry.get("weight") or 400)]
