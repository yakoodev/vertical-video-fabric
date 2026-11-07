"""Вопросы по стриму и выбор мемов: монтажёр спрашивает, владелец отвечает нажатием.

Владелец: «агент смотрит весь стрим, формирует список вопросов и варианты ответа и у меня
спрашивает, чтобы я объяснял, что происходит. Мемов не хватает, такую же систему с
вариантами». Вопросы и варианты заполняет агент-монтажёр через инструменты (app/stream_question_tools.py),
ответы читает перед отбором моментов. Модель тут не зашита.

Уровень вопроса (владелец: «вопросы надо делить на проект, видео, клип, не в общую кучу»). В базе
это ``scope``, наружу ещё и ``level``:

* ``channel`` = ``project``: про проект вообще («кто такой Глеб»). Ответ сразу в бриф проекта
  (app/projects.py), чтобы не спрашивать на каждом стриме.
* ``stream`` = ``video``: только про это видео.
* ``clip`` = ``clip``: только про один клип (``clip_plan_id``, версии одного клипа считаются вместе).

Уровень можно сменить и после ответа (``set_level``): ответ переезжает в бриф или обратно.
Весь список того, что агент знает о проекте, и его правка — app/project_knowledge.py.

Пауза монтажа (владелец: «монтаж ставить на паузу, пока на вопросы не ответили. а то у нас уже 3
версии горизонтальной нарезки, а на вопросы я отвечаю только сейчас»). Открытый обязательный вопрос
видео останавливает агентов: инструменты из ``MONTAGE_TOOLS`` и те же действия по токену отказывают
(``montage_refusal``). Чего пауза не касается: правки владельца руками в редакторе, рендера готовых
клипов, «Смотреть монтаж», субтитров, слоёв и звука. Вопрос проекта держит только то видео, откуда
он задан, вопрос клипа только этот клип. ``optional`` не держит ничего, мемы тоже: они не меняют,
что вырезать. Снять паузу может только владелец кнопкой «Монтировать без ответов»
(``release_montage``); кнопка покрывает вопросы, заданные до нажатия, новый вопрос снова останавливает.
Весь проект может не ждать ответов: настройка «Ждать ответов на вопросы перед монтажом» (app/agent_settings.py).

Где что лежит. Здесь — ответы и предпросмотр. Вынесено: вопросы и бриф ``app/stream_questions_core.py``, стоп монтажа ``app/stream_questions_gate.py``, выбор мема
``app/stream_questions_memes.py``, состояние страницы ``app/stream_questions_state.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.settings import settings

from app.stream_questions_core import (  # noqa: F401
    KINDS, SCOPES, LEVELS, LEVEL_OF_SCOPE, SCOPE_OF_LEVEL, STATUSES, MAX_QUESTIONS_PER_CALL, MIN_OPTIONS,
    MAX_OPTIONS, MAX_TEXT, SCHEMA_SQL, ensure_stream_questions_schema, _text, _body, _key, _times, _options,
    _question, answer_value, _clip_family, _clip_of_source, _asked_scope, add_questions, list_questions,
    get_question, _brief_patch, _write_brief, brief_place, in_brief, _unwrite_brief, _restore_other_answer,
)
from app.stream_questions_gate import (  # noqa: F401
    LEVEL_WORD, GATE_SHOWN, _release_row, montage_gate, release_montage, restore_montage_pause, _ru_questions,
    montage_refusal,
)
from app.stream_questions_memes import (  # noqa: F401
    MOMENT_STATUSES, MAX_MOMENTS_PER_CALL, MIN_MEME_OPTIONS, MAX_MEME_OPTIONS, MAX_URL_IMAGE_BYTES, choices_dir,
    _save_url_image, _mime_of, _prepare_option, _moment, add_meme_choices, list_moments, get_moment, option_file,
    _ensure_asset, choose_meme,
)
from app.stream_questions_state import (  # noqa: F401
    progress, _source_title, _clip_names, _elsewhere, page_state, clip_state, inbox_state, answers_brief,
)

# Что начинает новый монтаж или резку. Остальные инструменты клипа пауза не трогает. Руками владелец
# работает мимо этих инструментов (свои маршруты REST), так что его правки сюда не попадают.
MONTAGE_TOOLS = frozenset({"analyze_source", "create_clip", "make_compilation", "add_video_from_source", "auto_edit",
                           "suggest_cuts", "apply_cuts", "trim_piece"})


CLIP_PAD_SEC = 3.0
MAX_CLIP_SEC = 60.0
CLIP_HEIGHT = 480
THUMB_WIDTH = 480
MAX_CLIP_CACHE_BYTES = 3 * 1024 ** 3


FFMPEG_TIMEOUT_SEC = 180


def correct_answer(store: Any, question_id: int, text: Any) -> dict:
    """Владелец поправил свой ответ на экране «Что агент знает»: остаётся ответом, но своими словами."""
    get_question(store, question_id)
    value = _body(text)
    if not value:
        raise ValueError("нужен текст ответа")
    store.db.execute(
        "UPDATE stream_questions SET answer_option = NULL, answer_text = ?, status = 'answered', "
        "answered_at = CURRENT_TIMESTAMP WHERE id = ?", (value, int(question_id)))
    return get_question(store, question_id)


def forget_answer(store: Any, question_id: int) -> dict:
    """Владелец убрал ответ: для агента его больше нет, вопрос заново не задаём (его можно открыть в «Вопросах»)."""
    store.db.execute(
        "UPDATE stream_questions SET answer_option = NULL, answer_text = '', status = 'skipped', "
        "answered_at = CURRENT_TIMESTAMP WHERE id = ?", (int(question_id),))
    return get_question(store, question_id)


def answer_question(store: Any, question_id: int, option: Any = None, text: Any = "", skip: bool = False) -> dict:
    # Одна транзакция, как у set_level: смена уровня и ответ не разъедутся в брифе.
    with store.db.transaction():
        return _answer_locked(store, question_id, option, text, skip)


def _answer_locked(store: Any, question_id: int, option: Any, text: Any, skip: bool) -> dict:
    item = get_question(store, question_id)
    before = answer_value(item) if item["status"] == "answered" else ""
    custom = _body(text)
    picked = None
    if option not in (None, ""):
        try:
            picked = int(option)
        except (TypeError, ValueError) as exc:
            raise ValueError("option — номер варианта") from exc
        if not 0 <= picked < len(item["options"]):
            raise ValueError("такого варианта нет")
    if skip:
        status = "skipped"
    elif picked is None and not custom:
        status = "open"  # снял выбор и стёр текст — вопрос снова открыт
    else:
        status = "answered"
    store.db.execute(
        "UPDATE stream_questions SET answer_option = ?, answer_text = ?, status = ?, "
        "answered_at = CASE WHEN ? = 'open' THEN NULL ELSE CURRENT_TIMESTAMP END WHERE id = ?",
        (None if skip else picked, "" if skip else custom, status, status, int(question_id)),
    )
    fresh = get_question(store, question_id)
    brief: dict[str, Any] = {"written": False}
    if status == "answered" and fresh["scope"] == "channel":
        try:
            brief = _write_brief(store, fresh)
        except Exception as exc:  # noqa: BLE001 - ответ уже сохранён, бриф можно дописать позже
            brief = {"written": False, "reason": str(exc)[:200]}
    elif before and fresh["scope"] == "channel":
        try:
            _unwrite_brief(store, fresh, before)
        except Exception as exc:  # noqa: BLE001 - ответ снят, а строку брифа владелец уберёт сам
            brief = {"written": False, "reason": str(exc)[:200]}
    return {**fresh, "brief": brief, "gate": montage_gate(store, int(fresh["source_id"]))}


def set_level(store: Any, question_id: int, level: Any, clip_plan_id: Any = None) -> dict:
    """Владелец поправил уровень вопроса: ответ переезжает в бриф проекта или обратно, сам ответ не меняется.
    Клип вопроса помнится и у видео-уровня, чтобы вернуть вопрос к клипу."""
    level = str(level or "")
    if level not in LEVELS:
        raise ValueError(f"level — {', '.join(LEVELS)}")
    with store.db.transaction():
        item = get_question(store, question_id)
        scope = SCOPE_OF_LEVEL[level]
        clip = item.get("clip_plan_id")
        if scope == "clip":
            wanted = clip_plan_id if clip_plan_id not in (None, "") else clip
            if wanted is None:
                raise ValueError("вопрос про клип: нужен clip_id")
            clip = _clip_of_source(store, int(item["source_id"]), wanted)
        brief: dict[str, Any] = {"written": False}
        if scope != item["scope"] or clip != item.get("clip_plan_id"):
            value = answer_value(item) if item["status"] == "answered" else ""
            store.db.execute("UPDATE stream_questions SET scope = ?, clip_plan_id = ? WHERE id = ?",
                             (scope, clip, int(question_id)))
            fresh = get_question(store, question_id)
            try:
                if value and item["scope"] == "channel" and scope != "channel":
                    _unwrite_brief(store, item, value)
                elif value and scope == "channel" and item["scope"] != "channel":
                    brief = _write_brief(store, fresh)
            except Exception as exc:  # noqa: BLE001 - уровень уже сменён, строку брифа владелец поправит сам
                brief = {"written": False, "reason": str(exc)[:200]}
        fresh = get_question(store, question_id)
    return {**fresh, "brief": brief, "gate": montage_gate(store, int(fresh["source_id"]))}


# --------------------------------------------------------------------------- #
# Кусочек исходника вокруг момента
# --------------------------------------------------------------------------- #

_ffmpeg_slots = threading.BoundedSemaphore(2)
_clip_locks: dict[str, threading.Lock] = {}
_clip_locks_guard = threading.Lock()


def clips_dir() -> Path:
    return settings.data_dir / "stream_questions" / "clips"


def _source_file(store: Any, source_id: int) -> tuple[dict, Path]:
    source = store.get_source(int(source_id))
    path = Path(source.get("local_path") or "")
    if not path.is_file():
        raise KeyError("файла записи больше нет")
    try:
        path.resolve(strict=False).relative_to(settings.source_dir.resolve())
    except ValueError as exc:
        raise KeyError("файл записи вне папки исходников") from exc
    return source, path


def _span(source: dict, t_start: float, t_end: float) -> tuple[float, float]:
    duration = float(source.get("duration_sec") or 0)
    start = max(0.0, float(t_start) - CLIP_PAD_SEC)
    end = float(t_end) + CLIP_PAD_SEC
    if duration:
        end = min(end, duration)
    return round(start, 1), max(1.0, round(min(end - start, MAX_CLIP_SEC), 1))


def _trim_cache(keep: Path) -> None:
    folder = keep.parent
    files = sorted((p for p in folder.iterdir() if p.is_file() and p != keep),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    total = keep.stat().st_size
    for path in files:
        total += path.stat().st_size
        if total > MAX_CLIP_CACHE_BYTES:
            path.unlink(missing_ok=True)


def _run_ffmpeg(args: list[str]) -> None:
    with _ffmpeg_slots:
        subprocess.run(["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y", *args], check=True,
                       timeout=FFMPEG_TIMEOUT_SEC, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def preview_clip(store: Any, source_id: int, t_start: float, t_end: float) -> Path:
    """Лёгкий кусочек записи вокруг момента. Кэш на диске, чтобы ради вопроса не тянуть 12 ГБ стрима."""
    source, video = _source_file(store, source_id)
    start, length = _span(source, t_start, t_end)
    folder = clips_dir()
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{int(source_id)}_{int(start * 10)}_{int(length * 10)}.mp4"
    with _clip_locks_guard:
        lock = _clip_locks.setdefault(out.name, threading.Lock())
    with lock:
        if not out.is_file():
            tmp = out.with_name(out.stem + f".{uuid4().hex}.tmp.mp4")
            try:
                _run_ffmpeg(["-ss", f"{start:.2f}", "-i", str(video), "-t", f"{length:.2f}", "-map", "0:v:0", "-map", "0:a:0?",
                             "-vf", f"scale=-2:{CLIP_HEIGHT}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                             "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", str(tmp)])
                tmp.replace(out)
            finally:
                tmp.unlink(missing_ok=True)
        _trim_cache(out)
    return out


def preview_thumb(store: Any, source_id: int, t: float) -> Path:
    _source, video = _source_file(store, source_id)
    moment = max(0.0, float(t))
    folder = clips_dir().parent / "thumbs"
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{int(source_id)}_{int(moment * 2)}.jpg"
    if not out.is_file():
        tmp = out.with_name(out.stem + f".{uuid4().hex}.tmp.jpg")
        try:
            _run_ffmpeg(["-ss", f"{moment:.2f}", "-i", str(video), "-frames:v", "1", "-vf", f"scale={THUMB_WIDTH}:-2",
                         "-q:v", "5", str(tmp)])
            tmp.replace(out)
        finally:
            tmp.unlink(missing_ok=True)
    return out
