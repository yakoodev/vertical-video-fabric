"""«Клип на песню» как работа сервиса: данные задания, план клипа и деньги за картинки (docs/music-video.md,
плейбук docs/playbooks/music-video.md).

Пресет владельца 13.11 (song15 «paper» — буквы-вырезки на рваной бумаге, song2 «comic» — плоский комикс): песня
владельца (никакой ИИ-музыки), участок start–end, большие слова бьют в атаки слогов вокала, склейки на долях, сцены —
картинки nano-banana в 2.5D (Blender). Раньше — только CLI ``tools/music_video/make.py`` вне сервиса. Теперь задание
``mv_jobs`` ведёт работник (app/music_video_job.py) по шагам, каждый шаг переживает перезапуск:

``new`` (разбор песни: текст по слогам — app/lyrics_audio.py, сетка долей — tools/music_video/audio.py) →
``analyzed`` (черновик плана) → ``planned`` (или ``plan_review`` — ждёт правки плана агентом) → оценка картинок →
``awaiting_approval`` (карточка владельцу) → ``generating`` → ``images_ready`` → ``prepared`` (текстуры и scene.json)
→ ``rendered`` (кадры Blender здесь; нет его — ``remote_render``: очередь хост-воркера app/render_queue.py,
``tools/render_worker.py`` на компьютере с Blender рисует и отдаёт mp4; очередь выключена — ``needs_blender``) →
``done`` (mp4 — клип проекта).

Деньги — расширение этапа ``music_video`` у app/aivideo_gen.py (как позы объяснялки): картинка ≈ 3 ₽ (nano-banana 1K),
одна карточка на задание; настройка проекта «Тратить без подтверждения» одобряет её сама в своих пределах, потолки
ИИ-видео (AIVIDEO_ENABLED, месяц проекта, MUSIC_VIDEO_CAP_RUB на задание) — те же; без «да» (кнопкой или настройкой)
платный запуск не идёт — это охраняет сам конвейер (``_preflight``). Сверху — потолок задания ``max_rub``: смета
больше — карточка не отправляется. Своя картинка (``asset_id`` в плане) и сухой прогон (``options.dry_run``, только
REST и тесты — заглушки вместо картинок) — 0 ₽, без карточки. Тот же промпт второй раз не оплачивается (кэш запусков).

Картинки — версии ``aiv_refs``: цель ``mv_job`` (id задания), слот — id картинки плана; ``mv_image`` — фон или
персонаж, ``mv_pose`` — новая поза с референсом (``refs`` — только на картинки без своих refs: позы ждут основу).
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from app import projects as P
from app.settings import settings

STAGE = "music_video"
TARGET = "mv_job"
IMAGE_KIND = "mv_image"
POSE_KIND = "mv_pose"
STYLES = ("comic", "paper")
STYLE_LABELS = {"comic": "комикс", "paper": "рваная бумага"}
DRAW_PROVIDER = "polza"
DEFAULT_IMAGES = 6
MAX_IMAGES = 40
MIN_SEC = 4.0
MAX_SEC = 240.0
MAX_LYRICS = 12000
LANGS = ("", "ru", "en", "uk", "es", "de", "fr", "pt", "it", "ja", "ko")

STATUS_LABELS = {
    "new": "разбор песни", "analyzed": "черновик плана", "plan_review": "ждёт правки плана", "planned": "оценка картинок",
    "awaiting_approval": "ждёт одобрения картинок", "generating": "рисуются картинки", "images_ready": "текстуры и сцены",
    "prepared": "рендер кадров", "remote_render": "рендер на компьютере с Blender", "needs_blender": "нужен Blender",
    "rendered": "сборка mp4", "done": "готово",
    "failed": "ошибка", "cancelled": "отменено",
}
# Работник ведёт эти статусы сам; plan_review ждёт агента (music_video_continue), остальные — конец.
ACTIVE_STATUSES = ("new", "analyzed", "planned", "awaiting_approval", "generating", "images_ready", "prepared",
                   "remote_render", "needs_blender", "rendered")
TERMINAL = ("done", "failed", "cancelled")
# Доля готовности на входе в шаг (рендер внутри — по кадрам).
PROGRESS = {"new": 0.0, "analyzed": 0.12, "plan_review": 0.15, "planned": 0.15, "awaiting_approval": 0.18,
            "generating": 0.2, "images_ready": 0.35, "prepared": 0.42, "remote_render": 0.42, "needs_blender": 0.42,
            "rendered": 0.9,
            "done": 1.0}


class MvError(ValueError):
    """Понятный отказ (вход, план, деньги) — текст для владельца и агента."""


MV_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS mv_jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        track_id INTEGER,
        title TEXT NOT NULL DEFAULT '',
        style TEXT NOT NULL DEFAULT 'comic',
        start_sec REAL NOT NULL DEFAULT 0,
        end_sec REAL,
        max_rub REAL NOT NULL DEFAULT 0,
        lang TEXT NOT NULL DEFAULT '',
        options_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL DEFAULT 'new',
        message TEXT NOT NULL DEFAULT '',
        approval_id INTEGER,
        estimate_rub REAL NOT NULL DEFAULT 0,
        clip_id INTEGER,
        clip_plan_id INTEGER,
        error TEXT NOT NULL DEFAULT '',
        notes_json TEXT NOT NULL DEFAULT '[]',
        requested_by TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_mv_jobs_project ON mv_jobs(project_id, id)",
    "CREATE INDEX IF NOT EXISTS idx_mv_jobs_status ON mv_jobs(status)",
]


def ensure_music_video_schema(conn) -> None:
    """Таблица заданий (из ``Database.init``) + расширение этапа генерации."""
    for sql in MV_SCHEMA_SQL:
        conn.execute(sql)
    _register()


# --------------------------------------------------------------------------- #
# Инструмент tools/music_video (модули грузятся по пути: имена plan/images/audio слишком общие для sys.modules)
# --------------------------------------------------------------------------- #


def tools_dir() -> Path:
    raw = os.getenv("MV_TOOLS_DIR", "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "tools" / "music_video"


def tool_module(name: str) -> Any:
    """Модуль tools/music_video/<name>.py под именем ``mv_tool_<name>`` (только чистые: plan, images, audio)."""
    key = f"mv_tool_{name}"
    if key in sys.modules:
        return sys.modules[key]
    path = tools_dir() / f"{name}.py"
    if not path.is_file():
        raise MvError(f"нет инструмента клипа на песню: {path} (tools/music_video не в образе?)")
    spec = importlib.util.spec_from_file_location(key, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception:
        sys.modules.pop(key, None)
        raise
    return module


def card_cap_rub() -> float:
    try:
        return float(os.getenv("MUSIC_VIDEO_CAP_RUB", "200") or 200)
    except ValueError:
        return 200.0


# --------------------------------------------------------------------------- #
# Задание
# --------------------------------------------------------------------------- #


def _loads(raw: Any, default: Any) -> Any:
    try:
        value = json.loads(raw) if isinstance(raw, str) and raw else default
    except ValueError:
        return default
    return value if isinstance(value, type(default)) else default


def _view(row: Any) -> dict:
    data = dict(row)
    data["options"] = _loads(data.pop("options_json", "{}"), {})
    data["notes"] = _loads(data.pop("notes_json", "[]"), [])
    data["status_label"] = STATUS_LABELS.get(data["status"], data["status"])
    data["style_label"] = STYLE_LABELS.get(data["style"], data["style"])
    return data


def get_job(store: Any, job_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM mv_jobs WHERE id = ?", (int(job_id),))
    if not row:
        raise KeyError(f"нет задания клипа на песню #{job_id}")
    return _view(row)


def list_jobs(store: Any, project_id: int | None = None, limit: int = 20) -> list[dict]:
    if project_id is None:
        rows = store.db.query_all("SELECT * FROM mv_jobs ORDER BY id DESC LIMIT ?", (max(1, int(limit)),))
    else:
        rows = store.db.query_all("SELECT * FROM mv_jobs WHERE project_id = ? ORDER BY id DESC LIMIT ?",
                                  (int(project_id), max(1, int(limit))))
    return [_view(r) for r in rows]


_FIELDS = {"status", "message", "approval_id", "estimate_rub", "clip_id", "clip_plan_id", "error", "options",
           "notes", "title"}


def update_job(store: Any, job_id: int, **fields: Any) -> dict:
    sets, params = [], []
    for key, value in fields.items():
        if key not in _FIELDS:
            raise KeyError(key)
        if key in ("options", "notes"):
            key, value = f"{key}_json", json.dumps(value, ensure_ascii=False)
        sets.append(f"{key} = ?")
        params.append(value)
    if sets:
        store.db.execute(f"UPDATE mv_jobs SET {', '.join(sets)}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*params, int(job_id)))
    return get_job(store, job_id)


def set_status(store: Any, job_id: int, status: str, expect: tuple[str, ...] | None = None, **fields: Any) -> bool:
    """Сменить статус («сравнить-и-записать»: ``expect`` — из каких можно). True — записано."""
    if status not in STATUS_LABELS:
        raise ValueError(status)
    where, params = "id = ?", [int(job_id)]
    if expect:
        where += f" AND status IN ({', '.join('?' for _ in expect)})"
        params += list(expect)
    extra = ""
    values: list[Any] = [status]
    for key, value in fields.items():
        if key not in _FIELDS or key in ("status", "options", "notes"):
            raise KeyError(key)
        extra += f", {key} = ?"
        values.append(value)
    cur = store.db.execute(f"UPDATE mv_jobs SET status = ?{extra}, updated_at = CURRENT_TIMESTAMP WHERE {where}",
                           (*values, *params))
    return bool(cur.rowcount)


def job_dir(job: dict) -> Path:
    """Папка задания (OUT инструмента): plan.json, analysis.json, work/…, итоговый mp4 до переноса в клипы."""
    folder = Path(settings.runtime_dir) / "music_video" / f"mv{int(job['id'])}"
    (folder / "work").mkdir(parents=True, exist_ok=True)
    return folder


def is_dry(job: dict) -> bool:
    return bool((job.get("options") or {}).get("dry_run"))


def _num(value: Any, name: str, default: float | None = None) -> float | None:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise MvError(f"{name} — число секунд") from exc


def create_job(store: Any, project_id: int, *, track_id: int | None = None, music_url: str = "", style: str = "comic",
               start: Any = 0.0, end: Any = None, max_rub: Any = 60.0, lyrics: str = "", lang: str = "",
               title: str = "", images: Any = None, review_plan: bool = False, draft: bool = False,
               dry_run: bool = False, requested_by: str = "agent") -> dict:
    """Новое задание (проверка входа сразу, работа — фоном). Текст песни сохраняется у трека (как у «Мувика»)."""
    from app import frag_movie as F

    P.get_project(store, int(project_id), include_sources=False)
    style = str(style or "comic").strip().lower()
    if style not in STYLES:
        raise MvError("style — comic (плоский комикс) или paper (рваная бумага)")
    lang = str(lang or "").strip().lower()
    if lang not in LANGS:
        raise MvError(f"язык песни — один из {', '.join(x for x in LANGS if x)} (или пусто — сам)")
    if track_id in (None, "", 0) and str(music_url or "").strip():
        from app.music_import import import_music_from_url

        track_id = int(import_music_from_url(store, str(music_url).strip(), "")["id"])
    if track_id in (None, "", 0):
        raise MvError("нужна песня: track_id из музыки (list_music), файл или ссылка — своя песня, не ИИ-музыка")
    track = store.get_audio_track(int(track_id))
    path = Path(str(track.get("file_path") or ""))
    if not path.is_file():
        raise MvError(f"у трека #{track['id']} нет файла")
    start_sec = max(0.0, _num(start, "start", 0.0) or 0.0)
    end_sec = _num(end, "end")
    duration = float(track.get("duration_sec") or 0) or None
    if end_sec is None:
        end_sec = duration
    if end_sec is not None and duration:
        end_sec = min(end_sec, duration)
    if end_sec is not None and end_sec - start_sec < MIN_SEC:
        raise MvError(f"участок песни короче {MIN_SEC:g} с (start {start_sec:g}, end {end_sec:g})")
    if end_sec is not None and end_sec - start_sec > MAX_SEC:
        raise MvError(f"участок длиннее {MAX_SEC:g} с — раздели песню на клипы (start/end)")
    budget = _num(max_rub, "max_rub", 0.0) or 0.0
    if budget < 0:
        raise MvError("max_rub — неотрицательная сумма")
    count = int(images) if images not in (None, "", 0) else DEFAULT_IMAGES
    if not 1 <= count <= MAX_IMAGES:
        raise MvError(f"images — от 1 до {MAX_IMAGES} картинок")
    text = str(lyrics or "").replace("\r", "").strip()
    if len(text) > MAX_LYRICS:
        raise MvError(f"текст песни длиннее {MAX_LYRICS} знаков")
    if text:
        F.save_track_lyrics(store, int(track["id"]), text, lang)
    label = str(track.get("label") or track.get("original_filename") or f"трек {track['id']}")
    name = " ".join(str(title or "").split())[:100] or F.song_title(label) or label[:100]
    options = {"images": count, "review_plan": bool(review_plan), "draft": bool(draft), "track_label": label}
    if dry_run:
        options["dry_run"] = True
    cur = store.db.execute(
        "INSERT INTO mv_jobs (project_id, track_id, title, style, start_sec, end_sec, max_rub, lang, options_json, "
        "requested_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), int(track["id"]), name, style, round(start_sec, 3),
         None if end_sec is None else round(end_sec, 3), round(budget, 2), lang,
         json.dumps(options, ensure_ascii=False), str(requested_by or "")[:40]))
    return get_job(store, int(cur.lastrowid))


# --------------------------------------------------------------------------- #
# План клипа (OUT/plan.json инструмента)
# --------------------------------------------------------------------------- #

PLAN_KINDS = ("plate", "cutout")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def plan_path(job: dict) -> Path:
    return job_dir(job) / "plan.json"


def load_plan(job: dict) -> dict | None:
    path = plan_path(job)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def image_kind(plan: dict, iid: str) -> str:
    return POSE_KIND if (plan.get("images", {}).get(iid) or {}).get("refs") else IMAGE_KIND


def validate_plan(plan: Any, style: str = "") -> dict:
    """План — как у CLI (docs/music-video.md): images {id: {kind plate|cutout, prompt, refs?, asset_id?, ar?}},
    scenes [...] (сцены проверяет prep). refs — только на картинки без своих refs."""
    if not isinstance(plan, dict):
        raise MvError("план — объект JSON (images, scenes, …) как в docs/music-video.md")
    images = plan.get("images")
    if not isinstance(images, dict) or not images:
        raise MvError("в плане нет images {id: {kind, prompt}}")
    if len(images) > MAX_IMAGES:
        raise MvError(f"картинок в плане {len(images)} > {MAX_IMAGES}")
    for iid, spec in images.items():
        if not _ID_RE.match(str(iid)):
            raise MvError(f"id картинки «{iid}» — латиница, цифры, _ и -")
        if not isinstance(spec, dict):
            raise MvError(f"картинка {iid}: объект {{kind, prompt}}")
        if spec.get("kind", "plate") not in PLAN_KINDS:
            raise MvError(f"картинка {iid}: kind plate (фон) или cutout (персонаж на хромакее)")
        if not spec.get("asset_id") and not str(spec.get("prompt") or "").strip():
            raise MvError(f"картинка {iid}: нет prompt (или asset_id своей картинки)")
        if "TODO" in str(spec.get("prompt") or ""):
            raise MvError(f"картинка {iid}: в промпте осталось TODO")
        for ref in spec.get("refs") or []:
            base = images.get(ref)
            if base is None:
                raise MvError(f"картинка {iid}: refs → нет картинки {ref}")
            if base.get("refs"):
                raise MvError(f"картинка {iid}: refs → {ref}, у которой свои refs; новая поза — от исходной картинки")
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise MvError("в плане нет scenes")
    if style and plan.get("style") and plan["style"] != style:
        raise MvError(f"style плана {plan['style']} ≠ стиля задания {style}")
    return plan


def save_plan(job: dict, plan: dict) -> dict:
    plan = validate_plan(plan, job["style"])
    plan.setdefault("style", job["style"])
    path = plan_path(job)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    return plan


def style_prompt(plan: dict, iid: str) -> str:
    """Промпт картинки — тем же кодом, что у CLI (tools/music_video/images.style_prompt)."""
    return tool_module("images").style_prompt(plan, plan["images"][iid])


def _scene_text(an: dict, scene: dict) -> str:
    words = an.get("words") or []
    cap = scene.get("captions") or {}
    a, b = str(cap.get("from") or ""), str(cap.get("to") or "")
    if not (a.startswith("w:") and b.startswith("w:")):
        return ""
    try:
        i0, i1 = int(float(a[2:].split("-")[0].split("+")[0])), int(float(b[2:].split("-")[0].split("+")[0]))
    except ValueError:
        return ""
    return " ".join(str(w.get("w") or "") for w in words[i0:i1 + 1]).strip()


PLATE_SUBJECT = {
    "comic": "A funny full-bleed cartoon background scene for a music video",
    "paper": "A moody cinematic background scene for a music video",
}


def auto_prompts(plan: dict, an: dict) -> dict:
    """Черновик: у каждого фона — промпт по строкам песни сцен, где он стоит (слова — тема, не надпись)."""
    style = plan.get("style", "comic")
    by_image: dict[str, list[str]] = {}
    for scene in plan.get("scenes") or []:
        bg = scene.get("bg")
        if isinstance(bg, str):
            text = _scene_text(an, scene)
            if text:
                by_image.setdefault(bg, []).append(text)
    for n, (iid, spec) in enumerate(plan.get("images", {}).items()):
        if spec.get("prompt") and "TODO" not in spec["prompt"]:
            continue
        lines = " / ".join(dict.fromkeys(by_image.get(iid) or []))[:400]
        subject = PLATE_SUBJECT.get(style, PLATE_SUBJECT["comic"])
        spec["prompt"] = (f"{subject}, picturing what these sung lyrics are about (show it, never write the words): "
                          f"«{lines}». Clear focal subject in the middle, room for big lyrics at the top."
                          if lines else f"{subject}: an atmospheric location #{n + 1} that fits the song, clear "
                                        "focal subject in the middle, room for big lyrics at the top.")
    return plan


def draft_plan(job: dict, an: dict) -> dict:
    plan = tool_module("plan").draft(an, job["style"], int((job.get("options") or {}).get("images") or DEFAULT_IMAGES))
    return auto_prompts(plan, an)


# --------------------------------------------------------------------------- #
# Расширение этапа генерации (app/aivideo_gen.py): картинки плана
# --------------------------------------------------------------------------- #


def group_of(job_id: int) -> str:
    return f"{TARGET}:{int(job_id)}"


def image_ref(store: Any, job: dict, iid: str, plan: dict | None = None) -> dict | None:
    from app import aivideo as V

    plan = plan or load_plan(job) or {}
    return V.selected_ref(store, int(job["project_id"]), TARGET, int(job["id"]), image_kind(plan, iid), iid)


def own_asset_path(store: Any, job: dict, spec: dict) -> Path | None:
    """Своя картинка плана (``asset_id``): файл из «Файлов для монтажа» этого проекта или общих."""
    from app.asset_scope import allowed, refusal

    try:
        asset = store.get_montage_asset(int(spec["asset_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise MvError(f"нет файла asset_id {spec.get('asset_id')}") from exc
    if not allowed(asset, int(job["project_id"])):
        raise MvError(refusal(store, asset, int(job["project_id"]), "клип на песню"))
    path = Path(str(asset.get("file_path") or ""))
    return path if path.is_file() else None


class _Extension:
    stage = STAGE
    label = "картинки клипа на песню"
    card_prefix = "Клип на песню"
    cap_hint = "меньше картинок в плане (images) или свои картинки (asset_id)"
    target_types = (TARGET,)
    ref_kinds = {IMAGE_KIND: "картинка клипа на песню", POSE_KIND: "поза персонажа клипа на песню"}
    aspects = {IMAGE_KIND: "9:16", POSE_KIND: "9:16"}

    @property
    def card_cap_rub(self) -> float:
        return card_cap_rub()

    def project_of(self, store: Any, target_type: str, target_id: int) -> int:
        return int(get_job(store, target_id)["project_id"])

    def plan(self, store: Any, project_id: int, items: list, regenerate: bool) -> list[dict]:
        from app import aivideo as V
        from app import aivideo_gen as G
        from app.aivideo_providers import image_price

        if not items:
            raise MvError("этап клипа на песню — только с items: «mv_job:ID» или «mv_job:ID:mv_image:<картинка>»")
        out: list[dict] = []
        for raw in items:
            target_type, target_id, _kind, slot = G._parse_item(raw)
            if target_type != TARGET:
                raise MvError(f"элемент {raw!r}: «mv_job:ID[:вид:картинка]»")
            job = get_job(store, target_id)
            if int(job["project_id"]) != int(project_id):
                raise MvError("задание клипа на песню из другого проекта")
            plan = load_plan(job)
            if not plan:
                raise MvError(f"у задания #{job['id']} ещё нет плана")
            style = V.get_style(store, project_id)
            provider = "none" if is_dry(job) else (style["provider"] if style["provider"] != "none" else DRAW_PROVIDER)
            for iid, spec in plan["images"].items():
                if slot and iid != slot:
                    continue
                if spec.get("asset_id"):
                    continue  # своя картинка — 0 ₽
                kind = image_kind(plan, iid)
                ref = V.selected_ref(store, int(project_id), TARGET, int(job["id"]), kind, iid)
                fresh = ref is not None and (ref.get("prompt") or "") == style_prompt(plan, iid)
                if fresh and not regenerate:
                    continue
                item = G._image_item(STAGE, TARGET, int(job["id"]), kind,
                                     f"«{job['title']}»: {spec.get('kind', 'plate')} {iid}", style["image_model"],
                                     style["quality"], provider, slot=iid)
                res = str(spec.get("res") or "1K")
                price = image_price(item["model"], res, provider)
                item.update(resolution=res, aspect=str(spec.get("ar") or "9:16"), estimate_rub=price["rub"],
                            price_note=price["note"], group=group_of(int(job["id"])), regenerate=bool(regenerate))
                out.append(item)
        # Основа — раньше поз: позы ждут её (deps), а работник берёт запуски по порядку.
        return sorted(out, key=lambda i: i["ref_kind"] == POSE_KIND)

    def deps(self, store: Any, job: dict) -> list[tuple[int, str]]:
        if job.get("ref_kind") == POSE_KIND:
            return [(int(job["target_id"]), f"{TARGET}:{IMAGE_KIND}")]
        return []

    def build(self, store: Any, job: dict) -> tuple[str, list[Path], list[str], dict]:
        from app.aivideo_prompts import _asset_path

        mv = get_job(store, int(job["target_id"]))
        plan = load_plan(mv) or {}
        iid = str(job.get("slot") or "")
        spec = (plan.get("images") or {}).get(iid)
        if spec is None:
            raise MvError(f"в плане задания #{mv['id']} нет картинки {iid}")
        prompt = style_prompt(plan, iid)
        refs, roles, missing = [], [], []
        for ref_id in spec.get("refs") or []:
            base = (plan["images"].get(ref_id) or {})
            path = own_asset_path(store, mv, base) if base.get("asset_id") else _asset_path(
                store, image_ref(store, mv, ref_id, plan))
            if path:
                refs.append(path)
                roles.append(f"{ref_id}: исходная картинка персонажа")
            else:
                missing.append(f"{ref_id}: картинки ещё нет")
        caption = ["клип на песню", iid, spec.get("kind", "plate")]
        return prompt, refs, caption, {"roles": roles, "missing": missing}

    def card_line(self, store: Any, project_id: int, est: dict) -> str:
        groups = {i.get("group") for i in est["items"] if i.get("group")}
        poses = sum(1 for i in est["items"] if i["ref_kind"] == POSE_KIND)
        head = "Клип на песню"
        if len(groups) == 1:
            try:
                job = get_job(store, int(next(iter(groups)).split(":")[1]))
                span = (f"{job['start_sec']:g}–{job['end_sec']:g} с" if job.get("end_sec") is not None
                        else f"с {job['start_sec']:g} с")
                head = (f"Клип на песню «{P._text(job['title'], 80)}» ({STYLE_LABELS.get(job['style'], job['style'])}, "
                        f"{span}; потолок задания {job['max_rub']:g} ₽)")
            except (KeyError, ValueError, IndexError):
                pass
        what = f"картинок {est['count']}" + (f" (из них новых поз персонажей {poses})" if poses else "")
        return f"{head}: {what} ≈ {est['total_rub']:.2f} ₽ ({est['model']}). Песня — своя, музыку не генерирую."


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


__all__ = ["ACTIVE_STATUSES", "IMAGE_KIND", "MvError", "POSE_KIND", "STAGE", "STATUS_LABELS", "STYLES", "TARGET",
           "auto_prompts", "create_job", "draft_plan", "ensure_music_video_schema", "get_job", "image_kind",
           "image_ref", "job_dir", "list_jobs", "load_plan", "save_plan", "set_status", "style_prompt",
           "tools_dir", "update_job", "validate_plan"]
