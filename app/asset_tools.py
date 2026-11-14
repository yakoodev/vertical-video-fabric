"""Инструменты библиотеки файлов для монтажа: шрифты, эмодзи, паки, картинки и видео (загрузка, поиск, генерация,
отрезок источника), правило про реальных людей.

Входят в общий реестр (``app/agent_tools.py``)."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.tool_core import (
    _assets_project, _motion_assets, _num, _obj, _short, ASSET_OWNER_PARAMS, Tool, ToolContext, ToolError,
)


def similar_pack_refs(ref: str, limit: int = 5) -> list[str]:
    """Ближайшие по написанию существующие ссылки того же вида (difflib): «success» → cash, cheer…

    Звук ищем в его паке (нет такого пака — во freesound, настоящих записях), стикер — в стикерах."""
    import difflib

    try:
        assets = _motion_assets()
        packs = assets.load_packs()
    except Exception:  # noqa: BLE001 - без паков подсказки нет, ошибка остаётся понятной
        return []
    kind, _, rest = ref.partition(":")
    pack_id, _, file = rest.rpartition("/")
    kinds = {"sfx": ("sfx",), "sticker": ("sticker", "meme"), "meme": ("meme", "sticker")}.get(kind, ())
    pool = [p for p in packs.values() if p.get("kind") in kinds]
    if kind == "sfx":
        named = [p for p in pool if p.get("id") == pack_id] or [p for p in pool if p.get("id") == "freesound"]
        pool = named or pool
    want = Path(file or rest).stem.lower()
    scored = []
    for pack in pool:
        for name, item in (pack.get("_items") or {}).items():
            words = [Path(name).stem.lower(), *(str(t).lower() for t in item.get("tags") or [])]
            score = max(difflib.SequenceMatcher(None, want, w).ratio() for w in words)
            scored.append((score, f"{pack.get('kind')}:{pack['id']}/{name}"))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [_layer_ref(ref) for _score, ref in scored[:limit]]


def _font_manifest() -> list[dict]:
    """Шрифты: из app.motion_assets, а без него — прямо из vendor/fonts/fonts.json."""
    try:
        from app import motion_assets

        if hasattr(motion_assets, "list_fonts"):
            fonts = motion_assets.list_fonts()
            return [f for f in (fonts.get("fonts") if isinstance(fonts, dict) else fonts) or [] if isinstance(f, dict)]
    except ImportError:
        pass
    from app.settings import settings

    path = settings.root_dir / "vendor" / "fonts" / "fonts.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [f for f in data if isinstance(f, dict)] if isinstance(data, list) else []


def h_list_fonts(_ctx: ToolContext, _args: dict) -> dict:
    """Шрифты, сгруппированные по семействам: модели нужны family + доступные weight."""
    families: dict[str, dict] = {}
    for font in _font_manifest():
        family = str(font.get("family") or "").strip()
        if not family:
            continue
        entry = families.setdefault(family, {"family": family, "weights": [], "cyrillic": True, "tags": [], "hint": ""})
        from app.user_fonts import font_weights

        weights = font_weights(font) if font.get("variable") else (
            [font["weight"]] if font.get("weight") is not None else [])
        entry["weights"].extend(w for w in weights if w not in entry["weights"])
        if font.get("user"):
            entry["user"] = True  # свой, загруженный (upload_font)
        if font.get("variable"):
            entry["variable"] = True  # вес — любой из weights (ось wght), не одно начертание
        entry["cyrillic"] = entry["cyrillic"] and bool(font.get("cyrillic", True))
        for tag in font.get("tags") or []:
            if tag not in entry["tags"]:
                entry["tags"].append(tag)
        if font.get("hint") and not entry["hint"]:
            entry["hint"] = str(font["hint"])[:120]
    for entry in families.values():
        entry["weights"].sort()
    return {"fonts": list(families.values())}


MAX_UPLOAD_FONT_B64 = 28 * 1024 * 1024  # 20 МБ файла в base64


def h_upload_font(_ctx: ToolContext, args: dict) -> dict:
    """Свой шрифт (TTF/OTF/WOFF2) в шрифты сервиса (data/fonts) — base64 в аргументах, как upload_image."""
    import base64
    import binascii

    from app.user_fonts import FontError, font_weights

    name = Path(str(args.get("filename") or "")).name
    raw = str(args.get("data_base64") or "")
    if len(raw) > MAX_UPLOAD_FONT_B64:
        raise ToolError("шрифт больше 20 МБ")
    if "," in raw[:100] and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("data_base64 — не base64") from exc
    try:
        entry = _motion_assets().upload_font(data, name, args.get("family"), args.get("weight"), args.get("style"))
    except FontError as exc:
        raise ToolError(f"шрифт не принят: {exc}") from exc
    out = {"ok": True, "family": entry["family"], "file": entry["file"], "weights": font_weights(entry),
           "style": entry["style"], "cyrillic": entry.get("cyrillic"), "variable": bool(entry.get("variable")),
           "use": f"props.font: \"{entry['family']}\", props.weight — из weights"}
    if entry.get("cyrillic") is False:
        out["warning"] = "в шрифте нет кириллицы — русский текст нарисуется запасным шрифтом; бери для латиницы"
    elif entry.get("cyrillic") is None:
        out["note"] = "WOFF2: кириллицу не проверить — посмотри view_frame с русским титром"
    return out


def h_search_emoji(_ctx: ToolContext, args: dict) -> dict:
    query = str(args.get("q") or "").strip()
    if not query:
        raise ToolError("нужен запрос q (слово по-русски или по-английски)")
    limit = max(1, min(int(args.get("limit") or 12), 40))
    found = _motion_assets().search_emoji(query, limit=limit)
    return {
        "query": query,
        "total": found.get("total", 0),
        "results": [
            {"char": e.get("char"), "name": e.get("name"), "en": e.get("en"), "ref": e.get("ref")}
            for e in found.get("items") or []
        ][:limit],
        "hint": "слой emoji: props.char = символ; или image: props.src = ref",
    }


def h_list_packs(_ctx: ToolContext, args: dict) -> dict:
    kind = str(args.get("kind") or "").strip() or None
    return {
        "packs": [
            {k: p.get(k) for k in ("id", "name", "kind", "count", "license") if p.get(k) is not None}
            for p in _motion_assets().list_packs(kind)
        ],
        "hint": "sfx — звуки (audio asset_id), sticker/meme — картинки (image src), emoji — через search_emoji",
    }


def _layer_ref(ref: str) -> str:
    """Мем-паки слой берёт как ``sticker:`` (так их принимают и проверка, и рендер)."""
    return "sticker:" + ref[len("meme:"):] if ref.startswith("meme:") else ref


def h_search_pack(_ctx: ToolContext, args: dict) -> dict:
    assets = _motion_assets()
    pack = str(args.get("pack") or "").strip()
    query = str(args.get("q") or "").strip()
    kind = str(args.get("kind") or "").strip()
    limit = max(1, min(int(args.get("limit") or 20), 60))
    if pack:
        packs = [pack]
    else:
        # Звуки без явного пака — только freesound: интерфейсные клики/тоны Kenney владелец
        # назвал дешёвыми (их можно взять, лишь назвав пак).
        packs = [p["id"] for p in assets.list_packs()
                 if p.get("kind") != "emoji" and (not kind or p.get("kind") == kind)
                 and (p.get("kind") != "sfx" or p["id"] == "freesound")]
    results = []
    for pack_id in packs:
        found = assets.pack_items(pack_id, query, limit=limit)
        if found is None:
            raise ToolError(f"пака {pack_id} нет (см. list_packs)")
        for item in found.get("items") or []:
            entry = {k: item.get(k) for k in ("name", "tags", "duration") if item.get(k)}
            entry.update(pack=pack_id, kind=found.get("kind"), ref=_layer_ref(str(item.get("ref") or "")))
            results.append(entry)
        if len(results) >= limit:
            break
    return {
        "query": query,
        "results": results[:limit],
        "hint": "sfx → слой audio props.asset_id = ref; sticker → слой image props.src = ref",
    }


def h_list_assets(ctx: ToolContext, args: dict) -> dict:
    from app.asset_scope import visible_assets
    from app.video_memes import unapproved_asset_ids

    query = str(args.get("q") or "").strip().lower()
    project_id = _assets_project(ctx, args)
    hidden = unapproved_asset_ids(ctx.store)  # неодобренные видеомемы ИИ не ставит — и не показываем
    out = []
    for asset in visible_assets(ctx.store, project_id):
        if int(asset["id"]) in hidden:
            continue
        hay = " ".join(str(asset.get(k) or "") for k in ("label", "description", "tags")).lower()
        if query and not all(word in hay for word in query.split()):
            continue
        out.append(
            {
                "asset_id": asset["id"],
                "kind": asset["kind"],
                "label": asset["label"],
                "description": asset.get("description") or "",
                "tags": asset.get("tags") or "",
                "width": asset.get("width"),
                "height": asset.get("height"),
                "duration_sec": asset.get("duration_sec"),
                "scope": "project" if asset.get("project_id") is not None else "shared",
            }
        )
        if len(out) >= 60:
            break
    return {"assets": out, "project_id": project_id}


def h_search_web_images(_ctx: ToolContext, args: dict) -> dict:
    from app.ai.asset_search import search_images

    query = str(args.get("query") or "").strip()
    if len(query) < 2:
        raise ToolError("нужен запрос query")
    try:
        found = search_images(query, int(args.get("count") or 6), free_only=args.get("free_only", True) is not False)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    return {
        "query": found.get("query"),
        "results": [
            {k: r.get(k) for k in ("url", "title", "source", "license", "width", "height") if r.get(k) is not None}
            for r in found.get("results") or []
        ],
        "hint": ("понравившуюся картинку добавь в библиотеку через add_image_from_url, потом ставь слоем image с "
                 "asset_id; фото/видео реальных людей при запрете (плейбук, заказ) add_image_from_url не примет — "
                 "бери рисованные, мультяшные, аниме, животных"),
    }


def h_add_image_from_url(ctx: ToolContext, args: dict) -> dict:
    from app.ai.asset_generate import add_from_url

    url = str(args.get("url") or "").strip()
    try:
        asset = add_from_url(ctx.store, url, label=str(args.get("label") or "")[:120],
                             description=str(args.get("description") or "")[:500],
                             project_id=_assets_project(ctx, args))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сеть/удалённый сайт
        raise ToolError(f"не удалось скачать: {str(exc)[:200]}") from exc
    query = str(args.get("query") or "").strip()
    if query:
        from app.ai.asset_generate import image_matches

        if not image_matches(asset, query):
            # Поиск по названию приносит мимо (на «Олег Монгол» — фото БелАЗа).
            ctx.store.delete_montage_asset(asset["id"])
            raise ToolError(f"картинка не про «{query[:60]}» — возьми другого кандидата из search_web_images")
    try:
        people = people_gate(ctx, asset)
    except ToolError:
        ctx.store.delete_montage_asset(asset["id"])  # скачанное под запрет в библиотеке не держим
        raise
    out = {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
           "width": asset.get("width"), "height": asset.get("height")}
    return {**out, **people}


# Плейбуки, где владелец запретил фото реальных людей на вставках (docs/playbooks: «только мемы без фото
# реальных людей»), и такой же запрет словами в запросе.
NO_REAL_PEOPLE_PLAYBOOKS = {"vtuber-story", "stream-shorts", "stream-horizontal"}
NO_REAL_PEOPLE_RE = re.compile(
    r"без\s+(?:фото(?:графий)?\s+|видео\s+)?(?:реальн\w*|живых|настоящих)\s+люд|без\s+(?:фото(?:графий)?\s+)?людей"
    r"|no\s+real\s+people",
    re.IGNORECASE,
)


def real_people_rule(ctx: ToolContext) -> str | None:
    """Почему в этом ходе нельзя вставлять фото/видео реальных людей (плейбук или слова владельца) — или None."""
    if ctx.playbook and str(ctx.playbook).lower() in NO_REAL_PEOPLE_PLAYBOOKS:
        return f"плейбук {ctx.playbook}: только мемы без фото реальных людей"
    if ctx.request and NO_REAL_PEOPLE_RE.search(ctx.request):
        return "владелец в заказе: без фото реальных людей"
    return None


def people_gate(ctx: ToolContext, asset: dict) -> dict:
    """Gemini-зрение: реальный человек на ассете? (кэш в ассете, app/ai/people_check.py). Запрещено в этом
    ходе (``real_people_rule``) — ToolError с объяснением; иначе — поля для ответа (предупреждение/пометка).

    QA 4 (клип 896): «angry monitor» — гифка с офисным работником, «sleeping student» — фото студента, при
    заказе «без фото реальных людей»; проверка релевантности их пропустила."""
    from app.ai.people_check import check_asset, describe

    if asset.get("kind") == "audio":
        return {}
    check = check_asset(ctx.store, asset)
    if check.get("real_person") is None:
        return {"people_check": check.get("skipped") or "не проверено"}
    if not check["real_person"]:
        return {"people_check": "реальных людей нет" + (f" ({check['what']})" if check.get("what") else "")}
    rule = real_people_rule(ctx)
    if rule:
        raise ToolError(f"на вставке #{asset['id']} {describe(check)} — {rule}. Возьми рисованную/мультяшную "
                        "картинку, аниме или животных (другой кандидат search_web_images / другой файл библиотеки)")
    return {"people_check": describe(check),
            "people_warning": (f"на вставке {describe(check)}: если заказ «без фото реальных людей» — замени; "
                               "в отчёте назови это прямо")}


MAX_UPLOAD_IMAGE_BYTES = 12 * 1024 * 1024
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def h_upload_image(ctx: ToolContext, args: dict) -> dict:
    """Своя картинка агента (скриншот сайта и т.п.) в библиотеку — base64 в аргументах.

    Агент-монтажёр снимал скрины vtubika.store у себя и собирал multipart-запрос
    к /api/montage-assets руками: по MCP загрузить файл было нечем."""
    import base64
    import binascii
    from uuid import uuid4

    from app.montage_assets import assets_dir, kind_for, probe_asset

    name = Path(str(args.get("filename") or "image.png")).name
    if Path(name).suffix.lower() not in _IMAGE_EXTS:
        raise ToolError("filename — картинка: .png, .jpg, .webp или .gif")
    raw = str(args.get("data_base64") or "")
    if "," in raw[:100] and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]  # data:image/png;base64,...
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("data_base64 — не base64") from exc
    if not data:
        raise ToolError("пустой файл")
    if len(data) > MAX_UPLOAD_IMAGE_BYTES:
        raise ToolError("картинка больше 12 МБ — уменьши или сожми")
    dest = assets_dir() / f"{uuid4().hex}-{name}"
    dest.write_bytes(data)
    meta = probe_asset(dest)
    if not meta.get("width"):
        dest.unlink(missing_ok=True)
        raise ToolError("это не картинка — файл не читается")
    asset = ctx.store.create_montage_asset(
        kind=kind_for(name, "") or "image",
        label=str(args.get("label") or Path(name).stem)[:120],
        description=str(args.get("description") or "")[:500],
        tags="",
        file_path=str(dest),
        original_filename=name,
        mime_type="",
        size_bytes=len(data),
        project_id=_assets_project(ctx, args),
        **meta,
    )
    return {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
            "width": asset.get("width"), "height": asset.get("height"), **_animation_fields(dest)}


def _animation_fields(path: Path) -> dict:
    """Анимированная картинка (GIF/APNG/webm-alpha)? — кадры, fps и подсказка, куда её ставить."""
    from app.anim_frames import animation_info, brief, describe

    info = animation_info(path)
    if not info.get("animated"):
        return {}
    return {"animation": brief(info), "animation_note": describe(info) + " — слой image играет её по кругу, "
            "PNGtuber (add_pngtuber) — как состояние аватара"}


MAX_UPLOAD_VIDEO_BYTES = 40 * 1024 * 1024
_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".mkv"}
MAX_SOURCE_CLIP_SEC = 180.0


def _video_asset_result(asset: dict) -> dict:
    sound = ("звук файла в клипе — props.audio: true (дорожкой: volume 0..2, gain_db −30..+12), без него слой немой"
             if asset.get("has_audio") else "у файла нет звука — слой немой")
    return {"ok": True, "asset_id": asset["id"], "kind": asset["kind"], "label": asset["label"],
            "width": asset.get("width"), "height": asset.get("height"), "duration_sec": asset.get("duration_sec"),
            "has_audio": bool(asset.get("has_audio")),
            "hint": "слой {type: video, props: {asset_id, from, w, device: phone}} — запись в телефоне; "
                    "во весь кадр шире 9:16 — auto_crop_layer; " + sound}


def h_upload_video(ctx: ToolContext, args: dict) -> dict:
    """Своё видео (запись экрана приложения) в библиотеку — base64 в аргументах, как upload_image.
    Большие записи — лучше add_video_from_source (исходник сервиса) или POST /api/montage-assets."""
    import base64
    import binascii
    from uuid import uuid4

    from app.montage_assets import assets_dir, probe_asset

    name = Path(str(args.get("filename") or "video.mp4")).name
    if Path(name).suffix.lower() not in _VIDEO_EXTS:
        raise ToolError("filename — видео: .mp4, .webm, .mov, .m4v или .mkv")
    raw = str(args.get("data_base64") or "")
    if "," in raw[:100] and raw.startswith("data:"):
        raw = raw.split(",", 1)[1]  # data:video/mp4;base64,...
    if len(raw) > MAX_UPLOAD_VIDEO_BYTES * 4 // 3 + 16:
        raise ToolError("видео больше 40 МБ — сожми, обрежь или загрузи исходником и возьми add_video_from_source")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("data_base64 — не base64") from exc
    if not data:
        raise ToolError("пустой файл")
    if len(data) > MAX_UPLOAD_VIDEO_BYTES:
        raise ToolError("видео больше 40 МБ — сожми, обрежь или загрузи исходником и возьми add_video_from_source")
    dest = assets_dir() / f"{uuid4().hex}-{name}"
    dest.write_bytes(data)
    meta = probe_asset(dest)
    if not meta.get("width") or not meta.get("duration_sec"):
        dest.unlink(missing_ok=True)
        raise ToolError("это не видео — файл не читается")
    asset = ctx.store.create_montage_asset(
        kind="video",
        label=str(args.get("label") or Path(name).stem)[:120],
        description=str(args.get("description") or "")[:500],
        tags="",
        file_path=str(dest),
        original_filename=name,
        mime_type="",
        size_bytes=len(data),
        project_id=_assets_project(ctx, args),
        **meta,
    )
    return {**_video_asset_result(asset), **_animation_fields(dest)}


def source_clip_args(src: Path, start: float, end: float, dest: Path, audio: bool = True) -> list[str]:
    """ffmpeg: кусок исходника → видео для слоя video (h264, ≤ 1920 по стороне). ``audio`` — со
    звуком (первая дорожка, AAC): слой играет его только с props.audio; False — немое."""
    sound = ["-map", "0:v:0", "-map", "0:a:0?", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"] if audio else ["-an"]
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-i", str(src),
            "-t", f"{end - start:.3f}", *sound, "-sn", "-dn",
            "-vf", "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", str(dest)]


def h_add_video_from_source(ctx: ToolContext, args: dict) -> dict:
    """Кусок исходника сервиса (запись экрана, загруженная как проект) → видео в библиотеку."""
    from uuid import uuid4

    from app.montage_assets import assets_dir, probe_asset
    from app.render import _run_ffmpeg

    try:
        source = ctx.store.get_source(int(args.get("source_id")))
    except (KeyError, TypeError, ValueError) as exc:
        raise ToolError("нет такого источника source_id (list_projects)") from exc
    src = Path(source.get("local_path") or "")
    if not src.is_file():
        raise ToolError("у источника нет файла видео")
    duration = _num(source.get("duration_sec"), 0.0) or 0.0
    start = max(0.0, _num(args.get("start"), 0.0) or 0.0)
    end = _num(args.get("end"), None)
    if end is None:
        end = duration if duration > 0 else start + MAX_SOURCE_CLIP_SEC
    if duration > 0:
        end = min(end, duration)
    if end - start < 0.2:
        raise ToolError("кусок короче 0.2 с — проверь start/end")
    if end - start > MAX_SOURCE_CLIP_SEC:
        raise ToolError(f"кусок длиннее {MAX_SOURCE_CLIP_SEC:.0f} с — возьми нужные места (from у слоя — "
                        "секунда внутри куска)")
    dest = assets_dir() / f"{uuid4().hex}-source{source['id']}-{start:.0f}-{end:.0f}.mp4"
    try:
        _run_ffmpeg(source_clip_args(src, start, end, dest, audio=args.get("audio", True) is not False), timeout=900)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        raise ToolError(f"не нарезалось: {str(exc)[:200]}") from exc
    meta = probe_asset(dest)
    if not meta.get("duration_sec"):
        dest.unlink(missing_ok=True)
        raise ToolError("в этом куске нет кадров")
    asset = ctx.store.create_montage_asset(
        kind="video",
        label=str(args.get("label") or f"{Path(source.get('original_filename') or '').stem or 'источник'} {start:.0f}–{end:.0f} с")[:120],
        description=str(args.get("description") or "")[:500],
        tags="",
        file_path=str(dest),
        original_filename=dest.name,
        mime_type="video/mp4",
        size_bytes=dest.stat().st_size,
        # Кусок проекта: без клипа и явного проекта файл идёт в проект самого источника.
        project_id=_assets_project(ctx, args) or source.get("project_id"),
        **meta,
    )
    return _video_asset_result(asset)


def h_add_video_from_url(ctx: ToolContext, args: dict) -> dict:
    """Отрезок ролика по ссылке владельца (yt-dlp, только start..end) → видео со звуком в библиотеку."""
    from app.video_import import import_video_from_url

    try:
        asset = import_video_from_url(ctx.store, str(args.get("url") or ""), args.get("start"), args.get("end"),
                                      str(args.get("label") or ""), project_id=_assets_project(ctx, args))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - сеть/удалённый сайт
        raise ToolError(f"не удалось скачать отрезок: {str(exc)[:200]}") from exc
    return _video_asset_result(asset)


def h_generate_image(ctx: ToolContext, args: dict) -> dict:
    from app.ai.asset_generate import generate_asset

    prompt = str(args.get("prompt") or "").strip()
    if len(prompt) < 3:
        raise ToolError("нужно описание картинки prompt")
    try:
        asset = generate_asset(ctx.store, prompt, label=str(args.get("label") or "")[:120],
                               project_id=_assets_project(ctx, args))
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    except RuntimeError as exc:
        raise ToolError(f"не нарисовалось: {str(exc)[:200]}") from exc
    return {"ok": True, "asset_id": asset["id"], "label": asset["label"],
            "width": asset.get("width"), "height": asset.get("height")}


def h_svg_to_webm(ctx: ToolContext, args: dict) -> dict:
    """SVG (текст или файл библиотеки) → webm с альфой в «Файлы для монтажа» (app/svg_webm.py)."""
    from app.asset_scope import allowed, refusal
    from app.svg_webm import SvgWebmError, asset_svg_text, svg_to_asset

    project_id = _assets_project(ctx, args)
    try:
        if args.get("asset_id"):
            source = ctx.store.get_montage_asset(int(args["asset_id"]))
            if not allowed(source, project_id):
                raise ToolError(refusal(ctx.store, source, project_id))
        svg = asset_svg_text(ctx.store, args["asset_id"]) if args.get("asset_id") else args.get("svg")
        asset, info = svg_to_asset(ctx.store, svg, args, label=str(args.get("label") or "SVG-анимация"),
                                   project_id=project_id)
    except KeyError as exc:
        raise ToolError(f"файла для монтажа {args.get('asset_id')!r} нет") from exc
    except (ValueError, SvgWebmError) as exc:
        raise ToolError(str(exc)) from exc
    return {"ok": True, "asset_id": asset["id"], "label": asset["label"], "cached": info["cached"],
            "frames": info.get("frames"), "fps": info.get("fps"), "duration": info.get("duration"),
            "width": info.get("width"), "height": info.get("height"),
            "note": "слой image с этим asset_id играет анимацию с прозрачностью (с начала слоя, по кругу)"}


def tools() -> list[Tool]:
    return [
        Tool(
            "list_fonts", "Смотрю шрифты",
            "Шрифты для слоёв (family, weight, cyrillic; user — свой загруженный, variable — любой вес из "
            "weights). Для русского текста бери cyrillic=true. / Fonts.",
            _obj({}), h_list_fonts, scope="global",
        ),
        Tool(
            "upload_font", "Загружаю шрифт",
            "Свой шрифт (фирменный шрифт бренда) в шрифты сервиса: data_base64 — файл в base64 (можно data:-URL), "
            "filename .ttf/.otf/.woff2 (до 20 МБ), family? (иначе из файла), weight? 100..900, style? normal|italic. "
            "Файл проверяется (сигнатура, таблицы, кириллица); лицензия — на совести загружающего. Тот же "
            "family+weight+style заменяет прежний. Вариативный (ось wght) — один файл, вес любой из weights. "
            "Вернёт family для props.font. / Upload a font file.",
            _obj({"data_base64": {"type": "string"}, "filename": {"type": "string"}, "family": {"type": "string"},
                  "weight": {"type": "integer"}, "style": {"type": "string", "enum": ["normal", "italic"]}},
                 ["data_base64", "filename"]),
            h_upload_font, scope="global",
            label=lambda a: f"Загружаю шрифт {(a.get('family') or a.get('filename') or '')[:40]}",
        ),
        Tool(
            "search_emoji", "Ищу эмодзи",
            "Поиск эмодзи по слову (рус/англ): вернёт символ и код для слоя emoji или image src emoji:<код>. "
            "/ Emoji search.",
            _obj({"q": {"type": "string"}, "limit": {"type": "integer"}}, ["q"]), h_search_emoji, scope="global",
            label=lambda a: f"Ищу эмодзи «{_short(a.get('q'), 24)}»",
        ),
        Tool(
            "list_packs", "Смотрю паки",
            "Паки стикеров, звуков (sfx) и мемов, встроенные в сервис. / Built-in sticker/sfx/meme packs.",
            _obj({"kind": {"type": "string", "enum": ["sticker", "meme", "sfx", "emoji"]}}), h_list_packs,
            scope="global",
        ),
        Tool(
            "search_pack", "Ищу в паке",
            "Поиск звуков/стикеров/мемов по словам (рус/англ): pack — конкретный пак, или kind "
            "(sfx/sticker/meme) — по всем пакам вида. Вернёт ref: sfx:<pack>/<file> для audio asset_id, "
            "sticker:<pack>/<file> для image src. / Search sfx/sticker/meme packs.",
            _obj({"q": {"type": "string"}, "pack": {"type": "string"},
                  "kind": {"type": "string", "enum": ["sfx", "sticker", "meme"]}, "limit": {"type": "integer"}}),
            h_search_pack, scope="global",
            label=lambda a: f"Ищу «{_short(a.get('q'), 24)}» в " + (f"паке {a['pack']}" if a.get("pack")
                                                                  else f"паках {a.get('kind') or ''}".strip()),
        ),
        Tool(
            "list_assets", "Смотрю библиотеку",
            "«Файлы для монтажа» владельца (мемы, реакции, звуки) — asset_id для слоёв image/audio и "
            "вставок: только общие и файлы проекта клипа, чужие брать нельзя. q — фильтр по словам; без клипа в "
            "контексте — clip_id или project_id (проект-серия), иначе только общие. / Owner's montage library: shared files "
            "and files of the clip's project.",
            _obj({"q": {"type": "string"}, **ASSET_OWNER_PARAMS}), h_list_assets, scope="global",
        ),
        Tool(
            "search_web_images", "Ищу картинку в интернете",
            "Найти картинку/мем в интернете (Gemini + Google; без Gemini — Imgflip-мемы, Wikimedia Commons, "
            "Giphy при GIPHY_API_KEY — запрос лучше по-английски). Ничего не скачивает — только кандидаты. "
            "free_only=true — только свободные лицензии. / Web image search.",
            _obj({"query": {"type": "string"}, "free_only": {"type": "boolean"}, "count": {"type": "integer"}},
                 ["query"]),
            h_search_web_images, scope="global", label=lambda a: f"Ищу в интернете «{_short(a.get('query'), 30)}»",
        ),
        Tool(
            "add_image_from_url", "Скачиваю картинку",
            "Скачать картинку по прямой ссылке в библиотеку; вернёт asset_id для слоя image. query — что "
            "искал (кого/что должна показывать картинка): Gemini проверит, что она про это, иначе ошибка и "
            "бери следующего кандидата. / Download to library.",
            _obj({"url": {"type": "string"}, "label": {"type": "string"}, "description": {"type": "string"},
                  "query": {"type": "string"}, **ASSET_OWNER_PARAMS}, ["url"]),
            h_add_image_from_url, scope="global", label=lambda a: f"Скачиваю картинку {(a.get('label') or '')[:40]}",
        ),
        Tool(
            "upload_image", "Загружаю картинку",
            "Своя картинка (скриншот сайта, кадр) в библиотеку: data_base64 — содержимое файла в base64 "
            "(можно data:-URL), filename с расширением .png/.jpg/.webp/.gif, label. До 12 МБ. Вернёт asset_id "
            "для слоя image. / Upload a local image as base64.",
            _obj({"data_base64": {"type": "string"}, "filename": {"type": "string"}, "label": {"type": "string"},
                  "description": {"type": "string"}, **ASSET_OWNER_PARAMS}, ["data_base64", "filename"]),
            h_upload_image, scope="global", label=lambda a: f"Загружаю {(a.get('label') or a.get('filename') or '')[:40]}",
        ),
        Tool(
            "upload_video", "Загружаю видео",
            "Своё видео (запись экрана приложения) в библиотеку: data_base64 — файл в base64 (можно data:-URL), "
            "filename .mp4/.webm/.mov/.m4v, label. До 40 МБ (больше — загрузи исходником и add_video_from_source). "
            "Вернёт asset_id для слоя video. / Upload a screen recording as base64.",
            _obj({"data_base64": {"type": "string"}, "filename": {"type": "string"}, "label": {"type": "string"},
                  "description": {"type": "string"}, **ASSET_OWNER_PARAMS}, ["data_base64", "filename"]),
            h_upload_video, scope="global", label=lambda a: f"Загружаю видео {(a.get('label') or a.get('filename') or '')[:40]}",
        ),
        Tool(
            "add_video_from_source", "Беру кусок видео",
            "Кусок исходника сервиса (проект из list_projects: запись экрана, загруженная как видео) → видео "
            "в библиотеку для слоя video: source_id, start, end (секунды исходника, до 180 с; нет end — до "
            "конца). Звук сохраняется (audio:false — немое); в клипе он играет только у слоя с props.audio: true. "
            "/ Cut a source range into the library for a video layer.",
            _obj({"source_id": {"type": "integer"}, "start": {"type": "number"}, "end": {"type": "number"},
                  "label": {"type": "string"}, "audio": {"type": "boolean"}, **ASSET_OWNER_PARAMS}, ["source_id"]),
            h_add_video_from_source, scope="global",
            label=lambda a: f"Беру кусок источника {a.get('source_id')} {_num(a.get('start'), 0.0) or 0:g}–"
            f"{a.get('end') if a.get('end') is not None else 'конец'} с",
        ),
        Tool(
            "add_video_from_url", "Скачиваю отрезок видео",
            "Отрезок ролика по ссылке ВЛАДЕЛЬЦА (YouTube и всё, что умеет yt-dlp): скачивается только start..end "
            "(секунды ролика, до 180 с, ≤ 1080p) тем же способом и прокси, что источники, → видео СО ЗВУКОМ в "
            "библиотеку (asset_id для слоя video; звук в клипе — props.audio: true). Сам ролики в интернете не "
            "ищи — только ссылка владельца. / Download only a section of a video by the owner's link.",
            _obj({"url": {"type": "string"}, "start": {"type": "number"}, "end": {"type": "number"},
                  "label": {"type": "string"}, **ASSET_OWNER_PARAMS}, ["url", "start", "end"]),
            h_add_video_from_url, scope="global",
            label=lambda a: f"Скачиваю отрезок {_num(a.get('start'), 0.0) or 0:g}–{_num(a.get('end'), 0.0) or 0:g} с"
            + (f" «{_short(a.get('label'), 30)}»" if a.get("label") else ""),
        ),
        Tool(
            "generate_image", "Рисую картинку",
            "Нарисовать оригинальную картинку/стикер (ИИ) в библиотеку; вернёт asset_id. / Generate an image.",
            _obj({"prompt": {"type": "string"}, "label": {"type": "string"}, **ASSET_OWNER_PARAMS}, ["prompt"]),
            h_generate_image,
            scope="global", label=lambda a: f"Рисую «{_short(a.get('prompt'), 40)}»",
        ),
        Tool(
            "svg_to_webm", "Перевожу SVG в анимацию",
            "SVG с анимацией (CSS @keyframes / SMIL) → webm с прозрачностью в «Файлы для монтажа»: покадрово в "
            "headless-браузере (анимации на паузе и перемотаны на время кадра — без рывков и пропусков). svg — "
            "текст SVG или asset_id — SVG из библиотеки; duration (с; нет — по анимациям SVG), fps (30), width/"
            "height (нет — из SVG), scale (1), strip_bg (убрать первый прямоугольник-фон). Тот же SVG — тот же "
            "файл (кэш). Ответ asset_id: слой image играет его с альфой. / Animated SVG → alpha webm asset.",
            _obj({"svg": {"type": "string"}, "asset_id": {"type": "integer"}, "label": {"type": "string"},
                  "duration": {"type": "number", "minimum": 0.2, "maximum": 30},
                  "fps": {"type": "integer", "minimum": 1, "maximum": 60},
                  "width": {"type": "integer"}, "height": {"type": "integer"},
                  "scale": {"type": "number", "minimum": 0.25, "maximum": 3}, "strip_bg": {"type": "boolean"},
                  **ASSET_OWNER_PARAMS}),
            h_svg_to_webm, scope="global", label=lambda a: "SVG → webm",
        ),
    ]
