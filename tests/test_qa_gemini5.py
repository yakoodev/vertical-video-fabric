"""QA 5 Gemini-монтажёра (клип 897, плейбук vtuber-story): находки и их починка.

1. Блокер: во 2-й сцене рамка аватара срезала правый край лица и глаз — рамку сузили ради чата, а крупность
   1.5 срезала её бока по центру. Инвариант: лицо с запасом всегда в КАДРЕ (рамка, крупность, set_layout руками).
2. На смене сцены ≈0.2 с полупрозрачного ютуба (наплыв) — стоп-кадр старой сцены до конца наплыва.
3. Эмфаза на заглушённом «х****» и на «Я» в панчлайне вместо «обосралась».
4. Стикер поверх субтитров; звёздочки раньше глушения после set_subtitle_words; стоп-кадр 0.5 с в конце.
5. «Проверил глазами — лицо в кадре» при срезанном лице — факт сверки отчёта.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from app.agent_tools import ToolContext, run_tool
from app.ai import avatar_box
from app.stream_layout import (
    avatar_auto_scale,
    avatar_freezes,
    avatar_geometry,
    avatar_visible_rect,
    face_cut_sides,
    fit_avatar_face,
    normalize_layout,
)
from test_cuts import _mock_render

REQUEST = ("Смонтируй по плейбуку vtuber-story: история витубера Муры — она думает, что по монитору ползёт "
           "огромный таракан; панчлайн «Я обосралась» в конце. Мат заглушить (censor_profanity).")

# ------------------------------------------------------------------ данные detect_avatar клипа 897 (кэш)

FACE_BIG = {"x0": 0.622, "y0": 0.383, "x1": 0.81, "y1": 0.56}
FACE_CORNER = {"x0": 0.793, "y0": 0.638, "x1": 0.902, "y1": 0.762}
BIG = {"x0": 0.49, "y0": 0.036, "x1": 0.91, "y1": 0.938, "face": FACE_BIG,
       "ui": [{"x0": 0.065, "y0": 0.536, "x1": 0.347, "y1": 0.582}, {"x0": 0.1, "y0": 0.608, "x1": 0.308, "y1": 0.71},
              {"x0": 0.161, "y0": 0.738, "x1": 0.347, "y1": 0.849}, {"x0": 0.083, "y0": 0.88, "x1": 0.284, "y1": 0.939}]}
CHAT = [{"x0": 0.852, "y0": 0.0, "x1": 0.999, "y1": 0.059}, {"x0": 0.871, "y0": 0.076, "x1": 0.999, "y1": 0.134},
        {"x0": 0.853, "y0": 0.151, "x1": 0.968, "y1": 0.21}, {"x0": 0.835, "y0": 0.226, "x1": 0.94, "y1": 0.285},
        {"x0": 0.806, "y0": 0.301, "x1": 0.999, "y1": 0.358}, {"x0": 0.835, "y0": 0.375, "x1": 0.952, "y1": 0.435},
        {"x0": 0.865, "y0": 0.448, "x1": 0.999, "y1": 0.509}, {"x0": 0.895, "y0": 0.523, "x1": 0.999, "y1": 0.582},
        {"x0": 0.923, "y0": 0.523, "x1": 0.97, "y1": 0.582}, {"x0": 0.083, "y0": 0.88, "x1": 0.284, "y1": 0.939}]
CORNER = {"x0": 0.707, "y0": 0.457, "x1": 0.951, "y1": 0.963, "face": FACE_CORNER, "ui": CHAT}
TIMES = [279.875, 285.225, 290.575, 295.925, 301.275, 306.625]
QA5_CAM = {"x": 0.6924, "y": 0.509, "w": 0.2306, "h": 0.4847}  # рамка 2-й сцены в QA 5 (w 0.231)
SRC_W, SRC_H = 1280, 720


def _inside(outer: dict, inner: dict, eps: float = 2e-3) -> bool:
    return (outer["x0"] <= inner["x0"] + eps and outer["y0"] <= inner["y0"] + eps
            and outer["x1"] >= inner["x1"] - eps and outer["y1"] >= inner["y1"] - eps)


def _cam_rect(cam: dict) -> dict:
    return {"x0": cam["x"], "y0": cam["y"], "x1": cam["x"] + cam["w"], "y1": cam["y"] + cam["h"]}


# ------------------------------------------------------------------ 1. лицо аватара целиком в кадре


def test_qa5_frame_really_cut_the_face():
    # Как было: рамка w 0.231 при крупности 1.504 — видна середина рамки 0.734–0.888, лицо до 0.902.
    scale = avatar_auto_scale(QA5_CAM, 1080, 1920, SRC_W, SRC_H)
    visible = avatar_visible_rect({"cam": QA5_CAM, "video_scale": scale}, 1080, 1920, SRC_W, SRC_H)
    assert scale == pytest.approx(1.504, abs=0.01)
    assert face_cut_sides(visible, FACE_CORNER) == ["right"]


def test_cam_always_keeps_face_zone_even_when_chat_stays():
    zone = avatar_box.face_zone(FACE_CORNER)
    # Запас: четверть ширины лица с боков, полвысоты сверху.
    assert zone["x1"] == pytest.approx(0.902 + 0.25 * 0.109, abs=1e-3)
    assert zone["y0"] == pytest.approx(0.638 - 0.5 * 0.124, abs=1e-3)
    out = avatar_box.place_avatar([BIG, CORNER, CORNER, CORNER, CORNER, CORNER], TIMES)
    big, corner = out["places"]
    for place in (big, corner):
        assert _inside(_cam_rect(place["cam"]), avatar_box.face_zone(place["face"]))
    # Окно чата у правого края модели не убрать без среза лица — оно остаётся и названо (а не режем лицо).
    assert corner["cam"]["x"] + corner["cam"]["w"] >= zone["x1"] - 1e-3
    assert any(w["x0"] >= 0.89 for w in corner["ui_left"])
    # trim_ui сам по себе тоже не заходит в зону лица.
    (_x0, _y0, x1, _y1), _sides = avatar_box.trim_ui((0.69, 0.42, 0.97, 0.99), CORNER, CHAT, FACE_CORNER)
    assert x1 >= zone["x1"] - 1e-6


def test_fit_avatar_face_keeps_face_visible_and_big():
    zone = avatar_box.face_zone(FACE_CORNER)
    corner = avatar_box.place_avatar([CORNER])["cam"]
    scale = avatar_auto_scale(corner, 1080, 1920, SRC_W, SRC_H)
    cam, new_scale, how = fit_avatar_face(corner, zone, scale, 1080, 1920, SRC_W, SRC_H)
    visible = avatar_visible_rect({"cam": cam, "video_scale": new_scale}, 1080, 1920, SRC_W, SRC_H)
    assert _inside(visible, zone) and how
    # Аватар по-прежнему крупный: почти на всю высоту кадра, а не марка посередине.
    assert avatar_geometry({"mode": "avatar_full", "cam": cam, "video_scale": new_scale}, 1080, 1920, SRC_W,
                           SRC_H)["h"] >= 0.85 * 1920
    # Рамка, что и так держит лицо, — без правок.
    big = avatar_box.place_avatar([BIG])["cam"]
    s = avatar_auto_scale(big, 1080, 1920, SRC_W, SRC_H)
    assert fit_avatar_face(big, avatar_box.face_zone(FACE_BIG), s, 1080, 1920, SRC_W, SRC_H) == (big, s, [])


def _avatar_plan(tmp_path, monkeypatch):
    """Клип 897 со сдвигом на начало исходника: кусок 0–32.1 с, кэш detect_avatar как у клипа."""
    store, _service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 32.1)])
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": preset["id"]})
    shift = 277.2
    found = avatar_box.place_avatar([BIG, CORNER, CORNER, CORNER, CORNER, CORNER], [t - shift for t in TIMES])
    places = found["places"]
    places[0]["src_from"], places[0]["src_to"] = 0.0, 5.008
    places[1]["src_from"], places[1]["src_to"] = 5.008, 32.1
    places[1]["from_shot_cut"] = True
    places[1]["transition"] = {"start": 4.875, "end": 5.142}
    cache = {**found, "places": places, "frames": 6, "times": [t - shift for t in TIMES],
             "per_frame": [BIG, CORNER, CORNER, CORNER, CORNER, CORNER]}
    full = store.get_clip_plan(plan["id"])
    avatar_box._cache_path(plan["id"], full).write_text(json.dumps(cache), encoding="utf-8")
    return store, plan


def _visible(layout: dict, cam: dict, scale: float) -> dict:
    return avatar_visible_rect({**layout, "cam": cam, "video_scale": scale}, 1080, 1920, SRC_W, SRC_H)


def test_set_layout_avatar_full_keeps_face_in_frame_and_fixes_manual_cams(tmp_path, monkeypatch):
    from app.agent_tools import avatar_face_report

    store, plan = _avatar_plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full"})
    assert ok, out
    lay = out["layout"]
    assert len(lay["cams"]) == 2
    for entry, face in zip(lay["cams"], (FACE_BIG, FACE_CORNER)):
        scale = entry.get("video_scale") or lay["video_scale"]
        assert _inside(_visible(lay, entry["cam"], scale), avatar_box.face_zone(face)), entry
    assert all(r["face_whole"] for r in avatar_face_report(store, plan["id"]))

    # Агент задаёт рамки руками — как в QA 5 (узкая рамка 2-й сцены, крупность 1.504): сервис расширяет/центрирует
    # и говорит об этом, лицо в кадре.
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cams": [
        {"from_t": 0, "to_t": 5.0, "cam": lay["cams"][0]["cam"]},
        {"from_t": 5.0, "to_t": 32.1, "cam": QA5_CAM, "video_scale": 1.504}]})
    assert ok, out
    assert any("лицо аватара не помещалось" in w and "cams[1]" in w for w in out["warnings"]), out
    entry = out["layout"]["cams"][1]
    assert _inside(_visible(out["layout"], entry["cam"], entry.get("video_scale") or out["layout"]["video_scale"]),
                   avatar_box.face_zone(FACE_CORNER))
    assert all(r["face_whole"] for r in avatar_face_report(store, plan["id"]))


def test_face_cut_is_a_fact_for_lint_and_report(tmp_path, monkeypatch):
    from app import quality_lint as Q
    from app.ai import editor_agent

    store, plan = _avatar_plan(tmp_path, monkeypatch)
    # Раскладка клипа 897 как есть (сохранена до починки): рамка 2-й сцены режет лицо.
    rs = store.get_clip_plan(plan["id"])["render_settings"]
    store.set_clip_plan_render_settings(plan["id"], {**rs, "layout": {
        "mode": "avatar_full", "cam": QA5_CAM, "video_scale": 1.504, "cams": [
            {"from": 0.0, "to": 5.008, "cam": {"x": 0.4648, "y": 0.0, "w": 0.4704, "h": 0.9921}, "video_scale": 1.499},
            {"from": 5.008, "to": 32.1, "cam": QA5_CAM, "video_scale": 1.504}]}})
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    facts = editor_agent.report_facts(ctx)
    assert facts["avatar_face_whole"] == "нет" and facts["avatar_face_cut"][0]["cut"] == ["правый край"]
    assert facts["avatar_face_cut"][0]["from_t"] == pytest.approx(5.01, abs=0.02)
    lint = Q.lint_clip(store, plan["id"], media=False)
    assert any("лицо аватара срезано" in f["message"] for f in lint["findings"] if f["severity"] == "warn")
    # Отчёт агента QA 5 — неправда про лицо ловится сверкой.
    report = "Lint пройден. Проверил глазами — лицо в кадре, текст читается отлично."
    gaps = editor_agent.report_fact_gaps(report, facts)
    assert any("лицо аватара НЕ целиком" in g and "правый край" in g for g in gaps), gaps
    honest = "Во 2-й сцене правый край лица срезан краем кадра — рамку не успел поправить."
    assert not any("лицо аватара" in g for g in editor_agent.report_fact_gaps(honest, facts))


# ------------------------------------------------------------------ 2. наплыв смены сцены — стоп-кадр


def test_cams_from_places_freeze_dissolve_and_snap_manual_boundary():
    places = [{"src_from": 0.0, "src_to": 5.008, "cam": {"x": 0.1, "y": 0, "w": 0.4, "h": 1}},
              {"src_from": 5.008, "src_to": 32.1, "cam": {"x": 0.6, "y": 0.5, "w": 0.3, "h": 0.5},
               "transition": {"start": 4.875, "end": 5.142}}]
    cams = avatar_box.cams_from_places(places)
    assert cams[0]["to"] == cams[1]["from"] == pytest.approx(5.182) and cams[1]["freeze_from"] == pytest.approx(4.835)
    # Граница руками «на 5.0» — та же смена: стоп-кадр и граница на конце наплыва.
    manual = avatar_box.snap_holds([{"from": 0, "to": 5.0, "cam": places[0]["cam"]},
                                    {"from": 5.0, "to": 32.1, "cam": places[1]["cam"]}], places)
    assert manual[1]["from"] == pytest.approx(5.182) and manual[1]["freeze_from"] == pytest.approx(4.835)
    lay = normalize_layout({"mode": "avatar_full", "cam": places[1]["cam"], "cams": cams})
    assert lay["cams"][1]["freeze_from"] == pytest.approx(4.835)
    assert avatar_freezes(lay, 0.0, 32.1) == [{"t0": 4.835, "t1": 5.182}]
    assert avatar_freezes(lay, 4.9, 20.0) == []  # часть начинается внутри наплыва — кадра до него нет
    assert avatar_freezes(normalize_layout({"mode": "avatar_full", "cam": places[1]["cam"]}), 0, 10) == []


def test_dissolve_render_has_no_frame_of_the_other_scene(tmp_path):
    from test_api_pipeline import _require_ffmpeg
    from test_avatar_framing import _scene_change_source

    from app.render import _layout_filter, _preset_for_segment, build_ffmpeg_render_args
    from app.scene_cuts import transition_span

    _require_ffmpeg()
    video = tmp_path / "dissolve.mp4"
    _scene_change_source(video, 0.5)  # аватар белым слева → наплыв 0.5 с на 2.0 с → аватар справа
    left, right = {"x0": 0.0, "y0": 0.0, "x1": 0.5, "y1": 1.0}, {"x0": 0.5, "y0": 0.0, "x1": 1.0, "y1": 1.0}
    places = avatar_box.place_avatar([left, right], [1.0, 3.2])["places"]
    places = avatar_box.settle_transitions(places, lambda lo, hi, hint: transition_span(str(video), lo, hi, hint=hint))
    places[0]["src_from"], places[-1]["src_to"] = 0.0, 4.0
    assert places[1].get("transition")
    base = {"output_width": 180, "output_height": 320, "fps": 30, "scale_mode": "cover"}
    seg = {"start_sec": 0.5, "end_sec": 3.5}

    def foreign_frames(cams: list[dict]) -> tuple[int, list[float]]:
        lay = normalize_layout({"mode": "avatar_full", "cam": cams[0]["cam"], "cams": cams})
        preset = _preset_for_segment({**base, "layout": lay}, seg)
        out = tmp_path / f"out{len(list(tmp_path.iterdir()))}.mp4"
        subprocess.run(build_ffmpeg_render_args(video, out, 0.5, 3.5, preset), check=True, capture_output=True)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, check=True).stdout
        size = 180 * 320
        bad = []
        for k in range(len(raw) // size):
            row = raw[k * size + 160 * 180: k * size + 161 * 180]
            if min(row[40:140]) < 200:  # в рамке аватара (белого) — серое/тёмное: чужая сцена
                bad.append(round(k / 30, 3))
        return len(raw) // size, bad

    middle = [{"from": p["src_from"], "to": p["src_to"], "cam": p["cam"]} for p in places]
    n_old, bad_old = foreign_frames(middle)
    assert len(bad_old) >= 4  # граница на середине наплыва: полупрозрачная чужая сцена (QA 5)
    n_new, bad_new = foreign_frames(avatar_box.cams_from_places(places))
    assert bad_new == [] and n_new == n_old == 90  # ни одного чужого кадра, длина та же
    # Без наплыва граф ровно прежний.
    lay = normalize_layout({"mode": "avatar_full", "cam": right_cam(), "cams": middle})
    graph = _layout_filter(1080, 1920, 30, "cover", "center", lay, None, None, avatar_spans=[])
    assert "tpad" not in graph and "[0:v]crop" in graph


def right_cam() -> dict:
    return {"x": 0.5, "y": 0.0, "w": 0.5, "h": 1.0}


# ------------------------------------------------------------------ 3. эмфаза: не мат и не местоимение

QA5_LINES = [
    [("монитору", 12.97, 13.48), ("огромная", 13.57, 14.12), ("х****", 14.12, 14.65), ("какая-то", 14.71, 15.3),
     ("лезет.", 15.39, 15.94)],
    [("Я", 28.0, 29.03), ("обосралась,", 29.12, 30.07), ("п*****.", 30.52, 31.46)],
]
QA5_MUTE = [{"start": 14.272, "end": 14.66, "by": "censor", "level": 3, "style": "silence"},
            {"start": 30.6, "end": 31.5, "by": "censor", "level": 3, "style": "reverse"}]


def _qa5_plan(tmp_path, monkeypatch, layers=None):
    store, _service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, [(0, 32.1)])
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": preset["id"], "mute": QA5_MUTE,
                                                     **({"layers": layers} if layers else {})})
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": ws[0][1], "end": ws[-1][2], "text": " ".join(w for w, _a, _b in ws)} for ws in QA5_LINES],
        "words": [{"word": w, "start": a, "end": b} for ws in QA5_LINES for w, a, b in ws],
    })
    return store, plan


def _emph(store, plan) -> dict:
    return {w["word"]: w.get("emph") for w in store.get_clip_plan_subtitles(plan["id"])["words"] if w.get("emph")}


def test_emphasis_never_on_muted_word_and_pronoun_gives_way_to_key_word(tmp_path, monkeypatch):
    store, plan = _qa5_plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    # Как агент QA 5: «х****» и «Я».
    out, ok = run_tool(ctx, "emphasize_words", {"words": ["х****", "Я"]})
    assert ok, out
    assert _emph(store, plan) == {"обосралась,": 1}
    notes = " ".join(out["warnings"])
    assert "заглушённый мат" in notes and "«Я»" in notes and "«обосралась,»" in notes
    # Только мат — отказ (нечего выделять), чужое не выделено.
    out, ok = run_tool(ctx, "emphasize_words", {"words": ["п*****."], "clear": True})
    assert ok and _emph(store, plan) == {}
    # auto: в строке с матом — не мат; в панчлайне — слово владельца.
    out, ok = run_tool(ctx, "emphasize_words", {"auto": True, "clear": True})
    assert ok, out
    marked = _emph(store, plan)
    assert "х****" not in marked and "п*****." not in marked and "обосралась," in marked and "Я" not in marked


def test_censor_drops_emphasis_from_muted_words(tmp_path, monkeypatch):
    store, plan = _qa5_plan(tmp_path, monkeypatch)
    payload = store.get_clip_plan_subtitles(plan["id"])
    words = [{**w, "emph": 1} for w in payload["words"]]
    store.set_clip_plan_subtitles(plan["id"], {**payload, "words": words})
    marked = _emph(store, plan)
    assert "х****" not in marked and "п*****." not in marked and "огромная" in marked


# ------------------------------------------------------------------ 4. звёздочки — вместе с глушением


def test_masked_word_moves_under_its_mute_after_set_subtitle_words(tmp_path, monkeypatch):
    from app.censor import word_for_span
    from app.profanity import mute_range

    # Обратная функция тишины: слово, из которого её поставили.
    for level in (1, 2, 3):
        for start, end in ((30.52, 31.46), (5.0, 5.3)):
            a, b = mute_range(start, end, level)
            s, e = word_for_span(a, b, level)
            assert s == pytest.approx(start, abs=0.01) and e == pytest.approx(end, abs=0.01)
    store, plan = _qa5_plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    words = [{"word": w, "start": a, "end": b} for ws in QA5_LINES for w, a, b in ws]
    words[-2] = {"word": "обосралась,", "start": 29.12, "end": 30.07}
    words[-1] = {"word": "п*****.", "start": 30.07, "end": 30.73}  # как у агента QA 5: на полслова раньше тишины
    out, ok = run_tool(ctx, "set_subtitle_words", {"words": words})
    assert ok, out
    stored = store.get_clip_plan_subtitles(plan["id"])["words"]
    mat = next(w for w in stored if w["word"].startswith("п*"))
    assert mat["start"] == pytest.approx(30.52, abs=0.02) and mat["end"] == pytest.approx(31.46, abs=0.02)
    assert any("сдвинуты под свои отрезки глушения" in w for w in out.get("warnings") or []), out
    lines = store.get_clip_plan_subtitles(plan["id"])["lines"]
    assert lines[-1]["end"] >= mat["end"] - 1e-3 and lines[-1]["text"].endswith("п*****.")
    # Слово уже на своей тишине — не трогаем.
    out, ok = run_tool(ctx, "set_subtitle_words", {"words": [dict(w) for w in stored]})
    assert ok and not any("сдвинуты" in w for w in out.get("warnings") or [])


# ------------------------------------------------------------------ 4. стикер поверх субтитров


def test_sticker_over_captions_is_warned_with_a_fix():
    from app.quality_lint import image_card_findings

    caps = {"id": "auto_caps", "type": "captions", "start": 0.0, "end": 31.5, "track": 0, "x": 0.5, "y": 0.72,
            "props": {"style": "hormozi", "maxWords": 2}}
    sticker = {"id": "l_ede30d", "type": "image", "start": 14.12, "end": 16.12, "track": 2, "x": 0.5, "y": 0.5,
               "props": {"asset_id": "630", "w": 1080.0}}
    found = [f for f in image_card_findings(None, [caps, sticker], 31.5) if "закрывает субтитры" in f["message"]]
    assert found and found[0]["severity"] == "warn" and '"track": 3' in found[0]["fix"]
    # Субтитры выше картинкой (track) — не мешает.
    assert not [f for f in image_card_findings(None, [{**caps, "track": 3}, sticker], 31.5)
                if "закрывает субтитры" in f["message"]]
    # Карточка на полосе субтитров — подсказка места мимо них.
    card = {**sticker, "y": 0.72, "props": {"asset_id": "630", "w": 400.0}}
    msg = [f for f in image_card_findings(None, [caps, card], 31.5) if "закрывает субтитры" in f["message"]]
    assert msg and "x=" in msg[0]["fix"] and "y=" in msg[0]["fix"]
    # Мимо субтитров — молчим.
    top = {**card, "y": 0.3}
    assert not [f for f in image_card_findings(None, [caps, top], 31.5) if "закрывает субтитры" in f["message"]]


def test_add_layers_warns_sticker_over_captions(tmp_path, monkeypatch):
    caps = {"id": "auto_caps", "type": "captions", "start": 0.0, "end": 31.5, "track": 0, "x": 0.5, "y": 0.72,
            "props": {"style": "hormozi", "maxWords": 2}}
    from PIL import Image

    store, plan = _qa5_plan(tmp_path, monkeypatch, layers=[caps])
    path = tmp_path / "roach.png"
    Image.new("RGB", (64, 64), (90, 60, 30)).save(path)
    asset = store.create_montage_asset(kind="image", label="3d roach", file_path=str(path), width=64, height=64)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"type": "image", "start": 14.12, "end": 16.12, "track": 2, "x": 0.5, "y": 0.5,
         "props": {"asset_id": str(asset["id"]), "w": 1080}}]})
    assert ok, out
    assert any("закрывает субтитры" in w for w in out.get("warnings") or []), out


# ------------------------------------------------------------------ 4. стоп-кадр в конце при vtuber-story


def test_vtuber_story_has_no_end_hold_unless_asked(tmp_path, monkeypatch):
    from app.agent_tools import apply_playbook_defaults
    from app.auto_edit import playbook_rules

    assert playbook_rules("vtuber-story")["end_hold_sec"] == 0
    store, plan = _qa5_plan(tmp_path, monkeypatch)
    hold = lambda: store.get_clip_plan(plan["id"])["render_settings"].get("end_hold_sec")  # noqa: E731
    plain = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST)
    assert apply_playbook_defaults(plain) is False and hold() is None  # без плейбука — общее умолчание
    asked = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST + " В конце стоп-кадр на полсекунды.",
                        playbook="vtuber-story")
    assert apply_playbook_defaults(asked) is False and hold() is None
    ctx = ToolContext(store=store, clip_plan_id=plan["id"], request=REQUEST, playbook="vtuber-story")
    assert apply_playbook_defaults(ctx) is True and hold() == 0
    run_tool(ctx, "set_end_hold", {"sec": 0.8})
    assert apply_playbook_defaults(ctx) is False and hold() == 0.8  # агент задал явно — не трогаем
