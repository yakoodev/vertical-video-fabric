"""ИИ-видео: референсы и версии файлов (из ``app/aivideo.py``)."""

from __future__ import annotations

from typing import Any

from app.aivideo_cast import AivError, _loads, _json, get_character, get_location

# --------------------------------------------------------------------------- #
# Версии картинок (refs)
# --------------------------------------------------------------------------- #


def ref_view(row: Any) -> dict:
    data = dict(row)
    data["selected"] = bool(data.get("selected"))
    data["stale"] = bool(data.get("stale"))
    data["needs_review"] = bool(data.get("needs_review"))
    data["score"] = _loads(data.pop("score_json", ""), {}) or None
    data["url"] = f"/media/montage-assets/{data['asset_id']}" if data.get("asset_id") else None
    return data


def get_ref(store: Any, ref_id: int) -> dict:
    row = store.db.query_one("SELECT * FROM aiv_refs WHERE id = ?", (int(ref_id),))
    if not row:
        raise KeyError(f"нет версии {ref_id}")
    return ref_view(row)


def list_refs(store: Any, project_id: int, target_type: str | None = None, target_id: int | None = None,
              kind: str | None = None, slot: str | None = None) -> list[dict]:
    where, params = ["project_id = ?"], [int(project_id)]
    for col, value in (("target_type", target_type), ("target_id", target_id), ("kind", kind), ("slot", slot)):
        if value is not None:
            where.append(f"{col} = ?")
            params.append(value)
    rows = store.db.query_all(f"SELECT * FROM aiv_refs WHERE {' AND '.join(where)} ORDER BY id", params)
    return [ref_view(r) for r in rows]


def selected_ref(store: Any, project_id: int, target_type: str, target_id: int, kind: str,
                 slot: str = "") -> dict | None:
    row = store.db.query_one(
        "SELECT * FROM aiv_refs WHERE project_id = ? AND target_type = ? AND target_id = ? AND kind = ? AND slot = ? "
        "ORDER BY selected DESC, id DESC LIMIT 1", (int(project_id), target_type, int(target_id), kind, slot))
    return ref_view(row) if row else None


def add_ref(store: Any, project_id: int, target_type: str, target_id: int, kind: str, asset_id: int,
            prompt: str = "", job_id: int | None = None, slot: str = "", select: bool = True) -> dict:
    # Все картинки ИИ-видео, объяснялок и историй идут сюда: чужой файл по id не проходит (app/asset_scope.py).
    from app.asset_scope import allowed, refusal

    asset = store.get_montage_asset(int(asset_id))
    if not allowed(asset, project_id):
        raise AivError(refusal(store, asset, project_id, "работа"))
    row = store.db.query_one(
        "SELECT MAX(version) AS v FROM aiv_refs WHERE project_id = ? AND target_type = ? AND target_id = ? "
        "AND kind = ? AND slot = ?", (int(project_id), target_type, int(target_id), kind, slot))
    version = int(row["v"] or 0) + 1 if row else 1
    cur = store.db.execute(
        "INSERT INTO aiv_refs (project_id, target_type, target_id, kind, slot, version, asset_id, job_id, prompt) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (int(project_id), target_type, int(target_id), kind, slot, version, int(asset_id), job_id, prompt[:6000]))
    ref_id = int(cur.lastrowid)
    if select:
        select_ref(store, ref_id)
    return get_ref(store, ref_id)


def select_ref(store: Any, ref_id: int) -> dict:
    ref = get_ref(store, ref_id)
    store.db.execute(
        "UPDATE aiv_refs SET selected = CASE WHEN id = ? THEN 1 ELSE 0 END WHERE project_id = ? AND target_type = ? "
        "AND target_id = ? AND kind = ? AND slot = ?",
        (int(ref_id), ref["project_id"], ref["target_type"], ref["target_id"], ref["kind"], ref["slot"]))
    return get_ref(store, ref_id)


def set_ref_score(store: Any, ref_id: int, score: dict, needs_review: bool | None = None) -> dict:
    if needs_review is None:
        store.db.execute("UPDATE aiv_refs SET score_json = ? WHERE id = ?", (_json(score), int(ref_id)))
    else:
        store.db.execute("UPDATE aiv_refs SET score_json = ?, needs_review = ? WHERE id = ?",
                         (_json(score), int(bool(needs_review)), int(ref_id)))
    return get_ref(store, ref_id)


def add_upload_ref(store: Any, project_id: int, target_type: str, target_id: int, asset_id: int) -> dict:
    """Картинка владельца (его витубер/персонаж) — канонический референс. Только загрузка владельцем
    или агентом из его файлов; фото реальных людей из интернета не берём."""
    asset = store.get_montage_asset(int(asset_id))
    if asset.get("kind") not in ("image", "gif"):
        raise AivError("референс — картинка (png/jpg/webp)")
    if target_type == "character":
        if int(get_character(store, target_id)["project_id"]) != int(project_id):
            raise AivError("персонаж из другого проекта")
    elif target_type == "location":
        if int(get_location(store, target_id)["project_id"]) != int(project_id):
            raise AivError("локация из другого проекта")
    elif target_type != "style":
        raise AivError("референс владельца — для персонажа, локации или стиля")
    return add_ref(store, project_id, target_type, int(target_id), "upload", int(asset_id),
                   prompt=f"загрузка владельца: {asset.get('label') or asset.get('original_filename') or ''}",
                   select=False)
