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

    response = client.post(f"/api/sources/{source['id']}/analyze", json={"provider": "mock"})
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"

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
    assert "Accounts" in response.text

    response = client.get("/posts/new")
    assert response.status_code == 200
    assert "New Post" in response.text

    response = client.get("/jobs")
    assert response.status_code == 200
    assert "Jobs" in response.text

    response = client.get("/clips")
    assert response.status_code == 200
    assert "Clips" in response.text

    response = client.get("/presets")
    assert response.status_code == 200
    assert "Presets" in response.text


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
