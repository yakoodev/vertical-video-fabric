"""Инструменты ИИ-монтажёра: слои через app.motion, проблемы вместо падений, снимок истории."""

from __future__ import annotations

from app import agent_tools
from app.agent_tools import ImageResult, ToolContext, run_tool
from app.clip_history import snapshot, undo
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup


def _ctx(store, plan_id, label="ИИ: тест"):
    return ToolContext(store=store, clip_plan_id=plan_id, on_first_change=lambda: snapshot(store, plan_id, label))


TITLE = {
    "type": "text", "name": "Хук", "start": 0.0, "end": 2.0, "x": 0.5, "y": 0.25,
    "props": {"text": "ОН ЭТО СДЕЛАЛ", "size": 96, "color": "#FFFFFF", "stroke": {"color": "#000000", "width": 10}},
    "in": {"type": "pop", "duration": 0.3},
}


def test_registry_is_complete_and_json_serializable():
    names = {tool.name for tool in agent_tools.list_tools(include_discovery=True)}
    for required in ("get_clip", "view_frame", "add_layers", "update_layer", "remove_layers", "clear_layers",
                     "set_camera", "search_emoji", "list_packs", "search_pack", "search_web_images", "generate_subtitles", "separate_voices",
                     "add_image_from_url", "generate_image", "list_fonts", "set_subtitles", "set_transition",
                     "trim_piece", "add_insert", "list_projects", "list_clips"):
        assert required in names, required
    import json

    json.dumps([tool.public() for tool in agent_tools.list_tools(include_discovery=True)], ensure_ascii=False)
    assert "counter" in agent_tools.motion_reference()


def test_add_update_remove_layers_with_one_history_step(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])

    out, ok = run_tool(ctx, "add_layers", {"layers": [TITLE, {"type": "shape", "start": 3, "end": 4.5,
                                                              "x": 0.7, "y": 0.35, "props": {"kind": "star", "w": 200, "h": 200, "fill": "#FFD600"}}]})
    assert ok, out
    ids = [item["id"] for item in out["added"]]
    assert len(ids) == 2 and "титр «ОН ЭТО СДЕЛАЛ»" in out["added"][0]["summary"]

    out, ok = run_tool(ctx, "update_layer", {"id": ids[0], "patch": {"props": {"text": "НОВЫЙ ТЕКСТ"}, "y": 0.3}})
    assert ok, out
    assert out["layer"]["props"]["text"] == "НОВЫЙ ТЕКСТ" and out["layer"]["props"]["size"] == 96
    assert out["layer"]["y"] == 0.3

    out, ok = run_tool(ctx, "remove_layers", {"ids": [ids[1]]})
    assert ok and out["removed"] == 1

    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert [layer["id"] for layer in layers] == [ids[0]]
    # Один ход — один снимок: Ctrl+Z возвращает клип без слоёв целиком.
    assert store.count_clip_plan_history(plan["id"]) == 1
    _fresh, _changes, label = undo(store, plan["id"])
    assert label == "ИИ: тест"
    assert (store.get_clip_plan(plan["id"])["render_settings"] or {}).get("layers") in (None, [])


def test_invalid_layers_return_problems_and_change_nothing(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"type": "text", "start": 3, "end": 2, "props": {"text": "x"}},
        {"type": "hologram", "start": 1, "end": 2},
        {"type": "image", "start": 1, "end": 2, "props": {"asset_id": 99999}},
    ]})
    assert not ok
    assert len(out["problems"]) >= 3, out
    assert any("99999" in p for p in out["problems"])
    assert store.count_clip_plan_history(plan["id"]) == 0
    assert not (store.get_clip_plan(plan["id"])["render_settings"] or {}).get("layers")

    out, ok = run_tool(ctx, "update_layer", {"id": "nope", "patch": {"x": 0.1}})
    assert not ok and "nope" in out["error"]
    out, ok = run_tool(ctx, "no_such_tool", {})
    assert not ok


def test_craft_warnings_for_unsafe_zone_and_overload(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    low = {**TITLE, "y": 0.9}
    crowd = [{"type": "text", "start": 1, "end": 3, "x": 0.5, "y": 0.25 + i * 0.1,
              "props": {"text": f"СЛОВО {i}", "size": 80}} for i in range(3)]
    out, ok = run_tool(ctx, "add_layers", {"layers": [low, *crowd]})
    assert ok, out
    text = " ".join(out["warnings"])
    assert "нижние 20%" in text
    assert "перегруз" in text


def test_camera_validated_and_saved(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 2, "zoom": 1}, {"t": 2.2, "zoom": 1.3, "ease": "outCubic"}]})
    assert ok, out
    assert [k["zoom"] for k in store.get_clip_plan(plan["id"])["render_settings"]["camera"]] == [1, 1.3]
    out, ok = run_tool(ctx, "set_camera", {"keys": [{"t": 1, "zoom": 9}, {"zoom": 1}]})
    assert not ok and out["problems"]
    assert len(store.get_clip_plan(plan["id"])["render_settings"]["camera"]) == 2


def test_get_clip_shows_pieces_and_subtitles_in_clip_time(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {
        "lines": [{"start": 0.5, "end": 1.5, "text": "привет мир"}, {"start": 6.0, "end": 7.0, "text": "второй кусок"}],
        "words": [{"word": "привет", "start": 0.5, "end": 1.0}, {"word": "мир", "start": 1.0, "end": 1.5},
                  {"word": "второй", "start": 6.0, "end": 6.5}, {"word": "кусок", "start": 6.5, "end": 7.0}],
    })
    out, ok = run_tool(_ctx(store, plan["id"]), "get_clip", {})
    assert ok
    assert [p["clip_start"] for p in out["pieces"]] == [0.0, 5.0]
    assert out["duration"] == 10.0
    assert out["subtitles"]["lines"][0]["words"][1] == ["мир", 1.0, 1.5]
    assert out["layers"] == [] and out["camera"] == []


def test_view_frame_returns_an_image(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    result, ok = run_tool(_ctx(store, plan["id"]), "view_frame", {"t": 6.0})
    assert ok, result
    assert isinstance(result, ImageResult)
    assert result.mime_type.startswith("image/") and len(result.data) > 500
    assert result.info["kind"] in ("motion", "base")


def test_clip_settings_tools_trim_transition_subtitles(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {"lines": [{"start": 0.5, "end": 1.5, "text": "превет"}], "words": []})
    ctx = _ctx(store, plan["id"])

    out, ok = run_tool(ctx, "set_transition", {"type": "whip", "sfx": "whoosh"})
    assert ok and out["transition"]["type"] == "whip"
    out, ok = run_tool(ctx, "set_transition", {"type": "teleport"})
    assert not ok and out["problems"]

    out, ok = run_tool(ctx, "set_subtitles", {"on": True, "position_pct": 18})
    assert ok and out["subs_on"] is True and out["sub_pos_pct"] == 18

    out, ok = run_tool(ctx, "edit_subtitle_lines", {"edits": [{"i": 0, "text": "привет"}]})
    assert ok
    assert store.get_clip_plan_subtitles(plan["id"])["lines"][0]["text"] == "привет"

    out, ok = run_tool(ctx, "trim_piece", {"index": 0, "start_sec": 0.5, "end_sec": 5.5})
    assert ok, out
    seg = store.get_clip_plan(plan["id"])["segments"][0]
    assert (float(seg["start_sec"]), float(seg["end_sec"])) == (0.5, 5.5)
    out, ok = run_tool(ctx, "trim_piece", {"index": 0, "start_sec": 0, "end_sec": 1})
    assert not ok and out["problems"]
    assert store.count_clip_plan_history(plan["id"]) == 1


def test_discovery_tools(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store)
    out, ok = run_tool(ctx, "list_projects", {})
    assert ok and source["id"] in [p["id"] for p in out["projects"]]
    out, ok = run_tool(ctx, "list_clips", {"project_id": source["id"]})
    assert ok and plan["id"] in [c["clip_id"] for c in out["clips"]]
    out, ok = run_tool(ctx, "get_clip", {})
    assert not ok and "clip_id" in out["error"]


def test_emoji_and_pack_search_give_layer_refs():
    import pytest

    from app import motion_assets

    if not motion_assets.list_packs():
        pytest.skip("паки не скачаны (tools/fetch_packs.py)")
    ctx = ToolContext(store=None)
    out, ok = run_tool(ctx, "search_emoji", {"q": "огонь", "limit": 5})
    assert ok and any(e["char"] == "🔥" and e["ref"] == "emoji:1f525" for e in out["results"])
    out, ok = run_tool(ctx, "list_packs", {"kind": "sfx"})
    assert ok and all(p["kind"] == "sfx" for p in out["packs"])
    out, ok = run_tool(ctx, "search_pack", {"q": "", "kind": "sfx", "limit": 3})
    assert ok and out["results"] and all(r["ref"].startswith("sfx:") for r in out["results"])
    out, ok = run_tool(ctx, "search_pack", {"pack": "нет-такого"})
    assert not ok



def test_emoji_is_banned_for_the_agent(tmp_path, monkeypatch):
    """Владелец запретил эмодзи: слой emoji отбрасывается, символы из текста вычищаются."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {**TITLE, "props": {**TITLE["props"], "text": "ОН ЭТО СДЕЛАЛ 🔥"}},
        {"type": "emoji", "start": 3, "end": 4.5, "props": {"char": "🔥"}},
    ]})
    assert ok, out
    assert len(out["added"]) == 1
    assert any("эмодзи" in w for w in out["warnings"])
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert layers[0]["props"]["text"] == "ОН ЭТО СДЕЛАЛ"


def test_pip_insert_is_fitted_above_captions():
    """Гифка-окошко не ложится на субтитры: агент её не видит в view_frame."""
    from app.agent_tools import _fit_pip_insert

    render = {"layers": [{"id": "auto_caps", "type": "captions", "start": 0, "end": 40, "y": 0.365},
                         {"id": "auto_hook", "type": "text", "start": 0, "end": 3, "y": 0.13}]}
    entry = {"at": 35.5, "duration": 2.5, "x": 0.4, "y": 0.245, "scale": 0.6}
    note = _fit_pip_insert(render, entry, {"width": 498, "height": 278})
    assert note and entry["x"] == 0.5
    height = entry["scale"] * 1080 * 278 / 498 / 1920
    assert entry["y"] + height / 2 <= 0.365 - 0.075 + 1e-6
    assert entry["y"] - height / 2 >= 0.07 - 1e-6
    # Во время хука между ним и субтитрами места нет — мем выходит после хука.
    early = {"at": 1.0, "duration": 2.0, "y": 0.25, "scale": 0.6}
    note = _fit_pip_insert(render, early, {"width": 500, "height": 500})
    assert early["at"] == 3.0 and "после хука" in note
    h2 = early["scale"] * 1080 / 1920
    assert early["y"] - h2 / 2 >= 0.07 - 1e-6 and early["y"] + h2 / 2 <= 0.29 + 1e-6


def test_subtitle_gaps_find_pieces_with_speech_but_no_words():
    """Субтитры устарели после смены кусков: на куске с речью ни слова — агент должен узнать."""
    from app.agent_tools import subtitle_gaps

    class Store:
        def get_clip_plan_subtitles(self, _id):
            # слова только в третьем куске (время кусков подряд: 0–10, 10–20, 20–30)
            return {"words": [{"word": "привет", "start": 21.0, "end": 21.5}]}

        def get_source_transcript(self, _id):
            return [{"start": 101, "end": 105, "text": "тут говорят много слов"},
                    {"start": 221, "end": 222, "text": "привет"}]

    pieces = [{"source_start": 100, "source_end": 110}, {"source_start": 300, "source_end": 310},
              {"source_start": 220, "source_end": 230}]
    # Второй кусок без речи в исходнике — не дыра; первый — дыра.
    assert subtitle_gaps(Store(), {"id": 1, "source_id": 5}, pieces) == [0]


def test_add_image_from_url_drops_off_topic_picture(monkeypatch):
    """Агент скачал картинку мимо запроса — она удаляется, агент получает ошибку и берёт другую."""
    from app.agent_tools import ToolContext, run_tool

    deleted = []

    class Store:
        def delete_montage_asset(self, asset_id):
            deleted.append(asset_id)

    monkeypatch.setattr("app.ai.asset_generate.add_from_url",
                        lambda store, url, label="", description="": {"id": 5, "kind": "image", "label": label})
    monkeypatch.setattr("app.ai.asset_generate.image_matches", lambda asset, query, client=None: False)
    out, ok = run_tool(ToolContext(store=Store()), "add_image_from_url",
                       {"url": "https://x/belaz.jpg", "label": "Олег", "query": "Олег Монгол"})
    assert not ok and "не про" in out["error"] and deleted == [5]
    monkeypatch.setattr("app.ai.asset_generate.image_matches", lambda asset, query, client=None: True)
    out, ok = run_tool(ToolContext(store=Store()), "add_image_from_url", {"url": "https://x/oleg.jpg", "query": "Олег Монгол"})
    assert ok and out["asset_id"] == 5


def test_pip_insert_fit_counts_cuts_that_pull_it_onto_the_hook():
    """Вырезали начало: мем на 7.9 с оказывается в начале готового клипа, под хуком."""
    from app.agent_tools import _fit_pip_insert

    render = {"cuts": [{"start": 0.0, "end": 7.77}],
              "layers": [{"id": "auto_caps", "type": "captions", "start": 0, "end": 40, "y": 0.37},
                         {"id": "auto_hook", "type": "text", "start": 0, "end": 2.5, "y": 0.13}]}
    entry = {"at": 7.89, "duration": 2.7, "y": 0.175, "scale": 0.66}
    note = _fit_pip_insert(render, entry, {"width": 500, "height": 500})
    assert note and "после хука" in note
    assert abs(entry["at"] - (7.77 + 2.5)) < 0.02  # в исходном времени — сразу после хука готового клипа


def test_pip_insert_stays_above_active_punchline():
    from app.agent_tools import _fit_pip_insert

    render = {"layers": [{"id": "auto_caps", "type": "captions", "start": 0, "end": 40, "y": 0.37},
                         {"id": "p", "type": "text", "start": 10.0, "end": 12.0, "y": 0.28, "props": {"size": 150}}]}
    entry = {"at": 10.5, "duration": 2.0, "y": 0.175, "scale": 0.66}
    _fit_pip_insert(render, entry, {"width": 500, "height": 500})
    height = entry["scale"] * 1080 / 1920
    assert entry["y"] + height / 2 <= 0.28 - 150 * 0.65 / 1920 + 1e-6


def test_layer_on_word(tmp_path, monkeypatch):
    """«Поставь звук на слово»: at_word вместо секунд."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {
        "words": [{"word": "Пиздец,", "start": 1.0, "end": 1.4}, {"word": "Артас.", "start": 1.5, "end": 2.0},
                  {"word": "Артас!", "start": 6.0, "end": 6.4}],
        "lines": [{"start": 1.0, "end": 2.0, "text": "Пиздец, Артас."}, {"start": 6.0, "end": 6.4, "text": "Артас!"}],
        "duration": 10.0,
    })
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {**TITLE, "start": None, "end": None, "at_word": {"text": "артас", "nth": 2}, "duration": 1.0},
        {**TITLE, "at_word": {"line": 0, "word": 1}, "word_offset": -0.1},
    ]})
    assert ok, out
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    assert (layers[0]["start"], layers[0]["end"]) == (6.0, 7.0)
    assert (layers[1]["start"], layers[1]["end"]) == (1.4, 3.4)  # длина прежняя: 2 с
    out, ok = run_tool(ctx, "add_layers", {"layers": [{**TITLE, "at_word": {"text": "нет такого"}}]})
    assert not ok and "нет в субтитрах" in str(out)


def test_listen_maps_finished_range_to_source(tmp_path, monkeypatch):
    """listen: последние 6 с готового клипа (после вырезов) → нужные секунды исходника."""
    _require_ffmpeg()
    from app.subtitles.contracts import SubtitleResult, SubtitleWord

    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)  # куски [0,5] и [1,6]
    render = dict(store.get_clip_plan(plan["id"])["render_settings"] or {})
    store.set_clip_plan_render_settings(plan["id"], {**render, "cuts": [{"start": 6.0, "end": 7.0}]})
    seen = {}
    monkeypatch.setattr("app.render._run_ffmpeg", lambda args, timeout=0: seen.setdefault("args", args))

    class Whisper:
        def transcribe(self, *_a):
            return SubtitleResult(text="да", language="ru", duration=1.0, segments=[],
                                  words=[SubtitleWord(word="да", start=0.5, end=0.9)])

    monkeypatch.setattr("app.clip_subtitles.get_subtitle_provider", lambda name: Whisper())
    out, ok = run_tool(_ctx(store, plan["id"]), "listen", {})
    assert ok, out
    assert out["clip_duration"] == 9.0 and (out["from"], out["to"]) == (3.0, 9.0)
    assert out["words"] == [["да", 3.5, 3.9]] and out["silence_after_last_word"] == 5.1
    seeks = [seen["args"][i + 1] for i, a in enumerate(seen["args"]) if a == "-ss"]
    assert seeks == ["3.000", "1.000", "3.000"]  # [3,5] первого, [1,2] и [3,6] второго куска (вырез 2..3)


def test_layer_until_word(tmp_path, monkeypatch):
    """until_word: титр держится ровно до следующей реплики, конец не считать руками."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {
        "words": [{"word": "окно", "start": 1.0, "end": 1.4}, {"word": "камин", "start": 3.5, "end": 4.0}],
        "lines": [{"start": 1.0, "end": 1.4, "text": "окно"}, {"start": 3.5, "end": 4.0, "text": "камин"}],
        "duration": 10.0,
    })
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [{**TITLE, "at_word": {"text": "окно"}, "until_word": {"text": "камин"}}]})
    assert ok, out
    layer = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]
    assert (layer["start"], layer["end"]) == (1.0, 3.5)
    out, ok = run_tool(ctx, "add_layers", {"layers": [{**TITLE, "at_word": {"text": "камин"}, "until_word": {"text": "окно"}}]})
    assert not ok and "раньше начала" in str(out)


def test_counter_prefix_suffix_keep_spaces(tmp_path, monkeypatch):
    """Агент: «от 50 000 ₽» рендерился как «от50 000₽» — фильтр эмодзи обрезал пробелы."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    out, ok = run_tool(ctx, "add_layers", {"layers": [{"type": "counter", "start": 0, "end": 2, "x": 0.5, "y": 0.3,
                                                       "props": {"from": 0, "to": 50000, "prefix": "от ", "suffix": " ₽"}}]})
    assert ok, out
    props = store.get_clip_plan(plan["id"])["render_settings"]["layers"][0]["props"]
    assert (props["prefix"], props["suffix"]) == ("от ", " ₽")


def test_big_opaque_panel_over_video_is_rejected(tmp_path, monkeypatch):
    """Владелец: «плашки перекрывают видео». Непрозрачная плашка больше 40 % кадра — отказ."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    panel = {"type": "shape", "start": 0, "end": 3, "x": 0.5, "y": 0.3,
             "props": {"kind": "rect", "w": 1080, "h": 1000, "fill": "#101010"}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [panel]})
    assert not ok and "закрывает" in str(out)
    frame = {**panel, "props": {**panel["props"], "fill": None, "stroke": {"color": "#FFFFFF", "width": 6}}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [frame]})
    assert ok, out  # рамка без заливки видео не закрывает


def test_find_noises_reports_sound_outside_speech(tmp_path, monkeypatch):
    """Звук там, где по субтитрам никто не говорит, — находка (вшитый эффект, шум)."""
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)  # тон 600 Гц на всём исходнике
    store.set_clip_plan_subtitles(plan["id"], {
        "words": [{"word": "раз", "start": 0.0, "end": 2.0}],
        "lines": [{"start": 0.0, "end": 2.0, "text": "раз"}], "duration": 10.0,
    })
    out, ok = run_tool(_ctx(store, plan["id"]), "find_noises", {"threshold_db": -32})
    assert ok, out
    assert out["noises"] and out["noises"][0]["start"] >= 2.0
    assert all(n["start"] >= 2.0 for n in out["noises"])


def test_set_subtitle_words_replaces_text_and_timing(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    out, ok = run_tool(_ctx(store, plan["id"]), "set_subtitle_words", {"words": [
        {"word": "Художник,", "start": 1.0, "end": 1.6}, {"word": "риггер,", "start": 1.7, "end": 2.2},
        {"word": "ожидание.", "start": 2.3, "end": 3.0}]})
    assert ok, out
    payload = store.get_clip_plan_subtitles(plan["id"])
    assert [w["word"] for w in payload["words"]] == ["Художник,", "риггер,", "ожидание."]
    assert payload["words"][0]["start"] == 1.0 and payload["lines"]


def test_upload_image_tool_takes_base64(tmp_path, monkeypatch):
    import base64
    import subprocess

    from app.agent_tools import ToolError, h_upload_image

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    png = tmp_path / "shot.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x32", "-frames:v", "1", str(png)],
                   check=True)
    ctx = _ctx(store, plan["id"])
    out = h_upload_image(ctx, {"data_base64": base64.b64encode(png.read_bytes()).decode(), "filename": "shot.png",
                               "label": "Скрин"})
    assert out["ok"] and out["width"] == 64 and out["height"] == 32
    assert store.get_montage_asset(out["asset_id"])["label"] == "Скрин"
    for bad in (base64.b64encode(b"not an image").decode(), "!!"):
        try:
            h_upload_image(ctx, {"data_base64": bad, "filename": "x.png"})
        except ToolError:
            continue
        raise AssertionError("ожидалась ошибка")


def test_clip_context_fills_project_id_and_auto_focus(tmp_path, monkeypatch):
    import sys
    import types

    from app.agent_tools import run_tool

    _client, store, source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = _ctx(store, plan["id"])
    seen = {}

    def fake_shots(_ctx, args):
        seen["project"] = args.get("project_id")
        return {"ok": True}

    monkeypatch.setattr(agent_tools.get_tool("get_shots"), "handler", fake_shots)
    _out, ok = run_tool(ctx, "get_shots", {})
    assert ok and seen["project"] == source["id"]  # проект — источник клипа, без «нужен project_id»

    # Настоящий app.autofocus тянет OpenCV (есть в контейнере, не в venv хоста) — подменяем модуль.
    fake = types.SimpleNamespace(compute_segment_focus=lambda src, a, b, vlm_resolver=None: [
        {"t": 0.0, "x": 0.3}, {"t": 2.0, "x": 0.7, "cut": True}])
    monkeypatch.setitem(sys.modules, "app.autofocus", fake)
    out, ok = run_tool(ctx, "auto_focus", {})
    assert ok and [p["points"] for p in out["pieces"]] == [2, 2]
    segment = store.get_clip_plan(plan["id"])["segments"][0]
    assert segment["focus"][1] == {"t": 2.0, "x": 0.7, "cut": True}
