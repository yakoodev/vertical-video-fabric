"""Истории: библиотека мира — миры, персонажи, локации, сцены (из ``app/stories.py``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import explainer as E
from app import projects as P

STYLE_KIND = "story_style"
SHEET_KIND = "story_sheet"
PLATE_KIND = "story_plate"
SCENE_KIND = "story_scene"


T_WORLD, T_CHAR, T_LOC, T_SCENE, T_EP = "story_world", "story_char", "story_loc", "story_scene", "story_ep"


ACTIVE_STATUSES = ("new", "scripted", "planned", "awaiting_approval", "generating", "assembling")
# Черновик выпуска: сценарий лежит и читается. Работник его не берёт, денег он не тратит; дальше только по «Запустить».
DRAFT_STATUS = "draft"


ORIGINS = ("generated", "upload", "agent")
REUSE_MIN = 0.5  # Жаккар основ «теги + действие + акцент»: сцена ≈ готовой → берём готовую


STYLE_REFS_MAX = 3
DEFAULT_STYLE_LABEL = "3D-игрушки: круглые головы, мягкий пластилиновый свет"
DEFAULT_STYLE = ("Cute 3D toy-like animation, simple characters with big perfectly round heads, dot eyes and tiny "
                 "bodies, soft matte clay/plasticine materials, warm soft studio lighting, cosy miniature diorama "
                 "sets, gentle depth of field, soft saturated colors")
FORMAT_STORY, FORMAT_DISASTER = "story", "disaster"
FORMATS = (FORMAT_STORY, FORMAT_DISASTER)


DEFAULT_CAPTIONS = {"color": "#ffffff", "active": "#ffffff", "stroke": "#151515", "size": 110, "y": 0.74,
                    "uppercase": False}


class StoryError(V.AivError):
    """Понятная ошибка историй (нет мира, неверный актёр, эпизод не в том состоянии)."""


def _loads(text: Any, default: Any) -> Any:
    return V._loads(text, default)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _tags(value: Any) -> list[str]:
    return E._tags(value)


def _drop_refs(store: Any, project_id: int, target_type: str, target_id: int) -> None:
    store.db.execute("DELETE FROM aiv_refs WHERE project_id = ? AND target_type = ? AND target_id = ?",
                     (int(project_id), target_type, int(target_id)))


def _ref_brief(ref: dict | None) -> dict | None:
    if not ref or not ref.get("asset_id"):
        return None
    return {"ref_id": ref["id"], "asset_id": ref["asset_id"], "url": ref["url"], "version": ref["version"],
            "stale": ref["stale"]}


def _image_asset(store: Any, asset_id: Any, what: str, project_id: int | None = None) -> dict:
    from app.asset_scope import allowed, refusal

    asset = store.get_montage_asset(int(asset_id))
    if asset.get("kind") != "image":
        raise StoryError(f"{what} — картинка (png/jpg/webp)")
    if project_id is not None and not allowed(asset, project_id):
        raise StoryError(f"{what}: " + refusal(store, asset, project_id, "мир"))
    return asset


# --------------------------------------------------------------------------- #
# Мир: стиль, рассказчик, голос, музыка, субтитры
# --------------------------------------------------------------------------- #

WORLD_FIELDS = ("name", "style_label", "style_passport", "negative", "narrator", "voice", "music_track_id",
                "captions", "notes", "slug", "format", "music")
MUSIC_MODES = ("", "auto", "none")


def _captions(value: Any, current: dict | None = None) -> dict:
    out = {**DEFAULT_CAPTIONS, **(current or {})}
    if not isinstance(value, dict):
        return out
    for key in ("color", "active", "stroke"):
        if value.get(key):
            found = V._hexes([value[key]])
            if not found:
                raise StoryError(f"captions.{key}: цвет #rrggbb")
            out[key] = found[0]
    if value.get("size") is not None:
        out["size"] = max(48, min(200, int(float(value["size"]))))
    if value.get("y") is not None:
        out["y"] = round(max(0.55, min(0.86, float(value["y"]))), 3)
    if value.get("uppercase") is not None:
        out["uppercase"] = bool(value["uppercase"])
    return out


def _world_view(store: Any, row: Any) -> dict:
    data = dict(row)
    data["voice"] = _loads(data.pop("voice_json", "{}"), {})
    data["captions"] = {**DEFAULT_CAPTIONS, **_loads(data.pop("captions_json", "{}"), {})}
    data["style_refs"] = [b for b in (_ref_brief(r) for r in style_refs(store, data)) if b]
    return data


def style_refs(store: Any, world: dict) -> list[dict]:
    """Референсы стиля мира: выбранные версии слотов 1..3 (загрузка или нарисованный кадр стиля)."""
    out = []
    for slot in range(1, STYLE_REFS_MAX + 1):
        ref = V.selected_ref(store, int(world["project_id"]), T_WORLD, int(world["id"]), STYLE_KIND, str(slot))
        if ref and ref.get("asset_id"):
            out.append(ref)
    return out


def get_world(store: Any, world_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM story_worlds WHERE id = ?", (int(world_id),))
    if not row:
        raise KeyError(f"нет мира историй {world_id}")
    return _world_view(store, row)


def list_worlds(store: Any, project_id: int | None = None) -> list[dict]:
    if project_id is None:
        rows = store.db.query_all("SELECT * FROM story_worlds ORDER BY id")
    else:
        rows = store.db.query_all("SELECT * FROM story_worlds WHERE project_id = ? ORDER BY id", (int(project_id),))
    return [_world_view(store, r) for r in rows]


def save_world(store: Any, project_id: int | None = None, world_id: int | None = None, **fields: Any) -> dict:
    """Новый мир (``project_id`` проекта «ИИ-видео») или правка (``world_id``). Смена стиля не
    перерисовывает готовое: сцены, листы и плиты помечаются «устарели» (видно в библиотеке)."""
    current = get_world(store, int(world_id)) if world_id else None
    if current is None:
        if project_id is None:
            raise StoryError("нужен project_id проекта «ИИ-видео» (или world_id для правки)")
        V.require_project(store, int(project_id))
    sets: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in WORLD_FIELDS or value is None:
            continue
        if key == "name":
            sets[key] = P._text(value, 60)
        elif key == "style_label":
            sets[key] = P._text(value, 120)
        elif key in ("style_passport", "negative"):
            sets[key] = P._long_text(value, 800)
        elif key in ("narrator", "notes"):
            sets[key] = P._long_text(value, 600)
        elif key == "voice":
            sets["voice_json"] = _json(E._voice(value))
        elif key == "music_track_id":
            sets[key] = int(value) if value not in ("", 0, "0") else None
            if sets[key]:
                try:
                    store.get_audio_track(sets[key])
                except KeyError as exc:
                    raise StoryError(f"нет трека {value} — list_music") from exc
        elif key == "captions":
            sets["captions_json"] = _json(_captions(value, (current or {}).get("captions")))
        elif key == "slug":
            sets[key] = V.slugify(value, "world")
        elif key == "format":
            sets[key] = P._choice(str(value or "").strip() or FORMAT_STORY, FORMATS, "format")
        elif key == "music":
            sets[key] = P._choice(str(value or "").strip().lower(), MUSIC_MODES, "music")
    if current is None and sets.get("format") == FORMAT_DISASTER:
        # Сериал-катастрофа: лоу-поли и тёмный свет, рассказчик на «вы» → 3-е лицо, музыку не вшиваем.
        from app import stories_disaster as D

        for key, default in (("style_label", D.STYLE_LABEL), ("style_passport", D.STYLE), ("narrator", D.NARRATOR),
                             ("music", "none")):
            if not sets.get(key):
                sets[key] = default
    if current is None:
        pid = int(project_id)
        name = sets.get("name") or "Истории"
        slug = V._unique_slug(store, "story_worlds", "project_id", pid,
                              sets.pop("slug", "") or V.slugify(name, "world"), None)
        cur = store.db.execute("INSERT INTO story_worlds (project_id, slug, name, style_label, style_passport, "
                               "voice_json, captions_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               (pid, slug, name, DEFAULT_STYLE_LABEL, DEFAULT_STYLE,
                                _json({"provider": "gemini"}), _json(DEFAULT_CAPTIONS)))
        world_id = int(cur.lastrowid)
    elif "slug" in sets:
        sets["slug"] = V._unique_slug(store, "story_worlds", "project_id", int(current["project_id"]), sets["slug"],
                                      int(current["id"]))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_worlds SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(world_id)))
    if current is not None and any(k in sets and sets[k] != current.get(k) for k in ("style_passport", "negative")):
        mark_world_stale(store, get_world(store, int(world_id)))
    return get_world(store, int(world_id))


def mark_world_stale(store: Any, world: dict) -> int:
    """Стиль мира поменялся — его картинки «устарели» (ничего не перерисовывается само)."""
    n = 0
    pid = int(world["project_id"])
    for table, target in (("story_characters", T_CHAR), ("story_locations", T_LOC), ("story_scenes", T_SCENE)):
        ids = [int(r["id"]) for r in store.db.query_all(f"SELECT id FROM {table} WHERE world_id = ?",
                                                        (int(world["id"]),))]
        for tid in ids:
            n += int(V.mark_stale(store, pid, target, tid) or 0)
    return n


def delete_world(store: Any, world_id: int) -> dict:
    world = get_world(store, world_id)
    pid = int(world["project_id"])
    busy = [int(r["id"]) for r in store.db.query_all(
        f"SELECT id FROM story_episodes WHERE world_id = ? AND status IN ({', '.join('?' for _ in ACTIVE_STATUSES)})",
        (int(world_id), *ACTIVE_STATUSES))]
    if busy:
        raise StoryError(f"у мира эпизоды в работе {busy} — отмени их (story_cancel)")
    drafts = [int(r["id"]) for r in store.db.query_all("SELECT id FROM story_episodes WHERE world_id = ? AND status = ?",
                                                       (int(world_id), DRAFT_STATUS))]
    if drafts:
        raise StoryError(f"у мира черновики {drafts}: сценарии пропадут вместе с миром, сначала удали их (story_delete)")
    for table, target in (("story_characters", T_CHAR), ("story_locations", T_LOC), ("story_scenes", T_SCENE),
                          ("story_episodes", T_EP)):
        for r in store.db.query_all(f"SELECT id FROM {table} WHERE world_id = ?", (int(world_id),)):
            _drop_refs(store, pid, target, int(r["id"]))
    _drop_refs(store, pid, T_WORLD, int(world_id))
    store.db.execute("DELETE FROM story_worlds WHERE id = ?", (int(world_id),))
    return {"ok": True, "deleted": int(world_id), "note": "картинки остались в «Файлах для монтажа», клипы целы"}


def add_style_ref(store: Any, world_id: int, asset_id: int) -> dict:
    """Референс стиля — картинка владельца (в свободный слот, иначе заменяет третий)."""
    world = get_world(store, world_id)
    asset = _image_asset(store, asset_id, "референс стиля", int(world["project_id"]))
    taken = {str(r["slot"]) for r in style_refs(store, world)}
    slot = next((str(n) for n in range(1, STYLE_REFS_MAX + 1) if str(n) not in taken), str(STYLE_REFS_MAX))
    V.add_ref(store, int(world["project_id"]), T_WORLD, int(world_id), STYLE_KIND, int(asset["id"]),
              prompt="upload", slot=slot)
    return get_world(store, world_id)


def world_voice(store: Any, world: dict, dry_run: bool = False) -> dict:
    """Голос рассказчика: выбранный голос мира. Другим голосом не подменяем, скрытая настройка проекта голос не решает:
    тон — только при ``dry_run`` (явная опция эпизода), как у объяснялок (E.mascot_voice)."""
    return E.mascot_voice(store, {"project_id": world["project_id"], "voice": world.get("voice") or {}}, dry_run)


def is_dry(ep: dict | None) -> bool:
    """Черновик без голоса и настоящих картинок: только по явной опции эпизода ``dry_run``."""
    return bool(((ep or {}).get("options") or {}).get("dry_run"))


def scene_usable(scene: dict, dry_run: bool = False) -> bool:
    """Заглушка сцены годится только черновику: настоящий эпизод её не возьмёт, а попросит нарисовать."""
    return bool(scene["ready"] or (dry_run and scene.get("placeholder")))


# --------------------------------------------------------------------------- #
# Актёры и локации
# --------------------------------------------------------------------------- #


def _char_view(store: Any, row: Any, project_id: int) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    data["sheet"] = _ref_brief(V.selected_ref(store, int(project_id), T_CHAR, int(data["id"]), SHEET_KIND))
    return data


def _loc_view(store: Any, row: Any, project_id: int) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    data["plate"] = _ref_brief(V.selected_ref(store, int(project_id), T_LOC, int(data["id"]), PLATE_KIND))
    return data


def get_character(store: Any, character_id: int) -> dict:
    row = store.db.query_one("SELECT c.*, w.project_id FROM story_characters c JOIN story_worlds w ON w.id = c.world_id "
                             "WHERE c.id = ?", (int(character_id),))
    if not row:
        raise KeyError(f"нет актёра {character_id}")
    return _char_view(store, row, int(row["project_id"]))


def list_characters(store: Any, world_id: int) -> list[dict]:
    world = get_world(store, world_id)
    rows = store.db.query_all("SELECT c.*, w.project_id FROM story_characters c JOIN story_worlds w ON w.id = c.world_id "
                              "WHERE c.world_id = ? ORDER BY c.id", (int(world_id),))
    return [_char_view(store, r, int(world["project_id"])) for r in rows]


def get_location(store: Any, location_id: int) -> dict:
    row = store.db.query_one("SELECT l.*, w.project_id FROM story_locations l JOIN story_worlds w ON w.id = l.world_id "
                             "WHERE l.id = ?", (int(location_id),))
    if not row:
        raise KeyError(f"нет локации {location_id}")
    return _loc_view(store, row, int(row["project_id"]))


def list_locations(store: Any, world_id: int) -> list[dict]:
    world = get_world(store, world_id)
    rows = store.db.query_all("SELECT l.*, w.project_id FROM story_locations l JOIN story_worlds w ON w.id = l.world_id "
                              "WHERE l.world_id = ? ORDER BY l.id", (int(world_id),))
    return [_loc_view(store, r, int(world["project_id"])) for r in rows]


def save_character(store: Any, world_id: int | None = None, character_id: int | None = None, name: Any = None,
                   identity_card: Any = None, persona: Any = None, tags: Any = None, slug: Any = None,
                   sheet_asset_id: Any = None, origin: str = "agent") -> dict:
    """Актёр мира: новый (``world_id``) или правка (``character_id``). ``identity_card`` — ПО-АНГЛИЙСКИ
    (форма головы, цвета hex, одежда, деталь) — дословно в каждый промпт сцены с ним."""
    if character_id:
        current = get_character(store, int(character_id))
        world = get_world(store, int(current["world_id"]))
    else:
        if not world_id:
            raise StoryError("нужен world_id (или character_id для правки)")
        world = get_world(store, int(world_id))
        current = None
        if not P._text(name):
            raise StoryError("нужно имя актёра (name)")
        if not P._text(identity_card):
            raise StoryError("нужна карта актёра (identity_card) ПО-АНГЛИЙСКИ: форма, цвета, одежда, деталь — "
                             "она идёт в каждый промпт сцены дословно")
    sets: dict[str, Any] = {}
    if name is not None:
        sets["name"] = P._text(name, 60)
    if identity_card is not None:
        sets["identity_card"] = P._long_text(identity_card, 600)
    if persona is not None:
        sets["persona"] = P._long_text(persona, 400)
    if tags is not None:
        sets["tags_json"] = _json(_tags(tags))
    if current is None:
        wanted = V.slugify(slug or sets.get("name"), "actor")
        new_slug = V._unique_slug(store, "story_characters", "world_id", int(world["id"]), wanted, None)
        cur = store.db.execute("INSERT INTO story_characters (world_id, slug, name, origin) VALUES (?, ?, ?, ?)",
                               (int(world["id"]), new_slug, sets.get("name") or new_slug,
                                origin if origin in ORIGINS else "agent"))
        character_id = int(cur.lastrowid)
    elif slug:
        sets["slug"] = V._unique_slug(store, "story_characters", "world_id", int(world["id"]),
                                      V.slugify(slug, "actor"), int(character_id))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_characters SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(character_id)))
    if current is not None and "identity_card" in sets and sets["identity_card"] != current["identity_card"]:
        V.mark_stale(store, int(world["project_id"]), T_CHAR, int(character_id))
        _stale_scenes(store, int(world["id"]), char_id=int(character_id))
    if sheet_asset_id:
        _image_asset(store, sheet_asset_id, "лист актёра", int(world["project_id"]))
        V.add_ref(store, int(world["project_id"]), T_CHAR, int(character_id), SHEET_KIND, int(sheet_asset_id),
                  prompt="upload")
    return get_character(store, int(character_id))


def save_location(store: Any, world_id: int | None = None, location_id: int | None = None, name: Any = None,
                  description: Any = None, tags: Any = None, slug: Any = None, plate_asset_id: Any = None,
                  origin: str = "agent") -> dict:
    """Локация мира: новая (``world_id``) или правка (``location_id``). ``description`` — ПО-АНГЛИЙСКИ
    (что за комната, мебель, свет), плита — фон без людей 9:16."""
    if location_id:
        current = get_location(store, int(location_id))
        world = get_world(store, int(current["world_id"]))
    else:
        if not world_id:
            raise StoryError("нужен world_id (или location_id для правки)")
        world = get_world(store, int(world_id))
        current = None
        if not P._text(name):
            raise StoryError("нужно название локации (name)")
        if not P._text(description):
            raise StoryError("нужно описание локации (description) ПО-АНГЛИЙСКИ: комната, мебель, предметы, свет")
    sets: dict[str, Any] = {}
    if name is not None:
        sets["name"] = P._text(name, 60)
    if description is not None:
        sets["description"] = P._long_text(description, 600)
    if tags is not None:
        sets["tags_json"] = _json(_tags(tags))
    if current is None:
        wanted = V.slugify(slug or sets.get("name"), "place")
        new_slug = V._unique_slug(store, "story_locations", "world_id", int(world["id"]), wanted, None)
        cur = store.db.execute("INSERT INTO story_locations (world_id, slug, name, origin) VALUES (?, ?, ?, ?)",
                               (int(world["id"]), new_slug, sets.get("name") or new_slug,
                                origin if origin in ORIGINS else "agent"))
        location_id = int(cur.lastrowid)
    elif slug:
        sets["slug"] = V._unique_slug(store, "story_locations", "world_id", int(world["id"]),
                                      V.slugify(slug, "place"), int(location_id))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_locations SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(location_id)))
    if current is not None and "description" in sets and sets["description"] != current["description"]:
        V.mark_stale(store, int(world["project_id"]), T_LOC, int(location_id))
        _stale_scenes(store, int(world["id"]), loc_id=int(location_id))
    if plate_asset_id:
        _image_asset(store, plate_asset_id, "плита локации", int(world["project_id"]))
        V.add_ref(store, int(world["project_id"]), T_LOC, int(location_id), PLATE_KIND, int(plate_asset_id),
                  prompt="upload")
    return get_location(store, int(location_id))


def _stale_scenes(store: Any, world_id: int, char_id: int | None = None, loc_id: int | None = None) -> None:
    world = get_world(store, world_id)
    for scene in list_scenes(store, world_id):
        if (char_id is not None and char_id in scene["cast"]) or (loc_id is not None and scene["location_id"] == loc_id):
            V.mark_stale(store, int(world["project_id"]), T_SCENE, int(scene["id"]))


def _scene_in_use(store: Any, world_id: int, scene_id: int) -> list[int]:
    """Несобранные эпизоды мира, у которых сцена в битах."""
    marks = ", ".join("?" for _ in ACTIVE_STATUSES)
    rows = store.db.query_all(f"SELECT id, beats_json FROM story_episodes WHERE world_id = ? AND status IN ({marks})",
                              (int(world_id), *ACTIVE_STATUSES))
    return [int(r["id"]) for r in rows
            if any(int(b.get("scene_id") or 0) == int(scene_id) for b in _loads(r["beats_json"], []))]


def delete_character(store: Any, character_id: int) -> dict:
    c = get_character(store, character_id)
    used = [s["id"] for s in list_scenes(store, int(c["world_id"])) if int(character_id) in s["cast"]]
    if used:
        raise StoryError(f"актёр есть в сценах {used[:12]} — сначала убери их (story_delete_scene)")
    _drop_refs(store, int(c["project_id"]), T_CHAR, int(character_id))
    store.db.execute("DELETE FROM story_characters WHERE id = ?", (int(character_id),))
    return {"ok": True, "deleted": int(character_id)}


def delete_location(store: Any, location_id: int) -> dict:
    loc = get_location(store, location_id)
    used = [s["id"] for s in list_scenes(store, int(loc["world_id"])) if s["location_id"] == int(location_id)]
    if used:
        raise StoryError(f"локация есть в сценах {used[:12]} — сначала убери их (story_delete_scene)")
    _drop_refs(store, int(loc["project_id"]), T_LOC, int(location_id))
    store.db.execute("DELETE FROM story_locations WHERE id = ?", (int(location_id),))
    return {"ok": True, "deleted": int(location_id)}


def sheet_path(store: Any, character: dict) -> Path | None:
    from app.aivideo_gen import _asset_path

    return _asset_path(store, V.selected_ref(store, int(character["project_id"]), T_CHAR, int(character["id"]),
                                             SHEET_KIND))


def plate_path(store: Any, location: dict) -> Path | None:
    from app.aivideo_gen import _asset_path

    return _asset_path(store, V.selected_ref(store, int(location["project_id"]), T_LOC, int(location["id"]),
                                             PLATE_KIND))


# --------------------------------------------------------------------------- #
# Библиотека сцен и переиспользование
# --------------------------------------------------------------------------- #


def _cast(value: Any) -> list[int]:
    out = sorted({int(v) for v in value or [] if str(v).strip().lstrip("-").isdigit() and int(v) > 0})
    return out[:4]


def _scene_view(row: Any, ref: dict | None, dry_jobs: set[int] | frozenset = frozenset()) -> dict:
    data = dict(row)
    data["cast"] = _loads(data.pop("cast_json", "[]"), [])
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    has_image = bool(ref and ref.get("asset_id"))
    data["placeholder"] = bool(has_image and ref.get("job_id") and int(ref["job_id"]) in dry_jobs)
    data["ready"] = has_image and not data["placeholder"]
    data["asset_id"] = int(ref["asset_id"]) if has_image else None
    data["url"] = ref["url"] if has_image else None
    data["ref_id"] = int(ref["id"]) if ref else None
    data["version"] = int(ref["version"]) if ref else 0
    data["stale"] = bool(ref and ref.get("stale"))
    return data


def get_scene(store: Any, scene_id: int) -> dict:
    row = store.db.query_one("SELECT s.*, w.project_id FROM story_scenes s JOIN story_worlds w ON w.id = s.world_id "
                             "WHERE s.id = ?", (int(scene_id),))
    if not row:
        raise KeyError(f"нет сцены {scene_id}")
    return _scene_view(row, V.selected_ref(store, int(row["project_id"]), T_SCENE, int(scene_id), SCENE_KIND),
                       E._dry_jobs(store, int(row["project_id"])))


def list_scenes(store: Any, world_id: int, ready_only: bool = False) -> list[dict]:
    world = get_world(store, world_id)
    rows = store.db.query_all("SELECT * FROM aiv_refs WHERE project_id = ? AND target_type = ? AND kind = ? "
                              "AND selected = 1", (int(world["project_id"]), T_SCENE, SCENE_KIND))
    refs = {int(r["target_id"]): V.ref_view(r) for r in rows}
    scenes = store.db.query_all("SELECT s.*, w.project_id FROM story_scenes s JOIN story_worlds w ON w.id = s.world_id "
                                "WHERE s.world_id = ? ORDER BY s.id", (int(world_id),))
    dry_jobs = E._dry_jobs(store, int(world["project_id"]))
    out = [_scene_view(r, refs.get(int(r["id"])), dry_jobs) for r in scenes]
    return [s for s in out if s["ready"]] if ready_only else out


def save_scene(store: Any, world_id: int | None = None, scene_id: int | None = None, location_id: Any = None,
               cast: Any = None, action: Any = None, accent: Any = None, title: Any = None, tags: Any = None,
               origin: str = "agent", asset_id: Any = None) -> dict:
    """Сцена библиотеки: новая (``world_id``) или правка (``scene_id``). ``asset_id`` — готовая картинка
    9:16 (загрузка) → версия сцены; без картинки сцена рисуется в эпизоде, где понадобилась."""
    if scene_id:
        current = get_scene(store, int(scene_id))
        world = get_world(store, int(current["world_id"]))
    else:
        if not world_id:
            raise StoryError("нужен world_id (или scene_id для правки)")
        world = get_world(store, int(world_id))
        current = None
        if not P._text(action):
            raise StoryError("нужно действие сцены (action) ПО-АНГЛИЙСКИ: кто что делает, поза, выражение, предметы")
    sets: dict[str, Any] = {}
    if location_id is not None:
        if location_id in (0, "0", ""):
            sets["location_id"] = None
        else:
            loc = get_location(store, int(location_id))
            if int(loc["world_id"]) != int(world["id"]):
                raise StoryError(f"локация {location_id} из другого мира")
            sets["location_id"] = int(loc["id"])
    if cast is not None:
        ids = _cast(cast)
        for cid in ids:
            if int(get_character(store, cid)["world_id"]) != int(world["id"]):
                raise StoryError(f"актёр {cid} из другого мира")
        sets["cast_json"] = _json(ids)
    if action is not None:
        sets["action"] = P._long_text(action, 600)
    if accent is not None:
        sets["accent"] = P._text(accent, 120)
    if title is not None:
        sets["title"] = P._text(title, 80)
    if tags is not None:
        sets["tags_json"] = _json(_tags(tags))
    if current is None:
        cur = store.db.execute("INSERT INTO story_scenes (world_id, origin) VALUES (?, ?)",
                               (int(world["id"]), origin if origin in ORIGINS else "agent"))
        scene_id = int(cur.lastrowid)
        sets.setdefault("slug", f"s{scene_id}")
        sets.setdefault("title", P._text(action, 60))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE story_scenes SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(scene_id)))
    if asset_id:
        _image_asset(store, asset_id, "картинка сцены", int(world["project_id"]))
        V.add_ref(store, int(world["project_id"]), T_SCENE, int(scene_id), SCENE_KIND, int(asset_id), prompt="upload")
    return get_scene(store, int(scene_id))


def delete_scene(store: Any, scene_id: int) -> dict:
    scene = get_scene(store, scene_id)
    used = _scene_in_use(store, int(scene["world_id"]), int(scene_id))
    if used:
        raise StoryError(f"сцена в работе у эпизодов {used} — дождись сборки или перепланируй их")
    _drop_refs(store, int(scene["project_id"]), T_SCENE, int(scene_id))
    store.db.execute("DELETE FROM story_scenes WHERE id = ?", (int(scene_id),))
    return {"ok": True, "deleted": int(scene_id)}


def mark_used(store: Any, scene_ids: list[int]) -> None:
    for sid in set(scene_ids):
        store.db.execute("UPDATE story_scenes SET uses = uses + 1, last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (int(sid),))


def scene_terms(spec: dict) -> set[str]:
    """Основы слов действия, тегов и акцента (русские и английские вместе)."""
    return E.stems(" ".join([str(spec.get("action") or ""), " ".join(spec.get("tags") or []),
                             str(spec.get("accent") or "")]))


def scene_similarity(a: dict, b: dict) -> float:
    return round(E._jaccard(scene_terms(a), scene_terms(b)), 3)


def match_scene(scenes: list[dict], spec: dict, threshold: float = REUSE_MIN) -> tuple[dict | None, float]:
    """Готовая сцена для кадра: то же место, тот же состав актёров, действие похоже (≥ порога)."""
    loc = int(spec.get("location_id") or 0)
    cast = _cast(spec.get("cast"))
    best, best_sim = None, 0.0
    for scene in scenes:
        if int(scene.get("location_id") or 0) != loc or _cast(scene.get("cast")) != cast:
            continue
        sim = scene_similarity(spec, scene)
        if sim > best_sim or (sim == best_sim and best is not None and scene.get("ready") and not best.get("ready")):
            best, best_sim = scene, sim
    return (best, best_sim) if best is not None and best_sim >= threshold else (None, best_sim)
