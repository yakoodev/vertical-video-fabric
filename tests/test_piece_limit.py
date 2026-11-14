"""Предел кусков в клипе — 40 (было 12: ручному мем-эдиту на 50 с понадобилось 13+).

Один предел на весь сервис (app/clip_spec.MAX_PIECES), и рендер с 40 кусками и переходом-наплывом
(xfade-граф на 40 входов) действительно собирается."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from app import clip_spec, compilation, manual_clips, meme_edit_plan
from app.clip_spec import MAX_PIECES, export_spec, json_schema, validate_spec
from app.ingest import probe_media
from app.manual_clips import create_manual_clip
from app.render import ClipRenderService
from app.settings import settings
from test_api_pipeline import _require_ffmpeg
from test_render import _store

STEP, LEN = 1.7, 1.5  # кусок i: [i·1.7, i·1.7 + 1.5] — не встык по исходнику, наплыв не «умный»


def _pieces(n: int) -> list[dict]:
    return [{"start_sec": round(i * STEP, 3), "end_sec": round(i * STEP + LEN, 3)} for i in range(n)]


def test_one_limit_for_the_whole_service():
    assert MAX_PIECES == 40
    assert manual_clips.MAX_PIECES is clip_spec.MAX_PIECES
    assert meme_edit_plan.MAX_PIECES is clip_spec.MAX_PIECES
    assert compilation.MAX_PIECES is clip_spec.MAX_PIECES
    assert json_schema()["properties"]["pieces"]["maxItems"] == MAX_PIECES


def _source(store, duration: float, path: Path | None = None) -> dict:
    if path is None:
        path = settings.source_dir / "long.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"src")
    return store.create_source("upload", path, duration_sec=duration, width=160, height=90, fps=30, status="ready")


def test_manual_clip_and_spec_take_forty_pieces_not_more(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    source = _source(store, 80)
    plan = create_manual_clip(store, source["id"], "Сорок", _pieces(40))
    assert len(plan["segments"]) == 40
    with pytest.raises(ValueError, match="40"):
        create_manual_clip(store, source["id"], "Сорок один", _pieces(41))

    spec = export_spec(store, plan["id"])
    assert len(spec["pieces"]) == 40
    validate_spec(store, plan["id"], spec)  # 40 — можно
    spec["pieces"].append({"start_sec": 75.0, "end_sec": 77.0})
    with pytest.raises(clip_spec.SpecError, match="не больше 40"):
        validate_spec(store, plan["id"], spec)


def test_real_render_forty_pieces_with_crossfades(tmp_path, monkeypatch):
    """40 частей + xfade-склейка на 40 входов на крошечном синтетическом исходнике."""
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    path = settings.source_dir / "forty.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    total = 40 * STEP + 1
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", f"{total:.1f}",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
                   check=True)
    source = _source(store, total, path)
    plan = create_manual_clip(store, source["id"], "Сорок кусков", _pieces(40))
    fade = 0.2
    transition = {"type": "fade", "duration": fade, "smart": False}
    store.set_clip_plan_render_settings(plan["id"], {"end_hold_sec": 0, "transition": transition})
    preset = store.create_ffmpeg_preset("Tiny", output_width=90, output_height=160, fps=30, scale_mode="cover",
                                        extra={"crf": 35})

    started = time.monotonic()
    clip = ClipRenderService(store).render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"], transition=transition)
    took = time.monotonic() - started

    assert clip["status"] == "succeeded", clip.get("error")
    out = probe_media(Path(clip["output_path"]))
    assert out.duration_sec == pytest.approx(40 * LEN - 39 * fade, abs=0.4)
    assert took < 240, f"рендер 40 кусков шёл {took:.0f} с"
