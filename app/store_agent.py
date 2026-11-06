"""Хранилище: чат с ИИ-монтажёром и учёт файлов Gemini. Часть ``AppStore``."""

from __future__ import annotations

import json
from typing import Any


def _decode_agent_message(row: dict) -> dict:
    try:
        steps = json.loads(row.pop("steps_json", "") or "[]")
    except (TypeError, ValueError):
        steps = []
    row["steps"] = steps if isinstance(steps, list) else []
    return row


class AgentStore:
    # ---- 💬 Чат с ИИ-монтажёром клипа ------------------------------------------

    def add_clip_agent_message(
        self,
        clip_plan_id: int,
        role: str,
        text: str,
        steps: list | None = None,
        status: str = "done",
        run_id: str = "",
        model: str = "",
    ) -> dict:
        cur = self.db.execute(
            """
            INSERT INTO clip_agent_messages (clip_plan_id, role, text, steps_json, status, run_id, model)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(clip_plan_id), str(role), str(text or "")[:20000],
                json.dumps(steps or [], ensure_ascii=False), str(status), str(run_id), str(model),
            ),
        )
        return self.get_clip_agent_message(int(cur.lastrowid))

    def get_clip_agent_message(self, message_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM clip_agent_messages WHERE id = ?", (message_id,))
        if not row:
            raise KeyError(f"agent message not found: {message_id}")
        return _decode_agent_message(dict(row))

    def update_clip_agent_message(
        self, message_id: int, *, text: str | None = None, steps: list | None = None, status: str | None = None
    ) -> None:
        """Живое обновление ответа: шаги дописываются по мере работы агента."""
        sets, params = [], []
        if text is not None:
            sets.append("text = ?")
            params.append(str(text)[:20000])
        if steps is not None:
            sets.append("steps_json = ?")
            params.append(json.dumps(steps, ensure_ascii=False))
        if status is not None:
            sets.append("status = ?")
            params.append(str(status))
        if not sets:
            return
        self.db.execute(f"UPDATE clip_agent_messages SET {', '.join(sets)} WHERE id = ?", (*params, message_id))

    def list_clip_agent_messages(self, clip_plan_id: int, limit: int = 200) -> list[dict]:
        rows = self.db.query_all(
            """
            SELECT * FROM (
                SELECT * FROM clip_agent_messages WHERE clip_plan_id = ? ORDER BY id DESC LIMIT ?
            ) ORDER BY id
            """,
            (int(clip_plan_id), int(limit)),
        )
        return [_decode_agent_message(dict(r)) for r in rows]

    def clear_clip_agent_messages(self, clip_plan_id: int) -> int:
        cur = self.db.execute("DELETE FROM clip_agent_messages WHERE clip_plan_id = ?", (int(clip_plan_id),))
        return int(cur.rowcount or 0)

    # ---- Gemini Files API: что уже залито и до какого времени живёт ----------

    def get_gemini_file(self, local_path: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM gemini_files WHERE local_path = ?", (str(local_path),))
        return dict(row) if row else None

    def save_gemini_file(self, local_path: str, **fields: Any) -> dict:
        self.db.execute(
            """
            INSERT INTO gemini_files (local_path, name, uri, mime_type, size_bytes, mtime, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(local_path) DO UPDATE SET
                name = excluded.name,
                uri = excluded.uri,
                mime_type = excluded.mime_type,
                size_bytes = excluded.size_bytes,
                mtime = excluded.mtime,
                expires_at = excluded.expires_at,
                created_at = CURRENT_TIMESTAMP
            """,
            (
                str(local_path),
                str(fields.get("name") or ""),
                str(fields.get("uri") or ""),
                str(fields.get("mime_type") or ""),
                int(fields.get("size_bytes") or 0),
                float(fields.get("mtime") or 0),
                str(fields.get("expires_at") or ""),
            ),
        )
        return self.get_gemini_file(local_path) or {}

    def forget_gemini_file(self, local_path: str) -> None:
        self.db.execute("DELETE FROM gemini_files WHERE local_path = ?", (str(local_path),))
