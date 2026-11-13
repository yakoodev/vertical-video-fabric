"""3D-серия: рендер длинной серии (эксперимент A3, 13.11 — серия 4, 1593 кадра, «рисую 3D» 30 мин на 0.25 и
«не уложились в 1800 с»): браузеры по памяти, срок по числу кадров, прогресс по кадрам, зависший рендер
останавливается с понятной причиной, проверка касаний — без отрисовки кадров."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app import s3d_render as R

ROOT = Path(__file__).resolve().parent.parent


def test_workers_by_ram_and_length(monkeypatch):
    monkeypatch.delenv("SERIES3D_WORKERS", raising=False)
    assert R.plan_workers(1593, free_mb=5600) == 2  # 6 ГБ контейнера минус сервис: два браузера
    assert R.plan_workers(1593, free_mb=2500) == 1
    assert R.plan_workers(1593, free_mb=20000) == R.MAX_WORKERS
    assert R.plan_workers(200, free_mb=20000) == 1  # короткий ролик — одним
    monkeypatch.setattr(R, "free_ram_mb", lambda: None)
    assert R.plan_workers(1593) == 1  # память не узнать — как раньше
    monkeypatch.setenv("SERIES3D_WORKERS", "3")
    assert R.plan_workers(100) == 3


def test_budget_scales_with_frames_not_fixed_1800():
    long_ep = R.render_budget(1593, 2)
    assert long_ep > 1800 and long_ep == R.RENDER_BASE_SEC + 797 * R.SEC_PER_FRAME_BUDGET
    assert R.render_budget(300, 1) < R.render_budget(1593, 1) <= R.TIMEOUT_SEC
    assert R.render_budget(10 ** 6, 1) == R.TIMEOUT_SEC


def test_progress_note_moves_with_frames():
    note0, share0 = R.progress_note(0, 1593, 5)
    note, share = R.progress_note(800, 1593, 400)
    assert share0 == 0.25 and 0.45 < share < 0.5 and "800 из 1593" in note and "осталось ~7 мин" in note
    assert R.progress_note(1593, 1593, 900)[1] == 0.7


def _child(tmp_path: Path, body: str) -> list[str]:
    script = tmp_path / "child.py"
    script.write_text("import sys, time\n" + body, "utf-8")
    return [sys.executable, str(script)]


def test_run_watched_reports_progress_and_output(tmp_path):
    seen = []
    args = _child(tmp_path, "for n in (30, 60):\n    sys.stderr.write(f'кусок 0-60: {n}/60\\n'); sys.stderr.flush();"
                            " time.sleep(0.3)\nprint('{\"ok\": true, \"frames\": 60}')\n")
    run = R.run_watched(args, total=60, budget=30, tick=0.1, on_progress=lambda d, t, e: seen.append(d))
    assert run["code"] == 0 and run["done"] == 60 and run["reason"] == "" and R._last_json(run["stdout"])["frames"] == 60
    assert max(seen) >= 30


def test_run_watched_stops_a_stalled_render(tmp_path):
    args = _child(tmp_path, "sys.stderr.write('кусок 0-60: 30/60\\n'); sys.stderr.flush(); time.sleep(30)\n")
    run = R.run_watched(args, total=60, budget=60, stall=1, tick=0.1)
    assert run["reason"] == "stall" and run["done"] == 30 and run["elapsed"] < 20


def test_run_watched_budget_and_cancel(tmp_path):
    args = _child(tmp_path, "time.sleep(30)\n")
    assert R.run_watched(args, total=10, budget=1, stall=100, tick=0.1)["reason"] == "timeout"
    assert R.run_watched(args, total=10, budget=100, stall=100, tick=0.1,
                         on_progress=lambda *a: False)["reason"] == "stopped"


def test_render_vrm_passes_workers_and_budget_and_explains_stall(tmp_path, monkeypatch):
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"duration": 53.1, "fps": 30}), "utf-8")
    monkeypatch.setattr(R, "ensure_vrm_bundle", lambda: R.VRM_BUNDLE)
    monkeypatch.setattr(R, "ensure_vrm_packs", lambda s: tmp_path)
    monkeypatch.setattr(R, "plan_workers", lambda frames: 2)
    monkeypatch.setattr("app.scene3d.chromium_path", lambda: "")
    got = {}

    def fake_run(args, **kw):
        got.update(args=args, **kw)
        return {"code": -9, "stdout": "", "stderr": "", "done": 412, "elapsed": 700.0, "reason": "stall"}

    monkeypatch.setattr(R, "run_watched", fake_run)
    res = R.render_vrm(spec, tmp_path / "out.mp4")
    args = got["args"]
    assert args[args.index("--workers") + 1] == "2" and got["total"] == 1593
    assert int(args[args.index("--timeout") + 1]) > got["budget"] > 1800
    assert res["code"] == -1 and "завис" in res["error"] and "412 из 1593" in res["error"] and res["workers"] == 2


def test_contact_check_runs_without_pixels():
    contact = (ROOT / "node" / "vrm" / "contact.mjs").read_text("utf-8")
    assert "scene.renderPose" in contact and "--pixels" in contact
    render = (ROOT / "node" / "scene3d" / "render.mjs").read_text("utf-8")
    assert "renderPose" in render and "--disable-accelerated-2d-canvas" in render
    main = (ROOT / "node" / "vrm" / "src" / "main.ts").read_text("utf-8")
    assert "pixels: false" in main
