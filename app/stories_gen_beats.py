"""Истории: биты — разбивка, сцены, правка (из ``app/stories_gen.py``)."""

from __future__ import annotations

import math
import re
from typing import Any

from app import projects as P
from app import stories as S
from app.stories_gen_script import CPS, CAMERA_MOVES

BEAT_MIN_SEC = 1.2
BEAT_MAX_SEC = 3.0
BEAT_MERGE_SEC = 0.7  # кусок фразы короче — сливается с соседним


MAX_NEW_SCENES = 40


CAMERA_CYCLE = ("push_in", "pan_right", "pull_out", "pan_left", "parallax_push", "tilt_up")
CAMERA_ALT = {"push_in": "pull_out", "pull_out": "push_in", "pan_left": "pan_right", "pan_right": "pan_left",
              "tilt_up": "tilt_down", "tilt_down": "tilt_up", "parallax_push": "pan_left", "static": "push_in"}


# --------------------------------------------------------------------------- #
# 2. Биты и сцены
# --------------------------------------------------------------------------- #


def _est(text: str) -> float:
    return len(text) / CPS


def split_text(text: str, n: int) -> list[str]:
    """Фраза → ``n`` кусков примерно поровну по длине, разрез — на границе слова (лучше после запятой,
    двоеточия, тире)."""
    words = text.split()
    n = max(1, min(n, len(words)))
    if n == 1:
        return [text]
    ends, pos = [], 0
    for w in words:
        pos += len(w) + 1
        ends.append(pos)
    total = ends[-1]
    cuts: list[int] = []
    prev = 0
    for j in range(1, n):
        quota = total * j / n
        lo, hi = prev + 1, len(words) - (n - j)
        best, best_cost = lo, float("inf")
        for k in range(lo, hi + 1):
            cost = abs(ends[k - 1] - quota) - (0.25 * total / n if re.search(r"[,:;—–]$", words[k - 1]) else 0.0)
            if cost < best_cost:
                best, best_cost = k, cost
        cuts.append(best)
        prev = best
    out, start = [], 0
    for c in cuts + [len(words)]:
        out.append(" ".join(words[start:c]))
        start = c
    return [p for p in out if p]


def split_beats(script: dict) -> list[dict]:
    """Фразы → биты: по кадру фразы на кусок (1.2–3 с по длине текста); длинная фраза с одним кадром —
    тот же кадр ещё раз (``variant: alt`` — другое движение камеры)."""
    beats: list[dict] = []
    for n, sentence in enumerate(script.get("sentences") or []):
        text, shots = sentence["text"], list(sentence.get("shots") or [])
        est = _est(text)
        need = max(1, math.ceil(est / BEAT_MAX_SEC))
        fit = max(1, int(est // BEAT_MIN_SEC))
        count = max(need, min(len(shots) or 1, fit))
        pieces = split_text(text, count)
        for j, piece in enumerate(pieces):
            if shots:
                k = min(j, len(shots) - 1)
                beat = {"text": piece, "sentence": n, "shot": k, "spec": dict(shots[k]),
                        "variant": "alt" if j >= len(shots) else None}
            else:
                beat = {"text": piece, "sentence": n, "shot": None, "spec": None, "variant": None}
            beats.append(beat)
    merged: list[dict] = []
    for beat in beats:
        prev = merged[-1] if merged else None
        if (prev and _est(beat["text"]) < BEAT_MERGE_SEC and prev["sentence"] == beat["sentence"]
                and _est(prev["text"] + " " + beat["text"]) <= BEAT_MAX_SEC + 0.6):
            prev["text"] = f"{prev['text']} {beat['text']}"
            continue
        merged.append(dict(beat))
    for i, beat in enumerate(merged):
        beat["i"] = i
        beat["est_sec"] = round(_est(beat["text"]), 2)
    return merged


def _pick_camera(want: str, prev: str | None, n: int) -> str:
    move = want if want in CAMERA_MOVES else CAMERA_CYCLE[n % len(CAMERA_CYCLE)]
    if prev and move == prev:
        move = CAMERA_ALT.get(move, "push_in")
    return move


def _scene_for_text(scenes: list[dict], text: str, avoid: set[int]) -> dict | None:
    """Без кадра в сценарии: готовая сцена, где больше общих основ с текстом бита (соседние — разные)."""
    from app import explainer as E

    words = E.stems(text)
    ranked = sorted(((len(words & (S.scene_terms(s) | E.stems(s.get("title")))), -int(s.get("uses") or 0), s)
                     for s in scenes if s["ready"]), key=lambda x: (-x[0], x[1], int(x[2]["id"])))
    for _score, _uses, scene in ranked:
        if int(scene["id"]) not in avoid:
            return scene
    return ranked[0][2] if ranked else None


def plan_beats(store: Any, episode_id: int) -> dict:
    """Биты эпизода со сценами (готовые из библиотеки или новые), камерой и звуками."""
    ep = S.get_episode(store, episode_id)
    if not S.narration_text(ep):
        raise S.StoryError("у эпизода нет сценария")
    world = S.get_world(store, int(ep["world_id"]))
    beats = split_beats(ep["script"])
    if S.is_3d(ep):
        # 3D-движок: картинок сцен нет — биты только для времени фраз, HUD и звуков (кадры ставит режиссура
        # app/series3d.py по словам озвучки при сборке).
        return _plan_beats_3d(store, episode_id, beats)
    library = S.list_scenes(store, int(world["id"]))
    by_id = {int(s["id"]): s for s in library}
    created: list[dict] = []
    notes: list[str] = []
    options = S.sfx_options(store, int(ep["project_id"]))
    missing_sfx: list[str] = []
    prev_camera: str | None = None
    for n, beat in enumerate(beats):
        spec = beat.get("spec")
        prev = beats[n - 1] if n else None
        if prev and beat.get("variant") == "alt" and prev.get("sentence") == beat["sentence"]:
            beat["scene_id"] = prev["scene_id"]
        elif spec and spec.get("scene_id") and int(spec["scene_id"]) in by_id:
            beat["scene_id"] = int(spec["scene_id"])
        elif spec:
            hit, sim = S.match_scene(library, spec)
            if hit is not None:
                beat["scene_id"] = int(hit["id"])
                if int(hit["id"]) in {int(c["id"]) for c in created}:
                    beat["reuse"] = "episode"
                else:
                    beat["reuse"] = "library"
                    notes.append(f"бит {beat['i']}: сцена #{hit['id']} «{P._text(hit['title'], 40)}» из библиотеки "
                                 f"(сходство {sim})")
            elif len(created) >= MAX_NEW_SCENES:
                alt = _scene_for_text([s for s in library if s["ready"]] or library, beat["text"], set())
                if alt is None:
                    raise S.StoryError(f"больше {MAX_NEW_SCENES} новых сцен на эпизод — сократи кадры")
                beat["scene_id"] = int(alt["id"])
                notes.append(f"бит {beat['i']}: больше {MAX_NEW_SCENES} новых сцен не рисую — взял #{alt['id']}")
            else:
                scene = S.save_scene(store, int(world["id"]), location_id=spec.get("location_id") or 0,
                                     cast=spec.get("cast") or [], action=spec["action"], accent=spec.get("accent") or "",
                                     tags=spec.get("tags") or [], title=P._text(beat["text"], 60), origin="generated")
                library.append(scene)
                created.append(scene)
                by_id[int(scene["id"])] = scene
                beat["scene_id"] = int(scene["id"])
        else:
            avoid = {int(prev["scene_id"])} if prev and prev.get("scene_id") else set()
            alt = _scene_for_text(library, beat["text"], avoid)
            if alt is None:
                raise S.StoryError("у фраз нет кадров, а библиотека сцен пуста — опиши кадры (story_set_script с "
                                   "shots) или добавь сцены (story_add_scene)")
            beat["scene_id"] = int(alt["id"])
            notes.append(f"бит {beat['i']}: кадра в сценарии нет — сцена по словам #{alt['id']}")
        if prev and prev.get("scene_id") == beat["scene_id"] and beat.get("variant") != "alt":
            beat["variant"] = "alt"  # та же сцена подряд — другое движение камеры
        beat["camera"] = _pick_camera((spec or {}).get("camera") or "", prev_camera, n)
        prev_camera = beat["camera"]
        tag = (spec or {}).get("sfx") if beat.get("variant") != "alt" else ""
        beat["sfx"] = None
        if tag:
            hit = S.resolve_sfx(tag, options)
            if hit is not None:
                beat["sfx"] = {"tag": tag, "ref": hit["ref"], "label": hit["label"]}
            elif tag not in missing_sfx:
                missing_sfx.append(tag)
    if missing_sfx:
        notes.append("нет записей для звуков: " + ", ".join(f"«{t}»" for t in missing_sfx[:8])
                     + " — синтетику не ставлю; попроси у владельца ссылку и добавь add_sound_from_url (тег «звук»)")
    _decorate(store, int(world["id"]), beats)
    new = sorted({b["scene_id"] for b in beats if b.get("scene_new")})
    reused = sorted({b["scene_id"] for b in beats} - set(new))
    S.set_status(store, episode_id, "planned", None, beats=beats, notes=notes, error="")
    return {"ok": True, "episode_id": int(episode_id), "beats": len(beats), "scenes": len({b["scene_id"] for b in beats}),
            "new_scenes": new, "reused_scenes": reused, "notes": notes}


def _plan_beats_3d(store: Any, episode_id: int, beats: list[dict]) -> dict:
    """Биты 3D-серии: без сцен библиотеки (``scene_id`` нет), звук кадра — настоящая запись по тегу сценария."""
    options = S.sfx_options(store, int(S.get_episode(store, episode_id)["project_id"]))
    notes: list[str] = ["3D-движок: сцены не рисуются — кадры, свет и звуки событий ставит режиссура по озвучке"]
    for beat in beats:
        tag = (beat.get("spec") or {}).get("sfx") if beat.get("variant") != "alt" else ""
        hit = S.resolve_sfx(tag, options) if tag else None
        beat.update({"scene_id": None, "scene_title": "", "scene_new": False, "camera": "",
                     "sfx": {"tag": tag, "ref": hit["ref"], "label": hit["label"]} if hit else None})
    S.set_status(store, episode_id, "planned", None, beats=beats, notes=notes, error="")
    return {"ok": True, "episode_id": int(episode_id), "beats": len(beats), "scenes": 0, "new_scenes": [],
            "reused_scenes": [], "notes": notes, "engine": S.ENGINE_3D}


def _decorate(store: Any, world_id: int, beats: list[dict]) -> None:
    scenes = {int(s["id"]): s for s in S.list_scenes(store, world_id)}
    for beat in beats:
        scene = scenes.get(int(beat.get("scene_id") or 0))
        beat["scene_title"] = scene["title"] if scene else ""
        beat["scene_new"] = bool(scene and not scene["ready"])


def update_beats(store: Any, episode_id: int, changes: list[dict]) -> dict:
    """Агент/владелец правит биты до сборки: ``[{i, scene_id | new_scene {location_id, cast, action, tags,
    accent}, camera?, sfx?}]`` → эпизод снова «планируется» (новая оценка и карточка)."""
    ep = S.get_episode(store, episode_id)
    if ep["status"] == "assembling":
        raise S.StoryError("эпизод собирается — дождись сборки")
    beats = ep.get("beats") or []
    if not beats:
        raise S.StoryError("сначала биты: story_continue (после сценария)")
    world = S.get_world(store, int(ep["world_id"]))
    library = S.list_scenes(store, int(world["id"]))
    by_i = {int(b["i"]): b for b in beats}
    problems = []
    options = None
    for ch in changes or []:
        if not isinstance(ch, dict) or ch.get("i") is None or int(ch["i"]) not in by_i:
            problems.append(f"нет бита {ch.get('i') if isinstance(ch, dict) else ch!r} (0–{len(beats) - 1})")
            continue
        beat = by_i[int(ch["i"])]
        if ch.get("scene_id"):
            scene = S.get_scene(store, int(ch["scene_id"]))
            if int(scene["world_id"]) != int(world["id"]):
                problems.append(f"бит {beat['i']}: сцена {scene['id']} другого мира")
                continue
            beat["scene_id"] = int(scene["id"])
        elif isinstance(ch.get("new_scene"), dict):
            spec = dict(ch["new_scene"])
            spec["cast"] = spec.get("cast") or spec.get("characters") or []
            hit, _sim = S.match_scene(library, {**spec, "location_id": spec.get("location_id") or 0})
            scene = hit or S.save_scene(store, int(world["id"]), location_id=spec.get("location_id") or 0,
                                        cast=spec["cast"], action=spec.get("action"), accent=spec.get("accent") or "",
                                        tags=spec.get("tags") or [], title=spec.get("title") or P._text(beat["text"], 60),
                                        origin="agent")
            if hit is None:
                library.append(scene)
            beat["scene_id"] = int(scene["id"])
        if ch.get("camera"):
            if ch["camera"] not in CAMERA_MOVES:
                problems.append(f"бит {beat['i']}: camera — {', '.join(CAMERA_MOVES)}")
                continue
            beat["camera"] = ch["camera"]
        if "sfx" in ch:
            tag = P._text(ch.get("sfx"), 120)
            if not tag:
                beat["sfx"] = None
            else:
                options = options if options is not None else S.sfx_options(store, int(world["project_id"]))
                hit = S.resolve_sfx(tag, options)
                if hit is None:
                    problems.append(f"бит {beat['i']}: записи «{tag}» нет (паки, «Файлы для монтажа» с тегом «звук») — "
                                    "add_sound_from_url по ссылке владельца")
                    continue
                beat["sfx"] = {"tag": tag, "ref": hit["ref"], "label": hit["label"]}
    if problems:
        raise S.StoryError("биты не приняты: " + "; ".join(problems))
    _decorate(store, int(world["id"]), beats)
    # Клип и слова прошлой сборки остаются: новая сборка станет его версией и перенесёт правки субтитров.
    S.set_status(store, episode_id, "planned", None, beats=beats, error="", timeline={})
    return S.get_episode(store, episode_id)
