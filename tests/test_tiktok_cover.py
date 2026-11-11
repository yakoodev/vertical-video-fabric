"""Обложка TikTok — секунда кадра: поле публикации, проверка, обложка клипа по умолчанию, путь до площадки."""

from __future__ import annotations

import pytest

from app.publish_format import normalize_cover_sec, tiktok_cover_sec, upload_extras
from app.worker import JobWorker
from test_publish_duplicates import env  # noqa: F401 - клип, аккаунт TikTok, подмена площадки


def _post(env, **extra):  # noqa: F811
    return env["client"].post(f"/api/clips/{env['clip']['id']}/posts",
                              json={"title": "Т", "targets": [env["account"]["id"]], **extra})


def test_normalize_cover_sec():
    assert normalize_cover_sec(None) is None and normalize_cover_sec("  ") is None
    assert normalize_cover_sec("2.5") == 2.5 and normalize_cover_sec(0) == 0.0
    for bad in (-1, "abc", 3601, float("nan"), True):
        with pytest.raises(ValueError):
            normalize_cover_sec(bad)


def test_cover_second_reaches_the_tiktok_upload(env):  # noqa: F811
    response = _post(env, cover_sec=2.5)
    assert response.status_code == 200, response.text
    assert response.json()["cover_sec"] == 2.5
    assert JobWorker(env["store"]).process_once()
    assert env["provider"].calls[0]["cover_sec"] == 2.5


def test_without_cover_nothing_is_sent(env):  # noqa: F811
    response = _post(env)
    assert response.status_code == 200 and response.json()["cover_sec"] is None
    assert JobWorker(env["store"]).process_once()
    assert "cover_sec" not in env["provider"].calls[0]  # вызов площадки прежний


@pytest.mark.parametrize("value", [-1, "abc", 5000])
def test_api_refuses_a_bad_cover_second(env, value):  # noqa: F811
    assert _post(env, cover_sec=value).status_code == 422
    assert env["store"].list_jobs() == []


def test_cover_second_past_the_clip_end_is_refused(env):  # noqa: F811
    env["store"].db.execute("UPDATE clips SET duration_sec = 8 WHERE id = ?", (env["clip"]["id"],))
    response = _post(env, cover_sec=9)
    assert response.status_code == 400 and "за концом клипа" in response.json()["detail"]
    assert _post(env, cover_sec=7.5).status_code == 200


def _choose_variant(store, clip_id, t):
    store.db.execute(
        "INSERT INTO clip_covers (clip_id, path, t, chosen) VALUES (?, ?, ?, 1)", (int(clip_id), "/x/cover.jpg", t))


def test_chosen_cover_variant_is_the_default(env):  # noqa: F811
    store, clip = env["store"], env["store"].get_clip(env["clip"]["id"])
    assert tiktok_cover_sec(store, {"clip_id": clip["id"]}, clip) is None  # обложки нет — кадр TikTok
    _choose_variant(store, clip["id"], 4.25)
    assert tiktok_cover_sec(store, {"clip_id": clip["id"]}, clip) == 4.25
    assert tiktok_cover_sec(store, {"clip_id": clip["id"], "cover_sec": 1}, clip) == 1.0  # своя секунда задания важнее
    assert upload_extras(store, {"clip_id": clip["id"]}, "tiktok") == {"cover_sec": 4.25}
    assert upload_extras(store, {"clip_id": clip["id"]}, "youtube") == {}  # другим площадкам — ничего
    # Обложка вшита в начало ролика (render.cover + burn): первый кадр и есть обложка — ничего не шлём.
    store.set_clip_plan_render_settings(clip["clip_plan_id"], {"cover": {"mode": "image", "image": "a" * 32 + ".jpg",
                                                                         "burn": True}})
    assert tiktok_cover_sec(store, {"clip_id": clip["id"]}, clip) is None


def test_default_cover_flows_through_the_worker(env):  # noqa: F811
    _choose_variant(env["store"], env["clip"]["id"], 3.0)
    assert _post(env).status_code == 200
    assert JobWorker(env["store"]).process_once()
    assert env["provider"].calls[0]["cover_sec"] == 3.0


def test_approval_carries_the_cover_second_into_the_job(tmp_path, monkeypatch):
    import test_autopilot as T
    from app import autopilot as A
    from app import projects as P
    from app.settings import settings
    from test_api_pipeline import _client

    _c, store = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(A, "now", lambda: T.WED)
    monkeypatch.delenv("AUTOPILOT_TG_TOKEN", raising=False)
    source = store.create_source("upload", settings.source_dir / "m.mp4", original_filename="m.mp4",
                                 duration_sec=3600, status="ready")
    pid = P.create_project(store, "Мура", kind="stream_channel", source_ids=[source["id"]])["id"]
    store.upsert_account("youtube", "yt-main", T.COOKIE)
    A.update_config(store, pid, {"slots": [{"days": [0, 1, 2, 3, 4], "times": ["18:00"]}], "platforms": ["youtube"]})
    plan = T._clip(store, source["id"])
    with pytest.raises(ValueError):
        A.submit_clip(store, plan["id"], cover_sec=-2)
    card = A.submit_clip(store, plan["id"], cover_sec=1.5)
    assert card["payload"]["cover_sec"] == 1.5
    A.decide(store, card["id"], "approve", actor="ui")
    assert T._jobs(store)[0]["cover_sec"] == 1.5


def test_upload_form_accepts_empty_and_refuses_bad_cover(env):  # noqa: F811
    files = {"file": ("v.mp4", b"\x00" * 64, "video/mp4")}
    data = {"title": "Т", "targets": str(env["account"]["id"]), "cover_sec": ""}
    ok = env["client"].post("/api/posts", files=files, data=data)
    assert ok.status_code == 200, ok.text
    assert ok.json()["cover_sec"] is None
    bad = env["client"].post("/api/posts", files={"file": ("w.mp4", b"\x01" * 64, "video/mp4")},
                             data={**data, "cover_sec": "-3"})
    assert bad.status_code == 400 and "Обложка TikTok" in bad.json()["detail"]
