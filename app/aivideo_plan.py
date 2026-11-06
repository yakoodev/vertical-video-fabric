"""ИИ-видео: что заказывать по этапу и в каком порядке — планы запусков, зависимости между ними (из ``app/aivideo_gen.py``)."""

from __future__ import annotations

from typing import Any

from app import aivideo as V
from app import projects as P
from app.aivideo_providers import choose_video_mode, video_capabilities
from app.aivideo_budget import ACTIVE, REF_LABELS, EXTENSIONS, _ext_of_kind
from app.aivideo_jobs import _log, _fresh, _is_chain, _usable, _neighbors
from app.aivideo_prompts import (
    STYLE_REF_COUNT, FRAME_KINDS, _quality, _parse_item, _image_item, speech_of, _voice_of, _line_key, _video_item,
    tail_mode, make_tail_start, start_frame_ref, end_frame_ref, _share_item, video_model_for, _video_busy,
    continuity_shot
)

STAGES = ("refs", "blocking", "keyframes", "voice", "video")


def plan_stage(store: Any, project_id: int, stage: str, script_id: int | None = None, items: list | None = None,
               regenerate: bool = False) -> list[dict]:
    """Список запусков этапа (без побочных эффектов). Без ``items`` — всё, чего ещё нет или
    что устарело; с ``items`` — только они (перегенерация — новая версия)."""
    from app.aivideo_providers import get_tts

    ext = EXTENSIONS.get(str(stage or ""))
    if ext is not None:
        V.require_project(store, project_id)
        planned = ext.plan(store, project_id, list(items or []), regenerate)
        for item in planned:
            item.setdefault("script_id", None)
            item["regenerate"] = bool(regenerate or item.get("regenerate"))
        return planned
    stage = P._choice(stage, STAGES, "stage")
    style = V.get_style(store, project_id)
    provider, model = style["provider"], style["image_model"]
    quality = _quality(store, project_id, script_id)
    if stage in ("blocking", "keyframes", "voice", "video"):
        if script_id is None:
            raise V.AivError(f"этап {stage} — для серии: нужен script_id")
        script = V.get_script(store, script_id)
        if int(script["project_id"]) != int(project_id):
            raise V.AivError("серия из другого проекта")
        if not script["shots"]:
            raise V.AivError("в серии нет планов — сначала сценарий (aiv_write_script / aiv_update_script)")
    wanted = {_parse_item(i) for i in items or []}
    explicit = bool(wanted)

    def want(target_type: str, target_id: int, kind: str, slot: str = "") -> bool | None:
        if not explicit:
            return None
        return any(t == target_type and i == target_id and (not k or k == kind) and (not s or s == slot)
                   for t, i, k, s in wanted)

    def need(ref: dict | None, target_type: str, target_id: int, kind: str, slot: str = "") -> bool:
        hit = want(target_type, target_id, kind, slot)
        if hit is not None:
            return hit
        return regenerate or not _fresh(ref)

    out: list[dict] = []
    if stage == "refs":
        for n in range(1, STYLE_REF_COUNT + 1):
            ref = V.selected_ref(store, project_id, "style", 0, "style", str(n))
            if need(ref, "style", 0, "style", str(n)):
                out.append(_image_item(stage, "style", 0, "style", f"стиль {n}", model, quality, provider, str(n)))
        for loc in V.list_locations(store, project_id):
            ref = V.selected_ref(store, project_id, "location", loc["id"], "location")
            if need(ref, "location", loc["id"], "location"):
                out.append(_image_item(stage, "location", loc["id"], "location", f"локация «{loc['name']}»", model,
                                       quality, provider))
        for c in V.list_characters(store, project_id):
            for kind in ("turnaround", "expressions"):
                ref = V.selected_ref(store, project_id, "character", c["id"], kind)
                if need(ref, "character", c["id"], kind):
                    out.append(_image_item(stage, "character", c["id"], kind, f"{c['name']}: {REF_LABELS[kind]}",
                                           model, quality, provider))
            # Облик — img2img-вариант листа: база — разворот (или картинки владельца) персонажа.
            turn = V.selected_ref(store, project_id, "character", c["id"], "turnaround")
            base = _fresh(turn) or any(i["target_type"] == "character" and i["target_id"] == c["id"]
                                       and i["ref_kind"] == "turnaround" for i in out) or bool(
                V.list_refs(store, project_id, "character", c["id"], "upload"))
            for look in c["looks"]:
                ref = V.selected_ref(store, project_id, "look", look["id"], "look")
                if need(ref, "look", look["id"], "look"):
                    item = _image_item(stage, "look", look["id"], "look", f"{c['name']}: облик «{look['name']}»",
                                       model, quality, provider)
                    if not base:
                        item["blocked"] = (f"облик «{look['name']}» — правка листа {c['name']}: сначала разворот "
                                           f"(character:{c['id']}:turnaround)")
                    out.append(item)
    elif stage == "blocking":
        # 3D-макет плана (старт и, у плана с видео, конец) — 0 ₽, компоновщиком.
        from app import aivideo_blocking as B

        for shot in script["shots"]:
            if shot["locked"] and not explicit:
                continue
            todo = B.needs_render(store, project_id, shot)
            for which in ("start", "end") if shot["needs_motion"] else ("start",):
                hit = want("shot", shot["id"], f"blocking_{which}")
                if hit is False or (hit is None and which not in todo and not regenerate):
                    continue
                out.append({"stage": stage, "kind": "blocking", "target_type": "shot", "target_id": int(shot["id"]),
                            "ref_kind": f"blocking_{which}", "slot": "", "script_id": int(script_id),
                            "label": f"план {shot['idx']}: блокинг {'старт' if which == 'start' else 'конец'}",
                            "provider": "none", "model": "scene", "estimate_rub": 0.0,
                            "price_note": "блокинг — наш движок сцен, 0 ₽"})
    elif stage == "keyframes":
        # Пары ключевых кадров по порядку: старт плана (если он не общий с концом прошлого) и конец
        # плана с видео. Конец плана N = старт сцепленного плана N+1 — один кадр на двоих (N+1 кадров
        # на цепочку из N планов), ничего не вынимается из видео — ошибки не копятся.
        # Видеомодель без конечного кадра (H3, Grok): конечные кадры не заказываем — видео к ним не
        # приходит; старт продолжения — последний кадр готового видео прошлого плана (после этапа video).
        shots = script["shots"]
        tail = tail_mode(store, project_id, script_id)
        for n, shot in enumerate(shots):
            if shot["locked"] and not explicit:
                continue
            prev = shots[n - 1] if n else None
            pair = bool(shot["needs_motion"])
            if _is_chain(shot, prev) and tail:
                out.append({**_share_item(shot, prev, script["id"]),
                            "label": f"план {shot['idx']}: старт — последний кадр видео плана {prev['idx']}",
                            "skipped": f"план {shot['idx']}: старт — последний кадр видео плана {prev['idx']} "
                                       "(после этапа video, 0 ₽)"})
            elif _is_chain(shot, prev):
                out.append({**_share_item(shot, prev, script["id"]),
                            "skipped": f"план {shot['idx']}: старт — конечный кадр плана {prev['idx']} (общий кадр, "
                                       "0 ₽)"})
            else:
                ref = V.selected_ref(store, project_id, "shot", shot["id"], "keyframe")
                if need(ref, "shot", shot["id"], "keyframe"):
                    item = _image_item(stage, "shot", shot["id"], "keyframe",
                                       f"план {shot['idx']}: старт" if pair else f"план {shot['idx']}", model,
                                       quality, provider)
                    out.append({**item, "script_id": int(script_id)})
            if pair and (not tail or want("shot", shot["id"], "endframe")):
                ref = V.selected_ref(store, project_id, "shot", shot["id"], "endframe")
                if need(ref, "shot", shot["id"], "endframe"):
                    item = _image_item(stage, "shot", shot["id"], "endframe", f"план {shot['idx']}: конец", model,
                                       quality, provider)
                    out.append({**item, "script_id": int(script_id)})
    elif stage == "video":
        out += _plan_video(store, project_id, script, style, explicit, want, need)
    else:  # voice
        for shot in script["shots"]:
            for i, line in enumerate(shot["dialogue"]):
                if speech_of(store, project_id, line["who"])[1] == "none":
                    continue  # молчит — реплики нет (normalize_shot их уже убирает)
                voice = _voice_of(store, project_id, line["who"], line.get("emotion") or "")
                ref = V.selected_ref(store, project_id, "line", shot["id"], "line_audio", str(i))
                current = _fresh(ref) and ref["prompt"] == _line_key(voice, line["text"])
                hit = want("line", shot["id"], "line_audio", str(i))
                if hit is False or (hit is None and current and not regenerate):
                    continue
                if voice["model"] == "sfx":
                    out.append({"stage": stage, "kind": "sfx", "target_type": "line", "target_id": int(shot["id"]),
                                "ref_kind": "line_audio", "slot": str(i), "script_id": int(script_id),
                                "label": f"план {shot['idx']}, звук {i + 1} ({line['who']})", "provider": "none",
                                "model": "sfx", "voice": voice["voice"], "text": line["text"], "chars": 0,
                                "estimate_rub": 0.0, "price_note": "звук пака (запись), 0 ₽"})
                    continue
                tts = get_tts(voice["provider"], voice["model"], voice["voice"], voice.get("style"))
                price = tts.price_rub_per_char() if getattr(tts, "paid", False) else 0.0
                free_note = "Gemini TTS, бесплатно" if voice["provider"] == "gemini" else "сухой прогон"
                out.append({"stage": stage, "kind": "tts", "target_type": "line", "target_id": int(shot["id"]),
                            "ref_kind": "line_audio", "slot": str(i), "script_id": int(script_id),
                            "label": f"план {shot['idx']}, реплика {i + 1} ({line['who']})",
                            "provider": voice["provider"], "model": voice["model"], "voice": voice["voice"],
                            **({"style": voice["style"]} if voice.get("style") else {}),
                            "text": line["text"], "chars": len(line["text"]),
                            "estimate_rub": round(len(line["text"]) * price, 2),
                            "price_note": free_note if not price else "цена TTS из app/dubbing.py"})
    for item in out:
        item.setdefault("script_id", script_id)
        item["regenerate"] = bool(regenerate or explicit)
    return out


def _plan_video(store: Any, project_id: int, script: dict, style: dict, explicit: bool, want: Any,
                need: Any) -> list[dict]:
    """Видео планов с needs_motion. Старт — стартовый кадр плана (свой или конечный кадр прошлого),
    конец — конечный кадр плана; модель с первым+последним кадром ИНТЕРПОЛИРУЕТ между ними. Нет
    старта (или конца у такой модели) — элемент ``blocked``: сначала этап keyframes."""
    provider, res = style["video_provider"], style["video_resolution"]
    model = video_model_for(style, script.get("quality"))
    caps = video_capabilities(model, provider)
    out: list[dict] = []
    shots = script["shots"]
    for n, shot in enumerate(shots):
        if not shot["needs_motion"]:
            continue
        if shot["locked"] and not explicit:
            continue
        ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "shot_video")
        if not need(ref, "shot", int(shot["id"]), "shot_video"):
            continue
        start, shared = start_frame_ref(store, project_id, shots, n)
        end = end_frame_ref(store, project_id, shot)
        tail = bool(shared) and not caps.get("first_last")
        item = _video_item(shot, script["id"], provider, model, res,
                           "video_tail" if tail else ("shared" if shared else "keyframe"))
        item["mode"] = choose_video_mode(model, end is not None, provider=provider)
        if start is None and tail:
            # Старт — хвост видео прошлого плана: есть видео, оно в этой же партии или уже в работе —
            # запуск ждёт его в очереди и вынимает последний кадр сам.
            prev = shots[n - 1]
            ready = _usable(V.selected_ref(store, project_id, "shot", int(prev["id"]), "shot_video"))
            planned = any(i["target_id"] == int(prev["id"]) and not i.get("blocked") for i in out)
            if ready or planned or _video_busy(store, int(prev["id"])):
                item["after"] = int(prev["id"])
                out.append(item)
            else:
                out.append({**item, "blocked": f"план {shot['idx']}: старт — последний кадр видео плана "
                                                f"{prev['idx']}: сначала видео плана {prev['idx']}"})
            continue
        if start is None:
            where = f"конечного кадра плана {shared}" if shared else "стартового кадра"
            out.append({**item, "blocked": f"план {shot['idx']}: нет {where} — сначала этап keyframes"})
            continue
        if caps.get("first_last") and end is None:
            out.append({**item, "blocked": f"план {shot['idx']}: нет конечного кадра — этап keyframes (модель "
                                            "интерполирует старт → конец)"})
            continue
        out.append(item)
    return out


def _frame_deps(store: Any, job: dict) -> list[tuple[int, str]]:
    """От чего зависит картинка (её делают в той же партии): облик/эмоции — от разворота персонажа;
    конечный кадр — от стартового кадра плана (или конечного кадра прошлого); стартовый — от
    последнего кадра прошлого плана."""
    kind = job.get("ref_kind")
    if job.get("kind") != "image":
        return []
    ext = _ext_of_kind(str(kind or ""))
    if ext is not None:
        # Расширение (истории: сцена ждёт листы актёров и плиту) — свои зависимости или никаких.
        return list(ext.deps(store, job)) if hasattr(ext, "deps") else []
    if kind in ("look", "expressions"):
        cid = int(job["target_id"]) if kind == "expressions" else int(V.get_look(store, int(job["target_id"]))[
            "character_id"])
        return [(cid, "character:turnaround")]
    if kind not in FRAME_KINDS:
        return []
    shot = V.get_shot(store, int(job["target_id"]))
    shots = V.list_shots(store, int(shot["script_id"]))
    n = next(i for i, s in enumerate(shots) if int(s["id"]) == int(shot["id"]))
    prev = shots[n - 1] if n else None
    if kind == "endframe":
        if _is_chain(shot, prev):
            return [(int(prev["id"]), "shot:endframe")]
        return [(int(shot["id"]), "shot:keyframe")]
    cont, _adjacent = continuity_shot(shots, n)  # кадр непрерывности — та же локация (см. _frame_request)
    if cont is None:
        return []
    deps = [(int(cont["id"]), "shot:endframe")] if cont.get("needs_motion") else []
    return deps + [(int(cont["id"]), "shot:keyframe")]


def _video_deps(store: Any, job: dict) -> list[tuple[int, str]]:
    """Видео сцепленного плана у модели без конечного кадра стартует с последнего кадра видео прошлого
    плана — ждёт его готовности."""
    if job.get("kind") != "video" or job.get("target_type") != "shot":
        return []
    shot = V.get_shot(store, int(job["target_id"]))
    prev, _nxt = _neighbors(store, shot)
    if not _is_chain(shot, prev):
        return []
    caps = video_capabilities(job.get("model") or "", job.get("provider"))
    if caps.get("first_last") and (job.get("params") or {}).get("start") != "video_tail":
        return []
    return [(int(prev["id"]), "shot:shot_video")]


def _deps_busy(store: Any, job: dict) -> bool:
    marks = ", ".join("?" for _ in ACTIVE)
    for target_id, what in _frame_deps(store, job) + _video_deps(store, job):
        target_type, ref_kind = what.split(":")
        kind = "video" if ref_kind == "shot_video" else "image"
        row = store.db.query_one(
            f"SELECT id FROM aiv_gen_jobs WHERE kind = ? AND target_type = ? AND target_id = ? AND ref_kind = ? "
            f"AND id != ? AND status IN ({marks}) LIMIT 1", (kind, target_type, int(target_id), ref_kind,
                                                             int(job["id"]), *ACTIVE))
        if row:
            return True
    return False


def _after_video(store: Any, job: dict) -> None:
    """Новое видео плана: если видеомодель не берёт конечный кадр, а следующий план его продолжает —
    последний кадр этого видео становится стартом следующего (``source=video_tail``), видео следующего
    устаревает. У моделей с первым+последним кадром ничего не сцепляется (старт — общий конечный кадр)."""
    if job.get("target_type") != "shot":
        return None
    shot = V.get_shot(store, int(job["target_id"]))
    _prev, nxt = _neighbors(store, shot)
    if not nxt or not _is_chain(nxt, shot):
        return None
    project_id = int(job["project_id"])
    if not tail_mode(store, project_id, shot.get("script_id")):
        return None
    try:
        make_tail_start(store, project_id, shot, nxt, job)
    except Exception as exc:  # noqa: BLE001 - видео готово; старт следующего вынется при его запуске
        _log.warning("aiv: хвост видео плана %s не вынут: %s", shot["idx"], exc)
    return None


def _after_frame(store: Any, job: dict) -> None:
    """Новый ключевой кадр → видео, которые на нём стоят, «устарели»: конечный кадр плана — его
    видео и видео сцепленного следующего (это его старт); стартовый — видео этого плана."""
    if job.get("ref_kind") not in FRAME_KINDS:
        return
    shot = V.get_shot(store, int(job["target_id"]))
    store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? AND kind = 'shot_video'",
                     (int(shot["id"]),))
    if job["ref_kind"] == "endframe":
        _prev, nxt = _neighbors(store, shot)
        if nxt and _is_chain(nxt, shot):
            store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                             "AND kind = 'shot_video'", (int(nxt["id"]),))
