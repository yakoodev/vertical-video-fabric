"""ИИ-видео: сценарии и планы (из ``app/aivideo.py``)."""

from __future__ import annotations

from typing import Any

from app import projects as P
from app.aivideo_cast import (
    QUALITIES, AivError, _json, slugify, require_project, _style_row, character_by_slug, list_characters,
    location_by_slug, list_locations, _shot_from_row
)

SCRIPT_STATUSES = ("draft", "approved", "producing", "assembled")
CAMERA_MOVES = ("static", "push_in", "pull_out", "pan_left", "pan_right", "tilt_up", "tilt_down",
                "parallax_push", "orbit", "float", "dolly_zoom", "tilt_reveal")
CAMERA_MOVE_ALIASES = {"push": "push_in", "zoom_in": "push_in", "zoom_out": "pull_out", "pull": "pull_out",
                       "pan": "pan_right", "still": "static", "none": "static", "hold": "static",
                       "parallax": "parallax_push", "arc": "orbit", "breathe": "float", "drift": "float",
                       "vertigo": "dolly_zoom", "reveal": "tilt_reveal", "tilt": "tilt_up"}
SHOT_SIZES = ("extreme_wide", "wide", "full", "medium", "medium_close", "close", "extreme_close")
TRANSITIONS = ("cut", "fade")
SHOT_MIN_SEC = 2.0
SHOT_MAX_SEC = 8.0
MAX_SHOTS = 20
MAX_CAST = 2  # больше двух персонажей в кадре — «другой человек» (риск постоянства)
MAX_LINES_PER_SHOT = 4
CHARS_PER_SEC = 14.0  # как DRY_CHARS_PER_SEC в dubbing: столько символов русской речи в секунду


# --------------------------------------------------------------------------- #
# Серии и планы
# --------------------------------------------------------------------------- #


def get_script(store: Any, script_id: int, with_shots: bool = True) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_scripts WHERE id = ?", (int(script_id),))
    if not row:
        raise KeyError(f"нет серии {script_id}")
    data = dict(row)
    if not data.get("quality"):
        data["quality"] = _style_row(store, data["project_id"])["quality"]
    if with_shots:
        data["shots"] = list_shots(store, script_id)
        data["duration_total"] = round(sum(float(s["duration"]) for s in data["shots"]), 2)
    return data


def list_scripts(store: Any, project_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT id FROM aiv_scripts WHERE project_id = ? ORDER BY episode_no, id",
                              (int(project_id),))
    out = []
    for r in rows:
        script = get_script(store, int(r["id"]), with_shots=True)
        script["shots_count"] = len(script.pop("shots"))
        out.append(script)
    return out


def create_script(store: Any, project_id: int, brief: str = "", title: str = "", episode_no: int | None = None,
                  duration_target: Any = None, quality: str | None = None) -> dict:
    require_project(store, project_id)
    if episode_no in (None, "", 0):
        row = store.db.query_one("SELECT MAX(episode_no) AS n FROM aiv_scripts WHERE project_id = ?",
                                 (int(project_id),))
        episode_no = int(row["n"] or 0) + 1 if row else 1
    target = _duration_target(duration_target)
    cur = store.db.execute(
        "INSERT INTO aiv_scripts (project_id, episode_no, title, brief, duration_target, quality) VALUES (?, ?, ?, ?, ?, ?)",
        (int(project_id), int(episode_no), P._text(title, 120) or f"Серия {episode_no}", P._long_text(brief, 3000),
         target, P._choice(quality, QUALITIES, "quality") if quality else ""))
    return get_script(store, int(cur.lastrowid))


def _duration_target(value: Any) -> float:
    try:
        target = float(value) if value not in (None, "") else 30.0
    except (TypeError, ValueError):
        target = 30.0
    return round(min(120.0, max(10.0, target)), 1)


def list_shots(store: Any, script_id: int) -> list[dict]:
    rows = store.db.query_all("SELECT * FROM aiv_shots WHERE script_id = ? ORDER BY idx, id", (int(script_id),))
    return [_shot_from_row(r) for r in rows]


# --------------------------------------------------------------------------- #
# Постоянство склеек: состояние на начало плана
# --------------------------------------------------------------------------- #

STATE_FIELDS = ("pose", "facing", "position", "holding", "clothing")
STATE_LABELS = {"pose": "поза", "facing": "смотрит", "position": "в кадре", "holding": "в руках",
                "clothing": "одежда"}
# Что не должно меняться между планами одной сцены, если действие этого не показывает.
STATE_WATCH = ("pose", "facing", "holding", "clothing")
MAX_PROPS = 6


def normalize_state(raw: Any, cast_ids: list[str], idx: int, warnings: list[str]) -> dict:
    """Состояние на начало плана: ``{characters: [{id, pose, facing, position, holding, clothing}],
    props: [{name, state}]}``. Принимает и словари ``{mira: {...}}`` / ``{носок: "снят"}``."""
    if not isinstance(raw, dict):
        return {}
    chars_raw = raw.get("characters") or []
    if isinstance(chars_raw, dict):
        chars_raw = [{"id": k, **(v if isinstance(v, dict) else {})} for k, v in chars_raw.items()]
    chars = []
    for item in chars_raw if isinstance(chars_raw, list) else []:
        if not isinstance(item, dict):
            continue
        cid = slugify(item.get("id") or item.get("character") or "", "")
        if not cid:
            continue
        if cid not in cast_ids:
            warnings.append(f"план {idx}: в state есть {cid!r}, а в кадре его нет — убран")
            continue
        entry = {"id": cid}
        for key in STATE_FIELDS:
            text = P._text(item.get(key), 80)
            if text:
                entry[key] = text
        if len(entry) > 1 and cid not in {c["id"] for c in chars}:
            chars.append(entry)
    props_raw = raw.get("props") or []
    if isinstance(props_raw, dict):
        props_raw = [{"name": k, "state": v} for k, v in props_raw.items()]
    props = []
    for item in props_raw if isinstance(props_raw, list) else []:
        if isinstance(item, str):
            item = {"name": item, "state": ""}
        if not isinstance(item, dict):
            continue
        name = P._text(item.get("name") or item.get("prop"), 60)
        if name:
            prop = {"name": name, "state": P._text(item.get("state"), 120)}
            try:
                count = int(item.get("count") or 0)
            except (TypeError, ValueError):
                count = 0
            if count > 0:
                prop["count"] = min(9, count)
            props.append(prop)
    out: dict = {}
    if chars:
        out["characters"] = chars
    if props:
        out["props"] = props[:MAX_PROPS]
    return out


def state_text(store: Any, project_id: int, state: dict) -> str:
    """Состояние плана — строка для промпта: «Мира: поза sitting; смотрит camera…; реквизит: носок — снят»."""
    parts = []
    for item in (state or {}).get("characters") or []:
        c = character_by_slug(store, project_id, item.get("id") or "")
        name = c["name"] if c else item.get("id")
        bits = [f"{key} {item[key]}" for key in STATE_FIELDS if item.get(key)]
        if bits:
            parts.append(f"{name}: " + ", ".join(bits))
    props = [(f"exactly {p.get('count') or 1} × {p['name']}") + (f" — {p['state']}" if p.get("state") else "")
             for p in (state or {}).get("props") or []]
    if props:
        parts.append("props: " + "; ".join(props))
    return "; ".join(parts)


def prop_counts(state: dict) -> str:
    """«exactly one cookie; exactly 2 × socks» — счёт реквизита для промпта и проверки видео."""
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}
    out = []
    for p in (state or {}).get("props") or []:
        n = int(p.get("count") or 1)
        out.append(f"exactly {words.get(n, n)} {p['name']}")
    return "; ".join(out)


def continuity_warnings(shots: list[dict]) -> list[str]:
    """Сцена продолжается, а поза/взгляд/руки/одежда/реквизит сменились — склейка прыгнет, если
    действие прошлого плана этого не показывает. Подсказка сценаристу, не ошибка."""
    out = []
    for prev, shot in zip(shots, shots[1:]):
        if not shot.get("continues_from_prev"):
            continue
        before = {c["id"]: c for c in (prev.get("state") or {}).get("characters") or []}
        for c in (shot.get("state") or {}).get("characters") or []:
            old = before.get(c["id"])
            if not old:
                continue
            for key in STATE_WATCH:
                if old.get(key) and c.get(key) and old[key].strip().lower() != c[key].strip().lower():
                    out.append(f"план {shot['idx']}: {c['id']} {STATE_LABELS[key]} «{old[key]}» → «{c[key]}» — "
                               f"действие плана {prev['idx']} должно это показать, иначе склейка прыгнет")
        props_before = {p["name"].lower(): p.get("state") or "" for p in (prev.get("state") or {}).get("props") or []}
        for p in (shot.get("state") or {}).get("props") or []:
            old = props_before.get(p["name"].lower())
            if old is not None and old and p.get("state") and old.lower() != p["state"].lower():
                out.append(f"план {shot['idx']}: {p['name']} «{old}» → «{p['state']}» — покажи это в плане {prev['idx']}")
        # Конец прошлого плана (end_state) — это старт этого: расхождение = скачок на склейке.
        ends = {c["id"]: c for c in (prev.get("end_state") or {}).get("characters") or []}
        for c in (shot.get("state") or {}).get("characters") or []:
            end = ends.get(c["id"])
            for key in STATE_WATCH if end else ():
                if end.get(key) and c.get(key) and end[key].strip().lower() != c[key].strip().lower():
                    out.append(f"план {shot['idx']}: {c['id']} {STATE_LABELS[key]} на старте «{c[key]}», а конец "
                               f"плана {prev['idx']} — «{end[key]}»: старт продолжения = конец прошлого (общий кадр)")
    return out


def end_state_of(shot: dict, nxt: dict | None) -> dict:
    """Состояние на КОНЕЦ плана: у сцепленного следующего плана его старт и есть этот конец
    (общий ключевой кадр); иначе — свой ``end_state``, нет и его — старт плана."""
    if nxt and nxt.get("continues_from_prev") and (nxt.get("state") or {}):
        return nxt["state"]
    return shot.get("end_state") or shot.get("state") or {}


def get_shot(store: Any, shot_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_shots WHERE id = ?", (int(shot_id),))
    if not row:
        raise KeyError(f"нет плана {shot_id}")
    return _shot_from_row(row)


def camera_move(value: Any) -> str:
    name = str(value or "static").strip().lower().replace("-", "_").replace(" ", "_")
    name = CAMERA_MOVE_ALIASES.get(name, name)
    if name not in CAMERA_MOVES:
        raise AivError(f"camera.move {value!r}: одно из {', '.join(CAMERA_MOVES)}")
    return name


def normalize_shot(store: Any, project_id: int, raw: dict, idx: int, warnings: list[str]) -> dict:
    """План из сценария/правки → поля строки. Неизвестный персонаж/локация — ошибка с подсказкой."""
    if not isinstance(raw, dict):
        raise AivError(f"план {idx}: нужен объект")
    try:
        duration = float(raw.get("duration") or 4)
    except (TypeError, ValueError) as exc:
        raise AivError(f"план {idx}: duration — число секунд") from exc
    if not SHOT_MIN_SEC <= duration <= SHOT_MAX_SEC:
        warnings.append(f"план {idx}: длительность {duration:g} с прижата к {SHOT_MIN_SEC:g}–{SHOT_MAX_SEC:g} с")
        duration = min(SHOT_MAX_SEC, max(SHOT_MIN_SEC, duration))
    location = slugify(raw.get("location") or "", "") if raw.get("location") else ""
    if location and not location_by_slug(store, project_id, location):
        known = ", ".join(l["slug"] for l in list_locations(store, project_id)) or "нет ни одной"
        raise AivError(f"план {idx}: нет локации {location!r} (есть: {known}) — aiv_define_location")
    cast = []
    for item in (raw.get("characters") or raw.get("cast") or [])[:MAX_CAST + 3]:
        if isinstance(item, str):
            item = {"id": item}
        if not isinstance(item, dict):
            continue
        slug = slugify(item.get("id") or item.get("character") or item.get("slug") or "", "")
        character = character_by_slug(store, project_id, slug) if slug else None
        if character is None:
            known = ", ".join(c["slug"] for c in list_characters(store, project_id)) or "нет ни одного"
            raise AivError(f"план {idx}: нет персонажа {slug!r} (есть: {known}) — aiv_define_character")
        look = slugify(item.get("look") or "", "") if item.get("look") else ""
        if look and look not in {l["slug"] for l in character["looks"]}:
            warnings.append(f"план {idx}: у {slug} нет облика {look!r} — будет канон (aiv_add_look)")
            look = ""
        cast.append({"id": slug, "look": look, "action": P._text(item.get("action"), 200)})
    if len(cast) > MAX_CAST:
        warnings.append(f"план {idx}: в кадре больше {MAX_CAST} персонажей — оставлены первые {MAX_CAST} "
                        "(толпа — главный источник «другого человека»)")
        cast = cast[:MAX_CAST]
    cam = raw.get("camera") if isinstance(raw.get("camera"), dict) else {"move": raw.get("camera")}
    camera = {"move": camera_move(cam.get("move"))}
    size = str(cam.get("shot") or cam.get("size") or "").strip().lower()
    if size:
        camera["shot"] = size if size in SHOT_SIZES else "medium"
    if cam.get("angle"):
        camera["angle"] = P._text(cam.get("angle"), 30)
    try:
        if cam.get("lens"):
            camera["lens"] = int(min(135, max(14, float(cam["lens"]))))
    except (TypeError, ValueError):
        pass
    speakers = {c["id"] for c in cast}
    dialogue = []
    for line in (raw.get("dialogue") or [])[:MAX_LINES_PER_SHOT]:
        if not isinstance(line, dict):
            continue
        text = P._text(line.get("text"), 300)
        if not text:
            continue
        who = slugify(line.get("who") or line.get("speaker") or "", "")
        speaker = character_by_slug(store, project_id, who) if who and who != "narrator" else None
        if who and who != "narrator" and not speaker:
            raise AivError(f"план {idx}: реплику говорит неизвестный {who!r}")
        if speaker and speaker["speech"] == "none":
            warnings.append(f"план {idx}: {who} молчит (speech none) — реплика «{text[:40]}» убрана")
            continue
        if who and who != "narrator" and who not in speakers:
            warnings.append(f"план {idx}: {who} говорит за кадром (его нет в characters плана)")
        dialogue.append({"who": who or "narrator", "text": text, "emotion": P._text(line.get("emotion"), 40)})
    chars = sum(len(d["text"]) for d in dialogue if not _is_sfx(store, project_id, d["who"]))
    if chars and chars / CHARS_PER_SEC > duration + 0.5:
        warnings.append(f"план {idx}: реплик на {chars / CHARS_PER_SEC:.1f} с при плане {duration:g} с — "
                        "при сборке план удлинится под озвучку (или сократи текст)")
    transition = str(raw.get("transition_in") or "cut").strip().lower()
    video_in, video_out = _window(raw.get("video_in")), _window(raw.get("video_out"))
    if video_in is not None and video_out is not None and video_out - video_in < 0.5:
        warnings.append(f"план {idx}: окно видео {video_in:g}–{video_out:g} с короче 0.5 с — сброшено")
        video_in = video_out = None
    return {
        "idx": idx, "duration": round(duration, 2), "location": location,
        "cast_json": _json(cast), "camera_json": _json(camera), "dialogue_json": _json(dialogue),
        "action": P._text(raw.get("action"), 400), "image_prompt": P._long_text(raw.get("image_prompt"), 800),
        "motion_prompt": P._text(raw.get("motion_prompt"), 400), "needs_motion": int(bool(raw.get("needs_motion"))),
        "lipsync": int(bool(raw.get("lipsync"))),
        "transition_in": transition if transition in TRANSITIONS else "cut",
        "sound": P._text(raw.get("sound"), 300), "video_in": video_in, "video_out": video_out,
        "state_json": _json(normalize_state(raw.get("state"), [c["id"] for c in cast], idx, warnings)),
        "end_state_json": _json(normalize_state(raw.get("end_state"), [c["id"] for c in cast], idx, warnings)),
        # None — не задано: решает replace_shots (по умолчанию да, если локация та же).
        "continues_from_prev": None if raw.get("continues_from_prev") is None else int(bool(raw["continues_from_prev"])),
    }


def _window(value: Any) -> float | None:
    """Секунда файла видео плана (окно сборки); пусто/мусор — None (всё видео)."""
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(min(30.0, max(0.0, number)), 3)
def _is_sfx(store: Any, project_id: int, who: str) -> bool:
    c = character_by_slug(store, project_id, who) if who and who != "narrator" else None
    return bool(c and c["speech"] == "sfx")


def replace_shots(store: Any, script_id: int, shots: list, title: str | None = None, logline: str | None = None) -> dict:
    """Весь шот-лист заново (сценарий Gemini или правка агентом). Картинки старых планов остаются в
    библиотеке, но к новым планам не привязаны."""
    script = get_script(store, script_id, with_shots=False)
    if not isinstance(shots, list) or not shots:
        raise AivError("нужен непустой список shots")
    if len(shots) > MAX_SHOTS:
        raise AivError(f"планов {len(shots)} — не больше {MAX_SHOTS}")
    warnings: list[str] = []
    rows = [normalize_shot(store, script["project_id"], raw, i + 1, warnings) for i, raw in enumerate(shots)]
    for i, row in enumerate(rows):
        if i == 0:
            row["continues_from_prev"] = 0  # первому плану продолжать нечего
        elif row["continues_from_prev"] is None:
            row["continues_from_prev"] = int(bool(row["location"]) and row["location"] == rows[i - 1]["location"])
    old =[int(r["id"]) for r in store.db.query_all("SELECT id FROM aiv_shots WHERE script_id = ?", (int(script_id),))]
    for shot_id in old:
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type IN ('shot', 'line') AND target_id = ?",
                         (shot_id,))
    store.db.execute("DELETE FROM aiv_shots WHERE script_id = ?", (int(script_id),))
    for row in rows:
        cols = ", ".join(row)
        store.db.execute(f"INSERT INTO aiv_shots (script_id, {cols}) VALUES (?, {', '.join('?' for _ in row)})",
                         (int(script_id), *row.values()))
    sets = {"rev": int(script["rev"]) + 1}
    if title is not None and P._text(title, 120):
        sets["title"] = P._text(title, 120)
    if logline is not None:
        sets["logline"] = P._text(logline, 400)
    cols = ", ".join(f"{k} = ?" for k in sets)
    store.db.execute(f"UPDATE aiv_scripts SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*sets.values(), int(script_id)))
    out = get_script(store, script_id)
    total = out["duration_total"]
    if abs(total - float(script["duration_target"])) > max(5.0, 0.25 * float(script["duration_target"])):
        warnings.append(f"сумма планов {total:g} с против цели {script['duration_target']:g} с")
    warnings += continuity_warnings(out["shots"])
    out["warnings"] = warnings
    return out


def update_script(store: Any, script_id: int, patch: dict) -> dict:
    script = get_script(store, script_id, with_shots=False)
    if isinstance(patch.get("shots"), list):
        return replace_shots(store, script_id, patch["shots"], patch.get("title"), patch.get("logline"))
    sets: dict[str, Any] = {}
    if patch.get("title"):
        sets["title"] = P._text(patch["title"], 120)
    if "logline" in patch:
        sets["logline"] = P._text(patch["logline"], 400)
    if "brief" in patch:
        sets["brief"] = P._long_text(patch["brief"], 3000)
    if "duration_target" in patch:
        sets["duration_target"] = _duration_target(patch["duration_target"])
    if patch.get("quality"):
        sets["quality"] = P._choice(patch["quality"], QUALITIES, "quality")
    if patch.get("status"):
        sets["status"] = P._choice(patch["status"], SCRIPT_STATUSES, "status")
    if sets:
        cols = ", ".join(f"{k} = ?" for k in sets)
        store.db.execute(f"UPDATE aiv_scripts SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                         (*sets.values(), int(script["id"])))
    return get_script(store, script_id)


def update_shot(store: Any, shot_id: int, patch: dict) -> dict:
    """Правка одного плана (поля как в шот-листе). Смена кадра/персонажей/локации — кадр «устарел»."""
    shot = get_shot(store, shot_id)
    script = get_script(store, shot["script_id"], with_shots=False)
    merged = {
        "duration": shot["duration"], "location": shot["location"], "characters": shot["cast"],
        "camera": shot["camera"], "dialogue": shot["dialogue"], "action": shot["action"],
        "image_prompt": shot["image_prompt"], "motion_prompt": shot["motion_prompt"],
        "needs_motion": shot["needs_motion"], "lipsync": shot["lipsync"], "transition_in": shot["transition_in"],
        "sound": shot.get("sound") or "", "video_in": shot.get("video_in"), "video_out": shot.get("video_out"),
        "state": shot.get("state") or {}, "continues_from_prev": shot.get("continues_from_prev", False),
        "end_state": shot.get("end_state") or {},
    }
    for key, value in (patch or {}).items():
        if key == "cast":
            key = "characters"
        if key in merged:
            merged[key] = value
    warnings: list[str] = []
    row = normalize_shot(store, script["project_id"], merged, int(shot["idx"]), warnings)
    row.pop("idx")
    if row["continues_from_prev"] is None:
        row["continues_from_prev"] = int(bool(shot.get("continues_from_prev")))
    if int(shot["idx"]) == 1:
        row["continues_from_prev"] = 0
    visual = any(row[k] != old for k, old in (
        ("location", shot["location"]), ("cast_json", _json(shot["cast"])), ("action", shot["action"]),
        ("image_prompt", shot["image_prompt"]), ("camera_json", _json(shot["camera"])),
        ("state_json", _json(shot.get("state") or {})),
        ("continues_from_prev", int(bool(shot.get("continues_from_prev"))))))
    if "locked" in (patch or {}):
        row["locked"] = int(bool(patch["locked"]))
    cols = ", ".join(f"{k} = ?" for k in row)
    store.db.execute(f"UPDATE aiv_shots SET {cols}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                     (*row.values(), int(shot_id)))
    motion = any(row[k] != (shot.get(k) or "") for k in ("motion_prompt", "sound"))
    end_changed = row["end_state_json"] != _json(shot.get("end_state") or {})
    if visual:
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ?", (int(shot_id),))
    elif end_changed:  # конец плана другой — его конечный кадр, блокинг и видео
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                         "AND kind IN ('endframe', 'blocking_end', 'shot_video')", (int(shot_id),))
    elif motion:
        store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                         "AND kind = 'shot_video'", (int(shot_id),))
    if row["state_json"] != _json(shot.get("state") or {}) and row["continues_from_prev"]:
        # Старт сцепленного плана = конец прошлого: его конечный кадр и видео устарели.
        prev = store.db.query_one("SELECT id FROM aiv_shots WHERE script_id = ? AND idx < ? ORDER BY idx DESC LIMIT 1",
                                  (int(shot["script_id"]), int(shot["idx"])))
        if prev:
            store.db.execute("UPDATE aiv_refs SET stale = 1 WHERE target_type = 'shot' AND target_id = ? "
                             "AND kind IN ('endframe', 'blocking_end', 'shot_video')", (int(prev["id"]),))
    out = get_shot(store, shot_id)
    out["warnings"] = warnings
    return out


def delete_script(store: Any, script_id: int) -> dict:
    get_script(store, script_id, with_shots=False)
    for r in store.db.query_all("SELECT id FROM aiv_shots WHERE script_id = ?", (int(script_id),)):
        store.db.execute("DELETE FROM aiv_refs WHERE target_type IN ('shot', 'line') AND target_id = ?", (int(r["id"]),))
    store.db.execute("DELETE FROM aiv_scripts WHERE id = ?", (int(script_id),))
    return {"ok": True, "deleted": int(script_id)}
