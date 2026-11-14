"""Вставка на стыке кусков встаёт ровно на отрендеренный стык (app/timebase.FrameGrid).

Ручной мем-эдит: рендер режет каждый кусок целым числом кадров, план считает точные секунды;
за несколько кусков набегало 1–2 кадра, и полнокадровый видеомем, поставленный по плану ровно
на стык, открывал по краю кадр спрятанного под ним геймплея."""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path

import pytest

from app.cuts import plan_cuts
from app.montage_assets import build_inserts_args, normalize_inserts
from app.render import ClipRenderService
from app.settings import settings
from app.timebase import FrameGrid, frame_at, part_frames
from test_api_pipeline import _require_ffmpeg
from test_render import _store

FPS = 30.0
# Длины кусков не на сетке кадров: у каждого дробная часть ≈0.4 кадра — рендер её отбрасывает,
# и к 4-му стыку план уходит от файла на 2 кадра.
PIECES = [(0.0, 1.513), (2.0, 4.18), (5.0, 6.547), (7.0, 9.313), (10.0, 11.68)]


def _rendered_starts(lengths: list[float]) -> list[int]:
    out, acc = [], 0
    for length in lengths:
        out.append(acc)
        acc += part_frames(length, FPS)
    return out


def _plan_starts(lengths: list[float]) -> list[float]:
    out, acc = [], 0.0
    for length in lengths:
        out.append(acc)
        acc += length
    return out


def _video_frames(args: list[str], label: int) -> tuple[int, int]:
    """Кадры, на которых картинка вставки ``label`` видна по окну ``enable`` (первый и последний)."""
    graph = args[args.index("-filter_complex") + 1]
    lo, hi = map(float, re.search(rf"enable='between\(t,([-\d.]+),([-\d.]+)\)'\[v{label}\]", graph).groups())
    return math.ceil(lo * FPS - 1e-9), math.floor(hi * FPS + 1e-9)


def test_frame_grid_maps_plan_boundaries_onto_rendered_frames():
    segs = [{"start_sec": a, "end_sec": b} for a, b in PIECES]
    lengths = [b - a for a, b in PIECES]
    grid = FrameGrid(segs, None, None, FPS)
    rendered, planned = _rendered_starts(lengths), _plan_starts(lengths)
    assert grid  # сдвиг есть
    # Без сетки стык 4 был бы на 227-м кадре, в файле он на 225-м — два кадра чужой картинки.
    assert frame_at(planned[4], FPS) - rendered[4] == 2
    for k in range(len(segs)):
        assert frame_at(grid(planned[k]), FPS) == rendered[k]
        # Вставку ставят с тремя знаками — меньше полукадра до стыка всё равно стык.
        assert frame_at(grid(round(planned[k], 3)), FPS) == rendered[k]
        assert frame_at(grid(planned[k] - 0.004), FPS) == rendered[k]
    # Кадр-в-кадр куски — сдвига нет, перевод не нужен.
    assert not FrameGrid([{"start_sec": 0, "end_sec": 1.0}, {"start_sec": 3, "end_sec": 4.5}], None, None, FPS)


def test_insert_edges_snap_to_frames_in_ffmpeg_window(tmp_path):
    """build_inserts_args: вставка занимает кадры frame_at(at)…frame_at(end)−1. Раньше 452/30 = 15.0667
    печаталось как 15.067, и первый кадр вставки терялся, а кадр на её конце оставался лишним."""
    asset = {"kind": "image", "file_path": str(Path(__file__)), "has_audio": False}
    inserts = normalize_inserts([{"asset_id": 1, "at": 452 / FPS, "duration": 30 / FPS, "mode": "full"}])
    args = build_inserts_args(tmp_path / "c.mp4", tmp_path / "o.mp4", inserts, {1: asset}, 180, 320, 30.0,
                              clip_has_audio=False, fps=FPS)
    assert _video_frames(args, 1) == (452, 481)


def _plan_with_insert(tmp_path, monkeypatch, cuts=None):
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "grid.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"src")
    source = store.create_source("upload", source_path, duration_sec=20, width=320, height=180, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segs = [store.create_ai_segment(analysis["id"], {"start_sec": a, "end_sec": b, "title": f"P{i}"},
                                     min_duration=1.5)
            for i, (a, b) in enumerate(PIECES)]
    plan = store.create_clip_plan(source["id"], analysis["id"], "Сетка", segment_ids=[s["id"] for s in segs])
    asset = store.create_montage_asset(kind="image", label="мем", file_path=str(Path(__file__)), width=200, height=200)
    lengths = [b - a for a, b in PIECES]
    planned = _plan_starts(lengths)
    # Полнокадровый мем закрывает кусок 3 целиком: от стыка 3 до стыка 4 (три знака — как пишет агент).
    at, end = round(planned[3], 3), round(planned[4], 3)
    render = {"inserts": [{"asset_id": asset["id"], "at": at, "duration": round(end - at, 3), "mode": "full",
                           "duck": True, "duck_volume": 0}]}
    if cuts:
        render["cuts"] = cuts
    store.set_clip_plan_render_settings(plan["id"], render)
    return store, store.get_clip_plan(plan["id"])


def test_insert_on_piece_boundary_starts_on_the_rendered_boundary_frame(tmp_path, monkeypatch):
    store, plan = _plan_with_insert(tmp_path, monkeypatch)
    service = ClipRenderService(store)
    inserts, assets = service._final_inserts(plan, None, FPS)  # noqa: SLF001
    rendered = _rendered_starts([b - a for a, b in PIECES])
    args = build_inserts_args(Path("c.mp4"), Path("o.mp4"), inserts, assets, 180, 320, 30.0,
                              clip_has_audio=False, fps=FPS)
    first, last = _video_frames(args, 1)
    assert first == rendered[3]  # ни кадра геймплея куска 3 до мема
    assert last == rendered[4] - 1  # и мем не залезает на кусок 4


def test_insert_on_boundary_after_cuts(tmp_path, monkeypatch):
    """С вырезами части — под-куски: сетка считается по ним (вырез внутри куска 1)."""
    cuts = [{"start": 2.2, "end": 2.8}]
    store, plan = _plan_with_insert(tmp_path, monkeypatch, cuts)
    service = ClipRenderService(store)
    cut_plan = plan_cuts(plan["segments"], cuts, FPS)
    inserts, assets = service._final_inserts(plan, cut_plan, FPS)  # noqa: SLF001
    part_lengths = [p["end_sec"] - p["start_sec"] for p in cut_plan.parts]
    rendered = _rendered_starts(part_lengths)
    k3 = next(i for i, p in enumerate(cut_plan.parts) if p["piece"] == 3)
    k4 = next(i for i, p in enumerate(cut_plan.parts) if p["piece"] == 4)
    args = build_inserts_args(Path("c.mp4"), Path("o.mp4"), inserts, assets, 180, 320, 30.0,
                              clip_has_audio=False, fps=FPS)
    assert _video_frames(args, 1) == (rendered[k3], rendered[k4] - 1)


def _frames_rgb(video: Path, frames: list[int]) -> list[tuple[int, int, int]]:
    expr = "+".join(f"eq(n\\,{n})" for n in frames)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video), "-vf", f"select={expr},scale=4:4", "-fps_mode", "passthrough",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True, timeout=120,
    ).stdout
    size = 4 * 4 * 3
    out = []
    for i in range(len(frames)):
        chunk = raw[i * size:(i + 1) * size]
        out.append(tuple(sum(chunk[c::3]) // 16 for c in range(3)))
    return out


def test_real_render_meme_insert_hides_every_gameplay_frame(tmp_path, monkeypatch):
    """Настоящий рендер: 5 кусков не на сетке кадров, красный мем на кусок 3 — кадр до стыка синий,
    первый кадр куска 3 уже красный, последний кадр куска 3 красный, первый кадр куска 4 синий."""
    _require_ffmpeg()
    store = _store(tmp_path, monkeypatch)
    source_path = settings.source_dir / "blue.mp4"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x90:r=30",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "12", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(source_path)], check=True)
    meme = tmp_path / "red.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=90x160", "-frames:v", "1",
                    str(meme)], check=True)
    source = store.create_source("upload", source_path, duration_sec=12, width=160, height=90, fps=30, status="ready")
    analysis = store.create_ai_analysis(source["id"], "mock", status="succeeded")
    segs = [store.create_ai_segment(analysis["id"], {"start_sec": a, "end_sec": b, "title": f"P{i}"},
                                     min_duration=1.5)
            for i, (a, b) in enumerate(PIECES)]
    plan = store.create_clip_plan(source["id"], analysis["id"], "Мем", segment_ids=[s["id"] for s in segs])
    asset = store.create_montage_asset(kind="image", label="мем", file_path=str(meme), width=90, height=160)
    planned = _plan_starts([b - a for a, b in PIECES])
    at, end = round(planned[3], 3), round(planned[4], 3)
    store.set_clip_plan_render_settings(plan["id"], {
        "end_hold_sec": 0,
        "inserts": [{"asset_id": asset["id"], "at": at, "duration": round(end - at, 3), "mode": "full",
                     "fill": "cover", "duck": True, "duck_volume": 0}]})
    preset = store.create_ffmpeg_preset("Tiny", output_width=90, output_height=160, fps=30, scale_mode="cover",
                                        extra={"crf": 35})

    clip = ClipRenderService(store).render_clip_plan(plan["id"], ffmpeg_preset_id=preset["id"])

    assert clip["status"] == "succeeded", clip.get("error")
    assert "вставки не наложены" not in str(clip.get("error") or "")
    rendered = _rendered_starts([b - a for a, b in PIECES])
    b3, b4 = rendered[3], rendered[4]
    before, first, last, after = _frames_rgb(Path(clip["output_path"]), [b3 - 1, b3, b4 - 1, b4])
    assert before[2] > 150 and before[0] < 80, before  # геймплей куска 2
    assert first[0] > 150 and first[2] < 80, first  # мем с первого кадра куска 3
    assert last[0] > 150 and last[2] < 80, last  # и до последнего
    assert after[2] > 150 and after[0] < 80, after  # кусок 4 — снова геймплей


@pytest.mark.parametrize("fps", [25.0, 30.0, 60.0])
def test_part_frames_is_the_renderer_formula(fps, tmp_path):
    """-frames:v части и FrameGrid считают одной функцией."""
    from app.render_args import build_ffmpeg_render_args

    for a, b in PIECES:
        args = build_ffmpeg_render_args(Path("s.mp4"), tmp_path / "p.mp4", a, b,
                                        {"fps": fps, "output_width": 90, "output_height": 160})
        assert int(args[args.index("-frames:v") + 1]) == part_frames(b - a, fps)
