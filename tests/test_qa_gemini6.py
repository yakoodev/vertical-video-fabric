"""QA 6 Gemini-монтажёра (клип 898, плейбук vtuber-story): регрессия кадрирования и её починка.

1. detect_avatar сказал «аватара нет», хотя Gemini его видел: ответ пришёл голым массивом в шкале 0–1000, разбор
   его не понял. Теперь разбор принимает все формы ответа, запрос — со схемой и одним повтором.
2. Сбой разбора/ответа ≠ «аватара нет»: агенту — разные ответы, при сбое без «задай cam руками».
3. Рамка руками на плеер YouTube — проверка кропа Gemini-зрением (кэш) и lint «аватар в кадре».
4. 12 set_layout подряд без прогресса — предел попыток: остановиться и спросить владельца.
5. Хвост «Седьмой стрель.» после панчлайна — оборванная фраза в конце клипа вырезается.
"""

from __future__ import annotations

import base64
import json

import pytest

from app import agent_tools
from app.agent_tools import ToolContext, run_tool
from app.ai import avatar_box, avatar_check, editor_agent
from app.settings import settings
from test_cuts import _mock_render

N = 6
# Мура крупно (1-я сцена) и в углу справа (2-я) — как в ответе Gemini из отчёта QA 6, доли кадра.
BIG = {"x0": 0.49, "y0": 0.04, "x1": 0.91, "y1": 0.94}
CORNER = {"x0": 0.71, "y0": 0.46, "x1": 0.95, "y1": 0.96}
FACE = {"x0": 0.79, "y0": 0.64, "x1": 0.9, "y1": 0.76}


def _k(box: dict, k: float = 1000.0) -> dict:
    return {key: round(v * k) for key, v in box.items()}


def _approx(box: dict | None, want: dict, eps: float = 2e-3) -> bool:
    return box is not None and all(abs(box[k] - want[k]) <= eps for k in ("x0", "y0", "x1", "y1"))


# ------------------------------------------------------------------ 1. разбор ответа Gemini


def test_parse_object_with_frames_in_fractions():
    text = json.dumps({"frames": [BIG, BIG, {}, CORNER, CORNER, CORNER]})
    boxes, ok = avatar_box.parse_frames(text, N)
    assert ok and _approx(boxes[0], BIG) and boxes[2] is None and _approx(boxes[5], CORNER)


def test_parse_bare_array_in_0_1000_scale_from_qa6():
    # 2-й ответ Gemini из отчёта QA 6: голый массив, «"x0": 490».
    items = [{**_k(BIG), "face": _k({"x0": 0.62, "y0": 0.38, "x1": 0.81, "y1": 0.56})}] * 2 \
        + [{**_k(CORNER), "face": _k(FACE), "ui": [_k({"x0": 0.85, "y0": 0.0, "x1": 1.0, "y1": 0.06})]}] * 4
    boxes, ok = avatar_box.parse_frames(json.dumps(items, indent=1), N)
    assert ok and all(boxes)
    assert _approx(boxes[0], BIG) and _approx(boxes[3], CORNER)
    assert _approx(boxes[3]["face"], FACE) and boxes[3]["ui"][0]["x1"] == 1.0
    found = avatar_box.place_avatar(boxes, [279.9, 285.2, 290.6, 295.9, 301.2, 306.6])
    assert found["cam"] is not None and found["place_count"] == 2  # раньше — «аватара не нашлось»
    assert avatar_box.parse_boxes(json.dumps(items), N) == boxes


def test_parse_box_2d_order_and_nested_wrappers():
    # box_2d — [ymin, xmin, ymax, xmax] в 0–1000 (норма Gemini).
    def b2d(box):
        return [round(box["y0"] * 1000), round(box["x0"] * 1000), round(box["y1"] * 1000), round(box["x1"] * 1000)]

    items = [{"frame": i + 1, "label": "avatar", "box_2d": b2d(CORNER), "face_2d": b2d(FACE)} for i in range(N)]
    for text in (json.dumps(items), json.dumps({"result": {"frames": items}}), json.dumps([{"frames": items}]),
                 json.dumps({"detections": items}), "```json\n" + json.dumps(items) + "\n```",
                 "Вот рамки:\n" + json.dumps({"frames": items}) + "\nГотово."):
        boxes, ok = avatar_box.parse_frames(text, N)
        assert ok and all(_approx(b, CORNER) for b in boxes), text[:60]
        assert _approx(boxes[0]["face"], FACE)
    # Вложенная рамка {"avatar": {...}} и bbox списком [x0, y0, x1, y1].
    nested = [{"avatar": BIG}, {"bbox": [BIG["x0"], BIG["y0"], BIG["x1"], BIG["y1"]]}]
    boxes, ok = avatar_box.parse_frames(json.dumps({"frames": nested}), 2)
    assert ok and _approx(boxes[0], BIG) and _approx(boxes[1], BIG)


def test_parse_pixels_and_frame_numbers_and_explicit_no_avatar():
    # Пиксели кадра 1280×720 (больше 1000) — по размеру кадра.
    px = {"x0": 0.71 * 1280, "y0": 0.46 * 720, "x1": 0.95 * 1280, "y1": 0.96 * 720}
    boxes, ok = avatar_box.parse_frames(json.dumps({"frames": [px, px]}), 2, size=(1280, 720))
    assert ok and _approx(boxes[0], CORNER)
    # Пиксели без размера кадра — не разобрать (а не «аватара нет»).
    assert avatar_box.parse_frames(json.dumps({"frames": [px, px]}), 2) == ([None, None], False)
    # Номера кадров не по порядку — по номеру; avatar: false — явное «нет» (разобран).
    shuffled = [{"frame": 3, "avatar": True, **CORNER}, {"frame": 1, "avatar": False}, {"frame": 2, "avatar": True, **BIG}]
    boxes, ok = avatar_box.parse_frames(json.dumps({"frames": shuffled}), 3)
    assert ok and boxes[0] is None and _approx(boxes[1], BIG) and _approx(boxes[2], CORNER)
    none, ok = avatar_box.parse_frames(json.dumps({"frames": [{"frame": i, "avatar": False} for i in (1, 2)]}), 2)
    assert ok and none == [None, None]
    # Одна рамка без списка (старый ответ) — на все кадры.
    boxes, ok = avatar_box.parse_frames(json.dumps(CORNER), 3)
    assert ok and all(_approx(b, CORNER) for b in boxes)


@pytest.mark.parametrize("text", ["", "   ", "не знаю", "{\"frames\": [{\"what\": \"Мура\"}]}", "[1, 2", "{\"x\": 1}"])
def test_parse_failure_is_not_no_avatar(text):
    boxes, ok = avatar_box.parse_frames(text, 3)
    assert boxes == [None] * 3 and ok is False


class FakeAvatarGemini:
    """Отвечает по очереди заготовленными текстами (последний — дальше по кругу)."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def generate_content(self, model, payload):
        self.calls.append(payload)
        text = self.texts[min(len(self.calls), len(self.texts)) - 1]
        if isinstance(text, Exception):
            raise text
        return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def _parts(n=N):
    return [{"inlineData": {"mimeType": "image/jpeg", "data": "AA=="}}] * n


def test_ask_boxes_uses_schema_and_retries_once_on_bad_answer():
    good = json.dumps([_k(CORNER)] * N)
    fake = FakeAvatarGemini(["[{\"x0\": 490}, {", good])
    boxes = avatar_box.ask_boxes(fake, _parts())
    assert len(fake.calls) == 2 and all(_approx(b, CORNER) for b in boxes)
    config = fake.calls[0]["generationConfig"]
    assert config["responseMimeType"] == "application/json" and config["temperature"] == 0
    schema = config["responseSchema"]
    assert schema["required"] == ["frames"] and "box" not in schema["properties"]
    assert set(schema["properties"]["frames"]["items"]["required"]) == {"frame", "avatar"}
    # Ответ сразу понятный — один вызов; явное «аватара нет» — тоже ответ, без повтора.
    once = FakeAvatarGemini([json.dumps({"frames": [{"frame": i + 1, "avatar": False} for i in range(N)]})])
    assert avatar_box.ask_boxes(once, _parts()) == [None] * N and len(once.calls) == 1


def test_ask_boxes_fails_loudly_after_retry():
    fake = FakeAvatarGemini(["", "мусор"])
    with pytest.raises(avatar_box.AvatarAnswerError, match=f"попыток {avatar_box.ASK_ATTEMPTS}"):
        avatar_box.ask_boxes(fake, _parts())
    assert len(fake.calls) == avatar_box.ASK_ATTEMPTS
    # Эндпоинт без responseSchema — повтор без схемы.
    old = FakeAvatarGemini([RuntimeError("Invalid JSON payload: unknown name responseSchema"), json.dumps([CORNER] * 2)])
    assert _approx(avatar_box.ask_boxes(old, _parts(2))[0], CORNER)
    assert "responseSchema" not in old.calls[1]["generationConfig"]


def test_sample_size_matches_grab():
    assert avatar_box.sample_size({"width": 1920, "height": 1080}) == (640, 360)
    assert avatar_box.sample_size({"width": 0}) is None


# ------------------------------------------------------------------ 2. detect_avatar: сбой ≠ «аватара нет»


def _plan(tmp_path, monkeypatch, pieces=((0, 32.1),)):
    store, _service, plan, preset, _calls = _mock_render(tmp_path, monkeypatch, list(pieces))
    store.set_clip_plan_render_settings(plan["id"], {"preset_id": preset["id"]})
    return store, plan


def test_detect_avatar_failure_is_service_error_not_no_avatar(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])

    def broken(*_a, **_k):
        raise avatar_box.AvatarAnswerError("ответ не разобран: '[{\"x0\": 490}' (попыток 2)")

    monkeypatch.setattr(avatar_box, "detect_avatar", broken)
    out, ok = run_tool(ctx, "detect_avatar", {})
    assert not ok and out["status"] == "service_failed"
    text = out["error"] + " ".join(out["problems"])
    assert "сбой" in text.lower() and "аватара нет" in text and "refresh" in text
    assert "задай cam руками" not in text and "НЕ угадывай" in text
    # avatar_full без рамки — тот же сбой, а не «не нашёл».
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full"})
    assert not ok and "сбой сервиса" in out["error"] and "не нашёл" not in out["error"]

    monkeypatch.setattr(avatar_box, "detect_avatar", lambda *_a, **_k: {"cam": None, "frames": 6, "places": []})
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "detect_avatar", {})
    assert ok and out["status"] == "no_avatar" and "Gemini ответил" in out["note"]


def test_detect_avatar_does_not_cache_a_failed_answer(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(avatar_box, "_grab", lambda path, t, out: b"jpeg")
    bad = FakeAvatarGemini(["", ""])
    with pytest.raises(avatar_box.AvatarAnswerError):
        avatar_box.detect_avatar(store, plan["id"], client=bad)
    assert not avatar_box._cache_path(plan["id"], store.get_clip_plan(plan["id"])).exists()
    good = FakeAvatarGemini([json.dumps([_k(CORNER)] * N)])
    found = avatar_box.detect_avatar(store, plan["id"], client=good)
    assert found["cam"] and found["frames_with_avatar"] == N


# ------------------------------------------------------------------ 3. рамка руками: аватар ли в ней


YOUTUBE_CAM = {"x": 0.125, "y": 0.6, "w": 0.25, "h": 0.4}  # рамка агента из QA 6: левый нижний угол — плеер YouTube
MURA_CAM = {"x": 0.69, "y": 0.3, "w": 0.31, "h": 0.7}


class FakeVisionCheck:
    """Gemini-зрение по кропу: «аватар» — если кроп правой половины кадра (x рамки ≥ 0.5)."""

    calls: list = []

    def __init__(self, *a, **k):
        pass

    def generate_content(self, model, payload):
        FakeVisionCheck.calls.append(payload)
        data = base64.b64decode(payload["contents"][0]["parts"][1]["inlineData"]["data"]).decode()
        x = float(data.split(":")[1])
        answer = {"avatar": True, "what": "аватар Муры"} if x >= 0.5 else {"avatar": False, "what": "плеер YouTube"}
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(answer, ensure_ascii=False)}]}}]}


def _vision(monkeypatch):
    FakeVisionCheck.calls = []
    monkeypatch.setattr(settings, "gemini_api_key", "test-key")
    monkeypatch.setattr("app.ai.gemini.GeminiClient", FakeVisionCheck)
    monkeypatch.setattr(avatar_check, "crop_frame", lambda path, t, cam: f"crop:{cam['x']}:{t}".encode())


def test_manual_cam_on_youtube_player_is_flagged_and_cached(tmp_path, monkeypatch):
    from app import quality_lint

    store, plan = _plan(tmp_path, monkeypatch)
    _vision(monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    cams = [{"from_t": 0, "to_t": 5.3, "cam": MURA_CAM}, {"from_t": 5.3, "to_t": 32.1, "cam": YOUTUBE_CAM}]
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cams": cams})
    assert ok, out
    assert out["avatar_miss"] is True
    verdicts = {round(c["cam"]["x"], 3): c["avatar"] for c in out["avatar_check"]}
    assert verdicts == {0.69: True, 0.125: False}
    warning = " ".join(out["warnings"])
    assert "аватара в кадре НЕТ" in warning and "плеер YouTube" in warning and "5.3–32.1 с" in warning
    # Крупно/по секунде — один кадр на место; повтор той же раскладки — из кэша, модель не зовём.
    assert len(FakeVisionCheck.calls) == 2
    run_tool(ctx, "set_layout", {"mode": "avatar_full", "cams": cams})
    assert len(FakeVisionCheck.calls) == 2
    # lint — по тому же кэшу.
    lint = quality_lint.lint_clip(store, plan["id"], media=False)
    absent = [f for f in lint["findings"] if f["severity"] == "warn" and "аватара в кадре нет" in f["message"]]
    assert len(absent) == 1 and absent[0]["t"] == 5.3 and "плеер YouTube" in absent[0]["message"]
    # Рамка на самого аватара на весь клип — без предупреждения.
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "set_layout",
                       {"mode": "avatar_full", "cam": MURA_CAM})
    assert ok and not out.get("avatar_miss") and all(c["avatar"] for c in out["avatar_check"])
    assert not [f for f in quality_lint.lint_clip(store, plan["id"], media=False)["findings"]
                if "аватара в кадре нет" in f["message"]]


def test_lint_avatar_in_frame_by_detect_avatar_places_without_gemini(tmp_path, monkeypatch):
    from app import quality_lint

    store, plan = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "gemini_api_key", "")
    found = avatar_box.place_avatar([BIG, CORNER, CORNER, CORNER, CORNER, CORNER], [2.7, 8.0, 13.4, 18.7, 24.1, 29.4])
    places = found["places"]
    places[0]["src_from"], places[0]["src_to"] = 0.0, 5.0
    places[1]["src_from"], places[1]["src_to"] = 5.0, 32.1
    full = store.get_clip_plan(plan["id"])
    avatar_box._cache_path(plan["id"], full).write_text(json.dumps({**found, "places": places}), encoding="utf-8")
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cams": [
        {"from_t": 0, "to_t": 5.0, "cam": {"x": 0.45, "y": 0.0, "w": 0.5, "h": 1.0}},
        {"from_t": 5.0, "to_t": 32.1, "cam": YOUTUBE_CAM}]})
    assert ok and out["avatar_miss"] and "ни одно место аватара" in " ".join(out["warnings"])
    assert all(c.get("skipped") for c in out["avatar_check"])  # без ключа — пометка, не отказ
    lint = quality_lint.lint_clip(store, plan["id"], media=False)
    absent = [f for f in lint["findings"] if "аватара в кадре нет" in f["message"]]
    assert len(absent) == 1 and absent[0]["severity"] == "warn" and absent[0]["t"] == 5.0
    # По местам detect_avatar (cams "auto") — аватар в кадре везде.
    run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "set_layout", {"mode": "avatar_full", "cams": "auto"})
    assert agent_tools.avatar_absent_spans(store, plan["id"]) == []


# ------------------------------------------------------------------ 4. предел попыток кадрирования


def test_framing_attempts_stop_and_ask_owner(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    _vision(monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])

    def broken(*_a, **_k):
        raise avatar_box.AvatarAnswerError("пустой ответ (попыток 2)")

    monkeypatch.setattr(avatar_box, "detect_avatar", broken)
    out, ok = run_tool(ctx, "detect_avatar", {})
    assert not ok and "stop_framing" not in out and ctx.framing_fails == 1
    # Удачная рамка (Gemini подтвердил аватар) — счёт неудач с нуля.
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cam": MURA_CAM})
    assert ok and ctx.framing_fails == 0
    for _ in range(2):
        out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cam": YOUTUBE_CAM})
        assert ok and out["avatar_miss"] and "stop_framing" not in out
    out, ok = run_tool(ctx, "detect_avatar", {"refresh": True})
    assert not ok and "3 неудачных попыток" in out["stop_framing"] and "вопрос" in out["stop_framing"]
    # Дальше рамки и detect_avatar не принимаются, остальное — работает.
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cam": {"x": 0.3, "y": 0.2, "w": 0.3, "h": 0.6}})
    assert not ok and out["stop_framing"] and "кадрирование остановлено" in out["error"]
    assert not run_tool(ctx, "detect_avatar", {})[1]
    assert run_tool(ctx, "get_clip", {})[1]


def test_unverified_manual_frames_limit(tmp_path, monkeypatch):
    store, plan = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "gemini_api_key", "")  # проверить рамку нечем — каждая «не подтверждена»
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    for i in range(agent_tools.FRAMING_MANUAL_LIMIT):
        out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cam": {"x": 0.5 + i * 0.01, "y": 0.3,
                                                                               "w": 0.3, "h": 0.6}})
        assert ok
    assert out["stop_framing"] and f"{agent_tools.FRAMING_MANUAL_LIMIT} рамок" in out["stop_framing"]


def test_framing_question_added_to_report():
    stop = "3 неудачных попыток"
    assert "Вопрос владельцу" in editor_agent.framing_question("Готово. Хук «ТАРАКАН НА МОНИТОРЕ?!» на 0–8 с.")
    own = "Готово. Подскажите, где Мура на 5–28 с — в каком углу кадра?"
    assert editor_agent.framing_question(own) == own
    assert "СТОП кадрирования" in editor_agent.framing_stop_nudge(stop) and "?" in editor_agent.framing_stop_nudge(stop)


def test_agent_loop_gets_stop_instruction_and_asks_owner(tmp_path, monkeypatch):
    from test_editor_agent import ScriptedGemini, _call

    store, plan = _plan(tmp_path, monkeypatch)
    monkeypatch.setattr(settings, "gemini_api_key", "")

    def broken(*_a, **_k):
        raise avatar_box.AvatarAnswerError("пустой ответ (попыток 2)")

    monkeypatch.setattr(avatar_box, "detect_avatar", broken)
    fake = ScriptedGemini([
        [_call("detect_avatar", {})],
        [_call("detect_avatar", {"refresh": True})],
        [_call("set_layout", {"mode": "avatar_full"})],
        [_call("set_layout", {"mode": "avatar_full", "cam": YOUTUBE_CAM})],
        [{"text": "Готово: сделал всё, кроме кадра аватара — он не получился."}],
        [{"text": "Готово: сделал всё, кроме кадра аватара — он не получился."}],
        [{"text": "Готово: сделал всё, кроме кадра аватара — он не получился."}],
    ])
    out = editor_agent.run_agent(store, plan["id"], "в кадре только аватар", client=fake, model="m")
    sent = [p.get("text", "") for _m, req in fake.requests for p in req["contents"][-1]["parts"] if "text" in p]
    assert any("СТОП кадрирования" in t for t in sent)
    assert [s["ok"] for s in out["steps"] if s["tool"] != "_say"][:4] == [False, False, False, False]
    assert "Вопрос владельцу" in out["text"]


# ------------------------------------------------------------------ 5. оборванная фраза в конце клипа


def _w(word, start, end):
    return {"word": word, "start": start, "end": end}


TAIL_WORDS = [_w("Я", 28.0, 29.03), _w("обосралась,", 29.12, 30.07), _w("п*****.", 30.52, 31.17),
              _w("Седьмой", 31.54, 31.89), _w("стрель.", 31.98, 32.07)]


def test_tail_fragment_after_punchline_is_cut():
    from app.cuts import cut_edge_warnings, suggest_cuts, tail_fragment

    tail = tail_fragment(TAIL_WORDS, 32.1)
    assert tail and tail["words"] == "Седьмой стрель." and tail["end"] == 32.1
    assert 31.17 < tail["start"] <= 31.52
    out = suggest_cuts(TAIL_WORDS, 32.1, mode="both")
    assert any(c["start"] == tail["start"] and c["end"] == 32.1 for c in out["cuts"])
    assert any("оборванная фраза" in it["reason"] for it in out["items"])
    assert any("оборванной фразой «Седьмой стрель.»" in w for w in cut_edge_warnings([], TAIL_WORDS, 32.1))
    assert not any("оборванной" in w for w in cut_edge_warnings([{"start": tail["start"], "end": 32.1}], TAIL_WORDS, 32.1))
    # Договорённая фраза (тишина в конце) и длинная последняя фраза — не трогаем.
    assert tail_fragment(TAIL_WORDS[:3], 32.1) is None
    long_tail = TAIL_WORDS[:3] + [_w("а", 31.5, 31.6), _w("потом", 31.7, 32.0), _w("я", 32.1, 32.2),
                                  _w("пошла", 32.3, 32.7), _w("спать", 32.8, 33.98)]
    assert tail_fragment(long_tail, 34.0) is None
    assert tail_fragment([_w("Привет", 0.1, 0.5), _w("всем", 0.55, 1.0)], 1.0) is None  # одна фраза на клип


def test_lint_warns_about_unfinished_tail(tmp_path, monkeypatch):
    from app import quality_lint

    store, plan = _plan(tmp_path, monkeypatch)
    store.set_clip_plan_subtitles(plan["id"], {"words": TAIL_WORDS, "language": "ru"})
    lint = quality_lint.lint_clip(store, plan["id"], media=False)
    tails = [f for f in lint["findings"] if "оборванной фразой" in f["message"]]
    assert len(tails) == 1 and tails[0]["severity"] == "warn" and "Седьмой стрель." in tails[0]["message"]
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "suggest_cuts", {})
    assert ok and any(c["end"] == pytest.approx(32.1) and c["start"] > 31.17 for c in out["cuts"])
    out, ok = run_tool(ToolContext(store=store, clip_plan_id=plan["id"]), "apply_cuts", {"cuts": out["cuts"]})
    assert ok, out
    assert not [f for f in quality_lint.lint_clip(store, plan["id"], media=False)["findings"]
                if "оборванной фразой" in f["message"]]
