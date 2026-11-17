"""set_audio_chain target=voice на клипе с исходником: рендер не падает «Output … same as Input».

Пилот 17.11: клип без субтитров и музыки, только цепочка голоса (mix.voice_fx). Основа писалась сразу в
итоговый файл (``has_post`` не знал о проходе голоса), и проход голоса читал и писал один файл — ffmpeg
отказывался. Здесь настоящий ffmpeg на синтетическом клипе 0.5 с 64×64.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="нужен ffmpeg")


def _tiny_clip(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=gray:s=64x64:r=10:d=0.5",
         "-f", "lavfi", "-i", "sine=f=440:d=0.5:sample_rate=48000", "-shortest", "-c:v", "libx264",
         "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True, timeout=60)
    return path


class _Svc:
    """Ровно то, что нужно _finalize_render: папка клипов и хранилище (музыки нет)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.store = SimpleNamespace()

    def _clip_dir(self) -> Path:
        return self.folder


def test_voice_chain_only_does_not_overwrite_its_input(tmp_path, monkeypatch):
    from app import render_sound
    from app.render_sound import SoundRender

    monkeypatch.setattr(render_sound, "_resolve_music_settings", lambda _store, _preset: None)
    out = _tiny_clip(tmp_path / "r1.mp4")  # основа уже лежит на месте итогового файла (has_post = False)
    from app.audio_fx import normalize_chain

    preset = {"mix": {"voice_fx": normalize_chain([{"fx": "highpass", "f": 120}])}}
    svc = _Svc(tmp_path)
    final, temps = SoundRender._finalize_render(svc, 1, "r1", out, out, preset, None)
    assert final == out and out.is_file() and out.stat().st_size > 0
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True)
    assert sorted(probe.stdout.split()) == ["audio", "video"]
    for temp in temps:
        assert temp != out
    assert not any(p.name.endswith(".base.mp4") and p.is_file() and p not in temps for p in tmp_path.iterdir())


def test_no_passes_keeps_file_as_is(tmp_path, monkeypatch):
    from app import render_sound
    from app.render_sound import SoundRender

    monkeypatch.setattr(render_sound, "_resolve_music_settings", lambda _store, _preset: None)
    out = _tiny_clip(tmp_path / "r2.mp4")
    before = out.read_bytes()
    final, temps = SoundRender._finalize_render(_Svc(tmp_path), 2, "r2", out, out, {}, None)
    assert final == out and temps == [] and out.read_bytes() == before
