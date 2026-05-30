def test_html_pages_render(tmp_path, monkeypatch):
    monkeypatch.setenv("POSTING_PROVIDER_MODE", "mock")
    from app.settings import settings

    settings.auth_enabled = False
    settings.api_token = ""
    settings.data_dir = tmp_path / "data"
    settings.upload_dir = settings.data_dir / "uploads"
    settings.runtime_dir = settings.data_dir / "runtime"
    settings.log_dir = settings.data_dir / "logs"
    settings.db_path = settings.data_dir / "app.sqlite"
    settings.secret_key_path = settings.data_dir / "secret.key"

    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    response = client.get("/accounts")
    assert response.status_code == 200
    assert "Accounts" in response.text

    response = client.get("/posts/new")
    assert response.status_code == 200
    assert "New Post" in response.text

    response = client.get("/jobs")
    assert response.status_code == 200
    assert "Jobs" in response.text


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
