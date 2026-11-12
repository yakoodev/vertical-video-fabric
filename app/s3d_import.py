"""Перенос готовых 3D-историй в демо-проект «3D-серия»: планы VRM-историй Витубики (``tools/vtubika_plans/*.json``) и
мем-пилот (``tools/meme3d_pilot.py``) — примерами серий. Запуск: ``python tools/series3d_import.py`` (владелец, на
своей базе); повтор не дублирует серии (по файлу-источнику).

Как план Витубики ложится на план серии:

* ``vrm.heroine`` → ``cast[0]`` (готовый персонаж библиотеки), ``vrm.avatars`` → остальные ``cast`` (на мониторе);
  нет поля ``vrm`` (воксельная история) — героиня ``alina``;
* ``sentences[]`` → ``lines[]``: ``text``, ``say``, ``pause_after``, ``show``; голос — рассказчик (``narrator``);
* ``shots[]`` → кадры: ``cam`` → ``camera`` (неизвестный VRM-ракурс → ``face``), ``push`` → ``move: slow_push`` с
  ``strength`` (без наезда — ``static``), ``roll``, ``at``; ``sketch``/``zoom``/``fov`` — ``extra`` как есть;
* ``do[]``: сообщение чата ``chat`` → событие ``comment``, ``sfx`` → ``sfx``, ``viewers`` → ``viewers``, ``screen`` →
  ``screen``; остальное (действия героини, монитор, телефон, копилка, HUD, карточки) и ``vrm[]`` — в
  ``lines[].vtubika`` как есть: движок исполняет их так же, как в истории;
* ``highlight`` → ``captions.highlight``, ``pronounce``, ``screen``, ``presets3d`` → ``presets``, ``pace.lead`` →
  ``lead``, ``tail``; голос и музыка истории — в ``source`` (по умолчанию серия озвучивается бесплатно).
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from app import s3d_plan as SP
from app import s3d_store as D

ROOT = Path(__file__).resolve().parent.parent
DEMO_NAME = "3D-серия: примеры (Витубика и мем-пилот)"
TYPED = {"chat": "comment", "sfx": "sfx", "viewers": "viewers", "screen": "screen"}
TIMING = ("at", "dt", "dur", "until", "span", "dt_end")


def _shot(sh: dict) -> dict:
    cam = sh.get("cam") if sh.get("cam") in SP.VRM_FRAMINGS else "face"
    push = float(sh.get("push", 0.15) or 0)
    out: dict[str, Any] = {"camera": cam, "move": "slow_push" if push > 0.04 else "static"}
    if push > 0.04:
        out["strength"] = round(min(1.5, max(0.2, push / 0.2)), 2)
    if sh.get("roll"):
        out["roll"] = float(sh["roll"])
    if sh.get("at"):
        out["at"] = str(sh["at"])
    extra = {k: sh[k] for k in ("sketch", "zoom", "fov", "dof", "shift") if sh.get(k) is not None}
    if extra:
        out["extra"] = extra
    return out


def _typed(ev: dict) -> dict | None:
    """Событие do, которое целиком ложится на событие серии (только своё поле и время), иначе None."""
    from app import vtubika as U

    keys = [k for k in ev if k not in TIMING]
    if len(keys) != 1 or keys[0] not in TYPED:
        if set(keys) == {"viewers", "label"}:
            keys = ["viewers"]
        else:
            return None
    key = keys[0]
    timing = {k: ev[k] for k in TIMING if k in ev}
    if key == "chat" and isinstance(ev["chat"], dict) and ev["chat"].get("user") and ev["chat"].get("text"):
        return {"kind": "comment", **{k: ev["chat"][k] for k in ("user", "text", "color", "big") if k in ev["chat"]}, **timing}
    if key == "sfx" and ev["sfx"] in U.SFX:
        return {"kind": "sfx", "sound": ev["sfx"], **timing}
    if key == "viewers":
        return {"kind": "viewers", "value": int(ev["viewers"]), **({"label": ev["label"]} if ev.get("label") else {}), **timing}
    if key == "screen" and ev["screen"] in SP.SCREEN_MODES:
        return {"kind": "screen", "mode": ev["screen"], **timing}
    return None


def vtubika_to_plan(vplan: dict, file: str = "") -> dict:
    """План истории Витубики → план серии (движок vrm_desk)."""
    cfg = vplan.get("vrm") or {}
    cast = [cfg.get("heroine") or "alina", *[a for a in cfg.get("avatars") or []]]
    lines = []
    for n, s in enumerate(vplan.get("sentences") or []):
        events, raw_do = [], []
        for ev in s.get("do") or []:
            typed = _typed(ev)
            if typed is not None:
                events.append(typed)
            else:
                raw_do.append(ev)
        line: dict[str, Any] = {"id": f"l{n + 1}", "who": "narrator", "text": s["text"],
                                "pause_after": float(s.get("pause_after") or 0.3), "shots": [_shot(x) for x in s.get("shots") or []],
                                "events": events}
        if s.get("say"):
            line["say"] = s["say"]
        if s.get("show"):
            line["show"] = s["show"]
        if raw_do or s.get("vrm"):
            line["vtubika"] = {"do": raw_do, "vrm": s.get("vrm") or []}
        lines.append(line)
    pace = vplan.get("pace") or {}
    plan: dict[str, Any] = {"version": SP.VERSION, "engine": "vrm_desk", "title": vplan.get("title") or vplan.get("id") or file,
                            "cast": cast, "location": "stream_room", "narrator": "narrator",
                            "lead": float(pace.get("lead", 0.6)), "tail": float(vplan.get("tail") or 1.8),
                            "captions": {"y": 0.8, "highlight": vplan.get("highlight") or {}},
                            "pronounce": {k: v for k, v in (vplan.get("pronounce") or {}).items() if v},
                            "screen": vplan.get("screen") or {}, "lines": lines,
                            "source": {"kind": "vtubika_plan", "file": file, "voice": vplan.get("voice"),
                                       "music": vplan.get("music"), "end_card": vplan.get("end_card", True)}}
    if vplan.get("presets3d"):
        plan["presets"] = {k: v for k, v in vplan["presets3d"].items() if k in ("look", "light", "mood", "fx")}
    return plan


def _literal(node: Any) -> Any:
    try:
        return ast.literal_eval(node)
    except ValueError:
        return None


def meme_pilot_plan(path: Path) -> dict:
    """Мем-пилот (робот Клод ворчит на пользователя): реплики из ``LINES`` скрипта, комната-пресет с палитрой
    пилота. Движок ``scene3d`` — план проверяется, рендер в сервисе — следующий шаг."""
    tree = ast.parse(path.read_text("utf-8"))
    values: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name == "LINES" and isinstance(node.value, ast.List):
                # В кортежах реплик бывают имена (файл второй пробы) — их значения не нужны.
                values[name] = [[_literal(e) for e in getattr(row, "elts", [])] for row in node.value.elts]
            elif name in ("TITLE", "DURATION"):
                values[name] = _literal(node.value)
    cams = ["wide", "face", "chest", "face", "full", "face", "chest", "face"]
    moves = ["slow_push", "static", "handheld", "crash_zoom", "static", "punch_in", "dolly_zoom", "slow_push"]
    lines = []
    for n, row in enumerate(values.get("LINES") or []):
        text = str(row[4]).replace("…", "… ").strip()
        line = {"id": str(row[0]), "who": "klod", "text": " ".join(text.split()),
                "shots": [{"camera": cams[n % len(cams)], "move": moves[n % len(moves)]}], "pause_after": 0.6}
        lines.append(line)
    if lines:
        lines[0]["events"] = [{"kind": "title", "text": str(values.get("TITLE") or "").replace("\n", " ")[:80], "dur": 2.6}]
        lines[3]["performance"] = [{"who": "klod", "preset": "sigh"}] if len(lines) > 3 else []
        lines[-2]["performance"] = [{"who": "klod", "preset": "offended"}] if len(lines) > 2 else []
    return {"version": SP.VERSION, "engine": "scene3d", "title": "ИИ о своём пользователе", "cast": ["klod"],
            "location": "meme_desk", "narrator": "klod", "presets": {"look": "toon_ink", "light": "overcast_soft"},
            "lines": lines, "source": {"kind": "meme_pilot", "file": "meme3d_pilot.py", "duration": values.get("DURATION")}}


def demo_project(store: Any) -> int:
    from app import projects as P

    row = store.db.query_one("SELECT id FROM projects WHERE name = ? AND kind = 'series3d'", (DEMO_NAME,))
    if row:
        return int(row["id"])
    return int(P.create_project(store, DEMO_NAME, kind="series3d",
                                description="Примеры: VRM-истории Витубики и мем-пилот, перенесённые из скриптов")["id"])


def import_all(store: Any, plans_dir: Path | None = None, meme: bool = True, project_id: int | None = None) -> dict:
    pid = int(project_id or demo_project(store))
    D.seed_builtins(store, pid)
    if meme:
        D.save_item(store, pid, "location", {"type": "room", "room": {"preset": "cozy_desk", "colors": {
            "wall": "#86a9bd", "wood": "#c98c58", "accent": "#3f7f84", "accent2": "#d9774b", "plant": "#6e9f6a"}}},
                    slug="meme_desk", name="Стол Клода (мем-пилот)")
    have = {(e["source"] or {}).get("file") for e in D.list_episodes(store, pid)}
    lib, cfg = D.library(store, pid), D.get_settings(store, pid)
    made, skipped = [], []
    for path in sorted((plans_dir or ROOT / "tools" / "vtubika_plans").glob("*.json")):
        if path.name in have:
            continue
        plan = vtubika_to_plan(json.loads(path.read_text("utf-8")), path.name)
        try:
            clean, warnings = SP.validate_plan(plan, lib, cfg)
        except SP.PlanError as exc:
            skipped.append({"file": path.name, "problems": exc.problems[:5]})
            continue
        ep = D.create_episode(store, pid, clean["title"], clean, author="import", note=f"импорт {path.name}",
                              source=clean.get("source"))
        made.append({"id": ep["id"], "title": ep["title"], "engine": ep["engine"], "file": path.name, "warnings": warnings})
    meme_file = ROOT / "tools" / "meme3d_pilot.py"
    if meme and "meme3d_pilot.py" not in have and meme_file.is_file():
        try:
            clean, warnings = SP.validate_plan(meme_pilot_plan(meme_file), lib, cfg)
            ep = D.create_episode(store, pid, clean["title"], clean, author="import", note="импорт мем-пилота",
                                  source=clean.get("source"))
            made.append({"id": ep["id"], "title": ep["title"], "engine": ep["engine"], "file": "meme3d_pilot.py",
                         "warnings": warnings})
        except SP.PlanError as exc:
            skipped.append({"file": "meme3d_pilot.py", "problems": exc.problems[:5]})
    return {"project_id": pid, "episodes": made, "skipped": skipped}
