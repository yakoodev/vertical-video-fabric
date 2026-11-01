"""QA 01.11 (vtuber-story, клип 889): правила плейбука главнее ремесла CRAFT, быстрые субтитры по
1–2 слова не «нарушают темп чтения», короткая вставка-иллюстрация может закрыть кадр."""

from __future__ import annotations

from app import quality_lint as Q
from app.agent_tools import FULL_INSERT_MAX_SEC, ToolContext, covering_problems, run_tool
from app.ai import editor_agent
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

# ------------------------------------------------------------------ плейбук главнее CRAFT


def test_active_playbook_from_request_or_get_playbook_step():
    msg = 'Смонтируй по плейбуку vtuber-story (get_playbook name="vtuber-story"): история витубера'
    assert editor_agent.active_playbook(msg) == "vtuber-story"
    assert editor_agent.active_playbook("сделай динамичнее") is None
    assert editor_agent.active_playbook("какая-то vtuber-story-2 штука") is None  # не другое имя
    steps = [{"tool": "get_playbook", "ok": True, "args": {"name": "meme-insert"}}]
    assert editor_agent.active_playbook("сделай динамичнее", steps) == "meme-insert"
    assert editor_agent.active_playbook(msg, [{**steps[0], "ok": False}]) == "vtuber-story"


def test_system_prompt_puts_playbook_above_craft_defaults():
    plain = editor_agent.system_prompt()
    assert "АКТИВНЫЙ ПЛЕЙБУК" not in plain
    prompt = editor_agent.system_prompt(playbook="vtuber-story")
    block = prompt.split("АКТИВНЫЙ ПЛЕЙБУК", 1)[1]
    assert "«vtuber-story»" in block and "КАК В ПЛЕЙБУКЕ" in block
    assert "## Чек-лист" in block and block.index("конец плейбука") < block.index("СПРАВКА ПО СЛОЯМ")
    assert "Активный плейбук задаёт хук иначе" in prompt  # оговорка у правила хука в CRAFT
    assert editor_agent.playbook_block("нет-такого") == ""


def test_run_agent_sends_playbook_in_system_instruction(tmp_path, monkeypatch):
    from test_editor_agent import ScriptedGemini

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    fake = ScriptedGemini([[{"text": "Плейбук прочитал, клип короткий."}]])
    editor_agent.run_agent(store, plan["id"], "что скажешь по плейбуку vtuber-story?", client=fake, model="m")
    system = fake.requests[0][1]["systemInstruction"]["parts"][0]["text"]
    assert "АКТИВНЫЙ ПЛЕЙБУК «vtuber-story»" in system


# ------------------------------------------------------------------ быстрые субтитры по 1–2 слова


def _page(t0, t1, text):
    return {"t0": t0, "t1": t1, "text": text, "words": text.split()}


def test_fast_two_word_captions_are_a_style_not_a_reading_problem():
    pages = [_page(1.41, 1.69, "что я"), _page(2.77, 3.09, "что у"), _page(3.09, 3.18, "меня тут")]
    normal = Q.page_reading_findings(pages, "субтитры", layer="auto_caps")
    assert sum(f["severity"] == "warn" for f in normal) == 3
    fast = Q.page_reading_findings(pages, "субтитры", layer="auto_caps", fast=True)
    warns = [f for f in fast if f["severity"] == "warn"]
    assert len(warns) == 1 and "мелькает 0.09" in warns[0]["message"]  # правда мелькнула — сказать
    assert any(f["severity"] == "info" and "слово за голосом" in f["message"] for f in fast)


def test_lint_clip_uses_fast_mode_for_max_words_two(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    words = [{"word": w, "start": 0.3 + i * 0.16, "end": 0.3 + i * 0.16 + 0.14}
             for i, w in enumerate("ребят все что я сейчас расскажу это пиздец какая история".split())]
    store.set_clip_plan_subtitles(plan["id"], {"words": words, "lines": [{"start": 0.3, "end": 3.6, "text": " ".join(
        w["word"] for w in words)}]})

    def lint(max_words):
        caps = {"id": "auto_caps", "type": "captions", "start": 0, "end": 10, "x": 0.5, "y": 0.6,
                "props": {"maxWords": max_words}}
        store.set_clip_plan_render_settings(plan["id"], {"layers": [caps]})
        return [f for f in Q.lint_clip(store, plan["id"], checks=["reading"], media=False)["findings"]
                if f["severity"] == "warn"]

    assert lint(3)  # обычные страницы по 3 слова за ~0.5 с — мелькают
    assert lint(2) == []


# ------------------------------------------------------------------ вставка на весь кадр ≤ 3 с


def test_short_full_frame_insert_is_allowed_long_one_suggests_full_insert():
    img = {"id": "insert_1", "type": "image", "start": 2.09, "end": 4.09, "x": 0.5, "y": 0.55,
           "props": {"asset_id": "565", "w": 1080}}
    assert covering_problems([img]) == []
    long = {**img, "end": 2.09 + FULL_INSERT_MAX_SEC + 1}
    [problem] = covering_problems([long])
    assert "mode: full" in problem and "≤ 3 с" in problem
    plate = {"id": "plate", "type": "shape", "start": 0, "end": 1, "props": {"kind": "rect", "w": 1080, "h": 1200,
                                                                          "fill": "#000000"}}
    assert covering_problems([plate])  # плашка-фигура — всё так же нельзя


def test_add_insert_full_mode_is_capped_at_three_seconds(tmp_path, monkeypatch):
    from pathlib import Path

    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    asset = store.create_montage_asset(kind="image", label="таракан", file_path=str(Path(__file__)), width=400,
                                       height=400)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "add_insert", {"asset_id": asset["id"], "mode": "full", "at": 1, "duration": 5})
    assert not ok and "не дольше 3 с" in out["error"]
    out, ok = run_tool(ctx, "add_insert", {"asset_id": asset["id"], "mode": "full", "at": 1, "duration": 2.5})
    assert ok and out["inserts"][0]["mode"] == "full"
