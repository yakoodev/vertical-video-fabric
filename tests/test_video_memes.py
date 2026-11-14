"""«Видеомемы» (app/video_memes.py): загрузка с подрезкой и расшифровкой в фоне, поиск по смыслу, одобрение,
врезка в клип (full со своим звуком и выключенным звуком клипа, pip), отказ неодобренным; эффект кадра negate."""

from __future__ import annotations

import contextlib
import re
import subprocess
from pathlib import Path

import pytest

from app import video_meme_ingest as ING
from app import video_memes as VM
from app.agent_tools import ToolContext, run_tool
from app.montage_assets import build_inserts_args, normalize_inserts
from app.motion import FX_KINDS, normalize_layers, validate_layers
from test_api_pipeline import _client, _require_ffmpeg


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=120)


def _meme_video(path: Path, lead: float = 0.5, body: float = 1.2, tail: float = 0.6, color: str = "red") -> Path:
    """Чёрное и тишина ``lead`` с, потом цвет и тон ``body`` с, потом цвет и тишина ``tail`` с."""
    total = lead + body + tail
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=black:s=320x180:d={lead}:r=25",
        "-f", "lavfi", "-i", f"color=c={color}:s=320x180:d={body + tail}:r=25",
        "-f", "lavfi", "-i", f"sine=frequency=660:duration={body}:sample_rate=48000",
        "-filter_complex",
        f"[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a]adelay={int(lead * 1000)}|{int(lead * 1000)},apad,"
        f"atrim=0:{total}[a]",
        "-map", "[v]", "-map", "[a]", "-t", f"{total}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(path),
    )
    return path


@pytest.fixture
def env(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    heard: list[Path] = []

    def fake_whisper(path: Path) -> dict:
        heard.append(path)
        return {"text": "Вау, ну ты даёшь!", "language": "ru",
                "words": [{"word": "Вау,", "start": 0.0, "end": 0.4}]}

    monkeypatch.setattr(ING, "TRANSCRIBE", fake_whisper)
    monkeypatch.setattr(ING, "_spawn", lambda target, name: target())  # фон — сразу, без потока
    return client, store, heard, tmp_path


def _upload(client, path: Path, **form) -> dict:
    with path.open("rb") as fh:
        res = client.post("/api/video-memes", files={"file": (path.name, fh, "video/mp4")},
                          data={k: str(v) for k, v in form.items()})
    assert res.status_code == 200, res.text
    return res.json()


# ------------------------------------------------------------------ поля и поиск (без ffmpeg)


def test_tags_normalize_and_parse_edges():
    assert VM.normalize_tags("Шок, #фейл; шок\nТиммейт") == ["шок", "фейл", "тиммейт"]
    assert VM.normalize_tags(["Победа", "", "победа", "Ёлки"]) == ["победа", "елки"]
    assert VM.normalize_tags('["кринж"]') == ["кринж"]
    log = ("[blackdetect @ 0] black_start:0 black_end:0.48 black_duration:0.48\n"
           "[silencedetect @ 0] silence_start: 0\n[silencedetect @ 0] silence_end: 0.52 | silence_duration: 0.52\n"
           "[silencedetect @ 0] silence_start: 1.71\n")
    lead, end = ING.parse_edges(log, 2.3, has_audio=True)
    # Начало — по позднему из краёв (чёрное до 0.48, звук с 0.52 минус запас), конец — тишина + запас.
    assert lead == pytest.approx(max(0.48, 0.52 - ING.LEAD_PAD)) and end == pytest.approx(1.71 + ING.TAIL_PAD)
    assert ING.parse_edges(log, 2.3, has_audio=False) == (0.48, 2.3)  # без звука — только по чёрному
    # «Тишина» почти весь файл — это сам мем, не край: не режем.
    assert ING.parse_edges("silence_start: 0\nsilence_end: 1.9 | x\n", 2.0, has_audio=True) == (0.0, 2.0)


def test_score_prefers_tags_and_matches_word_forms():
    shock = {"title": "Чел в шоке", "tags": ["шок", "вау"], "when_to_use": "когда случилось невероятное",
             "description": "", "transcript": "oh my god"}
    pray = {"title": "Молится", "tags": ["молитва"], "when_to_use": "перед решающим выстрелом, последний шанс",
            "description": "складывает руки", "transcript": ""}
    assert VM.score(shock, "шоке") > 0 and VM.score(shock, "шок") > VM.score(pray, "шок")
    assert VM.score(pray, "выстрел") > 0 and VM.score(pray, "решающий выстрел") > VM.score(pray, "выстрел")
    assert VM.score(shock, "god") > 0  # по словам расшифровки
    assert VM.score(pray, "кринж") == 0


# ------------------------------------------------------------------ загрузка, правка, одобрение, удаление


def test_upload_trims_transcribes_edits_and_deletes(env):
    _require_ffmpeg()
    client, store, heard, tmp = env
    src = _meme_video(tmp / "wow.mp4")
    meme = _upload(client, src, title="Вау", tags="шок, вау", when_to_use="невероятный выстрел")
    # Чёрное и тишина в начале и тишина в конце срезаны (мем ≈ 1.2 с тона + запас), файл — mp4 со звуком.
    assert 1.1 <= meme["duration_sec"] <= 1.65, meme["duration_sec"]
    assert meme["trimmed"]["auto"]["lead"] > 0.3 and meme["trimmed"]["auto"]["tail"] > 0.2
    assert meme["has_audio"] and meme["approved"] is False and meme["has_real_people"] is True
    assert meme["license_note"] == VM.DEFAULT_LICENSE and "file_path" not in meme
    # Расшифровка «в фоне» (здесь сразу): текст, язык, слова.
    assert heard and meme["transcript_status"] == "done" and meme["transcript"] == "Вау, ну ты даёшь!"
    assert meme["transcript_lang"] == "ru" and meme["transcript_words"][0]["word"] == "Вау,"
    asset = store.get_montage_asset(meme["asset_id"])
    assert asset["kind"] == "video" and asset["project_id"] is None and "видеомем" in asset["tags"]
    assert "невероятный выстрел" in asset["description"] and "Вау, ну ты даёшь" in asset["description"]
    assert asset["people_check"]["real_person"] is True  # отметка владельца — без Gemini
    assert client.get(meme["url"]).status_code == 200

    # Без автоподрезки и с ручной: длина как задана.
    raw = _upload(client, src, auto_trim="false", trim_start="0.2", trim_end="1.4")
    assert raw["duration_sec"] == pytest.approx(1.2, abs=0.08) and "auto" not in (raw["trimmed"] or {})

    patched = client.patch(f"/api/video-memes/{meme['id']}", json={
        "title": "Вау!!", "description": "чел хватается за голову", "tags": ["Шок", "победа"],
        "has_real_people": False, "transcript": "вау", "approved": True}).json()
    assert patched["title"] == "Вау!!" and patched["tags"] == ["шок", "победа"] and patched["approved"]
    assert patched["transcript"] == "вау" and patched["transcript_words"] == []
    assert store.get_montage_asset(meme["asset_id"])["people_check"]["real_person"] is False
    assert client.patch(f"/api/video-memes/{meme['id']}", json={"bogus": 1}).status_code in (200, 422)

    listed = client.get("/api/video-memes", params={"approved": "true"}).json()
    assert [m["id"] for m in listed["memes"]] == [meme["id"]] and "шок" in listed["suggested_tags"]
    found = client.get("/api/video-memes", params={"q": "голову"}).json()["memes"]
    assert [m["id"] for m in found] == [meme["id"]]
    assert client.get("/api/video-memes", params={"q": "голову", "max_sec": 0.5}).json()["memes"] == []

    again = client.post(f"/api/video-memes/{meme['id']}/transcribe").json()
    assert again["transcript"] == "Вау, ну ты даёшь!"  # перерасшифровка заменила ручной текст

    assert client.delete(f"/api/video-memes/{meme['id']}").json() == {"deleted": True}
    assert client.get(f"/api/video-memes/{meme['id']}").status_code == 404
    with pytest.raises(KeyError):
        store.get_montage_asset(meme["asset_id"])
    # Удаление файла из «Файлов для монтажа» уносит и мем (каскад).
    client.delete(f"/api/montage-assets/{raw['asset_id']}")
    assert client.get(f"/api/video-memes/{raw['id']}").status_code == 404


def test_upload_rejects_long_or_silent_garbage(env):
    _require_ffmpeg()
    client, _store, _heard, tmp = env
    long_clip = _meme_video(tmp / "long.mp4", lead=0.0, body=9.5, tail=0.0)
    with long_clip.open("rb") as fh:
        res = client.post("/api/video-memes", files={"file": ("long.mp4", fh, "video/mp4")})
    assert res.status_code == 400 and "trim" in res.json()["detail"]
    junk = tmp / "junk.mp4"
    junk.write_bytes(b"not a video")
    with junk.open("rb") as fh:
        assert client.post("/api/video-memes", files={"file": ("junk.mp4", fh, "video/mp4")}).status_code == 400


def test_from_url_uses_service_downloader(env, monkeypatch):
    _require_ffmpeg()
    client, _store, _heard, tmp = env
    import app.video_import as video_import

    src = _meme_video(tmp / "dl.mp4", lead=0.0, tail=0.0)
    asked: list = []

    @contextlib.contextmanager
    def fake_section(url, start, end):
        asked.append((url, start, end))
        yield src, "Funny reactions", float(start), float(end)

    monkeypatch.setattr(video_import, "fetched_section", fake_section)
    res = client.post("/api/video-memes/from-url", json={"url": "https://youtu.be/x", "start": 3, "tags": "смех"})
    assert res.status_code == 200, res.text
    meme = res.json()
    assert asked == [("https://youtu.be/x", 3.0, 3.0 + ING.URL_DEFAULT_SEC)]
    assert meme["source_url"] == "https://youtu.be/x" and meme["tags"] == ["смех"]
    assert meme["title"].startswith("Funny reactions")


# ------------------------------------------------------------------ инструменты агента и рендер вставки


def test_agent_search_place_and_unapproved_refusal(env, monkeypatch):
    _require_ffmpeg()
    from test_clip_spec import _setup

    client, store, _source, plan, _a, _b = _setup(env[3] / "spec", monkeypatch)
    monkeypatch.setattr(ING, "TRANSCRIBE", lambda p: {"text": "no way", "language": "en", "words": []})
    monkeypatch.setattr(ING, "_spawn", lambda target, name: target())
    meme = _upload(client, _meme_video(env[3] / "noway.mp4"), title="No way", tags="шок",
                   when_to_use="тиммейт сделал невозможное")
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])

    out, ok = run_tool(ctx, "search_video_memes", {"query": "тиммейт"})
    assert ok and out["memes"] == [] and "ждут одобрения" in out["note"]
    out, ok = run_tool(ctx, "search_video_memes", {"query": "тиммейт", "include_unapproved": True})
    assert ok and out["memes"][0]["meme_id"] == meme["id"] and out["memes"][0]["approved"] is False
    out, ok = run_tool(ctx, "place_video_meme", {"meme_id": meme["id"], "at": 1.0})
    assert not ok and "не одобрил" in out["error"]
    out, ok = run_tool(ctx, "add_insert", {"asset_id": meme["asset_id"], "at": 1.0, "mode": "full"})
    assert not ok and "не одобрил" in out["error"]  # и в обход — тоже нет
    listed, _ = run_tool(ctx, "list_assets", {})
    assert meme["asset_id"] not in [a["asset_id"] for a in listed["assets"]]

    out, ok = run_tool(ctx, "describe_video_meme", {"meme_id": meme["id"], "description": "в шоке",
                                                    "tags": ["шок", "тиммейт"]})
    assert ok and out["meme"]["tags"] == ["шок", "тиммейт"]
    VM.update_meme(store, meme["id"], approved=True)  # одобряет владелец (API/страница)
    out, ok = run_tool(ctx, "search_video_memes", {"query": "тиммейт", "max_sec": 3})
    assert ok and out["memes"][0]["meme_id"] == meme["id"]

    out, ok = run_tool(ctx, "place_video_meme", {"meme_id": meme["id"], "at": 1.0})
    assert ok, out
    ins = out["inserts"][0]
    assert ins["asset_id"] == meme["asset_id"] and ins["mode"] == "full" and ins["duck"] is True
    assert ins["duck_volume"] == 0.0 and ins["level"] == "speech"
    assert ins["duration"] == pytest.approx(min(meme["duration_sec"], VM.MEME_FULL_MAX_SEC), abs=0.01)
    out, ok = run_tool(ctx, "place_video_meme", {"meme_id": meme["id"], "at": 3.0, "mode": "pip", "scale": 0.5})
    assert ok and out["inserts"][1]["mode"] == "pip" and out["inserts"][1]["duck_volume"] == 0.2
    out, ok = run_tool(ctx, "place_video_meme", {"meme_id": meme["id"], "at": 0.2, "game_audio": "keep",
                                                 "duration": 0.5})
    assert ok and out["inserts"][0]["duck"] is False and "duck_volume" not in out["inserts"][0]
    assert any("обрезан" in w for w in out.get("warnings", []))


def test_full_cut_in_replaces_picture_and_cuts_clip_audio(tmp_path):
    _require_ffmpeg()
    clip = tmp_path / "clip.mp4"
    _ffmpeg("-f", "lavfi", "-i", "color=c=blue:s=180x320:d=4:r=25", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=4:sample_rate=48000", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-t", "4", str(clip))
    meme = tmp_path / "meme.mp4"  # беззвучный красный: в окне врезки должна быть тишина и красный кадр
    _ffmpeg("-f", "lavfi", "-i", "color=c=red:s=320x180:d=1.5:r=25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(meme))
    assets = {1: {"kind": "video", "file_path": str(meme), "has_audio": False}}
    inserts = normalize_inserts([{"asset_id": 1, "at": 1.0, "duration": 1.5, "mode": "full", "duck": True,
                                  "duck_volume": 0, "level": "speech"}])
    assert inserts[0]["duck_volume"] == 0.0 and inserts[0]["level"] == "speech"
    out = tmp_path / "out.mp4"
    args = build_inserts_args(clip, out, inserts, assets, 180, 320, 4.0)
    graph = args[args.index("-filter_complex") + 1]
    assert "volume=enable='between(t,1.000,2.500)':volume=0" in graph
    subprocess.run(args, check=True, timeout=120)

    def frame_rgb(t: float) -> tuple[int, ...]:
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(out), "-frames:v", "1", "-vf",
                              "scale=4:4", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                             capture_output=True, check=True, timeout=60).stdout
        return tuple(sum(raw[i::3]) // 16 for i in range(3))

    def rms_db(start: float, length: float) -> float:
        err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-ss", f"{start}", "-t", f"{length}",
                              "-i", str(out), "-vn", "-af", "astats=metadata=0", "-f", "null", "-"],
                             capture_output=True, text=True, timeout=60).stderr
        m = re.findall(r"RMS level dB:\s*(-?[\d.]+|-inf)", err)
        return float("-inf") if not m or m[-1] == "-inf" else float(m[-1])

    r, g, b = frame_rgb(1.8)
    assert r > 180 and b < 80, (r, g, b)  # картинка — мем
    r, g, b = frame_rgb(0.5)
    assert b > 180 and r < 80, (r, g, b)  # до врезки — клип
    assert rms_db(1.2, 1.0) < -60  # звук клипа выключен на врезку
    assert rms_db(3.0, 0.8) > -30  # после — вернулся


def test_speech_level_puts_meme_voice_on_par_with_clip_voice(tmp_path):
    _require_ffmpeg()
    import math

    from app.montage_assets import _insert_volume
    from app.motion_render import audio_levels

    tone = tmp_path / "tone.wav"
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=500:duration=1:sample_rate=48000", "-af", "volume=0.5", str(tone))
    rms, peak = audio_levels(tone)
    ins = {"asset": {"file_path": str(tone)}, "volume": 1.0}
    speech = _insert_volume({**ins, "level": "speech"}, speech_db=-24.0)
    want = min(-24.0 - rms, -3.0 - peak)
    assert 20 * math.log10(speech) == pytest.approx(want, abs=0.05)
    assert speech > _insert_volume(ins, speech_db=-24.0)  # «эффект» — на 10 дБ тише голоса


def test_pip_duck_only_when_duck_volume_set_and_old_inserts_unchanged():
    assert "duck_volume" not in normalize_inserts([{"asset_id": 1, "at": 1, "mode": "pip"}])[0]
    assert normalize_inserts([{"asset_id": 1, "at": 1, "duck_volume": 7}])[0]["duck_volume"] == 1.0
    assert "level" not in normalize_inserts([{"asset_id": 1, "at": 1, "level": "loud"}])[0]


# ------------------------------------------------------------------ эффект кадра negate


def test_fx_negate_kind_and_aliases():
    assert "negate" in FX_KINDS
    for alias in ("invert", "inverted", "negative"):
        clean = normalize_layers([{"id": "n", "type": "fx", "start": 2, "end": 3,
                                   "props": {"kind": alias, "intensity": 1, "color": "#6fa8ff"}}])
        assert clean[0]["props"]["kind"] == "negate" and clean[0]["props"]["color"] == "#6fa8ff"
    ok = [{"id": "n", "type": "fx", "start": 2, "end": 3, "props": {"kind": "negate"},
           "out": {"type": "fade", "duration": 0.15}}]
    assert not [p for p in validate_layers(ok) if "negate" in p or "kind" in p]
    fx_ts = (Path(__file__).resolve().parents[1] / "frontend/src/motion/layers/fx.ts").read_text(encoding="utf-8")
    assert '"negate"' in fx_ts and "function negate(" in fx_ts
