"""3D-серия → обычный холст-клип: 3D-видео во весь кадр, голос, звуки паков, музыка, субтитры, заголовки, переходы.

Клип — такой же, как у историй и объяснялок (``app.canvas.create_canvas_clip``): публикуется, правится в редакторе,
рендерится общей очередью. Повторный рендер серии — новая версия того же клипа (``app.clip_groups.finish_assembly``).

* Голос — один файл (реплики подряд, ``app/s3d_voice.build_track``), роль voice.
* Звуки — записи паков из событий (``sfx``), роль sfx.
* Музыка — трек библиотеки (``music.track_id``, ducking под голос) или петля пака (``music.ref``) на ``under_db``
  тише голоса.
* Субтитры — слова сценария на времени голоса (стиль «Историй»), ключевые слова цветом (``captions.highlight``).
* Заголовки (событие ``title``) — текстовые слои; переходы (``transition`` реплики) — слои на склейке:
  через чёрное/белое — плашка во весь кадр с наплывом, смэш и хлёст — вспышка, глитч — разрыв RGB, прожиг — засветка.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from app import clip_groups as CG
from app import projects as P

PREFIX = "s3d_"
TITLE_TRACK = 7
TRANS_TRACK = 9
SFX_MAX_SEC = 3.0


def _asset(store: Any, path: Path, kind: str, mime: str, label: str, project_id: int, desc: str) -> dict:
    from app.montage_assets import assets_dir, probe_asset

    dest = assets_dir() / f"series3d-{uuid4().hex[:12]}{path.suffix.lower()}"
    shutil.copyfile(path, dest)
    meta = probe_asset(dest)
    return store.create_montage_asset(kind=kind, label=label[:120], description=desc, tags="3d,серия", file_path=str(dest),
                                      original_filename=dest.name, mime_type=mime, size_bytes=dest.stat().st_size,
                                      project_id=project_id, **meta)


def transition_layers(transitions: list[dict], total: float) -> list[dict]:
    out = []
    for n, tr in enumerate(transitions):
        t, d, kind = float(tr["t"]), float(tr.get("duration") or 0.4), tr["kind"]
        half = d / 2
        lid = f"{PREFIX}tr{n:02d}"
        name = f"Переход: {kind}"
        if kind in ("dip_black", "dip_white", "iris", "match"):
            if kind == "match":
                continue  # монтаж по форме — сама склейка
            color = "#ffffff" if kind == "dip_white" else "#000000"
            out.append({"id": lid, "type": "shape", "name": name, "start": round(max(0.0, t - half), 3),
                        "end": round(min(total, t + half), 3), "track": TRANS_TRACK, "x": 0.5, "y": 0.5,
                        "props": {"kind": "rect", "w": 1200, "h": 2100, "fill": color},
                        "in": {"type": "fade", "duration": round(max(0.05, half), 3)},
                        "out": {"type": "fade", "duration": round(max(0.05, half), 3)}})
            continue
        fx = {"smash": ("flash_frame", 0.12, 0.8), "whip": ("flash_frame", 0.18, 0.45), "glitch": ("rgb_split", 0.35, 0.8),
              "film_burn": ("light_leak", 0.7, 0.9)}.get(kind, ("flash_frame", 0.15, 0.5))
        out.append({"id": lid, "type": "fx", "name": name, "start": round(max(0.0, t - 0.04), 3),
                    "end": round(min(total, t - 0.04 + max(fx[1], d * 0.6)), 3), "track": TRANS_TRACK,
                    "props": {"kind": fx[0], "intensity": fx[2], "speed": 1}})
    return out


def title_layers(titles: list[dict], total: float, captions_y: float) -> list[dict]:
    out = []
    y = 0.2 if captions_y > 0.5 else 0.72
    for n, tt in enumerate(titles):
        start = round(min(max(0.0, tt["t"]), max(0.0, total - 0.5)), 3)
        out.append({"id": f"{PREFIX}title{n:02d}", "type": "text", "name": "Заголовок", "start": start,
                    "end": round(min(total, start + float(tt["dur"])), 3), "track": TITLE_TRACK, "x": 0.5, "y": y,
                    "props": {"text": tt["text"], "size": 84, "color": "#ffffff", "weight": 900, "align": "center",
                              "maxWidth": 940, "lineHeight": 1.05, "stroke": {"color": "#151515", "width": 8},
                              "bg": {"color": "#000000", "opacity": 0.45, "radius": 18, "padX": 28, "padY": 14}},
                    "in": {"type": "pop", "duration": 0.25}, "out": {"type": "fade", "duration": 0.2}})
    return out


def sfx_layers(store: Any, sfx: list[dict], total: float) -> tuple[list[dict], list[str]]:
    from app.motion_assets import resolve_pack_ref
    from app.stories_gen_layers import _sfx_duration

    layers, notes = [], []
    for n, x in enumerate(sfx):
        start = max(0.0, float(x["t"]))
        if start >= total - 0.1:
            continue
        ref = str(x["ref"])
        if resolve_pack_ref(ref) is None:
            notes.append(f"звук {ref}: нет в паках (tools/fetch_packs.py) — пропущен")
            continue
        dur = min(SFX_MAX_SEC, max(0.3, _sfx_duration(store, ref)))
        layers.append({"id": f"{PREFIX}fx{n:02d}", "type": "audio", "name": f"Звук: {x.get('name') or ref}"[:60],
                       "start": round(start, 3), "end": round(min(total, start + dur), 3),
                       "props": {"asset_id": ref, "mode": "track", "volume": float(x.get("volume") or 0.5), "fadeOut": 0.15,
                                 "role": "sfx"}})
    return layers, notes


def music_layers(music: dict | None, total: float, voice_db: float) -> tuple[list[dict], int | None, list[str]]:
    """Музыка: трек библиотеки — (нет слоёв, id трека); петля пака — слои role music; нет — пусто."""
    from app import stories_3d as S3D

    if not music:
        return [], None, []
    if music.get("track_id"):
        return [], int(music["track_id"]), []
    ref = music.get("ref")
    if not ref:
        return [], None, []
    item = S3D._pack_item(str(ref))
    if item is None:
        return [], None, [f"музыка {ref}: нет в паках — серия без музыки"]
    gain = S3D.bed_gain_db(item, voice_db, float(music.get("under_db") or 18.0))
    layers = S3D.loop_layers(str(ref), 0.0, total, float(item.get("duration") or 0), {"volume": 1.0, "gain_db": gain, "role": "music"},
                             f"{PREFIX}music", f"Музыка: {item.get('name') or ref}", 1.0, 2.0)
    return layers, None, []


def assemble(store: Any, ep: dict, plan: dict, compiled: dict, track: Path, words: list[dict], raw: Path,
             sheet: Path | None, total: float, job: dict) -> dict:
    from app import agent_tools as T
    from app import vtubika_plan as VP
    from app.canvas import create_canvas_clip
    from app.clip_history import snapshot
    from app.explainer_gen import _speech_spans, _tool, auto_mix_clip
    from app.motion import normalize_camera, normalize_layers
    from app.stories_3d import _captions, voice_lufs
    from app.stories_library import DEFAULT_CAPTIONS

    pid = int(ep["project_id"])
    notes: list[str] = []
    label = ep.get("title") or plan.get("title") or f"3D-серия {ep['id']}"
    video = _asset(store, raw, "video", "video/mp4", f"3D: {label}", pid, "3D-серия (VRM, свой движок)")
    voice_path = track.with_suffix(".m4a")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(track), "-c:a", "aac", "-b:a", "192k", str(voice_path)],
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    voice = _asset(store, voice_path, "audio", "audio/mp4", f"Голос: {label}", pid, "голос 3D-серии (реплики подряд)")
    sheet_asset = _asset(store, sheet, "image", "image/jpeg", f"Лист кадров: {label}", pid, "лист кадров 3D-серии") if sheet else None
    caps_y = float((plan.get("captions") or {}).get("y") or 0.8)
    layers: list[dict] = [{"id": f"{PREFIX}3d", "type": "video", "name": "3D-серия", "start": 0.0, "end": round(total, 3),
                           "track": 1, "x": 0.5, "y": 0.5, "z": 0,
                           "props": {"asset_id": int(video["id"]), "w": 1080, "content": True, "from": 0, "speed": 1,
                                     "loop": False, "freeze": True}}]
    layers.append({"id": f"{PREFIX}voice", "type": "audio", "name": "Голос", "start": 0.0, "end": round(total, 3),
                   "props": {"asset_id": str(voice["id"]), "mode": "track", "volume": 1.0, "role": "voice"}})
    fx, fx_notes = sfx_layers(store, compiled["sfx"], total)
    layers += fx
    notes += fx_notes
    voice_db = voice_lufs(track)
    music, track_id, m_notes = music_layers(plan.get("music"), total, voice_db)
    layers += music
    notes += m_notes
    props = _captions({"captions": dict(DEFAULT_CAPTIONS)})
    _comp, marks = VP.caption_words(words, (plan.get("captions") or {}).get("highlight") or {})
    if marks:
        props["highlightWords"] = marks
    speech_end = max(float(w["end"]) for w in words)
    layers.append({"id": f"{PREFIX}caps", "type": "captions", "name": "Субтитры", "start": 0.0, "end": round(min(total, speech_end + 0.3), 3),
                   "track": 6, "x": 0.5, "y": caps_y, "props": props})
    layers += title_layers(compiled["titles"], total, caps_y)
    layers += transition_layers(compiled["transitions"], total)
    clip = create_canvas_clip(store, total, {"kind": "solid", "colors": ["#06070a", "#06070a"]}, label[:100],
                              f"3D-серия: {label}"[:500])
    clip_id = int(clip["id"])
    CG.hold_assembly(store, clip_id)
    P.assign_plan_project(store, clip_id, pid)
    render = dict(store.get_clip_plan(clip_id).get("render_settings") or {})
    render["layers"] = normalize_layers(layers)
    render["camera"] = normalize_camera([])
    store.set_clip_plan_render_settings(clip_id, render)
    ctx = T.ToolContext(store=store, clip_plan_id=clip_id, on_first_change=lambda: snapshot(store, clip_id, "3D-серия: сборка"))
    _tool(ctx, "set_subtitle_words", {"words": [{"word": w["word"], "start": w["start"], "end": w["end"]} for w in words]})
    if track_id:
        _tool(ctx, "set_music", {"on": True, "track_id": track_id})
        _tool(ctx, "set_music_duck", {"duck": _speech_spans(words)})
    mixed = auto_mix_clip(ctx, notes)
    version = CG.finish_assembly(store, clip_id, ep.get("clip_id"))
    if version.get("note") and not version.get("linked"):
        notes.append(version["note"])
    return {"clip_id": clip_id, "video_asset_id": int(video["id"]), "video_url": f"/media/montage-assets/{int(video['id'])}",
            "sheet_asset_id": int(sheet_asset["id"]) if sheet_asset else None,
            "sheet_url": f"/media/montage-assets/{int(sheet_asset['id'])}" if sheet_asset else None,
            "voice_asset_id": int(voice["id"]), "layers": len(render["layers"]), "notes": notes, "mix": mixed,
            "version": version}
