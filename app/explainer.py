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
3 ₽ nano-banana 1K), лист маскота и озвучка Polza — карточкой владельцу; Gemini TTS — сразу, 0 ₽.

Голос у маскота один на все ролики (решение владельца: «голос чтобы был единый на всех видео»): озвучивает всегда
выбранный голос маскота, подмены нет (``mascot_voice``, ``describe_voice``). Голос Polza платный, значит любой ролик
такого маскота останавливается на карточке с суммой, что бы ни стояло в проекте; бесплатно только голос Gemini.
Не выбран голос вообще — Gemini. Провайдер проекта ``none`` — НЕ сухой прогон: это значение по умолчанию у любого
проекта, новые позы и платный голос в нём тоже идут по карточке. Сухой прогон (тон вместо
речи, заглушки вместо поз, клип с пометкой «Черновик») — только по явной опции ролика ``options.dry_run``:
её ставят тесты и REST-запрос, инструмента агента для неё нет. Заглушка позы не считается готовой позой
(``placeholder``): настоящий ролик её не возьмёт, а попросит нарисовать.

Где что лежит. Здесь — видео, расширение ИИ-видео и пилот. Вынесено: маскоты и позы ``app/explainer_mascots.py``, подбор поз ``app/explainer_match.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import aivideo as V
from app import projects as P
from app.settings import settings

from app.explainer_mascots import (  # noqa: F401
    POSE_KIND, SHEET_KIND, ORIGINS, MAX_TAGS, NUMBER_WORDS, _NUM_RE, ExplError, _loads, _json, _hex, _tags, _voice,
    _mascot_view, get_mascot, list_mascots, MASCOT_FIELDS, save_mascot, delete_mascot, _drop_refs, set_sheet,
    sheet_path, dry_voice, mascot_voice, describe_voice, number_of, _dry_jobs, pose_usable, _pose_view,
    _selected_pose_refs, get_pose, list_poses, save_pose, delete_pose, mark_used, is_dry,
)
from app.explainer_match import (  # noqa: F401
    SIMILAR_MIN, _WORD_RE, _EN_STOP, stems, pose_terms, score, candidates, _jaccard, similar_pose,
)

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


DEFAULT_BG = "#2a1b47"
DRAW_PROVIDER = "polza"  # кто рисует новую позу в проекте с провайдером none (только по карточке)
DRY_MARK = "Черновик без голоса"
BG_NAMES = {"#2a1b47": "dark purple"}


def ensure_explainer_schema(conn) -> None:
    """Таблицы объяснялок (из ``Database.init`` после ИИ-видео) + расширение этапа генерации."""
    for sql in EXPL_SCHEMA_SQL:
        conn.execute(sql)
    _register()


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
    data["editor_url"] = f"/clips/{int(data['clip_id'])}" if data.get("clip_id") else None
    # Для строки ролика в списке: черновик ли, чем озвучен и каких поз ещё нет.
    data["dry_run"] = is_dry(data)
    data["voice_used"] = timeline.get("voice")
    beats = data["beats"] if "beats" in data else _loads(row["beats_json"], [])
    data["poses_to_draw"] = (sorted({str(b.get("pose_title") or "поза без названия") for b in beats if b.get("pose_new")})
                             if data["status"] in ("planned", "awaiting_approval", "generating") else [])
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
    voice = mascot_voice(store, mascot, is_dry(video))
    ref = V.selected_ref(store, int(video["project_id"]), "expl_video", int(video["id"]), NARRATION_KIND)
    if not _fresh(ref) or ref["prompt"] != narration_key(voice, narration_text(video)):
        return None
    return ref if _asset_path(store, ref) else None


def beat_pose_ids(video: dict) -> list[int]:
    return [int(b["pose_id"]) for b in video.get("beats") or [] if b.get("pose_id")]


def overview(store: Any, project_id: int) -> dict:
    """Вкладка «Объяснялки»: маскоты с библиотекой поз, ролики, бюджет и карточки этапа."""
    from app import aivideo_gen as G

    project = P.get_project(store, int(project_id), include_sources=False)  # маскот — в проекте любого вида
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
    label = "позы маскота и озвучка"
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
                dry = is_dry(video)
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
                        if not pose_usable(pose, dry):
                            out.append({**self._pose_item(store, project_id, pose, False, dry), "group": group})
            else:
                raise ExplError(f"элемент {raw!r}: «expl_video:ID[:narration|pose]», «pose:ID» или «mascot:ID»")
        return out

    def _image(self, store: Any, project_id: int, target_type: str, target_id: int, kind: str, label: str,
               group: str, dry: bool = False) -> dict:
        from app import aivideo_gen as G

        style = V.get_style(store, project_id)
        # Заглушка — только в черновике. Провайдер none (значение по умолчанию у любого проекта) — не повод
        # выдать градиент за позу: настоящая картинка платная, значит карточка владельцу с суммой.
        provider = "none" if dry else (style["provider"] if style["provider"] != "none" else DRAW_PROVIDER)
        item = G._image_item(STAGE, target_type, target_id, kind, label, style["image_model"], style["quality"],
                             provider)
        item["aspect"] = self.aspects.get(kind, "9:16")
        return {**item, "group": group} if group else item

    def _pose_item(self, store: Any, project_id: int, pose: dict, regenerate: bool, dry: bool = False) -> dict:
        mascot = get_mascot(store, int(pose["mascot_id"]))
        item = self._image(store, project_id, "pose", int(pose["id"]), POSE_KIND,
                           f"{mascot['name']}: поза «{pose['title'] or pose['slug']}»", "", dry)
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
        voice = mascot_voice(store, mascot, is_dry(video))
        tts = get_tts(voice["provider"], voice["model"], voice["voice"], voice.get("style"))
        price = tts.price_rub_per_char() if getattr(tts, "paid", False) else 0.0
        note = {"gemini": "Gemini TTS, бесплатно", "none": "сухой прогон, тон вместо речи"}.get(
            voice["provider"], "цена TTS из app/dubbing.py")
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
        paid_voice = next((i for i in tts if i["provider"] not in ("gemini", "none")), None)
        tail = (f"\nГолос маскота {paid_voice['voice']} платный, он один на все ролики маскота. Не хотите платить: "
                "откажите, смените голос маскота и нажмите «Продолжить» у ролика." if paid_voice else "")
        if len(groups) == 1:
            try:
                video = get_video(store, int(next(iter(groups)).split(":")[1]))
                mascot = get_mascot(store, int(video["mascot_id"]))
                reused = len({b["pose_id"] for b in video.get("beats") or [] if b.get("pose_id")}) - len(poses)
                head = (f"Объяснялка «{P._text(video['topic'], 80)}» (маскот {mascot['name']}, "
                        f"{video['duration_target']:.0f} с; поз из библиотеки: {max(0, reused)})")
            except (KeyError, ValueError, IndexError):
                pass
        return f"{head}: " + "; ".join(parts) + f" — итого ≈ {est['total_rub']:.2f} ₽." + tail


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


def _copy_asset(store: Any, src: Path, label: str, description: str, project_id: int | None = None) -> dict:
    """Файл → «Файлы для монтажа» (копия в каталог библиотеки, как загрузка)."""
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"{uuid4().hex}-expl-{V.slugify(src.stem, 'img')}{src.suffix.lower() or '.jpg'}"
    shutil.copyfile(src, dest)
    meta = probe_asset(dest)
    mime = "image/png" if dest.suffix == ".png" else "image/jpeg"
    return store.create_montage_asset(kind="image", label=label[:120], description=description[:500],
                                      tags="объяснялка,маскот", file_path=str(dest), original_filename=src.name,
                                      mime_type=mime, size_bytes=dest.stat().st_size, project_id=project_id, **meta)


def import_pilot(store: Any, project_id: int | None = None, directory: str | Path | None = None) -> dict:
    """Маскот «Ух» и 18 поз пилота (клип 887) → библиотека. Повторный вызов не дублирует: позы
    ищутся по slug. ``project_id`` — проект любого вида; не задан — создаётся проект «ИИ-видео»
    «Объяснялки: Ух» (Polza; платное всё равно только по карточке владельца). Голос — тот же, что у
    первых роликов (``explainer_pilot.MASCOT``): озвучка платная, по карточке."""
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
    P.get_project(store, int(project_id), include_sources=False)
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
            asset = _copy_asset(store, sheet, f"Маскот {MASCOT['name']}: лист", "лист маскота пилота", int(project_id))
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
                            f"поза пилота: {spec['description']}", int(project_id))
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
