from pathlib import Path

from app import motion_render
from app.motion_render import effect_gain_db


def test_effect_sits_under_quiet_voice(monkeypatch):
    # Громкие места эффекта −12 дБ, пик −2.
    monkeypatch.setattr(motion_render, "audio_levels", lambda path, seconds=30.0: (-12.0, -2.0))
    # Без голоса — прежняя абсолютная цель −20.
    assert effect_gain_db("ding.ogg") == -8.0
    # Тихая запись голоса (громкие места −26): эффект на 10 дБ тише неё, а не −20.
    assert effect_gain_db("ding.ogg", speech_db=-26.0) == -24.0
    # Громкий голос не делает эффект громче прежней цели.
    assert effect_gain_db("ding.ogg", speech_db=-6.0) == -8.0


def test_music_sits_under_the_clip_voice(monkeypatch, tmp_path):
    from app.render import music_volume_for

    levels = {"voice.mp4": (-24.0, -14.0), "track.mp3": (-8.0, -1.0)}
    starts = []
    monkeypatch.setattr(motion_render, "audio_levels",
                        lambda path, seconds=30.0, start=0.0: starts.append(start) or levels.get(str(path)))
    music = {"track_path": "track.mp3", "volume": 0.5}
    # Голос −24, фон на 16 дБ тише: −40; трек −8 → поправка −32 дБ.
    assert abs(music_volume_for("voice.mp4", music) - 10 ** (-32 / 20)) < 1e-9
    # volume 1.0 — вдвое громче обычного фона (+6 дБ).
    assert abs(music_volume_for("voice.mp4", {**music, "volume": 1.0}) - 2 * 10 ** (-32 / 20)) < 1e-9
    assert music_volume_for("voice.mp4", {**music, "volume": 0}) == 0.0
    # Голоса не измерить — прежняя громкость дорожки.
    assert music_volume_for("silent.mp4", music) == 0.5
    # Смещение старта: громкость меряется по тому куску трека, что зазвучит.
    music_volume_for("voice.mp4", {**music, "start_sec": 12.5})
    assert starts[-1] == 12.5


def test_music_pack_becomes_tracks_once(tmp_path, monkeypatch):
    import json

    from app.default_pack import seed_music
    from app.settings import settings
    from test_clip_spec import _client

    _client_, store = _client(tmp_path, monkeypatch)
    packs = tmp_path / "packs"
    (packs / "music").mkdir(parents=True)
    (packs / "music" / "upbeat.mp3").write_bytes(b"ID3fake")
    (packs / "music" / "items.json").write_text(
        json.dumps([{"file": "upbeat.mp3", "name": "Бодрый фон", "duration": 59.05}]), encoding="utf-8"
    )
    monkeypatch.setattr(settings, "motion_packs_dir", packs, raising=False)
    before = len(store.list_audio_tracks())
    seed_music(store)
    seed_music(store)
    tracks = [t for t in store.list_audio_tracks() if t["label"] == "Фон: Бодрый фон"]
    assert len(store.list_audio_tracks()) == before + 1 and len(tracks) == 1
    assert tracks[0]["volume"] == 0.5


def test_music_from_url_downloads_audio_only(tmp_path, monkeypatch):
    import subprocess

    from app import music_import
    from app.settings import settings
    from test_clip_spec import _client

    _c, store = _client(tmp_path, monkeypatch)
    calls = []
    real_run = subprocess.run

    def fake_run(args, **kw):
        if args[0] != "yt-dlp":
            return real_run(args, **kw)  # ffprobe и прочее — настоящие
        calls.append(args)
        out = Path(str(args[args.index("-o") + 1]).replace("%(ext)s", "mp3"))
        real_run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2", str(out)], check=True)
        return subprocess.CompletedProcess(args, 0, "Cool Track\n", "")

    monkeypatch.setattr(music_import.subprocess, "run", fake_run)
    track = music_import.import_music_from_url(store, "https://www.youtube.com/watch?v=abc")
    assert track["label"] == "Cool Track" and track["volume"] == 0.5
    assert Path(track["file_path"]).parent == settings.audio_dir
    assert "--no-playlist" in calls[0] and "-x" in calls[0]
    try:
        music_import.import_music_from_url(store, "file:///etc/passwd")
    except ValueError:
        pass
    else:
        raise AssertionError("не http(s) — ошибка")
