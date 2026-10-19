"""Компиляция: несколько моментов одного стрима в один клип «ТОП-N».

Частый формат шортсов со стримов — «лучшие моменты за стрим»: моменты подряд,
перед каждым — номер, между ними — переход со звуком. Собираем новый клип из
кусков выбранных клипов (куски общие: правка таймкодов в компиляции скопирует
кусок и не тронет исходный клип), переносим их субтитры со сдвигом и ставим
номера карточками.

Ограничение формата клипа — 12 кусков; если моментов с кусками больше, лишние
не берём и говорим об этом.
"""

from __future__ import annotations

from typing import Any

from app.auto_edit import caption_y_for
from app.clip_spec import MAX_PIECES
from app.motion import normalize_layers

CARD_SEC = 1.6


def build_compilation(
    store: Any,
    source_id: int,
    plan_ids: list[int],
    *,
    title: str = "",
    countdown: bool = True,
    caption_style: str = "hormozi",
) -> dict:
    """Создать клип-компиляцию; вернуть {clip_plan_id, pieces, skipped, duration}."""
    plans = [store.get_clip_plan(pid) for pid in plan_ids]
    plans = [p for p in plans if p["source_id"] == source_id and p.get("segments")]
    if len(plans) < 2:
        raise ValueError("для компиляции нужно хотя бы два клипа этого проекта")
    chosen: list[dict] = []
    pieces = 0
    skipped = []
    for plan in plans:
        n = len(plan["segments"])
        if pieces + n > MAX_PIECES:
            skipped.append(plan["id"])
            continue
        chosen.append(plan)
        pieces += n
    if len(chosen) < 2:
        raise ValueError(f"в компиляцию влезает не больше {MAX_PIECES} кусков — выберите клипы короче")

    order = list(reversed(chosen)) if countdown else chosen
    segment_ids = [s["id"] for plan in order for s in plan["segments"]]
    total_n = len(order)
    name = title.strip() or f"ТОП-{total_n} моментов стрима"
    plan = store.create_clip_plan(
        source_id,
        None,
        name,
        description="Компиляция: " + " · ".join(p["title"] for p in order)[:480],
        segment_ids=segment_ids,
        status="candidate",
    )

    # Субтитры: слова каждого клипа — со сдвигом на его начало в компиляции.
    words, lines = [], []
    layers: list[dict] = []
    offset = 0.0
    for index, src in enumerate(order):
        dur = sum(float(s["end_sec"]) - float(s["start_sec"]) for s in src["segments"])
        subs = store.get_clip_plan_subtitles(src["id"]) or {}
        for w in subs.get("words") or []:
            words.append({**w, "start": round(float(w["start"]) + offset, 3), "end": round(float(w["end"]) + offset, 3)})
        for line in subs.get("lines") or []:
            lines.append({**line, "start": round(float(line["start"]) + offset, 3), "end": round(float(line["end"]) + offset, 3)})
        number = total_n - index if countdown else index + 1
        layers.append({
            "id": f"comp_n{index}", "type": "text", "name": f"#{number}", "start": round(offset, 2),
            "end": round(offset + min(CARD_SEC, dur), 2), "track": 4, "x": 0.5, "y": 0.16,
            "in": {"type": "pop", "duration": 0.3}, "out": {"type": "fade", "duration": 0.25},
            "props": {"text": f"#{number}", "font": "Unbounded", "weight": 900, "size": 190, "gradient": ["#ffd600", "#ff8a00"],
                      "stroke": {"color": "#000000", "width": 14}},
        })
        layers.append({
            "id": f"comp_t{index}", "type": "text", "name": src["title"][:40], "start": round(offset + 0.15, 2),
            "end": round(offset + min(CARD_SEC + 0.6, dur), 2), "track": 3, "x": 0.5, "y": 0.255,
            "in": {"type": "slideUp", "duration": 0.3}, "out": {"type": "fade", "duration": 0.25},
            "props": {"text": src["title"][:60], "font": "Montserrat", "weight": 800, "size": 56, "color": "#ffffff",
                      "maxWidth": 900, "bg": {"color": "#7c5cff", "opacity": 1, "radius": 14, "padX": 24, "padY": 10, "skew": -8}},
        })
        if index:
            layers.append({"id": f"comp_w{index}", "type": "audio", "start": round(max(0.0, offset - 0.2), 2),
                           "end": round(offset + 0.6, 2), "props": {"asset_id": "sfx:generated/whoosh.ogg", "volume": 0.7}})
        offset += dur
    if words:
        layers.append({"id": "comp_caps", "type": "captions", "start": 0.0, "end": round(offset, 2), "track": 0,
                       "x": 0.5, "y": caption_y_for(order[0].get("render_settings")),
                       "props": {"style": caption_style, "maxWords": 3}})
        store.set_clip_plan_subtitles(plan["id"], {"lines": lines, "words": words, "duration": round(offset, 3), "edited": True})

    # Раскладку и прочие ручки берём у первого клипа (чаще всего у всех одинаковые).
    settings = dict(order[0].get("render_settings") or {})
    settings.update({
        "layers": normalize_layers(layers),
        "camera": [],
        "inserts": [],
        "cuts": [],
        "subs_on": False,
        # Склейка встык: номера и whoosh отмечают смену момента, а время слоёв и
        # субтитров совпадает с кусками точно (переход-наплыв съел бы доли секунды).
        "transition": {"type": "cut", "duration": 0.25, "smart": False, "audio": "smooth", "sfx": "none", "sfx_volume": 0.5},
    })
    store.set_clip_plan_render_settings(plan["id"], settings)
    return {"clip_plan_id": plan["id"], "pieces": len(segment_ids), "clips": len(order), "skipped": skipped,
            "duration": round(offset, 2)}
