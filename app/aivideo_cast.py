"""ИИ-видео: стиль проекта, персонажи, облики, локации (из ``app/aivideo.py``)."""

from __future__ import annotations

import json
import re
from typing import Any

from app import projects as P
from app.aivideo_providers import provider_ids

STYLE_KINDS = ("anime", "photoreal")
STYLE_LABELS = {"anime": "аниме / 2D-toon", "photoreal": "фотореализм"}
QUALITIES = ("draft", "quality")
QUALITY_LABELS = {"draft": "черновик", "quality": "качество"}
PROVIDERS = provider_ids()  # подключаемые: app/aivideo_providers.py CATALOGS
ORIGINS = ("invented", "owner")
SPEECH_MODES = ("voice", "sfx", "none")


SFX_REF_RE = re.compile(r"^sfx:[a-z0-9][a-z0-9_-]{0,63}/[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")


SLUG_RE = re.compile(r"[^a-z0-9_]+")
TRANSLIT = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя",
                    ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l", "m", "n", "o", "p", "r", "s",
                     "t", "u", "f", "h", "ts", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))


class AivError(ValueError):
    """Понятная ошибка ИИ-видео (нет проекта, неверный план, этап не разрешён)."""


def _loads(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def slugify(value: Any, fallback: str = "x") -> str:
    text = "".join(TRANSLIT.get(ch, ch) for ch in str(value or "").strip().lower())
    text = SLUG_RE.sub("_", text).strip("_")
    return text[:32] or fallback


def _hexes(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,\s;]+", value)
    out = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip()
        if re.fullmatch(r"#?[0-9a-fA-F]{6}", text):
            out.append("#" + text.lstrip("#").lower())
    return out[:8]


def _now_sql() -> str:
    return "CURRENT_TIMESTAMP"


# --------------------------------------------------------------------------- #
# Проект и стиль-библия
# --------------------------------------------------------------------------- #

STYLE_FIELDS = ("style_kind", "passport_text", "palette", "negative_text", "image_model", "provider", "quality",
                "captions_style", "music_track_id", "video_provider", "video_model", "video_resolution")
VIDEO_RESOLUTIONS = ("480p", "720p", "1080p")  # у модели — ближайшее своё (H3: 768p)
STAND_INS = ("human", "disc", "quadruped", "small")
# Потолок месяца — только владелец (REST с cookie), не агент.
OWNER_FIELDS = ("month_cap_rub",)


def require_project(store: Any, project_id: int) -> dict:
    """Проект существует. Вид проекта не проверяем: «Новое видео» — одна кнопка в любом проекте (решение 16), у стримового
    проекта тоже бывает история или объяснялка. Настройки генерации у него заводятся сами (``_style_row``)."""
    return P.get_project(store, int(project_id), include_sources=False)


def _style_row(store: Any, project_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_projects WHERE project_id = ?", (int(project_id),))
    if not row:
        store.db.execute("INSERT OR IGNORE INTO aiv_projects (project_id) VALUES (?)", (int(project_id),))
        row = store.db.query_one("SELECT * FROM aiv_projects WHERE project_id = ?", (int(project_id),))
    data = dict(row)
    data["palette"] = _loads(data.pop("palette_json", "[]"), [])
    return data


def get_style(store: Any, project_id: int) -> dict:
    from app.settings import settings

    require_project(store, project_id)
    data = _style_row(store, project_id)
    data["image_model"] = data.get("image_model") or settings.aivideo_image_model
    data["video_provider"] = data.get("video_provider") or data["provider"]
    from app.aivideo_providers import default_video_model, video_capabilities

    data["video_model_set"] = bool(data.get("video_model"))
    data["video_model"] = data.get("video_model") or settings.aivideo_video_model or default_video_model(
        data.get("quality"))
    data["video_caps"] = video_capabilities(data["video_model"], data["video_provider"])
    data["video_resolution"] = data.get("video_resolution") or "720p"
    data["style_label"] = STYLE_LABELS.get(data["style_kind"], data["style_kind"])
    data["quality_label"] = QUALITY_LABELS.get(data["quality"], data["quality"])
    return data


def update_style(store: Any, project_id: int, patch: dict, owner: bool = False) -> dict:
    """Стиль-библия и настройки проекта. ``owner`` — можно менять потолок месяца."""
    from app.aivideo_providers import catalog

    require_project(store, project_id)
    allowed = STYLE_FIELDS + (OWNER_FIELDS if owner else ())
    sets: dict[str, Any] = {}
    current = _style_row(store, project_id)
    image_provider = (patch or {}).get("provider") or current["provider"]
    video_provider = (patch or {}).get("video_provider") or current.get("video_provider") or image_provider
    for key, value in (patch or {}).items():
        if key not in allowed:
            if key in OWNER_FIELDS:
                raise AivError("потолок месяца меняет только владелец (в интерфейсе проекта)")
            continue
        if key == "style_kind":
            sets[key] = P._choice(value, STYLE_KINDS, "style_kind")
        elif key == "provider":
            sets[key] = P._choice(value, PROVIDERS, "provider")
        elif key == "quality":
            sets[key] = P._choice(value, QUALITIES, "quality")
        elif key == "palette":
            sets["palette_json"] = _json(_hexes(value))
        elif key == "video_provider":
            sets[key] = P._choice(value, PROVIDERS, "video_provider") if value else ""
        elif key == "image_model":
            model = str(value or "").strip()
            models = _catalog_models(catalog, image_provider, "image")
            if model and model not in models:
                raise AivError(f"image_model у {image_provider}: одна из {', '.join(models)}")
            sets[key] = model
        elif key == "video_model":
            model = str(value or "").strip()
            models = _catalog_models(catalog, video_provider, "video")
            if model and model not in models:
                raise AivError(f"video_model у {video_provider}: одна из {', '.join(models)} (kling/v3 режет план "
                               "на склейки — для сцепки планов не годится)")
            sets[key] = model
        elif key == "video_resolution":
            sets[key] = P._choice(value or "720p", VIDEO_RESOLUTIONS, "video_resolution")
        elif key == "passport_text":
            sets[key] = P._long_text(value, 600)
        elif key == "negative_text":
            sets[key] = P._long_text(value, 400)
        elif key == "captions_style":
            sets[key] = P._text(value, 40) or "hormozi"
        elif key == "music_track_id":
            sets[key] = int(value) if value not in (None, "", 0) else None
        elif key == "month_cap_rub":
            sets[key] = max(0.0, float(value)) if value not in (None, "") else None
    if sets:
        _style_row(store, project_id)
        if "passport_text" in sets or "palette_json" in sets or "style_kind" in sets:
            mark_stale(store, project_id, "style", 0)
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE aiv_projects SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE project_id = ?",
                         (*sets.values(), int(project_id)))
    return get_style(store, project_id)


def _catalog_models(catalog: Any, provider: str, kind: str) -> list[str]:
    try:
        cat = catalog(provider)
    except RuntimeError as exc:  # ProviderError: нет такого провайдера
        raise AivError(str(exc)) from exc
    return cat.image_models() if kind == "image" else cat.video_models()


# --------------------------------------------------------------------------- #
# Персонажи, облики, локации
# --------------------------------------------------------------------------- #


def _voice(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in ("provider", "model", "voice", "sfx", "style"):
        text = P._text(value.get(key), 300 if key == "style" else 200)
        if text:
            out[key] = text
    from app import dubbing

    if out.get("provider"):
        out["provider"] = dubbing.provider_key(out["provider"])
        if out["provider"] not in dubbing.PROVIDERS:
            raise AivError("voice.provider: gemini (бесплатно), polza (платно) или none (сухой прогон)")
        if out["provider"] == "gemini" and out.get("voice"):
            try:
                out["voice"] = dubbing.gemini_voice(out["voice"])
            except dubbing.DubError as exc:
                raise AivError(str(exc)) from exc
    if out.get("sfx") and not SFX_REF_RE.match(out["sfx"]):
        raise AivError("voice.sfx: звук пака «sfx:freesound/<файл>.ogg» (search_pack freesound) — только записи")
    return out


def _speech(value: Any) -> str:
    try:
        return P._choice(value, SPEECH_MODES, "speech", default="voice")
    except ValueError as exc:
        raise AivError(f"{exc} (voice — говорит, sfx — звуки вместо слов, none — молчит)") from exc


def _stand_in(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text and text not in STAND_INS:
        raise AivError(f"stand_in: {' | '.join(STAND_INS)} (манекен в блокинге; пусто — по карте личности)")
    return text


def _unique_slug(store: Any, table: str, scope_col: str, scope_id: int, wanted: str, own_id: int | None) -> str:
    slug, n = wanted, 2
    while True:
        row = store.db.query_one(f"SELECT id FROM {table} WHERE {scope_col} = ? AND slug = ?", (scope_id, slug))
        if not row or (own_id is not None and int(row["id"]) == int(own_id)):
            return slug
        slug, n = f"{wanted[:29]}_{n}", n + 1


def get_character(store: Any, character_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_characters WHERE id = ?", (int(character_id),))
    if not row:
        raise KeyError(f"нет персонажа {character_id}")
    data = dict(row)
    data["voice"] = _loads(data.pop("voice_json", "{}"), {})
    data["speech"] = data.get("speech") or "voice"
    data["speech_captions"] = bool(data.get("speech_captions", 1))
    data["constraints"] = data.get("constraints") or ""
    data["stand_in"] = data.get("stand_in") or ""
    data["looks"] = [dict(r) for r in store.db.query_all(
        "SELECT * FROM aiv_looks WHERE character_id = ? ORDER BY id", (int(character_id),))]
    return data


def character_by_slug(store: Any, project_id: int, slug: str) -> dict | None:
    row = store.db.query_one("SELECT id FROM aiv_characters WHERE project_id = ? AND slug = ?",
                             (int(project_id), str(slug or "")))
    return get_character(store, int(row["id"])) if row else None


def list_characters(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT id FROM aiv_characters WHERE project_id = ? ORDER BY id", (int(project_id),))
    return [get_character(store, int(r["id"])) for r in rows]


def save_character(store: Any, project_id: int, name: str = "", identity_card: Any = None, slug: str = "",
                   origin: str | None = None, voice: Any = None, notes: Any = None,
                   character_id: int | None = None, speech: str | None = None,
                   speech_captions: bool | None = None, constraints: Any = None,
                   stand_in: str | None = None) -> dict:
    """Новый персонаж или правка. Смена карты личности помечает его картинки и кадры с ним «устарело».
    ``speech``: voice (TTS) | sfx (не говорит — звуки пака по эмоции реплики) | none (молчит)."""
    require_project(store, project_id)
    if character_id:
        current = get_character(store, character_id)
        if int(current["project_id"]) != int(project_id):
            raise AivError("персонаж из другого проекта")
        sets: dict[str, Any] = {}
        if name:
            sets["name"] = P._text(name, 80)
        if identity_card is not None and P._long_text(identity_card, 1500) != current["identity_card"]:
            sets["identity_card"] = P._long_text(identity_card, 1500)
        if origin:
            sets["origin"] = P._choice(origin, ORIGINS, "origin")
        if voice is not None:
            sets["voice_json"] = _json(_voice(voice))
        if notes is not None:
            sets["notes"] = P._long_text(notes, 1000)
        if speech:
            sets["speech"] = _speech(speech)
        if speech_captions is not None:
            sets["speech_captions"] = int(bool(speech_captions))
        if constraints is not None and P._long_text(constraints, 600) != current["constraints"]:
            sets["constraints"] = P._long_text(constraints, 600)
        if stand_in is not None:
            sets["stand_in"] = _stand_in(stand_in)
        if slug and slugify(slug) != current["slug"]:
            raise AivError("slug персонажа не меняется (на него ссылаются планы) — заведи нового")
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            store.db.execute(f"UPDATE aiv_characters SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                             (*sets.values(), int(character_id)))
            if "identity_card" in sets or "constraints" in sets:
                mark_stale(store, project_id, "character", int(character_id))
        return get_character(store, character_id)
    name = P._text(name, 80)
    if not name:
        raise AivError("нужно имя персонажа")
    card = P._long_text(identity_card, 1500)
    if not card:
        raise AivError("нужна карта личности: возраст, волосы, глаза, рост и пропорции, 2–3 цвета в hex, "
                       "отличительная деталь — она идёт в каждый промпт дословно")
    slug = _unique_slug(store, "aiv_characters", "project_id", int(project_id), slugify(slug or name, "hero"), None)
    cur = store.db.execute(
        "INSERT INTO aiv_characters (project_id, slug, name, origin, identity_card, voice_json, notes, speech, "
        "speech_captions, constraints, stand_in) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), slug, name, P._choice(origin or "invented", ORIGINS, "origin"), card,
         _json(_voice(voice)), P._long_text(notes, 1000), _speech(speech),
         int(bool(True if speech_captions is None else speech_captions)), P._long_text(constraints, 600),
         _stand_in(stand_in)))
    return get_character(store, int(cur.lastrowid))


def delete_character(store: Any, character_id: int) -> dict:
    current = get_character(store, character_id)
    store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'character' AND target_id = ?", (int(character_id),))
    for look in current["looks"]:
        store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'look' AND target_id = ?", (int(look["id"]),))
    store.db.execute("DELETE FROM aiv_characters WHERE id = ?", (int(character_id),))
    return {"ok": True, "deleted": int(character_id)}


def save_look(store: Any, character_id: int, name: str = "", outfit_text: Any = None, beat: Any = None,
              slug: str = "", look_id: int | None = None) -> dict:
    """Облик персонажа на отрезке сюжета («в пижаме», «промокла»): правка канона — меняется только одежда/состояние."""
    character = get_character(store, character_id)
    if look_id:
        row = store.db.query_one("SELECT * FROM aiv_looks WHERE id = ? AND character_id = ?",
                                 (int(look_id), int(character_id)))
        if not row:
            raise KeyError(f"нет облика {look_id}")
        sets: dict[str, Any] = {}
        if name:
            sets["name"] = P._text(name, 80)
        if outfit_text is not None and P._long_text(outfit_text, 800) != row["outfit_text"]:
            sets["outfit_text"] = P._long_text(outfit_text, 800)
        if beat is not None:
            sets["beat"] = P._text(beat, 120)
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            store.db.execute(f"UPDATE aiv_looks SET {cols} WHERE id = ?", (*sets.values(), int(look_id)))
            if "outfit_text" in sets:
                mark_stale(store, character["project_id"], "look", int(look_id))
        return get_character(store, character_id)
    name = P._text(name, 80)
    outfit = P._long_text(outfit_text, 800)
    if not name or not outfit:
        raise AivError("облику нужны name и outfit_text (что меняется: одежда, состояние)")
    slug = _unique_slug(store, "aiv_looks", "character_id", int(character_id), slugify(slug or name, "look"), None)
    store.db.execute("INSERT INTO aiv_looks (character_id, slug, name, outfit_text, beat) VALUES (?, ?, ?, ?, ?)",
                     (int(character_id), slug, name, outfit, P._text(beat, 120)))
    return get_character(store, character_id)


def delete_look(store: Any, look_id: int) -> dict:
    store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'look' AND target_id = ?", (int(look_id),))
    store.db.execute("DELETE FROM aiv_looks WHERE id = ?", (int(look_id),))
    return {"ok": True, "deleted": int(look_id)}


def get_look(store: Any, look_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_looks WHERE id = ?", (int(look_id),))
    if not row:
        raise KeyError(f"нет облика {look_id}")
    return dict(row)


def get_location(store: Any, location_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_locations WHERE id = ?", (int(location_id),))
    if not row:
        raise KeyError(f"нет локации {location_id}")
    return dict(row)


def location_by_slug(store: Any, project_id: int, slug: str) -> dict | None:
    row = store.db.query_one("SELECT * FROM aiv_locations WHERE project_id = ? AND slug = ?",
                             (int(project_id), str(slug or "")))
    return dict(row) if row else None


def list_locations(store: Any, project_id: int) -> list[dict]:
    return [dict(r) for r in store.db.query_all("SELECT * FROM aiv_locations WHERE project_id = ? ORDER BY id",
                                                (int(project_id),))]


def save_location(store: Any, project_id: int, name: str = "", description: Any = None, slug: str = "",
                  location_id: int | None = None) -> dict:
    require_project(store, project_id)
    if location_id:
        current = get_location(store, location_id)
        sets: dict[str, Any] = {}
        if name:
            sets["name"] = P._text(name, 80)
        if description is not None and P._long_text(description, 800) != current["description"]:
            sets["description"] = P._long_text(description, 800)
        if sets:
            cols = ", ".join(f"{k} = ?" for k in sets)
            store.db.execute(f"UPDATE aiv_locations SET {cols} WHERE id = ?", (*sets.values(), int(location_id)))
            if "description" in sets:
                mark_stale(store, project_id, "location", int(location_id))
        return get_location(store, location_id)
    name = P._text(name, 80)
    if not name:
        raise AivError("нужно имя локации")
    slug = _unique_slug(store, "aiv_locations", "project_id", int(project_id), slugify(slug or name, "place"), None)
    cur = store.db.execute("INSERT INTO aiv_locations (project_id, slug, name, description) VALUES (?, ?, ?, ?)",
                           (int(project_id), slug, name, P._long_text(description, 800)))
    return get_location(store, int(cur.lastrowid))


def delete_location(store: Any, location_id: int) -> dict:
    store.db.execute("DELETE FROM aiv_refs WHERE target_type = 'location' AND target_id = ?", (int(location_id),))
    store.db.execute("DELETE FROM aiv_locations WHERE id = ?", (int(location_id),))
    return {"ok": True, "deleted": int(location_id)}


def _shot_from_row(row: Any) -> dict:
    data = dict(row)
    data["cast"] = _loads(data.pop("cast_json", "[]"), [])
    data["camera"] = _loads(data.pop("camera_json", "{}"), {})
    data["dialogue"] = _loads(data.pop("dialogue_json", "[]"), [])
    data["needs_motion"] = bool(data.get("needs_motion"))
    data["lipsync"] = bool(data.get("lipsync"))
    data["locked"] = bool(data.get("locked"))
    data["continuity"] = _loads(data.pop("continuity_json", ""), {}) or None
    data["state"] = _loads(data.pop("state_json", "{}"), {})
    data["end_state"] = _loads(data.pop("end_state_json", "{}"), {})
    data["blocking"] = _loads(data.pop("blocking_json", ""), {}) or None
    data["continues_from_prev"] = bool(data.get("continues_from_prev"))
    return data


def mark_stale(store: Any, project_id: int, target_type: str, target_id: int) -> int:
    """Правка карты/облика/локации/стиля → свои картинки и кадры планов, где это есть, — «устарело».
    Ничего не перегенерируется само: владелец видит «устарело, перегенерировать за ≈X ₽?»."""
    n = 0
    if target_type == "style":
        cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE project_id = ? AND kind != 'upload' "
                               "AND target_type != 'line'", (int(project_id),))
        return int(cur.rowcount or 0)
    cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE project_id = ? AND target_type = ? AND target_id = ? "
                           "AND kind != 'upload'", (int(project_id), target_type, int(target_id)))
    n += int(cur.rowcount or 0)
    slugs: set[str] = set()
    if target_type == "character":
        slugs = {get_character(store, target_id)["slug"]}
        for look in get_character(store, target_id)["looks"]:
            cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'look' AND target_id = ?",
                                   (int(look["id"]),))
            n += int(cur.rowcount or 0)
    elif target_type == "look":
        look = get_look(store, target_id)
        slugs = {f"look:{get_character(store, look['character_id'])['slug']}:{look['slug']}"}
    elif target_type == "location":
        slugs = {f"loc:{get_location(store, target_id)['slug']}"}
    for shot in _project_shots(store, project_id):
        keys = {c["id"] for c in shot["cast"]} | {f"look:{c['id']}:{c['look']}" for c in shot["cast"] if c["look"]}
        if shot["location"]:
            keys.add(f"loc:{shot['location']}")
        if keys & slugs:
            cur = store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ?",
                                   (int(shot["id"]),))
            n += int(cur.rowcount or 0)
    return n


def _project_shots(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT sh.* FROM aiv_shots sh JOIN aiv_scripts sc ON sc.id = sh.script_id "
                              "WHERE sc.project_id = ? ORDER BY sh.id", (int(project_id),))
    return [_shot_from_row(r) for r in rows]
