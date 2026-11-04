"""Документ обложки (app/cover_docs.py): модель, файлы, экспорт, ссылка на редактирование и её изоляция."""

from __future__ import annotations

import json
import shutil
import struct
import subprocess
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import cover_docs as D
from app.agent_tools import ImageResult, ToolContext, run_tool
from app.settings import settings
from test_api_pipeline import _client
from test_cover_variants import _no_compositor, _rendered

NODE_READY = shutil.which("node") and (Path(settings.root_dir) / "node" / "motion-render.mjs").is_file() and (
    Path(settings.root_dir) / "node_modules" / "@napi-rs" / "canvas").exists()


def _png_bytes() -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    head = struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\xff\x00\x00\x80" * 2 for _ in range(2))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


PNG_1X1 = _png_bytes()


@pytest.fixture(autouse=True)
def _fresh_limits():
    D.limiter.reset()
    yield
    D.limiter.reset()


def _doc_with_clip(tmp_path, monkeypatch):
    client, store, _source, plan, clip = _rendered(tmp_path, monkeypatch)
    _no_compositor(monkeypatch)
    doc_id = client.post("/api/cover-docs", json={"clip_id": clip["id"]}).raise_for_status().json()["id"]
    return client, store, plan, clip, doc_id


def _blank(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    doc_id = client.post("/api/cover-docs", json={}).raise_for_status().json()["id"]
    return client, store, doc_id


def _link(client, doc_id, perm="edit", days=30) -> tuple[str, dict]:
    made = client.post(f"/api/cover-docs/{doc_id}/links", json={"perm": perm, "days": days}).raise_for_status().json()
    return made["url"].rsplit("/", 1)[-1], made


def _with_auth(monkeypatch):
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "owner-token-for-tests", raising=False)
    monkeypatch.setattr(settings, "ensure_api_token", lambda: "owner-token-for-tests", raising=False)


# --------------------------------------------------------------------------- #
# Модель
# --------------------------------------------------------------------------- #


def test_sanitize_keeps_known_fields_and_clamps():
    raw = {"bg": "url(evil)", "layers": [
        {"type": "text", "id": "t1", "text": "Привет\x00\x07\nмир", "size": 99999, "color": "red", "x": float("nan"),
         "opacity": 7, "evil": "<script>", "font": "Нет такого", "stroke": {"color": "#112233", "width": 1e9}},
        {"type": "image", "id": "t1", "src": "../../etc/passwd", "w": -5},
        {"type": "unknown"},
        "not a layer",
    ]}
    doc = D.sanitize_doc(raw, 1080, 1920, assets=set())
    assert doc["width"] == 1080 and doc["bg"] == "#101018"
    text, image = doc["layers"]
    assert text["text"] == "Привет\nмир" and text["size"] == 800 and text["color"] == "#ffffff" and text["x"] == 0
    assert text["opacity"] == 1 and "evil" not in text and text["font"] == "" and text["stroke"]["width"] == 120
    assert image["src"] == "" and image["w"] == 1 and image["id"] != "t1"


def test_sanitize_refuses_size_from_outside_and_too_many_layers():
    doc = D.sanitize_doc({"width": 99999, "height": 99999, "layers": []}, 1080, 1920, set())
    assert (doc["width"], doc["height"]) == (1080, 1920)
    with pytest.raises(ValueError):
        D.sanitize_doc({"layers": [{"type": "shape"}] * (D.MAX_LAYERS + 1)}, 1080, 1920, set())
    with pytest.raises(ValueError):
        D.sanitize_doc({"layers": "x"}, 1080, 1920, set())


def test_templates_fit_every_format():
    for fmt, (width, height) in D.V.COVER_SIZES.items():
        for template in D.TEMPLATES:
            layers = D.template_layers(template["id"], width, height, {"a" * 32 + ".jpg"}, frame="a" * 32 + ".jpg", text="Хук")
            assert layers, (fmt, template["id"])
            for layer in layers:
                assert layer["x"] >= 0 and layer["x"] + layer["w"] <= width + 1
                assert layer["y"] >= 0 and layer["y"] + layer["h"] <= height + 1
            assert any(layer["type"] == "text" and layer["text"] == "Хук" for layer in layers)
            assert any(layer["type"] == "image" and layer["src"] for layer in layers)
    with pytest.raises(ValueError):
        D.template_layers("нет", 1080, 1920, set())


def test_save_needs_fresh_revision(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    state = client.get(f"/api/cover-docs/{doc_id}/doc").json()
    body = {"doc": {"layers": [{"type": "shape", "name": "Плашка"}]}, "base_rev": state["rev"]}
    saved = client.put(f"/api/cover-docs/{doc_id}/doc", json=body).raise_for_status().json()
    assert saved["rev"] == state["rev"] + 1 and saved["doc"]["layers"][0]["name"] == "Плашка"
    stale = client.put(f"/api/cover-docs/{doc_id}/doc", json=body)
    assert stale.status_code == 409 and stale.json()["detail"]["rev"] == saved["rev"]


def test_body_limit_and_bad_json(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    assert client.put(f"/api/cover-docs/{doc_id}/doc", content=b"{").status_code == 400
    huge = b'{"doc":{"layers":[]},"x":"' + b"a" * (D.MAX_DOC_BYTES * 2 + 10) + b'"}'
    assert client.put(f"/api/cover-docs/{doc_id}/doc", content=huge).status_code == 413


# --------------------------------------------------------------------------- #
# Файлы
# --------------------------------------------------------------------------- #


def test_upload_reencodes_images_and_refuses_the_rest(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    made = client.post(f"/api/cover-docs/{doc_id}/assets", content=PNG_1X1).raise_for_status().json()
    assert D.ASSET_RE.match(made["name"]) and made["name"].endswith(".png")
    got = client.get(f"/api/cover-docs/{doc_id}/assets/{made['name']}")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    for bad in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"<html>", b"", b"GIF89a....",
                b"\x89PNG\r\n\x1a\nnot an image"):
        assert client.post(f"/api/cover-docs/{doc_id}/assets", content=bad).status_code == 400, bad
    assert client.post(f"/api/cover-docs/{doc_id}/assets", content=b"x" * (D.MAX_UPLOAD_BYTES + 1)).status_code == 413
    assert len(D.doc_assets(doc_id)) == 1


def test_asset_names_cannot_leave_the_folder(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    secret = D.doc_dir(doc_id).parent / "secret.png"
    secret.write_bytes(PNG_1X1)
    for name in ("../secret.png", "..%2Fsecret.png", "secret.png", "a" * 32 + ".png", "a" * 32 + ".svg"):
        assert client.get(f"/api/cover-docs/{doc_id}/assets/{name}").status_code == 404, name
    assert D.asset_path(doc_id, "../secret.png") is None


def test_frames_and_thumbs_come_from_the_clip(tmp_path, monkeypatch):
    client, _store, _plan, clip, doc_id = _doc_with_clip(tmp_path, monkeypatch)
    state = client.get(f"/api/cover-docs/{doc_id}/doc").json()
    assert state["clip"]["duration"] > 0 and state["format"] == "9:16"
    frame = client.post(f"/api/cover-docs/{doc_id}/frames", json={"t": 1.5, "origin": "render"}).raise_for_status().json()
    assert frame["name"].endswith(".jpg") and frame["t"] == 1.5 and frame["width"] > 0
    source = client.post(f"/api/cover-docs/{doc_id}/frames", json={"t": 1.5, "origin": "source"}).raise_for_status().json()
    assert source["name"] != frame["name"]
    # Момент за концом ролика — последний кадр, а не ошибка ffmpeg.
    client.post(f"/api/cover-docs/{doc_id}/frames", json={"t": 1e9}).raise_for_status()
    thumb = client.get(f"/api/cover-docs/{doc_id}/thumb?t=2.2")
    assert thumb.status_code == 200 and thumb.headers["content-type"] == "image/jpeg"
    assert (D.doc_dir(doc_id) / "thumbs" / "4.jpg").is_file()


def test_template_fills_frame_and_reports_empty_avatar(tmp_path, monkeypatch):
    client, store, _plan, clip, doc_id = _doc_with_clip(tmp_path, monkeypatch)
    out = client.post(f"/api/cover-docs/{doc_id}/template", json={"template": "avatar-bottom"}).raise_for_status().json()
    images = [layer for layer in out["layers"] if layer["type"] == "image"]
    assert images[0]["src"] in out["assets"] and images[1]["src"] == ""
    assert D.empty_slots({"layers": images}) == ["Аватар"]
    assert client.post(f"/api/cover-docs/{doc_id}/template", json={"template": "нет"}).status_code == 400
    library = client.get(f"/api/cover-docs/{doc_id}/library").json()
    assert {t["id"] for t in library["templates"]} == {t["id"] for t in D.TEMPLATES}
    assert library["fonts"] and all("file" in f for f in library["fonts"])


# --------------------------------------------------------------------------- #
# Экспорт
# --------------------------------------------------------------------------- #


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    return struct.unpack(">II", data[16:24])


@pytest.mark.skipif(not NODE_READY, reason="нет собранного компоновщика")
def test_export_draws_layers_and_keeps_alpha_order(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    client.put(f"/api/cover-docs/{doc_id}/doc", json={"doc": {"bg": "#102030", "layers": [
        {"type": "shape", "shape": "rect", "fill": "#ff0000", "x": 0, "y": 0, "w": 540, "h": 1920},
        {"type": "shape", "shape": "ellipse", "fill": "#00ff00", "x": 540, "y": 860, "w": 200, "h": 200, "opacity": 0.5},
        {"type": "text", "text": "Текст", "x": 40, "y": 100, "w": 460, "h": 160, "size": 120, "color": "#ffffff",
         "stroke": {"color": "#000000", "width": 8}, "weight": 900},
    ]}}).raise_for_status()
    got = client.get(f"/api/cover-docs/{doc_id}/export.png?download=1")
    assert got.status_code == 200 and got.headers["content-type"] == "image/png"
    assert "attachment" in got.headers["content-disposition"]
    out = tmp_path / "x.png"
    out.write_bytes(got.content)
    assert _png_size(out) == (1080, 1920)
    pixels = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True
    ).stdout

    def at(x: int, y: int) -> tuple[int, ...]:
        i = (y * 1080 + x) * 3
        return tuple(pixels[i:i + 3])

    assert at(100, 1000) == (255, 0, 0)  # фигура
    assert at(1000, 100) == (16, 32, 48)  # фон
    r, g, b = at(640, 960)  # полупрозрачный эллипс поверх фона
    assert g > 120 and r < 40 and b < 50
    text_row = pixels[(180 * 1080) * 3:(181 * 1080) * 3]
    # Текст нарисован: в полосе заголовка есть и белые, и чёрные пиксели (заливка и обводка).
    assert any(text_row[i:i + 3] == b"\xff\xff\xff" for i in range(0, 1620, 3))
    assert any(text_row[i:i + 3] == b"\x00\x00\x00" for i in range(0, 1620, 3))
    # Без правок файл не перерисовывается, после правки — новая версия.
    first = next(D.doc_dir(doc_id).glob("out-*.png"))
    assert D.render_doc(store, doc_id) == first
    client.put(f"/api/cover-docs/{doc_id}/doc", json={"doc": {"bg": "#000000", "layers": []}}).raise_for_status()
    assert D.render_doc(store, doc_id) != first and not first.exists()
    assert client.get(f"/api/cover-docs/{doc_id}/export.jpg").headers["content-type"] == "image/jpeg"


@pytest.mark.skipif(not NODE_READY, reason="нет собранного компоновщика")
def test_apply_makes_it_the_clip_cover_like_a_variant(tmp_path, monkeypatch):
    client, store, plan, clip, doc_id = _doc_with_clip(tmp_path, monkeypatch)
    client.put(f"/api/cover-docs/{doc_id}/doc", json={"doc": {"layers": [{"type": "text", "text": "МОЙ ХУК", "w": 800, "h": 200}]}})
    applied = client.post(f"/api/cover-docs/{doc_id}/apply", json={"burn": True}).raise_for_status().json()
    assert applied["render_cover"]["image"] and applied["cover"]["renderer"] == "cover_doc"
    assert applied["cover"]["text"] == "МОЙ ХУК" and applied["cover"]["chosen"]
    assert Path(store.get_clip_cover_path(clip["id"])).is_file()
    assert store.get_clip_plan(plan["id"])["render_settings"]["cover"]["burn"] is True
    # Повтор заменяет прежний вариант из редактора, а не копит файлы.
    client.put(f"/api/cover-docs/{doc_id}/doc", json={"doc": {"layers": [{"type": "text", "text": "ДРУГОЙ", "w": 800, "h": 200}]}})
    client.post(f"/api/cover-docs/{doc_id}/apply", json={"burn": False}).raise_for_status()
    covers = client.get(f"/api/clips/{clip['id']}/covers").json()["covers"]
    assert [c["text"] for c in covers if c["renderer"] == "cover_doc"] == ["ДРУГОЙ"]
    assert sum(1 for c in covers if c["chosen"]) == 1


# --------------------------------------------------------------------------- #
# Ссылка: токены
# --------------------------------------------------------------------------- #


def test_token_is_long_random_and_stored_only_as_hash(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    token, made = _link(client, doc_id)
    assert D.TOKEN_RE.match(token) and len(token) * 6 >= 128
    rows = store.db.query_all("SELECT * FROM cover_doc_links", ())
    assert rows[0]["token_hash"] == D.token_hash(token)
    assert token not in json.dumps([dict(r) for r in rows])
    listed = client.get(f"/api/cover-docs/{doc_id}/links").json()["links"]
    assert listed[0]["id"] == made["id"] and token not in json.dumps(listed) and "token_hash" not in listed[0]
    assert token != _link(client, doc_id)[0]


def test_link_url_uses_public_address_or_warns(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    _token, made = _link(client, doc_id)
    assert made["url"].startswith("/c/") and made["warning"]
    from app import notify_tg

    monkeypatch.setattr(notify_tg, "config", lambda _store: {"public_url": "https://vvf.example.com"})
    _token, made = _link(client, doc_id)
    assert made["url"].startswith("https://vvf.example.com/c/") and "warning" not in made


def test_revoked_expired_and_wrong_tokens_look_the_same(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    live, made = _link(client, doc_id)
    assert client.get(f"/api/c/{live}/doc").status_code == 200
    gone, gone_made = _link(client, doc_id)
    client.delete(f"/api/cover-docs/{doc_id}/links/{gone_made['id']}").raise_for_status()
    old, old_made = _link(client, doc_id)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(timespec="seconds")
    store.db.execute("UPDATE cover_doc_links SET expires_at = ? WHERE id = ?", (past, old_made["id"]))
    answers = {token: client.get(f"/api/c/{token}/doc") for token in (gone, old, "A" * 32, "короткий")}
    for token, answer in answers.items():
        assert answer.status_code == 404, token
        assert answer.json()["detail"] == "Ссылка не работает"
    # Отзыв другой ссылки не задел живую.
    assert client.get(f"/api/c/{live}/doc").status_code == 200
    assert client.delete(f"/api/cover-docs/{doc_id}/links/99999").status_code == 404


def test_link_days_are_bounded_and_perm_checked(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    assert client.post(f"/api/cover-docs/{doc_id}/links", json={"perm": "admin"}).status_code == 400
    _token, made = _link(client, doc_id, days=100000)
    left = datetime.fromisoformat(made["expires_at"]) - datetime.now(timezone.utc)
    assert left <= timedelta(days=D.MAX_LINK_DAYS + 1)


# --------------------------------------------------------------------------- #
# Ссылка: изоляция
# --------------------------------------------------------------------------- #


def test_view_link_cannot_change_anything(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    token, _made = _link(client, doc_id, perm="view")
    state = client.get(f"/api/c/{token}/doc").json()
    assert state["perm"] == "view" and "id" not in state and "clip_id" not in state
    assert client.put(f"/api/c/{token}/doc", json={"doc": {"layers": []}}).status_code == 403
    assert client.post(f"/api/c/{token}/assets", content=PNG_1X1).status_code == 403
    assert client.post(f"/api/c/{token}/frames", json={"t": 1}).status_code == 403
    assert client.post(f"/api/c/{token}/template", json={"template": "clean"}).status_code == 403


def test_edit_link_reaches_only_its_document(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    other = client.post("/api/cover-docs", json={}).json()["id"]
    secret = client.post(f"/api/cover-docs/{other}/assets", content=PNG_1X1).json()["name"]
    token, _made = _link(client, doc_id)
    client.put(f"/api/c/{token}/doc", json={"doc": {"layers": [{"type": "image", "src": secret}]}}).raise_for_status()
    # Чужой файл нельзя ни подставить в слой, ни скачать.
    assert client.get(f"/api/c/{token}/doc").json()["doc"]["layers"][0]["src"] == ""
    assert client.get(f"/api/c/{token}/assets/{secret}").status_code == 404
    # У документа по ссылке нет ни действий владельца, ни чужого id в адресе.
    assert client.post(f"/api/c/{token}/apply", json={}).status_code in (404, 405)
    assert client.post(f"/api/c/{token}/links", json={}).status_code in (404, 405)
    assert client.delete(f"/api/c/{token}").status_code in (404, 405)
    assert D.get_doc(store, other)["rev"] == 1


def test_token_does_not_open_the_rest_of_the_service(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    token, _made = _link(client, doc_id)
    _with_auth(monkeypatch)
    client.cookies.clear()
    assert client.get(f"/api/c/{token}/doc").status_code == 200
    assert client.get(f"/api/c/{token}/library").status_code == 200
    bearer = {"Authorization": f"Bearer {token}"}
    closed = ["/api/clips", "/api/sources", "/api/settings/telegram", "/api/cover-docs",
              f"/api/cover-docs/{doc_id}/doc", f"/api/cover-docs/{doc_id}/links", "/api/modules"]
    for path in closed:
        assert client.get(path, headers=bearer).status_code == 401, path
        assert client.get(f"{path}?token={token}").status_code == 401, path
    assert client.post("/api/cover-docs", json={}, headers=bearer).status_code == 401
    assert client.post(f"/api/cover-docs/{doc_id}/apply", json={}, headers=bearer).status_code == 401
    assert client.delete(f"/api/cover-docs/{doc_id}", headers=bearer).status_code == 401
    # Токен ссылки не годится и как кука входа.
    client.cookies.set(settings.auth_cookie_name, token)
    assert client.get("/api/clips").status_code == 401
    client.cookies.clear()


def test_public_page_only_for_live_token(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    token, made = _link(client, doc_id)
    _with_auth(monkeypatch)
    client.cookies.clear()
    good = client.get(f"/c/{token}", follow_redirects=False)
    # В тестах сборки SPA может не быть: тогда та же заглушка, но заголовки и отсутствие входа важнее.
    assert good.status_code in (200, 404)
    assert good.headers["referrer-policy"] == "no-referrer" and "noindex" in good.headers["x-robots-tag"]
    assert "no-store" in good.headers["cache-control"] and good.headers["x-frame-options"] == "DENY"
    bad = client.get("/c/" + "A" * 32, follow_redirects=False)
    assert bad.status_code == 404 and "Ссылка не работает" in bad.text
    # Остальной SPA по-прежнему за входом.
    assert client.get("/clips", follow_redirects=False, headers={"accept": "text/html"}).status_code in (303, 401, 404)


def test_module_off_closes_the_link_too(tmp_path, monkeypatch):
    client, store, doc_id = _blank(tmp_path, monkeypatch)
    token, _made = _link(client, doc_id)
    from app import modules

    assert modules.modules_for_route("GET", f"/api/c/{token}/doc") == ["covers"]
    assert modules.modules_for_route("GET", f"/c/{token}") == ["covers"]
    assert modules.modules_for_route("POST", "/api/cover-docs") == ["covers"]
    assert "covers" not in modules.modules_for_route("GET", "/clips")
    monkeypatch.setattr(modules, "enabled_map", lambda _store=None: {"covers": False})
    assert client.get(f"/api/c/{token}/doc").status_code == modules.HTTP_STATUS
    assert client.get(f"/c/{token}").status_code == modules.HTTP_STATUS


def test_rate_limit_per_token_and_for_guessing(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    token, _made = _link(client, doc_id)
    monkeypatch.setitem(D.LIMITS, "read", 3)
    monkeypatch.setitem(D.LIMITS, "bad_token", 2)
    codes = [client.get(f"/api/c/{token}/doc").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    # Владельца лимит не касается.
    assert all(client.get(f"/api/cover-docs/{doc_id}/doc").status_code == 200 for _ in range(5))
    guesses = [client.get("/api/c/" + str(i).zfill(32) + "/doc").status_code for i in range(4)]
    assert guesses[:2] == [404, 404] and guesses[-1] == 429


def test_rate_limiter_window_passes():
    limiter = D.RateLimiter()
    for _ in range(D.LIMITS["frame"]):
        assert limiter.allow("k", "frame", now=100.0)
    assert not limiter.allow("k", "frame", now=100.5)
    assert limiter.allow("other", "frame", now=100.5)
    assert limiter.allow("k", "frame", now=100.0 + D.WINDOW_SEC + 1)


def test_small_requests_have_small_body_limit(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    token, _made = _link(client, doc_id)
    big = b'{"t":1,"x":"' + b"a" * 10000 + b'"}'
    assert client.post(f"/api/c/{token}/frames", content=big).status_code == 413
    assert client.post(f"/api/c/{token}/template", content=big).status_code == 413


def test_text_is_only_data(tmp_path, monkeypatch):
    client, _store, doc_id = _blank(tmp_path, monkeypatch)
    token, _made = _link(client, doc_id)
    evil = "<img src=x onerror=alert(1)> {{7*7}} $(touch /tmp/x)"
    client.put(f"/api/c/{token}/doc", json={"doc": {"layers": [{"type": "text", "text": evil, "name": evil}]}}).raise_for_status()
    layer = client.get(f"/api/c/{token}/doc").json()["doc"]["layers"][0]
    assert layer["text"] == evil
    assert client.get(f"/api/c/{token}/fonts/..%2F..%2Fapp.sqlite").status_code == 404


def test_doc_survives_clip_delete_and_delete_removes_files(tmp_path, monkeypatch):
    client, store, _plan, clip, doc_id = _doc_with_clip(tmp_path, monkeypatch)
    client.post(f"/api/cover-docs/{doc_id}/frames", json={"t": 1, "origin": "render"}).raise_for_status()
    store.db.execute("UPDATE jobs SET clip_id = NULL WHERE clip_id = ?", (clip["id"],))
    store.db.execute("DELETE FROM clips WHERE id = ?", (clip["id"],))
    assert D.get_doc(store, doc_id)["clip_id"] is None
    assert client.post(f"/api/cover-docs/{doc_id}/frames", json={"t": 1}).status_code == 400
    assert client.delete(f"/api/cover-docs/{doc_id}").json() == {"deleted": True}
    assert not D.doc_dir(doc_id).exists()
    assert client.get(f"/api/cover-docs/{doc_id}/doc").status_code == 404


# --------------------------------------------------------------------------- #
# Инструменты агента
# --------------------------------------------------------------------------- #


@pytest.mark.skipif(not NODE_READY, reason="нет собранного компоновщика")
def test_agent_tools_create_edit_render_share(tmp_path, monkeypatch):
    client, store, _source, plan, clip = _rendered(tmp_path, monkeypatch)
    _no_compositor(monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    made, ok = run_tool(ctx, "create_cover_doc", {"template": "clean", "text": "ТЫ ЭТО ВИДЕЛ"})
    assert ok, made
    assert isinstance(made, ImageResult) and made.mime_type == "image/png"
    doc_id = made.info["doc_id"]
    text = next(layer for layer in made.info["layers"] if layer["type"] == "text")
    assert text["text"] == "ТЫ ЭТО ВИДЕЛ"
    edited, ok = run_tool(ctx, "update_cover_doc", {
        "doc_id": doc_id,
        "changes": [{"id": text["id"], "text": "Новый хук", "color": "#ffe600"}],
        "add": [{"type": "shape", "shape": "rect", "name": "Полоса", "x": 0, "y": 0, "w": 100, "h": 100, "fill": "#ff0000"}],
        "frame": {"t": 1, "origin": "render"},
    })
    assert ok, edited
    names = [layer["name"] for layer in edited.info["layers"]]
    assert "Полоса" in names and names[0] == "Кадр"
    assert next(layer for layer in edited.info["layers"] if layer["id"] == text["id"])["text"] == "Новый хук"
    applied, ok = run_tool(ctx, "render_cover_doc", {"doc_id": doc_id, "apply": True, "burn": False})
    assert ok and applied.info["applied"] is True
    share, ok = run_tool(ctx, "share_cover_doc", {"doc_id": doc_id, "perm": "view", "days": 2})
    assert ok and share["url"].count("/c/") == 1 and share["perm"] == "view"
    revoked, ok = run_tool(ctx, "share_cover_doc", {"doc_id": doc_id, "revoke": share["id"]})
    assert ok and revoked["revoked"] == share["id"]
    token = share["url"].rsplit("/", 1)[-1]
    assert client.get(f"/api/c/{token}/doc").status_code == 404


def test_agent_tools_are_scoped_to_the_clip(tmp_path, monkeypatch):
    client, store, _source, plan, clip = _rendered(tmp_path, monkeypatch)
    _no_compositor(monkeypatch)
    doc_id = client.post("/api/cover-docs", json={"clip_id": clip["id"]}).json()["id"]
    other_doc = client.post("/api/cover-docs", json={}).json()["id"]
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    refused, ok = run_tool(ctx, "share_cover_doc", {"doc_id": other_doc})
    assert not ok and "не этого клипа" in json.dumps(refused, ensure_ascii=False)
    share, ok = run_tool(ctx, "share_cover_doc", {"doc_id": doc_id})
    assert ok and share["url"]
