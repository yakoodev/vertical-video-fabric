"""Библиотека контента для моушн-слоёв: шрифты, эмодзи, стикеры, звуки, мем-шаблоны.

Откуда берётся:

* шрифты — ``vendor/fonts/`` в репозитории (``fonts.json`` + ttf, OFL);
* паки — ``vendor/packs/`` (``packs.json`` + ``emoji.json`` + каталоги паков),
  их качает ``tools/fetch_packs.py`` при сборке образа.

Ссылки на элементы паков (их пишут в слои ``render.layers`` — см. docs/motion/spec.md):

* ``emoji:1f525`` — Twemoji по коду (как имя файла Twemoji), можно и ``emoji:🔥``;
* ``sticker:<pack>/<file>`` — картинка из пака стикеров;
* ``meme:<pack>/<file>`` — мем-шаблон;
* ``sfx:<pack>/<file>`` — звук.

Рендер (app/render.py, app/motion.py) превращает ссылки в пути через
:func:`resolve_pack_ref`; API отдаёт файлы браузеру. Везде действует один
принцип: отдаём/резолвим только то, что перечислено в индексах — никаких путей
из запроса напрямую, так что ``../`` и прочие фокусы невозможны.
"""

from __future__ import annotations

import json
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, get_args, get_origin
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, params
from fastapi.responses import FileResponse

from app.settings import settings

FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
PACK_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
EMOJI_CODE_RE = re.compile(r"^[0-9a-f]{2,6}(?:-[0-9a-f]{2,6}){0,9}$")
REF_RE = re.compile(r"^(sticker|meme|sfx):([a-z0-9][a-z0-9_-]{0,63})/([A-Za-z0-9][A-Za-z0-9._-]{0,120})$")
# Какие паки годятся под какой префикс ссылки: стикер и мем — обе картинки,
# поэтому взаимозаменяемы; звук — только звук.
REF_PACK_KINDS = {"sticker": ("sticker", "meme"), "meme": ("meme", "sticker"), "sfx": ("sfx",)}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
AUDIO_EXT = {".ogg", ".mp3", ".wav", ".m4a", ".opus", ".flac"}
# Файлы неизменяемы по имени (паки прибиты к версиям), но за авторизацией —
# поэтому private: браузер кэширует, общие прокси — нет.
CACHE_HEADERS = {"Cache-Control": "private, max-age=2592000"}
MEDIA_TYPES = {
    ".ttf": "font/ttf", ".otf": "font/otf", ".woff2": "font/woff2", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
    ".webp": "image/webp", ".ogg": "audio/ogg", ".mp3": "audio/mpeg", ".wav": "audio/wav",
}


def fonts_dir() -> Path:
    return Path(settings.motion_fonts_dir)


def packs_dir() -> Path:
    return Path(settings.motion_packs_dir)


# --------------------------------------------------------------------------- #
# Загрузка индексов (кэш по mtime: пересборка паков подхватывается без рестарта)
# --------------------------------------------------------------------------- #
_json_cache: dict[str, tuple[tuple[int, int], Any]] = {}
_derived_cache: dict[str, tuple[Any, Any]] = {}


def _load_json(path: Path, default: Any) -> Any:
    try:
        st = path.stat()
    except OSError:
        return default
    key = str(path)
    stamp = (st.st_mtime_ns, st.st_size)
    cached = _json_cache.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    _json_cache[key] = (stamp, data)
    return data


def _derive(name: str, source: Any, build: Callable[[Any], Any]) -> Any:
    """Производные структуры (словари поиска) строим один раз на версию источника."""
    cached = _derived_cache.get(name)
    if cached and cached[0] is source:
        return cached[1]
    value = build(source)
    _derived_cache[name] = (source, value)
    return value


def _inside(base: Path, name: str) -> Path | None:
    """Путь base/name, только если он реально внутри base и файл существует."""
    if not FILE_RE.match(name):
        return None
    try:
        base_r = base.resolve()
        path = (base_r / name).resolve()
    except OSError:
        return None
    if path.parent != base_r or not path.is_file():
        return None
    return path


# --------------------------------------------------------------------------- #
# Шрифты
# --------------------------------------------------------------------------- #


def load_fonts() -> list[dict]:
    raw = _load_json(fonts_dir() / "fonts.json", [])
    return [f for f in raw if isinstance(f, dict) and isinstance(f.get("file"), str) and FILE_RE.match(f["file"])]


def font_path(file: str) -> Path | None:
    """Файл шрифта по имени — только из fonts.json."""
    if not any(f["file"] == file for f in load_fonts()):
        return None
    return _inside(fonts_dir(), file)


def list_fonts() -> list[dict]:
    return [{**f, "url": f"/api/motion/fonts/{f['file']}"} for f in load_fonts()]


# --------------------------------------------------------------------------- #
# Паки
# --------------------------------------------------------------------------- #


def load_packs() -> dict[str, dict]:
    raw = _load_json(packs_dir() / "packs.json", {})
    packs = raw.get("packs") if isinstance(raw, dict) else None

    def build(src: Any) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for p in src or []:
            if not isinstance(p, dict) or not PACK_RE.match(str(p.get("id", ""))):
                continue
            items = {
                i["file"]: i
                for i in p.get("items") or []
                if isinstance(i, dict) and isinstance(i.get("file"), str) and FILE_RE.match(i["file"])
            }
            out[p["id"]] = {**p, "_items": items}
        return out

    return _derive("packs", packs, build)


def _summary(pack: dict) -> dict:
    return {k: v for k, v in pack.items() if k not in ("items", "_items")}


def list_packs(kind: str | None = None) -> list[dict]:
    return [_summary(p) for p in load_packs().values() if not kind or p.get("kind") == kind]


def _item_ref(pack: dict, file: str) -> str:
    return f"{pack.get('kind', 'sticker')}:{pack['id']}/{file}"


def _public_item(pack: dict, item: dict) -> dict:
    return {
        **item,
        "ref": _item_ref(pack, item["file"]),
        "url": f"/api/motion/packs/{pack['id']}/{item['file']}",
    }


def _norm(text: Any) -> str:
    """Для поиска: регистр не важен, «ё» = «е» (в данных emojibase «смеется»)."""
    return str(text or "").casefold().replace("ё", "е")


def _matches(query_words: list[str], *fields: Any) -> bool:
    hay = _norm(" ".join(" ".join(map(str, f)) if isinstance(f, list) else str(f or "") for f in fields))
    return all(w in hay for w in query_words)


def pack_items(pack_id: str, q: str = "", limit: int = 500, offset: int = 0) -> dict | None:
    pack = load_packs().get(pack_id)
    if not pack:
        return None
    words = _norm(q).split()
    items = [
        it for it in pack["_items"].values()
        if not words or _matches(words, it.get("name"), it.get("tags"), it.get("file"))
    ]
    page = items[offset:offset + limit]
    return {**_summary(pack), "total": len(items), "items": [_public_item(pack, it) for it in page]}


def pack_file_path(pack_id: str, file: str) -> Path | None:
    """Файл пака — только если он перечислен в packs.json."""
    if not PACK_RE.match(pack_id or ""):
        return None
    pack = load_packs().get(pack_id)
    if not pack or file not in pack["_items"]:
        return None
    return _inside(packs_dir() / pack_id, file)


# --------------------------------------------------------------------------- #
# Эмодзи
# --------------------------------------------------------------------------- #
EMOJI_PACK = "twemoji"


def _emoji_index() -> dict:
    raw = _load_json(packs_dir() / "emoji.json", {})

    def build(src: Any) -> dict:
        items = [e for e in (src or {}).get("emoji", []) if isinstance(e, dict) and e.get("code")]
        by_code: dict[str, dict] = {}
        by_char: dict[str, str] = {}
        for e in items:
            by_code[e["code"]] = e
            by_char[e.get("char", "")] = e["code"]
            by_char[e.get("char", "").replace("️", "")] = e["code"]
            for skin in e.get("skins", []):
                by_code.setdefault(skin, {**e, "code": skin, "char": _char_from_code(skin)})
        return {"groups": (src or {}).get("groups", []), "items": items, "by_code": by_code, "by_char": by_char}

    return _derive("emoji", raw, build)


def _char_from_code(code: str) -> str:
    try:
        return "".join(chr(int(part, 16)) for part in code.split("-"))
    except ValueError:
        return ""


def emoji_code_for_char(char: str) -> str | None:
    """«🔥» -> «1f525» (имя файла Twemoji). Знает и эмодзи вне индекса — по правилу Twemoji."""
    char = (char or "").strip()
    if not char:
        return None
    idx = _emoji_index()["by_char"]
    if char in idx:
        return idx[char]
    stripped = char if "‍" in char else char.replace("️", "")
    code = "-".join(f"{ord(c):x}" for c in stripped)
    return code if emoji_path(code) else None


def emoji_path(code: str) -> Path | None:
    code = (code or "").lower()
    if not EMOJI_CODE_RE.match(code):
        return None
    return _inside(packs_dir() / EMOJI_PACK, f"{code}.svg")


def _emoji_public(e: dict) -> dict:
    return {**e, "ref": f"emoji:{e['code']}", "url": f"/api/motion/emoji/{e['code']}.svg"}


def _emoji_score(e: dict, words: list[str]) -> int:
    """0 — хоть одно слово не нашлось; иначе сумма «качества» совпадений слов."""
    names = [_norm(e.get("name")), _norm(e.get("en"))]
    tags = [_norm(t) for t in (e.get("tags") or []) + (e.get("en_tags") or [])]
    total = 0
    for w in words:
        if w in names:
            total += 6  # «огонь» — это 🔥, а не «горящее сердце» с тегом «огонь»
        elif w in tags:
            total += 4
        elif any(n.startswith(w) or f" {w}" in n for n in names):
            total += 3
        elif any(t.startswith(w) for t in tags):
            total += 2
        elif any(w in n for n in names) or any(w in t for t in tags):
            total += 1
        else:
            return 0
    return total


def search_emoji(q: str = "", group: str = "", limit: int = 200) -> dict:
    """Поиск эмодзи по русским и английским названиям/тегам (и по самому символу).

    Ранжирование простое и предсказуемое: точное совпадение названия или тега —
    выше, начало слова — ниже, вхождение — ещё ниже; внутри — порядок Unicode.
    """
    idx = _emoji_index()
    items = [e for e in idx["items"] if not group or e.get("group") == group]
    q = (q or "").strip()
    if q:
        exact_code = idx["by_char"].get(q) or idx["by_char"].get(q.replace("️", ""))
        words = _norm(q).split()
        scored: list[tuple[int, int, dict]] = []
        for e in items:
            score = 100 if e["code"] == exact_code else _emoji_score(e, words)
            if score > 0:
                scored.append((-score, int(e.get("order") or 0), e))
        scored.sort(key=lambda x: (x[0], x[1]))
        items = [x[2] for x in scored]
    total = len(items)
    return {
        "groups": idx["groups"],
        "total": total,
        "items": [_emoji_public(e) for e in items[: max(1, min(limit, 5000))]],
    }


# --------------------------------------------------------------------------- #
# Ссылки на элементы паков — для рендера
# --------------------------------------------------------------------------- #


def _parse_ref(ref: str) -> tuple[str, str, str] | None:
    """('emoji', 'twemoji', '1f525.svg') | ('sticker', pack, file) | None."""
    ref = (ref or "").strip()
    if ref.startswith("emoji:"):
        value = ref[len("emoji:"):].strip()
        code = value.lower() if EMOJI_CODE_RE.match(value.lower()) else emoji_code_for_char(value)
        if code and EMOJI_CODE_RE.match(code):
            return "emoji", EMOJI_PACK, f"{code}.svg"
        return None
    m = REF_RE.match(ref)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)


def resolve_pack_ref(ref: str) -> Path | None:
    """Путь к файлу по ссылке ``emoji:…`` / ``sticker:…`` / ``meme:…`` / ``sfx:…``.

    None — ссылка битая, пака/файла нет, или пак не того вида (``sfx:`` на картинку).
    """
    parsed = _parse_ref(ref)
    if not parsed:
        return None
    kind, pack_id, file = parsed
    if kind == "emoji":
        return emoji_path(file[:-4])
    pack = load_packs().get(pack_id)
    if not pack or pack.get("kind") not in REF_PACK_KINDS[kind]:
        return None
    return pack_file_path(pack_id, file)


def pack_item_info(ref: str) -> dict | None:
    """Всё, что известно об элементе по ссылке: путь, название, теги, размеры/длительность,
    лицензия. Для рендера (длительность звука, пропорции картинки) и для ИИ-агента."""
    path = resolve_pack_ref(ref)
    if not path:
        return None
    kind, pack_id, file = _parse_ref(ref)  # type: ignore[misc]
    if kind == "emoji":
        code = file[:-4]
        e = _emoji_index()["by_code"].get(code, {})
        pack = load_packs().get(EMOJI_PACK, {})
        return {
            "ref": f"emoji:{code}", "kind": "emoji", "pack": EMOJI_PACK, "file": file, "path": str(path),
            "name": e.get("name") or e.get("en") or code, "char": e.get("char") or _char_from_code(code),
            "tags": e.get("tags", []), "width": 36, "height": 36,
            "license": pack.get("license", "CC-BY-4.0"), "attribution": pack.get("attribution", ""),
            "url": f"/api/motion/emoji/{code}.svg",
        }
    pack = load_packs()[pack_id]
    item = pack["_items"][file]
    return {
        **item,
        "ref": _item_ref(pack, file),
        "kind": pack.get("kind"),
        "pack": pack_id,
        "path": str(path),
        "license": pack.get("license", ""),
        "attribution": pack.get("attribution", ""),
        "url": f"/api/motion/packs/{pack_id}/{file}",
    }


# --------------------------------------------------------------------------- #
# Импорт в «Файлы для монтажа»
# --------------------------------------------------------------------------- #


def import_to_montage_library(store: Any, pack_id: str, file: str) -> dict:
    """Копирует элемент пака в библиотеку «Файлов для монтажа», чтобы его можно было
    вставить классической вставкой (ffmpeg-оверлей) и чтобы его видел ИИ-монтаж.

    Повторный импорт того же файла возвращает уже существующую запись.
    """
    from app.montage_assets import assets_dir, kind_for, probe_asset

    path = pack_file_path(pack_id, file)
    if not path:
        raise KeyError(f"{pack_id}/{file}")
    if path.suffix.lower() == ".svg":
        # ffmpeg-вставки SVG не читают; SVG живут только как моушн-слой.
        raise ValueError("SVG-стикер можно положить только моушн-слоем, не вставкой")
    original = f"{pack_id}/{file}"
    for asset in store.list_montage_assets():
        if asset.get("original_filename") == original and Path(asset.get("file_path", "")).exists():
            return asset
    kind = kind_for(file)
    if kind is None:
        raise ValueError("этот тип файла нельзя сделать вставкой")
    pack = load_packs()[pack_id]
    item = pack["_items"][file]
    dest = assets_dir() / f"{uuid4().hex}-{file}"
    shutil.copyfile(path, dest)
    meta = probe_asset(dest)
    tags = ", ".join(str(t) for t in item.get("tags", []))
    credit = f" ({pack.get('attribution')})" if pack.get("attribution") else ""
    return store.create_montage_asset(
        kind=kind,
        label=str(item.get("name") or Path(file).stem),
        description=f"Из пака «{pack.get('name', pack_id)}»{credit}. Теги: {tags}",
        tags=tags,
        file_path=str(dest),
        original_filename=original,
        mime_type=MEDIA_TYPES.get(dest.suffix.lower(), ""),
        size_bytes=dest.stat().st_size,
        **meta,
    )


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _file_response(path: Path) -> FileResponse:
    return FileResponse(path, media_type=MEDIA_TYPES.get(path.suffix.lower()), headers=CACHE_HEADERS)


def build_router(auth_dep: Any = None, store: Any = None) -> APIRouter:
    """Роутер ``/api/motion`` (шрифты, эмодзи, паки).

    ``auth_dep`` — авторизация: ``AuthDep`` из app.main или сама ``require_auth``; передаётся
    снаружи, чтобы модуль не тянул app.main (циклический импорт). ``store`` нужен
    только для импорта в «Файлы для монтажа».
    """
    if auth_dep is None:
        deps = []
    elif get_origin(auth_dep) is Annotated:
        # AuthDep из app.main — Annotated[None, Depends(require_auth)]: берём сам Depends.
        deps = [d for d in get_args(auth_dep)[1:] if isinstance(d, params.Depends)]
    else:
        deps = [Depends(auth_dep)]
    router = APIRouter(prefix="/api/motion", tags=["Motion assets"], dependencies=deps)

    @router.get("/fonts", summary="Шрифты для моушн-слоёв (fonts.json + url)")
    def api_fonts() -> list[dict]:
        return list_fonts()

    @router.get("/fonts/{file}", include_in_schema=False)
    def api_font_file(file: str) -> FileResponse:
        path = font_path(file)
        if not path:
            raise HTTPException(status_code=404, detail="шрифт не найден")
        return _file_response(path)

    @router.get("/emoji", summary="Поиск эмодзи (русские и английские названия/теги)")
    def api_emoji(
        q: str = Query("", max_length=100),
        group: str = Query("", max_length=40),
        limit: int = Query(200, ge=1, le=5000),
    ) -> dict:
        return search_emoji(q, group, limit)

    @router.get("/emoji/{code}.svg", include_in_schema=False)
    def api_emoji_file(code: str) -> FileResponse:
        path = emoji_path(code)
        if not path:
            raise HTTPException(status_code=404, detail="эмодзи не найден")
        return _file_response(path)

    @router.get("/packs", summary="Паки: стикеры, звуки, мемы, эмодзи")
    def api_packs(kind: str = Query("", max_length=20)) -> list[dict]:
        return list_packs(kind or None)

    @router.get("/packs/{pack}", summary="Элементы пака (с поиском)")
    def api_pack(
        pack: str,
        q: str = Query("", max_length=100),
        limit: int = Query(500, ge=1, le=5000),
        offset: int = Query(0, ge=0),
    ) -> dict:
        data = pack_items(pack, q, limit, offset) if PACK_RE.match(pack) else None
        if data is None:
            raise HTTPException(status_code=404, detail="пак не найден")
        return data

    @router.get("/packs/{pack}/{file}", include_in_schema=False)
    def api_pack_file(pack: str, file: str) -> FileResponse:
        path = pack_file_path(pack, file)
        if not path:
            raise HTTPException(status_code=404, detail="файл не найден")
        return _file_response(path)

    @router.post("/packs/{pack}/{file}/import", summary="Скопировать в «Файлы для монтажа»")
    def api_pack_import(pack: str, file: str) -> dict:
        # store можно передать и геттером (lambda: store), как у соседних роутеров
        current = store() if callable(store) and not hasattr(store, "list_montage_assets") else store
        if current is None:
            raise HTTPException(status_code=501, detail="импорт недоступен")
        try:
            return import_to_montage_library(current, pack, file)
        except KeyError:
            raise HTTPException(status_code=404, detail="файл не найден") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    return router
