"""Хранилище: настройки сервиса, пресеты промптов и обмен конфигом. Часть ``AppStore``."""

from __future__ import annotations

import json
from typing import Any

from app.settings import settings
from app.store_common import _choice, VALID_PROMPT_TASKS


class SettingsStore:
    # --- Share bundle: export/import config (and optionally accounts) as portable JSON ---

    def export_bundle(self, include_accounts: bool = False) -> dict:
        """Build a portable JSON bundle to share config with colleagues. Render/subtitle
        presets and prompts are safe to share; accounts carry live cookies and are
        included only when explicitly requested."""
        drop_common = {"id", "created_at", "updated_at"}
        drop_fp = drop_common | {"banner_id", "subtitle_profile_id", "music_track_id"}
        bundle: dict = {
            "version": 1,
            "ffmpeg_presets": [
                {k: v for k, v in p.items() if k not in drop_fp} for p in self.list_ffmpeg_presets()
            ],
            "subtitle_profiles": [
                {k: v for k, v in s.items() if k not in drop_common} for s in self.list_subtitle_profiles()
            ],
            "prompt_presets": [
                {
                    "task": p.get("task"),
                    "label": p.get("label"),
                    "prompt": p.get("prompt"),
                    "is_default": bool(p.get("is_default")),
                }
                for p in self.list_prompt_presets()
            ],
        }
        if include_accounts:
            accounts: list[dict] = []
            for a in self.list_accounts():
                row = self.db.query_one("SELECT encrypted_cookies FROM accounts WHERE id = ?", (a["id"],))
                cookies = self.cipher.decrypt_json(row["encrypted_cookies"]) if row else []
                accounts.append(
                    {
                        "platform": a["platform"],
                        "label": a["label"],
                        "proxy_url": self.get_account_proxy_url(a["id"]),
                        "cookies": cookies,
                    }
                )
            bundle["accounts"] = accounts
        return bundle

    def import_bundle(self, bundle: dict) -> dict:
        """Merge a bundle produced by export_bundle. Existing items (by label /
        task+label / platform+label) are skipped, not overwritten. Returns counts."""
        counts = {"ffmpeg_presets": 0, "subtitle_profiles": 0, "prompt_presets": 0, "accounts": 0, "skipped": 0}
        if not isinstance(bundle, dict):
            raise ValueError("bundle must be a JSON object")

        have_fp = {p.get("label") for p in self.list_ffmpeg_presets()}
        for p in bundle.get("ffmpeg_presets") or []:
            label = str(p.get("label") or "").strip()
            if not label or label in have_fp:
                counts["skipped"] += 1
                continue
            try:
                self.create_ffmpeg_preset(label, **{k: v for k, v in p.items() if k != "label"})
                have_fp.add(label)
                counts["ffmpeg_presets"] += 1
            except Exception:  # noqa: BLE001 - skip malformed entries, keep importing the rest
                counts["skipped"] += 1

        have_sp = {s.get("label") for s in self.list_subtitle_profiles()}
        for s in bundle.get("subtitle_profiles") or []:
            label = str(s.get("label") or "").strip()
            if not label or label in have_sp:
                counts["skipped"] += 1
                continue
            try:
                self.create_subtitle_profile(label, **{k: v for k, v in s.items() if k != "label"})
                have_sp.add(label)
                counts["subtitle_profiles"] += 1
            except Exception:  # noqa: BLE001
                counts["skipped"] += 1

        have_pp = {(p.get("task"), p.get("label")) for p in self.list_prompt_presets()}
        for p in bundle.get("prompt_presets") or []:
            key = (p.get("task"), p.get("label"))
            if not key[0] or not key[1] or key in have_pp:
                counts["skipped"] += 1
                continue
            try:
                self.create_prompt_preset(str(p["task"]), str(p["label"]), str(p.get("prompt") or ""), bool(p.get("is_default")))
                have_pp.add(key)
                counts["prompt_presets"] += 1
            except Exception:  # noqa: BLE001
                counts["skipped"] += 1

        have_acc = {(a.get("platform"), a.get("label")) for a in self.list_accounts()}
        for a in bundle.get("accounts") or []:
            key = (a.get("platform"), a.get("label"))
            if not key[0] or not key[1] or key in have_acc:
                counts["skipped"] += 1
                continue
            try:
                header = "; ".join(
                    f"{c.get('name')}={c.get('value')}"
                    for c in (a.get("cookies") or [])
                    if c.get("name")
                )
                if not header:
                    counts["skipped"] += 1
                    continue
                self.upsert_account(str(a["platform"]), str(a["label"]), header, str(a.get("proxy_url") or ""))
                have_acc.add(key)
                counts["accounts"] += 1
            except Exception:  # noqa: BLE001
                counts["skipped"] += 1

        return counts

    def set_app_setting(self, key: str, value: Any, encrypted: bool = False) -> dict:
        key = key.strip()
        if not key:
            raise ValueError("setting key is required")
        value_json = self.cipher.encrypt_json(value) if encrypted else json.dumps(value, ensure_ascii=False)
        self.db.execute(
            """
            INSERT INTO app_settings (key, value_json, encrypted, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                encrypted = excluded.encrypted,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value_json, int(encrypted)),
        )
        return self.get_app_setting(key, include_secret=True)

    def get_app_setting(self, key: str, default: Any | None = None, include_secret: bool = False) -> dict:
        row = self.db.query_one("SELECT * FROM app_settings WHERE key = ?", (key.strip(),))
        if not row:
            return {"key": key, "value": default, "encrypted": False}
        data = dict(row)
        encrypted = bool(data.get("encrypted"))
        data["encrypted"] = encrypted
        if encrypted:
            data["value"] = self.cipher.decrypt_json(data["value_json"]) if include_secret else ""
        else:
            data["value"] = json.loads(data["value_json"])
        return data

    def list_app_settings(self, include_secrets: bool = False) -> dict[str, Any]:
        rows = self.db.query_all("SELECT * FROM app_settings ORDER BY key")
        values: dict[str, Any] = {}
        for row in rows:
            data = dict(row)
            if data.get("encrypted"):
                values[data["key"]] = self.cipher.decrypt_json(data["value_json"]) if include_secrets else ""
            else:
                values[data["key"]] = json.loads(data["value_json"])
        return values

    def get_app_setting_value(self, key: str, default: Any | None = None, include_secret: bool = False) -> Any:
        return self.get_app_setting(key, default=default, include_secret=include_secret)["value"]

    def ensure_prompt_presets(self, defaults: dict[str, tuple[str, str]]) -> None:
        for task, (label, prompt) in defaults.items():
            if self.list_prompt_presets(task):
                continue
            self.create_prompt_preset(task, label, prompt, is_default=True)

    def create_prompt_preset(self, task: str, label: str, prompt: str, is_default: bool = False) -> dict:
        task = _choice(task, VALID_PROMPT_TASKS, "task")
        label = str(label).strip()
        prompt = str(prompt).strip()
        if not label:
            raise ValueError("prompt preset label is required")
        if not prompt:
            raise ValueError("prompt preset text is required")
        with self.db.transaction() as conn:
            if is_default:
                conn.execute("UPDATE prompt_presets SET is_default = 0, updated_at = CURRENT_TIMESTAMP WHERE task = ?", (task,))
            cur = conn.execute(
                """
                INSERT INTO prompt_presets (task, label, prompt, is_default)
                VALUES (?, ?, ?, ?)
                """,
                (task, label, prompt, int(is_default)),
            )
            preset_id = cur.lastrowid
        return self.get_prompt_preset(preset_id)

    def update_prompt_preset(
        self,
        preset_id: int,
        *,
        label: str,
        prompt: str,
        is_default: bool = False,
    ) -> dict:
        preset = self.get_prompt_preset(preset_id)
        label = str(label).strip()
        prompt = str(prompt).strip()
        if not label:
            raise ValueError("prompt preset label is required")
        if not prompt:
            raise ValueError("prompt preset text is required")
        with self.db.transaction() as conn:
            if is_default:
                conn.execute("UPDATE prompt_presets SET is_default = 0, updated_at = CURRENT_TIMESTAMP WHERE task = ?", (preset["task"],))
            conn.execute(
                """
                UPDATE prompt_presets
                SET label = ?, prompt = ?, is_default = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (label, prompt, int(is_default), preset_id),
            )
        return self.get_prompt_preset(preset_id)

    def delete_prompt_preset(self, preset_id: int) -> None:
        preset = self.get_prompt_preset(preset_id)
        count = self.db.query_one("SELECT COUNT(*) AS count FROM prompt_presets WHERE task = ?", (preset["task"],))["count"]
        if count <= 1:
            raise ValueError("cannot delete the last prompt preset for task")
        self.db.execute("DELETE FROM prompt_presets WHERE id = ?", (preset_id,))
        if preset["is_default"]:
            replacement = self.list_prompt_presets(preset["task"])[0]
            self.update_prompt_preset(replacement["id"], label=replacement["label"], prompt=replacement["prompt"], is_default=True)

    def list_prompt_presets(self, task: str | None = None) -> list[dict]:
        if task:
            task = _choice(task, VALID_PROMPT_TASKS, "task")
            rows = self.db.query_all(
                "SELECT * FROM prompt_presets WHERE task = ? ORDER BY is_default DESC, label, id",
                (task,),
            )
        else:
            rows = self.db.query_all("SELECT * FROM prompt_presets ORDER BY task, is_default DESC, label, id")
        return [self._prompt_preset_from_row(row) for row in rows]

    def get_prompt_preset(self, preset_id: int) -> dict:
        row = self.db.query_one("SELECT * FROM prompt_presets WHERE id = ?", (preset_id,))
        if not row:
            raise KeyError(f"prompt preset not found: {preset_id}")
        return self._prompt_preset_from_row(row)

    def get_default_prompt_preset(self, task: str) -> dict | None:
        task = _choice(task, VALID_PROMPT_TASKS, "task")
        row = self.db.query_one(
            """
            SELECT * FROM prompt_presets
            WHERE task = ?
            ORDER BY is_default DESC, id
            LIMIT 1
            """,
            (task,),
        )
        return self._prompt_preset_from_row(row) if row else None

    def get_global_proxy_url(self) -> str:
        stored = str(self.get_app_setting_value("global_proxy_url", "", include_secret=True) or "").strip()
        return stored or settings.posting_proxy_url

    def _prompt_preset_from_row(self, row) -> dict:
        data = dict(row)
        data["is_default"] = bool(data["is_default"])
        return data
