"""Объяснялки: план битов — контекст, подбор вставок, правка (из ``app/explainer_gen.py``)."""

from __future__ import annotations

import re
from typing import Any

from app import explainer as E
from app import projects as P
from app.explainer_gen_script import split_beats

MAX_NEW_POSES = 20


def plan_schema() -> dict:
    return {"type": "object", "properties": {
        "beats": {"type": "array", "items": {"type": "object", "properties": {
            "i": {"type": "integer"}, "pose_id": {"type": "integer"}, "new_pose_key": {"type": "string"},
            "chip": {"type": "string"}, "chip_word": {"type": "string"},
            "emphasis": {"type": "array", "items": {"type": "string"}}},
            "required": ["i", "pose_id"]}},
        "new_poses": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "title": {"type": "string"}, "description": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}}, "emotion": {"type": "string"},
            "number_shown": {"type": "integer"}}, "required": ["key", "title", "description", "tags"]}}},
        "required": ["beats"]}


def _library_brief(poses: list[dict]) -> list[dict]:
    return [{"id": p["id"], "title": p["title"], "description": p["description"], "tags": p["tags"],
             **({"number_shown": p["number_shown"]} if p.get("number_shown") else {}),
             **({"not_drawn_yet": True} if not p["ready"] else {})} for p in poses]


def plan_context(store: Any, video: dict, beats: list[dict], poses: list[dict]) -> dict:
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    text_all = " ".join(b["text"] for b in beats)
    ranked = [p for p, _s in E.candidates(poses, text_all, k=80)] if len(poses) > 80 else poses
    return {"mascot": {"name": mascot["name"], "what": mascot["identity_card"], "persona": mascot["persona"]},
            "topic": video["topic"], "library": _library_brief(ranked),
            "beats": [{"i": b["i"], "text": b["text"], "section": b["section"],
                       **({"point_start": b["point_start"]} if b.get("point_start") else {}),
                       "candidates": [p["id"] for p, s in E.candidates(poses, b["text"], 6) if s > 0]}
                      for b in beats]}


_CHIP_RE = re.compile(r"\b(\d{1,2}[:.]\d{2})\b|(\d+(?:[.,]\d+)?)\s*(%|час\w*|ч\b|мин\w*|раз\w*|л\b|кг|км|шаг\w*)",
                      re.IGNORECASE)
_UNITS = {"час": "ч", "ч": "ч", "мин": "мин", "раз": "раз", "л": "л", "кг": "кг", "км": "км", "шаг": "шагов",
          "%": "%"}


_ORDINAL_CHIP = re.compile(r"^([№#]?)\s*(\d+)\s*(?:шаг\w*)?$", re.IGNORECASE)


def _not_point_number(chip: dict | None, beat: dict) -> dict | None:
    """Номер пункта уже показывает плашка «#2»: чип «№2» или «2» рядом с ней повторяет то же число. Чипы с единицами
    («10 мин», «1 ч», «07:00») и другие числа остаются."""
    match = _ORDINAL_CHIP.match(str((chip or {}).get("text") or "").strip())
    if not match:
        return chip
    point = beat.get("point_start") or beat.get("point")
    if not point or int(match.group(2)) != int(point):
        return chip
    return None if match.group(1) or beat.get("point_start") else chip


def _fallback_chip(text: str) -> dict | None:
    m = _CHIP_RE.search(text)
    if not m:
        return None
    if m.group(1):
        hh, mm = re.split(r"[:.]", m.group(1))
        return {"text": f"{int(hh):02d}:{mm}", "word": m.group(1)}
    unit = next((v for k, v in _UNITS.items() if m.group(3).lower().startswith(k)), m.group(3))
    sep = "" if unit == "%" else " "
    return {"text": f"{m.group(2)}{sep}{unit}"[:8], "word": m.group(2)}


def _fallback_emphasis(text: str) -> list[str]:
    from app.word_emphasis import key_word_score

    words = [w for w in text.split() if w.strip()]
    scored = sorted(((key_word_score(w), w) for w in words), key=lambda x: -x[0])
    return [scored[0][1]] if scored and scored[0][0] > 0 else []


def match_scores(beats: list[dict], poses: list[dict]) -> list[list[float]]:
    """Очки «бит × поза» по тегам; поза с числом пальцев — только на начало своего пункта."""
    numbers = {p.get("number_shown") for p in poses if p.get("number_shown")}
    out = []
    for beat in beats:
        want = beat.get("point_start")
        row = []
        for pose in poses:
            n = pose.get("number_shown")
            if n:
                row.append(10.0 if want == n else -8.0)
            else:
                row.append(E.score(pose, beat["text"]) - (3.0 if want in numbers else 0.0))
        out.append(row)
    return out


def assign_by_tags(beats: list[dict], poses: list[dict]) -> list[int]:
    """Позы битам по тегам: максимум суммы очков, соседние — разные (Витерби); бит без совпадений —
    наименее занятая в ролике «нейтральная» поза (не повторять одну и ту же)."""
    scores = match_scores(beats, poses)
    n_p = len(poses)
    if n_p == 1:
        return [int(poses[0]["id"])] * len(beats)
    dp = [scores[0][:]]
    back: list[list[int]] = [[-1] * n_p]
    for n in range(1, len(beats)):
        prev = dp[-1]
        order = sorted(range(n_p), key=lambda k: -prev[k])
        best, second = order[0], order[1]
        row, bk = [], []
        for k in range(n_p):
            q = best if best != k else second
            row.append(scores[n][k] + prev[q])
            bk.append(q)
        dp.append(row)
        back.append(bk)
    k = max(range(n_p), key=lambda j: dp[-1][j])
    picks = [k]
    for n in range(len(beats) - 1, 0, -1):
        k = back[n][k]
        picks.append(k)
    picks.reverse()
    count: dict[int, int] = {}
    for k in picks:
        count[k] = count.get(k, 0) + 1
    for n, k in enumerate(picks):
        if scores[n][k] > 0:
            continue
        neighbors = {picks[n - 1] if n else -1, picks[n + 1] if n + 1 < len(picks) else -1}
        pool = [j for j in range(n_p) if j not in neighbors and not poses[j].get("number_shown")]
        if not pool:
            continue
        j = min(pool, key=lambda x: (count.get(x, 0), -scores[n][x], int(poses[x].get("uses") or 0), x))
        count[k] -= 1
        count[j] = count.get(j, 0) + 1
        picks[n] = j
    return [int(poses[k]["id"]) for k in picks]


def _assign_fallback(beats: list[dict], poses: list[dict], dry: bool = False) -> list[str]:
    """Без Gemini: позы по тегам (``assign_by_tags``), без новых поз; чипы и ключевые слова — эвристикой."""
    ready = [p for p in poses if E.pose_usable(p, dry)] or poses
    if not ready:
        raise E.ExplError("библиотека поз пуста, а Gemini недоступен — опиши позы сами: explainer_update_beats "
                          "(new_pose у каждого бита) или добавь позы (explainer_add_pose)")
    notes = ["Gemini недоступен — позы подобраны по тегам библиотеки, новых нет; биты можно поправить"]
    for beat, pose_id in zip(beats, assign_by_tags(beats, ready)):
        beat["pose_id"] = pose_id
        beat["chip"] = _not_point_number(_fallback_chip(beat["text"]), beat)
        beat["emphasis"] = _fallback_emphasis(beat["text"])
    return notes


def _chip(value: Any, word: Any = None) -> dict | None:
    """Чип бита: строка или ``{text, word}`` (так его отдаёт GET ролика) → ``{text, word}``; мусор — без чипа.
    Строка-словарь «{'text': '…» — след старой правки: в кадр она не идёт."""
    if isinstance(value, dict):
        word = word or value.get("word")
        value = value.get("text")
    text = P._text(value, 10) if isinstance(value, str) else ""
    if not text or text[0] in "{[":
        return None
    return {"text": text, "word": P._text(word, 40) if isinstance(word, str) else ""}


def _badge(value: Any) -> str | None:
    text = P._text(value, 6) if isinstance(value, str) else ""
    return text if text and text[0] not in "{[" else None


def _words_of(value: Any, limit: int) -> list[str]:
    return [P._text(w, 40) for w in value or [] if isinstance(w, str) and P._text(w)][:limit] if isinstance(
        value, list) else []


def _apply_gemini_plan(store: Any, mascot: dict, beats: list[dict], poses: list[dict], data: dict,
                       dry: bool = False) -> list[str]:
    notes: list[str] = []
    by_id = {int(p["id"]): p for p in poses}
    new_specs = {}
    for spec in data.get("new_poses") or []:
        if isinstance(spec, dict) and P._text(spec.get("key")) and P._text(spec.get("description")):
            new_specs[P._text(spec["key"], 40)] = spec
    created: dict[str, int] = {}
    reused_similar: dict[str, int] = {}
    answers = {int(b.get("i", -1)): b for b in data.get("beats") or [] if isinstance(b, dict)}
    library = list(poses)
    for beat in beats:
        ans = answers.get(int(beat["i"])) or {}
        pid = int(ans.get("pose_id") or 0)
        if pid and pid not in by_id:
            notes.append(f"бит {beat['i']}: позы {pid} нет в библиотеке — подобрал по тегам")
            pid = 0
        key = P._text(ans.get("new_pose_key"), 40)
        if not pid and key and key in new_specs:
            if key in created:
                pid = created[key]
            else:
                spec = new_specs[key]
                similar, sim = E.similar_pose(library, spec["description"], spec.get("tags"), spec.get("number_shown"))
                if similar is not None:
                    pid = int(similar["id"])
                    reused_similar[key] = pid
                    notes.append(f"новая поза «{P._text(spec.get('title') or spec['description'], 50)}» ≈ готовой "
                                 f"#{pid} «{similar['title']}» (сходство {sim}) — беру готовую")
                elif len(created) >= MAX_NEW_POSES:
                    notes.append(f"бит {beat['i']}: больше {MAX_NEW_POSES} новых поз не рисую — подобрал по тегам")
                else:
                    pose = E.save_pose(store, int(mascot["id"]), description=spec["description"],
                                       title=spec.get("title") or "", tags=spec.get("tags") or [],
                                       emotion=spec.get("emotion") or "", number_shown=spec.get("number_shown"),
                                       origin="generated")
                    library.append(pose)
                    by_id[int(pose["id"])] = pose
                    pid = int(pose["id"])
                created[key] = pid
        if not pid:
            ranked = [p for p, _s in E.candidates([p for p in library if E.pose_usable(p, dry)] or library,
                                                  beat["text"], 4)]
            pid = int(ranked[0]["id"]) if ranked else 0
        if not pid:
            raise E.ExplError(f"бит {beat['i']}: нет позы — библиотека пуста")
        beat["pose_id"] = pid
        beat["chip"] = _not_point_number(_chip(ans.get("chip"), ans.get("chip_word")), beat)
        beat["emphasis"] = _words_of(ans.get("emphasis"), 2)
    return notes


def _unrepeat(beats: list[dict], poses: list[dict]) -> None:
    """Две одинаковые позы подряд: вторую — на следующего кандидата; не нашлось — наезд (punch)."""
    ready = [p for p in poses]
    for n in range(1, len(beats)):
        if beats[n]["pose_id"] != beats[n - 1]["pose_id"]:
            continue
        nxt = beats[n + 1]["pose_id"] if n + 1 < len(beats) else None
        alt = next((p for p, s in E.candidates(ready, beats[n]["text"], 6)
                    if s > 0 and p["id"] not in (beats[n - 1]["pose_id"], nxt) and not p.get("number_shown")), None)
        if alt is not None:
            beats[n]["pose_id"] = int(alt["id"])
        else:
            beats[n]["variant"] = "punch"


def update_beats(store: Any, video_id: int, changes: list[dict]) -> dict:
    """Агент/владелец правит биты до сборки: ``[{i, pose_id? | new_pose {description, title, tags,
    number_shown}, chip? (строка или {text, word}, как в ответе GET), chip_word?, emphasis?, badge?}]`` →
    ролик снова «планируется» (новая оценка). Биты можно вернуть как получены."""
    video = E.get_video(store, video_id)
    if video["status"] in ("assembling",):
        raise E.ExplError("ролик собирается — дождись сборки")
    beats = video.get("beats") or []
    if not beats:
        if not E.narration_text(video):
            raise E.ExplError("сначала сценарий (explainer_set_script)")
        beats = split_beats(video["script"])
        for b in beats:
            b["pose_id"] = 0
    mascot = E.get_mascot(store, int(video["mascot_id"]))
    library = E.list_poses(store, int(mascot["id"]))
    by_i = {int(b["i"]): b for b in beats}
    problems = []
    for ch in changes or []:
        if not isinstance(ch, dict) or ch.get("i") is None or int(ch["i"]) not in by_i:
            problems.append(f"нет бита {ch.get('i') if isinstance(ch, dict) else ch!r} (0–{len(beats) - 1})")
            continue
        beat = by_i[int(ch["i"])]
        if ch.get("pose_id"):
            pose = E.get_pose(store, int(ch["pose_id"]))
            if int(pose["mascot_id"]) != int(mascot["id"]):
                problems.append(f"бит {beat['i']}: поза {pose['id']} другого маскота")
                continue
            beat["pose_id"] = int(pose["id"])
        elif isinstance(ch.get("new_pose"), dict):
            spec = ch["new_pose"]
            similar, _sim = E.similar_pose(library, str(spec.get("description") or ""), spec.get("tags"),
                                           spec.get("number_shown"))
            pose = similar or E.save_pose(store, int(mascot["id"]), description=spec.get("description"),
                                          title=spec.get("title") or "", tags=spec.get("tags") or [],
                                          emotion=spec.get("emotion") or "", number_shown=spec.get("number_shown"),
                                          origin="agent")
            if similar is None:
                library.append(pose)
            beat["pose_id"] = int(pose["id"])
        if "chip" in ch:
            beat["chip"] = _chip(ch.get("chip"), ch.get("chip_word"))
        if "emphasis" in ch:
            beat["emphasis"] = _words_of(ch.get("emphasis"), 3)
        if "badge" in ch:
            beat["badge"] = _badge(ch.get("badge"))
    if problems:
        raise E.ExplError("биты не приняты: " + "; ".join(problems))
    if any(not b.get("pose_id") for b in beats):
        raise E.ExplError("у каждого бита должна быть поза (pose_id или new_pose)")
    poses = {int(p["id"]): p for p in E.list_poses(store, int(mascot["id"]))}
    dry = E.is_dry(video)
    for beat in beats:
        pose = poses.get(int(beat["pose_id"]))
        beat["pose_title"] = pose["title"] if pose else ""
        beat["pose_new"] = bool(pose and not E.pose_usable(pose, dry))
    # Клип и слова прошлой сборки остаются: новая сборка станет его версией и перенесёт правки субтитров.
    E.set_status(store, video_id, "planned", None, beats=beats, error="", timeline={})
    return E.get_video(store, video_id)
