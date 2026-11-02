"""Истории: сборка эпизода на своём 3D-движке (``options.engine: "3d"``) — без картинок сцен и без платного.

Озвучка (Gemini TTS, бесплатно) → слова (Whisper; без него — по длине текста) → режиссура
``app.series3d.direct`` (локация-шаблон, герой-игрушка по карточке актёра, кадр на фразу, события на словах) →
рендер сцены ``node/scene3d`` в mp4 (headless Chromium, ``SCENE3D_GL``/``SCENE3D_WORKERS``) → видео в «Файлы для
монтажа» → холст-клип: 3D-видео во весь кадр, субтитры по 1–3 слова с подсветкой текущего, HUD-плашки фраз,
название серии и титр «ЧАСТЬ N+1» (сериал-катастрофа), звуки событий (настоящие записи паков), голос. Музыка у
сериала-катастрофы не вшивается. Обложки — кадры 3D-видео по шаблону формата.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import series3d as S3
from app import stories as S
from app import stories_disaster as D
from app.settings import settings

_log = logging.getLogger(__name__)

CAPTION_WORDS = 3  # субтитры по 1–3 слова, текущее подсвечено
ACTIVE_COLOR = "#ffd400"
SFX_MAX_SEC = 3.5
COVER_FRAMES = 3


def render3d(result: dict, out_path: Path) -> dict:
    """Сцена → mp4 (тесты подменяют: настоящий рендер — минуты)."""
    return S3.render_episode(result, out_path)


def hero_card(store: Any, ep: dict) -> str:
    """Карточка главного героя: первый актёр в кадрах сценария, иначе первый актёр мира."""
    for sentence in (ep.get("script") or {}).get("sentences") or []:
        for shot in sentence.get("shots") or []:
            for cid in shot.get("cast") or []:
                try:
                    return str(S.get_character(store, int(cid)).get("identity_card") or "")
                except KeyError:
                    continue
    chars = S.list_characters(store, int(ep["world_id"]))
    return str(chars[0].get("identity_card") or "") if chars else ""


def _captions(world: dict) -> dict:
    from app.stories_gen import _captions_props

    props = _captions_props(world)
    props["maxWords"] = CAPTION_WORDS
    if str(props.get("activeColor") or "").lower() == str(props.get("color") or "").lower():
        props["activeColor"] = ACTIVE_COLOR
    return props


def _frames(video: Path, times: list[float], out_dir: Path, stem: str) -> list[str]:
    """Кадры видео (JPEG) на заданных секундах — для обложек."""
    out = []
    for n, t in enumerate(times):
        path = out_dir / f"{stem}-cover{n}.jpg"
        proc = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
                               "-frames:v", "1", "-q:v", "3", str(path)], capture_output=True, text=True, check=False)
        if proc.returncode == 0 and path.is_file():
            out.append(str(path))
    return out


def cover_times(shots: list[dict]) -> list[float]:
    """Секунды для обложек: герой за спиной в начале истории, та самая ошибка, жуткая деталь в финале."""
    picks: list[float] = []

    def mid(s: dict) -> float:
        return round(s["t0"] + (s["t1"] - s["t0"]) * 0.6, 3)

    for want in (lambda s: s["camera"] == "ots" and s["role"] not in ("hook", ""),
                 lambda s: s["role"] == "mistake", lambda s: s["role"] == "finale" and s["camera"] == "detail",
                 lambda s: s["camera"] == "face"):
        hit = next((s for s in shots if want(s) and mid(s) not in picks), None)
        if hit is not None:
            picks.append(mid(hit))
        if len(picks) >= COVER_FRAMES:
            break
    return picks or [mid(shots[len(shots) // 2])]


def sfx_layers(store: Any, result: dict, placed: list[dict], total: float, prefix: str) -> list[dict]:
    """Звуки: события режиссуры (блэкаут, рывок, стук…) и звук кадра из сценария — настоящие записи, без дублей."""
    from app.stories_gen import SFX_OFFSET_SEC, SFX_VOLUME, _sfx_duration

    items = [dict(x) for x in result.get("sfx") or []]
    for p in placed:
        sfx = p.get("sfx") or {}
        if sfx.get("ref"):
            t = round(min(p["end"] - 0.2, p["start"] + SFX_OFFSET_SEC), 3)
            if not any(abs(x["t"] - t) < 0.6 for x in items):
                items.append({"t": t, "ref": str(sfx["ref"]), "volume": SFX_VOLUME, "label": sfx.get("label") or ""})
    layers = []
    for n, x in enumerate(sorted(items, key=lambda i: i["t"])):
        dur = min(SFX_MAX_SEC, max(0.3, _sfx_duration(store, str(x["ref"]))))
        start = max(0.0, float(x["t"]))
        if start >= total - 0.1:
            continue
        layers.append({"id": f"{prefix}fx{n:02d}", "type": "audio", "name": f"Звук: {x.get('label') or ''}"[:60],
                       "start": round(start, 3), "end": round(min(total, start + dur), 3),
                       "props": {"asset_id": str(x["ref"]), "mode": "track", "volume": float(x.get("volume") or 0.6),
                                 "fadeOut": 0.15, "role": "sfx"}})
    return layers


def save_video(store: Any, path: Path, label: str) -> dict:
    """mp4 3D-серии → «Файлы для монтажа» (видео для слоя холста)."""
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"series3d-{uuid4().hex[:12]}.mp4"
    shutil.move(str(path), dest)
    meta = probe_asset(dest)
    return store.create_montage_asset(kind="video", label=label[:120], description="3D-серия (свой движок)",
                                      tags="3d,серия", file_path=str(dest), original_filename=dest.name,
                                      mime_type="video/mp4", size_bytes=dest.stat().st_size, **meta)


def assemble(store: Any, episode_id: int) -> dict:
    """Озвучка → слова → режиссура → 3D-рендер → холст-клип с субтитрами, HUD, титрами и звуками."""
    from app import agent_tools as T
    from app import stories_gen as SG
    from app.aivideo_gen import _asset_path
    from app.canvas import MIN_DURATION_SEC, base_seconds, create_canvas_clip
    from app.clip_history import snapshot
    from app.dubbing import probe_duration
    from app.explainer_gen import auto_mix_clip, proportional_words
    from app.motion import normalize_camera, normalize_layers

    ep = S.get_episode(store, episode_id)
    world = S.get_world(store, int(ep["world_id"]))
    ref = S.narration_ref(store, ep)
    if ref is None:
        raise S.StoryError("нет озвучки эпизода — этап генерации")
    path = _asset_path(store, ref)
    audio_sec = float(probe_duration(path))
    notes: list[str] = [n for n in (ep.get("notes") or []) if not str(n).startswith("3D:")]
    words: list[dict] = []
    if S.world_voice(store, world)["provider"] != "none":
        try:
            words = SG.transcribe_words(path)
        except Exception as exc:  # noqa: BLE001 - Whisper выключен/не встал: по длине текста
            notes.append(f"Whisper не сработал ({type(exc).__name__}: {str(exc)[:120]}) — слова по длине текста")
    if not words:
        words = proportional_words(S.narration_text(ep), audio_sec)
    else:
        # Субтитры — слова сценария со временем распознанных (Whisper путает слова и цифры).
        words = S3.align_words(S.narration_text(ep), words)
    disaster = D.is_disaster(world, ep)
    speech_end = round(float(words[-1]["end"]), 3)
    tail = D.PART_SEC if disaster else SG.TAIL_SEC
    total = round(max(speech_end + tail, audio_sec + (0.0 if disaster else 0.4), MIN_DURATION_SEC), 2)
    if total > base_seconds():
        raise S.StoryError(f"серия {total:.1f} с длиннее холста {base_seconds():.0f} с")
    placed = [{**p, "scene_id": 0} for p in SG.place(ep["beats"], words, total)]
    look = S3.hero_look(hero_card(store, ep))
    options = ep.get("options") or {}
    result = S3.direct(ep["script"], words, total=total, look=look, location=options.get("location"))
    problems = S3.check_shots(result)
    if problems:
        notes.append("3D: проверка кадров — " + "; ".join(problems[:6]))
    work = settings.tmp_dir / f"series3d-{episode_id}-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        video = work / "series.mp4"
        info = render3d(result, video)
        covers = _frames(video, cover_times(result["shots"]), _covers_dir(), f"ep{int(episode_id)}-{uuid4().hex[:6]}")
        asset = save_video(store, video, f"3D: {ep.get('title') or ep['premise']}")
    finally:
        shutil.rmtree(work, ignore_errors=True)

    prefix = SG.LAYER_PREFIX
    layers: list[dict] = [{"id": f"{prefix}3d", "type": "video", "name": "3D-серия", "start": 0.0, "end": round(total, 3),
                           "track": 1, "x": 0.5, "y": 0.5, "z": 0,
                           "props": {"asset_id": int(asset["id"]), "w": 1080, "content": True, "from": 0, "speed": 1,
                                     "loop": False, "freeze": True}}]
    layers += sfx_layers(store, result, placed, total, prefix)
    if disaster:
        stroke = world["captions"]["stroke"]
        if options.get("title_card", True):
            layers += D.title_layers(ep, placed, stroke, prefix)
        layers += D.hud_layers(ep, placed, speech_end, float(world["captions"]["y"]), {}, prefix)
        if total - speech_end >= 0.5:
            layers.append(D.part_layer(ep, speech_end, total, stroke, prefix))
    layers.append({"id": f"{prefix}narr", "type": "audio", "name": "Рассказчик", "start": 0.0, "end": round(total, 3),
                   "props": {"asset_id": str(ref["asset_id"]), "mode": "track", "volume": 1.0, "role": "voice"}})
    layers.append({"id": f"{prefix}caps", "type": "captions", "name": "Субтитры по 1–3 слова", "start": 0.0,
                   "end": round(speech_end, 3), "track": 6, "x": 0.5, "y": float(world["captions"]["y"]),
                   "props": _captions(world)})
    clip = create_canvas_clip(store, total, {"kind": "solid", "colors": ["#06070a", "#06070a"]},
                              (ep.get("title") or ep["premise"])[:100],
                              f"3D-серия ({world['name']}): {ep['premise']}"[:500])
    clip_id = int(clip["id"])
    render = dict(store.get_clip_plan(clip_id).get("render_settings") or {})
    render["layers"] = normalize_layers(layers)
    render["camera"] = normalize_camera([])
    store.set_clip_plan_render_settings(clip_id, render)
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id,
                        on_first_change=lambda: snapshot(store, clip_id, "3D-серия: сборка"))
    SG._tool(ctx, "set_subtitle_words", {"words": words})
    track = SG._music_track(store, world, ep)
    if track:
        SG._tool(ctx, "set_music", {"on": True, "track_id": track})
        from app.explainer_gen import _speech_spans

        SG._tool(ctx, "set_music_duck", {"duck": _speech_spans(words)})
    elif D.music_off(world, ep) and D.MUSIC_NOTE not in notes:
        notes.append(D.MUSIC_NOTE)
    mixed = auto_mix_clip(ctx, notes)
    fresh = store.get_clip_plan(clip_id)
    shots = result["shots"]
    timeline = {"duration": total, "clip_id": clip_id, "audio_sec": round(audio_sec, 2), "engine": S.ENGINE_3D,
                "editor_url": f"/projects/{fresh['source_id']}/candidates?clip={clip_id}",
                "beats": [{k: p.get(k) for k in ("i", "start", "end", "text", "camera", "sfx", "sentence")} for p in placed],
                "shots_per_min": round(len(shots) / total * 60, 1), "sfx": len(result.get("sfx") or []),
                "layers": len(render["layers"]), "music_track_id": track, "cover_frames": covers,
                "scene3d": {"location": result["location"], "asset_id": int(asset["id"]),
                            "render_sec": info.get("seconds"), "frames": info.get("frames"),
                            "shots": [{k: s.get(k) for k in ("n", "t0", "t1", "text", "role", "action", "camera", "spot")}
                                      for s in shots],
                            "events": [{k: e.get(k) for k in ("kind", "t", "value")} for e in result["events"]],
                            "checks": problems, "look": result.get("look")},
                **({"mix": mixed} if mixed else {})}
    if disaster:
        hud = [lay for lay in render["layers"] if str(lay.get("id") or "").startswith(f"{prefix}hud")]
        timeline.update({"format": S.FORMAT_DISASTER, "part": D.part_of(ep), "speech_end": speech_end,
                         "part_card": D.part_card_text(ep),
                         "hud": [{"text": h["props"]["text"], "color": h["props"]["color"], "start": h["start"],
                                  "end": h["end"], "x": h["x"], "y": h["y"]} for h in hud],
                         "publish": D.publish_meta(ep)})
    notes.append(f"3D: {result['location']}, кадров {len(shots)}, рендер {info.get('seconds')} с")
    S.set_status(store, episode_id, "done", ("assembling",), clip_id=clip_id, words=words, timeline=timeline,
                 notes=notes, error="")
    out = {"ok": True, "clip_id": clip_id, "duration": total, "beats": len(placed), "shots": len(shots),
           "layers": len(render["layers"]), "editor_url": timeline["editor_url"], "notes": notes, "engine": S.ENGINE_3D}
    if options.get("render"):
        out["publish"] = SG.finish_publish(store, episode_id, clip_id, cover=options.get("cover", True))
    return out


def _covers_dir() -> Path:
    path = settings.clip_dir / "covers" / "series3d"
    path.mkdir(parents=True, exist_ok=True)
    return path
