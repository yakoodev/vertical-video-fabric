"""«Референсы» проекта (app/references.py): заведение файлом и по ссылке, разбор фоном (склейки, доли, палитра, лист
кадров), продолжение после перезапуска, файлы и их уборка, ручки API, инструменты агента и подсказка чату проекта."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app import projects as P
from app import references as R
from app import references_fetch as RF
from app.agent_tools import ImageResult, ToolContext, run_tool
from test_api_pipeline import _client, _require_ffmpeg

# Удар каждые 0.5 с (120 BPM): склейки на 1.5, 2.5 и 4.5 с попадают в долю.
BEAT = "aevalsrc='0.8*sin(2*PI*80*t)*exp(-20*mod(t\\,0.5))':d={d}:s=44100"


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=120)


def _video(path: Path) -> Path:
    """Вертикаль 6 с из четырёх планов (красный 1.5 с, синий 1 с, таблица 2 с, зелёный 1.5 с) под бит 120 BPM."""
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=360x640:d=1.5:r=30", "-f", "lavfi", "-i", "color=c=blue:s=360x640:d=1:r=30",
            "-f", "lavfi", "-i", "testsrc=s=360x640:d=2:r=30", "-f", "lavfi", "-i", "color=c=green:s=360x640:d=1.5:r=30",
            "-f", "lavfi", "-i", BEAT.format(d=6), "-filter_complex", "[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0[v]",
            "-map", "[v]", "-map", "4:a", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
    return path


def _image(path: Path) -> Path:
    _ffmpeg("-f", "lavfi", "-i", "color=c=0xff2040:s=320x240:d=1", "-frames:v", "1", str(path))
    return path


def _audio(path: Path) -> Path:
    _ffmpeg("-f", "lavfi", "-i", BEAT.format(d=8), str(path))
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    _require_ffmpeg()
    client, store = _client(tmp_path, monkeypatch)
    monkeypatch.setenv("REFERENCES_DIR", str(tmp_path / "refs"))
    project = P.create_project(store, "Канал")
    return client, store, int(project["id"]), tmp_path


def _upload(client, project_id: int, path: Path, **form) -> dict:
    with path.open("rb") as fh:
        res = client.post(f"/api/projects/{project_id}/references", files={"file": (path.name, fh, "application/octet-stream")},
                          data={k: str(v) for k, v in form.items()})
    assert res.status_code == 200, res.text
    return res.json()


# --------------------------------------------------------------------------- #
# Хранилище
# --------------------------------------------------------------------------- #


def test_store_crud_tags_pinned_and_order(env):
    _client_, store, pid, _tmp = env
    a = R.create_from_url(store, pid, "https://example.com/page", download=False, note="цвет как тут",
                          tags="Цвет, #подписи, цвет")
    assert a["kind"] == "link" and a["status"] == "queued" and a["tags"] == ["цвет", "подписи"]
    b = R.create_from_url(store, pid, "https://www.tiktok.com/@x/video/1", title="Темп", tags=["темп"])
    assert b["kind"] == "video" and b["download"] is True
    R.update_reference(store, a["id"], pinned=True, note="палитра и шрифт", tags=["цвет"])
    listed = R.list_references(store, pid)
    assert [r["id"] for r in listed] == [a["id"], b["id"]]  # главный сверху
    assert listed[0]["pinned"] and listed[0]["note"] == "палитра и шрифт"
    assert R.count_references(store, pid) == {"total": 2, "pinned": 1}
    assert [r["id"] for r in R.list_references(store, pid, pinned=True)] == [a["id"]]
    with pytest.raises(ValueError):
        R.create_from_url(store, pid, "ftp://x")
    with pytest.raises(KeyError):
        R.create_from_url(store, 9999, "https://example.com")
    R.ref_dir(b["id"], create=True).joinpath("media.mp4").write_bytes(b"x")
    R.delete_reference(store, b["id"])
    assert not R.ref_dir(b["id"]).exists()
    with pytest.raises(KeyError):
        R.get_reference(store, b["id"])


def test_deleting_project_removes_reference_files(env):
    _client_, store, pid, _tmp = env
    ref = R.create_from_url(store, pid, "https://example.com/a.jpg")
    folder = R.ref_dir(ref["id"], create=True)
    (folder / "media.jpg").write_bytes(b"x")
    stray = R.ref_dir(ref["id"] + 50, create=True)  # папка без строки (сбой посреди удаления)
    P.delete_project(store, pid)
    assert store.db.query_one("SELECT COUNT(*) AS n FROM project_references")["n"] == 0
    assert not folder.exists()
    assert R.clean_orphans(store) == 1 and not stray.exists()


# --------------------------------------------------------------------------- #
# Разбор
# --------------------------------------------------------------------------- #


def test_video_upload_is_analyzed_cuts_beats_sheet(env):
    client, store, pid, tmp = env
    ref = _upload(client, pid, _video(tmp / "ref.mp4"), title="Пример", note="темп и склейки в долю", tags="темп,склейки",
                  pinned="true")
    assert ref["status"] == "queued" and ref["kind"] == "video" and ref["pinned"] and ref["has_file"]
    assert R.process_pending(store) == 1
    got = client.get(f"/api/references/{ref['id']}").json()
    assert got["status"] == "ready", got["error"]
    a = got["analysis"]
    assert a["aspect"] == "9:16" and a["duration"] == pytest.approx(6.0, abs=0.1)
    assert a["shots"] == 4 and [round(t, 1) for t in a["cut_times"]] == [1.5, 2.5, 4.5]
    assert a["cuts_per_min"] == pytest.approx(30, abs=1)
    music = a["music"]
    assert music["clear"] and music["bpm"] == pytest.approx(120, abs=3)
    assert music["cuts_on_beat"] == 3 and music["cuts_on_beat_share"] == 1.0 and music["chance_share"] < 0.5
    assert a["hits"]["count"] >= 8 and a["pace"]["values"] and a["energy"]["values"]
    assert a["text"]["ocr"] is False and len(a["keyframes"]) >= 4 and a["palette"]
    assert "склеек/мин" in a["summary"] and "BPM" in a["summary"]
    assert {"file", "preview", "poster", "sheet"} <= set(got["urls"])
    sheet = client.get(got["urls"]["sheet"])
    assert sheet.status_code == 200 and sheet.headers["content-type"] == "image/jpeg"
    assert client.get(f"/media/references/{ref['id']}/k00.jpg").status_code == 200
    assert client.get(f"/media/references/{ref['id']}/../app.sqlite").status_code == 404
    assert client.get(f"/media/references/{ref['id']}/media.mp4").status_code == 404  # только по имени «file»


def test_image_and_audio_analysis(env):
    client, store, pid, tmp = env
    img = _upload(client, pid, _image(tmp / "frame.png"))
    snd = _upload(client, pid, _audio(tmp / "beat.wav"), note="музыка")
    assert img["kind"] == "image" and snd["kind"] == "audio"
    assert R.process_pending(store) == 2
    img = R.get_reference(store, img["id"])
    assert img["status"] == "ready" and img["width"] == 320 and img["aspect"] == "4:3"
    top = img["analysis"]["palette"][0]
    assert top["hex"].startswith("#f") and top["share"] > 0.9  # почти весь кадр — один красный
    snd = R.get_reference(store, snd["id"])
    assert snd["status"] == "ready" and snd["duration_sec"] == pytest.approx(8, abs=0.1)
    assert snd["analysis"]["music"]["bpm"] == pytest.approx(120, abs=3) and snd["analysis"]["energy"]["values"]


def test_not_media_is_refused(env):
    client, _store, pid, tmp = env
    junk = tmp / "notes.txt"
    junk.write_text("hello", encoding="utf-8")
    with junk.open("rb") as fh:
        res = client.post(f"/api/projects/{pid}/references", files={"file": ("notes.txt", fh, "text/plain")})
    assert res.status_code == 400 and "видео" in res.json()["detail"]


# --------------------------------------------------------------------------- #
# Ссылки
# --------------------------------------------------------------------------- #


def test_links_video_page_and_direct_image(env, monkeypatch):
    client, store, pid, tmp = env
    clip = _video(tmp / "src.mp4")
    png = _image(tmp / "pic.png").read_bytes()
    calls: list[str] = []

    def fake_ytdlp(url: str, folder: Path) -> tuple[Path, str]:
        calls.append(url)
        if "nothing" in url:
            raise RF.FetchError("видео не найдено")
        (folder / "dl").mkdir(parents=True, exist_ok=True)
        dest = folder / "dl" / "dl.mp4"
        shutil.copyfile(clip, dest)
        return dest, "Крутой эдит"

    def fake_get(url: str):
        if url.endswith(".png") or "og.png" in url:
            return "image/png", png, ""
        return "text/html", None, ('<html><head><title>Ignored</title><meta property="og:title" content="Статья про '
                                   'монтаж"><meta property="og:image" content="/og.png"></head></html>')

    monkeypatch.setattr(RF, "YTDLP", fake_ytdlp)
    monkeypatch.setattr(RF, "HTTP_GET", fake_get)
    res = client.post(f"/api/projects/{pid}/references/link",
                      json={"url": "https://www.tiktok.com/@a/video/1", "note": "переходы", "tags": ["переходы"]})
    assert res.status_code == 200, res.text
    tiktok = res.json()
    page = client.post(f"/api/projects/{pid}/references/link", json={"url": "https://blog.example.com/nothing"}).json()
    pic = client.post(f"/api/projects/{pid}/references/link", json={"url": "https://cdn.example.com/a/pic.png"}).json()
    assert pic["kind"] == "image"
    assert R.process_pending(store) == 3
    tiktok = R.get_reference(store, tiktok["id"])
    assert tiktok["status"] == "ready" and tiktok["title"] == "Крутой эдит" and tiktok["analysis"]["shots"] == 4
    assert not (R.ref_dir(tiktok["id"]) / "dl").exists()
    page = R.get_reference(store, page["id"])
    assert page["status"] == "ready" and page["kind"] == "link" and page["title"] == "Статья про монтаж"
    assert page["analysis"]["page"]["image"] == "https://blog.example.com/og.png"
    assert R.artifact_path(page, "poster") is not None  # картинка страницы — кадром карточки
    pic = R.get_reference(store, pic["id"])
    assert pic["status"] == "ready" and pic["kind"] == "image" and pic["width"] == 320
    assert calls == ["https://www.tiktok.com/@a/video/1", "https://blog.example.com/nothing"]


def test_failed_download_and_resume_after_restart(env, monkeypatch):
    client, store, pid, tmp = env
    clip = _video(tmp / "src.mp4")
    calls: list[str] = []

    def fail(url: str, folder: Path):
        calls.append(url)
        raise RF.FetchError("не скачалось: провайдер режет соединение")

    monkeypatch.setattr(RF, "YTDLP", fail)
    ref = R.create_from_url(store, pid, "https://youtu.be/abc")
    R.process_pending(store)
    failed = R.get_reference(store, ref["id"])
    assert failed["status"] == "failed" and "провайдер" in failed["error"]
    # Скачалось, но сервис перезапустили посреди разбора: второй раз не качаем.
    folder = R.ref_dir(ref["id"], create=True)
    shutil.copyfile(clip, folder / "media.mp4")
    R.set_fields(store, ref["id"], file_name="media.mp4", status="running", stage="analyze")
    assert R.recover(store) == 1 and R.get_reference(store, ref["id"])["status"] == "queued"
    R.process_pending(store)
    assert R.get_reference(store, ref["id"])["status"] == "ready" and calls == ["https://youtu.be/abc"]
    res = client.post(f"/api/references/{ref['id']}/analyze")
    assert res.status_code == 200 and res.json()["status"] == "queued"


def test_ytdlp_args_and_hosts():
    args = RF.ytdlp_args("https://www.tiktok.com/@a/video/1", Path("x/dl.%(ext)s"))
    assert "--no-playlist" in args and f"duration <=? {RF.MAX_VIDEO_SEC}" in args
    assert RF.is_video_host("https://vm.tiktok.com/abc") and RF.is_video_host("https://youtu.be/x")
    assert RF.is_video_host("https://ru.pinterest.com/pin/1") and not RF.is_video_host("https://example.com/a")


# --------------------------------------------------------------------------- #
# Агент
# --------------------------------------------------------------------------- #


def test_agent_tools_list_get_add_and_project_bound(env, monkeypatch):
    client, store, pid, tmp = env
    ref = _upload(client, pid, _video(tmp / "ref.mp4"), note="склейки в долю", pinned="true")
    R.process_pending(store)
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "references_list", {"project_id": pid}, validate=True)
    assert ok and out["count"] == 1 and out["references"][0]["pinned"] and "приёмов" in out["rule"]
    assert out["references"][0]["note"] == "склейки в долю" and out["references"][0]["cuts_per_min"]
    sheet, ok = run_tool(ctx, "reference_get", {"reference_id": ref["id"]}, validate=True)
    assert ok and isinstance(sheet, ImageResult) and sheet.mime_type == "image/jpeg"
    assert sheet.info["shots"] == 4 and sheet.info["music"]["cuts_on_beat"] == 3 and sheet.info["keyframes"]
    frame, ok = run_tool(ctx, "reference_get", {"reference_id": ref["id"], "keyframe": 1}, validate=True)
    assert ok and isinstance(frame, ImageResult) and frame.info["keyframe"] == 1
    numbers, ok = run_tool(ctx, "reference_get", {"reference_id": ref["id"], "image": False, "detail": True})
    assert ok and isinstance(numbers, dict) and "detail" in numbers and "beats" not in numbers
    added, ok = run_tool(ctx, "reference_add", {"project_id": pid, "url": "https://youtu.be/xyz", "note": "крючок"},
                         validate=True)
    assert ok and added["reference"]["status"] == "queued" and added["reference"]["note"] == "крючок"
    bad, ok = run_tool(ctx, "reference_add", {"project_id": pid, "path": str(tmp / "ref.mp4")})
    assert not ok and "папки данных" in bad["error"]

    # Чат другого проекта не видит чужие референсы.
    from app import chat_tools as CT

    other = P.create_project(store, "Другой")
    res, ok, _ = CT.run(store, "reference_get", {"reference_id": ref["id"]}, project_id=int(other["id"]))
    assert not ok and "из проекта" in res["error"]
    res, ok, _ = CT.run(store, "references_list", {"project_id": pid}, project_id=int(other["id"]))
    assert not ok
    res, ok, _ = CT.run(store, "references_list", {}, project_id=pid)
    assert ok and res["count"] == 2


def test_chat_group_playbooks_and_system_prompt(env):
    _client_, store, pid, _tmp = env
    from app import chat_tools as CT
    from app import project_chat as PC
    from app.project_chat_playbook import groups_for, preload_groups

    assert CT.group_of("references_list") == "references" and CT.group_of("reference_get") == "references"
    assert "references_list" not in CT.base_names()
    assert "references" in CT.preload_for("сделай как в референсе")
    for name in ("frag-movie", "amv", "music-video", "meme-edit", "explainer-channel", "vtuber-story", "handoff"):
        assert "references" in preload_groups(name), name
        assert "references" in groups_for(name), name
    assert PC._project_groups(store, pid) == ()
    ref = R.create_from_url(store, pid, "https://youtu.be/x", title="Эталон темпа", note="склейки на каждую долю",
                            tags=["темп"], pinned=True)
    assert PC._project_groups(store, pid) == ("references",)
    text = PC._system({"id": 1, "project_id": pid, "references": R.pinned_brief(store, pid),
                       "references_total": 1}, [])
    assert f"#{ref['id']} video «Эталон темпа» — что взять: склейки на каждую долю; темп" in text
    assert "только приёмы" in text and "references_list" in text
    assert "У проекта есть референсы" not in PC._system({"id": 1, "project_id": pid}, [])


def test_page_that_does_not_open_keeps_a_title_and_beat_clarity(env, monkeypatch):
    _client_, store, pid, _tmp = env
    from app import references_analyze as RA

    def blocked(url: str):
        raise RF.FetchError("не открылось: 403")

    monkeypatch.setattr(RF, "HTTP_GET", blocked)
    ref = R.create_from_url(store, pid, "https://en.wikipedia.org/wiki/Jump_cut", download=False)
    R.process_pending(store)
    ref = R.get_reference(store, ref["id"])
    assert ref["status"] == "ready" and ref["kind"] == "link" and ref["title"] == "Jump cut"
    # Атаки ровно в доли — ритм явный; атаки мимо долей — нет (речь, шум).
    beats = [i * 0.5 for i in range(40)]
    on = {"beats": beats, "duration": 20.0, "onsets": [(b, 0.9) for b in beats[1:]]}
    off = {"beats": beats, "duration": 20.0, "onsets": [(b + 0.21, 0.9) for b in beats[1:]]}
    assert RA.beat_clarity(on)[0] >= RA.CLEAR_LIFT > RA.beat_clarity(off)[0]
    pace = RA.pace_curve([1.0, 1.5, 9.0], 10.0)  # окна по 2 с: две склейки в первом — 60 в минуту
    assert pace["step"] == 2.0 and pace["values"] == [60.0, 0.0, 0.0, 0.0, 30.0]
