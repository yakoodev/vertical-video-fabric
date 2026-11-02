"""Третий QA Gemini-монтажёра (02.11, клип 895, плейбук vtuber-story): картинки на лице аватара и на хуке.

Граница мест на наплыве и чат в рамке — tests/test_avatar_framing.py; черновик в отчёте —
tests/test_editor_agent.py; ч/б и субтитры — node/motion.test.mjs; слабые слова — tests/test_word_emphasis.py."""

from __future__ import annotations

import cv2
import numpy as np

from app import agent_tools
from app.agent_tools import ToolContext, run_tool
from app.ai import avatar_box
from test_api_pipeline import _require_ffmpeg
from test_clip_spec import _setup

HOOK = {"id": "auto_hook", "type": "text", "start": 0, "end": 8, "x": 0.5, "y": 0.16,
        "props": {"text": "ТАРАКАН НА\nМОНИТОРЕ?!", "size": 90, "weight": 900}}
CAPS = {"id": "auto_caps", "type": "captions", "start": 0, "end": 10, "x": 0.5, "y": 0.72, "props": {"size": 80}}


def _card(tmp_path, store, name="card", size=(500, 889)):
    png = tmp_path / f"{name}.png"  # вертикальная карточка на сплошном фоне (как generate_image у клипа 895)
    cv2.imwrite(str(png), np.full((size[1], size[0], 3), 200, dtype=np.uint8))
    return store.create_montage_asset(kind="image", label=name, file_path=str(png), width=size[0], height=size[1])


def _avatar_clip(tmp_path, monkeypatch):
    _require_ffmpeg()
    _client, store, _source, plan, _a, _b = _setup(tmp_path, monkeypatch)
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    out, ok = run_tool(ctx, "set_layout", {"mode": "avatar_full", "cam": {"x": 0.47, "y": 0.0, "w": 0.47, "h": 0.99}})
    assert ok, out
    out, ok = run_tool(ctx, "add_layers", {"layers": [HOOK, CAPS]})
    assert ok, out
    return store, plan, ctx


def test_face_zone_of_avatar_full_from_detect_avatar_or_cam(tmp_path, monkeypatch):
    store, plan, _ctx = _avatar_clip(tmp_path, monkeypatch)
    zones = agent_tools.avatar_face_zones(store, plan["id"])
    assert zones and zones[0]["t0"] == 0.0 and zones[-1]["t1"] > 5
    x0, y0, x1, y1 = zones[0]["rect"]
    assert x0 < 540 < x1 and y0 < 960 and y1 > 700  # середина верхней части аватара
    # detect_avatar дал лицо — зона по нему (место с той же рамкой cam).
    cam = {"x": 0.47, "y": 0.0, "w": 0.47, "h": 0.99}
    found = {"cam": cam, "box": {"x0": 0.49, "y0": 0.02, "x1": 0.91, "y1": 0.94},
             "face": {"x0": 0.6, "y0": 0.5, "x1": 0.8, "y1": 0.7}, "places": []}
    monkeypatch.setattr(avatar_box, "cached_avatar", lambda store_, pid: found)
    fx0, fy0, fx1, fy1 = agent_tools.avatar_face_zones(store, plan["id"])[0]["rect"]
    assert fy0 > y0 and fy1 - fy0 < y1 - y0 and abs(fy0 - 0.5 / 0.99 * 1920) < 60
    # Не avatar_full — лица не знаем, проверки прежние.
    ctx = ToolContext(store=store, clip_plan_id=plan["id"])
    run_tool(ctx, "set_layout", {"mode": "single"})
    assert agent_tools.avatar_face_zones(store, plan["id"]) == []


def test_card_over_avatar_face_and_hook_warns_with_a_spot(tmp_path, monkeypatch):
    from app.quality_lint import lint_clip

    store, plan, ctx = _avatar_clip(tmp_path, monkeypatch)
    asset = _card(tmp_path, store)
    # Как у клипа 895: вертикальная карточка 500 px шириной, y 0.40 — на лице Муры и на «МОНИТОРЕ» хука.
    card = {"id": "dorm", "type": "image", "start": 2.09, "end": 4.59, "x": 0.5, "y": 0.4,
            "props": {"asset_id": str(asset["id"]), "w": 500}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [card]})
    assert ok, out
    text = " ".join(out["warnings"])
    assert "закрывает лицо аватара" in text and "во весь кадр не дольше 3 с" in text, text
    assert "под хуком auto_hook" in text  # вертикальная карточка (размер файла), а не квадрат 500×500
    lint = lint_clip(store, plan["id"], checks=["montage"], media=False)
    face = [f for f in lint["findings"] if f.get("layer") == "dorm" and "лицо" in f["message"]]
    assert face and face[0]["severity"] == "warn"

    # Лицо известно точно (detect_avatar), карточка горизонтальная — есть место между лицом и субтитрами.
    cam = {"x": 0.47, "y": 0.0, "w": 0.47, "h": 0.99}
    found = {"cam": cam, "box": {"x0": 0.49, "y0": 0.02, "x1": 0.91, "y1": 0.94},
             "face": {"x0": 0.6, "y0": 0.3, "x1": 0.8, "y1": 0.5}, "places": []}
    monkeypatch.setattr(avatar_box, "cached_avatar", lambda store_, pid: found)
    wide = _card(tmp_path, store, "wide", (800, 450))
    out, ok = run_tool(ctx, "update_layer", {"id": "dorm", "patch": {"props": {"asset_id": str(wide["id"])}}})
    assert ok, out
    hint = next(w for w in out["warnings"] if "лицо аватара" in w)
    assert "x=" in hint and "40 % кадра" in hint, hint
    x = float(hint.split("x=")[1].split(" ")[0])
    y = float(hint.split("y=")[1].split(",")[0])
    w_px = int(hint.split("props.w ≤ ")[1].split(" ")[0])
    assert w_px <= 432 and y > 0.5
    # Поставили, куда сказали, — тихо.
    out, ok = run_tool(ctx, "update_layer", {"id": "dorm", "patch": {"x": x, "y": y, "props": {"w": w_px}}})
    assert ok, out
    assert not [w for w in out["warnings"] if "лицо" in w or "хуком" in w], out["warnings"]
    lint = lint_clip(store, plan["id"], checks=["montage"], media=False)
    assert not [f for f in lint["findings"] if f.get("layer") == "dorm" and ("лицо" in f["message"]
                                                                            or "хуком" in f["message"])]


def test_full_frame_insert_over_avatar_only_short(tmp_path, monkeypatch):
    from app.quality_lint import image_card_findings

    store, plan, ctx = _avatar_clip(tmp_path, monkeypatch)
    asset = _card(tmp_path, store, "full", (1080, 1920))
    short = {"id": "full", "type": "image", "start": 6.5, "end": 9.0, "x": 0.5, "y": 0.5,
             "props": {"asset_id": str(asset["id"]), "w": 1080}}
    out, ok = run_tool(ctx, "add_layers", {"layers": [short]})
    assert ok, out
    assert not [w for w in out["warnings"] if "аватар" in w], out["warnings"]  # во весь кадр ≤ 3 с — можно
    # Длиннее 3 с add_layers/update_layer не примут (закрытие кадра), а lint по готовым слоям — скажет.
    _out, ok = run_tool(ctx, "update_layer", {"id": "full", "patch": {"start": 4.3}})
    assert not ok
    faces = agent_tools.avatar_face_zones(store, plan["id"])
    long = image_card_findings(store, [{**short, "start": 4.3}], 10.0, faces=faces)
    assert [f["message"] for f in long] == ["картинка full во весь кадр 4.7 с закрывает аватара"]
    assert image_card_findings(store, [short], 10.0, faces=faces) == []
