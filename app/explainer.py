"""Объяснялки: ролик «маскот объясняет тему» — данные, библиотека поз и деньги (docs/guides/explainer.md).

Пилот владельца (клип 887, «3 ошибки сна», сова «Ух») — рецепт: мультяшный маскот без лица-человека
в разных позах, склейка каждые 1.5–3 с под голос, плоский фон цвета холста, крупные субтитры с
выделенными словами, плашки «#1/#2/#3», маленькие чипы («07:00»), вжухи на склейках, музыка.

Сущности (проект вида «ИИ-видео», ``projects.kind = ai_video`` — деньги, карточки и работник
генерации общие с ``app/aivideo_gen.py``):

* маскот (``expl_mascots``) — имя, карта личности (английский текст, ДОСЛОВНО в каждый промпт
  позы), стиль-паспорт, цвет фона (тот же, что у холста: позы рисуются на нём), характер для
  сценариста, голос TTS (провайдер/голос/стиль, Gemini — бесплатно), музыка, цвета плашек;
  лист маскота — версия ``aiv_refs`` (target ``mascot``, kind ``mascot_sheet``): загрузка владельца
  или генерация по карточке;
* библиотека поз (``expl_poses``) — у каждой текст позы для модели картинок, короткое русское
  название, теги (эмоция, жест, предметы, показанное число), картинка — версия ``aiv_refs``
  (target ``pose``, kind ``pose``). Поза без картинки — «нужно нарисовать» (её рисует этап ролика);
* ролик (``expl_videos``) — тема, длительность, тон, статус конвейера, сценарий, биты, слова озвучки,
  итоговый клип. Конвейер и сборка — ``app/explainer_gen.py``.

Переиспользование поз без эмбеддингов: ``candidates`` — ранжирование библиотеки по тегам и словам
(русские и английские основы слов) для каждого бита, Gemini выбирает из кандидатов или описывает
новую позу; ``similar_pose`` — защита от дублей: новая поза, почти совпадающая с готовой (теги или
описание по Жаккару ≥ ``SIMILAR_MIN``, то же показанное число), не рисуется — берётся готовая.

Деньги — расширение этапа ``explainer`` у ``app/aivideo_gen.py`` (``register_extension``): позы (по
3 ₽ nano-banana 1K), лист маскота и озвучка Polza — карточкой владельцу; Gemini TTS и сухой прогон
(провайдер проекта ``none``) — сразу, 0 ₽.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import aivideo as V
from app import projects as P
from app.settings import settings

EXPL_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS expl_mascots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        slug TEXT NOT NULL,
        name TEXT NOT NULL DEFAULT '',
        identity_card TEXT NOT NULL DEFAULT '',
        persona TEXT NOT NULL DEFAULT '',
        style_passport TEXT NOT NULL DEFAULT '',
        bg_color TEXT NOT NULL DEFAULT '#2a1b47',
        accent_color TEXT NOT NULL DEFAULT '#ffd447',
        chip_color TEXT NOT NULL DEFAULT '#7a6ff0',
        outline_color TEXT NOT NULL DEFAULT '#1b1030',
        voice_json TEXT NOT NULL DEFAULT '{}',
        music_track_id INTEGER,
        captions_style TEXT NOT NULL DEFAULT 'hormozi',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE (project_id, slug)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS expl_poses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mascot_id INTEGER NOT NULL REFERENCES expl_mascots(id) ON DELETE CASCADE,
        slug TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        emotion TEXT NOT NULL DEFAULT '',
        number_shown INTEGER,
        origin TEXT NOT NULL DEFAULT 'generated',
        uses INTEGER NOT NULL DEFAULT 0,
        last_used_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_expl_poses_mascot ON expl_poses(mascot_id)",
    # Ролик. status: new → scripted → planned → awaiting_approval → generating → assembling → done |
    # failed | cancelled. Сценарий, биты, слова — JSON (правятся агентом/владельцем до сборки).
    """
    CREATE TABLE IF NOT EXISTS expl_videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        mascot_id INTEGER NOT NULL REFERENCES expl_mascots(id) ON DELETE CASCADE,
        topic TEXT NOT NULL DEFAULT '',
        duration_target REAL NOT NULL DEFAULT 45,
        tone TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'new',
        title TEXT NOT NULL DEFAULT '',
        script_json TEXT NOT NULL DEFAULT '{}',
        beats_json TEXT NOT NULL DEFAULT '[]',
        words_json TEXT NOT NULL DEFAULT '[]',
        timeline_json TEXT NOT NULL DEFAULT '{}',
        options_json TEXT NOT NULL DEFAULT '{}',
        notes_json TEXT NOT NULL DEFAULT '[]',
        approval_id INTEGER,
        clip_id INTEGER,
        estimate_rub REAL NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        requested_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_expl_videos_status ON expl_videos(status, id)",
]

STAGE = "explainer"
POSE_KIND = "pose"
SHEET_KIND = "mascot_sheet"
NARRATION_KIND = "narration"
VIDEO_STATUSES = ("new", "scripted", "planned", "awaiting_approval", "generating", "assembling", "done", "failed",
                  "cancelled")
ACTIVE_STATUSES = ("new", "scripted", "planned", "awaiting_approval", "generating", "assembling")
STATUS_LABELS = {"new": "пишу сценарий", "scripted": "подбираю позы", "planned": "считаю стоимость",
                 "awaiting_approval": "ждёт одобрения", "generating": "рисую позы и озвучку",
                 "assembling": "собираю клип", "done": "готово", "failed": "ошибка", "cancelled": "отменено"}
TONES = ("дружелюбно", "энергично", "с юмором", "серьёзно", "спокойно")
DURATION_MIN = 30.0
DURATION_MAX = 60.0
ORIGINS = ("pilot", "generated", "upload", "agent")
SIMILAR_MIN = 0.6  # Жаккар тегов или описания: новая поза ≈ готовой → берём готовую
MAX_TAGS = 16
DEFAULT_BG = "#2a1b47"
BG_NAMES = {"#2a1b47": "dark purple"}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "один": 1, "два": 2, "три": 3,
                "четыре": 4, "пять": 5}
# «one raised feather-finger», «exactly three feather-fingers», «3 fingers up»; не «one wing-finger» (указывает).
_NUM_RE = re.compile(r"\b(one|two|three|four|five|[1-5])\s+(?:raised\s+|spread\s+|up\s+)?(?:feather-?)?fingers?\b",
                     re.IGNORECASE)
_WORD_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
_EN_STOP = frozenset("""a an the and or of in on at to with by for from its it is are one other both very
small big little huge face pose wing wings while into over under up down out as like""".split())


class ExplError(V.AivError):
    """Понятная ошибка объяснялки (нет маскота, неверная поза, ролик не в том состоянии)."""


def ensure_explainer_schema(conn) -> None:
    """Таблицы объяснялок (из ``Database.init`` после ИИ-видео) + расширение этапа генерации."""
    for sql in EXPL_SCHEMA_SQL:
        conn.execute(sql)
    _register()


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
    if out.get("provider") and out["provider"] not in ("gemini", "polza", "none"):
        raise ExplError("voice.provider: gemini (бесплатно) | polza (платно) | none")
    return out


def _mascot_view(store: Any, row: Any) -> dict:
    data = dict(row)
    data["voice"] = _loads(data.pop("voice_json", "{}"), {})
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
    """Новый маскот (``project_id`` проекта «ИИ-видео») или правка (``mascot_id``). Смена карты
    личности, стиля или фона не перерисовывает позы сама: старые остаются (новые — по новой карте)."""
    current = get_mascot(store, int(mascot_id)) if mascot_id else None
    if current is None:
        if project_id is None:
            raise ExplError("нужен project_id проекта «ИИ-видео» (или mascot_id для правки)")
        V.require_project(store, int(project_id))
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


def mascot_voice(store: Any, mascot: dict) -> dict:
    """Голос озвучки ролика: провайдер маскота; проект в сухом прогоне (провайдер ``none``) — тон."""
    style = V._style_row(store, int(mascot["project_id"]))
    voice = dict(mascot.get("voice") or {})
    provider = "none" if style["provider"] == "none" else (voice.get("provider") or "gemini")
    if provider == "gemini":
        out = {"provider": provider, "model": voice.get("model") or settings.gemini_tts_model,
               "voice": voice.get("voice") or settings.gemini_tts_voice}
        return {**out, "style": voice["style"]} if voice.get("style") else out
    if provider == "none":
        return {"provider": "none", "model": "dry", "voice": "tone"}
    return {"provider": provider, "model": voice.get("model") or settings.polza_tts_model,
            "voice": voice.get("voice") or settings.polza_tts_voice}


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


def _pose_view(row: Any, ref: dict | None) -> dict:
    data = dict(row)
    data["tags"] = _loads(data.pop("tags_json", "[]"), [])
    data["ready"] = bool(ref and ref.get("asset_id"))
    data["asset_id"] = int(ref["asset_id"]) if data["ready"] else None
    data["url"] = ref["url"] if data["ready"] else None
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
    return _pose_view(row, ref)


def list_poses(store: Any, mascot_id: int, ready_only: bool = False) -> list[dict]:
    mascot = get_mascot(store, mascot_id)
    refs = _selected_pose_refs(store, int(mascot["project_id"]))
    rows = store.db.query_all("SELECT p.*, m.project_id FROM expl_poses p JOIN expl_mascots m ON m.id = p.mascot_id "
                              "WHERE p.mascot_id = ? ORDER BY p.id", (int(mascot_id),))
    out = [_pose_view(r, refs.get(int(r["id"]))) for r in rows]
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
        sets.setdefault("title", P._text(description, 60))
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


# --- подбор без эмбеддингов ------------------------------------------------ #


def stems(text: Any) -> set[str]:
    """Основы слов (первые 5 букв, ё→е) без служебных: русские и английские вместе."""
    from app.word_emphasis import STOPWORDS

    out = set()
    for word in _WORD_RE.findall(str(text or "").lower().replace("ё", "е")):
        if word in STOPWORDS or word in _EN_STOP or (len(word) < 3 and not word.isdigit()):
            continue
        out.add(word[:5])
    return out


def pose_terms(pose: dict) -> tuple[set[str], set[str]]:
    """(основы тегов и названия, основы описания)."""
    tag_text = " ".join(pose.get("tags") or []) + " " + str(pose.get("title") or "") + " " + str(pose.get("emotion")
                                                                                              or "")
    return stems(tag_text), stems(pose.get("description"))


def score(pose: dict, text: str) -> float:
    words = stems(text)
    if not words:
        return 0.0
    tags, desc = pose_terms(pose)
    n = number_of("", pose.get("number_shown"))
    bonus = 0.0
    if n:
        names = {k for k, v in NUMBER_WORDS.items() if v == n}
        low = str(text or "").lower()
        if any(re.search(rf"\b{re.escape(w)}\b", low) for w in names | {str(n)}):
            bonus = 2.5
    return round(2.0 * len(tags & words) + 1.0 * len((desc - tags) & words) + bonus, 2)


def candidates(poses: list[dict], text: str, k: int = 8) -> list[tuple[dict, float]]:
    ranked = sorted(((p, score(p, text)) for p in poses), key=lambda item: (-item[1], -int(item[0].get("uses") or 0),
                                                                             int(item[0]["id"])))
    return ranked[:max(1, k)]


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def similar_pose(poses: list[dict], description: str, tags: Any = None, number_shown: Any = None,
                 threshold: float = SIMILAR_MIN) -> tuple[dict | None, float]:
    """Готовая поза, почти совпадающая с новой (Жаккар тегов или описания ≥ порога, то же число)."""
    want_num = number_of(description, number_shown)
    want_tags = stems(" ".join(_tags(tags)))
    want_desc = stems(description)
    best, best_sim = None, 0.0
    for pose in poses:
        if number_of(pose.get("description") or "", pose.get("number_shown")) != want_num:
            continue
        tag_s, desc_s = pose_terms(pose)
        sim = max(_jaccard(want_tags, tag_s) if want_tags else 0.0, _jaccard(want_desc, desc_s))
        if sim > best_sim:
            best, best_sim = pose, sim
    return (best, round(best_sim, 3)) if best is not None and best_sim >= threshold else (None, round(best_sim, 3))


# --------------------------------------------------------------------------- #
# Ролики
# --------------------------------------------------------------------------- #

VIDEO_JSON = {"script_json": ("script", {}), "beats_json": ("beats", []), "words_json": ("words", []),
              "timeline_json": ("timeline", {}), "options_json": ("options", {}), "notes_json": ("notes", [])}


def _video_view(row: Any, full: bool = True) -> dict:
    data = dict(row)
    timeline = _loads(data.get("timeline_json"), {})
    for col, (key, default) in VIDEO_JSON.items():
        raw = data.pop(col, None)
        if full or key in ("options", "notes"):
            data[key] = _loads(raw, default)
    data["status_label"] = STATUS_LABELS.get(data["status"], data["status"])
    data["editor_url"] = timeline.get("editor_url") if data.get("clip_id") else None
    return data


def get_video(store: Any, video_id: int, full: bool = True) -> dict:
    row = store.db.query_one("SELECT * FROM expl_videos WHERE id = ?", (int(video_id),))
    if not row:
        raise KeyError(f"нет ролика-объяснялки {video_id}")
    return _video_view(row, full)


def list_videos(store: Any, project_id: int | None = None, mascot_id: int | None = None,
                limit: int = 50) -> list[dict]:
    where, params = [], []
    if project_id is not None:
        where.append("project_id = ?")
        params.append(int(project_id))
    if mascot_id is not None:
        where.append("mascot_id = ?")
        params.append(int(mascot_id))
    sql = "SELECT * FROM expl_videos" + (" WHERE " + " AND ".join(where) if where else "")
    rows = store.db.query_all(sql + " ORDER BY id DESC LIMIT ?", (*params, max(1, min(int(limit), 500))))
    return [_video_view(r, full=False) for r in rows]


def update_video(store: Any, video_id: int, **fields: Any) -> dict:
    sets: dict[str, Any] = {}
    for key, value in fields.items():
        col = next((c for c, (k, _d) in VIDEO_JSON.items() if k == key), None)
        if col:
            sets[col] = _json(value)
        else:
            sets[key] = value
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE expl_videos SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(video_id)))
    return get_video(store, video_id)


def set_status(store: Any, video_id: int, status: str, expect: tuple[str, ...] | None = None, **fields: Any) -> bool:
    """Сменить статус (сравнить-и-записать: ``expect`` — из каких можно). False — ролик уже в другом."""
    status = P._choice(status, VIDEO_STATUSES, "status")
    sets: dict[str, Any] = {"status": status}
    for key, value in fields.items():
        col = next((c for c, (k, _d) in VIDEO_JSON.items() if k == key), None)
        if col:
            sets[col] = _json(value)
        else:
            sets[key] = value
    cols = ", ".join(f"{k} = ?" for k in sets)
    sql = f"UPDATE expl_videos SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?"
    args: list[Any] = [*sets.values(), int(video_id)]
    if expect:
        sql += f" AND status IN ({', '.join('?' for _ in expect)})"
        args += list(expect)
    return bool(store.db.execute(sql, args).rowcount)


def create_video(store: Any, mascot_id: int, topic: str, duration: Any = 45, tone: str = "",
                 options: dict | None = None, requested_by: str = "agent") -> dict:
    mascot = get_mascot(store, mascot_id)
    topic = P._long_text(topic, 600)
    if len(topic) < 3:
        raise ExplError("нужна тема ролика (topic)")
    try:
        seconds = float(duration or 45)
    except (TypeError, ValueError) as exc:
        raise ExplError("duration — секунды 30–60") from exc
    if not DURATION_MIN <= seconds <= DURATION_MAX:
        raise ExplError(f"duration {seconds:g} с: объяснялка {DURATION_MIN:.0f}–{DURATION_MAX:.0f} с")
    if sheet_path(store, mascot) is None:
        raise ExplError(f"у маскота «{mascot['name']}» нет листа (sheet) — загрузи картинку "
                        "(explainer_save_mascot sheet_asset_id) или нарисуй (explainer_generate_sheet)")
    cur = store.db.execute(
        "INSERT INTO expl_videos (project_id, mascot_id, topic, duration_target, tone, options_json, requested_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (int(mascot["project_id"]), int(mascot_id), topic, round(seconds, 1), P._text(tone, 60) or "дружелюбно",
         _json(options or {}), P._text(requested_by, 40)))
    return get_video(store, int(cur.lastrowid))


def delete_video(store: Any, video_id: int) -> dict:
    video = get_video(store, video_id, full=False)
    from app import aivideo_gen as G

    if video.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'ролик удалён' "
                         "WHERE approval_id = ? AND status IN ('awaiting', 'approved', 'held')",
                         (int(video["approval_id"]),))
    _drop_refs(store, int(video["project_id"]), "expl_video", int(video_id))
    store.db.execute("DELETE FROM expl_videos WHERE id = ?", (int(video_id),))
    G.kick()
    return {"ok": True, "deleted": int(video_id), "note": "клип (если собран) остался — удаляется отдельно"}


def video_group(video_id: int) -> str:
    return f"expl_video:{int(video_id)}"


def narration_text(video: dict) -> str:
    return str((video.get("script") or {}).get("text") or "").strip()


def narration_key(voice: dict, text: str) -> str:
    from app.aivideo_gen import _line_key

    return _line_key(voice, text)


def narration_ref(store: Any, video: dict) -> dict | None:
    """Готовая озвучка ролика тем же голосом и тем же текстом (иначе — нет)."""
    from app.aivideo_gen import _asset_path, _fresh

    mascot = get_mascot(store, int(video["mascot_id"]))
    voice = mascot_voice(store, mascot)
    ref = V.selected_ref(store, int(video["project_id"]), "expl_video", int(video["id"]), NARRATION_KIND)
    if not _fresh(ref) or ref["prompt"] != narration_key(voice, narration_text(video)):
        return None
    return ref if _asset_path(store, ref) else None


def beat_pose_ids(video: dict) -> list[int]:
    return [int(b["pose_id"]) for b in video.get("beats") or [] if b.get("pose_id")]


def overview(store: Any, project_id: int) -> dict:
    """Вкладка «Объяснялки»: маскоты с библиотекой поз, ролики, бюджет и карточки этапа."""
    from app import aivideo_gen as G

    project = V.require_project(store, project_id)
    mascots = []
    for m in list_mascots(store, project_id):
        m["poses"] = list_poses(store, int(m["id"]))
        mascots.append(m)
    return {"project": {k: project.get(k) for k in ("id", "name", "kind")},
            "provider": V._style_row(store, project_id)["provider"],
            "mascots": mascots, "videos": list_videos(store, project_id),
            "budget": G.budget_view(store, project_id),
            "approvals": [a for a in G.stage_approvals(store, project_id) if a.get("stage") == STAGE],
            "pilot_dir_ready": pilot_dir().is_dir()}


# --------------------------------------------------------------------------- #
# Расширение этапа генерации (app/aivideo_gen.py): позы, лист маскота, озвучка ролика
# --------------------------------------------------------------------------- #

POSE_PROMPT = (
    "Same character exactly as in the reference sheet: '{name}', {identity}. {style}. Vertical 9:16 frame, the "
    "character in full body, centered horizontally, filling the middle 55% of the frame height (empty space above "
    "and below for captions), plain flat solid {bg_name} background {bg}, no floor, no shadows on the background, "
    "no text, no letters, no numbers, no watermark. Pose: {pose}")
SHEET_PROMPT = (
    "Character model sheet of an original cartoon mascot '{name}': {identity}. {style}. Two rows on a plain flat "
    "solid {bg_name} background {bg}: top row — front, three-quarter, side and back views, full body; bottom row — "
    "four expressions (neutral, surprised, smug, happy), half body. The same character in every view, consistent "
    "proportions and colors. No text, no letters, no watermark.")
DEFAULT_STYLE = "Flat 2D vector cartoon, clean bold outlines, soft cel shading"


def _bg_name(color: str) -> str:
    return BG_NAMES.get(color.lower(), "single-color")


def _prompt_args(mascot: dict) -> dict:
    bg = mascot.get("bg_color") or DEFAULT_BG
    return {"name": mascot["name"], "identity": mascot["identity_card"].strip().rstrip("."),
            "style": (mascot.get("style_passport") or DEFAULT_STYLE).strip().rstrip("."), "bg": bg.upper(),
            "bg_name": _bg_name(bg)}


class _Extension:
    stage = STAGE
    label = "объяснялка: позы маскота и озвучка"
    target_types = ("pose", "mascot", "expl_video")
    ref_kinds = {POSE_KIND: "поза маскота", SHEET_KIND: "лист маскота", NARRATION_KIND: "озвучка объяснялки"}
    aspects = {POSE_KIND: "9:16", SHEET_KIND: "16:9"}

    @property
    def card_cap_rub(self) -> float:
        return float(settings.explainer_video_cap_rub)

    def project_of(self, store: Any, target_type: str, target_id: int) -> int:
        if target_type == "pose":
            return int(get_pose(store, target_id)["project_id"])
        if target_type == "mascot":
            return int(get_mascot(store, target_id)["project_id"])
        return int(get_video(store, target_id, full=False)["project_id"])

    def plan(self, store: Any, project_id: int, items: list, regenerate: bool) -> list[dict]:
        from app import aivideo_gen as G

        out: list[dict] = []
        if not items:
            raise ExplError("этап объяснялки — только с items: «expl_video:ID», «pose:ID», «mascot:ID»")
        for raw in items:
            target_type, target_id, kind, _slot = G._parse_item(raw)
            if target_type == "pose":
                pose = get_pose(store, target_id)
                if int(pose["project_id"]) != int(project_id):
                    raise ExplError("поза из другого проекта")
                if pose["ready"] and not regenerate:
                    continue
                out.append(self._pose_item(store, project_id, pose, regenerate))
            elif target_type == "mascot":
                mascot = get_mascot(store, target_id)
                if mascot["sheet"] and not regenerate:
                    continue
                out.append(self._image(store, project_id, "mascot", int(mascot["id"]), SHEET_KIND,
                                       f"{mascot['name']}: лист маскота", ""))
            elif target_type == "expl_video":
                video = get_video(store, target_id)
                if int(video["project_id"]) != int(project_id):
                    raise ExplError("ролик из другого проекта")
                group = video_group(target_id)
                if kind in ("", NARRATION_KIND):
                    item = self._narration_item(store, video, regenerate)
                    if item is not None:
                        out.append({**item, "group": group})
                if kind in ("", POSE_KIND):
                    seen = set()
                    for pid in beat_pose_ids(video):
                        if pid in seen:
                            continue
                        seen.add(pid)
                        pose = get_pose(store, pid)
                        if not pose["ready"]:
                            out.append({**self._pose_item(store, project_id, pose, False), "group": group})
            else:
                raise ExplError(f"элемент {raw!r}: «expl_video:ID[:narration|pose]», «pose:ID» или «mascot:ID»")
        return out

    def _image(self, store: Any, project_id: int, target_type: str, target_id: int, kind: str, label: str,
               group: str) -> dict:
        from app import aivideo_gen as G

        style = V.get_style(store, project_id)
        item = G._image_item(STAGE, target_type, target_id, kind, label, style["image_model"], style["quality"],
                             style["provider"])
        item["aspect"] = self.aspects.get(kind, "9:16")
        return {**item, "group": group} if group else item

    def _pose_item(self, store: Any, project_id: int, pose: dict, regenerate: bool) -> dict:
        mascot = get_mascot(store, int(pose["mascot_id"]))
        item = self._image(store, project_id, "pose", int(pose["id"]), POSE_KIND,
                           f"{mascot['name']}: поза «{pose['title'] or pose['slug']}»", "")
        item["regenerate"] = bool(regenerate)
        return item

    def _narration_item(self, store: Any, video: dict, regenerate: bool) -> dict | None:
        from app.aivideo_providers import get_tts

        text = narration_text(video)
        if not text:
            raise ExplError(f"у ролика {video['id']} нет текста — сначала сценарий")
        if not regenerate and narration_ref(store, video) is not None:
            return None
        mascot = get_mascot(store, int(video["mascot_id"]))
        voice = mascot_voice(store, mascot)
        tts = get_tts(voice["provider"], voice["model"], voice["voice"], voice.get("style"))
        price = tts.price_rub_per_char() if getattr(tts, "paid", False) else 0.0
        note = {"gemini": "Gemini TTS, бесплатно", "none": "сухой прогон"}.get(voice["provider"], "цена TTS из app/dubbing.py")
        return {"stage": STAGE, "kind": "tts", "target_type": "expl_video", "target_id": int(video["id"]),
                "ref_kind": NARRATION_KIND, "slot": "", "label": f"озвучка «{P._text(video['topic'], 60)}»",
                "provider": voice["provider"], "model": voice["model"], "voice": voice["voice"],
                **({"style": voice["style"]} if voice.get("style") else {}),
                "text": text, "chars": len(text), "estimate_rub": round(len(text) * price, 2), "price_note": note,
                "regenerate": bool(regenerate)}

    def build(self, store: Any, job: dict) -> tuple[str, list[Path], list[str], dict]:
        kind, target_id = job["ref_kind"], int(job["target_id"])
        if kind == POSE_KIND:
            pose = get_pose(store, target_id)
            mascot = get_mascot(store, int(pose["mascot_id"]))
            prompt = POSE_PROMPT.format(**_prompt_args(mascot), pose=pose["description"].strip())
            sheet = sheet_path(store, mascot)
            refs = [sheet] if sheet else []
            missing = [] if sheet else [f"{mascot['name']}: лист маскота"]
            caption = ["поза", mascot["name"], pose["title"] or pose["slug"]]
            return prompt, refs, caption, {"roles": [f"{mascot['slug']}: лист"] if sheet else [], "missing": missing}
        if kind == SHEET_KIND:
            mascot = get_mascot(store, target_id)
            prompt = SHEET_PROMPT.format(**_prompt_args(mascot))
            from app.aivideo_gen import _asset_path

            uploads = [p for p in (_asset_path(store, r) for r in V.list_refs(
                store, int(mascot["project_id"]), "mascot", target_id, "upload")) if p][-3:]
            return prompt, uploads, ["лист маскота", mascot["name"]], {"roles": [p.name for p in uploads],
                                                                       "missing": []}
        raise ExplError(f"неизвестный вид картинки объяснялки {kind}")

    def card_line(self, store: Any, project_id: int, est: dict) -> str:
        groups = {i.get("group") for i in est["items"] if i.get("group")}
        poses = [i for i in est["items"] if i["ref_kind"] == POSE_KIND]
        sheet = [i for i in est["items"] if i["ref_kind"] == SHEET_KIND]
        tts = [i for i in est["items"] if i["kind"] == "tts"]
        parts = []
        if poses:
            parts.append(f"новых поз {len(poses)} ≈ {sum(i['estimate_rub'] for i in poses):.2f} ₽")
        if sheet:
            parts.append(f"лист маскота ≈ {sum(i['estimate_rub'] for i in sheet):.2f} ₽")
        if tts:
            parts.append(f"озвучка {tts[0]['chars']} симв. ({tts[0]['model']}, {tts[0]['voice']}) ≈ "
                         f"{sum(i['estimate_rub'] for i in tts):.2f} ₽")
        head = "Объяснялка"
        if len(groups) == 1:
            try:
                video = get_video(store, int(next(iter(groups)).split(":")[1]))
                mascot = get_mascot(store, int(video["mascot_id"]))
                reused = len({b["pose_id"] for b in video.get("beats") or [] if b.get("pose_id")}) - len(poses)
                head = (f"Объяснялка «{P._text(video['topic'], 80)}» (маскот {mascot['name']}, "
                        f"{video['duration_target']:.0f} с; поз из библиотеки: {max(0, reused)})")
            except (KeyError, ValueError, IndexError):
                pass
        return f"{head}: " + "; ".join(parts) + f" — итого ≈ {est['total_rub']:.2f} ₽."


_REGISTERED = False


def _register() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    from app import aivideo_gen as G

    ext = _Extension()
    G.register_extension(ext)
    G.REF_ASPECT.update(ext.aspects)
    _REGISTERED = True


def request_pose(store: Any, pose_id: int, regenerate: bool = False, requested_by: str = "agent",
                 note: str = "") -> dict:
    """Нарисовать позу (нет картинки) или перерисовать (``regenerate``): своя маленькая карточка."""
    from app import aivideo_gen as G

    pose = get_pose(store, pose_id)
    if pose["ready"] and not regenerate:
        return {"ok": True, "status": "nothing", "note": "у позы уже есть картинка — regenerate=true для новой версии"}
    return G.request_stage(store, int(pose["project_id"]), STAGE, None, [f"pose:{int(pose_id)}"],
                           regenerate=regenerate, requested_by=requested_by, note=note)


def request_sheet(store: Any, mascot_id: int, regenerate: bool = False, requested_by: str = "agent",
                  note: str = "") -> dict:
    from app import aivideo_gen as G

    mascot = get_mascot(store, mascot_id)
    return G.request_stage(store, int(mascot["project_id"]), STAGE, None, [f"mascot:{int(mascot_id)}"],
                           regenerate=regenerate, requested_by=requested_by, note=note)


# --------------------------------------------------------------------------- #
# Пилот владельца: маскот «Ух» и его позы (D:\vvf-data\runtime\explainer = /data/runtime/explainer)
# --------------------------------------------------------------------------- #


def pilot_dir() -> Path:
    return settings.runtime_dir / "explainer"


def _copy_asset(store: Any, src: Path, label: str, description: str) -> dict:
    """Файл → «Файлы для монтажа» (копия в каталог библиотеки, как загрузка)."""
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"{uuid4().hex}-expl-{V.slugify(src.stem, 'img')}{src.suffix.lower() or '.jpg'}"
    shutil.copyfile(src, dest)
    meta = probe_asset(dest)
    mime = "image/png" if dest.suffix == ".png" else "image/jpeg"
    return store.create_montage_asset(kind="image", label=label[:120], description=description[:500],
                                      tags="объяснялка,маскот", file_path=str(dest), original_filename=src.name,
                                      mime_type=mime, size_bytes=dest.stat().st_size, **meta)


def import_pilot(store: Any, project_id: int | None = None, directory: str | Path | None = None) -> dict:
    """Маскот «Ух» и 18 поз пилота (клип 887) → библиотека. Повторный вызов не дублирует: позы
    ищутся по slug. ``project_id`` не задан — создаётся проект «ИИ-видео» «Объяснялки: Ух» (Polza;
    платное всё равно только по карточке владельца)."""
    from app.explainer_pilot import MASCOT, POSES, SHEET_FILE

    folder = Path(directory) if directory else pilot_dir()
    if not folder.is_dir():
        raise ExplError(f"нет папки пилота {folder} (в контейнере /data/runtime/explainer)")
    if project_id is None:
        existing = next((m for m in list_mascots(store) if m["slug"] == MASCOT["slug"]), None)
        if existing:
            project_id = int(existing["project_id"])
        else:
            project_id = int(V.create_ai_project(store, "Объяснялки: Ух", style_kind="anime",
                                                 passport_text=MASCOT["style_passport"],
                                                 palette=[MASCOT["bg_color"], MASCOT["accent_color"]],
                                                 provider="polza",
                                                 description="Маскот-объяснялки: сова Ух объясняет темы за 45 с "
                                                             "(пилот — клип 887)")["project"]["id"])
    V.require_project(store, int(project_id))
    mascot = next((m for m in list_mascots(store, project_id) if m["slug"] == MASCOT["slug"]), None)
    fields = {k: MASCOT[k] for k in ("name", "identity_card", "persona", "style_passport", "bg_color",
                                     "accent_color", "chip_color", "outline_color", "voice", "captions_style",
                                     "notes")}
    music = MASCOT.get("music_track_id")
    if music:
        try:
            store.get_audio_track(int(music))
            fields["music_track_id"] = int(music)
        except KeyError:
            pass
    if mascot is None:
        mascot = save_mascot(store, int(project_id), slug=MASCOT["slug"], **fields)
    notes = []
    if not mascot["sheet"]:
        sheet = folder / SHEET_FILE
        if sheet.is_file():
            asset = _copy_asset(store, sheet, f"Маскот {MASCOT['name']}: лист", "лист маскота пилота")
            mascot = set_sheet(store, int(mascot["id"]), int(asset["id"]))
        else:
            notes.append(f"нет листа {sheet.name} — загрузи лист маскота")
    have = {p["slug"]: p for p in list_poses(store, int(mascot["id"]))}
    added, skipped = [], []
    for spec in POSES:
        if spec["slug"] in have and have[spec["slug"]]["ready"]:
            skipped.append(spec["slug"])
            continue
        src = folder / spec["file"]
        if not src.is_file():
            notes.append(f"нет файла {spec['file']}")
            continue
        asset = _copy_asset(store, src, f"Маскот {MASCOT['name']}: {spec['title']}",
                            f"поза пилота: {spec['description']}")
        if spec["slug"] in have:
            pose = save_pose(store, pose_id=int(have[spec["slug"]]["id"]), asset_id=int(asset["id"]), origin="pilot")
        else:
            pose = save_pose(store, int(mascot["id"]), description=spec["description"], title=spec["title"],
                             tags=spec["tags"], emotion=spec.get("emotion"), number_shown=spec.get("number"),
                             origin="pilot", asset_id=int(asset["id"]), slug=spec["slug"])
        added.append({"pose_id": pose["id"], "slug": pose["slug"], "title": pose["title"]})
    return {"ok": True, "project_id": int(project_id), "mascot_id": int(mascot["id"]), "added": added,
            "skipped": skipped, "notes": notes, "poses_total": len(list_poses(store, int(mascot["id"]))),
            "note": "маскот и позы пилота в библиотеке — новый ролик переиспользует их (explainer_create)"}
