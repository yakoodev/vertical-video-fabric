"""ИИ-видео: сборка серии в обычный холст-клип (docs/design/ai-video-projects.md §1.7).

Каждый план — слой ``image`` с выбранным ключевым кадром на своём отрезке; движение камеры плана:

* ``static`` / ``push_in`` / ``pull_out`` / ``pan_*`` / ``tilt_up|down`` — Ken Burns ключами слоя
  (масштаб и сдвиг; кадр с запасом по краям, чтобы не показать фон);
* ``parallax_push`` / ``orbit`` / ``float`` / ``dolly_zoom`` / ``tilt_reveal`` — наша 3D-камера
  (``app/camera3d.py``) на отрезке плана; на склейке камера возвращается в покой.

Реплики: озвучка этапа ``voice`` (голос персонажа, TTS из ``app/dubbing.py``) ложится по планам
подряд; если реплики длиннее плана — план удлиняется. Все реплики — одна дорожка (слой ``audio``),
субтитры клипа — из тех же реплик с их окнами (слой ``captions``, стиль проекта). Музыка — трек
проекта (паки, бесплатно). Результат — обычный клип: правится в редакторе, рендерится и
публикуется как всё остальное. Каждая сборка — новый клип (правки прошлой сборки не теряются).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import aivideo as V
from app import aivideo_gen as G
from app.settings import settings

LEAD_SEC = 0.35  # от начала плана до первой реплики
GAP_SEC = 0.2  # между репликами
TAIL_SEC = 0.45  # после последней реплики
FADE_SEC = 0.3
SFX_MIN_SEC = 1.0  # реплика-звук персонажа, который не говорит (speech sfx): окно подписи
OVERSCAN = 1.08  # запас кадра: камера/наезд не показывают края
PAN_SCALE = 1.18
PAN_SHIFT = 0.065
LAYER_PREFIX = "aiv_"
CAMERA_PRESET_MOVES = {"parallax_push", "orbit", "float", "dolly_zoom", "tilt_reveal"}
# Один плоский кадр — не сцена со слоями: сильный наклон/облёт показывает его край. Мягче и с запасом.
PRESET_INTENSITY = {"parallax_push": 0.8, "orbit": 0.35, "float": 0.8, "dolly_zoom": 0.35, "tilt_reveal": 0.3}
PRESET_OVERSCAN = 1.35
SOFT = "inOutQuad"


def _keys(move: str, length: float) -> tuple[float, dict]:
    """Ken Burns плана: (база масштаба, ключи слоя в его локальном времени)."""
    end = round(max(0.1, length), 3)
    if move == "push_in":
        return 1.0, {"scale": [{"t": 0, "v": OVERSCAN, "ease": SOFT}, {"t": end, "v": 1.24}]}
    if move == "pull_out":
        return 1.0, {"scale": [{"t": 0, "v": 1.24, "ease": SOFT}, {"t": end, "v": OVERSCAN}]}
    if move in ("pan_left", "pan_right"):
        a, b = (0.5 - PAN_SHIFT, 0.5 + PAN_SHIFT) if move == "pan_left" else (0.5 + PAN_SHIFT, 0.5 - PAN_SHIFT)
        return PAN_SCALE, {"x": [{"t": 0, "v": a, "ease": SOFT}, {"t": end, "v": b}]}
    if move in ("tilt_up", "tilt_down"):
        a, b = (0.5 - PAN_SHIFT, 0.5 + PAN_SHIFT) if move == "tilt_up" else (0.5 + PAN_SHIFT, 0.5 - PAN_SHIFT)
        return PAN_SCALE, {"y": [{"t": 0, "v": a, "ease": SOFT}, {"t": end, "v": b}]}
    if move in CAMERA_PRESET_MOVES:
        return PRESET_OVERSCAN, {}
    # static: лёгкое «дыхание», чтобы кадр не был мёртвым
    return 1.0, {"scale": [{"t": 0, "v": OVERSCAN, "ease": SOFT}, {"t": end, "v": OVERSCAN + 0.035}]}


def _line_audio(store: Any, project_id: int, shot: dict, i: int, line: dict) -> Path | None:
    voice = G._voice_of(store, project_id, line["who"], line.get("emotion") or "")
    ref = V.selected_ref(store, project_id, "line", int(shot["id"]), "line_audio", str(i))
    if not G._fresh(ref) or ref["prompt"] != G._line_key(voice, line["text"]):
        return None
    return G._asset_path(store, ref)


def ensure_voice(store: Any, script: dict) -> dict:
    """Озвучка всех реплик: сухой прогон — сразу (0 ₽); платная — только этапом voice с одобрением."""
    est = G.estimate(store, script["project_id"], "voice", script["id"])
    if not est["count"]:
        return {"status": "ready"}
    if est["paid"]:
        raise G.BudgetError(
            f"озвучка {est['count']} реплик не сделана (≈ {est['total_rub']:.2f} ₽) — этап voice: aiv_request_approval"
            "(stage voice) → «Разрешить» владельца → сборка")
    return G.request_stage(store, script["project_id"], "voice", script["id"], requested_by="assemble")


def timeline(store: Any, script: dict) -> dict:
    """Окна планов и реплик (секунды клипа)."""
    from app.dubbing import probe_duration

    project_id = int(script["project_id"])
    t = 0.0
    shots, lines = [], []
    for shot in script["shots"]:
        placed = []
        for i, line in enumerate(shot["dialogue"]):
            character, speech = G.speech_of(store, project_id, line["who"])
            if speech == "none":
                continue
            path = _line_audio(store, project_id, shot, i, line)
            if path is None:
                raise V.AivError(f"план {shot['idx']}: нет озвучки реплики {i + 1} — этап voice")
            d = max(0.2, probe_duration(path))
            caption = True
            if speech == "sfx":
                # Звук короткий — окно реплики не короче SFX_MIN_SEC (успеть прочитать подпись).
                d = max(d, SFX_MIN_SEC)
                caption = bool((character or {}).get("speech_captions", True))
            placed.append((line, path, d, caption))
        need = (LEAD_SEC + sum(p[2] for p in placed) + GAP_SEC * (len(placed) - 1) + TAIL_SEC) if placed else 0
        length = round(max(float(shot["duration"]), need), 3)
        cursor = t + LEAD_SEC
        for line, path, d, caption in placed:
            lines.append({"shot": shot["idx"], "who": line["who"], "text": line["text"], "start": round(cursor, 3),
                          "end": round(cursor + d, 3), "path": path, "caption": caption})
            cursor += d + GAP_SEC
        shots.append({"shot": shot, "start": round(t, 3), "end": round(t + length, 3),
                      "stretched": round(length - float(shot["duration"]), 2)})
        t += length
    return {"shots": shots, "lines": lines, "total": round(t, 3)}


def assemble(store: Any, script_id: int, music_track_id: int | None = None, captions_style: str | None = None,
             background: Any = None, title: str | None = None) -> dict:
    """Серия → новый холст-клип. Нужны кадры всех планов (этап keyframes); озвучка — этап voice
    (сухой прогон делается тут же бесплатно)."""
    from app.camera3d import PresetError, apply_preset
    from app.canvas import MIN_DURATION_SEC, base_seconds, create_canvas_clip
    from app.clip_subtitles import save_lines
    from app.dubbing import mix_args
    from app.motion import DEFAULT_FOV, normalize_camera, normalize_layers

    script = V.get_script(store, script_id)
    project_id = int(script["project_id"])
    style = V.get_style(store, project_id)
    if not script["shots"]:
        raise V.AivError("в серии нет планов")
    frames = {}
    missing = []
    for shot in script["shots"]:
        ref = V.selected_ref(store, project_id, "shot", int(shot["id"]), "keyframe")
        if not ref or not ref.get("asset_id"):
            missing.append(shot["idx"])
        else:
            frames[shot["id"]] = ref
    if missing:
        raise V.AivError(f"нет кадра у планов {', '.join(map(str, missing))} — этап keyframes "
                         "(aiv_request_approval stage keyframes)")
    voice = ensure_voice(store, script)
    script = V.get_script(store, script_id)
    plan = timeline(store, script)
    total = plan["total"]
    if total < MIN_DURATION_SEC:
        plan["shots"][-1]["end"] = MIN_DURATION_SEC
        total = MIN_DURATION_SEC
    if total > base_seconds():
        raise V.AivError(f"серия {total:.1f} с длиннее предела холста {base_seconds():.0f} с — сократи планы")

    clip = create_canvas_clip(store, total, background or {"kind": "solid", "colors": ["#0b0b10"]},
                              title or f"{script['title']}"[:100],
                              f"ИИ-видео: серия {script['episode_no']} «{script['title']}». {script['logline']}"[:500])
    plan_id = int(clip["id"])
    layers: list[dict] = []
    camera: list[dict] = []
    notes: list[str] = []
    for n, item in enumerate(plan["shots"]):
        shot, t0, t1 = item["shot"], item["start"], item["end"]
        nxt = plan["shots"][n + 1]["shot"] if n + 1 < len(plan["shots"]) else None
        fade_in = shot["transition_in"] == "fade" and n > 0
        fade_out = bool(nxt and nxt["transition_in"] == "fade")
        end = min(total, t1 + (FADE_SEC if fade_out else 0.0))
        move = (shot["camera"] or {}).get("move") or "static"
        base, keys = _keys(move, end - t0)
        layer = {"id": f"{LAYER_PREFIX}shot_{shot['idx']}", "type": "image", "name": f"План {shot['idx']}",
                 "start": round(t0, 3), "end": round(end, 3), "track": 1, "x": 0.5, "y": 0.5, "z": 0,
                 "scale": base, "props": {"asset_id": int(frames[shot["id"]]["asset_id"]), "w": 1080}}
        if keys:
            layer["keys"] = keys
        if fade_in:
            layer["in"] = {"type": "fade", "duration": FADE_SEC}
        layers.append(layer)
        if move in CAMERA_PRESET_MOVES:
            length = min(12.0, max(0.3, t1 - t0))
            try:
                res = apply_preset(move, t0, length, PRESET_INTENSITY.get(move, 1.0), camera=camera, layers=layers,
                                   clip_duration=total)
                camera = res["camera"]
                if move == "parallax_push":
                    # Плоский кадр: лёгкий наклон пресета ничего не даёт, а перспективный перенос
                    # крупной (×1.35) плоскости при наезде срезал край. Чистый dolly — аффинно.
                    camera = [{**k, "rotX": 0.0} if t0 - 1e-3 <= k["t"] <= t1 + 1e-3 and "rotX" in k else k
                              for k in camera]
                    res["keys"] = [{**k, "rotX": 0.0} if "rotX" in k else k for k in res["keys"]]
                last = res["keys"][-1] if res["keys"] else None
                if nxt is not None and last is not None:
                    hold = {k: v for k, v in last.items() if k != "ease"}
                    camera = normalize_camera(camera + [
                        {**hold, "t": round(t1 - 0.02, 3), "ease": "step"},
                        {"t": round(t1, 3), "zoom": 1.0, "x": 0.5, "y": 0.5, "z": 0, "rotX": 0, "rotY": 0,
                         "fov": DEFAULT_FOV}])
            except PresetError as exc:
                notes.append(f"план {shot['idx']}: камера {move} не встала ({exc}) — остался Ken Burns")
        if item["stretched"] > 0.05:
            notes.append(f"план {shot['idx']} удлинён на {item['stretched']:.1f} с под реплики")

    if plan["lines"]:
        work = settings.tmp_dir / f"aiv-mix-{uuid4().hex}"
        work.mkdir(parents=True, exist_ok=True)
        try:
            track = work / "voices.mp3"
            _ffmpeg(mix_args([(Path(l["path"]), l["start"]) for l in plan["lines"]], total, track))
            asset = _save_track(store, track, script)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        layers.append({"id": f"{LAYER_PREFIX}voices", "type": "audio", "name": "Реплики", "start": 0.0,
                       "end": round(total, 3), "props": {"asset_id": int(asset["id"]), "mode": "track",
                                                         "volume": 1.0, "fadeIn": 0.02, "fadeOut": 0.05}})
    # Субтитры — реплики голосом и (если у персонажа включено) подписи к его звукам.
    captions = [{"start": l["start"], "end": l["end"], "text": l["text"]} for l in plan["lines"] if l["caption"]]
    if captions:
        save_lines(store, plan_id, captions)
        layers.append({"id": f"{LAYER_PREFIX}captions", "type": "captions", "name": "Субтитры", "start": 0.0,
                       "end": round(total, 3), "track": 6, "x": 0.5, "y": 0.72,
                       "props": {"style": captions_style or style.get("captions_style") or "hormozi"}})

    render = dict(store.get_clip_plan(plan_id).get("render_settings") or {})
    render["layers"] = normalize_layers(layers)
    render["camera"] = normalize_camera(camera)
    track_id = music_track_id if music_track_id not in (None, "", 0) else style.get("music_track_id")
    if track_id:
        try:
            store.get_audio_track(int(track_id))
            render.update({"music_on": True, "track_id": int(track_id), "music_start_sec": 0.0})
        except (KeyError, TypeError, ValueError):
            notes.append(f"музыка: нет трека {track_id} — без музыки")
    store.set_clip_plan_render_settings(plan_id, render)
    store.db.execute("UPDATE aiv_scripts SET clip_plan_id = ?, status = 'assembled', updated_at = CURRENT_TIMESTAMP "
                     "WHERE id = ?", (plan_id, int(script_id)))
    fresh = store.get_clip_plan(plan_id)
    return {"ok": True, "clip_id": plan_id, "project_id": fresh["source_id"], "duration": round(total, 2),
            "shots": len(plan["shots"]), "lines": len(plan["lines"]), "layers": len(render["layers"]),
            "camera_keys": len(render["camera"]), "voice": voice.get("status"),
            "editor_url": f"/projects/{fresh['source_id']}/candidates?clip={plan_id}", "notes": notes,
            "note": ("обычный холст-клип: правь в редакторе (слои aiv_*), рендер и публикация — как у любого клипа")}


def _ffmpeg(args: list[str]) -> None:
    from app.dubbing import _run

    _run(args)


def _save_track(store: Any, track: Path, script: dict) -> dict:
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"{uuid4().hex}-aiv-voices-{script['id']}.mp3"
    shutil.move(str(track), dest)
    meta = probe_asset(dest)
    return store.create_montage_asset(
        kind="audio", label=f"ИИ-видео: реплики серии {script['episode_no']}"[:120],
        description=f"Озвучка серии «{script['title']}» по репликам персонажей"[:500], tags="ии-видео",
        file_path=str(dest), original_filename=dest.name, mime_type="audio/mpeg", size_bytes=dest.stat().st_size,
        **{**meta, "width": 0, "height": 0})
