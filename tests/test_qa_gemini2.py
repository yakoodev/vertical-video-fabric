"""Повторный QA Gemini-монтажёра (02.11, клип 892, плейбук vtuber-story): находки, починенные в сервисе.

Аватар в нескольких местах и проверка взгляда — tests/test_avatar_framing.py и tests/test_editor_agent.py."""

from __future__ import annotations

from app.agent_tools import ToolContext, run_tool
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

# ------------------------------------------------------------------ 3) наезд-удар, а не медленный наезд


def test_slow_zoom_warns_and_punch_at_builds_a_hit(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    # Как у клипа 892: [{t:0, zoom:1.0}, {t:28, zoom:1.15}] — плавно через весь ролик.
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 0, "zoom": 1.0}, {"t": 8, "zoom": 1.15}]})
    assert ok, out
    slow = [w for w in out["warnings"] if "медленный наезд" in w]
    assert slow and "8 с" in slow[0] and "punch_at" in slow[0]
    # Короткий удар — без предупреждения.
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 7.8, "zoom": 1.0}, {"t": 8, "zoom": 1.15},
                                                     {"t": 8.8, "zoom": 1.15}, {"t": 9.1, "zoom": 1.0}]})
    assert ok and not [w for w in out["warnings"] if "медленный" in w]
    # Помощник punch_at: ключи удара сами, прежние ключи вне окна остаются.
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 1.0, "zoom": 1.05}], "punch_at": 6.0})
    assert ok, out
    zooms = [(k["t"], k["zoom"]) for k in out["camera"]]
    assert (1.0, 1.05) in zooms and (5.8, 1.0) in zooms and (6.0, 1.15) in zooms and (6.8, 1.15) in zooms
    assert zooms[-1] == (7.15, 1.0) and not [w for w in out["warnings"] if "медленный" in w]
    assert out["punch"]["at"] == [6.0]
    # Без keys — поверх текущей камеры; несколько ударов.
    out, ok = run_tool(ctx, "set_camera", {"punch_at": [2, 6], "punch_zoom": 1.2})
    assert ok and sum(1 for k in out["camera"] if k["zoom"] == 1.2) == 4
    _out, ok = run_tool(ctx, "set_camera", {"punch_at": 2, "punch_zoom": 3})
    assert not ok


# ------------------------------------------------------------------ 4) ч/б до конца клипа; 5) повтор вставки


def test_grayscale_to_the_end_warns_in_add_layers_and_lint(tmp_path, monkeypatch):
    from app import motion
    from app.quality_lint import lint_clip

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    # Клип 10 с; ч/б 6.7 с → конец (как 28.0–31.3 у клипа 892).
    bw = {"id": "bw", "type": "fx", "start": 6.7, "end": 10.0, "track": 1, "props": {"kind": "grayscale"}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [bw]})
    assert ok, out
    warning = next(w for w in out["warnings"] if "ч/б" in w)
    assert "до конца клипа" in warning and "t + 1.5–2 с" in warning
    lint = lint_clip(store, plan["id"], checks=["montage"], media=False)
    found = [f for f in lint["findings"] if f["check"] == "montage"]
    assert found and found[0]["severity"] == "warn" and found[0]["layer"] == "bw" and found[0]["t"] == 6.7
    # 1.7 с на панчлайн — норма; 3 с посреди клипа — подсказка.
    assert motion.fx_grayscale_long([{**bw, "start": 5.0, "end": 6.7}], 10.0) == []
    long_mid = motion.fx_grayscale_long([{**bw, "start": 2.0, "end": 5.0, "props": {"kind": "bw"}}], 10.0)
    assert long_mid and "дольше 2.5 с" in long_mid[0]
    assert motion.fx_grayscale_long([{**bw, "props": {"kind": "vhs"}}], 10.0) == []


def test_same_insert_twice_is_a_lint_warning(tmp_path, monkeypatch):
    from app.quality_lint import lint_clip

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    rs = store.get_clip_plan(plan["id"])["render_settings"] or {}
    store.set_clip_plan_render_settings(plan["id"], {**rs, "inserts": [
        {"asset_id": 7, "at": 2.0, "duration": 1.5, "mode": "full"},
        {"asset_id": 7, "at": 6.0, "duration": 2.0, "mode": "full"},
        {"asset_id": 8, "at": 8.0, "duration": 1.0, "mode": "sound"}]})
    lint = lint_clip(store, plan["id"], checks=["montage"], media=False)
    repeat = [f for f in lint["findings"] if "одна и та же вставка" in f["message"]]
    assert len(repeat) == 1 and "дважды" in repeat[0]["message"] and repeat[0]["t"] == 6.0
    assert lint["by_check"]["montage"] == 1 and "монтаж 1" in lint["summary"]


def _top_pixel(tmp_path, video, t):
    import subprocess

    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True, timeout=60).stdout
    i = (int(640 * 0.05) * 360 + 180) * 3
    return raw[i], raw[i + 1], raw[i + 2]


def test_full_insert_of_horizontal_gif_has_blurred_fill_not_black_bars(tmp_path):
    import subprocess

    from app.montage_assets import build_inserts_args, normalize_inserts

    _require_ffmpeg()
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=360x640:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    gif = tmp_path / "wide.gif"  # горизонтальная гифка 16:9
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=320x180:d=1:r=5", str(gif)],
                   check=True)
    assets = {1: {"kind": "gif", "file_path": str(gif), "has_audio": False}}
    tops = {}
    for fill in ("blur", "cover", "black"):
        inserts = normalize_inserts([{"asset_id": 1, "at": 0.5, "duration": 2.0, "mode": "full",
                                      **({"fill": fill} if fill != "blur" else {})}])
        assert inserts[0].get("fill") == (None if fill == "blur" else fill)  # по умолчанию — размытие, поле не пишем
        out = tmp_path / f"{fill}.mp4"
        subprocess.run(build_inserts_args(clip, out, inserts, assets, 360, 640, 3.0, clip_has_audio=False),
                       check=True, timeout=120)
        tops[fill] = _top_pixel(tmp_path, out, 1.5)
    assert tops["blur"][0] > 150 and tops["blur"][2] < 90   # сверху — размытая та же гифка, не поля
    assert tops["cover"][0] > 150                            # cover — гифка во весь кадр
    assert max(tops["black"]) < 40                           # black — прежние поля по просьбе


def test_add_insert_warns_when_the_same_file_is_inserted_again(tmp_path, monkeypatch):
    from pathlib import Path

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    asset = store.create_montage_asset(kind="image", label="таракан", file_path=str(Path(__file__)), width=480,
                                       height=270)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "add_insert", {"asset_id": asset["id"], "mode": "full", "at": 1, "duration": 1.5})
    assert ok and not [w for w in out.get("warnings") or [] if "уже вставлен" in w]
    out, ok = run_tool(ctx, "add_insert", {"asset_id": asset["id"], "mode": "full", "at": 6, "duration": 2,
                                           "fill": "cover"})
    assert ok and any("уже вставлен на 1 с" in w for w in out["warnings"])
    assert out["inserts"][1]["fill"] == "cover"


# ------------------------------------------------------------------ 7) «тишина» внутри выреза


def test_silence_hint_ignores_what_is_cut(tmp_path):
    import subprocess

    from app.audio_mix import analyze, silence_after_cuts

    # Клип 892: тишина 18.6–19.6 с целиком в вырезе 18.47–19.66 с — подсказки быть не должно.
    assert silence_after_cuts([(18.6, 19.6)], [{"start": 18.47, "end": 19.66}]) == []
    # Вырез съел середину: по краям 0.3 + 0.3 с — в ролике это 0.6 с тишины встык.
    assert silence_after_cuts([(5.0, 7.0)], [{"start": 5.3, "end": 6.7}], min_len=0.5) == [(5.0, 7.0)]
    assert silence_after_cuts([(5.0, 7.0)], [{"start": 5.3, "end": 6.7}], min_len=0.8) == []
    assert silence_after_cuts([(1.0, 2.0)], []) == [(1.0, 2.0)]

    _require_ffmpeg()
    wav = tmp_path / "voice.wav"  # 1.5 с звука, 1.5 с тишины, 1.5 с звука
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=300:duration=4.5:sample_rate=48000",
                    "-af", "volume=enable='between(t,1.5,3.0)':volume=0", "-ac", "2", str(wav)], check=True)
    model = {"duration": 4.5, "stems": {"source": wav}, "items": [], "music": None, "speechless": False, "rs": {}}
    hole = [v for v in analyze(model)["verdicts"] if v["check"] == "silence"]
    assert hole and "тишина в ролике" in hole[0]["message"]
    cut = analyze({**model, "rs": {"cuts": [{"start": 1.4, "end": 3.1}]}})
    assert not [v for v in cut["verdicts"] if v["check"] == "silence"] and "silence_gaps" not in cut


# ------------------------------------------------------------------ 8) мусор в хэштегах


def test_hashtags_keep_only_cyrillic_latin_digits_underscore():
    import json

    from app.ai.publish_meta import full_description, parse_metadata

    meta = parse_metadata(json.dumps({
        "title": "Таракан на мониторе",
        "description": "Мура и таракан в клипе #таракан皮 #стрим",
        "hashtags": ["#таракан皮", "#Shorts", "#витубер_мура", "#ёлка2", "#皮", "#🔥огонь"],
        "tiktok": {"title": "Таракан", "description": "смотри #таракан皮"}}, ensure_ascii=False))
    assert meta["hashtags"] == ["#таракан", "#shorts", "#витубер_мура", "#ёлка2", "#огонь"]
    assert meta["description"] == "Мура и таракан в клипе #таракан #стрим"
    assert meta["platforms"]["tiktok"]["description"] == "смотри #таракан"
    assert "皮" not in full_description(meta["description"], meta["hashtags"])
