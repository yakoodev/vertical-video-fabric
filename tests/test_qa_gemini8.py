"""QA 8 Gemini-монтажёра (клип 901, плейбук vtuber-story).

1. detect_avatar со схемой ответа: gemini-3.5-flash 6 раз подряд терял x1 и зацикливал число. Рамка теперь —
   вложенный объект с обязательными полями в порядке x0, y0, x1, y1; повторы — без схемы.
2. В отчёт протёк русский внутренний монолог-план перед «Готово!».
3. Плашка канала, поставленная раньше хука, выглядывала из-под него с 0 с — теперь выходит, когда хук уходит.
"""

from __future__ import annotations

import json

from app import agent_tools
from app.agent_tools import ToolContext, run_tool
from app.ai import avatar_box, editor_agent
from test_qa_gemini6 import CORNER, N, FakeAvatarGemini, _approx, _parts, _plan


def test_avatar_schema_nested_box_and_retries_without_schema():
    schema = avatar_box.response_schema()
    item = schema["properties"]["frames"]["items"]
    assert item["properties"]["box"]["required"] == ["x0", "y0", "x1", "y1"]
    assert item["properties"]["box"]["propertyOrdering"] == ["x0", "y0", "x1", "y1"]
    assert "x1" not in item["properties"]  # плоских необязательных полей больше нет
    broken = '{"frames": [{"frame": 1, "avatar": true, "box": {"x0": 0.71, "y0": 0.4600000000000000000000'
    good = json.dumps({"frames": [{"frame": i + 1, "avatar": True, "box": CORNER} for i in range(N)]})
    fake = FakeAvatarGemini([broken, good])
    boxes = avatar_box.ask_boxes(fake, _parts())
    assert all(_approx(b, CORNER) for b in boxes)
    assert "responseSchema" in fake.calls[0]["generationConfig"]
    assert "responseSchema" not in fake.calls[1]["generationConfig"]


def test_russian_planning_monologue_is_cut_before_report():
    leaked = ("- 1) Посмотрел картинки, аватар не найден. 3) Мне запрещено подбирать координаты наугад. "
              "5) Я должен отчитаться и задать вопрос. Отчитаюсь, как просили.\nГотово! Хук поставил, мат заглушил.")
    assert editor_agent._strip_leaks(leaked) == "Готово! Хук поставил, мат заглушил."
    # Обычный отчёт с вводной фразой без плана самому себе — не трогаем.
    normal = "Клип про таракана.\nГотово: хук на месте, субтитры по два слова."
    assert editor_agent._strip_leaks(normal) == normal


def test_nameplate_set_before_hook_moves_after_it(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "set_nameplate", {"platform": "twitch", "handle": "sareamura", "corner": "top-right"})
    assert ok, out
    out, ok = run_tool(ctx, "add_layers", {"layers": [
        {"type": "text", "start": 0, "end": 8.04, "y": 0.17, "props": {"text": "ТАРАКАН НА МОНИТОРЕ"}}]})
    assert ok, out
    assert agent_tools.nameplate_after_hook(ctx) is True
    layers = store.get_clip_plan(plan["id"])["render_settings"]["layers"]
    plate = next(l for l in layers if l["id"] == "brand_nameplate")
    assert abs(plate["start"] - 8.04) < 1e-6
    assert agent_tools.nameplate_after_hook(ctx) is False  # повтор ничего не меняет


def test_missing_thought_signature_gets_skip_signature():
    # QA 9 (клип 902): «Function call is missing a thought_signature» — подпись-заглушка первому вызову хода.
    contents = [
        {"role": "user", "parts": [{"text": "смонтируй"}]},
        {"role": "model", "parts": [{"functionCall": {"name": "get_clip", "args": {}}},
                                    {"functionCall": {"name": "lint_clip", "args": {}}}]},
        {"role": "model", "parts": [{"functionCall": {"name": "view_frame", "args": {}}, "thoughtSignature": "abc"}]},
    ]
    assert editor_agent.patch_thought_signatures(contents) is True
    first, second = contents[1]["parts"]
    assert first["thoughtSignature"] == editor_agent.SKIP_SIGNATURE and "thoughtSignature" not in second
    assert contents[2]["parts"][0]["thoughtSignature"] == "abc"
    assert editor_agent.patch_thought_signatures(contents) is False
