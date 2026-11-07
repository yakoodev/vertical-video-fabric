"""Объяснялки: сценарий и разбивка на биты (из ``app/explainer_gen.py``)."""

from __future__ import annotations

import re
from typing import Any

from app import explainer as E
from app import projects as P

CPS = 15.0  # символов русской речи в секунду (пилот: 680 символов за 44 с)
BEAT_MIN_SEC = 1.5
BEAT_MAX_SEC = 3.0
BEAT_MERGE_SEC = 0.9  # бит короче — сливается с соседним (если влезает)


HOOK_MAX_CHARS = 32  # крючок — первые ~2 с


def script_schema() -> dict:
    return {"type": "object", "properties": {
        "title": {"type": "string"}, "hook": {"type": "string"}, "intro": {"type": "string"},
        "points": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "body": {"type": "string"}}, "required": ["title", "body"]}},
        "cta": {"type": "string"}},
        "required": ["title", "hook", "points", "cta"]}


def script_context(store: Any, video: dict) -> dict:
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    project_id = int(video["project_id"])
    options = video.get("options") or {}
    rules = [{"kind": r["kind"], "text": r["text"]} for r in P.list_rules(store, project_id=project_id)]
    project = P.get_project(store, project_id, include_sources=False)
    # Ролик канала объяснялок (app/explainer_channel.py): канал может быть другим проектом, чем
    # библиотека маскота — его правила (уроки из ответов владельца) и описание тоже в промпт.
    channel_id = options.get("channel_project_id")
    if channel_id and int(channel_id) != project_id:
        try:
            channel = P.get_project(store, int(channel_id), include_sources=False)
            seen = {(r["kind"], r["text"]) for r in rules}
            rules += [{"kind": r["kind"], "text": r["text"]}
                      for r in P.list_rules(store, project_id=int(channel_id), include_global=False)
                      if (r["kind"], r["text"]) not in seen]
            project = channel
        except KeyError:
            pass
    previous = [v["topic"] for v in E.list_videos(store, project_id, int(mascot["id"]), limit=12)
                if int(v["id"]) != int(video["id"])]
    out = {"channel": project["name"], "channel_about": project.get("description") or "",
           "mascot": {"name": mascot["name"], "persona": mascot["persona"]},
           "topic": video["topic"], "duration_sec": video["duration_target"], "tone": video["tone"],
           "target_chars": int(video["duration_target"] * CPS), "owner_rules": rules,
           "previous_topics": previous}
    for key in ("niche", "angle", "owner_feedback"):
        if options.get(key):
            out[key] = options[key]
    return out


def _sentence(text: Any) -> str:
    text = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if text and text[-1] not in ".!?…":
        text += "."
    return text


def normalize_script(data: dict) -> dict:
    """Сценарий (Gemini или агент) → разделы и полный текст. ``text`` без пунктов — один раздел."""
    sections: list[dict] = []
    if P._text(data.get("hook")):
        sections.append({"kind": "hook", "text": _sentence(data["hook"])})
    if P._text(data.get("intro")):
        sections.append({"kind": "intro", "text": _sentence(data["intro"])})
    points = [p for p in data.get("points") or [] if isinstance(p, dict) and (p.get("title") or p.get("body"))]
    for n, point in enumerate(points[:6], 1):
        title = _sentence(point.get("title"))
        body = " ".join(_sentence(s) for s in re.split(r"(?<=[.!?…])\s+", str(point.get("body") or "")) if s.strip())
        sections.append({"kind": "point", "n": n, "title": title, "text": " ".join(x for x in (title, body) if x)})
    if P._text(data.get("cta")):
        sections.append({"kind": "cta", "text": _sentence(data["cta"])})
    if not sections and P._text(data.get("text")):
        sections.append({"kind": "body", "text": " ".join(str(data["text"]).split())})
    if not sections:
        raise E.ExplError("пустой сценарий: нужны hook, points [{title, body}], cta (или text)")
    text = " ".join(s["text"] for s in sections)
    warnings = []
    if sections[0]["kind"] == "hook" and len(sections[0]["text"]) > HOOK_MAX_CHARS + 8:
        warnings.append(f"крючок звучит около {len(sections[0]['text']) / CPS:.1f} с".replace(".", ",")
                        + ": лучше уложиться в 2 с, первые секунды решают, досмотрят ли ролик")
    if points and not 2 <= len(points) <= 6:
        warnings.append(f"пунктов {len(points)} — лучше 3–5")
    return {"title": P._text(data.get("title"), 100), "sections": sections, "text": text,
            "points": len(points), "chars": len(text), "est_sec": round(len(text) / CPS, 1), "warnings": warnings}


def set_script(store: Any, video_id: int, data: dict, source: str = "agent") -> dict:
    """Сценарий ролика → разделы и текст; биты и сборка сбрасываются (дальше — подбор поз)."""
    video = E.get_video(store, video_id)
    if video["status"] in ("assembling",):
        raise E.ExplError("ролик собирается — дождись сборки")
    script = normalize_script(data or {})
    script["source"] = source
    target = float(video["duration_target"])
    if abs(script["est_sec"] - target) > target * 0.25:
        script["warnings"].append(f"текст ≈ {script['est_sec']:.0f} с при цели {target:.0f} с")
    # Клип и слова прошлой сборки остаются: новая сборка станет его версией и перенесёт правки субтитров.
    fields = {"script": script, "beats": [], "timeline": {}, "title": script["title"] or P._text(video["topic"], 100)}
    # Сравнить и записать: отменённый ролик остаётся отменённым, правка сохранена. Отмена могла прийти, пока
    # писался текст, и сценарий её не перетрёт; продолжают его кнопкой «Продолжить» (resume).
    moving = tuple(s for s in E.VIDEO_STATUSES if s != "cancelled")
    if not E.set_status(store, video_id, "scripted", moving, **fields, error="", approval_id=None):
        E.set_status(store, video_id, "cancelled", ("cancelled",), **fields)
    return E.get_video(store, video_id)


# --------------------------------------------------------------------------- #
# 2. Биты и позы
# --------------------------------------------------------------------------- #


def _est(text: str) -> float:
    return len(text) / CPS


def _clauses(sentence: str) -> list[str]:
    parts = re.split(r"(?<=[,:;])\s+|\s+(?=[—–]\s)", sentence)
    return [p.strip() for p in parts if p.strip()]


def _chunk(sentence: str) -> list[str]:
    if _est(sentence) <= BEAT_MAX_SEC:
        return [sentence]
    out: list[str] = []
    cur = ""
    for clause in _clauses(sentence):
        cand = f"{cur} {clause}".strip()
        if cur and _est(cand) > BEAT_MAX_SEC and _est(cur) >= BEAT_MIN_SEC * 0.8:
            out.append(cur)
            cur = clause
        else:
            cur = cand
    if cur:
        out.append(cur)
    final: list[str] = []
    for piece in out:  # часть всё ещё длинная — по словам пополам
        while _est(piece) > BEAT_MAX_SEC * 1.3:
            words = piece.split()
            half = max(1, len(words) // 2)
            final.append(" ".join(words[:half]))
            piece = " ".join(words[half:])
        final.append(piece)
    return final


def split_beats(script: dict) -> list[dict]:
    """Разделы сценария → биты по 1.5–3 с (оценка по длине текста)."""
    beats: list[dict] = []
    for section in script.get("sections") or []:
        sentences = [s for s in re.split(r"(?<=[.!?…])\s+", section["text"]) if s.strip()]
        first = True
        for sentence in sentences:
            pieces = _chunk(sentence.strip())
            if first and section["kind"] == "point" and len(pieces) == 1:
                # «Ошибка номер один: телефон в кровати.» — как в пилоте: номер (пальцы + плашка) и
                # тема — два бита.
                head, sep, tail = sentence.strip().partition(":")
                if sep and len(head) >= 8 and len(tail.strip()) >= 8:
                    pieces = [head + sep, tail.strip()]
            for piece in pieces:
                beats.append({"text": piece, "section": section["kind"], "point": section.get("n"),
                              "point_start": section.get("n") if (first and section["kind"] == "point") else None})
                first = False
    merged: list[dict] = []
    for beat in beats:
        prev = merged[-1] if merged else None
        if (prev and _est(beat["text"]) < BEAT_MERGE_SEC and prev["section"] == beat["section"]
                and prev.get("point") == beat.get("point") and not beat.get("point_start")
                and _est(prev["text"] + " " + beat["text"]) <= BEAT_MAX_SEC + 0.5):
            prev["text"] = f"{prev['text']} {beat['text']}"
            continue
        merged.append(dict(beat))
    for i, beat in enumerate(merged):
        beat["i"] = i
        beat["est_sec"] = round(_est(beat["text"]), 2)
        beat["badge"] = f"#{beat['point_start']}" if beat.get("point_start") else None
    return merged
