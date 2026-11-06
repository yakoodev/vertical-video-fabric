"""Группы клипов: снимок монтажа и отпечаток состояния (из ``app/clip_groups.py``)."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from app.clip_groups_facts import Query, _columns

# ---- снимок монтажа ------------------------------------------------------------------------------


_TEXT_KEYS = ("title", "description", "notes", "favorite")


def state_fingerprint(state: dict) -> str:
    """Тот же отпечаток, что у рендера (app/quality_lint.plan_fingerprint), для готового снимка."""
    spec = {k: v for k, v in (state.get("spec") or {}).items() if k not in _TEXT_KEYS}
    raw = json.dumps({"spec": spec, "subtitles": state.get("subtitles")}, ensure_ascii=False, sort_keys=True,
                     default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def add_snapshot(store: Any, clip_plan_id: int, state: dict, *, clip_id: int | None, kind: str, label: str = "") -> int:
    cur = store.db.execute(
        "INSERT INTO clip_versions (clip_plan_id, clip_id, kind, label, state_json) VALUES (?, ?, ?, ?, ?)",
        (int(clip_plan_id), clip_id, kind, str(label or "")[:200], json.dumps(state, ensure_ascii=False, sort_keys=True)),
    )
    return int(cur.lastrowid)


def capture_quiet(store: Any, clip_plan_id: int) -> dict | None:
    """Монтаж плана перед рендером. Не сняли — рендер всё равно идёт."""
    try:
        from app.clip_history import capture

        return capture(store, clip_plan_id)
    except Exception:  # noqa: BLE001 - снимок — страховка, рендер важнее
        return None


def snapshot_render(store: Any, clip_plan_id: int, clip_id: int, state: dict | None) -> None:
    """Сервис рендера: запомнить монтаж, с которого сделан файл. Никогда не ломает рендер."""
    if not state:
        return
    try:
        add_snapshot(store, clip_plan_id, state, clip_id=clip_id, kind="render")
    except Exception:  # noqa: BLE001 - снимок — страховка, рендер важнее
        pass


def _history_times(store: Any, plan_ids: list[int]) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for chunk in _chunks(plan_ids):
        marks = ",".join("?" for _ in chunk)
        for row in store.db.query_all(
            f"SELECT id, clip_plan_id, label, created_at FROM clip_plan_history WHERE clip_plan_id IN ({marks}) ORDER BY id",
            chunk,
        ):
            out.setdefault(int(row["clip_plan_id"]), []).append(dict(row))
    return out


def history_candidate(history: list[dict], render_created_at: str, depth: int) -> tuple[str, dict | None]:
    """Откуда взять монтаж рендера по истории правок: снимок ПЕРЕД первой правкой после рендера.

    Возвращает («entry», запись) — снимок нашёлся; («current», None) — правок после рендера не было, это
    текущее состояние; («lost», None) — история урезана глубиной и нужного снимка могло не остаться."""
    after = [h for h in history if str(h["created_at"]) > str(render_created_at)]
    if not after:
        return "current", None
    trimmed = len(history) >= depth and len(after) == len(history)
    if trimmed:
        return "lost", None
    return "entry", after[0]


def state_of_render(store: Any, clip: dict) -> dict | None:
    """Монтаж, с которого сделан этот рендер, или None, если снимок не сохранился."""
    from app.clip_history import HISTORY_DEPTH, capture

    plan_id = clip.get("clip_plan_id")
    if not plan_id:
        return None
    row = store.db.query_one(
        "SELECT state_json FROM clip_versions WHERE clip_id = ? ORDER BY id DESC LIMIT 1", (int(clip["id"]),)
    )
    if row:
        return json.loads(row["state_json"])
    rows = [dict(r) for r in store.db.query_all(
        "SELECT id, label, created_at, state_json FROM clip_plan_history WHERE clip_plan_id = ? ORDER BY id",
        (int(plan_id),),
    )]
    stamp = clip.get("plan_fingerprint")
    if stamp:
        # У новых рендеров есть отпечаток: снимок истории с тем же отпечатком — точно он.
        for entry in rows:
            try:
                state = json.loads(entry["state_json"])
            except ValueError:
                continue
            if state_fingerprint(state) == stamp:
                return state
    how, entry = history_candidate(rows, str(clip.get("created_at") or ""), HISTORY_DEPTH)
    if how == "current":
        return capture(store, int(plan_id))
    if how == "lost" or entry is None:
        return None
    try:
        return json.loads(entry["state_json"])
    except ValueError:
        return None


def backfill_snapshots(query: Query) -> list[tuple[int, int, str]]:
    """Снимки для старых рендеров из истории правок (скрипт группировки): история хранит 20 шагов и
    со временем их потеряет. Рендеры без правок после них не трогаем: их монтаж и есть текущий."""
    from app.clip_history import HISTORY_DEPTH

    if "clip_group_id" not in _columns(query, "clip_plans"):
        have: set[int] = set()
    else:
        have = {int(r["clip_id"]) for r in query("SELECT clip_id FROM clip_versions WHERE clip_id IS NOT NULL", ())}
    history: dict[int, list[dict]] = {}
    for row in query("SELECT id, clip_plan_id, label, created_at, state_json FROM clip_plan_history ORDER BY id", ()):
        history.setdefault(int(row["clip_plan_id"]), []).append(dict(row))
    out: list[tuple[int, int, str]] = []
    for row in query(
        "SELECT id, clip_plan_id, created_at, plan_fingerprint FROM clips "
        "WHERE status = 'succeeded' AND clip_plan_id IS NOT NULL ORDER BY id",
        (),
    ):
        if int(row["id"]) in have:
            continue
        entries = history.get(int(row["clip_plan_id"]), [])
        chosen: str | None = None
        if row["plan_fingerprint"]:
            for entry in entries:
                try:
                    if state_fingerprint(json.loads(entry["state_json"])) == row["plan_fingerprint"]:
                        chosen = entry["state_json"]
                        break
                except ValueError:
                    continue
        if chosen is None:
            how, entry = history_candidate(entries, str(row["created_at"] or ""), HISTORY_DEPTH)
            if how == "entry" and entry:
                chosen = entry["state_json"]
        if chosen:
            out.append((int(row["id"]), int(row["clip_plan_id"]), chosen))
    return out


# ---- сетка: один элемент на клип ------------------------------------------------------------------


def _chunks(ids: list[int], size: int = 400) -> Iterable[list[int]]:
    for start in range(0, len(ids), size):
        yield ids[start:start + size]
