"""Линт ``freezes``: застывшие куски готового рендера от 0.6 с сквозь зерно и титры (журнал монтажа 15.11).

Синтетика ffmpeg lavfi, крошечные ролики 216×384 во временной папке теста: фон едет (кадрирование широкого
testsrc со сдвигом), в середине кадр стоит (``loop``), поверх — зерно по времени и бегущая плашка «титра».
"""

from __future__ import annotations

import subprocess

import pytest

from app import quality_lint as Q
from app import quality_lint_freeze as F

# Клетка сетки 40×40 — 5×10 px: зерно гасится усреднением слабее, чем у 1080p (27×48 px), — проверка строже жизни.
SIZE = (216, 384)
FPS = 30
# Плашка «титра» едет по низу кадра: y 268 из 384 (0.70), высота до 32 (до 0.78).
OVERLAY_Y = 268
OVERLAY_BAND = (0.0, 0.68, 1.0, 0.80)


def _video(tmp_path, name, *, freeze_at=1.0, freeze_sec=1.2, total=3.4, overlay=(40, 16), grain=24):
    """Фон 216×384 едет, с ``freeze_at`` на ``freeze_sec`` замирает (``freeze_sec`` 0 — без стоп-кадра), зерно
    ``grain`` меняется каждый кадр, плашка ``overlay`` (w, h) бежит слева направо всё время."""
    from test_api_pipeline import _require_ffmpeg

    _require_ffmpeg()
    w, h = SIZE
    ow, oh = overlay
    chain = f"[0:v]crop={w}:{h}:x='mod(t*45\\,{w})':y=0"
    if freeze_sec > 0:
        start = int(round(freeze_at * FPS))
        chain += f",loop=loop={int(round(freeze_sec * FPS))}:size=1:start={start},setpts=N/{FPS}/TB"
    chain += f",trim=duration={total + freeze_sec:.2f},noise=alls={grain}:allf=t+u[bg];"
    graph = chain + f"[1:v]scale={ow}:{oh}[ov];[bg][ov]overlay=x='mod(t*70\\,{w})-{ow // 2}':y={OVERLAY_Y}:shortest=1[v]"
    out = tmp_path / name
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=s={2 * w}x{h}:r={FPS}:d={total + 2}",
           "-f", "lavfi", "-i", f"testsrc2=s=64x32:r={FPS}:d={total + 2}", "-filter_complex", graph,
           "-map", "[v]", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def _band_mask(_t):
    return F.mask_from_rects([OVERLAY_BAND], Q.FLASH_SIZE)


# ---------------------------------------------------------------- медиа


def test_static_stretch_under_grain_and_moving_title_is_found(tmp_path):
    video = _video(tmp_path, "frozen.mp4")
    runs = F.analyze_freezes(video)["runs"]
    assert len(runs) == 1, runs
    run = runs[0]
    assert run["start"] == pytest.approx(1.0, abs=0.25) and run["duration"] == pytest.approx(1.2, abs=0.25)

    # ffmpeg freezedetect рендер-QC (2 с) его не видит вовсе — зерно и плашка для него «движение».
    log = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vf",
                          "freezedetect=n=0.003:d=0.6", "-f", "null", "-"], capture_output=True, text=True).stderr
    assert "freeze_start" not in log


def test_big_moving_title_needs_the_layer_mask(tmp_path):
    """Крупный титр (≈ 10 % клеток) поверх стоящего кадра: без маски слоёв кадр «движется», с маской — стоит."""
    video = _video(tmp_path, "title.mp4", overlay=(128, 32))
    assert not F.analyze_freezes(video)["runs"]
    runs = F.analyze_freezes(video, mask_at=_band_mask)["runs"]
    assert len(runs) == 1 and runs[0]["start"] == pytest.approx(1.0, abs=0.25), runs
    assert runs[0]["masked_share"] > 0.05


def test_normal_motion_and_shared_flash_pass(tmp_path):
    moving = _video(tmp_path, "moving.mp4", freeze_sec=0)
    assert not F.analyze_freezes(moving)["runs"]
    assert not F.analyze_freezes(moving, mask_at=_band_mask)["runs"]

    # Тот же сканер, накормленный из прохода вспышек (30 к/с → каждый третий кадр), находит то же.
    frozen = _video(tmp_path, "frozen2.mp4")
    scanner = F.FreezeScanner(F.FREEZE_FPS)
    Q.analyze_flashes(frozen, consumers=[F.subsample_feed(scanner, Q.FLASH_FPS)])
    shared = scanner.finish()
    alone = F.analyze_freezes(frozen)["runs"]
    assert [round(r["start"], 1) for r in shared] == [round(r["start"], 1) for r in alone]


def test_intentional_freezes_and_end_hold_are_skipped(tmp_path):
    video = _video(tmp_path, "hold.mp4")
    runs = F.analyze_freezes(video, skip_at=lambda t: 0.9 <= t < 2.4)["runs"]
    assert not runs


# ---------------------------------------------------------------- чистые части


def test_scanner_on_arrays_flat_frames_and_merge():
    import numpy as np

    rng = np.random.default_rng(1)
    still = rng.uniform(40, 200, size=(40, 40)).astype(np.float32)
    frames = [still + rng.normal(0, 0.5, size=(40, 40)) for _ in range(8)]  # 0.8 с стоит (шум < порога)
    frames += [rng.uniform(40, 200, size=(40, 40)) for _ in range(5)]  # движение
    frames += [np.full((40, 40), 16.0) for _ in range(10)]  # чёрное — не судим (это blackdetect)
    scanner = F.FreezeScanner(10)
    scanner.feed(np.stack(frames))
    runs = scanner.finish()
    assert len(runs) == 1 and runs[0]["start"] == 0.0 and runs[0]["duration"] == pytest.approx(0.8)

    merged = F.merge_runs([{"start": 147.4, "end": 148.5, "duration": 1.1}, {"start": 149.3, "end": 149.9, "duration": 0.6},
                           {"start": 150.8, "end": 151.5, "duration": 0.7}, {"start": 160.0, "end": 160.7, "duration": 0.7}])
    assert [(m["start"], m["end"], m["duration"]) for m in merged] == [(147.4, 151.5, 2.4), (160.0, 160.7, 0.7)]
    assert merged[0]["parts"] == 3


def test_overlay_rects_from_clip_layers():
    layers = [{"id": "t", "type": "text", "start": 0, "end": 2, "x": 0.5, "y": 0.3, "props": {"text": "ТИТР", "size": 120}},
              {"id": "c", "type": "captions", "start": 0, "end": 5, "y": 0.7, "props": {}},
              {"id": "v", "type": "video", "start": 0, "end": 5, "x": 0.5, "y": 0.5, "props": {}}]
    rects = F.overlay_rects(layers, 1.0)
    assert len(rects) == 2  # видео-слой — содержимое кадра, не оверлей
    title, band = rects
    assert 0.25 < title[1] < 0.3 < title[3] < 0.36 and band[1] == pytest.approx(0.61) and band[3] == pytest.approx(0.79)
    assert len(F.overlay_rects(layers, 3.0)) == 1  # титр кончился
    pages = {"c": [{"t0": 4.0, "t1": 4.5}]}
    assert F.overlay_rects(layers, 3.0, caption_pages=pages) == []  # страница субтитров не видна
    mask = F.mask_from_rects(rects, 40)
    assert mask[12, 20] and mask[28, 0] and not mask[2, 20]
    assert F.overlay_rects([], 1.0, ass={"pages": [{"t0": 0.5, "t1": 1.5}], "bottom_pct": 12})[0][1] == pytest.approx(0.70)


def test_findings_map_to_clip_time_and_piece_speed():
    from app.timebase import ClipClock

    segments = [{"id": 11, "start_sec": 10.0, "end_sec": 14.0}, {"id": 12, "start_sec": 30.0, "end_sec": 36.0}]
    clock = ClipClock(segments)
    items = F.freeze_findings([{"start": 5.0, "end": 6.0, "duration": 1.0}], clock, segments=segments, speeds={12: 0.25})
    assert len(items) == 1
    item = items[0]
    assert item["check"] == "freezes" and item["severity"] == "warn" and item["piece"] == 1
    assert item["t"] == pytest.approx(5.0) and item["final_t"] == 5.0 and "×0.25" in item["message"]
    assert "set_segment_speed" in item["fix"] and "0.87–1.15" in item["fix"]


# ---------------------------------------------------------------- клип


def test_lint_clip_freezes_on_rendered_file(tmp_path, monkeypatch):
    from app.settings import settings
    from test_clip_spec import _setup

    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="queued")
    settings.clip_dir.mkdir(parents=True, exist_ok=True)
    video = _video(settings.clip_dir, f"freeze-{clip['id']}.mp4", overlay=(128, 32))
    store.finish_clip_render(clip["id"], "succeeded", output_path=video, duration_sec=4.6)

    # Без слоя крупный титр бежит по 10 % кадра — застывшее не видно; со слоем клипа на его месте — видно.
    bare = Q.lint_clip(store, plan["id"], checks=["freezes"])
    assert "freezes" not in bare["skipped"] and not bare["findings"], bare
    rs = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    rs["layers"] = [{"id": "title", "type": "text", "start": 0, "end": 10, "x": 0.5, "y": 0.74,
                     "props": {"text": "ОЧЕНЬ БОЛЬШОЙ ТИТР ВО ВСЮ ШИРИНУ", "size": 150, "maxWidth": 1080}}]
    store.set_clip_plan_render_settings(plan["id"], rs)
    result = Q.lint_clip(store, plan["id"], checks=["freezes", "flashes"])  # один проход на обе проверки
    found = [f for f in result["findings"] if f["check"] == "freezes"]
    assert len(found) == 1 and found[0]["final_t"] == pytest.approx(1.0, abs=0.25), result
    assert result["freezes"]["found"] == 1 and "застывшие куски 1" in result["summary"]
    assert Q.normalize_checks(["freeze"]) == ["freezes"]

    no_media = Q.lint_clip(store, plan["id"], checks=["freezes"], media=False)
    assert "freezes" in no_media["skipped"] and not no_media["findings"]
