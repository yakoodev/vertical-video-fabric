"""libass видит шрифты сервиса (vendor/fonts): стиль субтитров с Oswald рисуется Oswald, а не DejaVu.

Пилот 17.11: ASS-стиль с Oswald в контейнере прожигался запасным DejaVu — фильтр ``ass`` не знал о
vendor/fonts (fontconfig видит только системные шрифты). Теперь рендер и кадр агента передают
``fontsdir``; проверяем и строку фильтра, и настоящий выбор шрифта libass (лог ``fontselect``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from app.render_exec import ass_filter, subtitle_fonts_dir
from app.settings import settings

ASS = """[Script Info]
ScriptType: v4.00+
PlayResX: 160
PlayResY: 120

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Oswald,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:01.00,Karaoke,,0,0,0,,ПРИВЕТ
"""


def test_filter_passes_service_fonts_dir():
    vf = ass_filter("abc.ass")
    assert vf.startswith("ass=abc.ass:fontsdir='")
    folder = Path(settings.motion_fonts_dir).resolve()
    assert (folder / "Oswald-Bold.ttf").is_file()
    assert folder.as_posix().replace(":", "\\:") in vf
    assert subtitle_fonts_dir("Oswald") == Path(settings.motion_fonts_dir)


def test_render_burn_args_use_fonts_dir(tmp_path):
    from app.render_subs import build_ffmpeg_burn_subtitle_args

    args = build_ffmpeg_burn_subtitle_args(tmp_path / "in.mp4", tmp_path / "out.mp4", "s.ass", {}, None)
    vf = args[args.index("-vf") + 1]
    assert vf.startswith("ass=s.ass:fontsdir=")


def test_user_font_family_uses_user_fonts_dir(tmp_path, monkeypatch):
    import json

    monkeypatch.setattr(settings, "user_fonts_dir", tmp_path, raising=False)
    (tmp_path / "fonts.json").write_text(json.dumps([{"family": "Моя Гарнитура", "file": "my.ttf"}]), encoding="utf-8")
    assert subtitle_fonts_dir("моя гарнитура") == tmp_path
    assert subtitle_fonts_dir("Oswald") == Path(settings.motion_fonts_dir)


def _libass_ffmpeg() -> bool:
    if not shutil.which("ffmpeg"):
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, errors="replace")
    return " ass " in out.stdout


@pytest.mark.skipif(not _libass_ffmpeg(), reason="нет ffmpeg с libass")
def test_libass_resolves_oswald_from_service_fonts(tmp_path):
    (tmp_path / "t.ass").write_text(ASS, encoding="utf-8")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "info", "-f", "lavfi", "-i", "color=c=black:s=160x120:d=0.1",
         "-vf", ass_filter("t.ass"), "-frames:v", "1", "-y", "o.png"],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert proc.returncode == 0, proc.stderr[-500:]
    picks = [line for line in proc.stderr.splitlines() if "fontselect" in line and "(Oswald" in line]
    assert picks, proc.stderr[-800:]
    chosen = picks[0].split("->", 1)[1]
    assert "Oswald" in chosen and "DejaVu" not in chosen, picks[0]
