"""Истории: конвейер «завязка → готовый ролик» (docs/guides/stories.md).

Шаги эпизода (``story_episodes.status``), каждый повторяем и идемпотентен; ведёт ``advance`` (работник
``StoriesWorker`` или агент ``story_continue``):

1. ``new`` → **сценарий** (Gemini, бесплатно): крючок первой фразой, развитие, поворот, панчлайн;
   русский, 45–60 с; на КАЖДУЮ фразу 1–2 кадра: кто, где, действие (по-английски), камера, звук,
   акцент. Новые актёры и локации — только если без них никак (сначала переиспользуются свои).
   Gemini недоступен — ``failed`` с подсказкой: агент пишет сценарий сам (``story_set_script``).
2. ``scripted`` → **биты и сцены**: фраза → биты по 1.2–3 с (по кадрам фразы; длинная фраза с одним
   кадром — тот же кадр с другим движением камеры). На каждый бит — готовая сцена библиотеки (то же
   место, те же актёры, похожее действие) или новая (рисуется по карточке). Звук бита — настоящая
   запись (паки, «Файлы для монтажа»); записи нет — заметка, синтетики нет.
3. ``planned`` → **оценка и деньги**: бесплатная озвучка (Gemini TTS / сухой прогон) — сразу; новые
   сцены, листы, плиты, кадр стиля и платная озвучка — ОДНА карточка владельцу (этап ``stories``).
4. ``awaiting_approval`` / ``generating`` → ждём «Разрешить» и запуски (работник ИИ-видео).
5. ``assembling`` → **сборка**: слова озвучки (Whisper; сухой прогон — по длине текста) → биты по
   времени → холст-клип: сцены слоями ``image`` с мягкой камерой (Ken Burns ключами слоя или наша
   3D-камера ``parallax_push``), звуки действий, озвучка, субтитры по ОДНОМУ слову в нижней трети,
   музыка мира под голосом (``auto_mix``). По желанию — плашка-название, вжухи на склейках, рендер,
   обложки, заголовок/описание.

Где что лежит. Здесь — конвейер и всё, что читает подменяемые тестами имена (``_ask``, ``gemini_client``, ``transcribe_words``). Вынесено: сценарий
``app/stories_gen_script.py``, биты ``app/stories_gen_beats.py``, заказ генерации ``app/stories_gen_orders.py``, слои ``app/stories_gen_layers.py``. Те модули этот не импортируют, а он их — поэтому все имена по-прежнему доступны отсюда.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from app import aivideo as V
from app import autopilot as A
from app import clip_groups as CG
from app import word_fixes as WF
from app import explainer_gen as X
from app import projects as P
from app import stories as S
from app import stories_disaster as D
from app.settings import settings

from app.stories_gen_script import (  # noqa: F401
    CPS, MAX_NEW_CAST, MAX_NEW_LOCATIONS, MAX_SHOTS_PER_SENTENCE, CAMERA_MOVES, SCRIPT_RULES, _shot_schema,
    script_schema, script_rules, _base_schema, _sfx_brief, script_context, _sentence, _key, _materialize, _shot,
    normalize_script, script_words, set_script,
)
from app.stories_gen_beats import (  # noqa: F401
    BEAT_MIN_SEC, BEAT_MAX_SEC, BEAT_MERGE_SEC, MAX_NEW_SCENES, CAMERA_CYCLE, CAMERA_ALT, _est, split_text,
    split_beats, _pick_camera, _scene_for_text, plan_beats, _plan_beats_3d, _decorate, update_beats,
)
from app.stories_gen_orders import (  # noqa: F401
    estimate, request_generation, readiness, check_generation,
)
from app.stories_gen_layers import (  # noqa: F401
    REAL_SPLIT_SEC, MIN_PLACED_SEC, SFX_VOLUME, SFX_OFFSET_SEC, SFX_MAX_SEC, TITLE_SEC, LAYER_PREFIX, place,
    _sfx_duration, _captions_props, build_layers,
)

_log = logging.getLogger(__name__)


TAIL_SEC = 0.8


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #


def gemini_client() -> Any:
    """Клиент Gemini или None (нет ключа). Тесты подменяют."""
    return X.gemini_client()


def _ask(client: Any, rules: str, context: dict, schema: dict, temperature: float = 0.9) -> dict:
    try:
        return X._ask(client, rules, context, schema, temperature)
    except S.StoryError:
        raise
    except V.AivError as exc:
        raise S.StoryError(str(exc)) from exc


def write_script(store: Any, episode_id: int, client: Any = None) -> dict:
    ep = S.get_episode(store, episode_id)
    client = client or gemini_client()
    if client is None:
        raise S.StoryError("Gemini не настроен (GEMINI_API_KEY) — напиши сценарий сам: story_set_script(episode_id, "
                           "sentences [{text, shots [{location, characters, action, camera, sfx, accent}]}])")
    fmt = S.episode_format(S.get_world(store, int(ep["world_id"])), ep)
    rules = script_rules(fmt)
    blender = S.is_3d(ep) and S.episode_renderer(ep) == S.RENDERER_BLENDER
    if blender:
        from app import series3d_blender as SB

        rules += ("\nМЕСТА И ВЕЩИ — ТОЛЬКО ИЗ СПИСКА (сцена покажет ровно их; другого места или вещи в кадре не будет — "
                  "не упоминай улицу, квартиру изнутри, подвал, окна и т.п.). Надпись на стене (год, номер, слово) — можно.\n"
                  + SB.writer_places())
        lo, hi = SB.SCRIPT_WORDS
        rules += (f"\nДЛИНА — ЖЁСТКИЙ ЛИМИТ: серия {SB.SCRIPT_SEC[0]}–{SB.SCRIPT_SEC[1]} с, это {lo}–{hi} слов во всех "
                  f"фразах вместе (не больше {hi}). Длиннее — не влезет в серию.")
    data = _ask(client, rules, script_context(store, ep), script_schema(fmt), 0.9)
    if blender:
        data = fit_script_to_places(client, data)
        data = fit_script_length(client, data)
    return set_script(store, episode_id, data, source="gemini")


def fit_script_length(client: Any, data: dict) -> dict:
    """3D-серия (Blender): текст длиннее лимита слов (``SCRIPT_WORDS``, 55–65 с) — сократить фразы (одна попытка:
    та же роль, цифры и смысл, короче); не сократилось — предупреждение."""
    from app import series3d_blender as SB

    sentences = data.get("sentences") or []
    lo, hi = SB.SCRIPT_WORDS
    words = script_words(sentences)
    if words <= hi or not sentences:
        return data
    rules = (f"Сократи фразы серии до {lo}–{hi} слов ВСЕГО (сейчас {words}). Номер i и роль фразы те же, цифры и смысл "
             "те же, хук и финал не трогай по смыслу; можно убрать лишние детали. Верни все фразы.")
    ctx = {"sentences": [{"i": i, "role": s.get("role") or "", "text": s.get("text")} for i, s in enumerate(sentences)]}
    schema = {"type": "object", "properties": {"sentences": {"type": "array", "items": {
        "type": "object", "properties": {"i": {"type": "integer"}, "text": {"type": "string"}}, "required": ["i", "text"]}}},
        "required": ["sentences"]}
    try:
        fixed = _ask(client, rules, ctx, schema, 0.4)
    except S.StoryError:
        fixed = {}
    for item in fixed.get("sentences") or []:
        i = int(item.get("i", -1))
        if 0 <= i < len(sentences) and str(item.get("text") or "").strip():
            sentences[i] = {**sentences[i], "text": str(item["text"]).strip()}
    data["sentences"] = sentences
    left = script_words(sentences)
    if left > hi:
        data["warnings"] = list(data.get("warnings") or []) + [f"текст {left} слов — длиннее {hi} (≈ {SB.SCRIPT_SEC[1]} с)"]
    return data


def fit_script_to_places(client: Any, data: dict) -> dict:
    """Фразы, где сценарист назвал место/вещь вне набора локаций, — переписать (тот же смысл, место из списка)."""
    from app import series3d_blender as SB

    sentences = data.get("sentences") or []
    bad = SB.offlist_mentions(sentences)
    if not bad:
        return data
    rules = ("Перепиши указанные фразы сериала-катастрофы так, чтобы место и вещи были ТОЛЬКО из списка (смысл, цифры, "
             "длина и роль фразы — те же):\n" + SB.writer_places())
    ctx = {"sentences": [{"i": i, "text": sentences[i].get("text"), "problem": what} for i, what in bad]}
    schema = {"type": "object", "properties": {"sentences": {"type": "array", "items": {
        "type": "object", "properties": {"i": {"type": "integer"}, "text": {"type": "string"}}, "required": ["i", "text"]}}},
        "required": ["sentences"]}
    try:
        fixed = _ask(client, rules, ctx, schema, 0.5)
    except S.StoryError:
        return data
    for item in fixed.get("sentences") or []:
        i = int(item.get("i", -1))
        if 0 <= i < len(sentences) and str(item.get("text") or "").strip():
            sentences[i] = {**sentences[i], "text": str(item["text"]).strip()}
    data["sentences"] = sentences
    left = SB.offlist_mentions(sentences)
    data.setdefault("warnings", [])
    if left:
        data["warnings"] = list(data.get("warnings") or []) + [f"фраза {i + 1}: «{w}» — места нет в наборе" for i, w in left]
    return data


# --------------------------------------------------------------------------- #
# 5. Слова, время битов, сборка
# --------------------------------------------------------------------------- #


def transcribe_words(path: Path) -> list[dict]:
    """Слова озвучки с временем (Whisper large-v3-turbo). Тесты подменяют."""
    return X.transcribe_words(path)


def _music_track(store: Any, world: dict, ep: dict) -> int | None:
    if D.music_off(world, ep):
        return None
    opt = (ep.get("options") or {}).get("music_track_id")
    for value in (opt, world.get("music_track_id"), V._style_row(store, int(ep["project_id"])).get("music_track_id")):
        if value in (None, "", 0):
            continue
        try:
            store.get_audio_track(int(value))
            return int(value)
        except (KeyError, TypeError, ValueError):
            continue
    return None


def _tool(ctx: Any, name: str, args: dict, notes: list[str] | None = None) -> dict:
    try:
        return X._tool(ctx, name, args, notes)
    except V.AivError as exc:
        raise S.StoryError(str(exc)) from exc


def assemble(store: Any, episode_id: int) -> dict:
    """Озвучка → слова → биты по времени → холст-клип. Собирали раньше — новая версия того же клипа."""
    from app import agent_tools as T
    from app.aivideo_gen import _asset_path
    from app.canvas import MIN_DURATION_SEC, base_seconds, create_canvas_clip
    from app.clip_history import snapshot
    from app.dubbing import probe_duration
    from app.motion import normalize_camera, normalize_layers

    ep = S.get_episode(store, episode_id)
    if S.is_3d(ep):
        from app import stories_3d

        return stories_3d.assemble(store, episode_id)
    world = S.get_world(store, int(ep["world_id"]))
    ref = S.narration_ref(store, ep)
    if ref is None:
        raise S.StoryError("нет озвучки эпизода — этап генерации")
    path = _asset_path(store, ref)
    audio_sec = float(probe_duration(path))
    notes: list[str] = list(ep.get("notes") or [])
    dry = S.is_dry(ep)
    heard = None
    if dry:  # только явный сухой прогон: вместо речи тон, распознавать нечего
        words = X.proportional_words(S.narration_text(ep), audio_sec)
    else:
        # Текст сценария на времени речи: ошибки Whisper в субтитры не идут, без речи эпизод не выпускаем.
        words, heard = X.real_words(store, S.narration_text(ep), ref, path, S.StoryError, "эпизод", transcribe_words)
    previous = ep.get("clip_id")
    words, sub_fixes = WF.apply_previous(store, previous, ep.get("words") or [], words, notes)
    disaster = D.is_disaster(world, ep)
    speech_end = round(float(words[-1]["end"]), 3)
    if disaster:
        # Конец обрывается сразу после последнего слова — дальше только титр следующей части (2 с).
        total = round(max(speech_end + D.PART_SEC, MIN_DURATION_SEC), 2)
    else:
        total = round(max(words[-1]["end"] + TAIL_SEC, audio_sec + 0.4, MIN_DURATION_SEC), 2)
    if total > base_seconds():
        raise S.StoryError(f"история {total:.1f} с длиннее холста {base_seconds():.0f} с")
    placed = place(ep["beats"], words, total)
    title = (ep.get("title") or ep["premise"])[:100]
    clip = create_canvas_clip(store, total, {"kind": "solid", "colors": ["#101014", "#101014"]},
                              f"{X.E.DRY_MARK}: {title}"[:100] if dry else title,
                              f"История ({world['name']}): {ep['premise']}"[:500])
    clip_id = int(clip["id"])
    CG.hold_assembly(store, clip_id)
    P.assign_plan_project(store, clip_id, int(ep["project_id"]))  # иначе холст виден только по прямой ссылке
    layers, camera = build_layers(store, world, {**ep, "_speech_end": speech_end}, placed, int(ref["asset_id"]),
                                  total, notes)
    render = dict(store.get_clip_plan(clip_id).get("render_settings") or {})
    render["layers"] = normalize_layers(layers)
    render["camera"] = normalize_camera(camera)
    store.set_clip_plan_render_settings(clip_id, render)
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id,
                        on_first_change=lambda: snapshot(store, clip_id, "история: сборка"))
    _tool(ctx, "set_subtitle_words", {"words": words})
    track = _music_track(store, world, ep)
    if track:
        _tool(ctx, "set_music", {"on": True, "track_id": track})
        _tool(ctx, "set_music_duck", {"duck": X._speech_spans(words)})
    elif D.music_off(world, ep):
        if D.MUSIC_NOTE not in notes:
            notes.append(D.MUSIC_NOTE)
    else:
        notes.append("музыка не выбрана (у мира нет трека) — set_music по list_music")
    if (ep.get("options") or {}).get("cut_sfx"):
        result, ok = T.run_tool(ctx, "auto_sfx", {"on": ["cuts"]})
        if not ok:
            notes.append(f"вжухи на склейках не встали: {str((result or {}).get('error') or result)[:160]}")
    mixed = X.auto_mix_clip(ctx, notes)
    S.mark_used(store, [int(p["scene_id"]) for p in placed])
    sfx_count = sum(1 for p in placed if p.get("sfx"))
    version = CG.finish_assembly(store, clip_id, previous)
    if version["note"] and not version["linked"]:
        notes.append(version["note"])
    timeline = {"duration": total, "clip_id": clip_id, "audio_sec": round(audio_sec, 2),
                "editor_url": f"/clips/{clip_id}", "version": version, "subtitle_fixes": sub_fixes, "dry_run": dry,
                "captions": {"timing": "speech" if heard is not None else "text_length", "heard_share": heard,
                             "text": "script" if heard is not None else "script_no_speech"},
                "beats": [{k: p.get(k) for k in ("i", "start", "end", "text", "scene_id", "scene_title", "camera",
                                                  "sfx", "variant", "sentence")} for p in placed],
                "shots_per_min": round(len(placed) / total * 60, 1), "sfx": sfx_count,
                "layers": len(render["layers"]), "camera_keys": len(render["camera"]), "music_track_id": track,
                **({"mix": mixed} if mixed else {})}
    if disaster:
        hud = [lay for lay in render["layers"] if str(lay.get("id") or "").startswith(f"{LAYER_PREFIX}hud")]
        timeline.update({"format": S.FORMAT_DISASTER, "part": D.part_of(ep), "speech_end": speech_end,
                         "part_card": D.part_card_text(ep),
                         "hud": [{"text": h["props"]["text"], "color": h["props"]["color"], "start": h["start"],
                                  "end": h["end"], "x": h["x"], "y": h["y"]} for h in hud],
                         "publish": D.publish_meta(ep)})
    S.set_status(store, episode_id, "done", ("assembling",), clip_id=clip_id, words=words, timeline=timeline,
                 notes=notes, error="")
    out = {"ok": True, "clip_id": clip_id, "duration": total, "beats": len(placed), "layers": len(render["layers"]),
           "shots_per_min": timeline["shots_per_min"], "editor_url": timeline["editor_url"], "notes": notes,
           "version": version, "subtitle_fixes": sub_fixes}
    options = ep.get("options") or {}
    if options.get("render"):
        out["publish"] = finish_publish(store, episode_id, clip_id, cover=options.get("cover", True))
    return out


def finish_publish(store: Any, episode_id: int, clip_id: int, cover: bool = True) -> dict:
    """По желанию: рендер, обложки, заголовок/описание (Gemini может не ответить — эпизод готов всё равно)."""
    from app.render import ClipRenderService

    out: dict[str, Any] = {}
    ep = S.get_episode(store, episode_id)
    notes = list(ep.get("notes") or [])
    disaster = D.is_disaster(S.get_world(store, int(ep["world_id"])), ep)
    try:
        clip = ClipRenderService(store).render_clip_plan(clip_id)
        out["render"] = {"clip": clip.get("id"), "status": clip.get("status")}
        if clip.get("status") == "succeeded" and disaster:
            # Сериал-катастрофа: обложка по шаблону формата и описание «вопрос + своя версия + теги» — без ИИ.
            from app.ai.publish_meta import save_publish_meta

            if cover:
                try:
                    out["covers"] = D.make_covers(store, episode_id, int(clip["id"]))["covers"]
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"обложки не сделаны: {str(exc)[:160]}")
            meta = (ep.get("timeline") or {}).get("publish") or D.publish_meta(ep)
            record = save_publish_meta(store, int(clip["id"]), meta, source="story")
            out["title"] = record["title"]
            out["description"] = record["description"]
        elif clip.get("status") == "succeeded":
            if cover:
                try:
                    from app.cover_variants import generate_covers

                    out["covers"] = len(generate_covers(store, int(clip["id"]), count=3).get("covers") or [])
                except Exception as exc:  # noqa: BLE001
                    notes.append(f"обложки не сделаны: {str(exc)[:160]}")
            try:
                from app.ai.publish_meta import generate_publish_metadata, save_publish_meta

                if settings.gemini_api_key:
                    meta = generate_publish_metadata(store, int(clip["id"]))
                    save_publish_meta(store, int(clip["id"]), meta)
                    out["title"] = meta.get("title")
                else:
                    notes.append("заголовок и описание — без Gemini не сделаны")
            except Exception as exc:  # noqa: BLE001
                notes.append(f"заголовок/описание не сделаны: {str(exc)[:160]}")
        else:
            notes.append(f"рендер: {clip.get('status')} {str(clip.get('error') or '')[:160]}")
    except Exception as exc:  # noqa: BLE001
        notes.append(f"рендер не удался: {str(exc)[:200]}")
    S.update_episode(store, episode_id, notes=notes)
    return out


# --------------------------------------------------------------------------- #
# Ведущий: advance / работник
# --------------------------------------------------------------------------- #

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()
STEP_LABELS = {"new": "сценарий", "scripted": "подбор сцен", "planned": "оценка", "awaiting_approval": "одобрение",
               "generating": "генерация", "assembling": "сборка"}


def _lock(episode_id: int) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(int(episode_id), threading.Lock())


def advance(store: Any, episode_id: int, client: Any = None, max_steps: int = 8) -> dict:
    """Двигать эпизод по шагам, пока не упрётся в ожидание (карточка, запуски) или конец.
    Ошибка шага — ``failed`` с текстом (продолжить — ``resume``/``story_continue``)."""
    lock = _lock(episode_id)
    if not lock.acquire(blocking=False):
        return {**S.get_episode(store, episode_id, full=False), "busy": True}
    try:
        for _ in range(max_steps):
            status = S.get_episode(store, episode_id, full=False)["status"]
            try:
                if status == "new":
                    write_script(store, episode_id, client=client)
                elif status == "scripted":
                    plan_beats(store, episode_id)
                elif status == "planned":
                    if request_generation(store, episode_id)["status"] in ("awaiting_approval", "failed"):
                        break
                elif status in ("awaiting_approval", "generating"):
                    if check_generation(store, episode_id) in ("wait", "failed", "cancelled"):
                        break
                elif status == "assembling":
                    if (assemble(store, episode_id) or {}).get("wait"):
                        break  # 3D-серия ждёт рендер на компьютере (очередь хост-воркера)
                else:
                    break
            except Exception as exc:  # noqa: BLE001 - шаг упал: эпизод ждёт правки
                _log.warning("story %s step %s failed: %s", episode_id, status, exc)
                S.set_status(store, episode_id, "failed", None,
                             error=f"{STEP_LABELS.get(status, status)}: {str(exc)[:500]}")
                break
    finally:
        lock.release()
    return S.get_episode(store, episode_id)


def resume(store: Any, episode_id: int, client: Any = None) -> dict:
    """Продолжить упавший/отменённый эпизод с того шага, где есть данные."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] in ("failed", "cancelled"):
        if not S.narration_text(ep):
            status = "new"
        elif not ep.get("beats"):
            status = "scripted"
        else:
            status = "planned"
        S.set_status(store, episode_id, status, ("failed", "cancelled"), error="")
    return advance(store, episode_id, client=client)


def reassemble(store: Any, episode_id: int) -> dict:
    ep = S.get_episode(store, episode_id, full=False)
    if ep["status"] != "done":
        raise S.StoryError("пересобрать можно готовый эпизод (status done)")
    S.set_status(store, episode_id, "assembling", ("done",))
    return advance(store, episode_id)


def cancel(store: Any, episode_id: int) -> dict:
    ep = S.get_episode(store, episode_id, full=False)
    if ep.get("approval_id"):
        store.db.execute("UPDATE aiv_gen_jobs SET status = 'cancelled', error = 'эпизод отменён' "
                         "WHERE approval_id = ? AND status IN ('awaiting', 'approved', 'held')",
                         (int(ep["approval_id"]),))
        try:
            card = A.get_approval(store, int(ep["approval_id"]))
            if card["status"] == "pending":
                A._update(store, card["id"], status="expired", decision_comment="эпизод отменён")
        except KeyError:
            pass
    S.set_status(store, episode_id, "cancelled", None, error="отменено")
    return S.get_episode(store, episode_id)


def create(store: Any, world_id: int, premise: str, duration: Any = None, tone: str = "", script: dict | None = None,
           options: dict | None = None, requested_by: str = "agent", run: bool = False, client: Any = None) -> dict:
    """Новый эпизод. ``script`` — свой сценарий (без Gemini). ``run`` — сразу вести по шагам (иначе работник)."""
    ep = S.create_episode(store, world_id, premise, duration, tone, options, requested_by)
    if script:
        set_script(store, int(ep["id"]), script, source=requested_by)
    if run:
        return advance(store, int(ep["id"]), client=client)
    kick()
    return S.get_episode(store, int(ep["id"]))


_wake: threading.Event | None = None


def kick() -> None:
    if _wake is not None:
        _wake.set()


class StoriesWorker:
    """Фон: ведёт эпизоды по шагам (сценарий, сцены, карточка, ожидание, сборка). Платные запуски
    выполняет работник ИИ-видео после «Разрешить». ``STORIES_WORKER=0`` — не запускать."""

    IDLE_SECONDS = 5

    def __init__(self, store: Any) -> None:
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        import os

        global _wake
        if os.getenv("STORIES_WORKER", "1").strip().lower() in ("0", "false", "no", "off"):
            return
        if self._thread and self._thread.is_alive() and not self._stop.is_set():
            return
        self._stop = threading.Event()
        _wake = threading.Event()
        self._thread = threading.Thread(target=self._run, args=(self._stop, _wake), name="stories", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if _wake is not None:
            _wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def run_once(self) -> list[int]:
        marks = ", ".join("?" for _ in S.ACTIVE_STATUSES)
        rows = self.store.db.query_all(f"SELECT id FROM story_episodes WHERE status IN ({marks}) ORDER BY id",
                                       S.ACTIVE_STATUSES)
        done = []
        for row in rows:
            advance(self.store, int(row["id"]))
            done.append(int(row["id"]))
        return done

    def _run(self, stop: threading.Event, wake: threading.Event) -> None:
        while not stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - фон не умирает
                _log.warning("stories worker pass failed: %s", exc)
            wake.wait(self.IDLE_SECONDS)
            wake.clear()
