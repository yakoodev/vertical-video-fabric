"""Вопросы по стриму и выбор мемов: модель, ответы в бриф, страница, инструменты, каталог без реальных людей."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from app import meme_catalog as C
from app import projects as P
from app import stream_questions as Q
from app.agent_tools import ToolContext, run_tool
from app.settings import settings
from test_api_pipeline import _client

REAL_GET = C._get  # настоящий, до подмены фикстурой
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0\x00\x00\x00\x00IEND\xaeB`\x82")


def _video(path: Path, seconds: int = 12) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size=320x240:rate=15:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True)


def _source(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    video = tmp_path / "stream.mp4"
    _video(video)
    with video.open("rb") as fileobj:
        source = client.post("/api/sources", files={"file": ("stream.mp4", fileobj, "video/mp4")}).raise_for_status().json()
    return client, store, source


def _ask(**over):
    return {"kind": "context", "t_start": 2, "t_end": 5, "question": "Что тут происходит?",
            "options": ["Шутка", "Серьёзно"], **over}


@pytest.fixture(autouse=True)
def _no_catalog_network(monkeypatch, tmp_path):
    # Каталог не ходит в сеть в тестах: шаблоны — только те, что дал тест.
    monkeypatch.setattr(C, "_list_cache", (0.0, []))
    monkeypatch.setattr(C, "_api_list", lambda: [
        {"id": "87743020", "name": "Two Buttons", "url": "https://i.imgflip.com/1g8my4.jpg"},
        {"id": "181913649", "name": "Drake Hotline Bling", "url": "https://i.imgflip.com/30b1gx.jpg"},
    ])
    monkeypatch.setattr(C, "_get", lambda url: PNG)


def test_add_validates_and_dedupes(tmp_path, monkeypatch):
    _client_, store, source = _source(tmp_path, monkeypatch)
    sid = source["id"]
    out = Q.add_questions(store, sid, [_ask(), _ask(question="что тут происходит"), _ask(question="Кто это?", kind="who")])
    assert len(out["added"]) == 2 and len(out["duplicates"]) == 1
    for bad, why in [(_ask(question="Новый?", options=["один"]), "вариант"),
                     (_ask(question="Новый?", kind="x"), "kind"),
                     (_ask(question="Новый?", scope="x"), "scope"),
                     (_ask(question="Новый?", t_start=9999), "за концом"),
                     (_ask(question=""), "пустой")]:
        with pytest.raises(ValueError, match=why):
            Q.add_questions(store, sid, [bad])
    # Плохой вопрос в пачке не оставляет половину пачки
    with pytest.raises(ValueError):
        Q.add_questions(store, sid, [_ask(question="Третий?"), _ask(question="x")])
    assert len(Q.list_questions(store, sid)) == 2
    with pytest.raises(KeyError):
        Q.add_questions(store, 99999, [_ask()])


def test_answer_skip_reopen_and_page_progress(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    sid = source["id"]
    qid = Q.add_questions(store, sid, [_ask()])["added"][0]
    state = client.get(f"/api/sources/{sid}/stream-questions").raise_for_status().json()
    assert state["progress"]["questions"] == {"total": 1, "answered": 0, "skipped": 0, "open": 1}

    fresh = client.put(f"/api/stream-questions/{qid}/answer", json={"option": 1, "text": " и это смешно "}).json()
    assert fresh["status"] == "answered" and fresh["answer"] == "Серьёзно. и это смешно"

    assert client.put(f"/api/stream-questions/{qid}/answer", json={"option": 7}).status_code == 400
    assert client.put(f"/api/stream-questions/{qid}/answer", json={"option": "x"}).status_code == 400
    assert client.put("/api/stream-questions/99999/answer", json={"option": 0}).status_code == 404

    skipped = client.put(f"/api/stream-questions/{qid}/answer", json={"skip": True}).json()
    assert skipped["status"] == "skipped" and skipped["answer"] == ""
    assert Q.answers_brief(store, sid)["answers"] == []

    reopened = client.put(f"/api/stream-questions/{qid}/answer", json={"option": None, "text": ""}).json()
    assert reopened["status"] == "open"


def test_channel_answers_go_to_project_brief_and_stream_ones_do_not(tmp_path, monkeypatch):
    _client_, store, source = _source(tmp_path, monkeypatch)
    sid = source["id"]
    project = P.create_project(store, "Мура", source_ids=[sid])
    ids = Q.add_questions(store, sid, [
        _ask(question="Кто такой Глеб?", kind="who", scope="channel", about="Глеб", options=["Модератор", "Друг"]),
        _ask(question="В чём шутка про чибиков?", kind="joke", scope="channel", about="чибики", options=["Зрители", "Дети"]),
        _ask(question="Что за сервер?", scope="channel", options=["Свой", "Чужой"]),
        _ask(question="Это смешно?", options=["Да", "Нет"]),
    ])["added"]
    assert Q.answer_question(store, ids[0], 0)["brief"]["section"] == "people"
    assert Q.answer_question(store, ids[1], 0, "чат")["brief"]["section"] == "jokes"
    assert Q.answer_question(store, ids[2], 1)["brief"]["section"] == "notes"
    assert Q.answer_question(store, ids[3], 0)["brief"] == {"written": False}
    # Повторный ответ не плодит строки
    Q.answer_question(store, ids[2], 0)
    Q.answer_question(store, ids[0], 1)

    brief = P.get_brief(store, project["id"])["brief"]
    assert [(p["name"], p["who"]) for p in brief["people"]] == [("Глеб", "Друг")]
    assert brief["jokes"][0]["about"] == "Зрители. чат"
    assert brief["notes"] == "Что за сервер? — Свой"
    assert "смешно" not in json.dumps(brief, ensure_ascii=False)


def test_channel_answer_without_project_stays_with_the_stream(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    qid = Q.add_questions(store, source["id"], [_ask(scope="channel", kind="who", about="Глеб")])["added"][0]
    out = client.put(f"/api/stream-questions/{qid}/answer", json={"option": 0}).json()
    assert out["status"] == "answered" and out["brief"]["written"] is False and "не в проекте" in out["brief"]["reason"]


def test_preview_clip_is_short_cached_and_served_with_ranges(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    qid = Q.add_questions(store, source["id"], [_ask()])["added"][0]
    response = client.get(f"/api/stream-questions/{qid}/clip")
    assert response.status_code == 200 and response.headers["content-type"] == "video/mp4"
    clips = list(Q.clips_dir().iterdir())
    assert len(clips) == 1
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(clips[0])],
                           check=True, stdout=subprocess.PIPE)
    assert 8.0 <= float(probe.stdout) <= 9.5  # момент 2–5 с и по 3 с вокруг, начало упирается в 0
    mtime = clips[0].stat().st_mtime_ns
    partial = client.get(f"/api/stream-questions/{qid}/clip", headers={"Range": "bytes=0-99"})
    assert partial.status_code == 206 and len(partial.content) == 100
    assert clips[0].stat().st_mtime_ns == mtime
    assert client.get(f"/api/stream-questions/{qid}/thumb").headers["content-type"] == "image/jpeg"


def test_preview_is_capped_and_missing_recording_is_404(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    assert Q._span({"duration_sec": 10000}, 100, 900) == (97.0, Q.MAX_CLIP_SEC)
    qid = Q.add_questions(store, source["id"], [_ask()])["added"][0]
    Path(store.get_source(source["id"])["local_path"]).unlink()
    assert client.get(f"/api/stream-questions/{qid}/clip").status_code == 404
    assert client.get(f"/api/stream-questions/{qid}/thumb").status_code == 404
    # Сам вопрос и ответ живут и без записи
    assert client.put(f"/api/stream-questions/{qid}/answer", json={"option": 0}).status_code == 200


def test_every_new_route_needs_login(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "auth_enabled", True)
    monkeypatch.setattr(settings, "api_token", "secret-token")
    client.cookies.clear()
    for method, url in [("get", f"/api/sources/{source['id']}/stream-questions"), ("put", "/api/stream-questions/1/answer"),
                        ("get", "/api/stream-questions/1/clip"), ("get", "/api/stream-questions/1/thumb"),
                        ("put", "/api/meme-moments/1/choice"), ("get", "/api/meme-moments/1/clip"),
                        ("get", "/api/meme-moments/1/thumb"), ("get", "/api/meme-options/1/preview")]:
        assert getattr(client, method)(url, **({"json": {}} if method == "put" else {})).status_code == 401, url


def test_catalog_has_no_real_people_and_takes_only_allowed_templates():
    # Ручной список: фото реальных людей из топа imgflip сюда попасть не должны.
    for name in ("Drake Hotline Bling", "Distracted Boyfriend", "Roll Safe Think About It", "Hide the Pain Harold",
                 "Woman Yelling At Cat", "Change My Mind", "Disaster Girl", "UNO Draw 25 Cards"):
        assert name not in C.ALLOWED
    names = {i["name"] for i in C.list_catalog()["items"]}
    assert "Two Buttons" in names and "Drake Hotline Bling" not in names
    assert [i["name"] for i in C.list_catalog("всё горит")["items"]] == ["This Is Fine"]
    assert all(i["license"] == C.LICENSE_NOTE for i in C.list_catalog()["items"])
    with pytest.raises(C.CatalogError):
        C.fetch_template("Drake Hotline Bling")


def test_catalog_works_without_network(monkeypatch):
    def down():
        raise OSError("нет сети")

    monkeypatch.setattr(C, "_api_list", down)
    out = C.list_catalog()
    assert out["total"] == len(C.ALLOWED) and "warning" in out
    # Шаблон из зеркала доступен и без списка imgflip
    assert next(i for i in out["items"] if i["name"] == "This Is Fine")["available"] is True
    assert next(i for i in out["items"] if i["name"] == "Evil Kermit")["available"] is False
    assert C.fetch_template("This Is Fine")["source"] == C.MEMEGEN_SOURCE


def test_mirror_only_lists_allowed_templates():
    assert set(C.MEMEGEN) <= set(C.ALLOWED)


def _png(width, height):
    from io import BytesIO

    from PIL import Image

    out = BytesIO()
    Image.new("RGB", (width, height), "white").save(out, "PNG")
    return out.getvalue()


def test_fallback_address_must_give_the_same_picture(monkeypatch):
    meme = {"id": "87743020", "name": "Two Buttons", "url": "https://i.imgflip.com/1g8my4.png", "width": 60, "height": 90}
    monkeypatch.setattr(C, "_api_list", lambda: [meme])
    monkeypatch.setattr(C, "_cdn_ok", True)
    monkeypatch.setattr(C, "MEMEGEN", {})  # зеркала нет: идём через imgflip
    asked = []

    def cdn_down(url):
        asked.append(url)
        if "i.imgflip.com" in url:
            raise OSError("TLS")
        return _png(10, 10)

    monkeypatch.setattr(C, "_get", cdn_down)
    with pytest.raises(C.CatalogError, match="другая картинка"):
        C.fetch_template("Two Buttons")
    assert C._cdn_ok is False  # второй раз CDN не ждём

    asked.clear()
    monkeypatch.setattr(C, "_get", lambda url: asked.append(url) or _png(60, 90))
    assert C.fetch_template("Two Buttons")["path"].is_file()
    assert asked == ["https://imgflip.com/s/meme/Two-Buttons.png"]


def test_catalog_refuses_other_hosts():
    with pytest.raises(C.CatalogError):
        REAL_GET("https://evil.example/x.png")


def test_memes_full_cycle(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    sid = source["id"]
    upload = tmp_path / "own.png"
    upload.write_bytes(PNG)
    with upload.open("rb") as fileobj:
        asset = client.post("/api/montage-assets", files={"file": ("own.png", fileobj, "image/png")},
                            data={"label": "Свой"}).raise_for_status().json()
    out = Q.add_meme_choices(store, sid, [{
        "t_start": 3, "t_end": 6, "why": "Мура заперла чибика",
        "options": [{"catalog": "Two Buttons"}, {"asset_id": asset["id"], "note": "из библиотеки"}],
    }, {
        "t_start": 7, "why": "один вариант", "options": [{"catalog": "Two Buttons"}],
    }, {
        "t_start": 8, "why": "реальный человек", "options": [{"catalog": "Drake Hotline Bling"}, {"catalog": "Two Buttons"}],
    }])
    assert len(out["added"]) == 1 and len(out["failed"]) == 2
    assert "Drake" in json.dumps(out["failed"][1], ensure_ascii=False)
    moment_id = out["added"][0]["id"]

    # Повтор того же момента не плодит
    again = Q.add_meme_choices(store, sid, [{"t_start": 3, "t_end": 6, "why": "Мура заперла чибика", "options": []}])
    assert again["duplicates"] == [moment_id]

    state = client.get(f"/api/sources/{sid}/stream-questions").json()
    moment = state["moments"][0]
    assert "file_path" not in moment["options"][0]
    first, own = moment["options"]
    assert first["license"] == C.LICENSE_NOTE and first["source"] == C.MEMEGEN_SOURCE and first["asset_id"] is None

    preview = client.get(f"/api/meme-options/{first['id']}/preview")
    assert preview.status_code == 200 and preview.content == PNG
    assert preview.headers["x-content-type-options"] == "nosniff"

    chosen = client.put(f"/api/meme-moments/{moment_id}/choice", json={"option_id": first["id"]}).json()
    assert chosen["status"] == "chosen" and chosen["chosen_option_id"] == first["id"]
    asset_id = next(o for o in chosen["options"] if o["id"] == first["id"])["asset_id"]
    saved = store.get_montage_asset(asset_id)
    assert saved["kind"] == "image" and C.MEMEGEN_SOURCE in saved["description"] and C.LICENSE_NOTE in saved["description"]
    assert Path(saved["file_path"]).is_file()
    assert Q.answers_brief(store, sid)["memes"][0]["asset_id"] == asset_id

    # Выбрать снова — не плодит файлы в библиотеке
    assets_before = len(store.list_montage_assets())
    client.put(f"/api/meme-moments/{moment_id}/choice", json={"option_id": first["id"]}).raise_for_status()
    assert len(store.list_montage_assets()) == assets_before

    own_chosen = client.put(f"/api/meme-moments/{moment_id}/choice", json={"option_id": own["id"]}).json()
    assert next(o for o in own_chosen["options"] if o["id"] == own["id"])["asset_id"] == asset["id"]

    assert client.put(f"/api/meme-moments/{moment_id}/choice", json={"none": True}).json()["status"] == "none"
    assert Q.answers_brief(store, sid)["memes"][0]["status"] == "none"
    assert client.put(f"/api/meme-moments/{moment_id}/choice", json={"option_id": None}).json()["status"] == "open"
    assert client.put(f"/api/meme-moments/{moment_id}/choice", json={"option_id": 99999}).status_code == 400
    assert client.put("/api/meme-moments/99999/choice", json={"none": True}).status_code == 404


def test_url_option_needs_origin_and_refuses_private_addresses(tmp_path, monkeypatch):
    _client_, store, source = _source(tmp_path, monkeypatch)
    out = Q.add_meme_choices(store, source["id"], [{
        "t_start": 1, "why": "по ссылке",
        "options": [{"url": "https://example.com/a.png"}, {"url": "http://127.0.0.1/secret.png", "source": "x", "license": "y"}],
    }])
    assert out["added"] == []
    problems = json.dumps(out["failed"], ensure_ascii=False)
    assert "source и license" in problems and "недоступна" in problems


def test_option_file_outside_data_dir_is_not_served(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    moment = store.db.execute("INSERT INTO meme_moments (source_id, t_start, why) VALUES (?, 1, 'x')", (source["id"],)).lastrowid
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    option = store.db.execute("INSERT INTO meme_options (moment_id, file_path, mime) VALUES (?, ?, 'image/png')",
                              (moment, str(outside))).lastrowid
    assert client.get(f"/api/meme-options/{option}/preview").status_code == 404


def test_tools_fill_and_read(tmp_path, monkeypatch):
    _client_, store, source = _source(tmp_path, monkeypatch)
    sid = source["id"]
    ctx = ToolContext(store=store, clip_plan_id=None)
    result, ok = run_tool(ctx, "add_stream_questions", {"source_id": sid, "questions": [_ask()]})
    assert ok and result["page"] == f"/projects/{sid}/questions"
    qid = result["added"][0]
    result, ok = run_tool(ctx, "add_stream_questions", {"source_id": sid, "questions": [{"question": "?"}]})
    assert not ok

    Q.answer_question(store, qid, 0)
    result, ok = run_tool(ctx, "list_stream_answers", {"source_id": sid})
    assert ok and result["answers"][0]["answer"] == "Шутка" and result["progress"]["questions"]["answered"] == 1

    result, ok = run_tool(ctx, "list_meme_catalog", {"q": "pikachu"})
    assert ok and [i["name"] for i in result["items"]] == ["Surprised Pikachu"]

    result, ok = run_tool(ctx, "add_meme_choices", {"source_id": sid, "moments": [
        {"t_start": 2, "why": "шутка", "options": [{"catalog": "Two Buttons"}, {"catalog": "Two Buttons", "title": "Другой текст"}]}]})
    assert ok and result["added"][0]["options"] == 2
    result, ok = run_tool(ctx, "list_meme_choices", {"source_id": sid, "status": "open"})
    assert ok and len(result["moments"]) == 1


def test_project_brief_carries_stream_answers(tmp_path, monkeypatch):
    _client_, store, source = _source(tmp_path, monkeypatch)
    sid = source["id"]
    P.create_project(store, "Мура", source_ids=[sid])
    qid = Q.add_questions(store, sid, [_ask()])["added"][0]
    Q.answer_question(store, qid, 0)
    result, ok = run_tool(ToolContext(store=store, clip_plan_id=None), "get_project_brief", {"source_id": sid})
    assert ok and result["stream_answers"]["answers"][0]["answer"] == "Шутка"


def test_deleting_source_removes_questions_and_memes(tmp_path, monkeypatch):
    client, store, source = _source(tmp_path, monkeypatch)
    sid = source["id"]
    Q.add_questions(store, sid, [_ask()])
    Q.add_meme_choices(store, sid, [{"t_start": 2, "why": "шутка", "options": [{"catalog": "Two Buttons"}, {"catalog": "Two Buttons"}]}])
    client.delete(f"/api/sources/{sid}").raise_for_status()
    for table in ("stream_questions", "meme_moments", "meme_options"):
        assert store.db.query_one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table


def test_module_off_blocks_routes_and_hides_tools(tmp_path, monkeypatch):
    from app import agent_tools, modules

    client, store, source = _source(tmp_path, monkeypatch)
    modules.set_modules(store, {"stream_questions": False})
    try:
        assert client.get(f"/api/sources/{source['id']}/stream-questions").status_code == 423
        names = {t.name for t in agent_tools.list_tools(include_discovery=True, store=store)}
        assert "add_stream_questions" not in names
    finally:
        modules.set_modules(store, {"stream_questions": True})
