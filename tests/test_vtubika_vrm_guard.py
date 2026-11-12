"""VRM-история не отдаёт «ok» на испорченном рендере: кадры без героини (потерян контекст GPU) и пустые проверки
касаний (пальцы, кресло, волосы, руки) — ошибка с понятной причиной, ключ кэша не пишется."""

import json
import subprocess
import sys
from argparse import Namespace
from pathlib import Path

import pytest

from app import vtubika_plan as P
from app import vtubika_vrm as VRM
from app.vtubika_vrm import to_vrm_spec

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import vtubika_story as STORY  # noqa: E402


@pytest.fixture(scope="module")
def story():
    plan = P.load_plan(ROOT / "tools" / "vtubika_plans" / "fox.json")
    toks = P.tokens(P.caption_text(plan))
    words = [{"word": w, "start": round(0.9 + i * 0.4, 3), "end": round(0.9 + i * 0.4 + 0.35, 3)} for i, w in enumerate(toks)]
    total = round(words[-1]["end"] + 2.6, 3)
    built = P.build(plan, words, total)
    return plan, built, words, total, to_vrm_spec(plan, built, words, total)


def _render_rep(spec, **presence):
    n = round(spec["duration"] * spec["fps"])
    pres = {"frames": n, "expected": VRM.hero_frames(spec), "missing": 0, "first_missing": [], **presence}
    return {"ok": True, "frames": n, "fps": spec["fps"], "black_frames": [], "presence": pres, "retried": []}


GOOD_CHECKS = {"ok": True, "frames": 100, "fingers": {"min_gap_mm": 8.7, "at": 1, "point": "x", "surface": "клавиатура"},
               "chair": {"min_gap_mm": 3, "at": 1, "point": "head"}, "hair": {"min_gap_mm": 5, "at": 1, "point": "h"},
               "hands": {"torso": {"min_gap_mm": 19, "at": 1, "point": "p", "against": "torso"}}, "hand_spans": 0, "empty": [],
               "twist": {"frames": 100, "wrist_max": {"max": 57}, "bad": 0, "first": []}}


def test_hero_frames_skip_inserts(story):
    spec = story[4]
    n = round(spec["duration"] * spec["fps"])
    hero = VRM.hero_frames(spec)
    assert 0 < hero < n  # вставки (монитор, планшет, банка) — без героини


def test_good_render_and_checks_pass(story):
    spec = story[4]
    assert VRM.render_problems(_render_rep(spec), spec) == []
    assert VRM.check_problems(GOOD_CHECKS, spec) == []


def test_render_without_presence_fails(story):
    spec = story[4]
    rep = _render_rep(spec)
    del rep["presence"]
    with pytest.raises(VRM.VrmRenderError, match="presence"):
        VRM.require_render(rep, spec)


def test_render_with_missing_hero_frames_fails(story):
    spec = story[4]
    with pytest.raises(VRM.VrmRenderError, match="героини нет"):
        VRM.require_render(_render_rep(spec, missing=3, first_missing=[{"frame": 200, "problem": "потерян контекст GPU"}]), spec)
    with pytest.raises(VRM.VrmRenderError, match="ожидалась"):
        VRM.require_render(_render_rep(spec, expected=5), spec)


@pytest.mark.parametrize("broken, what", [
    ({"fingers": {"min_gap_mm": None, "at": -1, "point": "", "surface": ""}}, "пальцы"),
    ({"fingers": {}}, "пальцы"),
    ({"chair": {}}, "кресло"),
    ({"hair": {"min_gap_mm": None}}, "волосы"),
    ({"hands": {}}, "руки"),
    ({"frames": 0}, "0 кадров"),
    ({"empty": ["руки: проверка рук против тела не запускалась"]}, "не запускалась"),
    ({"twist": None}, "скрутка рук"),
    ({"twist": {"frames": 0, "bad": 0}}, "скрутка рук"),
])
def test_empty_checks_fail(story, broken, what):
    spec = story[4]
    with pytest.raises(VRM.VrmRenderError, match=what):
        VRM.require_checks({**GOOD_CHECKS, **broken}, spec)


def test_crashed_checks_fail(story):
    with pytest.raises(VRM.VrmRenderError, match="упали"):
        VRM.require_checks({"error": "Error: браузер завершился"}, story[4])


class _Fake:
    """subprocess.run для run_vrm: сборка бандла, рендер (отчёт render.mjs), проверки (отчёт contact.mjs)."""

    def __init__(self, render_rep, checks):
        self.render_rep, self.checks, self.calls = render_rep, checks, []

    def __call__(self, cmd, *a, **kw):
        self.calls.append(cmd)
        joined = " ".join(map(str, cmd))
        if "render.mjs" in joined:
            out = Path(cmd[cmd.index("--out") + 1])
            out.write_bytes(b"video")
            return subprocess.CompletedProcess(cmd, 0, json.dumps(self.render_rep) + "\n", "")
        if "contact.mjs" in joined:
            return subprocess.CompletedProcess(cmd, 0 if self.checks.get("ok") else 1, json.dumps(self.checks) + "\n", "")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _run(story, tmp_path, monkeypatch, fake):
    plan, built, words, total, spec = story
    root = tmp_path / "root"
    (root / "node" / "vrm" / "dist").mkdir(parents=True)
    (root / "node" / "vrm" / "dist" / "vrm.js").write_text("bundle")
    monkeypatch.setattr(STORY, "ROOT", root)
    monkeypatch.setattr(STORY.subprocess, "run", fake)
    monkeypatch.setattr(STORY, "burn_captions", lambda *a, **k: pytest.fail("до субтитров дойти не должно"))
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    args = Namespace(endcard_sounds_only=False, preview=None, vrm_packs="packs", gl="gpu", name="t", sheet="s.jpg",
                     endcard_sounds=None)
    STORY.run_vrm(args, plan, built, words, total, tmp_path / "n.mp3", 0.9, tmp_path, work, {}, 0.0)
    return work


def test_run_vrm_rejects_render_without_hero_and_keeps_no_key(story, tmp_path, monkeypatch):
    spec = story[4]
    work = tmp_path / "work"
    work.mkdir()
    (work / "scene_vrm.key").write_text("stale-key-of-old-good-render")
    fake = _Fake(_render_rep(spec, missing=1, first_missing=[{"frame": 270, "problem": "потерян контекст GPU"}]), GOOD_CHECKS)
    with pytest.raises(VRM.VrmRenderError, match="героини нет"):
        _run(story, tmp_path, monkeypatch, fake)
    assert not (work / "scene_vrm.key").exists()  # испорченный ролик не станет кэшем
    assert not any("contact.mjs" in " ".join(map(str, c)) for c in fake.calls)


def test_run_vrm_rejects_empty_checks(story, tmp_path, monkeypatch):
    spec = story[4]
    fake = _Fake(_render_rep(spec), {**GOOD_CHECKS, "fingers": {"min_gap_mm": None}, "hands": {}})
    with pytest.raises(VRM.VrmRenderError, match="пальцы.*руки|руки.*пальцы"):
        _run(story, tmp_path, monkeypatch, fake)


def test_run_vrm_render_exit_4_is_an_error(story, tmp_path, monkeypatch):
    def fake(cmd, *a, **kw):
        if "render.mjs" in " ".join(map(str, cmd)):
            return subprocess.CompletedProcess(cmd, 4, "", "scene3d: кадр 270 (t=9.000): потерян контекст GPU: webgl 1080×1920")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with pytest.raises(RuntimeError, match="код 4.*контекст GPU"):
        _run(story, tmp_path, monkeypatch, fake)
