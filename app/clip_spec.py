"""The clip file (``vvf.clip/1``): one editable JSON document per clip.

Why a file: the service cuts and renders on its own, but the owner wants two
more ways in — edit a clip by hand before rendering, and let an outside agent
(Claude / Codex, see ``docs/AGENTS.md`` and ``tools/vvf.py``) improve a clip
without knowing the service's internals. Both use the same document:

    GET  /api/clip-plans/{id}/spec     → the file
    PUT  /api/clip-plans/{id}/spec     → validate the whole file, then apply it
    POST /api/clip-plans/{id}/render   → render with what the file says
    GET  /api/clips/{id}               → result, incl. the QC report

One edit, one file; everything else is derived from it. Read-only parts
(``source``, ``renders``) are there so the editor of the file sees context —
they are ignored on PUT.

Pieces (``pieces``) are the ordered parts of the clip, cut from the source:
* keep ``segment_id`` to edit an existing piece,
* drop ``segment_id`` (or set null) to add a new piece,
* leave a piece out to remove it from the clip.
A piece shared with another clip is copied before its timing changes, so an
edit never leaks into a clip you did not open.

The PUT is all-or-nothing: every check runs before the first write.
"""

from __future__ import annotations

from typing import Any

from app.clip_settings import normalize_render_settings
from app.store import MAX_SEGMENT_DURATION_SEC, MIN_SEGMENT_DURATION_SEC, AppStore

SCHEMA_ID = "vvf.clip/1"
MAX_PIECES = 12
MAX_TITLE = 100
MAX_DESCRIPTION = 2000
MAX_NOTES = 4000


class SpecError(ValueError):
    """The file is invalid; ``problems`` lists every problem, not just the first."""

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def export_spec(store: AppStore, clip_plan_id: int) -> dict:
    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(plan["source_id"])
    renders = [
        {
            "clip_id": c["id"],
            "status": c["status"],
            "duration_sec": c.get("duration_sec"),
            "url": f"/media/clips/{c['id']}" if c["status"] == "succeeded" else None,
            "error": c.get("error") or None,
            "qc": c.get("qc"),
            "finished_at": c.get("finished_at"),
        }
        for c in store.list_clips_for_plan(clip_plan_id)
    ]
    return {
        "schema": SCHEMA_ID,
        "clip_id": plan["id"],
        "source": {
            "id": source["id"],
            "name": source.get("original_filename") or "",
            "duration_sec": source.get("duration_sec"),
            "width": source.get("width"),
            "height": source.get("height"),
            "video_url": f"/media/sources/{source['id']}",
        },
        "title": plan["title"],
        "description": plan["description"],
        "favorite": bool(plan.get("favorite")),
        "notes": plan.get("notes") or "",
        "pieces": [
            {
                "segment_id": seg["id"],
                "title": seg["title"],
                "start_sec": float(seg["start_sec"]),
                "end_sec": float(seg["end_sec"]),
                "focus": seg.get("focus") or [],
            }
            for seg in plan.get("segments") or []
        ],
        "render": normalize_render_settings(plan.get("render_settings") or {}),
        "renders": renders,
    }


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN guard


def validate_spec(store: AppStore, clip_plan_id: int, spec: Any) -> dict:
    """Check the whole file; return the normalized operations or raise SpecError."""
    plan = store.get_clip_plan(clip_plan_id)
    source = store.get_source(plan["source_id"])
    duration = float(source.get("duration_sec") or 0)
    problems: list[str] = []

    if not isinstance(spec, dict):
        raise SpecError(["файл клипа должен быть JSON-объектом"])
    if spec.get("schema") not in (None, SCHEMA_ID):
        problems.append(f"schema: ожидается {SCHEMA_ID!r}, пришло {spec.get('schema')!r}")

    title = spec.get("title", plan["title"])
    if not isinstance(title, str) or not title.strip():
        problems.append("title: нужна непустая строка")
    description = spec.get("description", plan["description"])
    if not isinstance(description, str):
        problems.append("description: нужна строка")
    notes = spec.get("notes", plan.get("notes") or "")
    if not isinstance(notes, str):
        problems.append("notes: нужна строка")

    own_ids = {seg["id"] for seg in plan.get("segments") or []}
    raw_pieces = spec.get("pieces")
    pieces: list[dict] = []
    if not isinstance(raw_pieces, list) or not raw_pieces:
        problems.append("pieces: нужен непустой список кусков")
        raw_pieces = []
    if len(raw_pieces) > MAX_PIECES:
        problems.append(f"pieces: не больше {MAX_PIECES} кусков в одном клипе")
    seen: set[int] = set()
    for index, raw in enumerate(raw_pieces[:MAX_PIECES]):
        where = f"pieces[{index}]"
        if not isinstance(raw, dict):
            problems.append(f"{where}: нужен объект")
            continue
        seg_id = raw.get("segment_id")
        if seg_id is not None:
            if not isinstance(seg_id, int) or seg_id not in own_ids:
                problems.append(f"{where}.segment_id: {seg_id!r} не из этого клипа (для нового куска уберите поле)")
                continue
            if seg_id in seen:
                problems.append(f"{where}.segment_id: кусок {seg_id} указан дважды")
                continue
            seen.add(seg_id)
        start, end = _num(raw.get("start_sec")), _num(raw.get("end_sec"))
        if start is None or end is None:
            problems.append(f"{where}: start_sec и end_sec должны быть числами")
            continue
        if start < 0 or end <= start:
            problems.append(f"{where}: нужно 0 ≤ start_sec < end_sec (пришло {start}–{end})")
            continue
        length = end - start
        if length < MIN_SEGMENT_DURATION_SEC or length > MAX_SEGMENT_DURATION_SEC:
            problems.append(
                f"{where}: длина куска {length:.1f} с, допустимо {MIN_SEGMENT_DURATION_SEC}–{MAX_SEGMENT_DURATION_SEC} с"
            )
            continue
        if duration and end > duration + 0.05:
            problems.append(f"{where}: end_sec {end} за концом исходника ({duration:.1f} с)")
            continue
        focus = raw.get("focus")
        if focus is not None and not isinstance(focus, list):
            problems.append(f"{where}.focus: список точек [{{t, x}}] или не указывать")
            continue
        # Shorthand for a fixed frame: manual_x → a one-point focus track (the
        # track is the single source of truth for framing; see _segment_reframe_x).
        if raw.get("manual_x") is not None:
            manual_x = _num(raw.get("manual_x"))
            if manual_x is None or not 0 <= manual_x <= 1:
                problems.append(f"{where}.manual_x: число 0..1 (фиксированный кадр) или не указывать")
                continue
            focus = [{"t": 0.0, "x": manual_x}]
        piece_title = raw.get("title")
        pieces.append(
            {
                "segment_id": seg_id,
                "title": piece_title.strip()[:MAX_TITLE] if isinstance(piece_title, str) and piece_title.strip() else None,
                "start_sec": round(start, 3),
                "end_sec": round(end, 3),
                "focus": focus,
            }
        )

    render = spec.get("render")
    if render is not None and not isinstance(render, dict):
        problems.append("render: нужен объект настроек (или не указывать)")

    if problems:
        raise SpecError(problems)
    return {
        "title": title.strip()[:MAX_TITLE],
        "description": description.strip()[:MAX_DESCRIPTION],
        "notes": notes[:MAX_NOTES],
        "favorite": spec.get("favorite"),
        "pieces": pieces,
        "render": normalize_render_settings(render) if isinstance(render, dict) else None,
    }


def apply_spec(store: AppStore, clip_plan_id: int, spec: Any) -> tuple[dict, list[str]]:
    """Validate everything, then apply. Returns (fresh file, human-readable changes)."""
    ops = validate_spec(store, clip_plan_id, spec)
    plan = store.get_clip_plan(clip_plan_id)
    current = {seg["id"]: seg for seg in plan.get("segments") or []}
    changes: list[str] = []

    if (ops["title"], ops["description"], ops["notes"]) != (plan["title"], plan["description"], plan.get("notes") or ""):
        store.update_clip_plan_text(clip_plan_id, ops["title"], ops["description"], ops["notes"])
        changes.append("текст клипа")
    if isinstance(ops["favorite"], bool) and ops["favorite"] != bool(plan.get("favorite")):
        store.set_clip_plan_flags(clip_plan_id, favorite=ops["favorite"])
        changes.append("избранное")

    ordered_ids: list[int] = []
    for index, piece in enumerate(ops["pieces"], start=1):
        seg_id = piece["segment_id"]
        if seg_id is None:
            seg = store.create_segment_for_plan(
                clip_plan_id, piece["start_sec"], piece["end_sec"], piece["title"] or f"Кусок {index}"
            )
            seg_id = seg["id"]
            changes.append(f"кусок {index}: добавлен {piece['start_sec']}–{piece['end_sec']} с")
        else:
            seg = current[seg_id]
            moved = (piece["start_sec"], piece["end_sec"]) != (round(float(seg["start_sec"]), 3), round(float(seg["end_sec"]), 3))
            if moved:
                if store.segment_plan_count(seg_id) > 1:
                    # Shared with another clip: copy, so the other clip stays as it was.
                    seg = store.create_segment_for_plan(
                        clip_plan_id, piece["start_sec"], piece["end_sec"], piece["title"] or seg["title"]
                    )
                    changes.append(f"кусок {index}: таймкоды → копия (кусок общий с другим клипом)")
                    seg_id = seg["id"]
                else:
                    store.update_ai_segment_timecodes(seg_id, piece["start_sec"], piece["end_sec"])
                    changes.append(f"кусок {index}: таймкоды {piece['start_sec']}–{piece['end_sec']} с")
            if piece["title"] and piece["title"] != seg["title"]:
                store.update_segment_title(seg_id, piece["title"])
                changes.append(f"кусок {index}: название")
        if piece["focus"] is not None and piece["focus"] != (seg.get("focus") or []):
            store.update_ai_segment_focus(seg_id, piece["focus"])
            changes.append(f"кусок {index}: кадрирование")
        ordered_ids.append(seg_id)

    if ordered_ids != [seg["id"] for seg in plan.get("segments") or []]:
        store.set_clip_plan_segments(clip_plan_id, ordered_ids)
        if set(ordered_ids) != set(current):
            changes.append("состав кусков")
        else:
            changes.append("порядок кусков")

    if ops["render"] is not None and ops["render"] != normalize_render_settings(plan.get("render_settings") or {}):
        store.set_clip_plan_render_settings(clip_plan_id, ops["render"])
        changes.append("настройки рендера")

    return export_spec(store, clip_plan_id), changes


def json_schema() -> dict:
    """JSON Schema of the file — for agents and editors with validation."""
    focus_point = {
        "type": "object",
        "properties": {
            "t": {"type": "number", "minimum": 0, "description": "секунды от начала куска"},
            "x": {"type": "number", "minimum": 0, "maximum": 1, "description": "центр кадра 9:16 по ширине исходника"},
            "y": {"type": "number", "minimum": 0, "maximum": 1},
            "cut": {"type": "boolean", "description": "резкий переход кадра в этой точке"},
        },
        "required": ["t", "x"],
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "Файл клипа Vertical Video Fabric",
        "type": "object",
        "required": ["pieces"],
        "properties": {
            "schema": {"const": SCHEMA_ID},
            "clip_id": {"type": "integer", "readOnly": True},
            "source": {"type": "object", "readOnly": True},
            "renders": {"type": "array", "readOnly": True},
            "title": {"type": "string", "maxLength": MAX_TITLE},
            "description": {"type": "string", "maxLength": MAX_DESCRIPTION},
            "favorite": {"type": "boolean"},
            "notes": {"type": "string", "maxLength": MAX_NOTES, "description": "свободные заметки: почему так смонтировано"},
            "pieces": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_PIECES,
                "items": {
                    "type": "object",
                    "required": ["start_sec", "end_sec"],
                    "properties": {
                        "segment_id": {"type": ["integer", "null"], "description": "не указывать для нового куска"},
                        "title": {"type": "string"},
                        "start_sec": {"type": "number", "minimum": 0},
                        "end_sec": {"type": "number"},
                        "focus": {"type": "array", "items": focus_point, "description": "трек кадра; одна точка = фиксированный кадр"},
                        "manual_x": {"type": "number", "minimum": 0, "maximum": 1, "writeOnly": True,
                                     "description": "сокращение: фиксированный кадр = focus [{t:0, x}]"},
                    },
                },
                "description": f"куски по порядку; каждый {MIN_SEGMENT_DURATION_SEC}–{MAX_SEGMENT_DURATION_SEC} с",
            },
            "render": {"type": "object", "description": "настройки рендера клипа, см. GET /api/render/transition-options"},
        },
    }
