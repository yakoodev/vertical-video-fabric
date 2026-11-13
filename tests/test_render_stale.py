"""lint_clip: «клип правили после рендера» — по отпечатку состояния клипа, а не по времени статуса.

QA 01.11 (клип 889): сразу после свежего рендера без правок lint_clip писал render_stale — рендер
в конце ставит статус «rendered», и updated_at плана всегда был позже конца рендера."""

from __future__ import annotations

from app import quality_lint as Q
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup
from test_quality_lint import _ffmpeg


def _rendered_clip(store, source, plan, tmp_path):
    from app.settings import settings

    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="queued")
    settings.clip_dir.mkdir(parents=True, exist_ok=True)
    video = _ffmpeg(settings.clip_dir, f"stale-{clip['id']}.mp4", "color=gray:s=64x64:r=30:d=2")
    store.finish_clip_render(clip["id"], "succeeded", output_path=video, duration_sec=2)
    return clip


def test_fresh_render_is_not_stale_until_the_clip_is_edited(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = _rendered_clip(store, source, plan, tmp_path)
    store.set_clip_plan_fingerprint(clip["id"], Q.plan_fingerprint(store, plan["id"]))
    store.update_clip_plan_status(plan["id"], "rendered")  # как в конце рендера — это не правка
    store.update_clip_plan_status(plan["id"], "rendered")
    plan_row = store.get_clip_plan(plan["id"], include_segments=False)
    store.db.execute("UPDATE clip_plans SET updated_at = '2999-01-01 00:00:00' WHERE id = ?", (plan["id"],))
    assert plan_row["status"] == "rendered"

    fresh = Q.lint_clip(store, plan["id"], checks=["loop"])
    assert fresh["render_clip_id"] == clip["id"] and "render_stale" not in fresh

    # Название и описание (автоописание после рендера) — не монтаж.
    store.db.execute("UPDATE clip_plans SET title = 'Новое' WHERE id = ?", (plan["id"],))
    assert "render_stale" not in Q.lint_clip(store, plan["id"], checks=["loop"])

    rs = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    rs["layers"] = [{"id": "t", "type": "text", "start": 0, "end": 1, "x": 0.5, "y": 0.3, "props": {"text": "ХУК"}}]
    store.set_clip_plan_render_settings(plan["id"], rs)
    assert "render_stale" in Q.lint_clip(store, plan["id"], checks=["loop"])


def test_old_render_without_fingerprint_uses_edit_history(tmp_path, monkeypatch):
    from app.clip_history import snapshot

    _require_ffmpeg()
    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = _rendered_clip(store, source, plan, tmp_path)
    store.update_clip_plan_status(plan["id"], "rendered")
    assert "render_stale" not in Q.lint_clip(store, plan["id"], checks=["loop"])
    snapshot(store, plan["id"], "правка")
    store.db.execute("UPDATE clip_plan_history SET created_at = '2999-01-01 00:00:00' WHERE clip_plan_id = ?",
                     (plan["id"],))
    assert "render_stale" in Q.lint_clip(store, plan["id"], checks=["loop"])
    assert clip["id"]


def test_render_service_stamps_the_fingerprint(tmp_path, monkeypatch):
    from app.render import ClipRenderService

    _require_ffmpeg()
    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = _rendered_clip(store, source, plan, tmp_path)
    service = ClipRenderService(store)
    monkeypatch.setattr(service, "_render_clip_plan_unlocked", lambda *a, **k: store.get_clip(clip["id"]))
    monkeypatch.setattr(service, "_after_render", lambda c: c)
    out = service.render_clip_plan(plan["id"])
    assert out["plan_fingerprint"] == Q.plan_fingerprint(store, plan["id"])
    assert store.get_clip(clip["id"])["plan_fingerprint"] == out["plan_fingerprint"]
    assert "render_stale" not in Q.lint_clip(store, plan["id"], checks=["loop"])
