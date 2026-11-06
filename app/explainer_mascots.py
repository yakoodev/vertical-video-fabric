"""Объяснялки: маскоты и позы (из ``app/explainer.py``)."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import projects as P
from app.settings import settings

POSE_KIND = "pose"
SHEET_KIND = "mascot_sheet"


ORIGINS = ("pilot", "generated", "upload", "agent")


MAX_TAGS = 16


NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "один": 1, "два": 2, "три": 3,
                "четыре": 4, "пять": 5}
# «one raised feather-finger», «exactly three feather-fingers», «3 fingers up»; не «one wing-finger» (указывает).
_NUM_RE = re.compile(r"\b(one|two|three|four|five|[1-5])\s+(?:raised\s+|spread\s+|up\s+)?(?:feather-?)?fingers?\b",
                     re.IGNORECASE)


class ExplError(V.AivError):
    """Понятная ошибка объяснялки (нет маскота, неверная поза, ролик не в том состоянии)."""


def _loads(text: Any, default: Any) -> Any:
    return V._loads(text, default)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _hex(value: Any, default: str) -> str:
    found = V._hexes([value]) if value else []
    return found[0] if found else default


def _tags(value: Any) -> list[str]:
    if isinstance(value, str):
        value = re.split(r"[,;\n]+", value)
    out: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = " ".join(str(item or "").strip().lower().replace("ё", "е").split())[:40]
        if text and text not in out:
            out.append(text)
    return out[:MAX_TAGS]


# --------------------------------------------------------------------------- #
# Маскот
# --------------------------------------------------------------------------- #


def _voice(value: Any) -> dict:
    if not isinstance(value, dict):
        return {}
    out = {}
    for key in ("provider", "model", "voice", "style"):
        text = P._text(value.get(key), 200)
        if text:
            out[key] = text
    if out.get("provider") and out["provider"] not in ("gemini", "polza"):
        raise ExplError("voice.provider: gemini (бесплатно) | polza (платно)")
    return out


def _mascot_view(store: Any, row: Any) -> dict:
    data = dict(row)
    data["voice"] = _loads(data.pop("voice_json", "{}"), {})
    data["voice_now"] = describe_voice(mascot_voice(store, data))
    sheet = V.selected_ref(store, int(data["project_id"]), "mascot", int(data["id"]), SHEET_KIND)
    data["sheet"] = ({"ref_id": sheet["id"], "asset_id": sheet["asset_id"], "url": sheet["url"],
                      "version": sheet["version"]} if sheet and sheet.get("asset_id") else None)
    return data


def get_mascot(store: Any, mascot_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM expl_mascots WHERE id = ?", (int(mascot_id),))
    if not row:
        raise KeyError(f"нет маскота {mascot_id}")
    return _mascot_view(store, row)


def list_mascots(store: Any, project_id: int | None = None) -> list[dict]:
    if project_id is None:
        rows = store.db.query_all("SELECT * FROM expl_mascots ORDER BY id")
    else:
        rows = store.db.query_all("SELECT * FROM expl_mascots WHERE project_id = ? ORDER BY id", (int(project_id),))
    return [_mascot_view(store, r) for r in rows]


MASCOT_FIELDS = ("name", "identity_card", "persona", "style_passport", "bg_color", "accent_color", "chip_color",
                 "outline_color", "voice", "music_track_id", "captions_style", "notes", "slug")


def save_mascot(store: Any, project_id: int | None = None, mascot_id: int | None = None, **fields: Any) -> dict:
    """Новый маскот (``project_id`` проекта любого вида) или правка (``mascot_id``). Смена карты
    личности, стиля или фона не перерисовывает позы сама: старые остаются (новые — по новой карте)."""
    current = get_mascot(store, int(mascot_id)) if mascot_id else None
    if current is None:
        if project_id is None:
            raise ExplError("нужен project_id проекта (или mascot_id для правки)")
        P.get_project(store, int(project_id), include_sources=False)  # маскот живёт в проекте любого вида
        if not P._text(fields.get("name")):
            raise ExplError("нужно имя маскота (name)")
        if not P._text(fields.get("identity_card")):
            raise ExplError("нужна карта личности (identity_card): кто это, форма, цвета hex, одежда, деталь — "
                            "по-английски, она идёт в каждый промпт позы дословно")
    sets: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in MASCOT_FIELDS or value is None:
            continue
        if key == "name":
            sets[key] = P._text(value, 60)
        elif key in ("identity_card", "style_passport"):
            sets[key] = P._long_text(value, 800)
        elif key in ("persona", "notes"):
            sets[key] = P._long_text(value, 600)
        elif key in ("bg_color", "accent_color", "chip_color", "outline_color"):
            sets[key] = _hex(value, "")
            if not sets[key]:
                raise ExplError(f"{key}: цвет #rrggbb")
        elif key == "voice":
            sets["voice_json"] = _json(_voice(value))
        elif key == "music_track_id":
            sets[key] = int(value) if value not in ("", 0, "0") else None
            if sets[key]:
                try:
                    store.get_audio_track(sets[key])
                except KeyError as exc:
                    raise ExplError(f"нет трека {value} — list_music") from exc
        elif key == "captions_style":
            sets[key] = P._text(value, 40) or "hormozi"
        elif key == "slug":
            sets[key] = V.slugify(value, "mascot")
    if current is None:
        pid = int(project_id)
        slug = V._unique_slug(store, "expl_mascots", "project_id", pid,
                              sets.pop("slug", "") or V.slugify(sets.get("name"), "mascot"), None)
        cur = store.db.execute("INSERT INTO expl_mascots (project_id, slug, name) VALUES (?, ?, ?)",
                               (pid, slug, sets.get("name") or slug))
        mascot_id = int(cur.lastrowid)
    else:
        if "slug" in sets:
            sets["slug"] = V._unique_slug(store, "expl_mascots", "project_id", int(current["project_id"]),
                                          sets["slug"], int(current["id"]))
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE expl_mascots SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(mascot_id)))
    return get_mascot(store, int(mascot_id))


def delete_mascot(store: Any, mascot_id: int) -> dict:
    mascot = get_mascot(store, mascot_id)
    poses = [int(r["id"]) for r in store.db.query_all("SELECT id FROM expl_poses WHERE mascot_id = ?",
                                                       (int(mascot_id),))]
    for pid in poses:
        _drop_refs(store, int(mascot["project_id"]), "pose", pid)
    _drop_refs(store, int(mascot["project_id"]), "mascot", int(mascot_id))
    store.db.execute("DELETE FROM expl_mascots WHERE id = ?", (int(mascot_id),))
    return {"ok": True, "deleted": int(mascot_id), "poses": len(poses)}


def _drop_refs(store: Any, project_id: int, target_type: str, target_id: int) -> None:
    store.db.execute("DELETE FROM aiv_refs WHERE project_id = ? AND target_type = ? AND target_id = ?",
                     (int(project_id), target_type, int(target_id)))


def set_sheet(store: Any, mascot_id: int, asset_id: int) -> dict:
    """Лист маскота — загрузка владельца/агента (файл из «Файлов для монтажа»)."""
    mascot = get_mascot(store, mascot_id)
    asset = store.get_montage_asset(int(asset_id))
    if asset.get("kind") != "image":
        raise ExplError("лист маскота — картинка")
    V.add_ref(store, int(mascot["project_id"]), "mascot", int(mascot_id), SHEET_KIND, int(asset_id), prompt="upload")
    return get_mascot(store, mascot_id)


def sheet_path(store: Any, mascot: dict) -> Path | None:
    from app.aivideo_gen import _asset_path

    ref = V.selected_ref(store, int(mascot["project_id"]), "mascot", int(mascot["id"]), SHEET_KIND)
    return _asset_path(store, ref)


def dry_voice() -> dict:
    return {"provider": "none", "model": "dry", "voice": "tone"}


def mascot_voice(store: Any, mascot: dict, dry_run: bool = False) -> dict:
    """Чем озвучивается ролик: выбранный голос маскота, один на все его ролики; другим голосом не подменяем.
    Тон — только при ``dry_run`` (явная опция ролика). Голос не выбран — Gemini. Платный голос идёт по карточке:
    это решает этап генерации, а не выбор голоса."""
    if dry_run:
        return dry_voice()
    voice = dict(mascot.get("voice") or {})
    provider = voice.get("provider") or "gemini"
    if provider == "none":  # старое значение: это не голос, настоящий ролик без голоса не выходит
        provider, voice = "gemini", {}
    if provider == "gemini":
        out = {"provider": provider, "model": voice.get("model") or settings.gemini_tts_model,
               "voice": voice.get("voice") or settings.gemini_tts_voice}
        return {**out, "style": voice["style"]} if voice.get("style") else out
    return {"provider": provider, "model": voice.get("model") or settings.polza_tts_model,
            "voice": voice.get("voice") or settings.polza_tts_voice}


def describe_voice(voice: dict, cost_rub: float | None = None, estimate_rub: float | None = None) -> dict:
    """Что сказать про голос владельцу и агенту: ``label`` — готовая фраза, ``real`` — речь ли это вообще."""
    provider = str(voice.get("provider") or "")
    out: dict[str, Any] = {"provider": provider, "voice": voice.get("voice"), "real": provider != "none",
                           "paid": provider not in ("gemini", "none"), "cost_rub": round(float(cost_rub or 0.0), 2)}
    if provider == "none":
        out["label"] = "Голоса нет: черновик, вместо речи тон."
    elif provider == "gemini":
        out["label"] = f"Голос маскота: Gemini {voice.get('voice')}, бесплатно."
    elif cost_rub is not None:
        out["label"] = f"Голос маскота: Polza {voice.get('voice')}, стоил {float(cost_rub):.2f} ₽."
    else:
        price = f", ≈ {float(estimate_rub):.2f} ₽" if estimate_rub else ""
        out["label"] = f"Голос маскота: Polza {voice.get('voice')}, платный{price}, только после «Разрешить»."
    return out


# --------------------------------------------------------------------------- #
# Библиотека поз
# --------------------------------------------------------------------------- #


def number_of(description: str, explicit: Any = None) -> int | None:
    """Сколько пальцев/чисел показывает поза: явное поле или «three feather-fingers» в описании."""
    if explicit not in (None, "", 0, "0"):
        try:
            n = int(explicit)
        except (TypeError, ValueError):
            n = NUMBER_WORDS.get(str(explicit).strip().lower())
        return n if n else None
    m = _NUM_RE.search(str(description or ""))
    if not m:
        return None
    word = m.group(1).lower()
    return int(word) if word.isdigit() else NUMBER_WORDS.get(word)


def _dry_jobs(store: Any, project_id: int) -> set[int]:
    """Запуски сухого прогона: картинка от них — заглушка, а не нарисованная поза."""
    rows = store.db.query_all("SELECT id FROM aiv_gen_jobs WHERE project_id = ? AND provider = 'none' AND ref_kind = ?",
                              (int(project_id), POSE_KIND))
    return {int(r["id"]) for r in rows}


def pose_usable(pose: dict, dry_run: bool = False) -> bool:
    """Можно ли ставить позу в ролик: настоящая картинка; заглушка — только в черновике."""
    return bool(pose["ready"] or (dry_run and pose.get("placeholder")))


def _pose_view(row: Any, ref: dict | None, dry_jobs: set[int] | frozenset = frozenset()) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    has_image = bool(ref and ref.get("asset_id"))
    data["placeholder"] = bool(has_image and ref.get("job_id") and int(ref["job_id"]) in dry_jobs)
    data["ready"] = has_image and not data["placeholder"]
    data["asset_id"] = int(ref["asset_id"]) if has_image else None
    data["url"] = ref["url"] if has_image else None
    data["ref_id"] = int(ref["id"]) if ref else None
    data["version"] = int(ref["version"]) if ref else 0
    return data


def _selected_pose_refs(store: Any, project_id: int) -> dict[int, dict]:
    rows = store.db.query_all("SELECT * FROM aiv_refs WHERE project_id = ? AND target_type = 'pose' AND kind = ? "
                              "AND selected = 1", (int(project_id), POSE_KIND))
    return {int(r["target_id"]): V.ref_view(r) for r in rows}


def get_pose(store: Any, pose_id: int) -> dict:
    row = store.db.query_one("SELECT p.*, m.project_id FROM expl_poses p JOIN expl_mascots m ON m.id = p.mascot_id "
                             "WHERE p.id = ?", (int(pose_id),))
    if not row:
        raise KeyError(f"нет позы {pose_id}")
    ref = V.selected_ref(store, int(row["project_id"]), "pose", int(pose_id), POSE_KIND)
    return _pose_view(row, ref, _dry_jobs(store, int(row["project_id"])))


def list_poses(store: Any, mascot_id: int, ready_only: bool = False) -> list[dict]:
    mascot = get_mascot(store, mascot_id)
    refs = _selected_pose_refs(store, int(mascot["project_id"]))
    dry_jobs = _dry_jobs(store, int(mascot["project_id"]))
    rows = store.db.query_all("SELECT p.*, m.project_id FROM expl_poses p JOIN expl_mascots m ON m.id = p.mascot_id "
                              "WHERE p.mascot_id = ? ORDER BY p.id", (int(mascot_id),))
    out = [_pose_view(r, refs.get(int(r["id"])), dry_jobs) for r in rows]
    return [p for p in out if p["ready"]] if ready_only else out


def save_pose(store: Any, mascot_id: int | None = None, pose_id: int | None = None, description: Any = None,
              title: Any = None, tags: Any = None, emotion: Any = None, number_shown: Any = None,
              origin: str = "agent", asset_id: int | None = None, slug: str = "") -> dict:
    """Поза в библиотеке: новая (``mascot_id``) или правка (``pose_id``). ``asset_id`` — готовая
    картинка (загрузка) → версия позы; без картинки поза ждёт генерации (этап ролика или
    ``request_pose``)."""
    if pose_id:
        current = get_pose(store, int(pose_id))
        mascot = get_mascot(store, int(current["mascot_id"]))
    else:
        if not mascot_id:
            raise ExplError("нужен mascot_id (или pose_id для правки)")
        mascot = get_mascot(store, int(mascot_id))
        current = None
        if not P._text(description):
            raise ExplError("нужно описание позы (description): эмоция, жест, предметы — по-английски, для "
                            "модели картинок")
    sets: dict[str, Any] = {}
    if description is not None:
        sets["description"] = P._long_text(description, 600)
    if title is not None:
        sets["title"] = P._text(title, 80)
    if tags is not None:
        sets["tags_json"] = _json(_tags(tags))
    if emotion is not None:
        sets["emotion"] = P._text(emotion, 40).lower()
    if number_shown is not None or description is not None:
        sets["number_shown"] = number_of(sets.get("description") or (current or {}).get("description") or "",
                                         number_shown)
    if current is None:
        origin = origin if origin in ORIGINS else "agent"
        cur = store.db.execute("INSERT INTO expl_poses (mascot_id, origin) VALUES (?, ?)",
                               (int(mascot["id"]), origin))
        pose_id = int(cur.lastrowid)
        sets.setdefault("slug", V.slugify(slug, f"p{pose_id}") if slug else f"p{pose_id}")
        # Владелец видит название в карточке и в списке «нужно нарисовать»: пустым оно быть не может.
        sets["title"] = sets.get("title") or P._text(description, 60)
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE expl_poses SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(pose_id)))
    if asset_id:
        asset = store.get_montage_asset(int(asset_id))
        if asset.get("kind") != "image":
            raise ExplError("картинка позы — image")
        V.add_ref(store, int(mascot["project_id"]), "pose", int(pose_id), POSE_KIND, int(asset_id),
                  prompt="upload" if origin != "pilot" else "pilot")
    return get_pose(store, int(pose_id))


def delete_pose(store: Any, pose_id: int) -> dict:
    """Убрать позу из библиотеки (картинки остаются в «Файлах для монтажа» — готовые клипы целы)."""
    pose = get_pose(store, pose_id)
    used = [int(r["id"]) for r in store.db.query_all(
        "SELECT id FROM expl_videos WHERE mascot_id = ? AND status IN ('planned', 'awaiting_approval', 'generating', "
        "'assembling') AND beats_json LIKE ?", (int(pose["mascot_id"]), f'%"pose_id": {int(pose_id)}%'))]
    if used:
        raise ExplError(f"поза в работе у роликов {used} — дождись сборки или перепланируй их")
    _drop_refs(store, int(pose["project_id"]), "pose", int(pose_id))
    store.db.execute("DELETE FROM expl_poses WHERE id = ?", (int(pose_id),))
    return {"ok": True, "deleted": int(pose_id)}


def mark_used(store: Any, pose_ids: list[int]) -> None:
    for pid in set(pose_ids):
        store.db.execute("UPDATE expl_poses SET uses = uses + 1, last_used_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (int(pid),))


def is_dry(video: dict | None) -> bool:
    """Сухой прогон — только по явной опции ролика, а не по настройкам проекта."""
    return bool(((video or {}).get("options") or {}).get("dry_run"))
