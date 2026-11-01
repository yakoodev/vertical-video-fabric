"""Линт качества перед сдачей (app/quality_lint.py, MCP lint_clip): зоны интерфейса площадок 9:16,
темп чтения титров и субтитров, вспышки и луп готового рендера (синтетика ffmpeg lavfi)."""

from __future__ import annotations

import subprocess

import pytest

from app import motion
from app import quality_lint as Q
from app.safe_zones import PLATFORM_UI, platform_ui
from app.timebase import ClipClock


def _text(lid, text, x=0.5, y=0.5, size=90, start=0.0, end=3.0, **props):
    return {"id": lid, "type": "text", "start": start, "end": end, "x": x, "y": y,
            "props": {"text": text, "size": size, **props}}


def _by(findings, check=None, layer=None, zone=None):
    return [f for f in findings if (check is None or f["check"] == check) and (layer is None or f.get("layer") == layer)
            and (zone is None or f.get("zone") == zone)]


# ---------------------------------------------------------------- площадки


def test_platform_table_and_aliases():
    assert set(PLATFORM_UI) == {"tiktok", "reels", "shorts"}
    # Подпись Reels выше, чем у TikTok и Shorts.
    assert PLATFORM_UI["reels"]["bottom"]["y0"] < PLATFORM_UI["tiktok"]["bottom"]["y0"]
    assert PLATFORM_UI["reels"]["bottom"]["y0"] < PLATFORM_UI["shorts"]["bottom"]["y0"]
    assert list(platform_ui(["youtube", "instagram"])) == ["shorts", "reels"]
    assert list(platform_ui("tiktok,мусор")) == ["tiktok"]
    assert list(platform_ui(None)) == ["tiktok", "reels", "shorts"]


def test_wide_title_under_button_column_suggests_max_width():
    layers = motion.normalize_layers([_text("wide", "ОЧЕНЬ ДЛИННЫЙ ЗАГОЛОВОК ТУТ", y=0.6, maxWidth=1000)])
    found = _by(Q.ui_zone_findings(layers), "ui_zones", "wide", "right")
    assert len(found) == 1, found
    f = found[0]
    assert f["severity"] == "warn" and set(f["platforms"]) == {"tiktok", "reels", "shorts"}
    assert f["overlap_px"]["tiktok"] > 40 and "maxWidth" in f["fix"] and "TikTok" in f["message"]
    # Сузили, как советует линт, — чисто.
    width = int(f["fix"].split("≤ ")[1].split(" ")[0])
    fixed = motion.normalize_layers([_text("wide", "ОЧЕНЬ ДЛИННЫЙ ЗАГОЛОВОК ТУТ", y=0.6, maxWidth=width)])
    assert not _by(Q.ui_zone_findings(fixed), "ui_zones", "wide", "right")


def test_reels_bottom_zone_is_higher_than_tiktok():
    layers = motion.normalize_layers([_text("low", "ПОДПИСЬ", y=0.77, size=80)])
    found = _by(Q.ui_zone_findings(layers), "ui_zones", "low", "bottom")
    assert len(found) == 1 and found[0]["platforms"] == ["reels"], found
    new_y = float(found[0]["fix"].split("y ≤ ")[1])
    moved = motion.normalize_layers([_text("low", "ПОДПИСЬ", y=new_y, size=80)])
    assert not Q.ui_zone_findings(moved)
    # Только TikTok — подпись Reels не мешает.
    assert not Q.ui_zone_findings(layers, platforms=["tiktok"])


def test_centered_title_nameplate_emoji_and_sticker():
    layers = motion.normalize_layers([
        _text("ok", "НОРМ", y=0.4),
        {"id": "np", "type": "nameplate", "start": 0, "end": 6, "x": 0.9, "y": 0.6,
         "props": {"handle": "sareamura", "platform": "twitch"}},
        {"id": "em", "type": "emoji", "start": 0, "end": 2, "x": 0.95, "y": 0.7, "props": {"char": "😂", "size": 160}},
        {"id": "st", "type": "image", "start": 0, "end": 2, "x": 0.94, "y": 0.75,
         "props": {"src": "sticker:pack/cat", "w": 220}},
    ])
    found = Q.ui_zone_findings(layers)
    assert not _by(found, layer="ok")
    for lid in ("np", "em", "st"):
        hit = _by(found, "ui_zones", lid, "right")
        assert hit and "сдвинь левее: x ≤" in hit[0]["fix"], (lid, found)


def test_keys_move_layer_into_column_at_some_time():
    layer = _text("fly", "ЕДЕТ", x=0.5, y=0.6, start=0, end=4)
    layer["keys"] = {"x": [{"t": 0, "v": 0.5}, {"t": 2, "v": 0.95}, {"t": 4, "v": 0.95}]}
    found = _by(Q.ui_zone_findings(motion.normalize_layers([layer])), "ui_zones", "fly", "right")
    assert found and found[0]["t"] >= 1.0, found


def test_captions_layer_pages_and_burned_subtitles():
    words = [{"t0": i * 0.4, "t1": i * 0.4 + 0.3, "w": f"слово{i}"} for i in range(9)]
    pages = Q.caption_layer_pages(words, {"style": "hormozi"}, 0, 10)
    assert pages and all(p["t1"] > p["t0"] for p in pages)
    assert sum(len(p["words"]) for p in pages) == 9 and max(len(p["words"]) for p in pages) <= 3
    # Оригинал перевода (s: orig) — только в слое, где он назван явно.
    orig = [{**w, "s": "orig"} for w in words]
    assert not Q.caption_layer_pages(orig, {"style": "hormozi"})
    assert Q.caption_layer_pages(orig, {"style": "minimal", "speakers": ["orig"]})

    low = motion.normalize_layers([{"id": "cap", "type": "captions", "start": 0, "end": 10, "x": 0.5, "y": 0.86,
                                    "props": {"style": "hormozi"}}])
    found = Q.ui_zone_findings(low, caption_pages={"cap": pages})
    assert _by(found, "ui_zones", "cap", "bottom"), found
    high = motion.normalize_layers([{**low[0], "y": 0.6}])
    assert not _by(Q.ui_zone_findings(high, caption_pages={"cap": pages}), "ui_zones", "cap", "bottom")

    ass = {"pages": Q.ass_pages(words, {"font_size": 64, "max_words_per_line": 4}), "bottom_pct": 12, "align": 2}
    burned = [f for f in Q.ui_zone_findings([], ass_block=ass) if f.get("zone") == "bottom"]
    assert burned and "position_pct" in burned[0]["fix"], burned
    raised = {**ass, "bottom_pct": 24}
    assert not [f for f in Q.ui_zone_findings([], ass_block=raised) if f.get("zone") == "bottom"]


# ---------------------------------------------------------------- темп чтения


def test_title_too_short_to_read_and_cut_inside():
    layers = motion.normalize_layers([
        _text("quick", "ОДИН ДВА ТРИ ЧЕТЫРЕ", start=1, end=1.8),
        _text("fine", "ДВА СЛОВА", start=3, end=5),
    ])
    found = Q.title_reading_findings(layers)
    assert [f["layer"] for f in found] == ["quick"]
    assert found[0]["need_sec"] == pytest.approx(1.7) and "end ≥" in found[0]["fix"]
    # Вырез съедает титр: 2 с на экране превращаются в 0.8 с.
    seg = [{"id": 1, "start_sec": 0, "end_sec": 10}]
    clock = ClipClock(seg, None, [{"start": 3.2, "end": 4.4}])
    cut = Q.title_reading_findings(layers, clock)
    assert {f["layer"] for f in cut} == {"quick", "fine"}
    assert "вырез" in next(f for f in cut if f["layer"] == "fine")["fix"]


def test_subtitle_pages_flash_fast_and_speed_piece():
    fast = [{"t0": i * 0.22, "t1": i * 0.22 + 0.2, "w": f"слово{i}"} for i in range(12)]
    pages = Q.caption_layer_pages(fast, {"style": "hormozi"}, 0, 20)
    found = Q.page_reading_findings(pages, "субтитры")
    assert any(f["severity"] == "warn" for f in found), found
    # Спокойная речь — без предупреждений.
    calm = [{"t0": i * 0.6, "t1": i * 0.6 + 0.4, "w": "да"} for i in range(6)]
    calm_pages = Q.caption_layer_pages(calm, {"style": "hormozi", "maxWords": 1}, 0, 20)
    assert not [f for f in Q.page_reading_findings(calm_pages, "субтитры") if f["severity"] == "warn"]
    # Кусок ×2: находка называет его скорость.
    clock = ClipClock([{"id": 1, "start_sec": 0, "end_sec": 20, "speed": 2.0}])
    sped = Q.page_reading_findings(pages, "субтитры", clock)
    assert any("×2" in f["message"] for f in sped), sped


# ---------------------------------------------------------------- вспышки (ряды)


def test_transitions_and_flash_windows_on_series():
    square5 = [0.0 if (i // 3) % 2 else 1.0 for i in range(90)]  # 5 вспышек/с
    legs = Q.transitions(square5, 0.1, 0.8)
    assert len(legs) >= 28
    windows = Q.flash_windows(legs, 30)
    assert windows and windows[0][2] >= 4
    square2 = [0.0 if (i // 8) % 2 else 1.0 for i in range(90)]  # ~2 вспышки/с
    assert not Q.flash_windows(Q.transitions(square2, 0.1, 0.8), 30)
    # Перепад между двумя светлыми кадрами — не вспышка; мелкая дрожь — тоже.
    bright = [0.85 if i % 2 else 0.99 for i in range(90)]
    assert not Q.transitions(bright, 0.1, 0.8)
    assert not Q.transitions([0.5 + 0.02 * (i % 2) for i in range(90)], 0.1, 0.8)


# ---------------------------------------------------------------- медиа (ffmpeg lavfi)


def _ffmpeg(tmp_path, name, src, vf=None, audio=None):
    from test_api_pipeline import _require_ffmpeg

    _require_ffmpeg()
    out = tmp_path / name
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", src]
    if audio:
        cmd += ["-f", "lavfi", "-i", audio, "-shortest", "-c:a", "aac"]
    if vf:
        cmd += ["-vf", vf]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


def test_flashes_in_rendered_file(tmp_path):
    strobe = _ffmpeg(tmp_path, "strobe.mp4", "color=white:s=64x64:r=30:d=3",
                     "geq=lum='if(lt(mod(N,6),3),235,16)':cb=128:cr=128")
    red = _ffmpeg(tmp_path, "red.mp4", "color=black:s=64x64:r=30:d=3",
                  "format=rgb24,geq=r='if(lt(mod(N,6),3),255,0)':g=0:b=0")
    calm = _ffmpeg(tmp_path, "calm.mp4", "testsrc=s=64x64:r=30:d=3")
    slow = _ffmpeg(tmp_path, "slow.mp4", "color=white:s=64x64:r=30:d=3",
                   "geq=lum='if(lt(mod(N,20),10),235,16)':cb=128:cr=128")

    got = Q.analyze_flashes(strobe)
    assert got["general"] and got["general"][0]["start"] < 0.5 and got["general"][0]["end"] > 2.5
    assert got["max_per_sec"]["general"] >= 4 and not got["red"]
    findings = Q.flash_findings(got)
    assert findings and findings[0]["severity"] == "warn" and "3 в секунду" in findings[0]["message"]
    assert Q.analyze_flashes(red)["red"]
    assert not Q.analyze_flashes(calm)["general"]
    assert not Q.analyze_flashes(slow)["general"]


def test_loop_score_good_and_bad(tmp_path):
    good = _ffmpeg(tmp_path, "good.mp4", "color=gray:s=64x64:r=30:d=2", audio="sine=f=440:d=2")
    bad = _ffmpeg(tmp_path, "bad.mp4", "color=white:s=64x64:r=30:d=2",
                  "geq=lum='if(lt(T,1),16,235)':cb=128:cr=128",
                  audio="aevalsrc='if(lt(t,1),0.5*sin(2*PI*440*t),0)':d=2")
    g = Q.loop_score(Q.loop_metrics(good))
    b_metrics = Q.loop_metrics(bad)
    b = Q.loop_score(b_metrics)
    assert g["score"] >= 0.9 and b["score"] <= 0.3, (g, b)
    items, info = Q.loop_findings(b_metrics, [])
    assert info["score"] == b["score"] and "подрежь" in items[0]["fix"] and "тишина" in items[0]["fix"]


# ---------------------------------------------------------------- клип, MCP, REST, автопилот


def test_lint_clip_plan_level_tool_and_rest(tmp_path, monkeypatch):
    from app.agent_tools import ToolContext, run_tool
    from app.chat_tools import BASE
    from test_clip_spec import _setup

    from test_api_pipeline import _require_ffmpeg

    _require_ffmpeg()
    client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    rs = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    rs["layers"] = [_text("wide", "ОЧЕНЬ ДЛИННЫЙ ЗАГОЛОВОК ТУТ", y=0.6, maxWidth=1000),
                    _text("quick", "ОДИН ДВА ТРИ ЧЕТЫРЕ", y=0.3, start=1, end=1.6)]
    rs["end_hold_sec"] = 0.5
    store.set_clip_plan_render_settings(plan["id"], rs)

    result = Q.lint_clip(store, plan["id"])
    assert result["ok"] and result["format"] == "9:16"
    assert _by(result["findings"], "ui_zones", "wide") and _by(result["findings"], "reading", "quick")
    assert "flashes" in result["skipped"] and "рендер" in result["skipped"]["flashes"]
    assert result["findings"][0]["severity"] == "warn"
    assert all("final_t" in f for f in result["findings"] if "t" in f)
    only = Q.lint_clip(store, plan["id"], checks=["reading"], media=False)
    assert only["checks"] == ["reading"] and {f["check"] for f in only["findings"]} == {"reading"}
    with pytest.raises(ValueError):
        Q.lint_clip(store, plan["id"], checks=["nope"])

    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "lint_clip", {"checks": ["ui_zones"]})
    assert ok and out["by_check"]["ui_zones"] >= 1 and "note" in out
    assert store.count_clip_plan_history(plan["id"]) == 0  # ничего не меняет
    assert "lint_clip" in BASE

    from app.settings import settings

    monkeypatch.setattr(settings, "render_loudnorm", True)  # стоп-кадр кладёт финальный проход звука
    body = client.get(f"/api/clip-plans/{plan['id']}/lint?checks=ui_zones,loop&platforms=tiktok").raise_for_status().json()
    assert body["platforms"] == ["tiktok"] and body["checks"] == ["ui_zones", "loop"]
    assert any(f["check"] == "loop" and "set_end_hold" in f.get("fix", "") for f in body["findings"])
    assert client.get(f"/api/clip-plans/{plan['id']}/lint?checks=bad").status_code == 400
    assert client.get("/api/clip-plans/999999/lint").status_code == 404


def test_lint_clip_uses_rendered_file(tmp_path, monkeypatch):
    from app.settings import settings
    from test_clip_spec import _setup

    client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    clip = store.create_clip(source["id"], clip_plan_id=plan["id"], status="queued")
    settings.clip_dir.mkdir(parents=True, exist_ok=True)
    video = _ffmpeg(settings.clip_dir, f"lint-{clip['id']}.mp4", "color=white:s=64x64:r=30:d=3",
                    "geq=lum='if(lt(mod(N,6),3),235,16)':cb=128:cr=128")
    store.finish_clip_render(clip["id"], "succeeded", output_path=video, duration_sec=3)
    result = Q.lint_clip(store, plan["id"], checks=["flashes", "loop"])
    assert result["render_id"] == clip["id"] and "flashes" not in result["skipped"]
    flashes = _by(result["findings"], "flashes")
    assert flashes and flashes[0]["final_t"] < 0.5
    assert result["loop"]["score"] is not None
    no_media = Q.lint_clip(store, plan["id"], checks=["flashes"], media=False)
    assert "flashes" in no_media["skipped"] and not no_media["findings"]
