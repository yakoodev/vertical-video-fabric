"""Цензура мата одной командой: тишина серединой слова, звёздочки, проверка Whisper по рендеру.

Агенты делали это вручную: искали мат в словах субтитров, ставили ``mute`` (mute_audio),
рендерили, прогоняли Whisper по готовому файлу и, если слово всё ещё узнаётся, расширяли
отрезок — по 3–4 круга на клип. Здесь то же самое по шагам:

1. ``censor_plan`` — мат в словах субтитров (app/profanity.py) → по отрезку тишины на слово
   (шкала clip, как у mute_audio) с меткой ``by: "censor"`` и уровнем; ручные отрезки
   остаются как были, свои прежние заменяются. В субтитрах — «б****». Без рендера, быстро.
2. ``verify_plan`` — рендер → Whisper по звуку ГОТОВОГО файла → мат в окне ±``WINDOW_SEC``
   от заглушённого слова? Уровень этого слова +1 и новый рендер, не больше ``max_rounds``
   кругов. Рендер и Whisper — в общей очереди тяжёлых задач (app/heavy_queue.py), по одному;
   модель Whisper — та же, что у субтитров (large-v3-turbo), в конце выгружается.

Whisper иногда угадывает мат по контексту даже при слове, заглушённом почти целиком
(уровень 3, слышна одна первая согласная): тогда ответ честно говорит «узнаётся по контексту»
— дальше только вырез слова или правка монтажа, тишиной это не лечится.

``censor_job`` — проверка фоном (рендер и Whisper — минуты): вызов ждёт не дольше
``JOB_WAIT_SEC`` и отвечает ``status: running``; повторный вызов подключается к той же
работе, второй не запускает (как расшифровка исходника, app/manual_clips.py).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.audio_mute import MAX_MUTES, normalize_mute
from app.profanity import DEFAULT_GROUPS, GROUPS, MAX_LEVEL, clamp_level, find_profanity, mute_range, star_subtitles

log = logging.getLogger(__name__)

TAG = "censor"
DEFAULT_MAX_ROUNDS = 3
MAX_ROUNDS_LIMIT = 5
WINDOW_SEC = 0.4  # мат, распознанный ближе этого к заглушённому слову, — «узнаётся»
JOB_WAIT_SEC = 45.0
JOB_KEEP_SEC = 600.0  # итог проверки, который никто не забрал, через 10 мин уже не отдаём
CONTEXT_NOTE = "узнаётся по контексту"


def _groups(soft: bool = False, groups: Any = None) -> tuple[str, ...]:
    if groups:
        picked = tuple(g for g in GROUPS if g in set(groups))
        if not picked:
            raise ValueError(f"groups — из {', '.join(GROUPS)}")
        return picked
    return (*DEFAULT_GROUPS, "soft") if soft else DEFAULT_GROUPS


def _clock(store: Any, plan: dict):
    from app.agent_tools import plan_clock

    return plan_clock(store, plan)


def _word_clip_ranges(clock: Any, start: float, end: float) -> list[dict]:
    """Слово (куски подряд, как хранятся субтитры) → шкала clip; через стык — двумя частями."""
    return clock.plain_range_to_clip(float(start), float(end))


def _overlaps(a0: float, a1: float, b0: float, b1: float) -> bool:
    return a0 < b1 and b0 < a1


def _levels_arg(levels: Any) -> tuple[int | None, dict[int, int]]:
    """``levels``: число — всем словам; ``{"<i>": n}`` или ``[{i, level}]`` — отдельным словам."""
    if levels is None or levels == "":
        return None, {}
    if isinstance(levels, (int, float)) and not isinstance(levels, bool):
        return clamp_level(levels), {}
    if isinstance(levels, str) and levels.strip().isdigit():
        return clamp_level(levels), {}
    out: dict[int, int] = {}
    if isinstance(levels, dict):
        items = levels.items()
    elif isinstance(levels, list):
        items = [(x.get("i"), x.get("level")) for x in levels if isinstance(x, dict)]
    else:
        raise ValueError("levels — число 1–3 (всем словам) или {номер слова: уровень}")
    for key, value in items:
        try:
            out[int(key)] = clamp_level(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("levels — {номер слова i: уровень 1–3}") from exc
    return None, out


def censor_plan(store: Any, clip_plan_id: int, levels: Any = None, *, stars: bool = True, soft: bool = False,
                groups: Any = None, skip: Any = (), clear: bool = False) -> dict:
    """Мат клипа: тишина серединой слова (свои отрезки ``by: censor`` заменяются, ручные — нет)
    и звёздочки в субтитрах. Без рендера. Уровень слова: из ``levels``, иначе прежний (от
    прошлого запуска), иначе 1. ``clear`` — снять свои отрезки (звёздочки остаются: Ctrl+Z)."""
    from app.clip_settings import normalize_render_settings

    picked = _groups(soft, groups)
    level_all, level_by_word = _levels_arg(levels)
    plan = store.get_clip_plan(clip_plan_id)
    render = normalize_render_settings(plan.get("render_settings") or {})
    old_mute = render.get("mute") or []
    manual = [m for m in old_mute if m.get("by") != TAG]
    previous = [m for m in old_mute if m.get("by") == TAG]
    if clear:
        if previous:
            store.set_clip_plan_render_settings(clip_plan_id, {**render, "mute": manual})
        return {"words": [], "removed": len(previous), "manual_kept": len(manual), "stars": 0,
                "note": f"снято отрезков цензуры: {len(previous)}; ручные остались ({len(manual)})"}
    payload = store.get_clip_plan_subtitles(clip_plan_id) or {}
    words = payload.get("words") or []
    if not words:
        raise ValueError("у клипа нет слов субтитров — сначала generate_subtitles (мат ищется по словам)")
    found = find_profanity(words, picked, skip)
    clock = _clock(store, plan)
    duration = clock.clip_duration
    fresh: list[dict] = []
    report: list[dict] = []
    for item in found:
        parts = _word_clip_ranges(clock, item["start"], item["end"])
        if not parts:
            continue
        w0, w1 = parts[0]["start"], parts[-1]["end"]
        prior = max((int(m.get("level") or 1) for m in previous if _overlaps(m["start"], m["end"], w0, w1)),
                    default=0)
        level = level_by_word.get(item["i"]) or level_all or prior or 1
        a, b = mute_range(item["start"], item["end"], level)
        spans = [{"start": p["start"], "end": min(p["end"], duration), "by": TAG, "level": level}
                 for p in clock.plain_range_to_clip(a, b) if p["start"] < duration]
        fresh.extend(spans)
        report.append({"i": item["i"], "word": item["word"], "group": item["group"],
                       "start": round(w0, 3), "end": round(w1, 3), "level": level,
                       "mute": [[s["start"], s["end"]] for s in spans]})
    if len(manual) + len(fresh) > MAX_MUTES:
        raise ValueError(f"отрезков тишины больше {MAX_MUTES} (ручных {len(manual)}, мата {len(fresh)}) — "
                         "слей ручные mute_audio или вырежи часть мата apply_cuts")
    mute = normalize_mute(manual + fresh)
    if mute != normalize_mute(old_mute):
        store.set_clip_plan_render_settings(clip_plan_id, {**render, "mute": mute})
    starred = 0
    if stars and found:
        new_payload, starred = star_subtitles(payload, found, picked)
        if starred:
            store.set_clip_plan_subtitles(clip_plan_id, {**new_payload, "edited": True})
            stored = (store.get_clip_plan_subtitles(clip_plan_id) or {}).get("words") or []
            for row in report:
                if 0 <= row["i"] < len(stored):
                    row["word"] = stored[row["i"]].get("word") or row["word"]
    note = (f"мат: {len(report)} слов — тишина серединой слова (шкала clip), в субтитрах звёздочки"
            if report else "мата в словах субтитров не найдено")
    return {"words": report, "groups": list(picked), "manual_kept": len(manual), "stars": starred,
            "removed": len(previous), "time": "clip", "note": note}


# --------------------------------------------------------------------------- #
# Проверка распознаванием готового рендера
# --------------------------------------------------------------------------- #


def _default_subtitle_profile_id(store: Any) -> int | None:
    """Как «по умолчанию» в API (main._default_subtitle_profile_id): сохранённый, иначе первый."""
    try:
        saved = int(store.get_app_setting_value("default_subtitle_profile_id") or 0)
    except (TypeError, ValueError):
        saved = 0
    if saved:
        return saved
    profiles = store.list_subtitle_profiles()
    return profiles[0]["id"] if profiles else None


def render_plan(store: Any, clip_plan_id: int) -> dict:
    """Рендер клипа с его собственными настройками — как POST /api/clip-plans/{id}/render.
    Очередь тяжёлых задач — внутри ``render_clip_plan``."""
    from app.clip_settings import settings_to_render_kwargs
    from app.render import ClipRenderService

    plan = store.get_clip_plan(clip_plan_id, include_segments=False)
    kwargs = (settings_to_render_kwargs(plan["render_settings"], _default_subtitle_profile_id(store))
              if plan.get("render_settings") else {})
    return ClipRenderService(store).render_clip_plan(clip_plan_id, **kwargs)


def hear_render(store: Any, clip: dict) -> list[dict]:
    """Что распознаёт Whisper в звуке готового файла: ``[{word, start, end}]`` (шкала final).

    Звук — весь микс ролика (то, что слышат автосубтитры площадки), модель — та же, что у
    субтитров. Извлечение звука и распознавание — одна задача в очереди тяжёлых."""
    from app.clip_subtitles import _default_profile
    from app.heavy_queue import heavy_slot
    from app.render import _run_ffmpeg
    from app.settings import settings
    from app.subtitles.registry import QUALITY_WHISPER_MODEL, get_subtitle_provider

    path = Path(str(clip.get("output_path") or ""))
    if not path.is_file():
        raise ValueError(f"файла рендера {clip.get('id')} нет")
    try:
        profile = {**_default_profile(store), "provider": "whisper"}
    except ValueError:
        profile = {"provider": "whisper"}
    profile["language"] = str(profile.get("language") or "").strip() or "ru"
    wav = settings.tmp_dir / f"censor-{uuid4().hex}.wav"
    with heavy_slot("whisper", f"цензура рендера {clip.get('id')}"):
        try:
            _run_ffmpeg(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn",
                         "-ac", "1", "-ar", "16000", str(wav)], timeout=60 * 5)
            try:
                result = get_subtitle_provider("whisper").transcribe(wav, profile, QUALITY_WHISPER_MODEL)
            except RuntimeError as exc:
                if "no word-level timestamps" in str(exc):
                    return []  # речи не распознано вовсе — мата тоже
                raise
            return [w.to_dict() for w in result.words]
        finally:
            wav.unlink(missing_ok=True)


def _release_whisper() -> None:
    """Модель Whisper после проверки не держим: память машины небольшая."""
    try:
        from app.subtitles.whisper_local import WhisperSubtitleProvider

        WhisperSubtitleProvider.unload()
    except Exception:  # noqa: BLE001 - выгрузка — забота, не повод сорвать ответ
        log.warning("цензура: модель Whisper не выгрузилась", exc_info=True)


def final_windows(store: Any, plan: dict, clip: dict, words: list[dict]) -> dict[int, tuple[float, float] | None]:
    """Слово (шкала clip) → его место в готовом ролике (вырезы, стоп-кадры); None — вырезано."""
    from app.render import ClipRenderService

    svc = ClipRenderService(store)
    cut_plan = svc._cut_plan(plan, clip.get("ffmpeg_preset_id"))
    mapper = svc._final_mapper(plan, cut_plan)
    out: dict[int, tuple[float, float] | None] = {}
    for w in words:
        a, b = float(w["start"]), float(w["end"])
        out[w["i"]] = mapper.range(a, b, 0.02) if mapper is not None else (a, b)
    return out


def heard_profanity(heard: list[dict], windows: dict[int, Any], groups: Any) -> tuple[dict[int, str], list[dict]]:
    """Распознанный мат: у каких заглушённых слов (в окне ±WINDOW_SEC) и где ещё — мимо всех окон."""
    from app.profanity import classify_token

    bad = [w for w in heard if classify_token(w.get("word"), groups) not in (None, "starred")]
    near: dict[int, str] = {}
    stray = []
    for w in bad:
        s, e = float(w["start"]), float(w["end"])
        hit = False
        for i, win in windows.items():
            if win is not None and _overlaps(s, e, win[0] - WINDOW_SEC, win[1] + WINDOW_SEC):
                near.setdefault(i, str(w.get("word") or "").strip())
                hit = True
        if not hit:
            stray.append({"word": str(w.get("word") or "").strip(), "start": round(s, 2), "end": round(e, 2)})
    return near, stray


def verify_plan(store: Any, clip_plan_id: int, levels: Any = None, *, max_rounds: int = DEFAULT_MAX_ROUNDS,
                stars: bool = True, soft: bool = False, groups: Any = None, skip: Any = (),
                progress: Callable[[dict], None] | None = None) -> dict:
    """Глушение + круги «рендер → Whisper → шире там, где узнаётся». Итог — по последнему кругу."""
    picked = _groups(soft, groups)
    rounds_max = max(1, min(MAX_ROUNDS_LIMIT, int(max_rounds or DEFAULT_MAX_ROUNDS)))
    state = censor_plan(store, clip_plan_id, levels, stars=stars, groups=picked, skip=skip)
    if not state["words"]:
        return {**state, "rounds": [], "render_id": None, "verified": False,
                "note": "мата в словах субтитров не найдено — рендер не запускался"}
    level_now = {w["i"]: w["level"] for w in state["words"]}
    rounds: list[dict] = []
    near: dict[int, str] = {}
    stray: list[dict] = []
    clip: dict = {}
    try:
        for n in range(1, rounds_max + 1):
            if progress:
                progress({"round": n, "stage": "рендер"})
            clip = render_plan(store, clip_plan_id)
            if clip.get("status") != "succeeded":
                raise ValueError(f"рендер не удался: {str(clip.get('error') or '')[:200]}")
            if progress:
                progress({"round": n, "stage": "распознаю рендер"})
            heard = hear_render(store, clip)
            plan = store.get_clip_plan(clip_plan_id)
            windows = final_windows(store, plan, clip, state["words"])
            near, stray = heard_profanity(heard, windows, picked)
            rounds.append({"round": n, "render_id": clip.get("id"), "levels": dict(level_now),
                           "heard": sorted(near)})
            if not near or n == rounds_max:
                break
            bump = {i: level_now[i] + 1 for i in near if level_now.get(i, MAX_LEVEL) < MAX_LEVEL}
            if not bump:
                break  # всё узнанное уже на последнем уровне — тишиной дальше не помочь
            level_now.update(bump)
            state = censor_plan(store, clip_plan_id, level_now, stars=False, groups=picked, skip=skip)
    finally:
        _release_whisper()
    in_final = final_windows(store, store.get_clip_plan(clip_plan_id), clip, state["words"]) if clip else {}
    words = []
    for w in state["words"]:
        row = {**w, "heard": w["i"] in near}
        if in_final.get(w["i"]) is None:
            row["heard"] = None
            row["note"] = "в готовом ролике вырезано"
        elif row["heard"]:
            row["heard_as"] = near[w["i"]]
            row["note"] = (CONTEXT_NOTE + " — тишиной не лечится: вырежи слово apply_cuts или оставь"
                           if w["level"] >= MAX_LEVEL else
                           f"узнаётся; круги кончились — повтори с max_rounds больше (уровень {w['level']})")
        words.append(row)
    still = [w for w in words if w["heard"]]
    note = (f"после {len(rounds)} кругов Whisper мат не узнаёт ни в одном из {len(words)} слов"
            if not still else
            f"узнаётся {len(still)} из {len(words)}: " + ", ".join(f"#{w['i']} {w['note']}" for w in still[:5]))
    if stray:
        note += f"; ещё мат вне заглушённого ({len(stray)}) — в субтитрах его нет, см. unmuted_heard"
    return {**state, "words": words, "rounds": rounds, "render_id": clip.get("id"), "verified": True,
            "unmuted_heard": stray, "note": note}


# --------------------------------------------------------------------------- #
# Фоном: проверка — минуты, запрос ждёт не дольше JOB_WAIT_SEC
# --------------------------------------------------------------------------- #

_JOBS: dict[int, dict] = {}
_JOBS_LOCK = threading.Lock()


def job_running(clip_plan_id: int) -> bool:
    with _JOBS_LOCK:
        job = _JOBS.get(int(clip_plan_id))
        return job is not None and not job["finished"].is_set()


def _run_job(store: Any, clip_plan_id: int, job: dict, options: dict) -> None:
    def progress(info: dict) -> None:
        job.update(info)

    try:
        job["result"] = verify_plan(store, clip_plan_id, progress=progress, **options)
    except Exception as exc:  # noqa: BLE001 - ошибку отдаст следующий опрос
        job["error"] = exc
    finally:
        job["finished_at"] = time.monotonic()
        job["finished"].set()


def censor_job(store: Any, clip_plan_id: int, *, wait: float | None = None, **options: Any) -> dict:
    """Проверка фоном: одна работа на клип (повторный вызов к ней подключается). Готово за
    ``wait`` с — итог ``verify_plan`` (``status: done``), иначе ``status: running`` с кругом."""
    wait = JOB_WAIT_SEC if wait is None else wait
    clip_plan_id = int(clip_plan_id)
    store.get_clip_plan(clip_plan_id, include_segments=False)  # нет клипа — KeyError сразу
    with _JOBS_LOCK:
        job = _JOBS.get(clip_plan_id)
        if job is not None and job["finished"].is_set() and                 time.monotonic() - float(job.get("finished_at") or 0) > JOB_KEEP_SEC:
            job = None  # давний итог — клип с тех пор могли править: проверяем заново
        if job is None:
            job = {"finished": threading.Event(), "started": time.monotonic(), "round": 0, "stage": "начинаю",
                   "max_rounds": options.get("max_rounds") or DEFAULT_MAX_ROUNDS}
            _JOBS[clip_plan_id] = job
            threading.Thread(target=_run_job, args=(store, clip_plan_id, job, options),
                             name=f"censor-{clip_plan_id}", daemon=True).start()
    if job["finished"].wait(max(0.0, float(wait))):
        with _JOBS_LOCK:
            if _JOBS.get(clip_plan_id) is job:
                _JOBS.pop(clip_plan_id)
        if "error" in job:
            error = job["error"]
            raise error if isinstance(error, (KeyError, ValueError)) else ValueError(str(error)[:300])
        return {**job["result"], "status": "done"}
    elapsed = round(time.monotonic() - job["started"])
    return {"status": "running", "round": job.get("round"), "max_rounds": job.get("max_rounds"),
            "stage": job.get("stage"), "elapsed_sec": elapsed, "retry_after_sec": 30,
            "note": (f"проверяю: круг {job.get('round') or 1} из {job.get('max_rounds')}, {job.get('stage')}. "
                     "Повтори тот же вызов через ~30 с — вторая проверка не запустится, ответ будет итогом.")}
