def test_html_pages_render(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTING_PROVIDER_MODE", "mock")
    from app.settings import settings

    settings.auth_enabled = False
    settings.api_token = ""
    settings.data_dir = tmp_path / "data"
    settings.upload_dir = settings.data_dir / "uploads"
    settings.source_dir = settings.data_dir / "sources"
    settings.clip_dir = settings.data_dir / "clips"
    settings.banner_dir = settings.data_dir / "banners"
    settings.audio_dir = settings.data_dir / "audio"
    settings.subtitle_dir = settings.data_dir / "subtitles"
    settings.tmp_dir = settings.data_dir / "tmp"
    settings.runtime_dir = settings.data_dir / "runtime"
    settings.log_dir = settings.data_dir / "logs"
    settings.db_path = settings.data_dir / "app.sqlite"
    settings.secret_key_path = settings.data_dir / "secret.key"
    settings.ensure_dirs()

    from fastapi.testclient import TestClient
    from app.main import app, store

    client = TestClient(app)
    response = client.get("/sources")
    assert response.status_code == 200
    assert "Sources" in response.text

    source_path = settings.source_dir / "source.mp4"
    source_path.write_bytes(b"video")
    source = store.create_source(
        "upload",
        source_path,
        original_filename="source.mp4",
        duration_sec=60,
        width=1080,
        height=1920,
        status="ready",
    )
    response = client.get(f"/sources/{source['id']}")
    assert response.status_code == 200
    assert "Timeline" in response.text
    assert "Artemox" in response.text

    response = client.get(f"/sources/{source['id']}/studio")
    assert response.status_code == 200
    assert "Запустить анализ" in response.text
    assert "timeline-board in-player" not in response.text
    assert "Модели" in response.text
    assert "Промпты" in response.text
    assert "Баннеры" in response.text
    assert "Субтитры" in response.text
    assert 'name="analysis_max_dimension" type="number" min="144" max="2160" value="1080"' in response.text
    assert 'name="analysis_video_crf" type="number" min="18" max="40" value="26"' in response.text
    assert 'name="reduce_fps_for_analysis" value="true" checked' not in response.text

    smotvibe_path = settings.source_dir / "smotvibe-source.mp4"
    smotvibe_path.write_bytes(b"video")
    smotvibe_source = store.create_source(
        "smotvibe_url",
        smotvibe_path,
        original_url="https://smotvibe.sbs/series/6802576/",
        original_filename="smotvibe-6802576.mp4",
        duration_sec=60,
        width=1080,
        height=1920,
        status="ready",
    )
    anime_preset = next(preset for preset in store.list_prompt_presets("analysis") if preset["label"] == "Anime analysis")
    response = client.get(f"/sources/{smotvibe_source['id']}/studio")
    assert response.status_code == 200
    assert f'value="{anime_preset["id"]}" selected' in response.text

    response = client.post(f"/api/sources/{source['id']}/analyze", json={"provider": "mock"})
    assert response.status_code == 200
    analysis_id = response.json()["id"]
    assert response.json()["status"] == "succeeded"
    response = client.get(f"/sources/{source['id']}/studio")
    assert response.status_code == 200
    assert "Генерации" in response.text
    response = client.post(
        f"/ui/ai-analyses/{analysis_id}/delete",
        data={"next": f"/sources/{source['id']}/studio?stage=analysis"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert store.list_ai_analyses(source_id=source["id"]) == []

    account = store.upsert_account("youtube", "clip-target", "SID=a; HSID=b; SSID=c; APISID=d; SAPISID=e")
    clip = store.create_clip(source["id"], title="Clip API title")
    clip_path = settings.clip_dir / "clip-api.mp4"
    clip_path.write_bytes(b"clip")
    clip = store.finish_clip_render(
        clip["id"],
        status="succeeded",
        output_path=clip_path,
        preview_path=clip_path,
        duration_sec=5,
        width=1080,
        height=1920,
        size_bytes=clip_path.stat().st_size,
    )
    response = client.post(
        f"/api/clips/{clip['id']}/posts",
        json={"title": "", "description": "", "targets": [account["id"]], "privacy": "private"},
    )
    assert response.status_code == 200
    assert response.json()["clip_id"] == clip["id"]

    response = client.get("/accounts")
    assert response.status_code == 200
    assert "Аккаунты" in response.text

    response = client.get("/posts/new")
    assert response.status_code == 200
    assert "New Post" in response.text

    response = client.get("/jobs")
    assert response.status_code == 200
    assert "Публикации" in response.text

    response = client.get("/clips")
    assert response.status_code == 200
    assert "Клипы" in response.text

    # The presets page is gone; its forms now live in the shared Settings panel.
    response = client.get("/presets", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/sources"

    # The Settings panel (rendered on every page) hosts the music + render tabs.
    response = client.get("/sources")
    assert response.status_code == 200
    assert 'data-settings-tab="music"' in response.text
    assert "Цветовой стиль" in response.text
    assert "Музыкальный трек" in response.text

    response = client.post(
        "/api/audio-tracks",
        files={"file": ("loop.mp3", b"audio-bytes", "audio/mpeg")},
        data={"label": "Lofi loop"},
    )
    assert response.status_code == 200
    track_id = response.json()["id"]

    response = client.post(
        "/api/ffmpeg-presets",
        json={
            "label": "Music + cinematic",
            "music_track_id": track_id,
            "music_volume": 0.3,
            "color_style": "cinematic",
            "vignette": 0.4,
        },
    )
    assert response.status_code == 200
    assert response.json()["music_track_id"] == track_id
    assert response.json()["color_style"] == "cinematic"

    # The uploaded track shows in the settings Music tab.
    response = client.get("/sources")
    assert "Lofi loop" in response.text

    # Standalone clip upload: a finished clip becomes a publishable library entry
    # without going through analysis/render. ffprobe is stubbed so the test does
    # not need real media or the ffprobe binary.
    import app.main as main_module
    from app.ingest import MediaMetadata

    monkeypatch.setattr(
        main_module,
        "probe_media",
        lambda path: MediaMetadata(duration_sec=8, width=1080, height=1920, fps=30, raw={}),
    )
    response = client.post(
        "/ui/clips/upload",
        files={"file": ("my-edit.mp4", b"finished-clip-bytes", "video/mp4")},
        data={"title": "Hand-edited clip"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    uploaded_clip_id = int(response.headers["location"].rsplit("/", 1)[1])
    uploaded_clip = store.get_clip(uploaded_clip_id)
    assert uploaded_clip["status"] == "succeeded"
    assert uploaded_clip["title"] == "Hand-edited clip"
    # The backing source is hidden from the Projects list.
    assert all(item["id"] != uploaded_clip["source_id"] for item in store.list_sources())
    # But the clip is visible in the library.
    response = client.get("/clips")
    assert "Hand-edited clip" in response.text

    # An uploaded clip exposes the render panel; a studio-rendered one does not.
    response = client.get(f"/clips/{uploaded_clip_id}")
    assert "Рендер: субтитры" in response.text
    response = client.get(f"/clips/{clip['id']}")
    assert "Рендер: субтитры" not in response.text

    # The render route delegates to the render service and redirects to the new
    # clip. ffmpeg is not available in the test env, so the service is stubbed.
    rendered = store.create_clip(uploaded_clip["source_id"], title="Hand-edited clip")
    rendered = store.finish_clip_render(
        rendered["id"], status="succeeded", output_path=settings.clip_dir / "rendered.mp4",
        preview_path=settings.clip_dir / "rendered.mp4", duration_sec=8, width=1080, height=1920, size_bytes=10,
    )
    (settings.clip_dir / "rendered.mp4").write_bytes(b"x")
    captured = {}

    def fake_render(clip_id, **kwargs):
        captured["clip_id"] = clip_id
        captured["kwargs"] = kwargs
        return rendered

    monkeypatch.setattr(main_module.clip_render_service, "render_uploaded_clip", fake_render)
    response = client.post(
        f"/ui/clips/{uploaded_clip_id}/render",
        data={"use_subtitles": "true", "use_banner": "true"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"/clips/{rendered['id']}"
    assert captured["clip_id"] == uploaded_clip_id

    # Auto page renders and its start route forwards the parsed config to the
    # automation service.
    response = client.get("/auto")
    assert response.status_code == 200
    assert "Автонарезка" in response.text

    captured_auto = {}
    monkeypatch.setattr(
        main_module.automation_service,
        "start_run",
        lambda **kwargs: captured_auto.update(kwargs) or {"id": 1},
    )
    response = client.post(
        "/ui/auto/start",
        data={
            "url": "https://youtu.be/abc",
            "targets": [account["id"]],
            "use_subtitles": "true",
            "interval_hours": "2",
            "max_clips": "5",
            "privacy": "unlisted",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/auto"
    assert captured_auto["url"] == "https://youtu.be/abc"
    assert captured_auto["publish"]["targets"] == [account["id"]]
    assert captured_auto["publish"]["interval_hours"] == 2
    assert captured_auto["publish"]["max_clips"] == 5
    assert captured_auto["publish"]["privacy"] == "unlisted"

    # The pipeline itself (run synchronously here) renders the planned clips and
    # schedules a publish job per clip on the chosen accounts.
    auto = main_module.automation_service
    monkeypatch.setattr(auto, "_ingest", lambda url, selection: source)
    monkeypatch.setattr(auto.analysis, "run_analysis", lambda *a, **k: {})
    monkeypatch.setattr(store, "ensure_clip_plans_for_source", lambda source_id: None)
    monkeypatch.setattr(store, "list_clip_plans", lambda source_id: [{"id": 1}, {"id": 2}])
    monkeypatch.setattr(auto.render, "render_clip_plan", lambda plan_id, **k: clip)
    run = auto._new_run(label="auto-test", url="https://youtu.be/abc")
    auto._run(
        run,
        "https://youtu.be/abc",
        {},
        {"provider": "mock", "model": "", "prompt": ""},
        {"ffmpeg_preset_id": None, "subtitle_profile_id": None, "banner_id": 0, "music_track_id": None, "music_volume": None},
        {"targets": [account["id"]], "privacy": "private", "allow_comments": True, "start_at": None, "interval_hours": 0, "max_clips": 0},
    )
    assert run["status"] == "done", run.get("error")
    assert run["clips"] == 2
    assert run["jobs"] == 2


def test_api_and_docs_require_token(monkeypatch):
    from app.settings import settings

    settings.auth_enabled = True
    settings.api_token = "unit-test-token"

    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/accounts")
    assert response.status_code == 401

    response = client.get("/api/accounts", headers={"Authorization": "Bearer unit-test-token"})
    assert response.status_code == 200

    response = client.get("/docs", follow_redirects=False)
    assert response.status_code == 303

    response = client.get("/docs", cookies={settings.auth_cookie_name: "unit-test-token"})
    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.text
