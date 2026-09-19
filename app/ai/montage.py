"""🤖 ИИ-монтаж: an agent that re-edits one clip through its clip file.

The model sees the clip as the editor does — its pieces (source timecodes), the
spoken lines around each piece with exact times and the pauses between them,
and, once the clip was rendered, the QC report of the last render. It answers
with a new list of pieces (hook first, dead air and filler trimmed, long pieces
jump-cut at pauses), a transition, subtitles on/off, a cover frame and a short
rationale. The proposal goes through ``validate_spec`` — the same all-or-nothing
check a human edit goes through — and any problems are sent back to the model to
fix (the agent loop). Nothing is written until the owner applies it.

Framing survives the re-cut: every new piece takes the focus track of the
original piece(s) it overlaps, re-based to its own start (the tracks are stored
relative to piece start, the source timeline is absolute).
"""

from __future__ import annotations

import json
from typing import Any, Callable

from app.ai.gemini import GeminiClient, _extract_response_text
from app.clip_spec import SpecError, export_spec, validate_spec
from app.settings import settings
from app.store import MAX_SEGMENT_DURATION_SEC, MIN_SEGMENT_DURATION_SEC
from app.transitions import SFX_KINDS, TRANSITIONS

CONTEXT_PAD_SEC = 10.0  # the model may extend a piece this far to finish a phrase
PAUSE_SEC = 0.8  # gaps between lines at least this long are listed as pauses
MAX_ATTEMPTS = 3
MAX_LINES = 400

RULES = f"""\
Ты — монтажёр вертикальных клипов (YouTube Shorts / TikTok / Reels). Тебе дан один клип:
его куски (таймкоды в секундах ИСХОДНОГО видео), реплики вокруг каждого куска с точным
временем, паузы между репликами и, если есть, отчёт QC последнего рендера.

Перемонтируй клип, чтобы его досматривали:
1. ХУК: первые 1–2 секунды должны цеплять — самая сильная фраза/реакция. Можно поставить
   сильный кусок первым или подрезать начало до момента, где уже что-то происходит.
2. Убери мёртвое: паузы, «эээ», повторы, уход в другую тему, тишину в начале и в конце.
   Длинный кусок с паузами внутри разрежь на несколько кусков по паузам (jump-cut).
3. Не режь посреди фразы: границы ставь на начало/конец реплик (±0.1 с).
4. Смысл и развязка должны остаться: не выкидывай панчлайн и контекст, без которого не смешно.
5. Итоговая длина: по возможности 15–60 с, никогда не больше 175 с.

Ограничения (иначе правка не пройдёт проверку):
- каждый кусок {MIN_SEGMENT_DURATION_SEC}–{MAX_SEGMENT_DURATION_SEC} с, не больше 12 кусков;
- куски берутся только из переданных окон реплик (исходный кусок ± {CONTEXT_PAD_SEC:.0f} с);
- source_piece — номер исходного куска (с 0), из которого взят новый кусок.
- transition.type: одно из {", ".join(TRANSITIONS)}; sfx: одно из {", ".join(SFX_KINDS)}.
  Для одного куска переход не важен. Мемный монтаж — flash/zoom + whoosh; «прошло время» — fadeblack.
- cover: номер НОВОГО куска и секунды от его начала — кадр с самой яркой эмоцией.
- rationale: 2–5 коротких пунктов по-русски, что и зачем изменил.
Если клип уже хорош — верни его почти без изменений и так и скажи в rationale.
Верни ТОЛЬКО JSON по схеме.
"""


def _schema() -> dict[str, Any]:
    piece = {
        "type": "object",
        "properties": {
            "source_piece": {"type": "integer"},
            "start_sec": {"type": "number"},
            "end_sec": {"type": "number"},
            "title": {"type": "string"},
        },
        "required": ["source_piece", "start_sec", "end_sec", "title"],
        "propertyOrdering": ["source_piece", "start_sec", "end_sec", "title"],
    }
    return {
        "type": "object",
        "properties": {
            "pieces": {"type": "array", "items": piece},
            "transition": {
                "type": "object",
                "properties": {"type": {"type": "string"}, "sfx": {"type": "string"}},
                "required": ["type", "sfx"],
            },
            "subtitles": {"type": "boolean"},
            "cover": {
                "type": "object",
                "properties": {"piece": {"type": "integer"}, "offset": {"type": "number"}},
                "required": ["piece", "offset"],
            },
            "rationale": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["pieces", "transition", "subtitles", "cover", "rationale"],
        "propertyOrdering": ["pieces", "transition", "subtitles", "cover", "rationale"],
    }


def build_context(store: Any, clip_plan_id: int) -> tuple[dict, dict]:
    """(current clip file, what the model sees)."""
    spec = export_spec(store, clip_plan_id)
    try:
        cues = store.get_source_transcript(int(spec["source"]["id"]))
    except KeyError:
        cues = []
    duration = float(spec["source"].get("duration_sec") or 0) or None
    pieces = []
    for index, piece in enumerate(spec["pieces"]):
        lo = max(0.0, piece["start_sec"] - CONTEXT_PAD_SEC)
        hi = piece["end_sec"] + CONTEXT_PAD_SEC
        if duration:
            hi = min(duration, hi)
        lines, pauses, prev_end = [], [], None
        for cue in cues:
            try:
                a, b = float(cue["start"]), float(cue["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if b <= lo or a >= hi:
                continue
            if prev_end is not None and a - prev_end >= PAUSE_SEC:
                pauses.append([round(prev_end, 2), round(a, 2)])
            prev_end = b
            lines.append({"start": round(a, 2), "end": round(b, 2), "text": str(cue.get("text") or "").strip()})
        pieces.append(
            {
                "index": index,
                "title": piece["title"],
                "start_sec": piece["start_sec"],
                "end_sec": piece["end_sec"],
                "window": [round(lo, 2), round(hi, 2)],
                "lines": lines[:MAX_LINES],
                "pauses": pauses,
            }
        )
    last_qc = next((r.get("qc") for r in reversed(spec.get("renders") or []) if r.get("qc")), None)
    context = {
        "title": spec["title"],
        "description": spec["description"],
        "total_sec": round(sum(p["end_sec"] - p["start_sec"] for p in spec["pieces"]), 1),
        "pieces": pieces,
        "transition": spec["render"]["transition"],
        "subtitles_on": spec["render"]["subs_on"],
        "last_render_qc": last_qc,
        "has_transcript": bool(cues),
    }
    return spec, context


def _abs_focus(spec: dict) -> list[tuple[float, float, float, list]]:
    """[(abs_start, abs_end, piece_start, focus)] of the original pieces."""
    return [(p["start_sec"], p["end_sec"], p["start_sec"], p.get("focus") or []) for p in spec["pieces"]]


def carry_focus(spec: dict, start: float, end: float) -> list[dict] | None:
    """Focus track for a new piece [start, end) from the original pieces it overlaps."""
    points: list[dict] = []
    for a, b, base, focus in _abs_focus(spec):
        if b <= start or a >= end:
            continue
        for p in focus:
            try:
                t_abs = base + float(p["t"])
                x = float(p["x"])
            except (KeyError, TypeError, ValueError):
                continue
            points.append({"abs": t_abs, "x": x, "cut": bool(p.get("cut"))})
    if not points:
        return None
    points.sort(key=lambda p: p["abs"])
    # value at the new piece's start: last point at/before it, else the first one
    before = [p for p in points if p["abs"] <= start]
    head = before[-1] if before else points[0]
    out = [{"t": 0.0, "x": round(head["x"], 4)}]
    for p in points:
        if start < p["abs"] < end:
            q = {"t": round(p["abs"] - start, 3), "x": round(p["x"], 4)}
            if p["cut"]:
                q["cut"] = True
            out.append(q)
    return out


def to_spec(spec: dict, proposal: dict) -> dict:
    """Turn the model's answer into a full clip file (keeps render settings)."""
    old = spec["pieces"]
    used: set[int] = set()
    pieces = []
    for raw in proposal.get("pieces") or []:
        try:
            start, end = float(raw["start_sec"]), float(raw["end_sec"])
            src = int(raw.get("source_piece", -1))
        except (KeyError, TypeError, ValueError):
            continue
        piece: dict[str, Any] = {"start_sec": round(start, 3), "end_sec": round(end, 3)}
        title = str(raw.get("title") or "").strip()
        if title:
            piece["title"] = title[:100]
        # Reuse the original segment for the FIRST piece cut from it — the rest
        # become new segments (a jump-cut splits one piece into several).
        if 0 <= src < len(old) and src not in used:
            used.add(src)
            piece["segment_id"] = old[src]["segment_id"]
        focus = carry_focus(spec, start, end)
        if focus is not None:
            piece["focus"] = focus
        pieces.append(piece)
    render = json.loads(json.dumps(spec["render"]))
    tr = proposal.get("transition") or {}
    if isinstance(tr, dict):
        if tr.get("type") in TRANSITIONS:
            render["transition"]["type"] = tr["type"]
        if tr.get("sfx") in SFX_KINDS:
            render["transition"]["sfx"] = tr["sfx"]
    if isinstance(proposal.get("subtitles"), bool):
        render["subs_on"] = proposal["subtitles"]
    cover = proposal.get("cover") or {}
    if isinstance(cover, dict) and pieces:
        try:
            ci = min(len(pieces) - 1, max(0, int(cover.get("piece", 0))))
            offset = max(0.0, float(cover.get("offset", 0)))
            length = pieces[ci]["end_sec"] - pieces[ci]["start_sec"]
            render["cover"] = {
                **render.get("cover", {}),
                "mode": "frame",
                "piece": ci,
                "offset": round(min(offset, max(0.0, length - 0.1)), 2),
                "burn": True,
            }
        except (TypeError, ValueError):
            pass
    notes = "🤖 ИИ-монтаж:\n" + "\n".join(f"• {r}" for r in proposal.get("rationale") or [] if str(r).strip())
    return {
        "schema": spec["schema"],
        "title": spec["title"],
        "description": spec["description"],
        "notes": notes[:4000],
        "pieces": pieces,
        "render": render,
    }


def _in_windows(context: dict, new_spec: dict) -> list[str]:
    """Keep the agent inside the material it was shown."""
    windows = [p["window"] for p in context["pieces"]]
    problems = []
    for i, p in enumerate(new_spec["pieces"]):
        if not any(w[0] - 0.05 <= p["start_sec"] and p["end_sec"] <= w[1] + 0.05 for w in windows):
            problems.append(f"pieces[{i}]: {p['start_sec']}–{p['end_sec']} вне переданных окон реплик")
    return problems


def diff(old: dict, new: dict) -> dict:
    total = lambda s: round(sum(p["end_sec"] - p["start_sec"] for p in s["pieces"]), 1)  # noqa: E731
    return {
        "before": [{"start_sec": p["start_sec"], "end_sec": p["end_sec"], "title": p["title"]} for p in old["pieces"]],
        "after": [
            {"start_sec": p["start_sec"], "end_sec": p["end_sec"], "title": p.get("title") or ""} for p in new["pieces"]
        ],
        "total_before": total(old),
        "total_after": total(new),
        "transition": new["render"]["transition"]["type"],
        "sfx": new["render"]["transition"]["sfx"],
        "subtitles": new["render"]["subs_on"],
    }


def propose_montage(
    store: Any,
    clip_plan_id: int,
    goal: str = "",
    client: GeminiClient | None = None,
    model: str | None = None,
    on_attempt: Callable[[int, list[str]], None] | None = None,
) -> dict:
    """Ask the model for a re-edit; loop on validation problems. Writes nothing."""
    spec, context = build_context(store, clip_plan_id)
    used_model = model or settings.gemini_montage_model
    gemini = client or GeminiClient()
    request = RULES + (f"\nПожелание владельца: {goal.strip()}\n" if goal.strip() else "") + "\nКлип (JSON):\n"
    request += json.dumps(context, ensure_ascii=False)
    contents: list[dict] = [{"role": "user", "parts": [{"text": request}]}]
    problems: list[str] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        response = gemini.generate_content(
            used_model,
            {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.4,
                    "responseMimeType": "application/json",
                    "responseSchema": _schema(),
                },
            },
        )
        text = _extract_response_text(response)
        try:
            proposal = json.loads(text)
            new_spec = to_spec(spec, proposal)
            problems = _in_windows(context, new_spec)
            if not problems:
                validate_spec(store, clip_plan_id, new_spec)
        except json.JSONDecodeError:
            problems = ["ответ не JSON"]
        except SpecError as exc:
            problems = exc.problems
        if on_attempt:
            on_attempt(attempt, problems)
        if not problems:
            return {
                "spec": new_spec,
                "diff": diff(spec, new_spec),
                "rationale": [str(r) for r in proposal.get("rationale") or []],
                "attempts": attempt,
                "model": used_model,
                "has_transcript": context["has_transcript"],
                "used_qc": context["last_render_qc"] is not None,
            }
        # The loop: show the model its own answer and exactly what failed.
        contents += [
            {"role": "model", "parts": [{"text": text}]},
            {"role": "user", "parts": [{"text": "Правка не прошла проверку:\n- " + "\n- ".join(problems)
                                         + "\nИсправь и верни JSON целиком."}]},
        ]
    raise RuntimeError("ИИ не смог выдать корректный монтаж: " + "; ".join(problems[:5]))


# ---- apply / undo / batch -------------------------------------------------

_EDITABLE = ("schema", "title", "description", "notes", "favorite", "pieces", "render")


def apply_montage(store: Any, clip_plan_id: int, spec: dict) -> tuple[dict, list[str]]:
    """Apply an AI re-edit, keeping the pre-AI clip file for «↩ Откатить».

    Only the FIRST AI edit is backed up: after «Ещё вариант» → apply again, the
    undo still returns to the owner's own version, not to an earlier AI one.
    """
    from app.clip_spec import apply_spec

    if store.get_clip_plan_montage_backup(clip_plan_id) is None:
        current = export_spec(store, clip_plan_id)
        store.set_clip_plan_montage_backup(clip_plan_id, {k: current[k] for k in _EDITABLE if k in current})
    return apply_spec(store, clip_plan_id, spec)


def undo_montage(store: Any, clip_plan_id: int) -> tuple[dict, list[str]]:
    from app.clip_spec import apply_spec

    backup = store.get_clip_plan_montage_backup(clip_plan_id)
    if backup is None:
        raise KeyError(f"no AI montage to undo for clip plan {clip_plan_id}")
    own = {seg["id"] for seg in store.get_clip_plan(clip_plan_id).get("segments") or []}
    # Pieces the AI dropped are no longer this clip's segments: restore them as
    # new pieces with the same timecodes, title and framing.
    pieces = []
    for piece in backup.get("pieces") or []:
        piece = dict(piece)
        if piece.get("segment_id") not in own:
            piece.pop("segment_id", None)
        pieces.append(piece)
    result = apply_spec(store, clip_plan_id, {**backup, "pieces": pieces})
    store.set_clip_plan_montage_backup(clip_plan_id, None)
    return result


import threading  # noqa: E402 - batch section
import time  # noqa: E402
import uuid  # noqa: E402

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
BATCH_WORKERS = 3


def montage_many(
    store: Any,
    clip_plan_ids: list[int],
    goal: str = "",
    render: Callable[[int], Any] | None = None,
    progress: Callable[[dict], None] | None = None,
    propose: Callable[..., dict] | None = None,
    on_items: Callable[[list[dict]], None] | None = None,
) -> list[dict]:
    """Propose + apply (+ render) for several clips, 3 at a time. Never raises:
    each clip's outcome is reported in its item."""
    from concurrent.futures import ThreadPoolExecutor

    propose = propose or propose_montage
    items = []
    for pid in dict.fromkeys(int(p) for p in clip_plan_ids):
        try:
            title = store.get_clip_plan(pid, include_segments=False).get("title") or f"План #{pid}"
        except KeyError:
            title = f"План #{pid}"
        items.append({"plan_id": pid, "title": title, "status": "queued"})
    if on_items:
        on_items(items)  # the live list: callers watch it fill in

    def one(item: dict) -> None:
        item["status"] = "thinking"
        if progress:
            progress(item)
        try:
            proposal = propose(store, item["plan_id"], goal=goal)
            _, changes = apply_montage(store, item["plan_id"], proposal["spec"])
            item.update(
                total_before=proposal["diff"]["total_before"],
                total_after=proposal["diff"]["total_after"],
                pieces_after=len(proposal["diff"]["after"]),
                changes=changes,
                rationale=proposal.get("rationale") or [],
            )
            if render:
                item["status"] = "rendering"
                if progress:
                    progress(item)
                clip = render(item["plan_id"])
                item["clip_id"] = (clip or {}).get("id") if isinstance(clip, dict) else None
            item["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - one clip never stops the batch
            item["status"] = "failed"
            item["error"] = str(exc)[:300]
        if progress:
            progress(item)

    with ThreadPoolExecutor(max_workers=max(1, min(BATCH_WORKERS, len(items) or 1))) as pool:
        list(pool.map(one, items))
    return items


def start_montage_job(
    store: Any, source_id: int, clip_plan_ids: list[int], goal: str = "", render: Callable[[int], Any] | None = None
) -> dict:
    job = {
        "id": uuid.uuid4().hex[:12],
        "source_id": source_id,
        "goal": goal,
        "render": render is not None,
        "status": "running",
        "started_at": time.time(),
        "items": [],
    }
    with _JOBS_LOCK:
        _JOBS[job["id"]] = job
        # Keep the registry small: the newest 20 jobs are plenty to look back at.
        for old in sorted(_JOBS.values(), key=lambda j: j["started_at"])[:-20]:
            _JOBS.pop(old["id"], None)

    def run() -> None:
        def on_progress(_item: dict) -> None:
            job["updated_at"] = time.time()

        try:
            items = montage_many(
                store,
                clip_plan_ids,
                goal=goal,
                render=render,
                progress=on_progress,
                on_items=lambda live: job.__setitem__("items", live),
            )
            job["items"] = items
            job["status"] = "done"
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["error"] = str(exc)[:300]
        job["finished_at"] = time.time()

    # Items are visible (queued) immediately, and filled in as the workers go.
    job["items"] = [{"plan_id": int(p), "status": "queued"} for p in dict.fromkeys(clip_plan_ids)]
    threading.Thread(target=run, name=f"ai-montage-{job['id']}", daemon=True).start()
    return job_view(job["id"])


def job_view(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise KeyError(f"ai montage job not found: {job_id}")
        view = json.loads(json.dumps(job, default=str))
    items = view.get("items") or []
    view["done"] = sum(1 for i in items if i.get("status") in {"done", "failed"})
    view["failed"] = sum(1 for i in items if i.get("status") == "failed")
    view["total"] = len(items)
    return view


def latest_job_for_source(source_id: int) -> dict | None:
    with _JOBS_LOCK:
        jobs = [j for j in _JOBS.values() if j["source_id"] == source_id]
    if not jobs:
        return None
    return job_view(max(jobs, key=lambda j: j["started_at"])["id"])
